"""The hook: the most striking moment of the session opens the edit as a teaser.

The first two seconds decide whether a viewer stays, so the edit can open on a
short teaser of its best moment before the edit proper begins. Pure: hero
moments and the signals the edit already measured in, ranked candidates out.

**Which moment.** Candidates are the session's hero moments (``highlights``),
never anything else - a hook is a hero shown early, not a new judgement. Each
gets a window of at most ``HOOK_MAX_SECONDS`` centred on its peak, inside its
own segment, and a score: the hero's score times its *visual impact*, how much
movement fills that window (sustained, against the clip's own busy level), so a
loud moment that looks still does not open the edit. The best is the AI's pick;
the top ``HOOK_CANDIDATES`` are offered to swap (P2).

**Where it goes** is the planner's job (``planner._place_hook``): at timeline
zero, on the beat grid, inside the duration budget.

The mode is the person's: ``on`` (the AI's pick), ``off`` (no teaser), or
``manual`` (a candidate they picked; if it stops being a candidate the AI's
pick is used and the edit says so).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Final

import numpy as np

from repcut.edit.signals import ClipSignals
from repcut.edit.types import HookMode, Segment
from repcut.highlights.detector import STILL_MOTION, HeroMoment

HOOK_MODES: Final = ("on", "off", "manual")

# A teaser longer than this is no longer a teaser; the guide's "first 2 seconds".
HOOK_MAX_SECONDS: Final = 2.0
HOOK_CANDIDATES: Final = 3
# Visual impact is never zero: a hero with little movement still ranks by its score.
IMPACT_FLOOR: Final = 0.1

MANUAL_GONE: Final = "the opening moment you picked is no longer a candidate; using the AI's pick"
LOCKED_OPENING: Final = "a locked cut opens this edit, so it has no teaser"
NO_ROOM_FOR_HOOK: Final = "the opening moment's footage is already used; this edit has no teaser"


@dataclass(frozen=True, slots=True)
class HookCandidate:
    """One possible opening: a hero moment's window, in its clip's own seconds."""

    segment_id: str
    sha256: str
    peak: float
    start: float
    end: float
    hero_score: float
    hero_rank: int
    visual_impact: float
    score: float
    rank: int
    reasons: tuple[str, ...]

    @property
    def duration(self) -> float:
        return self.end - self.start

    def to_json(self) -> dict[str, object]:
        return {
            "segment_id": self.segment_id,
            "sha256": self.sha256,
            "peak": self.peak,
            "start": self.start,
            "end": self.end,
            "hero_score": self.hero_score,
            "hero_rank": self.hero_rank,
            "visual_impact": self.visual_impact,
            "score": self.score,
            "rank": self.rank,
            "reasons": list(self.reasons),
        }


def window(segment: Segment, peak: float, seconds: float = HOOK_MAX_SECONDS) -> tuple[float, float]:
    """At most ``seconds`` of ``segment``, centred on ``peak`` and kept inside it."""
    length = min(seconds, segment.duration)
    start = min(max(peak - length / 2, segment.start), segment.end - length)
    return round(start, 3), round(start + length, 3)


def visual_impact(signals: ClipSignals, start: float, end: float) -> float:
    """How much movement fills ``[start, end)``: its mean against the clip's 95th percentile."""
    width = signals.window_seconds
    if not signals.motion or width <= 0:
        return IMPACT_FLOOR
    first = max(0, int(start / width))
    last = min(len(signals.motion), max(first + 1, int(np.ceil(end / width))))
    values = signals.motion[first:last]
    if not values:
        return IMPACT_FLOOR
    ceiling = max(float(np.percentile(signals.motion, 95)), STILL_MOTION * 2)
    return round(max(IMPACT_FLOOR, min(1.0, float(np.mean(values)) / ceiling)), 4)


def candidates(
    heroes: Sequence[HeroMoment],
    segments: Sequence[Segment],
    signals: Mapping[str, ClipSignals],
    *,
    top_n: int = HOOK_CANDIDATES,
) -> tuple[HookCandidate, ...]:
    """The top ``top_n`` openings, best first. Deterministic; only hero moments qualify."""
    by_id = {segment.id: segment for segment in segments}
    scored: list[tuple[float, int, str, HookCandidate]] = []
    for hero in heroes:
        segment = by_id.get(hero.segment_id)
        clip = signals.get(hero.sha256)
        if segment is None or clip is None:
            continue
        start, end = window(segment, hero.peak)
        impact = visual_impact(clip, start, end)
        score = round(hero.score * impact, 4)
        reasons = (*hero.reasons, f"movement fills {round(impact * 100)}% of the opening seconds")
        candidate = HookCandidate(
            segment_id=hero.segment_id,
            sha256=hero.sha256,
            peak=hero.peak,
            start=start,
            end=end,
            hero_score=hero.score,
            hero_rank=hero.rank,
            visual_impact=impact,
            score=score,
            rank=0,
            reasons=reasons,
        )
        scored.append((score, hero.rank, hero.segment_id, candidate))
    scored.sort(key=lambda item: (-item[0], item[1], item[2]))
    return tuple(
        replace(candidate, rank=rank)
        for rank, (_, _, _, candidate) in enumerate(scored[:top_n], start=1)
    )


def choose(
    options: Sequence[HookCandidate], mode: HookMode, chosen: str | None
) -> tuple[HookCandidate | None, str | None]:
    """(the opening to use, a warning). ``chosen`` is a segment id, read in ``manual`` mode."""
    if mode == "off" or not options:
        return None, None
    if mode == "manual":
        picked = next((c for c in options if c.segment_id == chosen), None)
        if picked is not None:
            return picked, None
        return options[0], MANUAL_GONE
    return options[0], None


__all__ = [
    "HOOK_CANDIDATES",
    "HOOK_MAX_SECONDS",
    "HOOK_MODES",
    "LOCKED_OPENING",
    "MANUAL_GONE",
    "NO_ROOM_FOR_HOOK",
    "HookCandidate",
    "HookMode",
    "candidates",
    "choose",
    "visual_impact",
    "window",
]
