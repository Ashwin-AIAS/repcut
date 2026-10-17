"""What the style profile is, as data, and how the recommenders read it.

The profile is a set of **priors**: small, bounded nudges to what Repcut would
suggest anyway. Every nudge is bounded so that a strong signal elsewhere (the
scene's lighting, a matched reference, the person's own explicit choice) still
wins; no option is ever removed (P2). An empty profile changes nothing at all:
every ``apply_*`` method below is the identity on ``StyleProfile()``, which is
what makes "cold start == Wave 1 behaviour" exact rather than approximate.

Every value carries its evidence (how many overrides, the sentence that says
why), so a recommendation shaped by the profile can always answer "why" from
the profile's own data.

Serialisation is pydantic only (``model_validate_json``), with
``extra="forbid"``: no pickle, no eval (guide section 7).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from repcut.edit.types import Pace

PROFILE_SCHEMA_VERSION: Literal[1] = 1

# Bounds: the most the profile can move each recommendation.
THEME_PRIOR_MAX = 0.35  # theme scores run roughly 0.1-0.7 (pack.toml [recommend])
INTENSITY_BIAS_MAX = 20.0  # percentage points on a theme's default intensity
PACING_BIAS_MAX = 1.0  # -1 slower ... +1 faster
MUSIC_ENERGY_MAX = 0.25  # on the 0-1 energy the music recommender wants
CAPTION_PRIOR_MAX = 1.0


class ProfileItem(StrEnum):
    """The profile's items. Each can be reset on its own (P2 applies to the profile)."""

    THEME = "theme"
    INTENSITY = "intensity"
    PACING = "pacing"
    CAPTION_STYLE = "caption_style"
    MUSIC_ENERGY = "music_energy"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Evidence(_Strict):
    """Why an item holds its value: how many overrides, and in words."""

    events: int = Field(ge=0)
    why: str = Field(max_length=400)
    active: bool = False  # enough evidence to change a recommendation


class Resets(_Strict):
    """``style_profile.resets_json``: when each item was last reset."""

    items: dict[ProfileItem, datetime] = Field(default_factory=dict)


class StyleProfile(_Strict):
    """A person's learned style. ``StyleProfile()`` is the cold start."""

    schema_version: Literal[1] = PROFILE_SCHEMA_VERSION
    theme: dict[str, float] = Field(default_factory=dict)
    intensity: float = 0.0
    pacing: float = 0.0
    caption_style: dict[str, float] = Field(default_factory=dict)
    music_energy: float = 0.0
    evidence: dict[ProfileItem, Evidence] = Field(default_factory=dict)

    def active(self, item: ProfileItem) -> bool:
        found = self.evidence.get(item)
        return found is not None and found.active

    def why(self, item: ProfileItem) -> str | None:
        found = self.evidence.get(item)
        return found.why if found is not None and found.active else None

    # --- what the recommenders call -----------------------------------------

    def theme_bonus(self, theme_id: str) -> float:
        if not self.active(ProfileItem.THEME):
            return 0.0
        return self.theme.get(theme_id, 0.0)

    def favourite_theme(self, available: list[str]) -> str | None:
        """The theme the profile leans to most, if it leans to one at all."""
        if not self.active(ProfileItem.THEME):
            return None
        ranked = sorted(
            ((self.theme.get(t, 0.0), -i, t) for i, t in enumerate(available)), reverse=True
        )
        best = ranked[0] if ranked else None
        return best[2] if best is not None and best[0] > 0 else None

    def apply_intensity(self, default: int) -> int:
        if not self.active(ProfileItem.INTENSITY):
            return default
        return int(min(100, max(0, round(default + self.intensity))))

    def apply_pace(self, pace: Pace) -> Pace:
        """Shorter shots for a fast-cut person, longer for a slow one; never below a beat."""
        if not self.active(ProfileItem.PACING) or self.pacing == 0.0:
            return pace
        factor = 1.0 - 0.4 * self.pacing  # +1 -> 0.6x shot length, -1 -> 1.4x
        return replace(
            pace,
            energy=min(1.0, max(0.0, pace.energy + 0.2 * self.pacing)),
            calm_beats=max(1, min(32, round(pace.calm_beats * factor))),
            peak_beats=max(1, min(32, round(pace.peak_beats * factor))),
        )

    def music_energy_shift(self) -> float:
        return self.music_energy if self.active(ProfileItem.MUSIC_ENERGY) else 0.0

    def caption_default(self, default: str, available: list[str]) -> str:
        if not self.active(ProfileItem.CAPTION_STYLE):
            return default
        best = max(available, key=lambda s: (self.caption_style.get(s, 0.0), s == default))
        return best if self.caption_style.get(best, 0.0) > 0 else default

    def notes(self) -> list[str]:
        """Every active item's sentence, in item order: what the profile is doing now."""
        return [e.why for item in ProfileItem if (e := self.evidence.get(item)) and e.active]


__all__ = [
    "CAPTION_PRIOR_MAX",
    "INTENSITY_BIAS_MAX",
    "MUSIC_ENERGY_MAX",
    "PACING_BIAS_MAX",
    "PROFILE_SCHEMA_VERSION",
    "THEME_PRIOR_MAX",
    "Evidence",
    "ProfileItem",
    "Resets",
    "StyleProfile",
]
