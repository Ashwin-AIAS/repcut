"""Set captions: the counted reps on hero moments, confirmed by the person (P2).

Nothing here burns anything in. A set becomes a caption only when the person
confirms it, with their own numbers; the export reads confirmed sets only.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from repcut.api.deps import JobQueueDep, SessionDep, SettingsDep
from repcut.api.edit import ProjectId
from repcut.api.edit import _named_errors as _edit_errors
from repcut.api.errors import GymSetNotFoundApiError, InvalidGymSetError
from repcut.db.models import GymSet
from repcut.gym import service
from repcut.gym.captions import SetCaption, suggested_text
from repcut.gym.jobs import GYM_REPS_JOB_TYPE
from repcut.gym.reps import PRODUCT_BOUNDARY

router = APIRouter(tags=["gym"])


class SetResponse(BaseModel):
    id: str
    segment_id: str
    start_seconds: float
    end_seconds: float
    status: Literal["suggested", "confirmed", "dismissed"]
    reps_suggested: int | None = Field(description="The counter's number; a suggestion only")
    exercise_suggested: str | None
    reason: str | None = Field(description="Why there is no suggested count, if there is none")
    suggestion: str = Field(description="The caption the suggestion would make")
    reps: int | None
    exercise: str | None
    weight: float | None
    weight_unit: Literal["kg", "lb"]
    caption: str | None = Field(description="The confirmed caption; null until confirmed")


class SetsResponse(BaseModel):
    project_id: str
    sets: list[SetResponse]
    boundary: str = Field(description="What rep counting never does")


class SetAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["confirm", "dismiss"]
    reps: int | None = Field(default=None, ge=0, le=200)
    exercise: str | None = Field(default=None, max_length=60)
    weight: float | None = Field(default=None, ge=0, le=1000)
    weight_unit: Literal["kg", "lb"] = "kg"


class JobStarted(BaseModel):
    job_ids: list[str]


@contextmanager
def _named_errors() -> Iterator[None]:
    try:
        yield
    except service.GymSetError as error:
        if "no set" in str(error):
            raise GymSetNotFoundApiError(str(error)) from error
        raise InvalidGymSetError(str(error)) from error


def _set(row: GymSet) -> SetResponse:
    confirmed = row.status == "confirmed"
    caption = (
        SetCaption(row.segment_id, row.reps, row.exercise, row.weight, row.weight_unit).text()
        if confirmed
        else None
    )
    return SetResponse(
        id=row.id,
        segment_id=row.segment_id,
        start_seconds=row.start_seconds,
        end_seconds=row.end_seconds,
        status=row.status,  # type: ignore[arg-type]
        reps_suggested=row.reps_suggested,
        exercise_suggested=row.exercise_suggested,
        reason=row.reason,
        suggestion=suggested_text(row.reps_suggested, row.exercise_suggested),
        reps=row.reps,
        exercise=row.exercise,
        weight=row.weight,
        weight_unit=row.weight_unit,  # type: ignore[arg-type]
        caption=caption,
    )


@router.get(
    "/projects/{project_id}/gym/sets",
    response_model=SetsResponse,
    summary="Set captions for the edit's best moments: suggested, confirmed or dismissed",
)
async def get_sets(
    project_id: ProjectId, session: SessionDep, settings: SettingsDep
) -> SetsResponse:
    with _edit_errors():
        rows = await service.sets(session, settings.data_dir, project_id)
    return SetsResponse(
        project_id=project_id, sets=[_set(r) for r in rows], boundary=PRODUCT_BOUNDARY
    )


@router.post(
    "/projects/{project_id}/gym/count",
    response_model=JobStarted,
    status_code=202,
    summary="Count reps on the hero moments (a job: the pose model is slow)",
)
async def post_count(
    project_id: ProjectId, session: SessionDep, settings: SettingsDep, queue: JobQueueDep
) -> JobStarted:
    with _edit_errors():
        await service.sets(session, settings.data_dir, project_id)
    return JobStarted(job_ids=[await queue.enqueue(GYM_REPS_JOB_TYPE, project_id=project_id)])


@router.post(
    "/projects/{project_id}/gym/sets/{set_id}",
    response_model=SetResponse,
    summary="Confirm a set caption with your numbers, or dismiss it",
)
async def post_set(
    project_id: ProjectId, set_id: str, body: SetAction, session: SessionDep
) -> SetResponse:
    with _named_errors():
        if body.action == "dismiss":
            row = await service.dismiss(session, project_id, set_id)
        else:
            change = service.SetChange(body.reps, body.exercise, body.weight, body.weight_unit)
            row = await service.confirm(session, project_id, set_id, change)
    return _set(row)


__all__ = ["router"]
