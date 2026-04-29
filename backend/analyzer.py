"""
Video momentum analyzer.

Finds "best moments" in a video by combining two signals (no AI/ML APIs):

1. Audio loudness — extracts mono PCM via ffmpeg, computes per-second RMS.
   Loud peaks usually correspond to cheering, impacts, hype, key dialogue.
2. Visual motion — samples frames with OpenCV, computes mean absolute frame
   difference per second. High motion correlates with action/excitement.

The two signals are normalized and summed into a per-second score. We then
pick the top-N non-overlapping windows around the highest peaks.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import struct
import subprocess
import tempfile
from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class Moment:
    start: float  # seconds
    end: float    # seconds
    score: float  # combined score
    audio_score: float
    motion_score: float

    def to_dict(self) -> dict:
        return {
            "start": round(self.start, 2),
            "end": round(self.end, 2),
            "duration": round(self.end - self.start, 2),
            "score": round(self.score, 3),
            "audio_score": round(self.audio_score, 3),
            "motion_score": round(self.motion_score, 3),
        }


def _run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        check=check,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def probe_duration(video_path: str) -> float:
    """Return video duration in seconds via ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "json", video_path,
    ]
    out = _run(cmd).stdout
    data = json.loads(out)
    return float(data["format"]["duration"])


def extract_audio_rms(video_path: str, sample_rate: int = 16000) -> np.ndarray:
    """Return per-second audio RMS as a numpy array.

    Extracts mono PCM s16le via ffmpeg into a temp file, then reads it.
    Returns a zero-filled array if the video has no audio stream.
    """
    with tempfile.NamedTemporaryFile(suffix=".pcm", delete=False) as tmp:
        pcm_path = tmp.name
    try:
        cmd = [
            "ffmpeg", "-y", "-i", video_path,
            "-vn", "-ac", "1", "-ar", str(sample_rate),
            "-f", "s16le", pcm_path,
        ]
        proc = _run(cmd, check=False)
        if proc.returncode != 0 or not os.path.exists(pcm_path) or os.path.getsize(pcm_path) == 0:
            return np.zeros(0, dtype=np.float32)

        raw = np.fromfile(pcm_path, dtype=np.int16).astype(np.float32) / 32768.0
        if raw.size == 0:
            return np.zeros(0, dtype=np.float32)

        # Per-second RMS
        n_per_sec = sample_rate
        n_secs = max(1, raw.size // n_per_sec)
        trimmed = raw[: n_secs * n_per_sec].reshape(n_secs, n_per_sec)
        rms = np.sqrt(np.mean(trimmed ** 2, axis=1))
        return rms
    finally:
        if os.path.exists(pcm_path):
            os.unlink(pcm_path)


def compute_motion_per_second(video_path: str, sample_fps: float = 4.0) -> np.ndarray:
    """Compute per-second motion intensity by frame differencing.

    Samples ~`sample_fps` frames per second; per-frame motion is mean abs
    diff of grayscale-downscaled frames; per-second motion is the mean of
    samples falling in that second.
    """
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return np.zeros(0, dtype=np.float32)

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    duration = total_frames / fps if fps > 0 else 0.0
    if duration <= 0:
        cap.release()
        return np.zeros(0, dtype=np.float32)

    n_secs = int(math.ceil(duration))
    bucket_sums = np.zeros(n_secs, dtype=np.float64)
    bucket_counts = np.zeros(n_secs, dtype=np.int32)

    step = max(1, int(round(fps / sample_fps)))
    prev_small = None
    frame_idx = 0
    while True:
        ret = cap.grab()
        if not ret:
            break
        if frame_idx % step == 0:
            ret, frame = cap.retrieve()
            if not ret:
                break
            small = cv2.resize(frame, (160, 90), interpolation=cv2.INTER_AREA)
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            if prev_small is not None:
                diff = cv2.absdiff(gray, prev_small)
                motion = float(diff.mean())
                t = frame_idx / fps
                sec = min(int(t), n_secs - 1)
                bucket_sums[sec] += motion
                bucket_counts[sec] += 1
            prev_small = gray
        frame_idx += 1

    cap.release()
    with np.errstate(invalid="ignore"):
        per_sec = np.where(bucket_counts > 0, bucket_sums / np.maximum(bucket_counts, 1), 0.0)
    return per_sec.astype(np.float32)


def _normalize(x: np.ndarray) -> np.ndarray:
    if x.size == 0:
        return x
    lo = float(np.percentile(x, 5))
    hi = float(np.percentile(x, 95))
    if hi - lo < 1e-9:
        return np.zeros_like(x, dtype=np.float32)
    n = (x - lo) / (hi - lo)
    return np.clip(n, 0.0, 1.0).astype(np.float32)


def _smooth(x: np.ndarray, window: int = 3) -> np.ndarray:
    if x.size == 0 or window <= 1:
        return x
    kernel = np.ones(window, dtype=np.float32) / window
    return np.convolve(x, kernel, mode="same").astype(np.float32)


def find_best_moments(
    video_path: str,
    n_clips: int = 4,
    clip_len: float = 25.0,
    min_gap: float = 5.0,
    audio_weight: float = 0.6,
    motion_weight: float = 0.4,
) -> list[Moment]:
    """Return up to n_clips non-overlapping Moments ranked by combined score."""
    duration = probe_duration(video_path)
    if duration <= 0:
        return []

    n_secs = int(math.ceil(duration))
    audio = extract_audio_rms(video_path)
    motion = compute_motion_per_second(video_path)

    # Pad/truncate to n_secs
    audio = np.pad(audio, (0, max(0, n_secs - audio.size)))[:n_secs]
    motion = np.pad(motion, (0, max(0, n_secs - motion.size)))[:n_secs]

    audio_n = _smooth(_normalize(audio), 3)
    motion_n = _smooth(_normalize(motion), 3)
    score = audio_weight * audio_n + motion_weight * motion_n

    # If totally silent + motionless, fall back to evenly-spaced clips
    if float(score.max()) < 1e-6:
        score = np.linspace(1.0, 0.5, n_secs).astype(np.float32)

    half = clip_len / 2.0
    moments: list[Moment] = []
    used = np.zeros(n_secs, dtype=bool)

    # Greedy non-overlapping selection on per-second peaks
    order = np.argsort(-score)
    for sec in order:
        if len(moments) >= n_clips:
            break
        if used[sec]:
            continue
        center = float(sec)
        start = max(0.0, center - half)
        end = min(duration, start + clip_len)
        # Re-pin start so duration is preserved when near the tail
        start = max(0.0, end - clip_len)
        # Mark window + min_gap as used
        block_start = max(0, int(math.floor(start - min_gap)))
        block_end = min(n_secs, int(math.ceil(end + min_gap)))
        if used[block_start:block_end].any():
            # Overlap with already-selected: skip
            continue
        used[block_start:block_end] = True
        moments.append(
            Moment(
                start=start,
                end=end,
                score=float(score[sec]),
                audio_score=float(audio_n[sec]),
                motion_score=float(motion_n[sec]),
            )
        )

    # Sort by start time so the UI lists them in chronological order
    moments.sort(key=lambda m: m.start)
    return moments


def has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
