"""Hero-moment scoring: which usable segments hold the moment of the session.

Pure: the measurements the edit already has in, ranked moments out. Nothing
here looks at a frame or calls a model; it reads what analysis and the edit
signals measured (P4: no new data leaves the machine for this).

Four signals per segment, each 0-1, combined with fixed weights (v1
heuristics; Prompt 10's taste profile is where they may start to learn):

- **Movement peak** (``MOTION``): the segment's busiest half-second, against the
  clip's own 95th percentile, so a calm clip's hardest rep still counts.
- **Impact** (``IMPACT``): the sharpest jump in loudness inside the segment,
  in dB over the half-second before - a plate set down, a bar racked. Steady
  loud music is not a jump.
- **Scene context** (``CONTEXT``): what scene analysis saw - an exercise being
  performed scores, rest and setup do not, and its energy level adds to it.
  Without analysis this signal is neutral (0.5), never invented.
- **End of a set** (``END_OF_SET``): how late in a continuous run of movement
  the segment sits. The last reps before the rest are the hard ones (the
  grind, the lockout), so a run's final segment scores highest.

The peak instant is the busiest window (movement, then impact), so the slowed
stretch is centred on what happened rather than on the segment's middle. Every
moment carries the reasons that scored it, in words the person reads (P2: an
AI choice shows why).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

import numpy as np

from repcut.edit.signals import ClipSignals
from repcut.edit.types import Segment

DETECTOR_VERSION: Final = 1
TOP_N: Final = 3

MOTION: Final = 0.35
IMPACT: Final = 0.25
CONTEXT: Final = 0.2
END_OF_SET: Final = 0.2

# A loudness jump of this many dB in half a second counts fully as an impact.
IMPACT_FULL_DB: Final = 18.0
# Movement below this (8-bit codes) is stillness, the same scale ``segments`` uses.
STILL_MOTION: Final = 1.2
# A gap of stillness this long ends a set.
REST_SECONDS: Final = 1.5
# A signal must reach this to be named as a reason.
REASON_AT: Final = 0.6

_EXERCISE_WORDS = ("exercise", "lift", "set", "training", "workout")
_REST_WORDS = ("rest", "setup", "set-up", "talking", "b-roll", "transition")
_ENERGY = {"high": 1.0, "med": 0.6, "medium": 0.6, "low": 0.2}


@dataclass(frozen=True, slots=True)
class SceneNote:
    """What scene analysis said about one scene of a clip, or None where it said nothing."""

    sha256: str
    start: float
    end: float
    content_type: str | None = None
    exercise_guess: str | None = None
    energy_level: str | None = None


@dataclass(frozen=True, slots=True)
class HeroMoment:
    """One ranked moment. ``peak`` is in the clip's own seconds."""

    segment_id: str
    sha256: str
    peak: float
    score: float
    rank: int
    reasons: tuple[str, ...]
    signals: Mapping[str, float]

    @property
    def reason(self) -> str:
        return "; ".join(self.reasons)

    def to_json(self) -> dict[str, object]:
        return {
            "segment_id": self.segment_id,
            "sha256": self.sha256,
            "peak": self.peak,
            "score": self.score,
            "rank": self.rank,
            "reasons": list(self.reasons),
            "signals": dict(self.signals),
        }


def _windows(signals: ClipSignals, segment: Segment) -> range:
    width = signals.window_seconds
    first = max(0, int(segment.start / width))
    last = min(len(signals.motion), max(first + 1, int(np.ceil(segment.end / width))))
    return range(first, last)


def _motion_ceiling(signals: ClipSignals) -> float:
    if not signals.motion:
        return 1.0
    return max(float(np.percentile(signals.motion, 95)), STILL_MOTION * 2)


def _jumps(signals: ClipSignals) -> list[float]:
    db = signals.audio_db
    return [0.0] + [max(0.0, db[i] - db[i - 1]) for i in range(1, len(db))]


def _context(notes: Sequence[SceneNote], segment: Segment) -> tuple[float, str | None]:
    middle = (segment.start + segment.end) / 2
    note = next(
        (n for n in notes if n.sha256 == segment.sha256 and n.start <= middle < n.end), None
    )
    if note is None or (note.content_type is None and note.energy_level is None):
        return 0.5, None
    kind = (note.content_type or "").casefold()
    if any(word in kind for word in _REST_WORDS):
        base = 0.0
    elif any(word in kind for word in _EXERCISE_WORDS) or note.exercise_guess:
        base = 0.7
    else:
        base = 0.4
    energy = _ENERGY.get((note.energy_level or "").strip().casefold(), 0.5)
    score = min(1.0, base + 0.3 * energy)
    named = (note.exercise_guess or "").strip()
    label = f"scene analysis saw {named[:60]}" if named and base > 0 else None
    return score, label


def _active_runs(signals: ClipSignals) -> list[tuple[float, float]]:
    """Continuous stretches of movement, split by rests of ``REST_SECONDS`` or more."""
    width = signals.window_seconds
    moving = [value >= STILL_MOTION for value in signals.motion]
    rest_windows = max(1, round(REST_SECONDS / width))
    runs: list[tuple[float, float]] = []
    start: int | None = None
    still = 0
    for index, flag in enumerate(moving):
        if flag:
            if start is None:
                start = index
            still = 0
        elif start is not None:
            still += 1
            if still >= rest_windows:
                runs.append((start * width, (index - still + 1) * width))
                start, still = None, 0
    if start is not None:
        runs.append((start * width, (len(moving) - still) * width))
    return runs


def _end_of_set(runs: Sequence[tuple[float, float]], segment: Segment) -> float:
    middle = (segment.start + segment.end) / 2
    for start, end in runs:
        if start <= middle <= end and end - start > 0:
            through = (segment.end - start) / (end - start)
            return float(min(1.0, max(0.0, through)))
    return 0.0


def score_segment(
    segment: Segment, signals: ClipSignals, notes: Sequence[SceneNote]
) -> tuple[float, float, dict[str, float], list[str]]:
    """(score, peak seconds, per-signal values, reasons) for one segment."""
    windows = _windows(signals, segment)
    width = signals.window_seconds
    ceiling = _motion_ceiling(signals)
    jumps = _jumps(signals)
    motion_values = [signals.motion[i] for i in windows] or [0.0]
    jump_values = [jumps[i] if i < len(jumps) else 0.0 for i in windows] or [0.0]
    motion = float(min(1.0, max(motion_values) / ceiling))
    impact = float(min(1.0, max(jump_values) / IMPACT_FULL_DB))
    context, context_reason = _context(notes, segment)
    end_of_set = _end_of_set(_active_runs(signals), segment)

    values = {
        "motion": round(motion, 4),
        "impact": round(impact, 4),
        "context": round(context, 4),
        "end_of_set": round(end_of_set, 4),
    }
    score = MOTION * motion + IMPACT * impact + CONTEXT * context + END_OF_SET * end_of_set

    combined = [
        signals.motion[i] / ceiling + (jumps[i] / IMPACT_FULL_DB if i < len(jumps) else 0.0)
        for i in windows
    ]
    if combined:
        best = windows[int(np.argmax(combined))]
        peak = (best + 0.5) * width
    else:
        peak = (segment.start + segment.end) / 2
    peak = round(min(segment.end, max(segment.start, peak)), 3)

    reasons: list[str] = []
    if motion >= REASON_AT:
        reasons.append("the most movement in this clip")
    if impact >= REASON_AT:
        reasons.append("a sharp impact sound (a plate or bar landing)")
    if end_of_set >= 0.8:
        reasons.append("the last reps before a rest")
    if context_reason is not None and context >= REASON_AT:
        reasons.append(context_reason)
    if not reasons:
        reasons.append("the strongest mix of movement and sound in this session")
    return round(score, 4), peak, values, reasons


def detect(
    segments: Sequence[Segment],
    signals: Mapping[str, ClipSignals],
    notes: Sequence[SceneNote] = (),
    *,
    top_n: int = TOP_N,
) -> tuple[HeroMoment, ...]:
    """The top ``top_n`` moments across the session, best first. Deterministic."""
    scored: list[tuple[float, str, Segment, float, dict[str, float], list[str]]] = []
    for segment in segments:
        clip = signals.get(segment.sha256)
        if clip is None or not clip.motion:
            continue
        score, peak, values, reasons = score_segment(segment, clip, notes)
        scored.append((score, segment.id, segment, peak, values, reasons))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return tuple(
        HeroMoment(
            segment_id=segment.id,
            sha256=segment.sha256,
            peak=peak,
            score=score,
            rank=rank,
            reasons=tuple(reasons),
            signals=values,
        )
        for rank, (score, _, segment, peak, values, reasons) in enumerate(scored[:top_n], start=1)
    )


__all__ = [
    "DETECTOR_VERSION",
    "TOP_N",
    "HeroMoment",
    "SceneNote",
    "detect",
    "score_segment",
]
