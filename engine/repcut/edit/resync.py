"""P2's hard technical meaning: an override re-plans everything it touches.

An override is one small value (``Override``). ``apply`` folds it into the
standing ``Decisions`` - a pure function, so the decisions are the whole record
of what a person chose - and ``replan`` runs the planner again from scratch
with those decisions. Nothing is patched in place: a re-plan that started from
the old plan could leave it internally inconsistent (a cut on the old track's
grid), which is the bug P2 names.

What survives, by construction:

- **Locked shots** are copied verbatim by the planner, so they are identical in
  the new plan, byte for byte, whatever else changed.
- **Removed and forced segments**, the target, the music choice and boundary
  corrections live in ``Decisions`` and are re-applied on every re-plan.

The time budget is the guide's: under 3 s for a full re-plan. The planner is
milliseconds; the budget is checked by the gate, not assumed.
"""

import time
from dataclasses import dataclass, replace
from typing import Literal

from repcut.edit.planner import PlanInputs, lock, plan
from repcut.edit.types import (
    BeatGrid,
    BoundaryOverride,
    Decisions,
    Pace,
    Plan,
    Segment,
    SourceRange,
)

RESYNC_BUDGET_SECONDS = 3.0

OverrideKind = Literal[
    "music_track",
    "music_off",
    "music_suggested",
    "target_duration",
    "cut_removed",
    "cut_locked",
    "cut_unlocked",
    "segment_forced",
    "segment_unforced",
    "regenerate",
    "scene_boundary",
]


class OverrideError(ValueError):
    """An override that names something the plan does not have. A fixed sentence."""


@dataclass(frozen=True, slots=True)
class Override:
    """One thing a person changed. Only the fields its ``kind`` uses are read."""

    kind: OverrideKind
    track_sha256: str | None = None
    target_seconds: float | None = None
    shot_id: str | None = None
    segment_id: str | None = None
    boundary: BoundaryOverride | None = None


def _shot(plan_: Plan, shot_id: str | None) -> str:
    if shot_id is None or not any(shot.id == shot_id for shot in plan_.shots):
        raise OverrideError("that cut is not in this edit")
    return shot_id


def apply(decisions: Decisions, current: Plan, override: Override) -> Decisions:
    """The decisions after ``override``. Pure."""
    kind = override.kind
    if kind == "music_track":
        if override.track_sha256 is None:
            raise OverrideError("no track was chosen")
        return replace(decisions, music="chosen", track_sha256=override.track_sha256)
    if kind == "music_off":
        return replace(decisions, music="off", track_sha256=None)
    if kind == "music_suggested":
        return replace(decisions, music="suggested", track_sha256=None)
    if kind == "target_duration":
        if override.target_seconds is None or not 5.0 <= override.target_seconds <= 180.0:
            raise OverrideError("an edit lasts between 5 and 180 seconds")
        return replace(decisions, target_seconds=override.target_seconds)
    if kind == "cut_removed":
        shot_id = _shot(current, override.shot_id)
        shot = next(s for s in current.shots if s.id == shot_id)
        # The shot's footage, not its whole segment: the rest of that take
        # stays available, and a locked shot from it stays where it is.
        removed = SourceRange(shot.sha256, shot.source_in, shot.source_out)
        return replace(
            decisions,
            removed_ranges=(*decisions.removed_ranges, removed),
            locked=tuple(s for s in decisions.locked if s.id != shot_id),
        )
    if kind == "cut_locked":
        shot_id = _shot(current, override.shot_id)
        if any(s.id == shot_id for s in decisions.locked):
            return decisions
        return replace(decisions, locked=(*decisions.locked, lock(current, shot_id)))
    if kind == "cut_unlocked":
        shot_id = _shot(current, override.shot_id)
        return replace(decisions, locked=tuple(s for s in decisions.locked if s.id != shot_id))
    if kind == "segment_forced":
        if override.segment_id is None:
            raise OverrideError("no footage was chosen")
        return replace(
            decisions,
            forced_segments=decisions.forced_segments | {override.segment_id},
            removed_segments=decisions.removed_segments - {override.segment_id},
        )
    if kind == "segment_unforced":
        if override.segment_id is None:
            raise OverrideError("no footage was chosen")
        return replace(decisions, forced_segments=decisions.forced_segments - {override.segment_id})
    if kind == "regenerate":
        return replace(decisions, seed=decisions.seed + 1)
    if kind == "scene_boundary":
        if override.boundary is None:
            raise OverrideError("no boundary was given")
        new = override.boundary
        # A second correction at the same place replaces the first.
        others = tuple(
            b
            for b in decisions.boundaries
            if not (b.sha256 == new.sha256 and abs(b.seconds - new.seconds) < 0.05)
        )
        return replace(decisions, boundaries=(*others, new))
    raise OverrideError("unknown override")


def replan(
    segments: tuple[Segment, ...], grid: BeatGrid | None, pace: Pace, decisions: Decisions
) -> Plan:
    """A fresh plan for these decisions. ``grid`` is None when the music is off."""
    return plan(
        PlanInputs(
            segments=segments,
            grid=grid,
            target_seconds=decisions.target_seconds,
            pace=pace,
            seed=decisions.seed,
            locked=decisions.locked,
            removed=decisions.removed_segments,
            forced=decisions.forced_segments,
            removed_ranges=decisions.removed_ranges,
        )
    )


@dataclass(frozen=True, slots=True)
class Resynced:
    decisions: Decisions
    plan: Plan
    seconds: float


def timed_replan(
    segments: tuple[Segment, ...], grid: BeatGrid | None, pace: Pace, decisions: Decisions
) -> Resynced:
    started = time.perf_counter()
    new_plan = replan(segments, grid, pace, decisions)
    return Resynced(decisions, new_plan, time.perf_counter() - started)


__all__ = [
    "RESYNC_BUDGET_SECONDS",
    "Override",
    "OverrideError",
    "OverrideKind",
    "Resynced",
    "apply",
    "replan",
    "timed_replan",
]
