"""The undo stack: one entry per change, from the controls or from the copilot.

A project's *decision state* is four kinds of row: its edit plan, its grading
choices, its caption settings and its export settings. Every change records
that state before and after it, as canonical JSON (sorted keys, no
timestamps), so "undo restores the state byte for byte" is a comparison of two
strings, not a hope.

Undo is selective: it reverts one entry's rows to their ``before`` value, but
only rows that still hold that entry's ``after`` value. A row changed since by
something else is a conflict and nothing is written (a fixed sentence says
so); undoing the newest change never conflicts. The edit plan's derived
``plan_json`` is not compared (a new clip re-plans it without a decision), but
is restored with its decisions.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from repcut.db.models import (
    CaptionSettings,
    EditPlan,
    ExportSettings,
    GradeChoice,
    HistoryEntry,
    TasteEvent,
)

# Columns that are bookkeeping, not decisions: never compared, never restored.
_SKIP = frozenset({"updated_at", "created_at"})

Rows = dict[str, dict[str, Any] | None]


class UndoError(LookupError):
    """Nothing to undo, or the change cannot be undone. A fixed sentence."""


def _columns(row: object, model: type) -> dict[str, Any]:
    table = model.__table__  # type: ignore[attr-defined]
    return {c.name: getattr(row, c.key) for c in table.columns if c.name not in _SKIP}


def canonical(state: Mapping[str, object]) -> str:
    """The one serialisation two states are compared by."""
    return json.dumps(state, sort_keys=True, separators=(",", ":"), default=str)


async def snapshot(session: AsyncSession, project_id: str) -> Rows:
    """Every decision row of one project, keyed so a row is found again by its key."""
    await session.flush()
    rows: Rows = {}
    plan = await session.get(EditPlan, project_id)
    rows["edit_plans"] = None if plan is None else _columns(plan, EditPlan)
    captions = await session.get(CaptionSettings, project_id)
    rows["caption_settings"] = None if captions is None else _columns(captions, CaptionSettings)
    export = await session.get(ExportSettings, project_id)
    rows["export_settings"] = None if export is None else _columns(export, ExportSettings)
    statement = select(GradeChoice).where(GradeChoice.project_id == project_id)
    for choice in (await session.execute(statement)).scalars():
        key = f"grade_choices:{choice.sha256}:{choice.scene_id or '-'}"
        rows[key] = _columns(choice, GradeChoice)
    return rows


def _comparable(key: str, value: dict[str, Any] | None) -> dict[str, Any] | None:
    if value is None or key != "edit_plans":
        return value
    return {k: v for k, v in value.items() if k != "plan_json"}


def changed_keys(before: Rows, after: Rows) -> list[str]:
    keys = sorted(set(before) | set(after))
    return [
        k
        for k in keys
        if canonical({"v": _comparable(k, before.get(k))})
        != canonical({"v": _comparable(k, after.get(k))})
    ]


async def _write(
    session: AsyncSession, project_id: str, key: str, value: dict[str, Any] | None
) -> None:
    if key.startswith("grade_choices:"):
        _, sha256, scene = key.split(":", 2)
        scene_id = None if scene == "-" else scene
        statement = select(GradeChoice).where(
            GradeChoice.project_id == project_id,
            GradeChoice.sha256 == sha256,
            GradeChoice.scene_id.is_(None)
            if scene_id is None
            else GradeChoice.scene_id == scene_id,
        )
        existing = (await session.execute(statement)).scalars().first()
        if existing is not None:
            await session.delete(existing)
            await session.flush()
        if value is not None:
            session.add(GradeChoice(**value))
        return
    model = {
        "edit_plans": EditPlan,
        "caption_settings": CaptionSettings,
        "export_settings": ExportSettings,
    }[key]
    row = await session.get(model, project_id)
    if value is None:
        if row is not None:
            await session.delete(row)
        return
    if row is None:
        session.add(model(**value))
        return
    for name, item in value.items():
        setattr(row, name, item)


@dataclass(frozen=True, slots=True)
class Undone:
    entry_id: str
    operation: str
    summary: str


async def record(
    session: AsyncSession,
    project_id: str,
    *,
    source: str,
    operation: str,
    summary: str,
    before: Rows,
    after: Rows,
    event_ids: list[str],
) -> HistoryEntry:
    entry = HistoryEntry(
        project_id=project_id,
        source=source,
        operation=operation,
        summary=summary,
        before_json=canonical(before),
        after_json=canonical(after),
        event_ids_json=json.dumps(sorted(event_ids)),
    )
    session.add(entry)
    await session.commit()
    return entry


async def latest(session: AsyncSession, project_id: str) -> HistoryEntry | None:
    statement = (
        select(HistoryEntry)
        .where(HistoryEntry.project_id == project_id, HistoryEntry.undone.is_(False))
        .order_by(HistoryEntry.created_at.desc(), HistoryEntry.id.desc())
        .limit(1)
    )
    return (await session.execute(statement)).scalars().first()


async def undo(session: AsyncSession, project_id: str, entry_id: str | None = None) -> Undone:
    """Revert one entry (the newest when ``entry_id`` is None). Raises ``UndoError``."""
    if entry_id is None:
        entry = await latest(session, project_id)
        if entry is None:
            raise UndoError("there is nothing to undo")
    else:
        entry = await session.get(HistoryEntry, entry_id)
        if entry is None or entry.project_id != project_id:
            raise UndoError("that change is not in this project's history")
        if entry.undone:
            raise UndoError("that change was already undone")
    before: Rows = json.loads(entry.before_json)
    after: Rows = json.loads(entry.after_json)
    now = await snapshot(session, project_id)
    keys = changed_keys(before, after)
    for key in keys:
        if canonical({"v": _comparable(key, now.get(key))}) != canonical(
            {"v": _comparable(key, after.get(key))}
        ):
            raise UndoError("that change was changed again since; undo the later change first")
    for key in keys:
        await _write(session, project_id, key, before.get(key))
    ids = json.loads(entry.event_ids_json)
    if ids:
        await session.execute(delete(TasteEvent).where(TasteEvent.id.in_(ids)))
    entry.undone = True
    await session.commit()
    return Undone(entry_id=entry.id, operation=entry.operation, summary=entry.summary)


async def entries(session: AsyncSession, project_id: str, limit: int) -> list[HistoryEntry]:
    statement = (
        select(HistoryEntry)
        .where(HistoryEntry.project_id == project_id)
        .order_by(HistoryEntry.created_at.desc(), HistoryEntry.id.desc())
        .limit(limit)
    )
    return list((await session.execute(statement)).scalars())


async def event_ids(session: AsyncSession, project_id: str) -> set[str]:
    statement = select(TasteEvent.id).where(TasteEvent.project_id == project_id)
    return set((await session.execute(statement)).scalars())


__all__ = [
    "Rows",
    "UndoError",
    "Undone",
    "canonical",
    "changed_keys",
    "entries",
    "event_ids",
    "latest",
    "record",
    "snapshot",
    "undo",
]
