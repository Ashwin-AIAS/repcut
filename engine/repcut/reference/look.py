"""A reference clip's look, as the numbers of a Repcut theme (statistical colour mapping only).

What is measured is how the reference's colours are *distributed*, compared
with a baseline: the person's own footage when they match inside a project,
or a neutral generated baseline when there is none (``neutral_baseline``).
The result is a :class:`~repcut.grading.theme.Look`, the same reviewable
numbers a hand-written theme has, so a matched look is rendered by the same
LUT generator, under the same caps and the same skin protection, as Moody or
Hardstyle. Nothing about the reference's content, texture or shapes is
transferred: only where its luma and chroma sit (P1: the look, not the
footage).

Two fits, in order:

1. **Tone.** The luma quantiles of the baseline and the reference define a
   monotone transfer curve (histogram matching). The look's tone parameters
   (contrast, black lift, black crush, highlight roll-off) are fitted to that
   curve with ``lut._tone`` as the forward model, inside ``Look``'s bounds.
2. **Colour.** With the tone fixed, saturation, warmth and the two split
   tones are fitted so that ``lut.apply_look`` on the baseline reproduces the
   reference's chroma per luma band: the mean chroma vector (casts, warmth,
   split toning) and its spread (saturation).

Every bound is ``Look``'s own, so a garish reference cannot produce a garish
theme: a parameter the fit wanted beyond a cap is held at the cap and named in
``LookFit.capped`` (logged, and shown to the person). Skin protection is never
fitted: an extracted theme keeps at least the default protection.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise

import numpy as np
from numpy.typing import NDArray
from scipy.optimize import least_squares

from repcut.grading.lut import _luma, _tone, apply_look
from repcut.grading.theme import Look, SplitTone
from repcut.logging import get_logger

logger = get_logger(__name__)

Floats = NDArray[np.float64]

# Bumped whenever what an extracted look means changes; part of the cached profile.
LOOK_EXTRACTOR_VERSION = 1

# Pixels each side contributes to the fit: enough for stable quantiles, few
# enough that one colour fit stays well under a second on a laptop core.
SAMPLE_PIXELS = 12_000
_QUANTILES = np.linspace(0.01, 0.99, 99)
# Luma bands the colour statistics are taken in (output luma).
_BANDS = np.array([0.0, 0.12, 0.25, 0.38, 0.5, 0.62, 0.75, 0.88, 1.0])
_MIN_BAND_SHARE = 0.01
# Pivot is held: contrast about a moving pivot and black lift/crush describe
# the same curves, so fitting both makes the numbers unreadable.
_PIVOT = Look().pivot
# Split tones weaker than this are noise in the measurement, not a look.
_MIN_SPLIT_STRENGTH = 0.012
_SKIN_PROTECTION = Look().skin_protection

# Regularisation of the tone fit (see ``_fit_tone``).
_TONE_RIDGE = np.array([0.01, 0.03, 0.03, 0.01])
_BOTH_BLACKS = 0.5

_TONE_FIELDS = ("contrast", "black_lift", "black_crush", "highlight_rolloff")
_COLOUR_FIELDS = ("saturation", "warmth")


@dataclass(frozen=True, slots=True)
class LookFit:
    look: Look
    # Parameters the reference wanted past one of Repcut's caps, held at the cap.
    capped: tuple[str, ...]
    # Mean absolute error left in the luma transfer curve (0-1 scale).
    tone_residual: float
    # Mean absolute error left in the per-band chroma statistics.
    colour_residual: float

    def as_json(self) -> dict[str, object]:
        return {
            "look": self.look.model_dump(mode="json"),
            "capped": list(self.capped),
            "tone_residual": round(self.tone_residual, 5),
            "colour_residual": round(self.colour_residual, 5),
            "version": LOOK_EXTRACTOR_VERSION,
        }


def pixels_of(
    frames: Sequence[NDArray[np.uint8]], *, count: int = SAMPLE_PIXELS, seed: int = 0
) -> Floats:
    """A fixed-size, seeded sample of RGB pixels (0-1) drawn evenly from every frame."""
    flat = [np.asarray(f, dtype=np.float64).reshape(-1, 3) / 255.0 for f in frames if f.size]
    if not flat:
        raise ValueError("no frames to sample")
    rng = np.random.default_rng(seed)
    per = max(count // len(flat), 1)
    picks = [f[rng.integers(0, len(f), size=per)] for f in flat]
    return np.concatenate(picks, axis=0)


def neutral_baseline(count: int = SAMPLE_PIXELS, seed: int = 7) -> Floats:
    """What an ungraded, well-exposed clip's pixels look like, generated (no footage).

    Luma spread across the range (a little denser in the mid-tones), muted
    chroma in every hue. Used when a reference is analysed with no footage of
    the person's to compare against.
    """
    rng = np.random.default_rng(seed)
    y = np.clip(rng.beta(1.6, 1.8, size=count), 0.0, 1.0)
    hue = rng.uniform(0.0, 2.0 * math.pi, size=count)
    chroma = rng.gamma(2.0, 0.035, size=count) * (4.0 * y * (1.0 - y) + 0.15)
    # A zero-luma chroma plane spanned by two orthogonal tints.
    a = np.array([1.0, -0.4, -0.6])
    a = a - (a @ np.array([0.2126, 0.7152, 0.0722]))
    b = np.array([-0.2, 0.55, -1.0])
    b = b - (b @ np.array([0.2126, 0.7152, 0.0722]))
    rgb = y[:, None] + (chroma * np.cos(hue))[:, None] * a + (chroma * np.sin(hue))[:, None] * b
    return np.asarray(np.clip(rgb, 0.0, 1.0), dtype=np.float64)


# --- statistics ---------------------------------------------------------------


def _chroma(rgb: Floats) -> Floats:
    """(R-Y, B-Y) per pixel: the zero-luma part of the colour."""
    y = _luma(rgb)
    return np.stack([rgb[:, 0] - y, rgb[:, 2] - y], axis=1)


def colour_stats(rgb: Floats) -> tuple[Floats, Floats]:
    """Per luma band: (mean chroma vector, chroma RMS) and each band's share of pixels."""
    y = _luma(rgb)
    c = _chroma(rgb)
    rows: list[list[float]] = []
    shares: list[float] = []
    for low, high in pairwise(_BANDS):
        inside = (y >= low) & (y < high if high < 1.0 else y <= high)
        share = float(inside.mean())
        shares.append(share)
        if inside.sum() < 5:
            rows.append([0.0, 0.0, 0.0])
            continue
        band = c[inside]
        mean = band.mean(axis=0)
        rms = float(np.sqrt(((band - mean) ** 2).sum(axis=1).mean()))
        rows.append([float(mean[0]), float(mean[1]), rms])
    return np.array(rows), np.array(shares)


# --- fits -----------------------------------------------------------------------


def _tone_look(values: Floats) -> Look:
    contrast, lift, crush, rolloff = (float(v) for v in values)
    return Look(
        contrast=contrast,
        pivot=_PIVOT,
        black_lift=lift,
        black_crush=crush,
        highlight_rolloff=rolloff,
        saturation=1.0,
        skin_protection=0.0,
    )


def _bounds(fields: Sequence[str]) -> tuple[list[float], list[float]]:
    lows, highs = [], []
    for name in fields:
        info = Look.model_fields[name]
        low = high = None
        for meta in info.metadata:
            low = getattr(meta, "ge", low)
            high = getattr(meta, "le", high)
        if low is None or high is None:
            raise ValueError(f"Look.{name} has no bounds to fit inside")
        lows.append(float(low))
        highs.append(float(high))
    return lows, highs


def _fit_tone(base_y: Floats, ref_y: Floats) -> tuple[Floats, float, list[str]]:
    qb = np.quantile(base_y, _QUANTILES)
    qr = np.quantile(ref_y, _QUANTILES)
    lows, highs = _bounds(_TONE_FIELDS)

    def residual(values: Floats) -> Floats:
        fit = _tone(qb, _tone_look(values)) - qr
        # Lifting and crushing the blacks at once mostly cancel; with sampling
        # noise in the low quantiles the fit would otherwise wander along that
        # ridge. Prefer one of the two, and small numbers when the curve allows.
        _, lift, crush, _ = values
        both = math.sqrt(max(lift * crush, 0.0) + 1e-12)
        ridge = _TONE_RIDGE * np.asarray(values)
        return np.asarray(np.concatenate([fit, [both * _BOTH_BLACKS], ridge]), dtype=np.float64)

    best = None
    for start in ([0.0, 0.0, 0.0, 0.0], [0.3, 0.0, 0.03, 0.15], [-0.2, 0.05, 0.0, 0.0]):
        x0 = np.clip(start, np.array(lows) + 1e-6, np.array(highs) - 1e-6)
        result = least_squares(residual, x0, bounds=(lows, highs), method="trf", diff_step=1e-3)
        if best is None or result.cost < best.cost:
            best = result
    if best is None:
        raise RuntimeError("no fit ran")
    capped = [
        name
        for name, value, low, high in zip(_TONE_FIELDS, best.x, lows, highs, strict=True)
        if (value >= high - 1e-3 and high > 0) or (value <= low + 1e-3 and low < 0)
    ]
    return best.x, float(np.abs(best.fun[: len(qb)]).mean()), capped


def _colour_look(tone: Look, values: Floats) -> Look:
    saturation, warmth, sa, sb, ha, hb = (float(v) for v in values)
    return tone.model_copy(
        update={
            "saturation": saturation,
            "warmth": warmth,
            "shadows": _split_tone(sa, sb, clamp=True),
            "highlights": _split_tone(ha, hb, clamp=True),
            "skin_protection": _SKIN_PROTECTION,
        }
    )


def _split_tone(a: float, b: float, *, clamp: bool) -> SplitTone | None:
    strength = math.hypot(a, b)
    if strength < 1e-9:
        return None
    if clamp:
        strength = min(strength, 0.15)
    hue = math.degrees(math.atan2(b, a)) % 360.0
    return SplitTone(hue=round(hue, 3) % 360.0, strength=strength)


def _fit_colour(tone: Look, base: Floats, ref: Floats) -> tuple[Look, float, list[str]]:
    target, ref_share = colour_stats(ref)
    lows, highs = _bounds(_COLOUR_FIELDS)
    # Split tones as (strength*cos hue, strength*sin hue); a box keeps the fit smooth,
    # the 0.15 strength cap is applied when the vector becomes a SplitTone.
    lows = [*lows, -0.15, -0.15, -0.15, -0.15]
    highs = [*highs, 0.15, 0.15, 0.15, 0.15]

    def residual(values: Floats) -> Floats:
        stats, share = colour_stats(apply_look(base, _colour_look(tone, values)))
        weight = np.sqrt(np.maximum(np.minimum(share, ref_share), 0.0))
        weight = np.where((share >= _MIN_BAND_SHARE) & (ref_share >= _MIN_BAND_SHARE), weight, 0.0)
        diff = (stats - target) * np.array([2.0, 2.0, 1.0])
        return np.asarray((diff * weight[:, None]).ravel(), dtype=np.float64)

    best = None
    for start in ([1.0, 0.0, 0.0, 0.0, 0.0, 0.0], [0.8, -0.01, -0.03, 0.0, 0.0, 0.0]):
        x0 = np.clip(start, np.array(lows) + 1e-6, np.array(highs) - 1e-6)
        result = least_squares(residual, x0, bounds=(lows, highs), method="trf", diff_step=1e-2)
        if best is None or result.cost < best.cost:
            best = result
    if best is None:
        raise RuntimeError("no fit ran")
    values = best.x
    capped = [
        name
        for name, value, low, high in zip(_COLOUR_FIELDS, values[:2], lows, highs, strict=False)
        if value >= high - 1e-3 or value <= low + 1e-3
    ]
    for name, (a, b) in (("shadows", values[2:4]), ("highlights", values[4:6])):
        if math.hypot(a, b) > 0.15 + 1e-6:
            capped.append(f"{name}.strength")
    look = _colour_look(tone, values)
    look = look.model_copy(
        update={
            "shadows": _keep(look.shadows),
            "highlights": _keep(look.highlights),
            "warmth": round(look.warmth, 4),
            "saturation": round(look.saturation, 4),
        }
    )
    stats, _ = colour_stats(apply_look(base, look))
    used = ref_share >= _MIN_BAND_SHARE
    colour_residual = float(np.abs(stats - target)[used].mean()) if used.any() else 0.0
    return look, colour_residual, capped


def _keep(tone: SplitTone | None) -> SplitTone | None:
    if tone is None or tone.strength < _MIN_SPLIT_STRENGTH:
        return None
    return SplitTone(hue=round(tone.hue, 1) % 360.0, strength=round(tone.strength, 4))


def extract_look(reference: Floats, baseline: Floats) -> LookFit:
    """The look that, applied to ``baseline``, best reproduces ``reference``'s colour statistics."""
    reference = np.clip(np.asarray(reference, dtype=np.float64), 0.0, 1.0)
    baseline = np.clip(np.asarray(baseline, dtype=np.float64), 0.0, 1.0)
    if len(reference) < 100 or len(baseline) < 100:
        raise ValueError("too few pixels to measure a look")
    tone_values, tone_residual, capped = _fit_tone(_luma(baseline), _luma(reference))
    tone = _tone_look(np.round(tone_values, 4))
    look, colour_residual, colour_capped = _fit_colour(tone, baseline, reference)
    capped_all = tuple(sorted(set(capped) | set(colour_capped)))
    if capped_all:
        # A garish reference degrades to the nearest look inside the caps; say so.
        logger.info("reference_look_capped", capped=list(capped_all))
    return LookFit(
        look=look,
        capped=capped_all,
        tone_residual=tone_residual,
        colour_residual=colour_residual,
    )


def look_distance(a: Look, b: Look, samples: Floats | None = None) -> float:
    """Mean RGB difference between two looks applied to the same pixels (0-1 scale)."""
    pixels = neutral_baseline() if samples is None else samples
    return float(np.abs(apply_look(pixels, a) - apply_look(pixels, b)).mean())


__all__ = [
    "LOOK_EXTRACTOR_VERSION",
    "SAMPLE_PIXELS",
    "LookFit",
    "colour_stats",
    "extract_look",
    "look_distance",
    "neutral_baseline",
    "pixels_of",
]
