import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

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

app = Flask(__name__)
# Limit uploads to 150 MB to stay safe on Render Free Tier (512 MB RAM)
app.config["MAX_CONTENT_LENGTH"] = 150 * 1024 * 1024

WORK_DIR = Path(tempfile.gettempdir()) / "fluxtube_studio"
WORK_DIR.mkdir(parents=True, exist_ok=True)

JOBS = {}
JOBS_LOCK = threading.Lock()


def cleanup_old_jobs(max_age_seconds: int = 900):
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


def format_duration(seconds: float) -> str:
    sec = max(0, int(seconds or 0))
    h, rem = divmod(sec, 3600)
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
    stem = Path(name or "video").stem
    cleaned = re.sub(r'[\\/*?:"<>|]+', "", stem).strip()[:90]
    return cleaned or "video"


@app.errorhandler(413)
def file_too_large(_e):
    return jsonify({"error": "File is too large. Please keep videos under 150 MB."}), 413


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/health")
def health():
    return jsonify({
        "status": "ok",
        "ffmpeg": bool(FFMPEG_PATH and (os.path.exists(FFMPEG_PATH) or shutil.which(FFMPEG_PATH))),
        "mode": "studio",
    })


@app.route("/api/process", methods=["POST"])
def process_video():
    cleanup_old_jobs()

    upload = request.files.get("file")
    if not upload or not upload.filename:
        return jsonify({"error": "Please select a video file first."}), 400

    mode = (request.form.get("mode") or "trim_mp4").strip()
    start_sec = parse_timestamp(request.form.get("start_time"))
    end_sec = parse_timestamp(request.form.get("end_time"))
    duration = float(request.form.get("duration") or 0)

    is_trimmed = False
    if start_sec is not None and end_sec is not None:
        if end_sec <= start_sec:
            return jsonify({"error": "End time must be greater than Start time."}), 400
        if start_sec > 0 or (duration > 0 and end_sec < int(duration)):
            is_trimmed = True

    job_id = uuid.uuid4().hex[:12]
    job_dir = WORK_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    orig_ext = Path(upload.filename).suffix.lower() or ".mp4"
    if len(orig_ext) > 8 or not re.fullmatch(r"\.[a-z0-9]+", orig_ext):
        orig_ext = ".mp4"

    input_path = job_dir / f"input{orig_ext}"
    upload.save(str(input_path))

    safe_stem = sanitize_filename(upload.filename)
    clip_tag = ""
    if is_trimmed:
        clip_tag = f" [{format_duration(start_sec).replace(':', '-')}_to_{format_duration(end_sec).replace(':', '-')}]"

    cmd = [FFMPEG_PATH, "-y"]
    if is_trimmed:
        cmd.extend(["-ss", f"{start_sec:.2f}", "-to", f"{end_sec:.2f}"])
    cmd.extend(["-i", str(input_path)])

    if mode == "audio_mp3":
        out_name = f"{safe_stem}{clip_tag}.mp3"
        out_path = job_dir / out_name
        cmd.extend(["-vn", "-c:a", "libmp3lame", "-b:a", "320k", str(out_path)])
    elif mode == "audio_m4a":
        out_name = f"{safe_stem}{clip_tag}.m4a"
        out_path = job_dir / out_name
        cmd.extend(["-vn", "-c:a", "aac", "-b:a", "192k", str(out_path)])
    elif mode == "compress_720":
        out_name = f"{safe_stem}{clip_tag}_720p.mp4"
        out_path = job_dir / out_name
        cmd.extend([
            "-vf", "scale=-2:'min(720,ih)'",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            str(out_path),
        ])
    elif mode == "compress_480":
        out_name = f"{safe_stem}{clip_tag}_480p.mp4"
        out_path = job_dir / out_name
        cmd.extend([
            "-vf", "scale=-2:'min(480,ih)'",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
            "-c:a", "aac", "-b:a", "96k",
            "-movflags", "+faststart",
            str(out_path),
        ])
    elif mode == "mute_video":
        out_name = f"{safe_stem}{clip_tag}_muted.mp4"
        out_path = job_dir / out_name
        cmd.extend(["-an", "-c:v", "copy", "-movflags", "+faststart", str(out_path)])
    else:
        # Fast lossless trim / remux to MP4
        out_name = f"{safe_stem}{clip_tag}_clip.mp4"
        out_path = job_dir / out_name
        cmd.extend(["-c", "copy", "-movflags", "+faststart", str(out_path)])

    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=240,
        )
        # If stream copy fails on uncommon codecs, retry with fast H.264 re-encode
        if result.returncode != 0 and mode in ("trim_mp4", "mute_video"):
            fallback_cmd = [FFMPEG_PATH, "-y"]
            if is_trimmed:
                fallback_cmd.extend(["-ss", f"{start_sec:.2f}", "-to", f"{end_sec:.2f}"])
            fallback_cmd.extend(["-i", str(input_path), "-c:v", "libx264", "-preset", "veryfast", "-crf", "23"])
            if mode == "mute_video":
                fallback_cmd.append("-an")
            else:
                fallback_cmd.extend(["-c:a", "aac", "-b:a", "160k"])
            fallback_cmd.extend(["-movflags", "+faststart", str(out_path)])
            result = subprocess.run(
                fallback_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=240,
            )

        if result.returncode != 0 or not out_path.exists():
            shutil.rmtree(job_dir, ignore_errors=True)
            err_tail = (result.stderr or "FFmpeg processing failed.").strip().splitlines()[-1:]
            return jsonify({"error": err_tail[0] if err_tail else "Processing failed."}), 500
    except subprocess.TimeoutExpired:
        shutil.rmtree(job_dir, ignore_errors=True)
        return jsonify({"error": "Processing timed out. Try a shorter clip or lighter preset."}), 504
    finally:
        try:
            input_path.unlink(missing_ok=True)
        except OSError:
            pass

    size_bytes = out_path.stat().st_size
    with JOBS_LOCK:
        JOBS[job_id] = {
            "id": job_id,
            "dir": str(job_dir),
            "filepath": str(out_path),
            "filename": out_name,
            "size_bytes": size_bytes,
            "actual_size": format_bytes(size_bytes),
            "created_at": time.time(),
        }

    return jsonify({
        "ok": True,
        "job_id": job_id,
        "filename": out_name,
        "actual_size": format_bytes(size_bytes),
        "download_url": f"/api/file/{job_id}",
    })


@app.route("/api/file/<job_id>")
def download_file(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job or not job.get("filepath") or not os.path.exists(job["filepath"]):
            return jsonify({"error": "File expired or not found. Please process again."}), 404
        filepath = job["filepath"]
        filename = job["filename"]

    return send_file(filepath, as_attachment=True, download_name=filename)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5050, debug=False, threaded=True)
