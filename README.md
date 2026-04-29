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

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cd backend
python app.py
# Open http://localhost:8000
```

You need `ffmpeg` and `ffprobe` on your `PATH`. On Ubuntu:

```bash
sudo apt-get install ffmpeg
```

## Run with Docker

```bash
docker build -t momentum-clipper .
docker run --rm -p 8080:8080 momentum-clipper
# Open http://localhost:8080
```

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
