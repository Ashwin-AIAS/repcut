"""Usable footage: a clip's signals and scene boundaries -> scored segments.

Pure. Two things make a stretch unusable:

- **Dead space**: nothing moves and nobody speaks for at least
  ``DEAD_MIN_SECONDS`` (resting between sets, walking to the camera). Speech is
  never dead: a spoken cue is content.
- **Shake**: global jitter above ``SHAKE_LIMIT`` for at least
  ``SHAKE_MIN_SECONDS``. A pan is not shake (``signals`` removes it first).

What is left is split at scene boundaries - the detector's, corrected by the
person's boundary overrides (amendment 012 row 11) - and every piece at least
``MIN_SEGMENT_SECONDS`` long becomes a ``Segment`` with:

- **quality** 0-1: steadier and livelier is better;
- **energy** 0-1: motion and loudness on fixed scales, so two clips of one
  session compare (the planner ranks them anyway).
"""

from collections.abc import Sequence
from itertools import pairwise

from repcut.edit.signals import ClipSignals
from repcut.edit.types import BoundaryOverride, Segment

DEAD_MOTION = 1.2
DEAD_MIN_SECONDS = 1.0
SHAKE_LIMIT = 0.02
# Two windows: a hard cut makes one window of apparent jitter, real shake lasts.
SHAKE_MIN_SECONDS = 1.0
MIN_SEGMENT_SECONDS = 0.6

# Scales for energy: motion in 8-bit codes, loudness in dBFS from a quiet floor.
MOTION_CEILING = 12.0
LOUDNESS_FLOOR_DB = -50.0
LOUDNESS_SPAN_DB = 35.0


def boundaries(
    scene_starts: Sequence[float],
    duration: float,
    overrides: Sequence[BoundaryOverride],
    sha256: str,
) -> tuple[float, ...]:
    """Interior scene boundaries after the person's corrections, sorted."""
    points = {round(start, 3) for start in scene_starts if 0.0 < start < duration}
    for override in overrides:
        if override.sha256 != sha256:
            continue
        if override.action == "add" and 0.0 < override.seconds < duration:
            points.add(round(override.seconds, 3))
        elif override.action == "remove":
            nearest = min(points, key=lambda p: abs(p - override.seconds), default=None)
            if nearest is not None and abs(nearest - override.seconds) <= 0.5:
                points.discard(nearest)
    return tuple(sorted(points))


def _speaking(signals: ClipSignals, start: float, end: float) -> bool:
    return any(r.start < end and r.end > start for r in signals.speech or ())


def _runs(flags: Sequence[bool], minimum_windows: int) -> list[bool]:
    """``flags``, keeping only runs of True at least ``minimum_windows`` long."""
    out = [False] * len(flags)
    index = 0
    while index < len(flags):
        if not flags[index]:
            index += 1
            continue
        end = index
        while end < len(flags) and flags[end]:
            end += 1
        if end - index >= minimum_windows:
            out[index:end] = [True] * (end - index)
        index = end
    return out


def unusable_windows(signals: ClipSignals) -> list[bool]:
    width = signals.window_seconds
    count = len(signals.motion)
    dead = [
        signals.motion[i] < DEAD_MOTION and not _speaking(signals, i * width, (i + 1) * width)
        for i in range(count)
    ]
    shaky = [value > SHAKE_LIMIT for value in signals.shake]
    dead_runs = _runs(dead, max(1, round(DEAD_MIN_SECONDS / width)))
    shake_runs = _runs(shaky, max(1, round(SHAKE_MIN_SECONDS / width)))
    return [a or b for a, b in zip(dead_runs, shake_runs, strict=True)]


def _score(signals: ClipSignals, first: int, last: int) -> tuple[float, float]:
    motion = signals.motion[first:last]
    shake = signals.shake[first:last]
    loud = signals.audio_db[first:last] if len(signals.audio_db) >= last else ()
    mean_motion = sum(motion) / len(motion)
    mean_shake = sum(shake) / len(shake)
    mean_db = sum(loud) / len(loud) if loud else LOUDNESS_FLOOR_DB
    motion_part = min(1.0, mean_motion / MOTION_CEILING)
    loud_part = min(1.0, max(0.0, (mean_db - LOUDNESS_FLOOR_DB) / LOUDNESS_SPAN_DB))
    steadiness = 1.0 - min(1.0, mean_shake / SHAKE_LIMIT)
    quality = round(0.7 * steadiness + 0.3 * min(1.0, mean_motion / DEAD_MOTION / 2), 4)
    energy = round(0.65 * motion_part + 0.35 * loud_part, 4)
    return quality, energy


def usable_segments(
    sha256: str,
    signals: ClipSignals,
    scene_starts: Sequence[float],
    overrides: Sequence[BoundaryOverride] = (),
) -> tuple[Segment, ...]:
    """The clip's usable segments, in source order."""
    width = signals.window_seconds
    bad = unusable_windows(signals)
    cuts = boundaries(scene_starts, signals.duration_seconds, overrides, sha256)
    edges = [0.0, *cuts, signals.duration_seconds]
    segments: list[Segment] = []
    for scene_index, (scene_start, scene_end) in enumerate(pairwise(edges)):
        window = int(scene_start / width)
        last_window = min(len(bad), int((scene_end - 1e-9) / width) + 1)
        while window < last_window:
            if bad[window]:
                window += 1
                continue
            run_end = window
            while run_end < last_window and not bad[run_end]:
                run_end += 1
            start = max(scene_start, window * width)
            end = min(scene_end, run_end * width)
            if end - start >= MIN_SEGMENT_SECONDS:
                quality, energy = _score(signals, window, run_end)
                segments.append(
                    Segment(
                        id=Segment.make_id(sha256, start, end),
                        sha256=sha256,
                        start=round(start, 3),
                        end=round(end, 3),
                        quality=quality,
                        energy=energy,
                        scene_index=scene_index,
                    )
                )
            window = run_end
    return tuple(segments)


__all__ = [
    "DEAD_MIN_SECONDS",
    "DEAD_MOTION",
    "MIN_SEGMENT_SECONDS",
    "SHAKE_LIMIT",
    "boundaries",
    "unusable_windows",
    "usable_segments",
]
