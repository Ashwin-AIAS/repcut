"""Synthetic taste histories: a known person's overrides, replayed as rows.

For the gate and for the profile review (profile-off vs profile-on): a
"moody, fast-cut" person who keeps choosing Moody over the suggestion, turns
slow motion off, matches a fast reference's pacing, grades a little stronger
and prefers the classic caption style. Nothing here is real data; the rows are built from
this module's own constants, with times one minute apart.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from repcut.db.models import TasteEventKind
from repcut.profile.learn import EventRow

_START = datetime(2026, 1, 1, tzinfo=UTC)


def _row(i: int, kind: TasteEventKind, rec: object, chosen: object, **features: object) -> EventRow:
    return EventRow(
        kind=kind.value,
        created_at=_START + timedelta(minutes=i),
        recommended={"value": rec},
        chosen={"value": chosen},
        features=features,
    )


def moody_fast_cut(sessions: int = 10) -> list[EventRow]:
    """``sessions`` editing sessions of one person; five overrides each."""
    rows: list[EventRow] = []
    i = 0
    suggested_themes = ("raw-plus", "aesthetic", "hardstyle")
    for s in range(sessions):
        rows.append(_row(i, TasteEventKind.THEME, suggested_themes[s % 3], "moody"))
        rows.append(_row(i + 1, TasteEventKind.INTENSITY, 80, 92))
        rows.append(_row(i + 2, TasteEventKind.SLOWMO_OFF, f"seg-{s}", None))
        if s % 2 == 0:
            rows.append(_row(i + 3, TasteEventKind.CAPTION_STYLE, "punch", "classic"))
        if s % 3 == 0:
            rows.append(
                _row(
                    i + 4,
                    TasteEventKind.REFERENCE_MATCHED,
                    None,
                    {"energy": 0.9, "calm_beats": 4, "peak_beats": 2},
                    part="pacing",
                )
            )
        i += 10
    return rows


__all__ = ["moody_fast_cut"]
