import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional

import requests
from flask import Flask, jsonify, render_template, request, send_file

try:
    import imageio_ffmpeg
except ImportError:
    imageio_ffmpeg = None


def resolve_ffmpeg() -> str:
    sys_ffmpeg = shutil.which("ffmpeg")
    if sys_ffmpeg:
        return sys_ffmpeg
    if imageio_ffmpeg is not None:
        return imageio_ffmpeg.get_ffmpeg_exe()
    return "ffmpeg"


FFMPEG_PATH = resolve_ffmpeg()
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()

app = Flask(__name__)
# 250 MB upload cap (keeps Render Free Tier 512 MB RAM safe while supporting podcasts)
app.config["MAX_CONTENT_LENGTH"] = 250 * 1024 * 1024

WORK_DIR = Path(tempfile.gettempdir()) / "fluxtube_ai_clips"
WORK_DIR.mkdir(parents=True, exist_ok=True)

JOBS: Dict[str, dict] = {}
JOBS_LOCK = threading.Lock()


def cleanup_old_jobs(max_age_seconds: int = 1200):
    now = time.time()
    with JOBS_LOCK:
        expired = [
            jid for jid, job in JOBS.items()
            if now - job.get("created_at", now) > max_age_seconds
        ]
        for jid in expired:
            job = JOBS.pop(jid, None)
            if job and job.get("dir"):
                shutil.rmtree(job["dir"], ignore_errors=True)


def format_bytes(size: Optional[int]) -> str:
    if not size or size <= 0:
        return "0 B"
    val = float(size)
    for unit in ["B", "KB", "MB", "GB"]:
        if val < 1024.0:
            return f"{val:.1f} {unit}" if unit in ("MB", "GB") else f"{int(val)} {unit}"
        val /= 1024.0
    return f"{val:.1f} TB"


def format_clock(seconds: float) -> str:
    sec = max(0, int(seconds or 0))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def format_ass_time(seconds: float) -> str:
    sec = max(0.0, float(seconds))
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    cs = int(round((sec - int(sec)) * 100))
    if cs >= 100:
        cs = 99
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def sanitize_filename(name: str) -> str:
    stem = Path(name or "clip").stem
    cleaned = re.sub(r'[\\/*?:"<>|]+', "", stem).strip()[:60]
    return cleaned or "clip"


def update_job(job_id: str, **kwargs):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job:
            job.update(kwargs)


def probe_duration(video_path: Path, fallback: float = 60.0) -> float:
    try:
        res = subprocess.run(
            [FFMPEG_PATH, "-i", str(video_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=15,
        )
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", res.stderr or "")
        if m:
            return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    except Exception:
        pass
    return max(5.0, float(fallback or 60.0))


def extract_audio_for_groq(video_path: Path, audio_path: Path) -> bool:
    """Extract lightweight mono 32kbps MP3 (~14 MB per hour, fits Groq's 25 MB free limit)."""
    cmd = [
        FFMPEG_PATH, "-y",
        "-i", str(video_path),
        "-vn", "-ac", "1", "-ar", "16000", "-b:a", "32k",
        str(audio_path),
    ]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180)
    return res.returncode == 0 and audio_path.exists() and audio_path.stat().st_size < 24 * 1024 * 1024


def transcribe_with_groq(audio_path: Path, api_key: str) -> Optional[dict]:
    url = "https://api.groq.com/openai/v1/audio/transcriptions"
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        with open(audio_path, "rb") as f:
            files = {"file": (audio_path.name, f, "audio/mpeg")}
            data = [
                ("model", "whisper-large-v3-turbo"),
                ("response_format", "verbose_json"),
                ("timestamp_granularities[]", "word"),
                ("timestamp_granularities[]", "segment"),
            ]
            resp = requests.post(url, headers=headers, files=files, data=data, timeout=180)
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return None


def pick_viral_clips_with_groq(
    transcript: dict,
    total_duration: float,
    num_clips: int,
    clip_len: int,
    api_key: str,
) -> Optional[List[dict]]:
    segments = transcript.get("segments") or []
    if not segments:
        return None

    lines = []
    for seg in segments[:260]:
        st = round(float(seg.get("start", 0)), 1)
        en = round(float(seg.get("end", 0)), 1)
        txt = (seg.get("text") or "").strip()
        if txt:
            lines.append(f"[{st}s - {en}s] {txt}")
    condensed = "\n".join(lines)[:14000]

    prompt = (
        f"You are an expert short-form viral podcast editor. "
        f"Pick the {num_clips} most engaging, high-retention moments from this transcript "
        f"(total video length {int(total_duration)}s). "
        f"Each clip MUST be approximately {clip_len} seconds long (between {max(12, clip_len - 5)}s and {clip_len + 5}s), "
        f"non-overlapping, and start on a strong hook.\n"
        f"Return ONLY valid JSON array of objects with exact keys: "
        f"start (number in seconds), end (number in seconds), title (short catchy 4-7 word title), score (integer 82-99).\n\n"
        f"Transcript:\n{condensed}"
    )

    try:
        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "llama-3.1-8b-instant",
                "temperature": 0.3,
                "messages": [
                    {"role": "system", "content": "Return ONLY a raw JSON array with no markdown fences."},
                    {"role": "user", "content": prompt},
                ],
            },
            timeout=60,
        )
        if resp.status_code != 200:
            return None
        content = resp.json()["choices"][0]["message"]["content"].strip()
        match = re.search(r"\[.*\]", content, re.S)
        raw = json.loads(match.group(0) if match else content)
        clips = []
        for item in raw[:num_clips]:
            st = max(0.0, float(item.get("start", 0)))
            en = min(total_duration, float(item.get("end", st + clip_len)))
            if en - st < 8:
                en = min(total_duration, st + clip_len)
            if en - st > clip_len + 8:
                en = min(total_duration, st + clip_len)
            clips.append({
                "start": round(st, 2),
                "end": round(en, 2),
                "title": str(item.get("title") or "Viral Podcast Moment")[:64],
                "score": int(item.get("score") or 91),
            })
        return clips if clips else None
    except Exception:
        return None


def build_fallback_clips(total_duration: float, num_clips: int, clip_len: int) -> List[dict]:
    """Smart evenly-spaced highlight selector when GROQ_API_KEY is not configured."""
    dur = max(10.0, total_duration)
    eff_len = min(float(clip_len), dur)
    if dur <= eff_len + 4 or num_clips == 1:
        start = max(0.0, (dur - eff_len) * 0.15)
        return [{
            "start": round(start, 2),
            "end": round(min(dur, start + eff_len), 2),
            "title": "Featured Vertical Highlight",
            "score": 92,
        }]

    usable_start = min(dur * 0.08, 15.0)
    usable_end = max(usable_start + eff_len, dur - min(dur * 0.05, 10.0))
    span = max(0.0, usable_end - usable_start - eff_len)
    step = span / max(1, num_clips - 1)

    labels = ["Opening Hook Highlight", "Key Podcast Insight", "High-Impact Takeaway"]
    clips = []
    for i in range(num_clips):
        st = round(usable_start + step * i, 2)
        en = round(min(dur, st + eff_len), 2)
        clips.append({
            "start": st,
            "end": en,
            "title": labels[i % len(labels)],
            "score": 94 - i * 3,
        })
    return clips


def generate_ass_subtitles(
    transcript: Optional[dict],
    clip_start: float,
    clip_end: float,
    ass_path: Path,
    caption_style: str,
) -> bool:
    if caption_style == "none" or not transcript:
        return False

    style_map = {
        "viral_yellow": ("&H0000E5FF", "&H00000000"),
        "crimson_pop": ("&H006C3EFF", "&H00000000"),
        "clean_white": ("&H00FFFFFF", "&H00000000"),
    }
    primary_col, outline_col = style_map.get(caption_style, style_map["viral_yellow"])

    words = transcript.get("words") or []
    clip_words = [
        w for w in words
        if float(w.get("end", 0)) > clip_start and float(w.get("start", 0)) < clip_end
    ]

    events = []
    if clip_words:
        chunk_size = 3
        for i in range(0, len(clip_words), chunk_size):
            chunk = clip_words[i:i + chunk_size]
            st = max(0.0, float(chunk[0].get("start", clip_start)) - clip_start)
            en = max(st + 0.25, float(chunk[-1].get("end", clip_start + 0.5)) - clip_start)
            text = " ".join((w.get("word") or "").strip() for w in chunk).strip().upper()
            text = text.replace("{", "").replace("}", "").replace("\n", " ")
            if text:
                events.append((st, en, text))
    else:
        for seg in (transcript.get("segments") or []):
            if float(seg.get("end", 0)) > clip_start and float(seg.get("start", 0)) < clip_end:
                st = max(0.0, float(seg.get("start", clip_start)) - clip_start)
                en = max(st + 0.5, float(seg.get("end", clip_start + 1.0)) - clip_start)
                text = (seg.get("text") or "").strip().upper().replace("\n", " ")
                if text:
                    events.append((st, en, text[:48]))

    if not events:
        return False

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 720
PlayResY: 1280
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,DejaVu Sans,54,{primary_col},&H00FFFFFF,{outline_col},&H96000000,-1,0,0,0,100,100,1,0,1,4.5,2.5,2,42,42,210,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    body_lines = [
        f"Dialogue: 0,{format_ass_time(st)},{format_ass_time(en)},Default,,0,0,0,,{txt}"
        for st, en, txt in events
    ]
    ass_path.write_text(header + "\n".join(body_lines) + "\n", encoding="utf-8")
    return True


def render_vertical_clip(
    input_path: Path,
    output_path: Path,
    start_sec: float,
    end_sec: float,
    framing: str,
    effect: str,
    ass_path: Optional[Path],
) -> Optional[str]:
    clip_dur = max(1.0, end_sec - start_sec)

    fx_filters = []
    if effect == "cinematic":
        fx_filters.append("eq=contrast=1.08:saturation=1.18:brightness=0.01")
        fx_filters.append("vignette=PI/5")
    elif effect == "warm_podcast":
        fx_filters.append("eq=contrast=1.06:saturation=1.12:brightness=0.02")
        fx_filters.append("colorbalance=rs=0.04:gs=0.01:bs=-0.04")

    if effect in ("cinematic", "warm_podcast"):
        fx_filters.append(
            f"drawbox=x=0:y=ih-12:w='iw*min(1,t/{clip_dur:.2f})':h=12:color=#FF3E6C@0.92:t=fill"
        )

    if ass_path and ass_path.exists():
        esc_ass = str(ass_path).replace("\\", "/").replace(":", "\\:")
        fx_filters.append(f"ass='{esc_ass}'")

    post_chain = ("," + ",".join(fx_filters)) if fx_filters else ""

    if framing == "blur_bg":
        filter_complex = (
            "[0:v]split=2[bg][fg];"
            "[bg]scale=720:1280:force_original_aspect_ratio=increase,"
            "crop=720:1280,boxblur=22:4,eq=brightness=-0.12[bg_blur];"
            "[fg]scale=720:-2:force_original_aspect_ratio=decrease[fg_scaled];"
            f"[bg_blur][fg_scaled]overlay=(W-w)/2:(H-h)/2{post_chain}[vout]"
        )
    else:
        filter_complex = (
            f"[0:v]scale=720:1280:force_original_aspect_ratio=increase,"
            f"crop=720:1280{post_chain}[vout]"
        )

    cmd = [
        FFMPEG_PATH, "-y",
        "-ss", f"{start_sec:.2f}",
        "-t", f"{clip_dur:.2f}",
        "-i", str(input_path),
        "-filter_complex", filter_complex,
        "-map", "[vout]",
        "-map", "0:a?",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "24",
        "-r", "30",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        str(output_path),
    ]

    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=240)
    if res.returncode != 0 or not output_path.exists():
        lines = (res.stderr or "FFmpeg render failed").strip().splitlines()
        return lines[-1] if lines else "Render failed"
    return None


def run_ai_clip_job(
    job_id: str,
    job_dir: Path,
    input_path: Path,
    orig_name: str,
    duration_hint: float,
    num_clips: int,
    clip_len: int,
    framing: str,
    caption_style: str,
    effect: str,
):
    try:
        update_job(job_id, status="processing", progress=8, stage="Analyzing podcast video...")
        total_dur = probe_duration(input_path, fallback=duration_hint)
        api_key = os.environ.get("GROQ_API_KEY", "").strip() or GROQ_API_KEY

        transcript = None
        ai_mode = "smart_auto"
        selected_clips = None

        if api_key:
            update_job(job_id, progress=16, stage="Extracting audio for AI speech recognition...")
            audio_path = job_dir / "audio.mp3"
            if extract_audio_for_groq(input_path, audio_path):
                update_job(job_id, progress=28, stage="AI transcribing speech (Groq Whisper)...")
                transcript = transcribe_with_groq(audio_path, api_key)
                try:
                    audio_path.unlink(missing_ok=True)
                except OSError:
                    pass

            if transcript:
                update_job(job_id, progress=42, stage="AI picking best viral 30s hooks...")
                selected_clips = pick_viral_clips_with_groq(
                    transcript, total_dur, num_clips, clip_len, api_key
                )
                ai_mode = "groq_ai"

        if not selected_clips:
            selected_clips = build_fallback_clips(total_dur, num_clips, clip_len)

        safe_stem = sanitize_filename(orig_name)
        rendered_clips = []
        total_to_render = len(selected_clips)

        for idx, clip in enumerate(selected_clips):
            pct = 48 + int((idx / max(1, total_to_render)) * 46)
            update_job(
                job_id,
                progress=pct,
                stage=f"Rendering Vertical Clip {idx + 1}/{total_to_render} (9:16 + Captions + FX)...",
            )

            ass_path = job_dir / f"clip_{idx + 1}.ass"
            has_subs = generate_ass_subtitles(
                transcript, clip["start"], clip["end"], ass_path, caption_style
            )

            out_name = f"{safe_stem}_clip{idx + 1}_9x16.mp4"
            out_path = job_dir / out_name
            err = render_vertical_clip(
                input_path=input_path,
                output_path=out_path,
                start_sec=clip["start"],
                end_sec=clip["end"],
                framing=framing,
                effect=effect,
                ass_path=ass_path if has_subs else None,
            )
            if err:
                raise RuntimeError(err)

            size_bytes = out_path.stat().st_size
            rendered_clips.append({
                "index": idx,
                "title": clip["title"],
                "score": clip["score"],
                "start_str": format_clock(clip["start"]),
                "end_str": format_clock(clip["end"]),
                "duration_sec": int(round(clip["end"] - clip["start"])),
                "has_captions": has_subs,
                "filename": out_name,
                "filepath": str(out_path),
                "size_label": format_bytes(size_bytes),
                "preview_url": f"/api/clip/{job_id}/{idx}/preview",
                "download_url": f"/api/clip/{job_id}/{idx}/download",
            })

        update_job(
            job_id,
            status="completed",
            progress=100,
            stage=f"Done — {len(rendered_clips)} vertical 9:16 clips ready!",
            ai_mode=ai_mode,
            clips=rendered_clips,
        )
    except Exception as exc:
        update_job(job_id, status="error", error=str(exc), stage="Generation failed")
    finally:
        try:
            input_path.unlink(missing_ok=True)
        except OSError:
            pass


@app.errorhandler(413)
def file_too_large(_e):
    return jsonify({"error": "File is over 250 MB. Please upload a smaller podcast file."}), 413


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/health")
def health():
    has_groq = bool(os.environ.get("GROQ_API_KEY", "").strip() or GROQ_API_KEY)
    return jsonify({
        "status": "ok",
        "ffmpeg": bool(FFMPEG_PATH and (os.path.exists(FFMPEG_PATH) or shutil.which(FFMPEG_PATH))),
        "ai_captions_enabled": has_groq,
    })


@app.route("/api/generate-clips", methods=["POST"])
def generate_clips():
    cleanup_old_jobs()

    upload = request.files.get("file")
    if not upload or not upload.filename:
        return jsonify({"error": "Please upload a podcast or video file first."}), 400

    num_clips = max(1, min(3, int(request.form.get("num_clips") or 3)))
    clip_len = max(15, min(60, int(request.form.get("clip_len") or 30)))
    framing = (request.form.get("framing") or "blur_bg").strip()
    caption_style = (request.form.get("caption_style") or "viral_yellow").strip()
    effect = (request.form.get("effect") or "cinematic").strip()
    duration_hint = float(request.form.get("duration") or 60)

    job_id = uuid.uuid4().hex[:12]
    job_dir = WORK_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    orig_ext = Path(upload.filename).suffix.lower() or ".mp4"
    if len(orig_ext) > 8 or not re.fullmatch(r"\.[a-z0-9]+", orig_ext):
        orig_ext = ".mp4"

    input_path = job_dir / f"source{orig_ext}"
    upload.save(str(input_path))

    with JOBS_LOCK:
        JOBS[job_id] = {
            "id": job_id,
            "dir": str(job_dir),
            "status": "queued",
            "progress": 5,
            "stage": "Queued for AI clipping...",
            "ai_mode": None,
            "clips": [],
            "error": None,
            "created_at": time.time(),
        }

    thread = threading.Thread(
        target=run_ai_clip_job,
        args=(
            job_id,
            job_dir,
            input_path,
            upload.filename,
            duration_hint,
            num_clips,
            clip_len,
            framing,
            caption_style,
            effect,
        ),
        daemon=True,
    )
    thread.start()

    return jsonify({"ok": True, "job_id": job_id})


@app.route("/api/job/<job_id>")
def get_job_status(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return jsonify({"error": "Job not found or expired."}), 404
        public_clips = [
            {k: v for k, v in c.items() if k != "filepath"}
            for c in (job.get("clips") or [])
        ]
        return jsonify({
            "id": job["id"],
            "status": job["status"],
            "progress": job["progress"],
            "stage": job["stage"],
            "ai_mode": job["ai_mode"],
            "clips": public_clips,
            "error": job["error"],
        })


@app.route("/api/clip/<job_id>/<int:idx>/<action>")
def serve_clip(job_id, idx, action):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job or idx < 0 or idx >= len(job.get("clips") or []):
            return jsonify({"error": "Clip not found or expired."}), 404
        clip = job["clips"][idx]
        filepath = clip["filepath"]
        filename = clip["filename"]

    if not os.path.exists(filepath):
        return jsonify({"error": "Clip file expired."}), 404

    as_attachment = action == "download"
    return send_file(filepath, as_attachment=as_attachment, download_name=filename, mimetype="video/mp4")


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5050, debug=False, threaded=True)
