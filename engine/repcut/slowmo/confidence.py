"""Does the interpolated picture hold up? The fallback that makes P1 real.

An interpolated frame is a guess about an instant the camera did not record.
When the motion estimate is wrong - a fast, straight barbell is RIFE's known
worst case - the guess shows something that never happened: a bent bar, a
doubled plate, a smeared arm. This module scores a shot's interpolation and
says whether it may be shown. Below ``THRESHOLD`` the shot plays at normal
speed, with the reason logged and reported.

Two measurements, both on the parts of the frame that move (a static gym wall
interpolates perfectly and would otherwise drown out a broken bar):

1. **Warp error against the truth (leave one out).** For captured frames
   ``f[i-1], f[i], f[i+1]``, interpolate ``f[i-1] -> f[i+1]`` and compare the
   result with the real ``f[i]``. This is the only check that has ground
   truth: the camera did record that instant. It spans twice the motion the
   real slow-motion frames span, so it is a pessimistic estimate - the safe
   direction for P1.
2. **Discontinuity in what is shown (SSIM).** For every invented frame ``m``
   between captured ``a`` and ``b``: a natural in-between frame is at least as
   similar to each neighbour as the neighbours are to each other. When ``m``
   is less like ``a`` and ``b`` than they are like each other, it is a jump in
   the sequence - a visible pop.

Both are structural similarity (SSIM, Gaussian 11x11, the standard form) on
luma at ``SCORE_SHORT_SIDE``, averaged inside a motion mask. The shot's
confidence is the worse of the two, each taken at a low percentile across the
shot rather than its mean: one broken frame is the artifact a viewer sees.

Pure: numpy and OpenCV only, no torch, no I/O. The interpolator is passed in.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

import cv2
import numpy as np

from repcut.slowmo.rife import Interpolator

CONFIDENCE_VERSION: Final = 1

# At or above this, a shot's slow motion is shown; below it, normal speed.
THRESHOLD: Final = 0.80

# Scoring resolution: enough for a bar's edge, small enough to be quick.
SCORE_SHORT_SIDE: Final = 360
# A pixel "moves" when its luma changes by this much (8-bit) across the gap.
MOTION_DELTA: Final = 12
# Below this fraction of moving pixels a pair is static: nothing to get wrong.
MIN_MOVING_FRACTION: Final = 0.004
# Leave-one-out samples per shot (each costs one extra interpolation).
LOO_SAMPLES: Final = 12
# The percentile a shot is judged at: its worst tenth, not its average.
PERCENTILE: Final = 10.0
# How strongly a discontinuity counts against a frame (SSIM points -> score).
DISCONTINUITY_WEIGHT: Final = 2.0

_C1 = (0.01 * 255) ** 2
_C2 = (0.03 * 255) ** 2


@dataclass(frozen=True, slots=True)
class Verdict:
    """One shot's judgement. ``reason`` is a fixed sentence the person reads."""

    confidence: float
    accepted: bool
    warp_score: float
    continuity_score: float
    samples: int
    reason: str

    def to_json(self) -> dict[str, object]:
        return {
            "version": CONFIDENCE_VERSION,
            "confidence": round(self.confidence, 4),
            "accepted": self.accepted,
            "warp_score": round(self.warp_score, 4),
            "continuity_score": round(self.continuity_score, 4),
            "samples": self.samples,
            "threshold": THRESHOLD,
            "reason": self.reason,
        }


ACCEPTED = "the in-between frames match the real motion; shown in slow motion"
WARP_FAILED = (
    "the interpolated frames do not match the real motion (often a fast, straight bar); "
    "played at normal speed instead"
)
POP_FAILED = "the interpolated frames jump against the real ones; played at normal speed instead"
TOO_SHORT = "too few frames to check the slow motion; played at normal speed instead"


def luma(frame: np.ndarray) -> np.ndarray:
    """BT.709 luma of an RGB24 frame, downscaled to the scoring size, float32."""
    height, width = frame.shape[:2]
    factor = SCORE_SHORT_SIDE / min(height, width)
    if factor < 1.0:
        size = (max(1, round(width * factor)), max(1, round(height * factor)))
        frame = cv2.resize(frame, size, interpolation=cv2.INTER_AREA)
    rgb = frame.astype(np.float32)
    result: np.ndarray = 0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]
    return result


def ssim_map(first: np.ndarray, second: np.ndarray) -> np.ndarray:
    """Per-pixel SSIM of two luma images (Wang et al. 2004, Gaussian sigma 1.5)."""

    def blur(image: np.ndarray) -> np.ndarray:
        out: np.ndarray = cv2.GaussianBlur(image, (11, 11), 1.5)
        return out

    mu1, mu2 = blur(first), blur(second)
    mu1_sq, mu2_sq, mu12 = mu1 * mu1, mu2 * mu2, mu1 * mu2
    sigma1 = blur(first * first) - mu1_sq
    sigma2 = blur(second * second) - mu2_sq
    sigma12 = blur(first * second) - mu12
    numerator = (2 * mu12 + _C1) * (2 * sigma12 + _C2)
    denominator = (mu1_sq + mu2_sq + _C1) * (sigma1 + sigma2 + _C2)
    result: np.ndarray = numerator / denominator
    return result


def motion_mask(first: np.ndarray, second: np.ndarray) -> np.ndarray | None:
    """Where the picture moves between two luma frames; None when almost nothing does."""
    moving = (np.abs(first - second) > MOTION_DELTA).astype(np.uint8)
    moving = cv2.dilate(moving, np.ones((9, 9), np.uint8))
    if moving.mean() < MIN_MOVING_FRACTION:
        return None
    mask: np.ndarray = moving.astype(bool)
    return mask


def masked_ssim(first: np.ndarray, second: np.ndarray, mask: np.ndarray) -> float:
    return float(ssim_map(first, second)[mask].mean())


def warp_scores(
    frames: Sequence[np.ndarray] | np.ndarray, interpolator: Interpolator
) -> list[float]:
    """Leave-one-out: interpolate over each sampled real frame and compare with it."""
    inner = list(range(1, len(frames) - 1))
    if len(inner) > LOO_SAMPLES:
        picks = np.linspace(0, len(inner) - 1, LOO_SAMPLES).round().astype(int)
        inner = [inner[int(p)] for p in sorted(set(picks.tolist()))]
    scores: list[float] = []
    for index in inner:
        before, truth, after = frames[index - 1], frames[index], frames[index + 1]
        guess = luma(interpolator.midpoint(before, after))
        real = luma(truth)
        mask = motion_mask(luma(before), luma(after))
        scores.append(1.0 if mask is None else masked_ssim(guess, real, mask))
    return scores


def continuity_scores(
    frames: Sequence[np.ndarray] | np.ndarray, middles: Sequence[np.ndarray]
) -> list[float]:
    """One score per invented frame: 1.0 when it sits naturally between its neighbours."""
    scores: list[float] = []
    for index, middle in enumerate(middles):
        a, b, m = luma(frames[index]), luma(frames[index + 1]), luma(middle)
        mask = motion_mask(a, b)
        if mask is None:
            scores.append(1.0)
            continue
        neighbours = masked_ssim(a, b, mask)
        nearest = min(masked_ssim(a, m, mask), masked_ssim(m, b, mask))
        jump = max(0.0, neighbours - nearest)
        scores.append(max(0.0, 1.0 - DISCONTINUITY_WEIGHT * jump))
    return scores


def judge(warp: Sequence[float], continuity: Sequence[float]) -> Verdict:
    """The shot's verdict from its per-frame scores."""
    if not warp or not continuity:
        return Verdict(0.0, False, 0.0, 0.0, 0, TOO_SHORT)
    warp_score = float(np.percentile(warp, PERCENTILE))
    continuity_score = float(np.percentile(continuity, PERCENTILE))
    confidence = min(warp_score, continuity_score)
    accepted = confidence >= THRESHOLD
    if accepted:
        reason = ACCEPTED
    elif warp_score <= continuity_score:
        reason = WARP_FAILED
    else:
        reason = POP_FAILED
    return Verdict(
        confidence, accepted, warp_score, continuity_score, len(warp) + len(continuity), reason
    )


__all__ = [
    "ACCEPTED",
    "CONFIDENCE_VERSION",
    "POP_FAILED",
    "THRESHOLD",
    "TOO_SHORT",
    "WARP_FAILED",
    "Verdict",
    "continuity_scores",
    "judge",
    "luma",
    "masked_ssim",
    "motion_mask",
    "ssim_map",
    "warp_scores",
]
