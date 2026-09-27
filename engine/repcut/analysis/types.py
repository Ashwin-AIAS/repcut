"""Shared dataclasses for the analysis package.

One definition, imported by every module that produces or consumes it -
``scenes.py``, ``sampler.py``, ``motion.py`` and (for the parts it needs)
``cache.py`` - so a scene boundary or an energy measurement cannot drift into
two slightly different shapes across the package.

Both are frozen and slot-based like every other value object in this codebase
(``media/artifacts.py``'s recipes, ``media/metadata.py``'s ``MediaProperties``):
immutable because a scene boundary computed once must not be mutated by a
later stage that only meant to read it, and slotted because there is no reason
for either to carry attributes nobody declared.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SceneBoundary:
    """One detected shot boundary, timed against the SOURCE's own timebase.

    Amendment 008 resolution 4 is the reason this carries two representations
    of the same boundary rather than one: ``start_seconds``/``end_seconds`` are
    authoritative and portable between the CFR proxy detection reads and the
    (possibly VFR) source frame extraction reads, because seconds mean the same
    wall-clock instant in both files - a frame *number* would not, since the
    two files disagree about how many frames occupy that instant. Detection
    happens on the proxy but must be interpreted against the source: the proxy
    preserves the source's full duration, only the frame *rate* is normalized,
    so the proxy's own seconds values map directly onto the source's timeline
    with nothing to re-derive.

    ``start_frame_source``/``end_frame_source`` are ``round(seconds *
    fps_source)`` - a frame handle for callers that need one, derived from the
    authoritative seconds value rather than measured independently, so the two
    can never disagree about the same boundary.
    """

    sequence_index: int
    start_seconds: float
    end_seconds: float
    start_frame_source: int
    end_frame_source: int

    def __post_init__(self) -> None:
        """The same two invariants ``db.models.Scene`` enforces with a CHECK.

        Validated here too, not only at the database, because a boundary this
        broken is a bug in the detector that produced it - ``scenes.py`` - and
        the failure should name that bug at the point it is created, not three
        layers later as an opaque CHECK violation from a session flush nothing
        in this call stack is anywhere near.
        """
        if self.end_seconds <= self.start_seconds:
            raise ValueError("a scene must have a positive duration")
        if self.sequence_index < 0:
            raise ValueError("sequence_index must not be negative")


@dataclass(frozen=True, slots=True)
class EnergyMeasurement:
    """One scene's motion and audio energy, combined into one comparable score.

    ``motion_energy`` and ``audio_energy`` are each non-negative and in their
    own native units (``motion.py`` documents both); ``energy_score`` is the
    0-100 combination ``db.models.Scene.energy_score`` stores, so the UI's
    sparkline has one number to plot without knowing how it was built.
    """

    motion_energy: float
    audio_energy: float
    energy_score: float

    def __post_init__(self) -> None:
        """Mirrors the bounds ``motion.py``'s combination formula promises."""
        if self.motion_energy < 0:
            raise ValueError("motion_energy must not be negative")
        if self.audio_energy < 0:
            raise ValueError("audio_energy must not be negative")
        if not 0.0 <= self.energy_score <= 100.0:
            raise ValueError("energy_score must be between 0 and 100")


__all__ = ["EnergyMeasurement", "SceneBoundary"]
