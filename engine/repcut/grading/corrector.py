"""The gym-lighting fixer: a bounded, per-scene correction before every theme.

Runs before every theme, the correction-only one included. It measures the
scene on this machine - as many proxy frames as the scene needs, never only
the one frame Gemini saw (amendment 012 row 10, P4 limits what leaves the
machine, not what is measured on it) - and adds the theme's adaptation rules
for the scene's lighting tags. ``vlm: null`` is a normal input: no tags means
a measurement-only correction, never an error (amendment 012 row 8).

The bounds are the guide's and they are hard: at most one stop of exposure and
800 K of white balance either way, denoise 0-1. A wrong tag can move the
correction by at most a rule's size (400 K, half a stop) and never past the
bound, so wrong analysis degrades gracefully instead of producing neon (P1).

Every step is pure: frames in, numbers out. ``apply.py`` reads the frames and
turns a ``Correction`` into FFmpeg stages.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray

from repcut.grading.theme import MAX_EXPOSURE_STOPS, MAX_TEMPERATURE_SHIFT_K, AdaptRule

# Where a correctly exposed gym scene's median display luma sits (0-1). Bright
# gyms land near it already; the correction only moves what is off.
TARGET_MEDIAN_LUMA = 0.42
# Fraction of a measured error the corrector removes. Below 1 on purpose: a
# gym at night is supposed to look like a gym at night, not like noon.
EXPOSURE_STRENGTH = 0.6
WHITE_BALANCE_STRENGTH = 0.6
# Luma noise (0-255 codes) at which denoise starts, and where it is full.
NOISE_FLOOR = 1.5
NOISE_FULL = 6.0
REFERENCE_KELVIN = 6500.0
# A white-balance estimate needs enough near-neutral pixels to mean anything.
MIN_NEUTRAL_FRACTION = 0.02

Image = NDArray[np.uint8]
Floats = NDArray[np.float64]


@dataclass(frozen=True, slots=True)
class SceneTags:
    """What analysis said about the scene's light, or ``None`` per field when it said nothing."""

    lighting_temperature: str | None = None
    lighting_quality: str | None = None


@dataclass(frozen=True, slots=True)
class Measurements:
    median_luma: float
    cast_kelvin: float | None
    noise_sigma: float
    frames: int


@dataclass(frozen=True, slots=True)
class Correction:
    """What the corrector will apply: always inside the guide's bounds."""

    exposure_stops: float = 0.0
    # Negative cools the picture, positive warms it.
    temperature_shift_k: float = 0.0
    denoise: float = 0.0
    rules: tuple[str, ...] = field(default=())

    def gains(self) -> tuple[float, float, float]:
        return white_balance_gains(self.temperature_shift_k)

    def as_features(self) -> dict[str, float | list[str]]:
        return {
            "exposure_stops": round(self.exposure_stops, 4),
            "temperature_shift_k": round(self.temperature_shift_k, 1),
            "denoise": round(self.denoise, 4),
            "rules": list(self.rules),
        }


# --- colour helpers ---------------------------------------------------------


def bt709_to_linear(value: Floats | float) -> Floats:
    """Inverse of the bt709 OETF (the transfer the proxy is tagged with)."""
    v = np.clip(np.asarray(value, dtype=np.float64), 0.0, 1.0)
    return np.where(v < 0.081, v / 4.5, np.power((v + 0.099) / 1.099, 1.0 / 0.45))


_XYZ_TO_BT709 = np.array(
    [
        [3.2404542, -1.5371385, -0.4985314],
        [-0.9692660, 1.8760108, 0.0415560],
        [0.0556434, -0.2040259, 1.0572252],
    ]
)


def planckian_xy(kelvin: float) -> tuple[float, float]:
    """CIE 1931 xy of a blackbody (Kang et al. 2002 cubic fit, 1667-25000 K)."""
    t = min(max(kelvin, 1667.0), 25000.0)
    if t <= 4000.0:
        x = -0.2661239e9 / t**3 - 0.2343589e6 / t**2 + 0.8776956e3 / t + 0.179910
    else:
        x = -3.0258469e9 / t**3 + 2.1070379e6 / t**2 + 0.2226347e3 / t + 0.240390
    if t <= 2222.0:
        y = -1.1063814 * x**3 - 1.34811020 * x**2 + 2.18555832 * x - 0.20219683
    elif t <= 4000.0:
        y = -0.9549476 * x**3 - 1.37418593 * x**2 + 2.09137015 * x - 0.16748867
    else:
        y = 3.0817580 * x**3 - 5.87338670 * x**2 + 3.75112997 * x - 0.37001483
    return x, y


def blackbody_rgb(kelvin: float) -> Floats:
    """Linear bt709 RGB of a blackbody's chromaticity, green = 1. Smooth in kelvin."""
    x, y = planckian_xy(kelvin)
    xyz = np.array([x / y, 1.0, (1.0 - x - y) / y])
    # Below ~2000 K a blackbody lies outside bt709 (negative blue); clamp, never invert.
    rgb = np.maximum(_XYZ_TO_BT709 @ xyz, 1e-3)
    return np.asarray(rgb / rgb[1], dtype=np.float64)


def white_balance_gains(shift_k: float) -> tuple[float, float, float]:
    """Per-channel linear gains for a shift; exactly (1, 1, 1) at zero."""
    if shift_k == 0.0:
        return (1.0, 1.0, 1.0)
    gains = blackbody_rgb(REFERENCE_KELVIN - shift_k) / blackbody_rgb(REFERENCE_KELVIN)
    gains = gains / gains[1]
    return (float(gains[0]), float(gains[1]), float(gains[2]))


def _mired(kelvin: float) -> float:
    return 1e6 / kelvin


def _relative_red_blue(kelvin: float) -> float:
    rgb, ref = blackbody_rgb(kelvin), blackbody_rgb(REFERENCE_KELVIN)
    return float((rgb[0] / rgb[2]) / (ref[0] / ref[2]))


_KELVIN_GRID = [1e6 / m for m in np.linspace(_mired(15000.0), _mired(2000.0), 400)]
_RATIO_GRID = np.array([math.log(_relative_red_blue(k)) for k in _KELVIN_GRID])


# --- measurement ------------------------------------------------------------


def _luma(rgb: Floats) -> Floats:
    return np.asarray(rgb @ np.array([0.2126, 0.7152, 0.0722]), dtype=np.float64)


def estimate_cast_kelvin(pixels: Floats) -> float | None:
    """The illuminant a grey-world over midtones implies, in kelvin."""
    luma = _luma(pixels)
    usable = (luma > 0.1) & (luma < 0.9)
    if usable.mean() < MIN_NEUTRAL_FRACTION:
        return None
    mean = bt709_to_linear(pixels[usable]).mean(axis=0)
    if mean[2] <= 1e-6 or mean[0] <= 1e-6:
        return None
    target = math.log(float(mean[0] / mean[2]))
    index = int(np.argmin(np.abs(_RATIO_GRID - target)))
    return float(_KELVIN_GRID[index])


def estimate_noise(luma: Floats) -> float:
    """Immerkær's fast noise estimate, in 0-255 codes."""
    if luma.shape[0] < 3 or luma.shape[1] < 3:
        return 0.0
    i = luma * 255.0
    lap = (
        i[:-2, :-2]
        - 2 * i[:-2, 1:-1]
        + i[:-2, 2:]
        - 2 * i[1:-1, :-2]
        + 4 * i[1:-1, 1:-1]
        - 2 * i[1:-1, 2:]
        + i[2:, :-2]
        - 2 * i[2:, 1:-1]
        + i[2:, 2:]
    )
    h, w = luma.shape
    return float(math.sqrt(math.pi / 2.0) * np.abs(lap).sum() / (6.0 * (w - 2) * (h - 2)))


def measure(frames: Sequence[Image]) -> Measurements | None:
    """Median luma, colour cast and noise over every frame. ``None`` for no frames."""
    if not frames:
        return None
    pixels = np.concatenate([f.reshape(-1, 3) for f in frames]).astype(np.float64) / 255.0
    noise = float(np.median([estimate_noise(_luma(f.astype(np.float64) / 255.0)) for f in frames]))
    return Measurements(
        median_luma=float(np.median(_luma(pixels))),
        cast_kelvin=estimate_cast_kelvin(pixels),
        noise_sigma=noise,
        frames=len(frames),
    )


# --- the correction ---------------------------------------------------------


def _matches(rule: AdaptRule, tags: SceneTags) -> bool:
    when = rule.when
    if (
        when.lighting_temperature is not None
        and when.lighting_temperature != tags.lighting_temperature
    ):
        return False
    return when.lighting_quality is None or when.lighting_quality == tags.lighting_quality


def _describe(rule: AdaptRule) -> str:
    when = rule.when
    keys = [f"{k}={v}" for k, v in when.model_dump().items() if v is not None]
    return " ".join(keys)


def correct(
    measurements: Measurements | None,
    tags: SceneTags,
    rules: Sequence[AdaptRule] = (),
) -> Correction:
    """Measurement first, then the theme's rules for these tags, then the hard bounds."""
    exposure = temperature = denoise = 0.0
    if measurements is not None:
        current = float(bt709_to_linear(max(measurements.median_luma, 1e-3)))
        target = float(bt709_to_linear(TARGET_MEDIAN_LUMA))
        exposure = EXPOSURE_STRENGTH * math.log2(target / current)
        if measurements.cast_kelvin is not None:
            mirrored = 2.0 * _mired(REFERENCE_KELVIN) - _mired(measurements.cast_kelvin)
            neutralise = REFERENCE_KELVIN - 1e6 / max(mirrored, _mired(25000.0))
            temperature = WHITE_BALANCE_STRENGTH * neutralise
        span = NOISE_FULL - NOISE_FLOOR
        denoise = min(max((measurements.noise_sigma - NOISE_FLOOR) / span, 0.0), 1.0)

    fired: list[str] = []
    for rule in rules:
        if _matches(rule, tags):
            exposure += rule.exposure_stops
            temperature += rule.temperature_shift_k
            denoise += rule.denoise
            fired.append(_describe(rule))

    return Correction(
        exposure_stops=min(max(exposure, -MAX_EXPOSURE_STOPS), MAX_EXPOSURE_STOPS),
        temperature_shift_k=min(
            max(temperature, -MAX_TEMPERATURE_SHIFT_K), MAX_TEMPERATURE_SHIFT_K
        ),
        denoise=min(max(denoise, 0.0), 1.0),
        rules=tuple(fired),
    )


__all__ = [
    "Correction",
    "Measurements",
    "SceneTags",
    "blackbody_rgb",
    "bt709_to_linear",
    "correct",
    "estimate_cast_kelvin",
    "estimate_noise",
    "measure",
    "planckian_xy",
    "white_balance_gains",
]
