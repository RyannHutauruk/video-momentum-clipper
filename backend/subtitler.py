"""
Auto-subtitles via local faster-whisper. No external API.

Workflow:
1. ``transcribe(video_path)`` runs Whisper on the full source and returns
   a list of (start, end, text) tuples at word-level granularity.
2. ``group_words`` chunks adjacent words into phrases of up to N words /
   M characters with no gap larger than G seconds.
3. ``write_ass`` writes a clip-relative ASS subtitle file ready to be
   burnt in by ffmpeg's ``subtitles=`` filter.

The model is loaded lazily and cached per-process so concurrent jobs can
share it. CPU + int8 is the default — fast enough for short videos and
zero GPU dependency.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass

# Lazy import so the rest of the app doesn't pay the import cost when
# subtitles are disabled.
_model = None
_model_lock = threading.Lock()
# `small` (~460 MB) roughly halves word-error-rate vs `base` for non-English
# languages (esp. Indonesian) at ~2.5x the CPU time. Worth it; users expect
# minute-long processing for clipping anyway.
_model_name = os.environ.get("WHISPER_MODEL", "small")
_model_compute = os.environ.get("WHISPER_COMPUTE", "int8")
_model_device = os.environ.get("WHISPER_DEVICE", "cpu")


@dataclass
class Word:
    start: float  # seconds, source-relative
    end: float
    text: str


@dataclass
class Phrase:
    start: float
    end: float
    text: str


def _load_model():
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                from faster_whisper import WhisperModel
                _model = WhisperModel(
                    _model_name,
                    device=_model_device,
                    compute_type=_model_compute,
                )
    return _model


def transcribe(video_path: str, language: str | None = None) -> list[Word]:
    """Transcribe a video to word-level timestamps.

    ``language`` is the ISO 639-1 code (``"en"``, ``"id"``, ...). ``None``
    auto-detects. VAD filter is on so silence stretches don't burn through
    the model. Returns an empty list when the source has no audio stream
    (e.g. some Pexels stock clips) so callers can skip subtitles silently
    instead of crashing the job.
    """
    model = _load_model()
    try:
        segments, _info = model.transcribe(
            video_path,
            language=language,
            word_timestamps=True,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 400},
        )
        words: list[Word] = []
        for seg in segments:
            for w in (seg.words or []):
                txt = (w.word or "").strip()
                if not txt:
                    continue
                words.append(Word(start=float(w.start), end=float(w.end), text=txt))
        return words
    except IndexError:
        # faster-whisper raises IndexError from its av-based audio decoder
        # when the input has zero audio streams. Treat as "no transcript".
        return []


def group_words(
    words: list[Word],
    max_words: int = 3,
    max_chars: int = 22,
    max_gap: float = 0.5,
) -> list[Phrase]:
    """Group adjacent words into short on-screen phrases.

    Resets the phrase when adding a word would exceed ``max_words`` or
    ``max_chars``, or when the gap from the previous word's end is larger
    than ``max_gap`` seconds (natural pause).
    """
    phrases: list[Phrase] = []
    if not words:
        return phrases

    cur: list[Word] = []
    for w in words:
        if cur:
            gap = w.start - cur[-1].end
            joined = " ".join(x.text for x in cur + [w])
            if (
                len(cur) >= max_words
                or len(joined) > max_chars
                or gap > max_gap
            ):
                phrases.append(_finalize(cur))
                cur = []
        cur.append(w)
    if cur:
        phrases.append(_finalize(cur))
    return phrases


def _finalize(words: list[Word]) -> Phrase:
    return Phrase(
        start=words[0].start,
        end=words[-1].end,
        text=" ".join(w.text for w in words),
    )


def slice_phrases(
    phrases: list[Phrase],
    start: float,
    end: float,
    pad: float = 0.05,
) -> list[Phrase]:
    """Filter phrases within [start, end] and rebase their times to start at 0."""
    out: list[Phrase] = []
    for p in phrases:
        if p.end < start - pad or p.start > end + pad:
            continue
        s = max(0.0, p.start - start)
        e = min(end - start, p.end - start)
        if e <= s:
            continue
        out.append(Phrase(start=s, end=e, text=p.text))
    return out


def _ts_ass(t: float) -> str:
    """ASS timestamp: H:MM:SS.cs (centiseconds)."""
    if t < 0:
        t = 0
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    cs = int(round((s - int(s)) * 100))
    if cs == 100:
        cs = 0
        s_int = int(s) + 1
    else:
        s_int = int(s)
    return f"{h}:{m:02d}:{s_int:02d}.{cs:02d}"


def _ass_escape(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace("{", "\\{")
        .replace("}", "\\}")
        .replace("\n", "\\N")
    )


def write_ass(
    phrases: list[Phrase],
    out_path: str,
    play_w: int = 1080,
    play_h: int = 1920,
    font_name: str = "DejaVu Sans",
    font_size: int = 84,
    margin_v: int = 540,
) -> None:
    """Write an ASS subtitle file for a single clip.

    Captions are bold white with a thick black outline, centered horizontally
    and lifted ~28% from the bottom so phone UI doesn't clip them. Style is
    chosen to evoke TikTok/Reels/Shorts captions.
    """
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {play_w}
PlayResY: {play_h}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{font_name},{font_size},&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,5,2,2,80,80,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header]
    for p in phrases:
        text = _ass_escape(p.text.upper())
        lines.append(
            f"Dialogue: 0,{_ts_ass(p.start)},{_ts_ass(p.end)},Cap,,0,0,0,,{text}\n"
        )
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("".join(lines))
