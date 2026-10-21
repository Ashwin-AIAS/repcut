"""Caption routes: the transcript on the edit's timeline, and changes to it.

Every route answers with the whole caption state, so the editor re-renders from
one source of truth. Caption text in a response is data: the UI renders it as
text (React escapes it), never as markup.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated, Literal

from fastapi import APIRouter
from fastapi import Path as PathParam
from pydantic import BaseModel, ConfigDict, Field

from repcut.api.deps import JobQueueDep, SessionDep, SettingsDep
from repcut.api.errors import EditNotReadyApiError, InvalidCaptionOverrideError
from repcut.captions import service
from repcut.captions.jobs import TRANSCRIBE_JOB_TYPE
from repcut.captions.styles import DEFAULT_STYLE, styles
from repcut.captions.transcribe import MODEL_MISSING
from repcut.copilot import tools
from repcut.edit.service import EditNotReadyError, project_clips

router = APIRouter(tags=["captions"])

_UUID = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
_WORD = r"^[0-9a-f]{16}-[0-9]{1,6}-[0-9]{1,4}$"
_LINE = r"^[0-9a-f]{16}-[0-9]{1,6}$"
_STYLE = r"^[a-z0-9][a-z0-9-]{0,39}$"
ProjectId = Annotated[str, PathParam(pattern=_UUID)]


class WordResponse(BaseModel):
    id: str
    text: str
    original: str
    start: float
    end: float
    probability: float
    edited: bool


class LineResponse(BaseModel):
    id: str
    start: float
    end: float
    hidden: bool
    words: list[WordResponse]


class StyleResponse(BaseModel):
    id: str
    name: str
    description: str


class CaptionsResponse(BaseModel):
    project_id: str
    enabled: bool
    style_id: str
    suggested_style_id: str
    styles: list[StyleResponse]
    lines: list[LineResponse]
    model_installed: bool
    model_message: str | None
    clips_untranscribed: list[str]
    languages: list[str]
    dropped_segments: int = Field(description="Segments the guard dropped as likely invented")


class CaptionChange(BaseModel):
    """One change. Only the fields its ``kind`` names are read."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["style", "enabled", "word", "line"]
    style_id: str | None = Field(default=None, pattern=_STYLE)
    enabled: bool | None = None
    word_id: str | None = Field(default=None, pattern=_WORD)
    text: str | None = Field(default=None, max_length=200)
    line_id: str | None = Field(default=None, pattern=_LINE)
    hidden: bool | None = None


class JobStarted(BaseModel):
    job_ids: list[str]


@contextmanager
def _named_errors() -> Iterator[None]:
    try:
        yield
    except EditNotReadyError as error:
        raise EditNotReadyApiError(str(error)) from error
    except (service.CaptionOverrideError, ValueError) as error:
        raise InvalidCaptionOverrideError(str(error)) from error


def _response(project_id: str, state: service.CaptionState) -> CaptionsResponse:
    return CaptionsResponse(
        project_id=project_id,
        enabled=state.settings.enabled,
        style_id=state.style.id,
        suggested_style_id=DEFAULT_STYLE,
        styles=[StyleResponse(id=s.id, name=s.name, description=s.description) for s in styles()],
        lines=[
            LineResponse(
                id=line.id,
                start=line.start,
                end=line.end,
                hidden=line.hidden,
                words=[
                    WordResponse(
                        id=w.id,
                        text=w.text,
                        original=w.original,
                        start=w.start,
                        end=w.end,
                        probability=round(w.probability, 3),
                        edited=w.edited,
                    )
                    for w in line.words
                ],
            )
            for line in state.lines
        ],
        model_installed=state.model_installed,
        model_message=None if state.model_installed else MODEL_MISSING,
        clips_untranscribed=list(state.clips_untranscribed),
        languages=list(state.languages),
        dropped_segments=state.dropped,
    )


@router.get(
    "/projects/{project_id}/captions",
    response_model=CaptionsResponse,
    summary="The edit's captions",
)
async def get_captions(
    project_id: ProjectId, session: SessionDep, settings: SettingsDep
) -> CaptionsResponse:
    with _named_errors():
        state = await service.state(session, settings.data_dir, project_id)
    return _response(project_id, state)


@router.post(
    "/projects/{project_id}/captions",
    response_model=CaptionsResponse,
    summary="Change the style, switch captions on or off, edit a word, hide a line",
)
async def post_caption_change(
    project_id: ProjectId, body: CaptionChange, session: SessionDep, settings: SettingsDep
) -> CaptionsResponse:
    change = service.CaptionOverride(
        kind=body.kind,
        style_id=body.style_id,
        enabled=body.enabled,
        word_id=body.word_id,
        text=body.text,
        line_id=body.line_id,
        hidden=body.hidden,
    )
    ctx = tools.ToolContext(session, settings.data_dir, project_id)
    with _named_errors():
        # The registry's operations where they exist (the copilot's too); a
        # hidden line still goes on the same undo stack.
        if body.kind in ("style", "enabled"):
            await tools.execute(
                ctx,
                "set_caption_style",
                {"style_id": body.style_id} if body.kind == "style" else {"enabled": body.enabled},
            )
        elif body.kind == "word":
            await tools.execute(
                ctx, "edit_caption_text", {"word_id": body.word_id or "", "text": body.text or ""}
            )
        else:

            async def line() -> str:
                await service.apply_override(session, settings.data_dir, project_id, change)
                return "Caption line hidden." if body.hidden else "Caption line shown."

            await tools.recorded(ctx, "caption_line", line)
        state = await service.state(session, settings.data_dir, project_id)
    return _response(project_id, state)


@router.post(
    "/projects/{project_id}/captions/transcribe",
    response_model=JobStarted,
    status_code=202,
    summary="Transcribe every clip that has no transcript yet, one job per clip",
)
async def post_transcribe(
    project_id: ProjectId, session: SessionDep, settings: SettingsDep, queue: JobQueueDep
) -> JobStarted:
    clips = await project_clips(session, project_id)
    ids = [
        await queue.enqueue(TRANSCRIBE_JOB_TYPE, project_id=clip.project_id, sha256=clip.sha256)
        for clip in clips
        if service.read_transcript(settings.data_dir, clip.sha256) is None
    ]
    return JobStarted(job_ids=ids)


__all__ = ["router"]
