"""Flask app: upload a video, return generated short-form clips."""

from __future__ import annotations

import json
import os
import random
import shlex
import shutil
import string
import subprocess
import threading
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

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
# Only proxy-compress when the source is genuinely too large to handle. Lower
# thresholds were re-encoding 1080p sources that were already well-compressed,
# and the re-encode softened them visibly versus CapCut/OpusClip output.
COMPRESS_THRESHOLD_MB = int(os.environ.get("COMPRESS_THRESHOLD_MB", "400"))
# When we DO proxy-compress, never downscale BELOW this height. We also skip
# the scale entirely if the source is already at or below this height — the
# whole point of proxying is to avoid analyzing 4K, not to soften 1080p.
COMPRESS_TARGET_HEIGHT = int(os.environ.get("COMPRESS_TARGET_HEIGHT", "1440"))
# Higher bitrate + lower CRF than before so the proxy stays visibly sharp.
COMPRESS_TARGET_BITRATE = os.environ.get("COMPRESS_TARGET_BITRATE", "9000k")

app = Flask(__name__, static_folder="static", template_folder="templates")
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024


# Goal presets — translate "what platform am I posting to" into clip length +
# how many clips to extract per minute of source. Tuned from public retention
# data: TikTok peaks at 21–34s, YouTube Shorts at 30–60s, podcast highlights
# work up to ~90s when the source is interesting.
GOAL_PRESETS: dict[str, dict] = {
    "tiktok": {
        "label": "TikTok / Reels",
        "blurb": "15–30s clips, ~1–2 per minute of source",
        "clip_len": 22.0,
        "per_minute": 1.5,
        "max_total": 8,
        "min_total": 1,
    },
    "shorts": {
        "label": "YouTube Shorts",
        "blurb": "30–60s clips, ~1 per minute of source",
        "clip_len": 45.0,
        "per_minute": 1.0,
        "max_total": 6,
        "min_total": 1,
    },
    "podcast": {
        "label": "Podcast highlights",
        "blurb": "60–90s clips, ~1 every 2 min of source",
        "clip_len": 70.0,
        "per_minute": 0.5,
        "max_total": 4,
        "min_total": 1,
    },
}


def _auto_count_for(duration_s: float, preset: dict) -> int:
    """How many clips to make for a given source duration + preset."""
    minutes = max(1.0, duration_s / 60.0)
    n = int(round(minutes * preset["per_minute"]))
    return max(preset["min_total"], min(preset["max_total"], n))


# Languages we offer in the UI subtitle picker. Whisper supports ~100;
# we ship the ones that cover the bulk of likely creator traffic +
# Indonesian (the user's home market) explicitly. "auto" = let Whisper
# detect.
SUBTITLE_LANGUAGES: list[dict[str, str]] = [
    {"code": "auto", "label": "Auto-detect"},
    {"code": "en", "label": "English"},
    {"code": "id", "label": "Indonesian"},
    {"code": "ms", "label": "Malay"},
    {"code": "es", "label": "Spanish"},
    {"code": "pt", "label": "Portuguese"},
    {"code": "fr", "label": "French"},
    {"code": "de", "label": "German"},
    {"code": "it", "label": "Italian"},
    {"code": "ja", "label": "Japanese"},
    {"code": "ko", "label": "Korean"},
    {"code": "zh", "label": "Chinese"},
    {"code": "ar", "label": "Arabic"},
    {"code": "hi", "label": "Hindi"},
    {"code": "ru", "label": "Russian"},
    {"code": "tr", "label": "Turkish"},
    {"code": "vi", "label": "Vietnamese"},
    {"code": "th", "label": "Thai"},
]
_SUBTITLE_CODES: set[str] = {x["code"] for x in SUBTITLE_LANGUAGES if x["code"] != "auto"}


def _normalize_language(raw: str | None) -> str | None:
    """Validate the language form field. Returns an ISO 639-1 code (lowercase)
    that Whisper understands, or None to mean auto-detect."""
    if not raw:
        return None
    code = str(raw).strip().lower()
    if code in ("auto", "", "none"):
        return None
    if code in _SUBTITLE_CODES:
        return code
    return None


# Caption-rendering styles offered in the UI. ``classic`` is the original
# phrase-at-a-time look. ``hype`` is Submagic-style per-word highlighting
# (active word in yellow). ``hype_emoji`` adds keyword-driven emoji
# decoration on top. Default is ``hype_emoji`` — that's the look modern
# short-form platforms reward.
CAPTION_STYLES: list[dict[str, str]] = [
    {"code": "hype_emoji", "label": "Hype + Emoji (Submagic-style)"},
    {"code": "hype", "label": "Hype (per-word highlight)"},
    {"code": "classic", "label": "Classic (phrase, white)"},
]
_CAPTION_STYLE_CODES: set[str] = {x["code"] for x in CAPTION_STYLES}


def _normalize_caption_style(raw: str | None) -> str:
    """Validate caption_style form field. Defaults to ``hype_emoji``."""
    if not raw:
        return "hype_emoji"
    code = str(raw).strip().lower()
    if code in _CAPTION_STYLE_CODES:
        return code
    return "hype_emoji"


def _resolve_goal(
    raw_goal: str | None,
    duration_s: float,
    raw_n_clips,
    raw_clip_len,
) -> tuple[str, int, float]:
    """Map a goal preset (or 'custom') + the form fields into (goal, n_clips, clip_len).

    For preset goals, the user's n_clips/clip_len fields are ignored: the
    preset + source duration decide. For 'custom', the user's fields win.
    """
    goal = (raw_goal or "tiktok").lower().strip()
    if goal in GOAL_PRESETS:
        preset = GOAL_PRESETS[goal]
        n_clips = _auto_count_for(duration_s, preset)
        clip_len = float(preset["clip_len"])
        return goal, n_clips, clip_len

    # Custom: clamp manual values
    try:
        n_clips = max(1, min(8, int(raw_n_clips)))
    except (ValueError, TypeError):
        n_clips = 4
    try:
        clip_len = max(8.0, min(90.0, float(raw_clip_len)))
    except (ValueError, TypeError):
        clip_len = 25.0
    return "custom", n_clips, clip_len


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


def _maybe_compress(src_path: Path, job_id: str, job: dict) -> Path:
    """Maybe transcode to a smaller H.264/AAC mp4 proxy.

    Only triggers if the source exceeds ``COMPRESS_THRESHOLD_MB`` AND is
    taller than ``COMPRESS_TARGET_HEIGHT`` — sources already at or below
    target height are left untouched even when large, so we never lose
    quality re-encoding a 1080p file. Returns the path the analyzer and
    clipper should consume.
    """
    size_mb = src_path.stat().st_size / (1024 * 1024)
    if size_mb < COMPRESS_THRESHOLD_MB:
        return src_path

    # Probe height — if source is already <= target height, skip proxy.
    try:
        probe = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=height",
                "-of", "json", str(src_path),
            ],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        meta = json.loads(probe.stdout or b"{}")
        src_h = int(meta.get("streams", [{}])[0].get("height", 0))
    except (json.JSONDecodeError, ValueError, IndexError):
        src_h = 0
    if 0 < src_h <= COMPRESS_TARGET_HEIGHT:
        return src_path

    job["status"] = "compressing"
    job["compress"] = {"input_mb": round(size_mb, 1)}
    _save_job(job_id, job)

    out = src_path.with_name(src_path.stem + "_compressed.mp4")
    cmd = [
        "ffmpeg", "-y", "-i", str(src_path),
        "-vf", f"scale=-2:'min({COMPRESS_TARGET_HEIGHT},ih)'",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-maxrate", COMPRESS_TARGET_BITRATE,
        "-bufsize", "18000k",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-ar", "44100",
        "-movflags", "+faststart",
        str(out),
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise RuntimeError(
            f"compression failed: {proc.stderr.decode('utf-8', errors='replace')[-1500:]}"
        )
    job["compress"]["output_mb"] = round(out.stat().st_size / (1024 * 1024), 1)
    _save_job(job_id, job)
    return out


# YouTube has rolled out bot-detection on every public unauthenticated client
# (``ios``, ``android``, ``mweb``, ``tv_embedded``) for downloads from
# datacenter IPs as of 2026. The single most reliable workaround that doesn't
# require a paid residential proxy is supplying a real-browser cookies.txt
# file via the ``YT_COOKIES_FILE`` env var.
#
# We still retry across player_clients first (some non-blocked accounts do
# succeed on ``ios``/``android``), and we ALSO add cookies-from-browser as an
# additional retry when a Chrome profile happens to exist on the box.
#
# Order matters: cheapest-first. The ``None`` entry at the end is the bare
# default-client attempt, mostly useful for non-YT URLs.
_YT_PLAYER_CLIENT_ORDER: list[str | None] = [
    "ios",
    "android",
    "mweb",
    "tv_embedded",
    "web_safari",
    None,
]

_YT_COOKIES_FILE = os.environ.get("YT_COOKIES_FILE", "").strip()
_YT_COOKIES_FROM_BROWSER = os.environ.get("YT_COOKIES_FROM_BROWSER", "").strip()


def _yt_dlp_cmd(
    url: str,
    out_template: str,
    player_client: str | None,
    cookies_file: str | None = None,
    cookies_from_browser: str | None = None,
) -> list[str]:
    cmd = [
        "yt-dlp",
        "--no-playlist",
        "--no-warnings",
        "--restrict-filenames",
        # Prefer h264+aac mp4 so we don't have to remux/transcode unknown
        # codecs downstream. ``best[ext=mp4]`` falls through to whatever the
        # site provides if no h264 mp4 is offered.
        "-f", "best[ext=mp4][vcodec^=avc1]/best[ext=mp4]/best",
        "-o", out_template,
    ]
    if player_client:
        cmd += ["--extractor-args", f"youtube:player_client={player_client}"]
    if cookies_file:
        cmd += ["--cookies", cookies_file]
    elif cookies_from_browser:
        cmd += ["--cookies-from-browser", cookies_from_browser]
    cmd.append(url)
    return cmd


def _download_url(url: str, dest_dir: Path, job_id: str) -> Path:
    """Download a video from a URL using yt-dlp.

    For YouTube URLs, retries with a sequence of alternate player_clients
    when the default is blocked by bot detection, and optionally falls back
    to a cookies file (``YT_COOKIES_FILE``) or browser-extracted cookies
    (``YT_COOKIES_FROM_BROWSER``). For non-YouTube URLs we just run yt-dlp
    once. Returns the path of the downloaded file on disk.
    """
    out_template = str(dest_dir / f"{job_id}.%(ext)s")
    is_yt = "youtube.com" in url.lower() or "youtu.be" in url.lower()

    # Build the attempt list. Each attempt is (player_client, cookies_file,
    # cookies_from_browser); we try player_client variants first (cheap), then
    # cookies if the user configured them (slower but bypasses bot check).
    attempts: list[tuple[str | None, str | None, str | None]] = []
    if is_yt:
        for client in _YT_PLAYER_CLIENT_ORDER:
            attempts.append((client, None, None))
        if _YT_COOKIES_FILE and os.path.exists(_YT_COOKIES_FILE):
            for client in ("ios", None):
                attempts.append((client, _YT_COOKIES_FILE, None))
        if _YT_COOKIES_FROM_BROWSER:
            for client in ("ios", None):
                attempts.append((client, None, _YT_COOKIES_FROM_BROWSER))
    else:
        attempts.append((None, None, None))

    last_err = ""
    for client, cookies_file, cookies_browser in attempts:
        # Reset any partial output from a previous attempt before retrying.
        for stale in dest_dir.glob(f"{job_id}.*"):
            try:
                stale.unlink()
            except OSError:
                pass
        cmd = _yt_dlp_cmd(url, out_template, client, cookies_file, cookies_browser)
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if proc.returncode == 0:
            matches = list(dest_dir.glob(f"{job_id}.*"))
            matches = [p for p in matches if p.suffix.lower() in ALLOWED_EXT]
            if matches:
                return matches[0]
        last_err = proc.stderr.decode("utf-8", errors="replace")

    if is_yt and ("Sign in" in last_err or "cookies" in last_err or "bot" in last_err.lower()):
        raise RuntimeError(
            "YouTube's bot-detection blocked every fallback client (ios / "
            "android / mweb / tv / web_safari) from this server's IP. "
            "Workarounds (any one): (1) export your browser's youtube.com "
            "cookies.txt and set YT_COOKIES_FILE on the server, (2) use a "
            "Google Drive / Dropbox / direct .mp4 link, or (3) download the "
            "video to your computer first and upload the file."
        )
    raise RuntimeError(
        f"yt-dlp failed for {url[:80]}:\n{last_err[-1500:]}"
    )


def _process(
    job_id: str,
    src_path: Path,
    n_clips: int,
    clip_len: float,
    safety_boost: bool = False,
    subtitles: bool = False,
    face_track: bool = True,
    language: str | None = None,
    caption_style: str = "hype_emoji",
) -> None:
    """Background worker: analyze + generate clips, update job status."""
    job = _load_job(job_id) or {}
    try:
        src_path = _maybe_compress(src_path, job_id, job)

        job["status"] = "analyzing"
        job["working_source"] = src_path.name
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
        _save_job(job_id, job)

        all_phrases = []
        if subtitles:
            from subtitler import transcribe, group_words
            job["status"] = "transcribing"
            _save_job(job_id, job)
            words = transcribe(str(src_path), language=language)
            all_phrases = group_words(words)
            job["transcript_words"] = len(words)
            job["transcript_phrases"] = len(all_phrases)
            _save_job(job_id, job)

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

            clip_phrases = None
            if subtitles and all_phrases:
                from subtitler import slice_phrases
                clip_phrases = slice_phrases(all_phrases, m.start, m.end)

            track = None
            if face_track:
                try:
                    from face_tracker import track_face_xs
                    track = track_face_xs(str(src_path), m.start, m.end)
                except Exception:
                    # Face tracking is best-effort — fall back silently to
                    # center-crop on any failure (codec edge case, etc.).
                    track = None

            res = generate_clip(
                str(src_path), str(out), m.start, m.end,
                hook=hook, cta=cta,
                safety_boost=safety_boost,
                subtitle_phrases=clip_phrases,
                face_track=track,
                caption_style=caption_style,
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
                "subtitles": res.subtitles,
                "face_track": res.face_track,
                "caption_style": res.caption_style,
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
    return render_template(
        "index.html",
        max_mb=MAX_UPLOAD_MB,
        languages=SUBTITLE_LANGUAGES,
        caption_styles=CAPTION_STYLES,
    )


@app.route("/api/health")
def health():
    return jsonify({"ok": True, "ffmpeg": has_ffmpeg()})


@app.route("/api/presets")
def list_presets():
    return jsonify({
        "presets": [
            {"id": k, "label": v["label"], "blurb": v["blurb"]}
            for k, v in GOAL_PRESETS.items()
        ]
    })


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

    safety_boost = request.form.get("safety_boost", "").lower() in ("1", "true", "on", "yes")
    subtitles = request.form.get("subtitles", "").lower() in ("1", "true", "on", "yes")
    # Default ON — face tracking is the right default for podcast/interview
    # sources, and falls back gracefully when no faces are detected.
    face_track = request.form.get("face_track", "1").lower() in ("1", "true", "on", "yes")
    raw_goal = request.form.get("goal", "tiktok")
    language = _normalize_language(request.form.get("language"))
    caption_style = _normalize_caption_style(request.form.get("caption_style"))

    job_id = _job_id()
    src_path = UPLOAD_DIR / f"{job_id}{ext}"
    f.save(src_path)

    try:
        duration = probe_duration(str(src_path))
    except Exception as e:  # noqa: BLE001
        src_path.unlink(missing_ok=True)
        return jsonify({"error": f"could not read video: {e}"}), 400

    goal, n_clips, clip_len = _resolve_goal(
        raw_goal,
        duration,
        request.form.get("n_clips"),
        request.form.get("clip_len"),
    )

    job = {
        "job_id": job_id,
        "status": "queued",
        "source": src_path.name,
        "duration": round(duration, 2),
        "goal": goal,
        "n_clips": n_clips,
        "clip_len": clip_len,
        "safety_boost": safety_boost,
        "subtitles": subtitles,
        "face_track": face_track,
        "language": language,
        "caption_style": caption_style,
        "created_at": datetime.utcnow().isoformat() + "Z",
    }
    _save_job(job_id, job)

    t = threading.Thread(
        target=_process,
        args=(job_id, src_path, n_clips, clip_len, safety_boost, subtitles, face_track, language, caption_style),
        daemon=True,
    )
    t.start()

    return jsonify({"job_id": job_id, "status_url": url_for("job_status", job_id=job_id)})


def _spawn_job(src_path: Path, goal: str, n_clips: int, clip_len: float, safety_boost: bool, subtitles: bool, source_label: str, duration: float, face_track: bool = True, language: str | None = None, caption_style: str = "hype_emoji") -> str:
    job_id = src_path.stem
    job = {
        "job_id": job_id,
        "status": "queued",
        "source": source_label,
        "duration": round(duration, 2),
        "goal": goal,
        "n_clips": n_clips,
        "clip_len": clip_len,
        "safety_boost": safety_boost,
        "subtitles": subtitles,
        "face_track": face_track,
        "language": language,
        "caption_style": caption_style,
        "created_at": datetime.utcnow().isoformat() + "Z",
    }
    _save_job(job_id, job)
    t = threading.Thread(
        target=_process,
        args=(job_id, src_path, n_clips, clip_len, safety_boost, subtitles, face_track, language, caption_style),
        daemon=True,
    )
    t.start()
    return job_id


@app.route("/api/upload_url", methods=["POST"])
def upload_url():
    """Pull a video from a remote URL (Drive/Dropbox/direct/etc) instead of
    uploading raw bytes. Bypasses any tunnel body-size limit on the client side.
    """
    payload = request.get_json(silent=True) or request.form
    url = (payload.get("url") or "").strip()
    if not url:
        return jsonify({"error": "no url"}), 400
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return jsonify({"error": "url must be http(s)"}), 400

    safety_boost = str(payload.get("safety_boost", "")).lower() in ("1", "true", "on", "yes")
    subtitles = str(payload.get("subtitles", "")).lower() in ("1", "true", "on", "yes")
    face_track = str(payload.get("face_track", "1")).lower() in ("1", "true", "on", "yes")
    raw_goal = payload.get("goal") or "tiktok"
    language = _normalize_language(payload.get("language"))
    caption_style = _normalize_caption_style(payload.get("caption_style"))

    job_id = _job_id()
    try:
        src_path = _download_url(url, UPLOAD_DIR, job_id)
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": f"download failed: {e}"}), 400

    try:
        duration = probe_duration(str(src_path))
    except Exception as e:  # noqa: BLE001
        src_path.unlink(missing_ok=True)
        return jsonify({"error": f"could not read video: {e}"}), 400

    goal, n_clips, clip_len = _resolve_goal(
        raw_goal, duration,
        payload.get("n_clips"), payload.get("clip_len"),
    )

    final_id = _spawn_job(src_path, goal, n_clips, clip_len, safety_boost, subtitles, src_path.name, duration, face_track=face_track, language=language, caption_style=caption_style)
    return jsonify({"job_id": final_id, "status_url": url_for("job_status", job_id=final_id)})


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
