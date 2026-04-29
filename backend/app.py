"""Flask app: upload a video, return generated short-form clips."""

from __future__ import annotations

import json
import os
import random
import string
import threading
from datetime import datetime
from pathlib import Path

from flask import (
    Flask,
    abort,
    jsonify,
    render_template,
    request,
    send_from_directory,
    url_for,
)

from analyzer import find_best_moments, has_ffmpeg, probe_duration
from clipper import CTAS, HOOKS, generate_clip


BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
CLIP_DIR = BASE_DIR / "clips"
JOB_DIR = BASE_DIR / "jobs"
for d in (UPLOAD_DIR, CLIP_DIR, JOB_DIR):
    d.mkdir(parents=True, exist_ok=True)

ALLOWED_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "500"))

app = Flask(__name__, static_folder="static", template_folder="templates")
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024


def _job_id() -> str:
    ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    return f"{ts}-{suffix}"


def _job_file(job_id: str) -> Path:
    return JOB_DIR / f"{job_id}.json"


def _save_job(job_id: str, data: dict) -> None:
    _job_file(job_id).write_text(json.dumps(data, indent=2))


def _load_job(job_id: str) -> dict | None:
    p = _job_file(job_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return None


def _process(
    job_id: str,
    src_path: Path,
    n_clips: int,
    clip_len: float,
    safety_boost: bool = False,
) -> None:
    """Background worker: analyze + generate clips, update job status."""
    job = _load_job(job_id) or {}
    try:
        job["status"] = "analyzing"
        _save_job(job_id, job)

        moments = find_best_moments(
            str(src_path), n_clips=n_clips, clip_len=clip_len
        )
        if not moments:
            job["status"] = "error"
            job["error"] = "No moments could be detected (video may be too short or unreadable)."
            _save_job(job_id, job)
            return

        job["moments"] = [m.to_dict() for m in moments]
        job["status"] = "clipping"
        job["clips"] = []
        _save_job(job_id, job)

        clips_out = CLIP_DIR / job_id
        clips_out.mkdir(parents=True, exist_ok=True)

        used_hooks: set[str] = set()
        used_ctas: set[str] = set()
        for i, m in enumerate(moments, start=1):
            hook = next((h for h in random.sample(HOOKS, len(HOOKS)) if h not in used_hooks), random.choice(HOOKS))
            used_hooks.add(hook)
            cta = next((c for c in random.sample(CTAS, len(CTAS)) if c not in used_ctas), random.choice(CTAS))
            used_ctas.add(cta)
            out = clips_out / f"clip_{i:02d}.mp4"
            res = generate_clip(
                str(src_path), str(out), m.start, m.end,
                hook=hook, cta=cta, safety_boost=safety_boost,
            )
            job["clips"].append({
                "index": i,
                "filename": res.filename,
                "hook": res.hook,
                "cta": res.cta,
                "start": round(res.start, 2),
                "end": round(res.end, 2),
                "duration": round(res.end - res.start, 2),
                "score": round(m.score, 3),
                "audio_score": round(m.audio_score, 3),
                "motion_score": round(m.motion_score, 3),
                "safety_boost": res.safety_boost,
                "url": f"/clips/{job_id}/{res.filename}",
            })
            _save_job(job_id, job)

        job["status"] = "done"
        _save_job(job_id, job)
    except Exception as e:  # noqa: BLE001
        job["status"] = "error"
        job["error"] = str(e)
        _save_job(job_id, job)


@app.route("/")
def index():
    return render_template("index.html", max_mb=MAX_UPLOAD_MB)


@app.route("/api/health")
def health():
    return jsonify({"ok": True, "ffmpeg": has_ffmpeg()})


@app.route("/api/upload", methods=["POST"])
def upload():
    if "video" not in request.files:
        return jsonify({"error": "no video field"}), 400
    f = request.files["video"]
    if not f.filename:
        return jsonify({"error": "empty filename"}), 400
    ext = Path(f.filename).suffix.lower()
    if ext not in ALLOWED_EXT:
        return jsonify({"error": f"unsupported extension {ext}"}), 400

    try:
        n_clips = max(1, min(8, int(request.form.get("n_clips", 4))))
    except ValueError:
        n_clips = 4
    try:
        clip_len = max(8.0, min(60.0, float(request.form.get("clip_len", 25))))
    except ValueError:
        clip_len = 25.0

    safety_boost = request.form.get("safety_boost", "").lower() in ("1", "true", "on", "yes")

    job_id = _job_id()
    src_path = UPLOAD_DIR / f"{job_id}{ext}"
    f.save(src_path)

    try:
        duration = probe_duration(str(src_path))
    except Exception as e:  # noqa: BLE001
        src_path.unlink(missing_ok=True)
        return jsonify({"error": f"could not read video: {e}"}), 400

    job = {
        "job_id": job_id,
        "status": "queued",
        "source": src_path.name,
        "duration": round(duration, 2),
        "n_clips": n_clips,
        "clip_len": clip_len,
        "safety_boost": safety_boost,
        "created_at": datetime.utcnow().isoformat() + "Z",
    }
    _save_job(job_id, job)

    t = threading.Thread(
        target=_process,
        args=(job_id, src_path, n_clips, clip_len, safety_boost),
        daemon=True,
    )
    t.start()

    return jsonify({"job_id": job_id, "status_url": url_for("job_status", job_id=job_id)})


@app.route("/api/jobs/<job_id>")
def job_status(job_id: str):
    job = _load_job(job_id)
    if not job:
        abort(404)
    return jsonify(job)


@app.route("/clips/<job_id>/<path:filename>")
def serve_clip(job_id: str, filename: str):
    job_dir = CLIP_DIR / job_id
    if not job_dir.exists():
        abort(404)
    return send_from_directory(job_dir, filename, as_attachment=False)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    app.run(host="0.0.0.0", port=port, debug=False)
