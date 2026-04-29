# Momentum Clipper

A simple, **no-AI** web app that turns long videos into monetizable short-form
clips for **YouTube Shorts / TikTok / Reels**.

It detects the most exciting moments in a video using two classic signals:

1. **Audio loudness** — peaks usually correspond to cheers, impacts, hype, or
   key dialogue.
2. **Visual motion** — frame-to-frame difference highlights action sequences.

The two signals are normalized and combined into a per-second score. The top
non-overlapping windows are then exported as **1080×1920 H.264** MP4 clips
with:

- A bold **hook caption** burned in for the first ~2.5 seconds (e.g. "Wait for
  it…", "Watch till the end") to maximize retention.
- A small **CTA caption** in the final ~2.5 seconds (e.g. "Follow for more")
  to drive monetization-friendly engagement.
- AAC audio + faststart muxing — platform-friendly defaults.

Inspired by tools like CapCut Auto-Clipper and OpusClip, but everything is
local and deterministic — no external AI APIs.

## Stack

- **Backend:** Python 3.12, Flask, ffmpeg, OpenCV (headless), NumPy
- **Frontend:** Plain HTML/CSS/JS (no framework)
- **Deploy:** Single Docker container (Fly.io / Render / any container host)

## Run locally

You need `ffmpeg` and `ffprobe` on your `PATH`.

- **macOS:** `brew install ffmpeg`
- **Ubuntu/Debian:** `sudo apt-get install ffmpeg fonts-dejavu-core`
- **Windows:** download static build from <https://www.gyan.dev/ffmpeg/builds/> and add to PATH.

```bash
python3 -m venv .venv
source .venv/bin/activate           # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt

python backend/app.py
# Open http://localhost:8000
```

Running locally has no upload size limit and no auth.

## Run with Docker

```bash
docker build -t momentum-clipper .
docker run --rm -p 8000:8000 -e PORT=8000 momentum-clipper
# Open http://localhost:8000
```

To persist generated clips on the host:

```bash
docker run --rm -p 8000:8000 -e PORT=8000 \
  -v "$(pwd)/data:/app/backend" \
  momentum-clipper
```

## Features

- Drag-drop file upload **or** "Paste URL" (Drive / Dropbox / direct .mp4 / yt-dlp-supported sites). The URL path bypasses any HTTP proxy upload limit.
- Auto-compresses sources >120 MB to 720p H.264 before analyzing, so the pipeline stays fast.
- Optional **"Boost monetization safety"** checkbox: applies a bundle of fingerprint-evading transforms to each clip (mirror, 110% zoom, color shift, +3% speed/pitch). Reduces automated Content-ID matches; does not legalize copyrighted material.

## How it works

```
upload ─► ffmpeg extract mono PCM ─► per-second RMS ─┐
                                                     ├─► combined score
       ─► OpenCV frame-diff (sampled) ─► per-sec motion ─┘
                                                     │
                                top-N non-overlapping windows
                                                     │
       ─► ffmpeg cut + scale 1080x1920 + drawtext (hook/CTA) ─► .mp4
```

See `backend/analyzer.py` and `backend/clipper.py`.

## Notes

- This is a **mock / MVP**. It assumes 16:9 source footage and center-crops to
  9:16. A real product would do face/saliency-aware cropping.
- For very long videos (>30 min) consider sampling the analysis at lower
  precision, or using ffmpeg's loudnorm filter for audio.
- Clips are written under `backend/clips/<job_id>/` and served from the same
  Flask app.
