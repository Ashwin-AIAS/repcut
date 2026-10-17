"""Set captions against the database: one row per hero segment, counted, confirmed.

Rows appear for the edit's hero moments (``edit.heroes``); counting is a job
(``GYM_REPS``) because the pose model is slow; confirming or dismissing is
the person's, and each is a taste event (P3). A row that is no longer a hero
is kept if it was confirmed (the person's numbers are theirs), hidden if not.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repcut.db.models import ChoiceScope, GymSet, GymSetStatus, TasteEvent, TasteEventKind
from repcut.edit import service as edit_service
from repcut.grading.service import load_clip
from repcut.gym.captions import SetCaption
from repcut.gym.reps import (
    COUNTER_VERSION,
    PoseEstimator,
    PoseUnavailableError,
    count_reps,
    segment_keypoints,
)
from repcut.gym.weights import POSE
from repcut.logging import get_logger
from repcut.media.store import absolute

logger = get_logger(__name__)

NOT_COUNTED = "reps not counted yet"
TASTE_SCHEMA_VERSION = 1


class GymSetError(LookupError):
    """No such set in this project, or a value out of range. A fixed sentence."""


@dataclass(frozen=True, slots=True)
class SetChange:
    reps: int | None = None
    exercise: str | None = None
    weight: float | None = None
    weight_unit: str = "kg"


async def _exercise(session: AsyncSession, sha256: str, start: float, end: float) -> str | None:
    middle = (start + end) / 2
    for scene, cache in await edit_service._scenes(session, sha256):
        if scene.start_seconds <= middle < scene.end_seconds and cache and cache.exercise_guess:
            return cache.exercise_guess.strip()[:60] or None
    return None


async def _rows(session: AsyncSession, project_id: str) -> dict[str, GymSet]:
    result = await session.execute(select(GymSet).where(GymSet.project_id == project_id))
    return {row.segment_id: row for row in result.scalars()}


async def sets(
    session: AsyncSession,
    data_dir: Path,
    project_id: str,
    edit: edit_service.EditState | None = None,
) -> list[GymSet]:
    """The project's sets, best hero first; rows are created for new heroes."""
    edit = edit or await edit_service.current(session, data_dir, project_id)
    segments = {s.id: s for s in edit.segments}
    rows = await _rows(session, project_id)
    created = False
    for hero in edit.heroes:
        segment = segments.get(hero.segment_id)
        if segment is None or hero.segment_id in rows:
            continue
        row = GymSet(
            project_id=project_id,
            sha256=hero.sha256,
            segment_id=hero.segment_id,
            start_seconds=segment.start,
            end_seconds=segment.end,
            counter_version=COUNTER_VERSION,
            reason=NOT_COUNTED,
            exercise_suggested=await _exercise(session, hero.sha256, segment.start, segment.end),
            weight_unit="kg",
            status=GymSetStatus.SUGGESTED.value,
        )
        session.add(row)
        rows[hero.segment_id] = row
        created = True
    if created:
        await session.commit()
    order = {h.segment_id: h.rank for h in edit.heroes}
    shown = [
        r
        for r in rows.values()
        if r.segment_id in order or r.status == GymSetStatus.CONFIRMED.value
    ]
    return sorted(shown, key=lambda r: (order.get(r.segment_id, 10**6), r.start_seconds))


async def count(
    session: AsyncSession, data_dir: Path, project_id: str, device_preference: str = "auto"
) -> int:
    """Count reps on every hero set not yet counted. One pose model, opened once."""
    pending = [
        r
        for r in await sets(session, data_dir, project_id)
        if r.status == GymSetStatus.SUGGESTED.value
        and (
            (r.reps_suggested is None and r.reason == NOT_COUNTED)
            or r.counter_version != COUNTER_VERSION
        )
    ]
    if not pending:
        return 0
    clips = {c.sha256: c for c in await edit_service.project_clips(session, project_id)}
    try:
        estimator = PoseEstimator(absolute(data_dir, POSE.stored_path), device_preference)
    except PoseUnavailableError as error:
        for row in pending:
            row.reason = str(error)
        await session.commit()
        return 0
    counted = 0
    with estimator:
        for row in pending:
            clip = clips.get(row.sha256)
            if clip is None:
                continue
            proxy = (await load_clip(session, data_dir, clip)).proxy
            frames = await asyncio.to_thread(
                segment_keypoints, proxy, row.start_seconds, row.end_seconds, estimator
            )
            result = count_reps(frames)
            row.reps_suggested = result.reps
            row.reason = result.reason
            row.counter_version = COUNTER_VERSION
            counted += 1
    logger.info("gym_reps_counted", sets=counted, peak_vram_bytes=estimator.peak_vram_bytes)
    await session.commit()
    return counted


async def _get(session: AsyncSession, project_id: str, set_id: str) -> GymSet:
    row = await session.get(GymSet, set_id)
    if row is None or row.project_id != project_id:
        raise GymSetError("no set with that id exists in this project")
    return row


def _event(row: GymSet, kind: TasteEventKind, chosen: dict[str, Any]) -> TasteEvent:
    return TasteEvent(
        schema_version=TASTE_SCHEMA_VERSION,
        kind=kind.value,
        scope=ChoiceScope.EDIT.value,
        project_id=row.project_id,
        sha256=row.sha256,
        pack_id=None,
        recommended_json=json.dumps(
            {"value": row.reps_suggested, "exercise": row.exercise_suggested}
        ),
        chosen_json=json.dumps(chosen),
        features_json=json.dumps(
            {"segment_id": row.segment_id, "seconds": round(row.end_seconds - row.start_seconds, 2)}
        ),
    )


async def confirm(session: AsyncSession, project_id: str, set_id: str, change: SetChange) -> GymSet:
    """The person's numbers, made a caption. Nothing is burned in before this (P2)."""
    row = await _get(session, project_id, set_id)
    if change.reps is not None and not 0 <= change.reps <= 200:
        raise GymSetError("reps must be between 0 and 200")
    if change.weight is not None and not 0 <= change.weight <= 1000:
        raise GymSetError("weight must be between 0 and 1000")
    if change.weight_unit not in ("kg", "lb"):
        raise GymSetError("weight is in kg or lb")
    exercise = (change.exercise or "").strip()[:60] or None
    if change.reps is None and exercise is None and change.weight is None:
        raise GymSetError("a set caption needs reps, an exercise or a weight")
    row.reps = change.reps
    row.exercise = exercise
    row.weight = change.weight
    row.weight_unit = change.weight_unit
    row.status = GymSetStatus.CONFIRMED.value
    session.add(
        _event(
            row,
            TasteEventKind.GYM_CAPTION_CONFIRMED,
            {
                "value": row.reps,
                "exercise": exercise,
                "weight": row.weight,
                "unit": row.weight_unit,
            },
        )
    )
    await session.commit()
    return row


async def dismiss(session: AsyncSession, project_id: str, set_id: str) -> GymSet:
    row = await _get(session, project_id, set_id)
    row.status = GymSetStatus.DISMISSED.value
    session.add(_event(row, TasteEventKind.GYM_CAPTION_DISMISSED, {"value": None}))
    await session.commit()
    return row


async def confirmed_captions(session: AsyncSession, project_id: str) -> list[SetCaption]:
    return [
        SetCaption(r.segment_id, r.reps, r.exercise, r.weight, r.weight_unit)
        for r in (await _rows(session, project_id)).values()
        if r.status == GymSetStatus.CONFIRMED.value
    ]


__all__ = [
    "NOT_COUNTED",
    "GymSetError",
    "SetChange",
    "confirm",
    "confirmed_captions",
    "count",
    "dismiss",
    "sets",
]
