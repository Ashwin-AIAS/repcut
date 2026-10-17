"""Set captions: "Back squat · 8 reps · 100 kg", suggested, then confirmed by the person.

Three parts, each honest about where it came from:
- reps: the counter's suggestion (``gym.reps``), editable;
- exercise: the scene analysis's guess (one sampled frame per scene, P4), editable;
- weight: ONE tap per set by the person. Plates are never read off the
  picture - the v1 boundary is stated, not hidden.

Only a confirmed set becomes a caption, and only on the shots cut from its
segment, at the top of the frame so it never covers the speech captions.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from repcut.captions.ass import Event, escape_text
from repcut.edit.types import Plan, Shot

SET_CAPTION_SECONDS = 3.0
_TOP = "{\\an8}"


@dataclass(frozen=True, slots=True)
class SetCaption:
    segment_id: str
    reps: int | None
    exercise: str | None
    weight: float | None
    weight_unit: str = "kg"

    def text(self) -> str:
        parts: list[str] = []
        if self.exercise:
            parts.append(self.exercise.strip()[:60])
        if self.reps is not None:
            parts.append(f"{self.reps} rep{'s' if self.reps != 1 else ''}")
        if self.weight is not None:
            parts.append(f"{self.weight:g} {self.weight_unit}")
        return " · ".join(parts)


def suggested_text(reps: int | None, exercise: str | None) -> str:
    """What the caption editor shows before the person confirms anything."""
    return SetCaption("", reps, exercise, None).text()


def set_events(plan: Plan, confirmed: Sequence[SetCaption], uppercase: bool = False) -> list[Event]:
    """One event per confirmed set, on the first shot cut from its segment.

    A set whose segment is not in the edit at all has nowhere to go and is not shown.
    """
    by_segment = {c.segment_id: c for c in confirmed if c.text()}
    out: list[Event] = []
    first: dict[str, Shot] = {}
    for shot in sorted(plan.shots, key=lambda s: (s.hook, s.timeline_in)):
        # The edit proper first; the opening teaser only when the set is shown nowhere else.
        if shot.segment_id in by_segment and shot.segment_id not in first:
            first[shot.segment_id] = shot
    for segment_id, shot in sorted(first.items(), key=lambda kv: kv[1].timeline_in):
        caption = by_segment[segment_id]
        end = min(shot.timeline_out, shot.timeline_in + SET_CAPTION_SECONDS)
        text = caption.text().upper() if uppercase else caption.text()
        out.append(Event(shot.timeline_in, end, _TOP + escape_text(text)))
    return out


__all__ = ["SET_CAPTION_SECONDS", "SetCaption", "set_events", "suggested_text"]
