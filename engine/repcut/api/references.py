"""Reference clips: mark one, measure it, match a project to it, detach any part (P2).

A reference arrives through the ordinary upload routes, so it gets the same
probe, ceiling and refusals as any upload; ``PUT /media/{id}/role`` then marks
it a reference. Only the person's own files: Repcut has no route that fetches
a video from anywhere (the person must have the right to use the reference).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated, Literal

from fastapi import APIRouter
from fastapi import Path as PathParam
from pydantic import BaseModel, ConfigDict, Field

from repcut.api.deps import JobQueueDep, SessionDep, SettingsDep
from repcut.api.errors import (
    ArtifactNotReadyError,
    InvalidReferenceRequestError,
    MediaFileNotFoundError,
    ReferenceNotFoundApiError,
)
from repcut.api.media import MediaFileId
from repcut.db.models import MediaFile, MediaRole
from repcut.grading.service import GradingNotReadyError
from repcut.grading.theme import ThemeFileError
from repcut.reference import service
from repcut.reference.jobs import REFERENCE_JOB_TYPE

router = APIRouter(tags=["references"])

_UUID = r"^[0-9a-fA-F-]{36}$"
ProjectId = Annotated[str, PathParam(pattern=_UUID)]
PartName = Literal["look", "pacing", "captions"]


def _all_parts() -> list[PartName]:
    return ["look", "pacing", "captions"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RoleRequest(_Model):
    role: Literal["footage", "reference"]


class RoleResponse(_Model):
    media_file_id: str
    role: str


class JobStartedResponse(_Model):
    job_id: str


class ProfileResponse(_Model):
    media_file_id: str
    look: dict[str, object]
    rhythm: dict[str, object]
    pace: dict[str, object]
    captions: dict[str, object]
    caption_match: dict[str, object]


class SaveLookRequest(_Model):
    name: str | None = Field(default=None, min_length=1, max_length=40)


class SavedThemeResponse(_Model):
    id: str
    name: str
    description: str


class MatchRequest(_Model):
    media_file_id: str = Field(pattern=_UUID)
    parts: list[PartName] = Field(default_factory=_all_parts, min_length=1)


class MatchResponse(_Model):
    reference_media_file_id: str
    theme_id: str | None
    look_on: bool
    pacing_on: bool
    captions_on: bool
    pace: dict[str, object] | None
    caption_style_id: str | None
    capped: list[str]
    caption_differences: list[str]
    reference_shot_median: float | None


@contextmanager
def _named_errors() -> Iterator[None]:
    try:
        yield
    except service.ReferenceNotFoundError as error:
        raise ReferenceNotFoundApiError(str(error)) from error
    except service.ReferenceError_ as error:
        raise InvalidReferenceRequestError(str(error)) from error
    except GradingNotReadyError as error:
        raise ArtifactNotReadyError(str(error)) from error
    except ThemeFileError as error:
        raise InvalidReferenceRequestError("that look could not be saved as a theme") from error


def _match(state: service.MatchState | None) -> MatchResponse | None:
    if state is None:
        return None
    return MatchResponse(
        reference_media_file_id=state.reference_media_file_id,
        theme_id=state.theme_id,
        look_on=state.look_on,
        pacing_on=state.pacing_on,
        captions_on=state.captions_on,
        pace=state.pace.model_dump(mode="json") if state.pace else None,
        caption_style_id=state.caption_style_id,
        capped=list(state.capped),
        caption_differences=list(state.caption_differences),
        reference_shot_median=state.rhythm.shot_median if state.rhythm else None,
    )


@router.put(
    "/media/{media_file_id}/role", response_model=RoleResponse, summary="Footage or reference"
)
async def put_role(
    media_file_id: MediaFileId, body: RoleRequest, session: SessionDep
) -> RoleResponse:
    media_file = await session.get(MediaFile, media_file_id)
    if media_file is None:
        raise MediaFileNotFoundError("that clip does not exist")
    with _named_errors():
        await service.set_role(session, media_file, MediaRole(body.role))
    return RoleResponse(media_file_id=media_file.id, role=media_file.role)


@router.post(
    "/projects/{project_id}/references/{media_file_id}/analyse",
    response_model=JobStartedResponse,
    status_code=202,
    summary="Measure a reference's look, rhythm and captions, as a job",
)
async def post_analyse(
    project_id: ProjectId, media_file_id: MediaFileId, session: SessionDep, queue: JobQueueDep
) -> JobStartedResponse:
    with _named_errors():
        media_file = await service._reference(session, project_id, media_file_id)
    job_id = await queue.enqueue(
        REFERENCE_JOB_TYPE, project_id=project_id, sha256=media_file.sha256
    )
    return JobStartedResponse(job_id=job_id)


@router.get(
    "/projects/{project_id}/references/{media_file_id}",
    response_model=ProfileResponse,
    summary="What a reference measured as",
)
async def get_profile(
    project_id: ProjectId, media_file_id: MediaFileId, session: SessionDep
) -> ProfileResponse:
    with _named_errors():
        media_file = await service._reference(session, project_id, media_file_id)
    result = await service.cached(session, media_file.sha256)
    if result is None:
        raise ArtifactNotReadyError("this reference has not been measured yet")
    return ProfileResponse(
        media_file_id=media_file.id,
        look=result.look.as_json(),
        rhythm=result.rhythm.as_json(),
        pace=result.pace.model_dump(mode="json"),
        captions=result.captions.as_json(),
        caption_match=result.caption_match.as_json(),
    )


@router.post(
    "/projects/{project_id}/references/{media_file_id}/save-look",
    response_model=SavedThemeResponse,
    status_code=201,
    summary="Save the reference's look as a theme",
)
async def post_save_look(
    project_id: ProjectId,
    media_file_id: MediaFileId,
    body: SaveLookRequest,
    session: SessionDep,
    settings: SettingsDep,
) -> SavedThemeResponse:
    with _named_errors():
        theme = await service.save_look(
            session, settings, project_id, media_file_id, name=body.name
        )
    return SavedThemeResponse(id=theme.id, name=theme.name, description=theme.description)


@router.get(
    "/projects/{project_id}/reference-match",
    response_model=MatchResponse | None,
    summary="Which parts of a reference this project uses",
)
async def get_match(project_id: ProjectId, session: SessionDep) -> MatchResponse | None:
    return _match(await service.state(session, project_id))


@router.put(
    "/projects/{project_id}/reference-match",
    response_model=MatchResponse,
    summary="Match this project to a reference: look, pacing and captions, each optional",
)
async def put_match(
    project_id: ProjectId, body: MatchRequest, session: SessionDep, settings: SettingsDep
) -> MatchResponse:
    with _named_errors():
        state = await service.match(session, settings, project_id, body.media_file_id, body.parts)
    response = _match(state)
    if response is None:
        raise InvalidReferenceRequestError("the match could not be read back")
    return response


@router.delete(
    "/projects/{project_id}/reference-match/{part}",
    response_model=MatchResponse | None,
    summary="Detach one part of the matched reference; the others stay",
)
async def delete_part(
    project_id: ProjectId, part: PartName, session: SessionDep
) -> MatchResponse | None:
    with _named_errors():
        return _match(await service.detach(session, project_id, part))


__all__ = ["router"]
