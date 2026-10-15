"""Grading routes: the suggestion, the person's choices, resets, stills and previews.

Keyed by ``media_file_id`` (one project's reference to a clip), because choices
belong to a project: the same clip graded two ways in two projects is two sets
of choices. Every route answers with the clip's whole grading state, so the UI
re-renders from one source of truth after any change (P2: dependents re-sync).
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Query, Request, Response
from fastapi import Path as PathParam
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from repcut.api.deps import JobQueueDep, SessionDep, SettingsDep
from repcut.api.errors import (
    ArtifactNotReadyError,
    GradingThemeNotFoundError,
    InvalidGradeChoiceError,
    MediaFileNotFoundError,
    SceneNotFoundError,
)
from repcut.api.media import MediaFileId, _file_size, _serve_bytes
from repcut.db.models import MediaFile
from repcut.grading import service
from repcut.grading.corrector import SceneTags
from repcut.grading.packs import PackLibrary, PackNotFoundError, ThemeNotFoundError
from repcut.grading.preview_job import GRADE_PREVIEW_JOB_TYPE
from repcut.grading.saved import with_saved
from repcut.grading.service import DEFAULT_PACK_ID
from repcut.grading.theme import LightingQuality, LightingTemperature
from repcut.media.store import UnsafeStorePathError

router = APIRouter(tags=["grading"])

_SCENE_KEY = r"^([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|clip)$"
SceneKey = Annotated[str | None, Query(pattern=_SCENE_KEY)]
_SLUG = r"^[a-z0-9][a-z0-9-]{0,39}$"


# --- schemas -----------------------------------------------------------------


class ThemeSummary(BaseModel):
    id: str
    name: str
    description: str
    default_intensity: int


class PackResponse(BaseModel):
    id: str
    name: str
    description: str
    default_theme: str
    themes: list[ThemeSummary]


class TagsResponse(BaseModel):
    lighting_temperature: str | None
    lighting_quality: str | None


class CorrectionResponse(BaseModel):
    exposure_stops: float
    temperature_shift_k: float
    denoise: float
    rules: list[str]


class ChoiceResponse(BaseModel):
    theme_id: str | None
    intensity: int | None
    lighting_temperature: str | None
    lighting_quality: str | None


class SceneGradeResponse(BaseModel):
    key: str = Field(description="The scene id, or 'clip' while the clip has no scenes")
    sequence_index: int
    start_seconds: float
    end_seconds: float
    analysed: TagsResponse
    effective: TagsResponse
    chosen: ChoiceResponse
    theme_id: str
    intensity: int
    suggested: bool = Field(description="Nothing here was chosen by a person: all AI defaults")
    correction: CorrectionResponse
    preview_ready: bool


class ClipGradeResponse(BaseModel):
    media_file_id: str
    pack_id: str
    recommended_theme_id: str
    scores: dict[str, float]
    chosen: ChoiceResponse
    theme_id: str
    intensity: int
    suggested: bool
    scenes: list[SceneGradeResponse]


class ChoiceRequest(BaseModel):
    """Only the fields sent are applied; send ``/grade/reset`` to go back to the suggestion."""

    model_config = ConfigDict(extra="forbid")

    theme_id: str | None = Field(default=None, pattern=_SLUG)
    intensity: int | None = Field(default=None, ge=0, le=100)
    lighting_temperature: LightingTemperature | None = None
    lighting_quality: LightingQuality | None = None


class ResetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene_id: str | None = Field(default=None, pattern=_SCENE_KEY)
    everything: bool = Field(
        default=False, description="With no scene_id: clear the clip's and every scene's choices"
    )


class PreviewJobResponse(BaseModel):
    job_id: str


# --- helpers -----------------------------------------------------------------


@contextmanager
def _named_errors() -> Iterator[None]:
    """Grading-layer failures as the API's named errors."""
    try:
        yield
    except (PackNotFoundError, ThemeNotFoundError) as error:
        raise GradingThemeNotFoundError(str(error)) from error
    except service.GradingNotReadyError as error:
        raise ArtifactNotReadyError(str(error)) from error
    except service.GradeSceneNotFoundError as error:
        raise SceneNotFoundError(str(error)) from error
    except service.GradeChoiceError as error:
        raise InvalidGradeChoiceError(str(error)) from error
    except UnsafeStorePathError as error:
        raise ArtifactNotReadyError("that graded preview is not available") from error


def _tags(tags: SceneTags) -> TagsResponse:
    return TagsResponse(
        lighting_temperature=tags.lighting_temperature, lighting_quality=tags.lighting_quality
    )


def _choice(choice: service.Choice) -> ChoiceResponse:
    return ChoiceResponse(
        theme_id=choice.theme_id,
        intensity=choice.intensity,
        lighting_temperature=choice.lighting_temperature,
        lighting_quality=choice.lighting_quality,
    )


def _response(grade: service.ClipGrade, data_dir: Path, media_file_id: str) -> ClipGradeResponse:
    return ClipGradeResponse(
        media_file_id=media_file_id,
        pack_id=grade.pack.manifest.id,
        recommended_theme_id=grade.recommendation.theme_id,
        scores=grade.recommendation.scores,
        chosen=_choice(grade.choice),
        theme_id=grade.theme_id,
        intensity=grade.intensity,
        suggested=grade.choice.is_empty() and all(s.choice.is_empty() for s in grade.scenes),
        scenes=[
            SceneGradeResponse(
                key=s.unit.key,
                sequence_index=s.unit.index,
                start_seconds=s.unit.start_seconds,
                end_seconds=s.unit.end_seconds,
                analysed=_tags(s.unit.analysed),
                effective=_tags(s.effective_tags),
                chosen=_choice(s.choice),
                theme_id=s.theme.id,
                intensity=s.intensity,
                suggested=s.choice.is_empty() and grade.choice.is_empty(),
                correction=CorrectionResponse(
                    exposure_stops=round(s.correction.exposure_stops, 4),
                    temperature_shift_k=round(s.correction.temperature_shift_k, 1),
                    denoise=round(s.correction.denoise, 4),
                    rules=list(s.correction.rules),
                ),
                preview_ready=service.preview_path(data_dir, grade, s).is_file(),
            )
            for s in grade.scenes
        ],
    )


async def _media_file(session: SessionDep, media_file_id: str) -> MediaFile:
    media_file = await session.get(MediaFile, media_file_id)
    if media_file is None:
        raise MediaFileNotFoundError("that clip is not in this library")
    return media_file


async def _grade(
    session: SessionDep, settings: SettingsDep, media_file_id: str
) -> service.ClipGrade:
    media_file = await _media_file(session, media_file_id)
    with _named_errors():
        return await service.clip_grade(session, settings.data_dir, media_file)


# --- routes ------------------------------------------------------------------


@router.get("/grading/packs/{pack_id}", response_model=PackResponse, summary="A theme pack")
async def get_pack(
    pack_id: Annotated[str, PathParam(pattern=_SLUG)], settings: SettingsDep
) -> PackResponse:
    with _named_errors():
        pack = PackLibrary().load(pack_id)
    if pack_id == DEFAULT_PACK_ID:
        # Saved reference looks join the built-in pack (grading/saved.py).
        pack = with_saved(pack, settings.data_dir)
    return PackResponse(
        id=pack.manifest.id,
        name=pack.manifest.name,
        description=pack.manifest.description,
        default_theme=pack.manifest.default_theme,
        themes=[
            ThemeSummary(
                id=t.id,
                name=t.name,
                description=t.description,
                default_intensity=t.default_intensity,
            )
            for t in pack.themes
        ],
    )


@router.get(
    "/media/{media_file_id}/grade",
    response_model=ClipGradeResponse,
    summary="The clip's grade: suggestion, choices and per-scene correction",
)
async def get_grade(
    media_file_id: MediaFileId, session: SessionDep, settings: SettingsDep
) -> ClipGradeResponse:
    grade = await _grade(session, settings, media_file_id)
    return _response(grade, settings.data_dir, media_file_id)


@router.put(
    "/media/{media_file_id}/grade",
    response_model=ClipGradeResponse,
    summary="Choose a theme or intensity for the whole clip",
)
async def put_clip_choice(
    media_file_id: MediaFileId,
    body: ChoiceRequest,
    session: SessionDep,
    settings: SettingsDep,
) -> ClipGradeResponse:
    grade = await _grade(session, settings, media_file_id)
    with _named_errors():
        await service.apply_choice(
            session, grade, scene_id=None, changes=body.model_dump(exclude_unset=True)
        )
    return _response(
        await _grade(session, settings, media_file_id), settings.data_dir, media_file_id
    )


@router.put(
    "/media/{media_file_id}/grade/scenes/{scene_id}",
    response_model=ClipGradeResponse,
    summary="Override one scene: theme, intensity or its lighting tags",
)
async def put_scene_choice(
    media_file_id: MediaFileId,
    scene_id: Annotated[str, PathParam(pattern=_SCENE_KEY)],
    body: ChoiceRequest,
    session: SessionDep,
    settings: SettingsDep,
) -> ClipGradeResponse:
    grade = await _grade(session, settings, media_file_id)
    with _named_errors():
        await service.apply_choice(
            session, grade, scene_id=scene_id, changes=body.model_dump(exclude_unset=True)
        )
    return _response(
        await _grade(session, settings, media_file_id), settings.data_dir, media_file_id
    )


@router.post(
    "/media/{media_file_id}/grade/reset",
    response_model=ClipGradeResponse,
    summary="Back to the suggestion",
)
async def post_reset(
    media_file_id: MediaFileId,
    body: ResetRequest,
    session: SessionDep,
    settings: SettingsDep,
) -> ClipGradeResponse:
    grade = await _grade(session, settings, media_file_id)
    with _named_errors():
        await service.reset(session, grade, scene_id=body.scene_id, everything=body.everything)
    return _response(
        await _grade(session, settings, media_file_id), settings.data_dir, media_file_id
    )


@router.get(
    "/media/{media_file_id}/grade/still",
    response_class=StreamingResponse,
    summary="One graded still of a scene, under the current or a given theme",
)
async def get_still(
    request: Request,
    media_file_id: MediaFileId,
    session: SessionDep,
    settings: SettingsDep,
    scene: SceneKey = None,
    theme: Annotated[str | None, Query(pattern=_SLUG)] = None,
    intensity: Annotated[int | None, Query(ge=0, le=100)] = None,
) -> Response:
    grade = await _grade(session, settings, media_file_id)
    with _named_errors():
        target = grade.scene(scene) if scene is not None else grade.scenes[len(grade.scenes) // 2]
        if theme is not None or intensity is not None:
            target = service.with_theme(grade, target, theme or target.theme.id, intensity)
        path = await service.ensure_still(settings.data_dir, grade, target)
    size = await asyncio.to_thread(_file_size, path)
    if size is None:
        raise ArtifactNotReadyError("that graded still could not be rendered")
    return await _serve_bytes(request, path, size, "image/jpeg")


@router.post(
    "/media/{media_file_id}/grade/preview",
    response_model=PreviewJobResponse,
    status_code=202,
    summary="Render every scene's graded preview, as a job with progress",
)
async def post_preview(
    media_file_id: MediaFileId, session: SessionDep, queue: JobQueueDep
) -> PreviewJobResponse:
    media_file = await _media_file(session, media_file_id)
    job_id = await queue.enqueue(
        GRADE_PREVIEW_JOB_TYPE, project_id=media_file.project_id, sha256=media_file.sha256
    )
    return PreviewJobResponse(job_id=job_id)


@router.get(
    "/media/{media_file_id}/grade/preview",
    response_class=StreamingResponse,
    summary="One scene's graded preview, if the preview job has rendered it",
)
async def get_preview(
    request: Request,
    media_file_id: MediaFileId,
    session: SessionDep,
    settings: SettingsDep,
    scene: SceneKey = None,
) -> Response:
    grade = await _grade(session, settings, media_file_id)
    with _named_errors():
        target = grade.scene(scene) if scene is not None else grade.scenes[0]
        path = service.preview_path(settings.data_dir, grade, target)
    size = await asyncio.to_thread(_file_size, path)
    if size is None:
        raise ArtifactNotReadyError("this scene's graded preview has not been rendered yet")
    return await _serve_bytes(request, path, size, "video/mp4")


__all__ = ["router"]
