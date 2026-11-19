import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import certifi
import yt_dlp
from yt_dlp.utils import download_range_func
from flask import Flask, jsonify, render_template, request, send_file

try:
    import imageio_ffmpeg
except ImportError:
    imageio_ffmpeg = None

# Ensure SSL certificates are properly configured across platforms
os.environ.setdefault("SSL_CERT_FILE", certifi.where())
os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())


def resolve_ffmpeg() -> str:
    sys_ffmpeg = shutil.which("ffmpeg")
    if sys_ffmpeg:
        return sys_ffmpeg
    if imageio_ffmpeg is not None:
        return imageio_ffmpeg.get_ffmpeg_exe()
    return "ffmpeg"


FFMPEG_PATH = resolve_ffmpeg()
BASE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))

app = Flask(
    __name__,
    template_folder=str(BASE_DIR / "templates"),
    static_folder=str(BASE_DIR / "static"),
)

DOWNLOAD_DIR = Path(tempfile.gettempdir()) / "fluxtube_downloads"
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
COOKIE_TMP_PATH = Path(tempfile.gettempdir()) / "fluxtube_cookies.txt"

JOBS: Dict[str, dict] = {}
JOBS_LOCK = threading.Lock()
CLIENT_CACHE: Dict[str, Optional[List[str]]] = {}
FALLBACK_STREAMS_CACHE: Dict[str, dict] = {}

# Public fallback instances for YouTube (Invidious, Piped, Cobalt)
INVIDIOUS_INSTANCES = [
    "https://inv.nadeko.net",
    "https://invidious.nerdvpn.de",
    "https://invidious.jing.rocks",
    "https://yt.artemislena.eu",
    "https://invidious.projectsegfau.lt",
    "https://invidious.private.coffee",
    "https://invidious.drgns.space",
    "https://invidious.flokinet.to",
]

PIPED_INSTANCES = [
    "https://pipedapi.kavin.rocks",
    "https://api.piped.privacydev.net",
    "https://pipedapi.leptons.xyz",
    "https://pipedapi-libre.kavin.rocks",
]


def is_youtube_url(url: str) -> bool:
    u = (url or "").lower()
    return "youtube.com" in u or "youtu.be" in u


def extract_youtube_id(url: str) -> Optional[str]:
    patterns = [
        r"(?:v=|\/)([0-9A-Za-z_-]{11})(?:[&?#]|$)",
        r"(?:embed\/|v\/|shorts\/|live\/)([0-9A-Za-z_-]{11})(?:[&?#]|$)",
        r"youtu\.be\/([0-9A-Za-z_-]{11})(?:[&?#]|$)",
    ]
    for p in patterns:
        m = re.search(p, url or "")
        if m:
            return m.group(1)
    return None


def detect_platform(url: str) -> str:
    u = (url or "").lower()
    if is_youtube_url(u):
        return "YouTube"
    if "instagram.com" in u:
        return "Instagram"
    if "tiktok.com" in u:
        return "TikTok"
    if "facebook.com" in u or "fb.watch" in u or "fb.com" in u:
        return "Facebook"
    return "Video Link"


def get_cookiefile_path() -> Optional[str]:
    """Resolves cookies from Render Secret Files, Env Vars, uploaded UI cookies, or local cookies.txt."""
    # 1. Render Secret File (/etc/secrets/cookies.txt)
    secret_file = Path("/etc/secrets/cookies.txt")
    if secret_file.is_file() and secret_file.stat().st_size > 50:
        return str(secret_file)

    # 2. Local cookies.txt in project root
    local_cookie = BASE_DIR / "cookies.txt"
    if local_cookie.is_file() and local_cookie.stat().st_size > 50:
        return str(local_cookie)

    # 3. Cookies uploaded via Web UI (/tmp/fluxtube_cookies.txt)
    if COOKIE_TMP_PATH.is_file() and COOKIE_TMP_PATH.stat().st_size > 50:
        return str(COOKIE_TMP_PATH)

    # 4. Environment variable YOUTUBE_COOKIES
    raw = os.environ.get("YOUTUBE_COOKIES", "").strip()
    if raw and len(raw) > 50:
        normalized = raw.replace("\\n", "\n").replace("\r\n", "\n").replace("\r", "\n")
        COOKIE_TMP_PATH.write_text(normalized, encoding="utf-8")
        return str(COOKIE_TMP_PATH)

    return None


def get_active_proxy() -> Optional[str]:
    proxy = os.environ.get("YOUTUBE_PROXY", "").strip()
    return proxy if proxy else None


def format_bytes(size):
    if not size or size <= 0:
        return None
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024.0:
            return f"{size:.1f} {unit}" if unit in ("MB", "GB") else f"{int(size)} {unit}"
        size /= 1024.0
    return f"{size:.1f} TB"


def format_duration(seconds):
    if seconds is None:
        return "0:00"
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def parse_timestamp(val) -> Optional[float]:
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return max(0.0, float(val))
    s = str(val).strip()
    if not s:
        return None
    if re.fullmatch(r"\d+(\.\d+)?", s):
        return max(0.0, float(s))
    parts = s.split(":")
    try:
        if len(parts) == 2:
            return max(0.0, int(parts[0]) * 60 + float(parts[1]))
        if len(parts) == 3:
            return max(0.0, int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2]))
    except ValueError:
        return None
    return None


def sanitize_filename(name: str) -> str:
    return re.sub(r'[\\/*?:"<>|]+', "", name).strip()[:110] or "video"


def build_base_ydl_opts(clients: Optional[List[str]] = None, has_cookies: bool = False) -> dict:
    opts: Dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "source_address": "0.0.0.0",  # Force IPv4
        "geo_bypass": True,
        "retries": 3,
        "fragment_retries": 3,
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        },
    }

    if clients:
        opts["extractor_args"] = {"youtube": {"player_client": clients}}

    proxy = get_active_proxy()
    if proxy:
        opts["proxy"] = proxy

    cookie_file = get_cookiefile_path()
    if cookie_file:
        opts["cookiefile"] = cookie_file

    return opts


def cleanup_old_jobs(max_age_seconds: int = 1800):
    now = time.time()
    with JOBS_LOCK:
        expired = [
            jid for jid, job in JOBS.items()
            if now - job.get("created_at", now) > max_age_seconds
        ]
        for jid in expired:
            job = JOBS.pop(jid, None)
            if job and job.get("filepath"):
                try:
                    Path(job["filepath"]).unlink(missing_ok=True)
                except OSError:
                    pass


def get_exact_stream_size(fmt: dict, duration: float, is_audio: bool = False) -> int:
    if not fmt:
        return 0
    if fmt.get("filesize"):
        return int(fmt["filesize"])
    if fmt.get("filesize_approx"):
        return int(fmt["filesize_approx"])
    bitrate = fmt.get("abr") if is_audio else (fmt.get("vbr") or fmt.get("tbr"))
    if bitrate and duration:
        return int((bitrate * 1000 / 8) * duration)
    return 0


def build_quality_options(info: dict):
    formats = info.get("formats") or []
    duration = info.get("duration") or 0

    m4a_audios = [
        f for f in formats
        if f.get("acodec") not in (None, "none")
        and f.get("vcodec") in (None, "none")
        and f.get("ext") == "m4a"
    ]
    all_audios = [
        f for f in formats
        if f.get("acodec") not in (None, "none") and f.get("vcodec") in (None, "none")
    ]
    best_m4a = max(m4a_audios, key=lambda f: f.get("abr") or 0, default=None)
    best_any_audio = max(all_audios, key=lambda f: f.get("abr") or 0, default=None)
    chosen_audio = best_m4a or best_any_audio
    best_audio_size = get_exact_stream_size(chosen_audio, duration, is_audio=True)

    by_height = {}
    for f in formats:
        vcodec = f.get("vcodec")
        height = f.get("height")
        if vcodec == "none" or not height or height < 144:
            continue

        ext = f.get("ext", "")
        fps = f.get("fps") or 30
        has_exact_size = 1 if f.get("filesize") else 0
        is_mp4 = 1 if ext == "mp4" else 0
        tbr = f.get("tbr") or 0

        score = (is_mp4, has_exact_size, 1 if fps >= 50 else 0, tbr)
        existing = by_height.get(height)
        if not existing or score > existing["_score"]:
            video_size = get_exact_stream_size(f, duration, is_audio=False)
            has_audio = f.get("acodec") not in (None, "none")
            total_bytes = video_size + (0 if (has_audio or not video_size) else best_audio_size)
            by_height[height] = {
                "format_id": f.get("format_id"),
                "height": height,
                "fps": int(fps) if fps else 30,
                "size_bytes": total_bytes,
                "size_label": format_bytes(total_bytes) or "Auto",
                "has_audio": has_audio,
                "_score": score,
            }

    tier_labels = {
        2160: ("4K UHD", "2160p", "ULTRA"),
        1440: ("2K QHD", "1440p", "PRO"),
        1080: ("Full HD", "1080p", "HD+"),
        720: ("HD", "720p", "FAST"),
        480: ("SD", "480p", "LITE"),
        360: ("Compact", "360p", "DATA"),
        240: ("Low", "240p", "MINI"),
    }

    options = []
    for h in sorted(by_height.keys(), reverse=True):
        if h not in tier_labels and h < 360 and len(by_height) > 2:
            continue
        item = by_height[h]
        title, res, badge = tier_labels.get(h, (f"{h}p", f"{h}p", "HD"))
        options.append({
            "id": f"video_{h}",
            "format_id": item["format_id"],
            "audio_id": None if item["has_audio"] else (chosen_audio.get("format_id") if chosen_audio else None),
            "type": "video",
            "title": title,
            "resolution": res,
            "height": h,
            "fps": item["fps"],
            "badge": f"{badge} • {item['fps']}FPS" if item["fps"] >= 50 else badge,
            "ext": "MP4",
            "size": item["size_label"],
            "size_bytes": item["size_bytes"],
        })

    if not options:
        best_size = get_exact_stream_size(info, duration, is_audio=False)
        options.append({
            "id": "video_best",
            "format_id": info.get("format_id"),
            "audio_id": None,
            "type": "video",
            "title": "Original HD",
            "resolution": f"{info.get('height')}p" if info.get("height") else "Best",
            "height": info.get("height") or 1080,
            "fps": int(info.get("fps") or 30),
            "badge": "ORIGINAL",
            "ext": "MP4",
            "size": format_bytes(best_size) or "Auto",
            "size_bytes": best_size,
        })

    mp3_bytes = best_audio_size if best_audio_size else (int((192 * 1000 / 8) * duration) if duration else 0)
    options.append({
        "id": "audio_mp3",
        "type": "audio",
        "title": "Audio MP3",
        "resolution": "MP3",
        "height": 0,
        "fps": 0,
        "badge": "AUDIO",
        "ext": "MP3",
        "size": format_bytes(mp3_bytes) or "Auto",
        "size_bytes": mp3_bytes,
    })
    options.append({
        "id": "audio_m4a",
        "type": "audio",
        "title": "Audio M4A",
        "resolution": "M4A",
        "height": 0,
        "fps": 0,
        "badge": "AAC",
        "ext": "M4A",
        "size": format_bytes(best_audio_size) or "Auto",
        "size_bytes": best_audio_size,
    })

    return options


# ============================================================================
# MULTI-ENGINE FALLBACK PIPELINE (INVIDIOUS & PIPED AUTO-FAILOVER)
# Solves YouTube datacenter IP blocking on Render / VPS without requiring cookies
# ============================================================================

def make_http_request(url: str, timeout: float = 4.5, headers: Optional[dict] = None) -> Optional[bytes]:
    req_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "application/json,text/html,*/*",
    }
    if headers:
        req_headers.update(headers)

    ctx = ssl.create_default_context(cafile=certifi.where())
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    proxy = get_active_proxy()
    if proxy:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy}),
            urllib.request.HTTPSHandler(context=ctx),
        )
    else:
        opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx))

    try:
        req = urllib.request.Request(url, headers=req_headers)
        with opener.open(req, timeout=timeout) as response:
            return response.read()
    except Exception:
        return None


def fetch_youtube_fallback(video_id: str, original_url: str) -> Optional[dict]:
    """Queries healthy Invidious and Piped public APIs when yt-dlp is challenged by YouTube bot protection."""
    # 1. Try Invidious Instances
    for base in INVIDIOUS_INSTANCES:
        api_url = f"{base}/api/v1/videos/{video_id}"
        content = make_http_request(api_url, timeout=3.5)
        if not content:
            continue
        try:
            data = json.loads(content.decode("utf-8"))
            if not data or not data.get("title"):
                continue

            title = data.get("title") or "YouTube Video"
            duration = int(data.get("lengthSeconds") or 0)
            uploader = data.get("author") or "YouTube Creator"
            thumb = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
            if data.get("videoThumbnails"):
                thumb = data["videoThumbnails"][-1].get("url") or thumb

            format_streams = data.get("formatStreams") or []
            adaptive_formats = data.get("adaptiveFormats") or []

            # Store stream URLs in cache for download execution
            stream_map = {}

            # Audio streams
            best_audio_url = None
            for af in adaptive_formats:
                type_str = af.get("type", "")
                if "audio" in type_str:
                    best_audio_url = af.get("url")
                    break

            qualities = []

            # Check for 1080p in adaptiveFormats
            vid_1080 = next((f for f in adaptive_formats if f.get("resolution") == "1080p" or "1080" in f.get("qualityLabel", "")), None)
            if vid_1080 and vid_1080.get("url"):
                stream_map["video_1080"] = {
                    "video_url": vid_1080.get("url"),
                    "audio_url": best_audio_url,
                    "height": 1080,
                    "needs_mux": True,
                }
                qualities.append({
                    "id": "video_1080",
                    "format_id": "fallback_1080",
                    "audio_id": "fallback_audio",
                    "type": "video",
                    "title": "Full HD",
                    "resolution": "1080p",
                    "height": 1080,
                    "fps": 30,
                    "badge": "HD+ • FAST",
                    "ext": "MP4",
                    "size": "Auto",
                    "size_bytes": int((2500 * 1000 / 8) * duration) if duration else 0,
                })

            # Check 720p (muxed or adaptive)
            vid_720 = next((f for f in format_streams if f.get("resolution") == "720p" or "720" in f.get("qualityLabel", "")), None)
            if vid_720 and vid_720.get("url"):
                stream_map["video_720"] = {
                    "video_url": vid_720.get("url"),
                    "audio_url": None,
                    "height": 720,
                    "needs_mux": False,
                }
                qualities.append({
                    "id": "video_720",
                    "format_id": "fallback_720",
                    "audio_id": None,
                    "type": "video",
                    "title": "HD",
                    "resolution": "720p",
                    "height": 720,
                    "fps": 30,
                    "badge": "HD",
                    "ext": "MP4",
                    "size": "Auto",
                    "size_bytes": int((1200 * 1000 / 8) * duration) if duration else 0,
                })

            # Check 360p (muxed)
            vid_360 = next((f for f in format_streams if f.get("resolution") == "360p" or "360" in f.get("qualityLabel", "")), None)
            if vid_360 and vid_360.get("url"):
                stream_map["video_360"] = {
                    "video_url": vid_360.get("url"),
                    "audio_url": None,
                    "height": 360,
                    "needs_mux": False,
                }
                qualities.append({
                    "id": "video_360",
                    "format_id": "fallback_360",
                    "audio_id": None,
                    "type": "video",
                    "title": "Compact",
                    "resolution": "360p",
                    "height": 360,
                    "fps": 30,
                    "badge": "DATA",
                    "ext": "MP4",
                    "size": "Auto",
                    "size_bytes": int((500 * 1000 / 8) * duration) if duration else 0,
                })

            if best_audio_url or format_streams:
                audio_stream_src = best_audio_url or (format_streams[0].get("url") if format_streams else None)
                stream_map["audio_mp3"] = {
                    "video_url": None,
                    "audio_url": audio_stream_src,
                    "height": 0,
                    "needs_mux": False,
                }
                stream_map["audio_m4a"] = {
                    "video_url": None,
                    "audio_url": audio_stream_src,
                    "height": 0,
                    "needs_mux": False,
                }
                qualities.append({
                    "id": "audio_mp3",
                    "type": "audio",
                    "title": "Audio MP3",
                    "resolution": "MP3",
                    "height": 0,
                    "fps": 0,
                    "badge": "AUDIO",
                    "ext": "MP3",
                    "size": "Auto",
                    "size_bytes": int((192 * 1000 / 8) * duration) if duration else 0,
                })
                qualities.append({
                    "id": "audio_m4a",
                    "type": "audio",
                    "title": "Audio M4A",
                    "resolution": "M4A",
                    "height": 0,
                    "fps": 0,
                    "badge": "AAC",
                    "ext": "M4A",
                    "size": "Auto",
                    "size_bytes": int((128 * 1000 / 8) * duration) if duration else 0,
                })

            if qualities:
                FALLBACK_STREAMS_CACHE[video_id] = stream_map
                return {
                    "id": video_id,
                    "platform": "YouTube",
                    "title": title,
                    "thumbnail": thumb,
                    "duration": duration,
                    "duration_string": format_duration(duration),
                    "uploader": uploader,
                    "webpage_url": original_url,
                    "qualities": qualities,
                    "engine": "fallback",
                    "engine_label": "Cloud Fallback Engine",
                }
        except Exception:
            continue

    # 2. Try Piped Instances
    for base in PIPED_INSTANCES:
        api_url = f"{base}/streams/{video_id}"
        content = make_http_request(api_url, timeout=3.5)
        if not content:
            continue
        try:
            data = json.loads(content.decode("utf-8"))
            if not data or not data.get("title"):
                continue

            title = data.get("title") or "YouTube Video"
            duration = int(data.get("duration") or 0)
            uploader = data.get("uploader") or "YouTube Creator"
            thumb = data.get("thumbnailUrl") or f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"

            video_streams = data.get("videoStreams") or []
            audio_streams = data.get("audioStreams") or []

            best_audio = audio_streams[0].get("url") if audio_streams else None
            stream_map = {}
            qualities = []

            for vs in video_streams:
                quality_label = vs.get("quality", "")
                if "1080" in quality_label and "video_1080" not in stream_map:
                    stream_map["video_1080"] = {
                        "video_url": vs.get("url"),
                        "audio_url": best_audio if vs.get("videoOnly") else None,
                        "height": 1080,
                        "needs_mux": bool(vs.get("videoOnly")),
                    }
                    qualities.append({
                        "id": "video_1080",
                        "format_id": "piped_1080",
                        "audio_id": "piped_audio",
                        "type": "video",
                        "title": "Full HD",
                        "resolution": "1080p",
                        "height": 1080,
                        "fps": vs.get("fps") or 30,
                        "badge": "HD+ • FAST",
                        "ext": "MP4",
                        "size": "Auto",
                        "size_bytes": int((2500 * 1000 / 8) * duration) if duration else 0,
                    })
                elif "720" in quality_label and "video_720" not in stream_map:
                    stream_map["video_720"] = {
                        "video_url": vs.get("url"),
                        "audio_url": best_audio if vs.get("videoOnly") else None,
                        "height": 720,
                        "needs_mux": bool(vs.get("videoOnly")),
                    }
                    qualities.append({
                        "id": "video_720",
                        "format_id": "piped_720",
                        "audio_id": "piped_audio",
                        "type": "video",
                        "title": "HD",
                        "resolution": "720p",
                        "height": 720,
                        "fps": vs.get("fps") or 30,
                        "badge": "HD",
                        "ext": "MP4",
                        "size": "Auto",
                        "size_bytes": int((1200 * 1000 / 8) * duration) if duration else 0,
                    })

            if best_audio:
                stream_map["audio_mp3"] = {"video_url": None, "audio_url": best_audio, "height": 0, "needs_mux": False}
                stream_map["audio_m4a"] = {"video_url": None, "audio_url": best_audio, "height": 0, "needs_mux": False}
                qualities.append({
                    "id": "audio_mp3",
                    "type": "audio",
                    "title": "Audio MP3",
                    "resolution": "MP3",
                    "height": 0,
                    "fps": 0,
                    "badge": "AUDIO",
                    "ext": "MP3",
                    "size": "Auto",
                    "size_bytes": int((192 * 1000 / 8) * duration) if duration else 0,
                })
                qualities.append({
                    "id": "audio_m4a",
                    "type": "audio",
                    "title": "Audio M4A",
                    "resolution": "M4A",
                    "height": 0,
                    "fps": 0,
                    "badge": "AAC",
                    "ext": "M4A",
                    "size": "Auto",
                    "size_bytes": int((128 * 1000 / 8) * duration) if duration else 0,
                })

            if qualities:
                FALLBACK_STREAMS_CACHE[video_id] = stream_map
                return {
                    "id": video_id,
                    "platform": "YouTube",
                    "title": title,
                    "thumbnail": thumb,
                    "duration": duration,
                    "duration_string": format_duration(duration),
                    "uploader": uploader,
                    "webpage_url": original_url,
                    "qualities": qualities,
                    "engine": "fallback",
                    "engine_label": "Cloud Fallback Engine",
                }
        except Exception:
            continue

    return None


def stream_media_to_file(
    stream_url: str,
    dest_path: Path,
    job_id: str,
    pct_start: float = 0.0,
    pct_end: float = 85.0,
    label: str = "Downloading stream...",
) -> bool:
    ctx = ssl.create_default_context(cafile=certifi.where())
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    proxy = get_active_proxy()
    if proxy:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy}),
            urllib.request.HTTPSHandler(context=ctx),
        )
    else:
        opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx))

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    }
    req = urllib.request.Request(stream_url, headers=headers)

    try:
        with opener.open(req, timeout=30) as resp, open(dest_path, "wb") as out_file:
            total_str = resp.headers.get("Content-Length")
            total = int(total_str) if total_str and total_str.isdigit() else 0
            downloaded = 0
            start_time = time.time()

            while True:
                chunk = resp.read(64 * 1024)
                if not chunk:
                    break
                out_file.write(chunk)
                downloaded += len(chunk)
                elapsed = time.time() - start_time
                speed = downloaded / elapsed if elapsed > 0 else 0

                pct = pct_start + ((downloaded / total) * (pct_end - pct_start)) if total else min(pct_start + 15, pct_end)
                with JOBS_LOCK:
                    job = JOBS.get(job_id)
                    if job:
                        job["status"] = "downloading"
                        job["progress"] = round(min(pct, 98.0), 1)
                        job["speed"] = f"{format_bytes(speed)}/s" if speed else "Streaming..."
                        if total and speed > 0:
                            eta = (total - downloaded) / speed
                            job["eta"] = f"{int(eta)}s"
        return True
    except Exception as exc:
        with JOBS_LOCK:
            job = JOBS.get(job_id)
            if job:
                job["error"] = f"Stream download failed: {exc}"
        return False


def run_fallback_download_job(
    job_id: str,
    video_id: str,
    quality_id: str,
    title: str,
    start_sec: Optional[float] = None,
    end_sec: Optional[float] = None,
):
    job_dir = DOWNLOAD_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    safe_title = sanitize_filename(title)

    is_clipped = start_sec is not None and end_sec is not None and end_sec > start_sec
    clip_suffix = ""
    if is_clipped:
        clip_suffix = f" [{format_duration(start_sec).replace(':', '-')}_to_{format_duration(end_sec).replace(':', '-')}]"

    stream_info = (FALLBACK_STREAMS_CACHE.get(video_id) or {}).get(quality_id)
    if not stream_info:
        # If cache lost, attempt to re-fetch
        re_info = fetch_youtube_fallback(video_id, f"https://www.youtube.com/watch?v={video_id}")
        if re_info:
            stream_info = (FALLBACK_STREAMS_CACHE.get(video_id) or {}).get(quality_id)

    if not stream_info:
        with JOBS_LOCK:
            job = JOBS.get(job_id)
            if job:
                job["status"] = "error"
                job["error"] = "Fallback stream source not found or expired. Please re-fetch video."
        return

    video_url = stream_info.get("video_url")
    audio_url = stream_info.get("audio_url")
    needs_mux = stream_info.get("needs_mux", False)

    try:
        if quality_id.startswith("audio_"):
            # Audio only
            target_ext = "mp3" if quality_id == "audio_mp3" else "m4a"
            raw_audio = job_dir / "raw_audio.tmp"
            final_file = job_dir / f"{safe_title}{clip_suffix}.{target_ext}"

            success = stream_media_to_file(audio_url or video_url, raw_audio, job_id, 0.0, 75.0, "Downloading Audio")
            if not success or not raw_audio.exists() or raw_audio.stat().st_size == 0:
                raise RuntimeError("Failed to download audio stream.")

            with JOBS_LOCK:
                job = JOBS.get(job_id)
                if job:
                    job["status"] = "processing"
                    job["progress"] = 80.0
                    job["speed"] = "Converting audio"

            # FFmpeg conversion
            cmd = [FFMPEG_PATH, "-y"]
            if is_clipped:
                cmd.extend(["-ss", str(start_sec), "-to", str(end_sec)])
            cmd.extend(["-i", str(raw_audio)])
            if target_ext == "mp3":
                cmd.extend(["-vn", "-acodec", "libmp3lame", "-b:a", "192k"])
            else:
                cmd.extend(["-vn", "-acodec", "copy"])
            cmd.append(str(final_file))

            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            raw_audio.unlink(missing_ok=True)

        elif needs_mux and video_url and audio_url:
            # Separate video and audio streams to mux
            raw_video = job_dir / "raw_video.tmp"
            raw_audio = job_dir / "raw_audio.tmp"
            final_file = job_dir / f"{safe_title}{clip_suffix}.mp4"

            # Download Video track (0% - 50%)
            v_ok = stream_media_to_file(video_url, raw_video, job_id, 0.0, 50.0, "Downloading Video Track")
            if not v_ok or not raw_video.exists():
                raise RuntimeError("Failed to download video stream.")

            # Download Audio track (50% - 85%)
            a_ok = stream_media_to_file(audio_url, raw_audio, job_id, 50.0, 85.0, "Downloading Audio Track")
            if not a_ok or not raw_audio.exists():
                raise RuntimeError("Failed to download audio stream.")

            with JOBS_LOCK:
                job = JOBS.get(job_id)
                if job:
                    job["status"] = "processing"
                    job["progress"] = 88.0
                    job["speed"] = "Muxing video & audio"

            cmd = [FFMPEG_PATH, "-y"]
            if is_clipped:
                cmd.extend(["-ss", str(start_sec), "-to", str(end_sec)])
            cmd.extend([
                "-i", str(raw_video),
                "-i", str(raw_audio),
                "-c:v", "copy",
                "-c:a", "aac",
                "-movflags", "+faststart",
                str(final_file),
            ])
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            raw_video.unlink(missing_ok=True)
            raw_audio.unlink(missing_ok=True)

        else:
            # Single progressive stream with audio
            raw_video = job_dir / "raw_stream.tmp"
            final_file = job_dir / f"{safe_title}{clip_suffix}.mp4"

            v_ok = stream_media_to_file(video_url, raw_video, job_id, 0.0, 80.0, "Downloading Video Stream")
            if not v_ok or not raw_video.exists():
                raise RuntimeError("Failed to download stream.")

            with JOBS_LOCK:
                job = JOBS.get(job_id)
                if job:
                    job["status"] = "processing"
                    job["progress"] = 85.0
                    job["speed"] = "Finalizing video"

            if is_clipped:
                cmd = [
                    FFMPEG_PATH, "-y",
                    "-ss", str(start_sec), "-to", str(end_sec),
                    "-i", str(raw_video),
                    "-c", "copy",
                    "-movflags", "+faststart",
                    str(final_file),
                ]
                subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                raw_video.unlink(missing_ok=True)
            else:
                shutil.move(str(raw_video), str(final_file))

        if not final_file.exists() or final_file.stat().st_size == 0:
            raise RuntimeError("Final video file was not generated.")

        with JOBS_LOCK:
            job = JOBS.get(job_id)
            if job:
                job["status"] = "completed"
                job["progress"] = 100.0
                job["filepath"] = str(final_file)
                job["filename"] = final_file.name
                job["actual_size"] = format_bytes(final_file.stat().st_size)
                job["speed"] = "Complete"
                job["eta"] = "0s"

    except Exception as exc:
        with JOBS_LOCK:
            job = JOBS.get(job_id)
            if job:
                job["status"] = "error"
                job["error"] = f"Fallback error: {exc}"


# ============================================================================
# FLASK WEB ROUTES
# ============================================================================

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/health")
def health():
    cookie_file = get_cookiefile_path()
    return jsonify({
        "status": "ok",
        "ffmpeg": bool(FFMPEG_PATH and (os.path.exists(FFMPEG_PATH) or shutil.which(FFMPEG_PATH))),
        "deno": bool(shutil.which("deno")),
        "yt_dlp": yt_dlp.version.__version__,
        "has_cookies": bool(cookie_file),
        "has_proxy": bool(get_active_proxy()),
    })


@app.route("/api/settings", methods=["GET", "POST"])
def settings():
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        cookies_content = (data.get("cookies") or "").strip()
        proxy = (data.get("proxy") or "").strip()

        if cookies_content:
            normalized = cookies_content.replace("\\n", "\n").replace("\r\n", "\n").replace("\r", "\n")
            COOKIE_TMP_PATH.write_text(normalized, encoding="utf-8")

        if proxy:
            os.environ["YOUTUBE_PROXY"] = proxy
        elif "proxy" in data and not proxy:
            os.environ.pop("YOUTUBE_PROXY", None)

        return jsonify({
            "status": "ok",
            "message": "Cloud settings saved successfully!",
            "has_cookies": bool(get_cookiefile_path()),
            "has_proxy": bool(get_active_proxy()),
        })

    cookie_file = get_cookiefile_path()
    active_proxy = get_active_proxy()
    masked_proxy = None
    if active_proxy:
        if "@" in active_proxy:
            auth_part, host_part = active_proxy.split("@", 1)
            masked_proxy = f"http://***@{host_part}"
        else:
            masked_proxy = active_proxy

    return jsonify({
        "has_cookies": bool(cookie_file),
        "cookies_path": str(cookie_file) if cookie_file else None,
        "has_proxy": bool(active_proxy),
        "active_proxy": masked_proxy,
        "yt_dlp_version": yt_dlp.version.__version__,
        "fallback_ready": True,
        "ffmpeg_ready": bool(FFMPEG_PATH and (os.path.exists(FFMPEG_PATH) or shutil.which(FFMPEG_PATH))),
    })


@app.route("/api/info", methods=["POST"])
def get_info():
    cleanup_old_jobs()
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "Please paste a valid YouTube, Instagram, TikTok, or Facebook link."}), 400

    cookie_file = get_cookiefile_path()
    has_cookies = bool(cookie_file)

    # If cookies are provided, prioritize 'web' / 'mweb' clients which authenticate directly
    if is_youtube_url(url):
        if has_cookies:
            strategies = [["web", "mweb"], ["android_vr", "web"], None]
        else:
            # Datacenter strategy list without cookies
            strategies = [["android_vr", "web"], ["ios", "mweb"], ["tv_embedded", "web_embedded"], None]
    else:
        strategies = [None]

    info = None
    last_err = None
    winning_clients = None

    # Step 1: Attempt standard yt-dlp extraction
    for clients in strategies:
        ydl_opts = build_base_ydl_opts(clients, has_cookies=has_cookies)
        ydl_opts["skip_download"] = True
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                extracted = ydl.extract_info(url, download=False)
            if extracted:
                info = extracted
                winning_clients = clients
                break
        except Exception as exc:
            last_err = str(exc)
            continue

    # Step 2: If yt-dlp failed on YouTube (e.g. bot protection / 429 / datacenter IP block), trigger Fallback Engine!
    if not info and is_youtube_url(url):
        yt_id = extract_youtube_id(url)
        if yt_id:
            fallback_info = fetch_youtube_fallback(yt_id, url)
            if fallback_info:
                return jsonify(fallback_info)

    if not info:
        err_msg = last_err or "Could not extract video metadata."
        if "confirm you’re not a bot" in err_msg or "429" in err_msg or "Sign in" in err_msg:
            err_msg = (
                "YouTube has restricted this cloud datacenter IP. "
                "Please add YouTube Cookies in 'Cloud Setup' on top, or enter a proxy to enable direct downloads."
            )
        return jsonify({"error": err_msg}), 400

    webpage_url = info.get("webpage_url") or url
    CLIENT_CACHE[webpage_url] = winning_clients
    CLIENT_CACHE[url] = winning_clients

    qualities = build_quality_options(info)
    return jsonify({
        "id": info.get("id"),
        "platform": detect_platform(url),
        "title": info.get("title") or "Untitled Video",
        "thumbnail": info.get("thumbnail"),
        "duration": info.get("duration") or 0,
        "duration_string": format_duration(info.get("duration")),
        "uploader": info.get("uploader") or info.get("channel") or "Creator",
        "webpage_url": webpage_url,
        "qualities": qualities,
        "engine": "yt-dlp",
        "engine_label": "High-Speed Native Engine",
    })


def run_download_job(
    job_id: str,
    url: str,
    quality_id: str,
    title: str,
    format_id: Optional[str] = None,
    audio_id: Optional[str] = None,
    start_sec: Optional[float] = None,
    end_sec: Optional[float] = None,
    engine: str = "yt-dlp",
):
    # Route to Fallback Engine if format is from fallback
    yt_id = extract_youtube_id(url)
    if engine == "fallback" or (yt_id and yt_id in FALLBACK_STREAMS_CACHE):
        run_fallback_download_job(job_id, yt_id, quality_id, title, start_sec, end_sec)
        return

    job_dir = DOWNLOAD_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    safe_title = sanitize_filename(title)

    is_clipped = start_sec is not None and end_sec is not None and end_sec > start_sec
    clip_suffix = ""
    if is_clipped:
        clip_suffix = f" [{format_duration(start_sec).replace(':', '-')}_to_{format_duration(end_sec).replace(':', '-')}]"

    outtmpl = str(job_dir / f"{safe_title}{clip_suffix}.%(ext)s")

    def progress_hook(d):
        status = d.get("status")
        with JOBS_LOCK:
            job = JOBS.get(job_id)
            if not job:
                return
            if status == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                downloaded = d.get("downloaded_bytes") or 0
                pct = (downloaded / total * 100) if total else 50.0
                speed = d.get("speed")
                eta = d.get("eta")
                job["status"] = "downloading"
                job["progress"] = round(min(pct, 98.0), 1)
                job["speed"] = f"{format_bytes(speed)}/s" if speed else "Clipping..."
                job["eta"] = f"{int(eta)}s" if eta is not None else "—"
            elif status == "finished":
                job["status"] = "processing"
                job["progress"] = 99.0
                job["speed"] = "Trimming & Muxing" if is_clipped else "Muxing"
                job["eta"] = "Almost done"

    cached = CLIENT_CACHE.get(url)
    cookie_file = get_cookiefile_path()
    has_cookies = bool(cookie_file)

    if is_youtube_url(url):
        if has_cookies:
            strategies = [cached] if cached else [["web", "mweb"], None]
        else:
            strategies = [cached] + [s for s in [["android_vr", "web"], ["ios", "mweb"], None] if s != cached]
    else:
        strategies = [None]

    last_err = None
    for clients in strategies:
        ydl_opts = build_base_ydl_opts(clients, has_cookies=has_cookies)
        ydl_opts["outtmpl"] = outtmpl
        ydl_opts["ffmpeg_location"] = FFMPEG_PATH
        ydl_opts["progress_hooks"] = [progress_hook]

        if is_clipped:
            ydl_opts["download_ranges"] = download_range_func(None, [(start_sec, end_sec)])
            ydl_opts["force_keyframes_at_cuts"] = True

        if quality_id == "audio_mp3":
            ydl_opts["format"] = "bestaudio/best"
            ydl_opts["postprocessors"] = [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }]
        elif quality_id == "audio_m4a":
            ydl_opts["format"] = "bestaudio[ext=m4a]/bestaudio/best"
            ydl_opts["postprocessors"] = [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "m4a",
            }]
        elif quality_id.startswith("video_"):
            height = quality_id.split("_", 1)[1]
            if format_id and audio_id and not format_id.startswith("fallback_"):
                exact_combo = f"{format_id}+{audio_id}"
            elif format_id and not format_id.startswith("fallback_"):
                exact_combo = format_id
            else:
                exact_combo = None

            if height.isdigit():
                fallback_combo = (
                    f"bestvideo[height<={height}][ext=mp4]+bestaudio[ext=m4a]/"
                    f"bestvideo[height<={height}]+bestaudio/"
                    f"best[height<={height}]/best"
                )
            else:
                fallback_combo = "bestvideo+bestaudio/best"

            ydl_opts["format"] = f"{exact_combo}/{fallback_combo}" if exact_combo else fallback_combo
            ydl_opts["merge_output_format"] = "mp4"
        else:
            ydl_opts["format"] = "bestvideo+bestaudio/best"
            ydl_opts["merge_output_format"] = "mp4"

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])

            files = [p for p in job_dir.iterdir() if p.is_file()]
            if not files:
                raise RuntimeError("Output file was not created.")
            final_file = max(files, key=lambda p: p.stat().st_size)

            with JOBS_LOCK:
                job = JOBS.get(job_id)
                if job:
                    job["status"] = "completed"
                    job["progress"] = 100.0
                    job["filepath"] = str(final_file)
                    job["filename"] = final_file.name
                    job["actual_size"] = format_bytes(final_file.stat().st_size)
                    job["speed"] = "Complete"
                    job["eta"] = "0s"
            return
        except Exception as exc:
            last_err = str(exc)
            for p in job_dir.iterdir():
                if p.is_file():
                    try:
                        p.unlink(missing_ok=True)
                    except OSError:
                        pass
            continue

    # If yt-dlp failed and this is YouTube, try Fallback download
    if yt_id:
        fb_info = fetch_youtube_fallback(yt_id, url)
        if fb_info:
            run_fallback_download_job(job_id, yt_id, quality_id, title, start_sec, end_sec)
            return

    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job:
            job["status"] = "error"
            job["error"] = last_err or "Download failed across all engines."


@app.route("/api/download", methods=["POST"])
def start_download():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    quality_id = (data.get("quality_id") or "video_1080").strip()
    title = (data.get("title") or "video").strip()
    format_id = data.get("format_id")
    audio_id = data.get("audio_id")
    engine = data.get("engine") or "yt-dlp"

    start_sec = parse_timestamp(data.get("start_time"))
    end_sec = parse_timestamp(data.get("end_time"))
    duration = float(data.get("duration") or 0)

    clip_requested = False
    if start_sec is not None and end_sec is not None:
        if end_sec <= start_sec:
            return jsonify({"error": "End time must be greater than Start time."}), 400
        if start_sec > 0 or (duration > 0 and end_sec < int(duration)):
            clip_requested = True

    if not url:
        return jsonify({"error": "Missing video URL."}), 400

    job_id = uuid.uuid4().hex[:12]
    with JOBS_LOCK:
        JOBS[job_id] = {
            "id": job_id,
            "status": "queued",
            "progress": 0.0,
            "speed": "Starting...",
            "eta": "—",
            "filepath": None,
            "filename": None,
            "actual_size": None,
            "error": None,
            "created_at": time.time(),
        }

    thread = threading.Thread(
        target=run_download_job,
        args=(
            job_id,
            url,
            quality_id,
            title,
            format_id,
            audio_id,
            start_sec if clip_requested else None,
            end_sec if clip_requested else None,
            engine,
        ),
        daemon=True,
    )
    thread.start()
    return jsonify({"job_id": job_id})


@app.route("/api/progress/<job_id>")
def get_progress(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return jsonify({"error": "Job not found"}), 404
        return jsonify({
            "id": job["id"],
            "status": job["status"],
            "progress": job["progress"],
            "speed": job["speed"],
            "eta": job["eta"],
            "filename": job["filename"],
            "actual_size": job["actual_size"],
            "error": job["error"],
        })


@app.route("/api/file/<job_id>")
def download_file(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job or job["status"] != "completed" or not job["filepath"]:
            return jsonify({"error": "File not ready or expired"}), 404
        filepath = job["filepath"]
        filename = job["filename"]

    return send_file(filepath, as_attachment=True, download_name=filename)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    try:
        import webview
        threading.Thread(
            target=lambda: app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False, threaded=True),
            daemon=True,
        ).start()
        time.sleep(0.8)
        webview.create_window("FLUXTUBE", f"http://127.0.0.1:{port}", width=1240, height=820, background_color="#09080B")
        webview.start()
    except ImportError:
        app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
