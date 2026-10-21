"""Export routes: presets, per-scene reframe offsets, the render job, the file.

The file route serves the rendered MP4 by project and preset, never by a path
from the request, with the same Range handling as the previews (``media``).
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Request, Response
from fastapi import Path as PathParam
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from repcut.api.deps import JobQueueDep, SessionDep, SettingsDep
from repcut.api.errors import (
    EditNotReadyApiError,
    ExportNotReadyApiError,
    InvalidCaptionOverrideError,
)
from repcut.api.media import _serve_bytes
from repcut.copilot import tools
from repcut.edit.service import EditNotReadyError
from repcut.export import service
from repcut.export.jobs import EXPORT_ALL_JOB_TYPE, EXPORT_JOB_TYPE
from repcut.export.presets import config

router = APIRouter(tags=["export"])

_UUID = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
_PRESET = r"^[a-z0-9][a-z0-9-]{0,39}$"
_SCENE = r"^[0-9a-f]{16}:[0-9a-z-]{1,40}$"
ProjectId = Annotated[str, PathParam(pattern=_UUID)]
PresetId = Annotated[str, PathParam(pattern=_PRESET)]


class PresetResponse(BaseModel):
    id: str
    label: str
    width: int
    height: int


class SceneResponse(BaseModel):
    key: str
    clip_name: str
    index: int
    start_seconds: float
    end_seconds: float
    in_edit: bool
    offset: float


class LatestExport(BaseModel):
    preset_id: str
    size_bytes: int
    segments_total: int
    segments_rendered: int
    segments_reused: int
    timings_ms: dict[str, int]
    problems: list[str]
    warnings: list[str]


class ExportResponse(BaseModel):
    project_id: str
    preset_id: str
    suggested_preset_id: str
    presets: list[PresetResponse]
    scenes: list[SceneResponse]
    latest: LatestExport | None


class ExportChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preset_id: str | None = Field(default=None, pattern=_PRESET)
    scene: str | None = Field(default=None, pattern=_SCENE)
    offset: float | None = Field(default=None, ge=-1.0, le=1.0)


class JobStarted(BaseModel):
    job_ids: list[str]


@contextmanager
def _named_errors() -> Iterator[None]:
    try:
        yield
    except EditNotReadyError as error:
        raise EditNotReadyApiError(str(error)) from error
    except service.ExportNotReadyError as error:
        raise ExportNotReadyApiError(str(error)) from error
    except (service.ExportSettingsError, tools.ToolError) as error:
        raise InvalidCaptionOverrideError(str(error)) from error


async def _response(session: AsyncSession, data_dir: Path, project_id: str) -> ExportResponse:
    settings = await service.load_settings(session, project_id)
    cfg = config()
    preset = cfg.preset(settings.preset_id)
    latest = service.latest_export(data_dir, project_id, preset)
    return ExportResponse(
        project_id=project_id,
        preset_id=preset.id,
        suggested_preset_id=cfg.default,
        presets=[
            PresetResponse(id=p.id, label=p.label, width=p.width, height=p.height)
            for p in cfg.presets
        ],
        scenes=[
            SceneResponse(
                key=s.key,
                clip_name=s.clip_name,
                index=s.index,
                start_seconds=s.start_seconds,
                end_seconds=s.end_seconds,
                in_edit=s.in_edit,
                offset=settings.offsets.get(s.key, 0.0),
            )
            for s in await service.scenes(session, data_dir, project_id)
        ],
        latest=LatestExport(
            preset_id=preset.id,
            size_bytes=int(latest["size_bytes"]),
            segments_total=int(latest["segments_total"]),
            segments_rendered=int(latest["segments_rendered"]),
            segments_reused=int(latest["segments_reused"]),
            timings_ms={str(k): int(v) for k, v in latest["timings_ms"].items()},
            problems=[str(p) for p in latest["problems"]],
            warnings=[str(w) for w in latest["warnings"]],
        )
        if latest
        else None,
    )


@router.get(
    "/projects/{project_id}/export", response_model=ExportResponse, summary="Export settings"
)
async def get_export(
    project_id: ProjectId, session: SessionDep, settings: SettingsDep
) -> ExportResponse:
    with _named_errors():
        return await _response(session, settings.data_dir, project_id)


@router.post(
    "/projects/{project_id}/export/settings",
    response_model=ExportResponse,
    summary="Change the preset or a scene's reframe",
)
async def post_export_settings(
    project_id: ProjectId, body: ExportChange, session: SessionDep, settings: SettingsDep
) -> ExportResponse:
    ctx = tools.ToolContext(session, settings.data_dir, project_id)
    with _named_errors():
        if body.preset_id is not None:
            await tools.execute(ctx, "set_export_preset", {"preset_id": body.preset_id})
        if body.scene is not None and body.offset is not None:
            scene, offset = body.scene, body.offset

            async def reframe() -> str:
                await service.set_offset(session, settings.data_dir, project_id, scene, offset)
                return "Reframe moved."

            await tools.recorded(ctx, "reframe_offset", reframe)
        return await _response(session, settings.data_dir, project_id)


@router.post(
    "/projects/{project_id}/export",
    response_model=JobStarted,
    status_code=202,
    summary="Render the export as a job with progress; resumes from finished shots",
)
async def post_export(
    project_id: ProjectId, session: SessionDep, settings: SettingsDep, queue: JobQueueDep
) -> JobStarted:
    with _named_errors():
        await service.gather(session, settings.data_dir, project_id, track_subjects=False)
    return JobStarted(job_ids=[await queue.enqueue(EXPORT_JOB_TYPE, project_id=project_id)])


@router.post(
    "/projects/{project_id}/export/all",
    response_model=JobStarted,
    status_code=202,
    summary="Export every platform preset (9:16, 1:1, 16:9) from this edit, as one job",
)
async def post_export_all(
    project_id: ProjectId, session: SessionDep, settings: SettingsDep, queue: JobQueueDep
) -> JobStarted:
    with _named_errors():
        await service.gather(session, settings.data_dir, project_id, track_subjects=False)
    return JobStarted(job_ids=[await queue.enqueue(EXPORT_ALL_JOB_TYPE, project_id=project_id)])


@router.get(
    "/projects/{project_id}/export/{preset_id}/file",
    summary="The rendered export",
    response_class=StreamingResponse,
)
async def get_export_file(
    request: Request, project_id: ProjectId, preset_id: PresetId, settings: SettingsDep
) -> Response:
    try:
        preset = config().preset(preset_id)
    except KeyError as error:
        raise ExportNotReadyApiError("no export preset with that id exists") from error
    path = service.output_path(settings.data_dir, project_id, preset)
    size = await asyncio.to_thread(lambda: path.stat().st_size if path.is_file() else None)
    if size is None:
        raise ExportNotReadyApiError("this project has not been exported with that preset yet")
    response = await _serve_bytes(request, path, size, "video/mp4")
    response.headers["content-disposition"] = f'attachment; filename="repcut-{preset.id}.mp4"'
    return response


__all__ = ["router"]
