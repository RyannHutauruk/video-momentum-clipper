"""
Clip generator.

Takes a source video + a list of Moments and produces 9:16 vertical MP4s
suitable for YouTube Shorts / TikTok / Reels. Each clip:

- Is cropped/scaled to 1080x1920 (center-cut from 16:9 source).
- Has a bold "hook" caption burned in at the top for the first ~2 seconds.
- Has a small CTA caption at the bottom for the final ~2 seconds.
- Uses re-encoded H.264 + AAC for max platform compatibility (monetizable).

When ``safety_boost`` is enabled, the clip is also passed through a bundle of
Content-ID-evading transforms (mirror, zoom, slight color shift, slight speed
+ pitch shift). These do NOT make copyrighted content legal — they reduce the
chance of automated fingerprint matches. Use only on content you have rights
to (or are confident enough in fair use to defend manually).
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

# Safety-boost knobs. Tuned to be barely perceptible to viewers but to break
# common audio + video fingerprints used by automated content-ID systems.
SAFETY_TEMPO = 1.03           # +3% speed on both audio and video
SAFETY_PITCH_RATE = 1.03      # audio pitch shifted up by ~3%
SAFETY_ZOOM = 1.10            # 110% zoom (extra crop in)
SAFETY_SATURATION = 1.10
SAFETY_CONTRAST = 1.05
SAFETY_GAMMA = 0.97
SAFETY_AUDIO_SR = 44100


@dataclass
class ClipResult:
    path: str
    filename: str
    hook: str
    cta: str
    start: float
    end: float
    safety_boost: bool = False
    subtitles: bool = False
    face_track: bool = False
    caption_style: str = "classic"


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


# Approximate width of a bold sans-serif glyph at fontsize 1, in pixels.
# DejaVuSans-Bold averages ~0.58 of the fontsize in width; use the upper end
# so we never overestimate the budget and overflow.
_AVG_GLYPH_WIDTH = 0.60

# Pixels of side padding we want to keep clear of the canvas edge for the
# hook caption box (the box has its own boxborderw on top of this).
_HOOK_SIDE_MARGIN = 80

# Output canvas width.
_OUT_W = 1080


def _wrap_caption(text: str, max_chars_per_line: int, max_lines: int = 2) -> list[str]:
    """Greedy word-wrap into at most ``max_lines`` lines of at most
    ``max_chars_per_line`` chars. If a single word is longer than the line
    budget, it goes on its own line as-is (we'd rather render a slightly
    wider single line than break a word mid-glyph)."""
    words = text.split()
    if not words:
        return [""]
    lines: list[list[str]] = [[]]
    for w in words:
        candidate_len = len(" ".join(lines[-1] + [w]))
        if lines[-1] and candidate_len > max_chars_per_line and len(lines) < max_lines:
            lines.append([w])
        else:
            lines[-1].append(w)
    return [" ".join(line) for line in lines]


def _hook_layout(hook: str, max_fontsize: int = 84, min_fontsize: int = 56) -> tuple[list[str], int]:
    """Pick wrapped lines + fontsize for the hook so it fits the canvas.

    Strategy: try wrapping into 1 line, then 2 lines. For each candidate,
    compute the largest fontsize that keeps the longest line within the
    horizontal budget. Pick the option with the bigger fontsize."""
    upper = hook.upper()
    budget_px = _OUT_W - 2 * _HOOK_SIDE_MARGIN

    best_lines: list[str] = [upper]
    best_size: int = min_fontsize

    for max_lines in (1, 2):
        # Approximate chars-per-line that the budget can hold at max_fontsize.
        max_chars = max(8, int(budget_px / (max_fontsize * _AVG_GLYPH_WIDTH)))
        wrapped = _wrap_caption(upper, max_chars_per_line=max_chars, max_lines=max_lines)
        longest = max(len(line) for line in wrapped) or 1
        # Fontsize that fits the longest line in the budget.
        size = int(budget_px / (longest * _AVG_GLYPH_WIDTH))
        size = max(min_fontsize, min(max_fontsize, size))
        if size > best_size:
            best_size = size
            best_lines = wrapped
    return best_lines, best_size


def build_video_filter(
    hook_files: list[str],
    cta_file: str,
    clip_len: float,
    safety_boost: bool = False,
    subtitle_file: str | None = None,
    face_track: "FaceTrack | None" = None,
    hook_fontsize: int = 84,
) -> str:
    """Build the ffmpeg -vf filter chain.

    1. (face) source-space crop following the speaker's x-center, OR
       (no face) center-crop via scale-fit
    2. (boost) hflip — horizontal mirror, applied AFTER the face crop so
       the crop window is computed in unmirrored source coords
    3. Scale to 1080x1920 (with extra zoom when boost is on)
    4. (boost) eq — subtle color grade
    5. (subs) subtitles=path.ass — burnt-in word-grouped captions, BEFORE
       any speed-up so timestamps stay in clip-relative time.
    6. (boost) setpts — slight speed-up
    7. drawtext hook (top, large) for first 2.5s of OUTPUT
    8. drawtext cta (bottom) for last 2.5s of OUTPUT

    Uses textfile= to avoid all the escaping pitfalls of inline text
    (apostrophes, colons, commas, etc).
    """
    from face_tracker import crop_x_expr

    parts: list[str] = []

    if face_track is not None and face_track.samples:
        # Compute crop window in source pixels: 9:16 column at full source
        # height (or full width if source is taller than 9:16).
        sw, sh = face_track.source_w, face_track.source_h
        crop_w = int(round(sh * 9 / 16))
        if crop_w > sw:
            # Portrait source — use full width, shrink height instead.
            crop_w = sw
            crop_h = int(round(sw * 16 / 9))
        else:
            crop_h = sh
        x_expr = crop_x_expr(face_track, crop_w)
        parts.append(f"crop={crop_w}:{crop_h}:'{x_expr}':0")

    if safety_boost:
        parts.append("hflip")

    if safety_boost:
        zoom_w = int(round(1080 * SAFETY_ZOOM))
        zoom_h = int(round(1920 * SAFETY_ZOOM))
        parts.append(
            f"scale=w={zoom_w}:h={zoom_h}:force_original_aspect_ratio=increase"
        )
        parts.append("crop=1080:1920")
    else:
        parts.append(
            "scale=w=1080:h=1920:force_original_aspect_ratio=increase"
        )
        parts.append("crop=1080:1920")

    if safety_boost:
        parts.append(
            f"eq=saturation={SAFETY_SATURATION}"
            f":contrast={SAFETY_CONTRAST}"
            f":gamma={SAFETY_GAMMA}"
        )

    if subtitle_file:
        # ffmpeg subtitle filter wants escaping for ':', '\\' and '['.
        sub_arg = (
            subtitle_file.replace("\\", "\\\\")
            .replace(":", "\\:")
            .replace("'", "\\'")
        )
        parts.append(f"subtitles='{sub_arg}'")

    if safety_boost:
        # Speed-up via setpts; affects the timestamps, so drawtext enable
        # below uses the post-speedup duration.
        parts.append(f"setpts=PTS/{SAFETY_TEMPO}")

    out_len = clip_len / SAFETY_TEMPO if safety_boost else clip_len
    hook_end = min(2.5, max(0.5, out_len * 0.25))
    cta_start = max(0.0, out_len - 2.5)

    font_file = _pick_font()
    font_arg = f":fontfile={font_file}" if font_file else ""

    # Hook may be split across multiple lines; render each as its own
    # individually-centered drawtext, stacked vertically. ffmpeg 4.4 doesn't
    # support text_align, so this is the cleanest way to get true centered
    # multi-line text.
    line_height = int(hook_fontsize * 1.25)
    base_y = 240
    for i, hf in enumerate(hook_files):
        y = base_y + i * line_height
        parts.append(
            f"drawtext=textfile={hf}"
            f"{font_arg}"
            f":fontcolor=white:fontsize={hook_fontsize}:borderw=6:bordercolor=black"
            f":box=1:boxcolor=black@0.45:boxborderw=20"
            f":x=(w-text_w)/2:y={y}"
            f":enable='between(t\\,0\\,{hook_end:.3f})'"
        )

    cta_draw = (
        f"drawtext=textfile={cta_file}"
        f"{font_arg}"
        f":fontcolor=white:fontsize=58:borderw=4:bordercolor=black"
        f":box=1:boxcolor=black@0.55:boxborderw=18"
        f":x=(w-text_w)/2:y=h-260"
        f":enable='between(t\\,{cta_start:.3f}\\,{out_len:.3f})'"
    )

    parts.append(cta_draw)
    return ",".join(parts)


def build_audio_filter(safety_boost: bool) -> str | None:
    """Pitch-shift + speed-up audio when safety_boost is on.

    asetrate raises the sample rate which both pitches up and speeds up the
    audio; aresample brings it back to the target sample rate so the rest of
    the pipeline is happy. The video is sped up by the same factor via setpts
    so audio/video stay in sync.
    """
    if not safety_boost:
        return None
    return (
        f"asetrate={SAFETY_AUDIO_SR}*{SAFETY_PITCH_RATE},"
        f"aresample={SAFETY_AUDIO_SR}"
    )


def generate_clip(
    src: str,
    out_path: str,
    start: float,
    end: float,
    hook: str | None = None,
    cta: str | None = None,
    safety_boost: bool = False,
    subtitle_phrases: list | None = None,
    face_track: "FaceTrack | None" = None,
    caption_style: str = "classic",
) -> ClipResult:
    hook = hook or random.choice(HOOKS)
    cta = cta or random.choice(CTAS)
    clip_len = max(1.0, end - start)

    # Write hook + cta to temp files; drawtext's textfile= avoids escape hell.
    # Hook gets word-wrapped + sized so it never overflows the 1080-wide canvas;
    # each wrapped line is its own file so we can center-align line-by-line.
    tmp_dir = tempfile.mkdtemp(prefix="momclip_")
    hook_lines, hook_fontsize = _hook_layout(hook)
    hook_files: list[str] = []
    for i, line in enumerate(hook_lines):
        path = os.path.join(tmp_dir, f"hook_{i}.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(line)
        hook_files.append(path)
    cta_file = os.path.join(tmp_dir, "cta.txt")
    subtitle_file: str | None = None
    has_subs = bool(subtitle_phrases)
    try:
        with open(cta_file, "w", encoding="utf-8") as f:
            f.write(cta)

        if has_subs:
            from subtitler import write_ass
            subtitle_file = os.path.join(tmp_dir, "subs.ass")
            write_ass(subtitle_phrases, subtitle_file, style=caption_style)

        vf = build_video_filter(
            hook_files, cta_file, clip_len, safety_boost, subtitle_file,
            face_track=face_track, hook_fontsize=hook_fontsize,
        )
        af = build_audio_filter(safety_boost)

        cmd = [
            "ffmpeg", "-y",
            "-ss", f"{start:.3f}",
            "-i", src,
            "-t", f"{clip_len:.3f}",
            "-vf", vf,
        ]
        if af:
            cmd += ["-af", af]
        cmd += [
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
        cleanup = list(hook_files) + [cta_file, subtitle_file]
        for p in cleanup:
            if not p:
                continue
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
        safety_boost=safety_boost,
        subtitles=has_subs,
        face_track=face_track is not None and bool(face_track.samples),
        caption_style=caption_style if has_subs else "classic",
    )
