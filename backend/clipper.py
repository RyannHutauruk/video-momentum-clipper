"""
Clip generator.

Takes a source video + a list of Moments and produces 9:16 vertical MP4s
suitable for YouTube Shorts / TikTok / Reels. Each clip:

- Is cropped/scaled to 1080x1920 (center-cut from 16:9 source).
- Has a bold "hook" caption burned in at the top for the first ~2 seconds.
- Has a small CTA caption at the bottom for the final ~2 seconds.
- Uses re-encoded H.264 + AAC for max platform compatibility (monetizable).
"""

from __future__ import annotations

import os
import random
import shlex
import subprocess
import tempfile
from dataclasses import dataclass

HOOKS = [
    "Wait for it...",
    "You won't believe this",
    "Watch till the end",
    "This is insane",
    "Did that just happen?!",
    "POV: best moment",
    "The moment everyone missed",
    "Hold up... rewind",
]

CTAS = [
    "Follow for more",
    "Like + Subscribe",
    "More clips on the channel",
    "Tap follow for daily clips",
]


@dataclass
class ClipResult:
    path: str
    filename: str
    hook: str
    cta: str
    start: float
    end: float


def _pick_font() -> str | None:
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return None


def build_filter(hook_file: str, cta_file: str, clip_len: float) -> str:
    """Build the ffmpeg -vf filter chain.

    1. Scale + crop to 1080x1920 cover.
    2. Burn hook caption (top, large, white-on-dark-stroke) for 0-2.5s.
    3. Burn CTA caption (bottom) for last 2.5s.

    Uses textfile= to avoid all the escaping pitfalls of inline text
    (apostrophes, colons, commas, etc).
    """
    scale_crop = (
        "scale=w=1080:h=1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920"
    )

    font_file = _pick_font()
    font_arg = f":fontfile={font_file}" if font_file else ""

    hook_end = 2.5
    cta_start = max(0.0, clip_len - 2.5)

    hook_draw = (
        f"drawtext=textfile={hook_file}"
        f"{font_arg}"
        f":fontcolor=white:fontsize=84:borderw=6:bordercolor=black"
        f":box=1:boxcolor=black@0.45:boxborderw=20"
        f":x=(w-text_w)/2:y=240"
        f":enable='between(t\\,0\\,{hook_end})'"
    )

    cta_draw = (
        f"drawtext=textfile={cta_file}"
        f"{font_arg}"
        f":fontcolor=white:fontsize=58:borderw=4:bordercolor=black"
        f":box=1:boxcolor=black@0.55:boxborderw=18"
        f":x=(w-text_w)/2:y=h-260"
        f":enable='between(t\\,{cta_start}\\,{clip_len})'"
    )

    return ",".join([scale_crop, hook_draw, cta_draw])


def generate_clip(
    src: str,
    out_path: str,
    start: float,
    end: float,
    hook: str | None = None,
    cta: str | None = None,
) -> ClipResult:
    hook = hook or random.choice(HOOKS)
    cta = cta or random.choice(CTAS)
    clip_len = max(1.0, end - start)

    # Write hook + cta to temp files; drawtext's textfile= avoids escape hell.
    tmp_dir = tempfile.mkdtemp(prefix="momclip_")
    hook_file = os.path.join(tmp_dir, "hook.txt")
    cta_file = os.path.join(tmp_dir, "cta.txt")
    try:
        with open(hook_file, "w", encoding="utf-8") as f:
            f.write(hook.upper())
        with open(cta_file, "w", encoding="utf-8") as f:
            f.write(cta)

        vf = build_filter(hook_file, cta_file, clip_len)

        cmd = [
            "ffmpeg", "-y",
            "-ss", f"{start:.3f}",
            "-i", src,
            "-t", f"{clip_len:.3f}",
            "-vf", vf,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
            "-movflags", "+faststart",
            out_path,
        ]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if proc.returncode != 0:
            raise RuntimeError(
                f"ffmpeg failed for clip {start}-{end}:\n"
                f"cmd: {' '.join(shlex.quote(c) for c in cmd)}\n"
                f"stderr: {proc.stderr.decode('utf-8', errors='replace')[-2000:]}"
            )
    finally:
        for p in (hook_file, cta_file):
            try:
                os.unlink(p)
            except OSError:
                pass
        try:
            os.rmdir(tmp_dir)
        except OSError:
            pass

    return ClipResult(
        path=out_path,
        filename=os.path.basename(out_path),
        hook=hook,
        cta=cta,
        start=start,
        end=end,
    )
