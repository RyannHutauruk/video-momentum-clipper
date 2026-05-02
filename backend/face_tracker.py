"""
Face tracking for speaker-aware 9:16 cropping.

Given a source video and a clip range (start, end), samples a few frames per
second, runs a Haar-cascade face detector (built into OpenCV — no extra
download), and returns a smoothed list of (clip-relative time, x-center)
pairs that the clipper can feed into a time-varying ffmpeg ``crop`` filter.

Design notes
------------
- We sample the *source* video, not a re-extracted clip, to avoid an extra
  ffmpeg pass.
- Sampling rate is intentionally low (default 2 Hz). A talking head barely
  moves frame-to-frame; oversampling just adds CPU time without helping the
  crop. Heavy EMA smoothing on top hides any micro-jitter.
- When zero faces are detected on a sample, we hold the last known position
  rather than snapping back to centre. If the *whole* clip has zero faces
  we return ``None`` so the caller falls back to centre-crop.
- "Biggest face" is a deliberate simplification: in podcast-style sources
  the host's face dominates frame area. Multi-face speaker selection
  (mouth-motion / saliency) is out of scope for this first pass — falling
  back to the dominant face is good enough to crush the "speaker drifts off
  the edge of the crop" failure mode that center-crop has on landscape
  podcasts.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import cv2

_CASCADE_PATH = os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml")
_DETECTOR: cv2.CascadeClassifier | None = None


def _detector() -> cv2.CascadeClassifier:
    global _DETECTOR
    if _DETECTOR is None:
        _DETECTOR = cv2.CascadeClassifier(_CASCADE_PATH)
    return _DETECTOR


@dataclass
class FaceTrack:
    """Smoothed crop-center curve for a single clip range.

    ``samples`` is a list of (t_clip, x_center_norm) pairs where
    ``t_clip`` is seconds since the start of the clip (NOT source) and
    ``x_center_norm`` is the desired x-center in source-pixel units
    normalised to [0, 1].
    """

    samples: list[tuple[float, float]]
    source_w: int
    source_h: int


def track_face_xs(
    src: str,
    start: float,
    end: float,
    sample_hz: float = 2.0,
    ema_alpha: float = 0.35,
) -> FaceTrack | None:
    """Return a smoothed face-x curve for a clip range, or ``None``.

    Returns ``None`` if the source is unreadable, or if not a single face
    was detected across the clip — in those cases the caller should
    centre-crop.
    """
    cap = cv2.VideoCapture(src)
    if not cap.isOpened():
        return None
    try:
        src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        if src_w <= 0 or src_h <= 0:
            return None

        clip_len = max(0.5, end - start)
        step = 1.0 / max(0.5, sample_hz)
        det = _detector()
        if det.empty():
            return None

        raw: list[tuple[float, float | None]] = []
        t = 0.0
        while t <= clip_len + 1e-3:
            cap.set(cv2.CAP_PROP_POS_MSEC, (start + t) * 1000.0)
            ok, frame = cap.read()
            if not ok or frame is None:
                raw.append((t, None))
                t += step
                continue
            # Down-scale for detection speed; we only need the face bbox.
            h, w = frame.shape[:2]
            scale = 480.0 / max(1, w)
            small = cv2.resize(frame, (int(w * scale), int(h * scale)))
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            faces = det.detectMultiScale(
                gray, scaleFactor=1.2, minNeighbors=4,
                minSize=(30, 30),
            )
            if len(faces) == 0:
                raw.append((t, None))
            else:
                # Pick biggest face by area.
                fx, fy, fw, fh = max(faces, key=lambda b: b[2] * b[3])
                # Convert detection coords back to source coords, then norm.
                cx_src = (fx + fw / 2.0) / scale
                raw.append((t, cx_src / src_w))
            t += step

        # If no detections at all, give up.
        if not any(x is not None for _, x in raw):
            return None

        # Forward-fill: hold last known x across gaps; back-fill the start
        # if the very first samples missed.
        first_x = next((x for _, x in raw if x is not None), 0.5)
        filled: list[tuple[float, float]] = []
        last = first_x
        for tt, x in raw:
            if x is None:
                filled.append((tt, last))
            else:
                filled.append((tt, x))
                last = x

        # EMA smooth so the crop window doesn't twitch on micro-movements.
        smoothed: list[tuple[float, float]] = []
        ema = filled[0][1]
        for tt, x in filled:
            ema = ema * (1 - ema_alpha) + x * ema_alpha
            smoothed.append((tt, ema))

        return FaceTrack(samples=smoothed, source_w=src_w, source_h=src_h)
    finally:
        cap.release()


# Hard cap on how many keypoints we encode into the ffmpeg crop expression.
# Each keypoint adds one nested ``if(lt(t,...),...)`` level; ffmpeg's expression
# parser starts choking somewhere past ~80 levels of nesting (the actual limit
# is build-dependent — we hit "Failed to configure input pad" / "Error
# reinitializing filters" on a 70 s clip with 140 keypoints). 32 is plenty for
# a slowly-moving talking head and gives us headroom on every codec build.
_MAX_CROP_KEYPOINTS = 32


def crop_x_expr(
    track: FaceTrack,
    crop_w: int,
    safety_zoom: float = 1.0,
) -> str:
    """Build a piecewise-constant ffmpeg expression for the crop x-offset.

    ``crop_w`` is the output crop width in *source* pixels. The returned
    expression evaluates to a top-left x-coordinate clamped so the crop
    window always stays inside the frame.

    Long clips can produce hundreds of face samples; we decimate down to
    ``_MAX_CROP_KEYPOINTS`` before emitting the nested ``if(lt(...))`` chain
    so the expression stays inside ffmpeg's parser depth limit.
    """
    src_w = track.source_w
    max_left = max(0, src_w - crop_w)

    def left_for(x_norm: float) -> int:
        """Top-left x in source pixels for a given x-center normalised to [0,1]."""
        cx = x_norm * src_w
        left = int(round(cx - crop_w / 2))
        return max(0, min(max_left, left))

    if not track.samples:
        return str(max_left // 2)

    # Decimate uniformly so we never exceed the parser depth limit on long
    # clips (e.g. 70 s × 2 Hz = 140 samples). We always keep the last sample
    # so the trailing fallback represents the end of the clip.
    samples = track.samples
    n = len(samples)
    if n > _MAX_CROP_KEYPOINTS:
        stride = n / float(_MAX_CROP_KEYPOINTS)
        idxs = [int(round(i * stride)) for i in range(_MAX_CROP_KEYPOINTS)]
        idxs = sorted(set(min(n - 1, i) for i in idxs))
        if idxs[-1] != n - 1:
            idxs.append(n - 1)
        samples = [samples[i] for i in idxs]

    # Build nested if() from end backwards so the cheapest branch fires first.
    # The final fallback is the last sample's x — guarantees the expression
    # is total over t.
    last_x = left_for(samples[-1][1])
    expr = str(last_x)
    for tt, x_norm in reversed(samples[:-1]):
        x_pix = left_for(x_norm)
        expr = f"if(lt(t\\,{tt:.3f})\\,{x_pix}\\,{expr})"
    return expr
