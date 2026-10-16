"""Trust routes: the before/after for one scene, and what Repcut did to the edit.

Neither renders anything. The comparison names two files that already exist
(the proxy and the scene's graded preview) and how their frames line up; the
summary reads the edit record and refuses to answer with a claim the record
does not back.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from repcut.api.deps import SessionDep, SettingsDep
from repcut.api.edit import ProjectId, _named_errors
from repcut.api.grading import SceneKey, _grade
from repcut.api.grading import _named_errors as _grading_errors
from repcut.api.media import MediaFileId
from repcut.captions import service as captions_service
from repcut.grading import service as grading_service
from repcut.review import service
from repcut.review.compare import comparison

router = APIRouter(tags=["review"])


class ComparisonResponse(BaseModel):
    media_file_id: str
    scene_key: str
    start_seconds: float
    end_seconds: float
    fps: int
    offset_frames: int = Field(description="The proxy frame the preview's first frame shows")
    before_offset_seconds: float
    frame_count: int
    before_url: str = Field(description="The original: the clip's proxy")
    after_url: str = Field(description="Fully processed: the scene's graded preview")
    preview_ready: bool
    captions_on: bool
    captions_style: str


class FactResponse(BaseModel):
    pointer: str
    value: Any


class ClaimResponse(BaseModel):
    id: str
    text: str
    count: int | None
    facts: list[FactResponse]


class SummaryResponse(BaseModel):
    project_id: str
    claims: list[ClaimResponse]
    record: dict[str, Any] = Field(description="The edit record every claim points into")


@router.get(
    "/media/{media_file_id}/compare",
    response_model=ComparisonResponse,
    summary="Before and after for one scene: which files, and how their frames line up",
)
async def get_comparison(
    media_file_id: MediaFileId,
    session: SessionDep,
    settings: SettingsDep,
    scene: SceneKey = None,
) -> ComparisonResponse:
    grade = await _grade(session, settings, media_file_id)
    with _grading_errors():
        target = grade.scene(scene) if scene is not None else grade.scenes[0]
    spec = comparison(target.unit.start_seconds, target.unit.end_seconds)
    project_id = grade.clip.project_id
    captions = await captions_service.load_settings(session, project_id)
    key = target.unit.key
    return ComparisonResponse(
        media_file_id=media_file_id,
        scene_key=key,
        start_seconds=target.unit.start_seconds,
        end_seconds=target.unit.end_seconds,
        fps=spec.fps,
        offset_frames=spec.offset_frames,
        before_offset_seconds=round(spec.before_offset_seconds, 6),
        frame_count=spec.frame_count,
        before_url=f"/media/{media_file_id}/proxy",
        after_url=f"/media/{media_file_id}/grade/preview?scene={key}",
        preview_ready=grading_service.preview_path(settings.data_dir, grade, target).is_file(),
        captions_on=captions.enabled,
        captions_style=captions.style_id,
    )


@router.get(
    "/projects/{project_id}/edit/summary",
    response_model=SummaryResponse,
    summary="What Repcut did to this edit, every line backed by the edit record",
)
async def get_summary(
    project_id: ProjectId, session: SessionDep, settings: SettingsDep
) -> SummaryResponse:
    with _named_errors():
        edit_record = await service.record(session, settings.data_dir, project_id)
    claims = service.checked(edit_record)
    return SummaryResponse(
        project_id=project_id,
        claims=[ClaimResponse.model_validate(claim.to_json()) for claim in claims],
        record=edit_record,
    )


__all__ = ["router"]
