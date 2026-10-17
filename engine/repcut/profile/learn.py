"""Learn a style profile from taste events. Pure: rows in, ``StyleProfile`` out.

Every rule here is a count a person could check by hand, which is the point:
the profile must be able to say *why* (guide constraint), so it is built from
tallies, not from a fitted model.

- **theme**: each time a theme was chosen over the suggested one, +1 for the
  chosen theme and -0.5 for the one passed over. Prior = 0.08 x tally, bounded.
- **intensity**: the mean of (chosen - suggested) intensity.
- **pacing**: -1..+1 signals: a theme chosen over the suggestion whose pace is
  faster or slower (its pace energy difference, doubled), and slow motion
  turned off (+0.5, faster) or asked for (-0.5, slower).
- **caption_style**: like theme, over caption style choices.
- **music_energy**: the mean of (chosen track energy - suggested track energy).

Each item is shrunk towards zero by n / (n + 3) and becomes **active** only at
``MIN_EVENTS`` overrides: one click is a choice, not a style. Events older
than an item's last reset are ignored for that item.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from repcut.db.models import TasteEventKind
from repcut.profile.types import (
    CAPTION_PRIOR_MAX,
    INTENSITY_BIAS_MAX,
    MUSIC_ENERGY_MAX,
    PACING_BIAS_MAX,
    THEME_PRIOR_MAX,
    Evidence,
    ProfileItem,
    Resets,
    StyleProfile,
)

MIN_EVENTS = 3
SHRINK = 3.0
THEME_STEP = 0.08
CAPTION_STEP = 0.25


@dataclass(frozen=True, slots=True)
class EventRow:
    """The fields of one ``taste_events`` row the profile reads."""

    kind: str
    created_at: datetime
    recommended: Mapping[str, Any]
    chosen: Mapping[str, Any]
    features: Mapping[str, Any]

    @classmethod
    def from_json(
        cls, kind: str, created_at: datetime, recommended: str, chosen: str, features: str
    ) -> EventRow:
        """A row as stored: three JSON objects. Anything else reads as empty."""
        return cls(kind, created_at, _obj(recommended), _obj(chosen), _obj(features))


def _obj(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


@dataclass(frozen=True, slots=True)
class LearnContext:
    """What the tallies need to know about the things chosen."""

    theme_names: Mapping[str, str] = field(default_factory=dict)
    track_energy: Mapping[str, float] = field(default_factory=dict)
    caption_names: Mapping[str, str] = field(default_factory=dict)


def _shrink(n: int) -> float:
    return n / (n + SHRINK)


def _clamp(value: float, bound: float) -> float:
    return round(min(bound, max(-bound, value)), 4)


def _str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _num(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _times(n: int) -> str:
    return "once" if n == 1 else "twice" if n == 2 else f"{n} times"


def _since(rows: Iterable[EventRow], resets: Resets, item: ProfileItem) -> list[EventRow]:
    cut = resets.items.get(item)
    return [r for r in rows if cut is None or r.created_at > cut]


def _swaps(rows: Iterable[EventRow], kind: str) -> list[tuple[str, str | None]]:
    """(chosen, suggested) for every override of ``kind`` that changed the value."""
    out: list[tuple[str, str | None]] = []
    for row in rows:
        if row.kind != kind:
            continue
        chosen = _str(row.chosen.get("value"))
        suggested = _str(row.recommended.get("value"))
        if chosen is not None and chosen != suggested:
            out.append((chosen, suggested))
    return out


def _tally(swaps: list[tuple[str, str | None]]) -> dict[str, float]:
    tally: dict[str, float] = {}
    for chosen, suggested in swaps:
        tally[chosen] = tally.get(chosen, 0.0) + 1.0
        if suggested is not None:
            tally[suggested] = tally.get(suggested, 0.0) - 0.5
    return tally


def _theme(rows: list[EventRow], ctx: LearnContext) -> tuple[dict[str, float], Evidence]:
    swaps = _swaps(rows, TasteEventKind.THEME.value)
    n = len(swaps)
    tally = _tally(swaps)
    prior = {t: _clamp(THEME_STEP * v * _shrink(n), THEME_PRIOR_MAX) for t, v in tally.items()}
    prior = {t: v for t, v in prior.items() if v != 0.0}
    if not swaps:
        return {}, Evidence(events=0, why="No theme changes yet.")
    picked = Counter(c for c, _ in swaps).most_common(1)[0]
    name = ctx.theme_names.get(picked[0], picked[0])
    why = (
        f"You chose {name} over the suggested theme {_times(picked[1])} "
        f"(of {n} theme change{'' if n == 1 else 's'}); it now ranks higher in theme suggestions."
    )
    return prior, Evidence(events=n, why=why, active=n >= MIN_EVENTS)


def _intensity(rows: list[EventRow]) -> tuple[float, Evidence]:
    deltas = []
    for row in rows:
        if row.kind != TasteEventKind.INTENSITY.value:
            continue
        chosen, suggested = _num(row.chosen.get("value")), _num(row.recommended.get("value"))
        if chosen is not None and suggested is not None and chosen != suggested:
            deltas.append(chosen - suggested)
    n = len(deltas)
    if not n:
        return 0.0, Evidence(events=0, why="No intensity changes yet.")
    bias = _clamp(sum(deltas) / n * _shrink(n), INTENSITY_BIAS_MAX)
    way = "stronger" if bias > 0 else "lighter"
    why = (
        f"You set the grade {way} than suggested in {n} of your changes; "
        f"suggested intensity moves {abs(bias):.0f} points {'up' if bias > 0 else 'down'}."
    )
    return bias, Evidence(events=n, why=why, active=n >= MIN_EVENTS and round(bias) != 0)


def _pacing(rows: list[EventRow]) -> tuple[float, Evidence]:
    """Only overrides that are about pace itself.

    A theme change is not one: a person who picks Moody for its colour is not
    asking for Moody's long shots, so a theme's own pace never counts here.
    """
    signals: list[float] = []
    matched = slow_off = slow_on = 0
    for row in rows:
        if row.kind == TasteEventKind.SLOWMO_OFF.value:
            signals.append(0.5)
            slow_off += 1
        elif row.kind == TasteEventKind.SLOWMO_ON.value:
            signals.append(-0.5)
            slow_on += 1
        elif (
            row.kind == TasteEventKind.REFERENCE_MATCHED.value
            and row.features.get("part") == "pacing"
        ):
            pace = row.chosen.get("value")
            energy = _num(pace.get("energy")) if isinstance(pace, dict) else None
            if energy is not None:
                signals.append(max(-1.0, min(1.0, 2.0 * (energy - 0.5))))
                matched += 1
    n = len(signals)
    if not n:
        return 0.0, Evidence(events=0, why="No pacing signals yet.")
    bias = _clamp(sum(signals) / n * _shrink(n), PACING_BIAS_MAX)
    parts = []
    if slow_off:
        parts.append(f"slow motion turned off {_times(slow_off)}")
    if slow_on:
        parts.append(f"slow motion asked for {_times(slow_on)}")
    if matched:
        parts.append(f"a reference's pacing matched {_times(matched)}")
    way = "faster" if bias > 0 else "slower"
    change = f"{abs(round(40 * bias))}% {'shorter' if bias > 0 else 'longer'}"
    why = f"From {', '.join(parts)}: edits are cut {way} (shots about {change})."
    return bias, Evidence(events=n, why=why, active=n >= MIN_EVENTS and abs(bias) >= 0.05)


def _captions(rows: list[EventRow], ctx: LearnContext) -> tuple[dict[str, float], Evidence]:
    swaps = _swaps(rows, TasteEventKind.CAPTION_STYLE.value)
    n = len(swaps)
    if not n:
        return {}, Evidence(events=0, why="No caption style changes yet.")
    tally = _tally(swaps)
    prior = {s: _clamp(CAPTION_STEP * v * _shrink(n), CAPTION_PRIOR_MAX) for s, v in tally.items()}
    prior = {s: v for s, v in prior.items() if v != 0.0}
    picked = Counter(c for c, _ in swaps).most_common(1)[0]
    name = ctx.caption_names.get(picked[0], picked[0])
    why = f"You picked the {name} caption style {_times(picked[1])}; new edits start with it."
    return prior, Evidence(events=n, why=why, active=n >= MIN_EVENTS)


def _music(rows: list[EventRow], ctx: LearnContext) -> tuple[float, Evidence]:
    deltas = []
    for chosen, suggested in _swaps(rows, TasteEventKind.MUSIC_TRACK.value):
        a = ctx.track_energy.get(chosen)
        b = ctx.track_energy.get(suggested) if suggested else None
        if a is not None and b is not None:
            deltas.append(a - b)
    n = len(deltas)
    if not n:
        return 0.0, Evidence(events=0, why="No music changes yet.")
    shift = _clamp(sum(deltas) / n * _shrink(n), MUSIC_ENERGY_MAX)
    way = "more energetic" if shift > 0 else "calmer"
    why = (
        f"You swapped the suggested track for a {way} one {_times(n)}; "
        f"music suggestions lean {way}."
    )
    return shift, Evidence(events=n, why=why, active=n >= MIN_EVENTS and abs(shift) >= 0.02)


def learn(
    rows: Iterable[EventRow], ctx: LearnContext | None = None, resets: Resets | None = None
) -> StyleProfile:
    """The profile these overrides add up to. No rows: ``StyleProfile()`` exactly."""
    ctx = ctx or LearnContext()
    resets = resets or Resets()
    ordered = sorted(rows, key=lambda r: r.created_at)
    if not ordered:
        return StyleProfile()
    theme, e_theme = _theme(_since(ordered, resets, ProfileItem.THEME), ctx)
    intensity, e_int = _intensity(_since(ordered, resets, ProfileItem.INTENSITY))
    pacing, e_pace = _pacing(_since(ordered, resets, ProfileItem.PACING))
    captions, e_cap = _captions(_since(ordered, resets, ProfileItem.CAPTION_STYLE), ctx)
    music, e_music = _music(_since(ordered, resets, ProfileItem.MUSIC_ENERGY), ctx)
    return StyleProfile(
        theme=theme,
        intensity=intensity,
        pacing=pacing,
        caption_style=captions,
        music_energy=music,
        evidence={
            ProfileItem.THEME: e_theme,
            ProfileItem.INTENSITY: e_int,
            ProfileItem.PACING: e_pace,
            ProfileItem.CAPTION_STYLE: e_cap,
            ProfileItem.MUSIC_ENERGY: e_music,
        },
    )


__all__ = ["MIN_EVENTS", "EventRow", "LearnContext", "learn"]
