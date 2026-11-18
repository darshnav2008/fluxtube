import os
import re
import time
import uuid
import threading
import tempfile
from pathlib import Path
from typing import Optional

import certifi
import imageio_ffmpeg
import yt_dlp
from yt_dlp.utils import download_range_func
from flask import Flask, jsonify, render_template, request, send_file

# Ensure yt-dlp uses certifi CA bundle on macOS Python 3.16
os.environ.setdefault("SSL_CERT_FILE", certifi.where())
os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())

FFMPEG_PATH = imageio_ffmpeg.get_ffmpeg_exe()

app = Flask(__name__)

DOWNLOAD_DIR = Path(tempfile.gettempdir()) / "fluxtube_downloads"
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

JOBS = {}
JOBS_LOCK = threading.Lock()


def detect_platform(url: str) -> str:
    u = (url or "").lower()
    if "instagram.com" in u:
        return "Instagram"
    if "tiktok.com" in u:
        return "TikTok"
    if "twitter.com" in u or "x.com" in u:
        return "X / Twitter"
    if "youtube.com" in u or "youtu.be" in u:
        return "YouTube"
    return "Video Link"


def cleanup_old_jobs(max_age_seconds: int = 3600):
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
    if len(parts) == 2:
        try:
            return max(0.0, int(parts[0]) * 60 + float(parts[1]))
        except ValueError:
            return None
    if len(parts) == 3:
        try:
            return max(0.0, int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2]))
        except ValueError:
            return None
    return None


def sanitize_filename(name: str) -> str:
    return re.sub(r'[\\/*?:"<>|]+', "", name).strip()[:110] or "video"


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
        # Fallback for single-stream platforms (e.g., Instagram/TikTok/X direct MP4)
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


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/health")
def health():
    return jsonify({
        "status": "ok",
        "ffmpeg": bool(FFMPEG_PATH and os.path.exists(FFMPEG_PATH)),
        "yt_dlp": yt_dlp.version.__version__,
    })


@app.route("/api/info", methods=["POST"])
def get_info():
    cleanup_old_jobs()
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "Please paste a valid YouTube, Instagram, TikTok, or X link."}), 400

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        return jsonify({"error": f"Could not fetch video info: {str(exc)}"}), 400

    if not info:
        return jsonify({"error": "No video metadata returned."}), 404

    qualities = build_quality_options(info)
    return jsonify({
        "id": info.get("id"),
        "platform": detect_platform(url),
        "title": info.get("title") or "Untitled Video",
        "thumbnail": info.get("thumbnail"),
        "duration": info.get("duration") or 0,
        "duration_string": format_duration(info.get("duration")),
        "uploader": info.get("uploader") or info.get("channel") or "Creator",
        "webpage_url": info.get("webpage_url") or url,
        "qualities": qualities,
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
):
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

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "outtmpl": outtmpl,
        "ffmpeg_location": FFMPEG_PATH,
        "progress_hooks": [progress_hook],
    }

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
        if format_id and audio_id:
            exact_combo = f"{format_id}+{audio_id}"
        elif format_id:
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
    except Exception as exc:
        with JOBS_LOCK:
            job = JOBS.get(job_id)
            if job:
                job["status"] = "error"
                job["error"] = str(exc)


@app.route("/api/download", methods=["POST"])
def start_download():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    quality_id = (data.get("quality_id") or "video_1080").strip()
    title = (data.get("title") or "video").strip()
    format_id = data.get("format_id")
    audio_id = data.get("audio_id")

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


@app.route("/api/checkout", methods=["POST"])
def create_checkout():
    data = request.get_json(silent=True) or {}
    plan = (data.get("plan") or "pro_monthly").strip()
    method = (data.get("method") or "upi").strip()
    amount = 199 if plan == "pro_monthly" else 99
    txn_id = f"FLX-{uuid.uuid4().hex[:8].upper()}"
    return jsonify({
        "status": "paid",
        "transaction_id": txn_id,
        "plan": plan,
        "method": method,
        "amount_inr": amount,
        "message": "Payment verified. FLUXTUBE Pro unlocked!",
    })


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5050, debug=False, threaded=True)
