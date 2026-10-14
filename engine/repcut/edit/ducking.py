"""Music under speech: a gain envelope from speech regions, rendered by FFmpeg.

Speech regions come from ``vad`` (clip seconds) and are mapped through the plan
onto the timeline: a region counts where a shot shows that part of its clip.
The envelope is a sum of trapezoids in dB, one per merged region:

- **duck** under speech by ``DUCK_DB`` (the gate asks for at least 8 dB; the
  skill's target is 9; this leaves margin), with a ``ATTACK_SECONDS`` ramp in
  and ``RELEASE_SECONDS`` out. Abrupt ducking is audible as a pump.
- **swell** on action: shots whose footage is in the top energy band and has
  no speech get ``SWELL_DB`` over a base that sits ``BASE_DB`` below unity.

Overlapping regions are merged first, so the trapezoids never add up past one
duck. The envelope is numbers only; ``ffmpeg_builder.build_music_stem`` turns
it into the ``volume`` filter and refuses anything else.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from repcut.edit.types import Plan
from repcut.edit.vad import SpeechRegion

DUCK_DB = -12.0
SWELL_DB = 3.0
BASE_DB = -3.0
ATTACK_SECONDS = 0.15
RELEASE_SECONDS = 0.4
SWELL_RAMP_SECONDS = 0.3
SWELL_ENERGY = 0.75


@dataclass(frozen=True, slots=True)
class Ramp:
    """``gain_db`` held over [start, end], ramped in over ``rise`` and out over ``fall``."""

    start: float
    end: float
    gain_db: float
    rise: float
    fall: float


def merge(regions: Iterable[tuple[float, float]], gap: float = 0.0) -> list[tuple[float, float]]:
    merged: list[list[float]] = []
    for start, end in sorted(regions):
        if merged and start <= merged[-1][1] + gap:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(a, b) for a, b in merged]


def timeline_speech(
    plan: Plan, speech: Mapping[str, tuple[SpeechRegion, ...] | None]
) -> list[tuple[float, float]]:
    """Speech regions on the edit's timeline, merged."""
    found: list[tuple[float, float]] = []
    for shot in plan.shots:
        # A slow-motion shot carries no sound of its own (export/render.py):
        # there is no speech under it to duck for.
        if shot.slow:
            continue
        for region in speech.get(shot.sha256) or ():
            start = max(region.start, shot.source_in)
            end = min(region.end, shot.source_out)
            if end > start:
                offset = shot.timeline_in - shot.source_in
                found.append((round(start + offset, 3), round(end + offset, 3)))
    # Release + attack: two regions closer than one full ramp are one duck.
    return merge(found, gap=ATTACK_SECONDS + RELEASE_SECONDS)


def envelope(
    plan: Plan, speech_on_timeline: list[tuple[float, float]], energy: Mapping[str, float]
) -> list[Ramp]:
    ramps = [
        Ramp(start, end, DUCK_DB - BASE_DB, ATTACK_SECONDS, RELEASE_SECONDS)
        for start, end in speech_on_timeline
    ]
    for shot in plan.shots:
        if energy.get(shot.segment_id, 0.0) < SWELL_ENERGY:
            continue
        clear = all(
            end <= shot.timeline_in or start >= shot.timeline_out
            for start, end in speech_on_timeline
        )
        if clear:
            ramps.append(
                Ramp(
                    shot.timeline_in,
                    shot.timeline_out,
                    SWELL_DB,
                    SWELL_RAMP_SECONDS,
                    SWELL_RAMP_SECONDS,
                )
            )
    return ramps


def _term(ramp: Ramp) -> str:
    """One trapezoid as an FFmpeg expression in ``t``: 0 outside, ``gain_db`` inside."""
    rise = f"(t-{ramp.start - ramp.rise:.3f})/{ramp.rise:.3f}"
    fall = f"({ramp.end + ramp.fall:.3f}-t)/{ramp.fall:.3f}"
    return f"{ramp.gain_db:.2f}*clip(min({rise},{fall}),0,1)"


def gain_expression(ramps: list[Ramp]) -> str:
    """The whole envelope, in dB, as a flat sum (no nesting, any length)."""
    return " + ".join([f"{BASE_DB:.2f}", *(_term(ramp) for ramp in ramps)])


def gain_at(ramps: list[Ramp], time: float) -> float:
    """The same envelope evaluated in Python, for tests and the UI's curve."""
    total = BASE_DB
    for ramp in ramps:
        rise = (time - (ramp.start - ramp.rise)) / ramp.rise
        fall = (ramp.end + ramp.fall - time) / ramp.fall
        total += ramp.gain_db * min(1.0, max(0.0, min(rise, fall)))
    return total


__all__ = [
    "ATTACK_SECONDS",
    "BASE_DB",
    "DUCK_DB",
    "RELEASE_SECONDS",
    "Ramp",
    "envelope",
    "gain_at",
    "gain_expression",
    "merge",
    "timeline_speech",
]
