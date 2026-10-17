"""v1 reframing: a centre crop to the preset's aspect, nudged per scene by hand.

``offset`` runs -1 (crop hard left / top) to +1 (hard right / bottom) across the
picture the crop leaves out; 0 is the centre. The crop never leaves the frame
and never pads: nothing is invented at the edges (P1). ``crop_path`` below moves
that crop over time to follow the subject (Prompt 10).
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Sequence
from dataclasses import dataclass

from repcut.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Crop:
    width: int
    height: int
    x: int
    y: int

    def filter(self) -> str:
        return f"crop={self.width}:{self.height}:{self.x}:{self.y}"


def _even(value: float) -> int:
    return max(2, int(value) // 2 * 2)


def crop_for(width: int, height: int, aspect: float, offset: float = 0.0) -> Crop:
    """The largest ``aspect`` rectangle inside ``width``x``height``, placed by ``offset``."""
    if width <= 0 or height <= 0 or aspect <= 0:
        raise ValueError("crop needs positive dimensions")
    offset = min(1.0, max(-1.0, offset))
    if width / height > aspect:
        crop_w, crop_h = _even(height * aspect), _even(height)
    else:
        crop_w, crop_h = _even(width), _even(width / aspect)
    crop_w, crop_h = min(crop_w, width // 2 * 2), min(crop_h, height // 2 * 2)
    slack_x, slack_y = width - crop_w, height - crop_h
    # Even offsets: a 4:2:0 crop that starts on an odd pixel shifts chroma by half a sample.
    x = min(round(slack_x * (offset + 1) / 4) * 2, slack_x // 2 * 2)
    y = min(round(slack_y * (offset + 1) / 4) * 2, slack_y // 2 * 2)
    return Crop(crop_w, crop_h, x, y)


# --- subject-tracked reframing (Prompt 10) ---------------------------------------

# A subject missing for longer than this is "lost": the crop eases to the centre.
LOST_SECONDS = 1.0
# Zero-phase smoothing (a Gaussian over time): kills detector jitter without the
# lag a one-sided filter would add. The camera never chases a twitch.
SMOOTH_SIGMA_SECONDS = 0.35
# How fast the crop may pan, in crop-widths per second: a real operator's ceiling.
MAX_PAN_PER_SECOND = 0.9
# One keyframe per this many output frames; FFmpeg interpolates linearly between.
KEY_EVERY_FRAMES = 6


@dataclass(frozen=True, slots=True)
class CropPath:
    """A shot's crop over time: fixed size, ``x`` keyed by the shot's own frame number."""

    width: int
    height: int
    y: int
    keys: tuple[tuple[int, int], ...]
    lost_seconds: float = 0.0
    centred: bool = False  # nobody was found at all: the plain centre crop

    def x_at(self, frame: int) -> float:
        keys = self.keys
        if frame <= keys[0][0]:
            return float(keys[0][1])
        for (f0, x0), (f1, x1) in itertools.pairwise(keys):
            if f0 <= frame <= f1:
                return x0 + (x1 - x0) * (frame - f0) / max(1, f1 - f0)
        return float(keys[-1][1])


def _interp(times: list[float], values: list[float], at: float) -> float:
    if at <= times[0]:
        return values[0]
    if at >= times[-1]:
        return values[-1]
    for i in range(1, len(times)):
        if at <= times[i]:
            t0, t1 = times[i - 1], times[i]
            return values[i - 1] + (values[i] - values[i - 1]) * (at - t0) / max(1e-9, t1 - t0)
    return values[-1]


def _gaussian(values: list[float], sigma: float) -> list[float]:
    if sigma <= 0 or len(values) < 3:
        return values
    radius = max(1, int(3 * sigma))
    weights = [math.exp(-0.5 * (k / sigma) ** 2) for k in range(-radius, radius + 1)]
    out = []
    n = len(values)
    for i in range(n):
        total = acc = 0.0
        for k, w in zip(range(-radius, radius + 1), weights, strict=True):
            j = min(n - 1, max(0, i + k))  # edges held, not mirrored: no invented motion
            acc += w * values[j]
            total += w
        out.append(acc / total)
    return out


def crop_path(
    samples: Sequence[tuple[float, float | None]],
    *,
    start: float,
    frames: int,
    fps: int,
    width: int,
    height: int,
    aspect: float,
    speed: float = 1.0,
) -> CropPath:
    """The crop for one shot that keeps the subject centred, smoothed, in the frame.

    ``samples`` are (source seconds, subject centre x 0-1 or None) from the
    clip's track. The shot covers ``frames`` output frames from source time
    ``start`` (``speed`` source seconds per output second). Where the subject
    is missing for more than ``LOST_SECONDS`` the target is the frame centre,
    reached smoothly; never a jump, never padding (P1).
    """
    fixed = crop_for(width, height, aspect, 0.0)
    slack = width - fixed.width
    centre_x = slack / 2
    if slack <= 0 or frames <= 0:
        return CropPath(fixed.width, fixed.height, fixed.y, ((0, fixed.x),), centred=True)
    end = start + frames / fps * speed
    seen = [(t, cx) for t, cx in samples if cx is not None and start - 2 <= t <= end + 2]
    if not seen:
        return CropPath(fixed.width, fixed.height, fixed.y, ((0, fixed.x),), centred=True)
    # Targets on a fixed time grid (one per output frame), centre where lost.
    times = [t for t, _ in seen]
    xs = [cx * width - fixed.width / 2 for _, cx in seen]
    target: list[float] = []
    lost = 0
    for f in range(frames):
        t = start + f / fps * speed
        before = max((s for s in times if s <= t), default=None)
        after = min((s for s in times if s >= t), default=None)
        gap = (
            (after - before)
            if before is not None and after is not None
            else (t - before if before is not None else after - t if after is not None else 1e9)
        )
        if gap > LOST_SECONDS:
            target.append(centre_x)
            lost += 1
        else:
            target.append(_interp(times, xs, t))
    smooth = _gaussian(target, SMOOTH_SIGMA_SECONDS * fps)
    # Pan-speed ceiling, applied both ways so it adds no lag.
    step = MAX_PAN_PER_SECOND * fixed.width / fps
    for seq in (range(1, frames), range(frames - 2, -1, -1)):
        for i in seq:
            prev = smooth[i - 1] if seq.step == 1 else smooth[i + 1]
            smooth[i] = min(prev + step, max(prev - step, smooth[i]))
    clamped = [min(float(slack), max(0.0, x)) for x in smooth]
    marks = sorted({*range(0, frames, KEY_EVERY_FRAMES), frames - 1})
    keys = tuple((f, round(clamped[f] / 2) * 2) for f in marks)
    if lost:
        logger.info("reframe_subject_lost", lost_seconds=round(lost / fps, 2), shot_frames=frames)
    return CropPath(fixed.width, fixed.height, fixed.y, keys, lost_seconds=round(lost / fps, 3))


__all__ = ["LOST_SECONDS", "Crop", "CropPath", "crop_for", "crop_path"]
