"""A reference clip's rhythm: how long its shots are, how they sit on its beat, -> planner numbers.

Cuts come from the scene detection every clip already gets (Prompt 03's
pipeline: a reference is analysed exactly like footage), the beat from the
same analysis the music library uses. The output is a ``PaceSpec``, the
numbers a theme already carries for the planner, so matching a reference's
pacing changes the planner's inputs and nothing else. The planner never
invents a cut to reach the reference's rate: with too little footage it does
what it can, and ``pacing_gap`` says by how much it fell short.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise

import numpy as np

from repcut.edit.planner import PACING_BPM_HIGH, PACING_BPM_LOW, SLOWMO_EVERY_SECONDS
from repcut.grading.theme import PaceSpec

RHYTHM_VERSION = 1

# A cut within this of a beat counts as on the beat (the planner's own sync bound).
BEAT_TOLERANCE_SECONDS = 0.04
# Cuts per 10 s mapped onto the planner's 0-1 energy: slower than LOW is calm,
# faster than HIGH is as fast as a theme goes.
CUTS_PER_10S_LOW = 1.5
CUTS_PER_10S_HIGH = 10.0
# A shot shorter than this is a detector flash, not an editor's cut.
MIN_SHOT_SECONDS = 0.15
# Share of the reference that plays slowed, mapped to how often the planner may
# place a slow shot.
SLOWMO_SHARE_HIGH = 0.2
SLOWMO_SHARE_LOW = 0.02
SLOWMO_EVERY_FREQUENT = 8.0
SLOWMO_EVERY_RARE = 30.0
# The gap between the reference's pacing and the edit's is reported past this.
GAP_RATIO = 1.25


@dataclass(frozen=True, slots=True)
class PacingProfile:
    duration_seconds: float
    shot_count: int
    shot_median: float
    shot_p25: float
    shot_p75: float
    cuts_per_10s: float
    bpm: float | None
    # Share of cuts within BEAT_TOLERANCE_SECONDS of a beat; None without a beat.
    beat_alignment: float | None
    # Share of the duration that looks slowed (frame repeats); None if unmeasured.
    slowmo_share: float | None

    def as_json(self) -> dict[str, object]:
        return {
            "duration_seconds": round(self.duration_seconds, 3),
            "shot_count": self.shot_count,
            "shot_median": round(self.shot_median, 3),
            "shot_p25": round(self.shot_p25, 3),
            "shot_p75": round(self.shot_p75, 3),
            "cuts_per_10s": round(self.cuts_per_10s, 3),
            "bpm": None if self.bpm is None else round(self.bpm, 2),
            "beat_alignment": None
            if self.beat_alignment is None
            else round(self.beat_alignment, 3),
            "slowmo_share": None if self.slowmo_share is None else round(self.slowmo_share, 3),
            "version": RHYTHM_VERSION,
        }

    @classmethod
    def from_json(cls, data: dict[str, object]) -> PacingProfile:
        def opt(key: str) -> float | None:
            value = data.get(key)
            return None if value is None else float(value)  # type: ignore[arg-type]

        return cls(
            duration_seconds=float(data["duration_seconds"]),  # type: ignore[arg-type]
            shot_count=int(data["shot_count"]),  # type: ignore[call-overload]
            shot_median=float(data["shot_median"]),  # type: ignore[arg-type]
            shot_p25=float(data["shot_p25"]),  # type: ignore[arg-type]
            shot_p75=float(data["shot_p75"]),  # type: ignore[arg-type]
            cuts_per_10s=float(data["cuts_per_10s"]),  # type: ignore[arg-type]
            bpm=opt("bpm"),
            beat_alignment=opt("beat_alignment"),
            slowmo_share=opt("slowmo_share"),
        )


def shot_lengths(cuts: Sequence[float], duration: float) -> list[float]:
    """Shot lengths from the cut times inside (0, duration); flashes merged into the next shot."""
    if duration <= 0:
        raise ValueError("a reference has a positive duration")
    edges = [0.0, *sorted(c for c in cuts if 0.0 < c < duration), duration]
    lengths: list[float] = []
    carry = 0.0
    for start, end in pairwise(edges):
        length = end - start + carry
        if length < MIN_SHOT_SECONDS:
            carry = length
            continue
        lengths.append(length)
        carry = 0.0
    if carry and lengths:
        lengths[-1] += carry
    return lengths or [duration]


def beat_alignment(cuts: Sequence[float], beats: Sequence[float]) -> float | None:
    inner = [c for c in cuts if c > 0]
    if not inner or len(beats) < 2:
        return None
    grid = np.asarray(sorted(beats))
    hits = 0
    for cut in inner:
        index = int(np.searchsorted(grid, cut))
        near = min(abs(cut - grid[i]) for i in (index - 1, index) if 0 <= i < len(grid))
        hits += near <= BEAT_TOLERANCE_SECONDS
    return hits / len(inner)


def profile(
    cuts: Sequence[float],
    duration: float,
    *,
    beats: Sequence[float] = (),
    bpm: float | None = None,
    slowmo_share: float | None = None,
) -> PacingProfile:
    lengths = np.asarray(shot_lengths(cuts, duration))
    inner = [c for c in cuts if 0.0 < c < duration]
    return PacingProfile(
        duration_seconds=duration,
        shot_count=len(lengths),
        shot_median=float(np.median(lengths)),
        shot_p25=float(np.quantile(lengths, 0.25)),
        shot_p75=float(np.quantile(lengths, 0.75)),
        cuts_per_10s=10.0 * (len(lengths) - 1) / duration,
        bpm=bpm,
        beat_alignment=beat_alignment(inner, beats),
        slowmo_share=slowmo_share,
    )


def energy_of(cuts_per_10s: float) -> float:
    span = CUTS_PER_10S_HIGH - CUTS_PER_10S_LOW
    return float(min(max((cuts_per_10s - CUTS_PER_10S_LOW) / span, 0.0), 1.0))


def to_pace(p: PacingProfile) -> PaceSpec:
    """The planner numbers that cut like the reference: shot lengths in beats of its tempo."""
    energy = energy_of(p.cuts_per_10s)
    bpm = p.bpm or (PACING_BPM_LOW + (PACING_BPM_HIGH - PACING_BPM_LOW) * energy)
    period = 60.0 / bpm

    def beats(seconds: float) -> int:
        return int(min(max(round(seconds / period), 1), 32))

    calm = beats(p.shot_p75)
    peak = min(beats(p.shot_p25), calm)
    if p.slowmo_share is None:
        every = SLOWMO_EVERY_SECONDS
    elif p.slowmo_share >= SLOWMO_SHARE_HIGH:
        every = SLOWMO_EVERY_FREQUENT
    elif p.slowmo_share <= SLOWMO_SHARE_LOW:
        every = SLOWMO_EVERY_RARE
    else:
        every = SLOWMO_EVERY_SECONDS
    return PaceSpec(
        energy=round(energy, 3), calm_beats=calm, peak_beats=peak, slowmo_every_seconds=every
    )


def pacing_gap(p: PacingProfile, achieved_shot_lengths: Sequence[float]) -> str | None:
    """A sentence when the edit cuts much slower than the reference, else None. Never a fix."""
    if not achieved_shot_lengths:
        return None
    achieved = float(np.median(achieved_shot_lengths))
    if achieved <= p.shot_median * GAP_RATIO:
        return None
    return (
        f"the reference cuts about every {p.shot_median:.1f} s; your usable footage "
        f"supports shots of about {achieved:.1f} s here, so this edit cuts slower"
    )


def duplicate_share(diffs: Sequence[float], threshold: float = 0.6) -> float:
    """Share of consecutive frame differences (mean abs, 0-255) that are repeats."""
    if not diffs:
        return 0.0
    values = np.asarray(diffs)
    return float((values < threshold).mean())


__all__ = [
    "BEAT_TOLERANCE_SECONDS",
    "RHYTHM_VERSION",
    "PacingProfile",
    "beat_alignment",
    "duplicate_share",
    "energy_of",
    "pacing_gap",
    "profile",
    "shot_lengths",
    "to_pace",
]
