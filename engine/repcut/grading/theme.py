"""A theme is data: one TOML file, validated here, never code (amendment 012).

A pack is a directory of theme files plus a ``pack.toml``. Adding a pack or a
theme is a new directory or file and zero engine changes; ``packs.py`` finds
them, this module checks their shape, ``lut.py`` turns a theme's ``[look]``
into a 3D LUT, and ``corrector.py`` reads its ``[adapt]`` rules.

Every bound below is a P1 bound: a theme may only push a picture as far as a
colourist would with the same controls. A theme file outside them is rejected
by name (``ThemeFileError``), never clamped silently, because a silently
clamped theme renders something its author did not write.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

# Theme and pack ids become path components (packs.py) - the same shape check
# security.md asks of every identifier that reaches the filesystem.
SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")

# The guide's bounds on adaptive correction (P1: a wrong analysis degrades, it
# never produces neon). corrector.py clamps to these; a theme's own adaptation
# rules are validated against them too.
MAX_EXPOSURE_STOPS = 1.0
MAX_TEMPERATURE_SHIFT_K = 800.0

LightingTemperature = Literal["warm", "neutral", "cool"]
LightingQuality = Literal["bright", "dim", "harsh", "soft", "mixed"]


class ThemeFileError(ValueError):
    """A theme or pack file that cannot be read, parsed or validated."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SplitTone(_Strict):
    """A hue pushed into one tonal range. Hue in degrees, strength 0-0.15."""

    hue: float = Field(ge=0, lt=360)
    strength: float = Field(ge=0, le=0.15)


class Look(_Strict):
    """What the LUT does, as numbers a person can read and review.

    Applied in this order to display-referred bt709 RGB (lut.py):
    tone curve on luma (contrast about ``pivot``, then ``black_lift``,
    ``black_crush``, ``highlight_rolloff``), saturation about luma, ``warmth``,
    split toning, then skin-hue protection. Neutral values are an identity.
    """

    contrast: float = Field(default=0.0, ge=-0.5, le=0.6)
    pivot: float = Field(default=0.45, ge=0.2, le=0.7)
    black_lift: float = Field(default=0.0, ge=0.0, le=0.12)
    black_crush: float = Field(default=0.0, ge=0.0, le=0.08)
    highlight_rolloff: float = Field(default=0.0, ge=0.0, le=0.35)
    saturation: float = Field(default=1.0, ge=0.5, le=1.4)
    warmth: float = Field(default=0.0, ge=-0.06, le=0.06)
    shadows: SplitTone | None = None
    highlights: SplitTone | None = None
    # How firmly skin-coloured inputs keep their hue through the look (0-1).
    skin_protection: float = Field(default=0.85, ge=0.0, le=1.0)


class Finish(_Strict):
    """Applied after the LUT, scaled by intensity. Grain is a finish, not content (P1)."""

    grain: float = Field(default=0.0, ge=0.0, le=12.0)


class AdaptWhen(_Strict):
    lighting_temperature: LightingTemperature | None = None
    lighting_quality: LightingQuality | None = None


class AdaptRule(_Strict):
    """One rule: when the scene's lighting tags match, nudge the correction.

    Keys are what analysis actually produces (amendment 012 row 8). A rule with
    an empty ``when`` would fire on every scene, which is a base correction,
    not an adaptation, so it is rejected.
    """

    when: AdaptWhen
    exposure_stops: float = Field(default=0.0, ge=-0.5, le=0.5)
    temperature_shift_k: float = Field(default=0.0, ge=-400, le=400)
    denoise: float = Field(default=0.0, ge=0.0, le=0.5)

    @field_validator("when")
    @classmethod
    def _not_empty(cls, value: AdaptWhen) -> AdaptWhen:
        if value.lighting_temperature is None and value.lighting_quality is None:
            raise ValueError("an adaptation rule must match at least one lighting tag")
        return value


class Recommend(_Strict):
    """How strongly analysis suggests this theme. Highest total wins; ties by pack order."""

    base: float = Field(default=0.0, ge=0.0, le=1.0)
    lighting_quality: dict[LightingQuality, float] = Field(default_factory=dict)
    lighting_temperature: dict[LightingTemperature, float] = Field(default_factory=dict)


class PaceSpec(_Strict):
    """How a theme cuts (Prompt 05): its energy and shot lengths in beats.

    Data, like the look. ``edit.types.Pace`` is built from it; the LUT digest
    reads ``look`` only, so pace never changes a LUT.
    """

    energy: float = Field(default=0.5, ge=0.0, le=1.0)
    calm_beats: int = Field(default=8, ge=1, le=32)
    peak_beats: int = Field(default=4, ge=1, le=32)


class Theme(_Strict):
    id: str
    name: str = Field(min_length=1, max_length=40)
    description: str = Field(min_length=1, max_length=280)
    default_intensity: int = Field(default=80, ge=0, le=100)
    look: Look = Look()
    finish: Finish = Finish()
    adapt: list[AdaptRule] = Field(default_factory=list)
    recommend: Recommend = Recommend()
    pace: PaceSpec = PaceSpec()

    @field_validator("id")
    @classmethod
    def _slug(cls, value: str) -> str:
        if not SLUG_PATTERN.match(value):
            raise ValueError("theme id must be a lowercase slug")
        return value


class Pack(_Strict):
    id: str
    name: str = Field(min_length=1, max_length=40)
    description: str = Field(min_length=1, max_length=280)
    # Display order, and the order ties in recommendation are broken by.
    themes: list[str] = Field(min_length=1)
    default_theme: str

    @field_validator("id")
    @classmethod
    def _slug(cls, value: str) -> str:
        if not SLUG_PATTERN.match(value):
            raise ValueError("pack id must be a lowercase slug")
        return value


def _read_toml(path: Path) -> dict[str, object]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ThemeFileError(f"{path.name}: cannot be read ({exc.strerror})") from exc
    try:
        return tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ThemeFileError(f"{path.name}: not valid TOML ({exc})") from exc


def _first_error(exc: ValidationError) -> str:
    error = exc.errors()[0]
    where = ".".join(str(part) for part in error["loc"]) or "file"
    return f"{where}: {error['msg']}"


def load_theme(path: Path) -> Theme:
    """One theme file, validated. The file's stem must be the theme's id."""
    data = _read_toml(path)
    try:
        theme = Theme.model_validate(data)
    except ValidationError as exc:
        raise ThemeFileError(f"{path.name}: {_first_error(exc)}") from exc
    if theme.id != path.stem:
        raise ThemeFileError(f"{path.name}: id {theme.id!r} does not match the file name")
    return theme


def load_pack_manifest(path: Path) -> Pack:
    data = _read_toml(path)
    try:
        pack = Pack.model_validate(data)
    except ValidationError as exc:
        raise ThemeFileError(f"{path.name}: {_first_error(exc)}") from exc
    if pack.default_theme not in pack.themes:
        raise ThemeFileError(f"{path.name}: default_theme is not one of its themes")
    if len(set(pack.themes)) != len(pack.themes):
        raise ThemeFileError(f"{path.name}: a theme is listed twice")
    for theme_id in pack.themes:
        if not SLUG_PATTERN.match(theme_id):
            raise ThemeFileError(f"{path.name}: theme id {theme_id!r} is not a slug")
    return pack


__all__ = [
    "MAX_EXPOSURE_STOPS",
    "MAX_TEMPERATURE_SHIFT_K",
    "SLUG_PATTERN",
    "AdaptRule",
    "AdaptWhen",
    "Finish",
    "LightingQuality",
    "LightingTemperature",
    "Look",
    "Pack",
    "Recommend",
    "SplitTone",
    "Theme",
    "ThemeFileError",
    "load_pack_manifest",
    "load_theme",
]
