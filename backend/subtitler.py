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
    # Word-level timings that make up this phrase. Populated by ``group_words``
    # and ``slice_phrases`` so downstream renderers can do per-word highlighting
    # (Submagic-style) without re-running Whisper.
    words: list["Word"] | None = None


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
        words=list(words),
    )


def slice_phrases(
    phrases: list[Phrase],
    start: float,
    end: float,
    pad: float = 0.05,
) -> list[Phrase]:
    """Filter phrases within [start, end] and rebase their times to start at 0.

    Word-level timings inside each phrase are rebased too so per-word
    highlighting renders against the same clip-relative clock as the phrase
    itself.
    """
    out: list[Phrase] = []
    clip_len = max(0.0, end - start)
    for p in phrases:
        if p.end < start - pad or p.start > end + pad:
            continue
        s = max(0.0, p.start - start)
        e = min(clip_len, p.end - start)
        if e <= s:
            continue
        rebased_words: list[Word] | None = None
        if p.words:
            rebased_words = []
            for w in p.words:
                ws = max(0.0, w.start - start)
                we = min(clip_len, w.end - start)
                if we <= ws:
                    continue
                rebased_words.append(Word(start=ws, end=we, text=w.text))
            if not rebased_words:
                rebased_words = None
        out.append(Phrase(start=s, end=e, text=p.text, words=rebased_words))
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


# ASS colors are ``&HBBGGRR&`` (BGR, not RGB). Declared as string constants
# here so we don't accidentally swap bytes in the renderer.
_ASS_WHITE = r"\c&HFFFFFF&"
_ASS_YELLOW = r"\c&H00FFFF&"  # #FFFF00 in RGB


CAPTION_STYLES = ("classic", "hype", "hype_emoji")


def write_ass(
    phrases: list[Phrase],
    out_path: str,
    play_w: int = 1080,
    play_h: int = 1920,
    font_name: str = "DejaVu Sans",
    font_size: int = 84,
    margin_v: int = 540,
    style: str = "classic",
) -> None:
    """Write an ASS subtitle file for a single clip.

    Three styles:

    ``classic``
        One dialogue line per phrase, all white. Original look.
    ``hype``
        Submagic-style per-word highlighting: one dialogue event per word,
        the active word in yellow, others in white. Requires ``phrase.words``
        to be populated (Whisper word-timestamps) — falls back to ``classic``
        for any phrase without word timings.
    ``hype_emoji``
        Same as ``hype`` plus one emoji appended next to the highest-signal
        keyword in each phrase. Emoji is persistent for the phrase duration.

    Captions are bold white with a thick black outline, centered horizontally
    and lifted ~28% from the bottom so phone UI doesn't clip them.
    """
    if style not in CAPTION_STYLES:
        style = "classic"

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
        lines.extend(_render_phrase(p, style))
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("".join(lines))


def _render_phrase(p: Phrase, style: str) -> list[str]:
    """Render one phrase into one or more ASS Dialogue lines."""
    words = p.words or []
    # If we don't have word-level timings we can't do per-word highlighting;
    # fall back to the flat classic render.
    if style == "classic" or not words:
        text = _ass_escape(p.text.upper())
        return [
            f"Dialogue: 0,{_ts_ass(p.start)},{_ts_ass(p.end)},Cap,,0,0,0,,{text}\n"
        ]

    # Hype / hype_emoji: per-word. Pick an emoji (hype_emoji only) that will
    # be appended to its matched word for the whole phrase duration.
    emoji_idx: int | None = None
    emoji_char: str | None = None
    if style == "hype_emoji":
        from emojis import pick_phrase_emoji
        pick = pick_phrase_emoji([w.text for w in words])
        if pick is not None:
            emoji_idx, emoji_char = pick

    rendered_tokens: list[str] = []
    for i, w in enumerate(words):
        tok = w.text.upper()
        if emoji_char and i == emoji_idx:
            tok = f"{tok} {emoji_char}"
        rendered_tokens.append(tok)

    out: list[str] = []
    for active in range(len(words)):
        parts: list[str] = []
        for i, tok in enumerate(rendered_tokens):
            color = _ASS_YELLOW if i == active else _ASS_WHITE
            parts.append("{" + color + "}" + _ass_escape(tok))
        text = " ".join(parts)
        ws = words[active].start
        we = words[active].end
        # Extend the last word to the phrase end so the caption doesn't pop
        # off a beat early when Whisper clips the trailing word timestamp
        # tightly.
        if active == len(words) - 1:
            we = max(we, p.end)
        out.append(
            f"Dialogue: 0,{_ts_ass(ws)},{_ts_ass(we)},Cap,,0,0,0,,{text}\n"
        )
    return out
