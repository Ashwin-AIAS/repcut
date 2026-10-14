"""Edit routes: the music library, the project's edit, and overrides on it.

Every edit route answers with the whole edit, so the UI re-renders from one
source of truth after any change (P2: dependents re-sync, visibly). Track and
clip names are display strings; no route takes a path.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated, Literal

from fastapi import APIRouter
from fastapi import Path as PathParam
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from repcut.api.deps import JobQueueDep, SessionDep, SettingsDep
from repcut.api.errors import EditNotReadyApiError, InvalidEditOverrideError
from repcut.db.models import MusicTrack
from repcut.edit import service
from repcut.edit.ducking import gain_at
from repcut.edit.jobs import EDIT_SIGNALS_JOB_TYPE, MUSIC_SCAN_JOB_TYPE
from repcut.edit.resync import RESYNC_BUDGET_SECONDS, Override, OverrideError, OverrideKind
from repcut.edit.types import BoundaryOverride

router = APIRouter(tags=["edit"])

_UUID = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
_DIGEST = r"^[0-9a-f]{64}$"
_SHOT = r"^[0-9a-f]{16}$"
_SEGMENT = r"^[0-9a-f]{16}-[0-9]{1,9}-[0-9]{1,9}$"
ProjectId = Annotated[str, PathParam(pattern=_UUID)]
MAX_LIST = 500


# --- schemas -----------------------------------------------------------------


class TrackResponse(BaseModel):
    sha256: str
    display_name: str
    status: str
    reason: str | None
    bpm: float | None
    duration_seconds: float | None


class LibraryResponse(BaseModel):
    tracks: list[TrackResponse]
    licence_note: str = Field(
        default=(
            "Only tracks you own or that are royalty-free for your use. Repcut never "
            "downloads music. Posting somewhere strict? Export without music."
        )
    )


class JobStarted(BaseModel):
    job_ids: list[str]


class RecommendationResponse(BaseModel):
    sha256: str
    display_name: str
    score: float
    bpm: float
    reason: str


class ShotResponse(BaseModel):
    id: str
    segment_id: str
    sha256: str
    source_in: float
    source_out: float
    timeline_in: float
    timeline_out: float
    beat_in: int | None
    beat_out: int | None
    locked: bool
    speed: float = 1.0
    fallback_in: float | None = None
    hero_score: float | None = None
    hero_reason: str | None = None


class HeroResponse(BaseModel):
    segment_id: str
    sha256: str
    peak: float
    score: float
    rank: int
    reasons: list[str]
    slow_in_edit: bool
    slowmo_off: bool


class SegmentResponse(BaseModel):
    id: str
    sha256: str
    start: float
    end: float
    quality: float
    energy: float
    used: bool
    removed: bool
    forced: bool
    slowmo_on: bool = False


class GainPoint(BaseModel):
    seconds: float
    gain_db: float


class EditResponse(BaseModel):
    project_id: str
    theme_id: str
    target_seconds: float
    duration_seconds: float
    music: Literal["suggested", "chosen", "off"]
    track_sha256: str | None
    music_start_seconds: float
    snapped: bool
    beats: list[float] = Field(description="Beat times on the edit's timeline, for the markers")
    downbeats: list[float]
    shots: list[ShotResponse]
    heroes: list[HeroResponse] = Field(default_factory=list)
    segments: list[SegmentResponse]
    recommendations: list[RecommendationResponse]
    gain: list[GainPoint]
    speech_measured: bool
    clips_pending: list[str]
    warnings: list[str]
    resync_ms: float
    resync_budget_ms: float = RESYNC_BUDGET_SECONDS * 1000


class BoundaryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sha256: str = Field(pattern=_DIGEST)
    action: Literal["add", "remove"]
    seconds: float = Field(ge=0.0, le=24 * 3600)


class OverrideRequest(BaseModel):
    """One change. Only the field its ``kind`` names is read."""

    model_config = ConfigDict(extra="forbid")

    kind: OverrideKind
    track_sha256: str | None = Field(default=None, pattern=_DIGEST)
    target_seconds: float | None = Field(default=None, ge=5.0, le=180.0)
    shot_id: str | None = Field(default=None, pattern=_SHOT)
    segment_id: str | None = Field(default=None, pattern=_SEGMENT)
    boundary: BoundaryRequest | None = None


# --- helpers -----------------------------------------------------------------


@contextmanager
def _named_errors() -> Iterator[None]:
    try:
        yield
    except service.EditNotReadyError as error:
        raise EditNotReadyApiError(str(error)) from error
    except OverrideError as error:
        raise InvalidEditOverrideError(str(error)) from error


async def _beats(session: SessionDep, state: service.EditState) -> tuple[list[float], list[float]]:
    plan = state.plan
    if not plan.snapped or plan.track_sha256 is None:
        return [], []
    tracks = {t.sha256: t for _, t in await service.library(session)}
    track = tracks.get(plan.track_sha256)
    if track is None:
        return [], []
    analysis = track.analysis
    zero = plan.music_start_seconds

    def on_timeline(times: tuple[float, ...]) -> list[float]:
        return [
            round(t - zero, 4) for t in times if 0.0 <= t - zero <= plan.duration_seconds + 1e-6
        ]

    return on_timeline(analysis.beats), on_timeline(analysis.bar_starts)


async def _response(session: SessionDep, project_id: str, state: service.EditState) -> EditResponse:
    plan = state.plan
    used = {shot.segment_id for shot in plan.shots}
    slow_segments = {shot.segment_id for shot in plan.shots if shot.slow}
    beats, downbeats = await _beats(session, state)
    steps = max(1, int(plan.duration_seconds / 0.1))
    gain = (
        [
            GainPoint(
                seconds=round(i * 0.1, 2), gain_db=round(gain_at(list(state.ramps), i * 0.1), 2)
            )
            for i in range(steps + 1)
        ]
        if state.ramps
        else []
    )
    return EditResponse(
        project_id=project_id,
        theme_id=state.theme.id,
        target_seconds=state.decisions.target_seconds,
        duration_seconds=plan.duration_seconds,
        music=state.decisions.music,
        track_sha256=plan.track_sha256,
        music_start_seconds=plan.music_start_seconds,
        snapped=plan.snapped,
        beats=beats,
        downbeats=downbeats,
        shots=[ShotResponse(**shot.to_json()) for shot in plan.shots],  # type: ignore[arg-type]
        segments=[
            SegmentResponse(
                id=s.id,
                sha256=s.sha256,
                start=s.start,
                end=s.end,
                quality=s.quality,
                energy=s.energy,
                used=s.id in used,
                removed=s.id in state.decisions.removed_segments,
                forced=s.id in state.decisions.forced_segments,
                slowmo_on=s.id in state.decisions.slowmo_on,
            )
            for s in state.segments[:MAX_LIST]
        ],
        heroes=[
            HeroResponse(
                segment_id=h.segment_id,
                sha256=h.sha256,
                peak=h.peak,
                score=h.score,
                rank=h.rank,
                reasons=list(h.reasons),
                slow_in_edit=h.segment_id in slow_segments,
                slowmo_off=h.segment_id in state.decisions.slowmo_off,
            )
            for h in state.heroes
        ],
        recommendations=[
            RecommendationResponse(
                sha256=r.sha256,
                display_name=r.display_name,
                score=r.score,
                bpm=r.bpm,
                reason=r.reason,
            )
            for r in state.recommendations
        ],
        gain=gain,
        speech_measured=state.speech_measured,
        clips_pending=list(state.clips_pending),
        warnings=list(state.warnings),
        resync_ms=round(state.resync_seconds * 1000, 2),
    )


# --- music -------------------------------------------------------------------


@router.get("/music/tracks", response_model=LibraryResponse, summary="The music library")
async def get_tracks(session: SessionDep) -> LibraryResponse:
    statement = (
        select(MusicTrack)
        .where(MusicTrack.present.is_(True))
        .order_by(MusicTrack.display_name)
        .limit(MAX_LIST)
    )
    rows = (await session.execute(statement)).scalars()
    return LibraryResponse(
        tracks=[
            TrackResponse(
                sha256=row.sha256,
                display_name=row.display_name,
                status=row.status.value,
                reason=row.reason,
                bpm=row.bpm,
                duration_seconds=row.duration_seconds,
            )
            for row in rows
        ]
    )


@router.post(
    "/music/scan",
    response_model=JobStarted,
    status_code=202,
    summary="Look for new tracks and analyse them, as a job with progress",
)
async def post_scan(queue: JobQueueDep) -> JobStarted:
    return JobStarted(job_ids=[await queue.enqueue(MUSIC_SCAN_JOB_TYPE)])


# --- the edit ----------------------------------------------------------------


@router.get(
    "/projects/{project_id}/edit", response_model=EditResponse, summary="The project's edit"
)
async def get_edit(
    project_id: ProjectId, session: SessionDep, settings: SettingsDep
) -> EditResponse:
    with _named_errors():
        state = await service.current(session, settings.data_dir, project_id)
    return await _response(session, project_id, state)


@router.post(
    "/projects/{project_id}/edit/overrides",
    response_model=EditResponse,
    summary="Apply one override; everything not locked is re-planned",
)
async def post_override(
    project_id: ProjectId, body: OverrideRequest, session: SessionDep, settings: SettingsDep
) -> EditResponse:
    override = Override(
        kind=body.kind,
        track_sha256=body.track_sha256,
        target_seconds=body.target_seconds,
        shot_id=body.shot_id,
        segment_id=body.segment_id,
        boundary=BoundaryOverride(body.boundary.sha256, body.boundary.action, body.boundary.seconds)
        if body.boundary
        else None,
    )
    with _named_errors():
        state = await service.apply_override(session, settings.data_dir, project_id, override)
    return await _response(session, project_id, state)


@router.post(
    "/projects/{project_id}/edit/signals",
    response_model=JobStarted,
    status_code=202,
    summary="Measure every clip that has no signals yet, one job per clip",
)
async def post_signals(
    project_id: ProjectId, session: SessionDep, settings: SettingsDep, queue: JobQueueDep
) -> JobStarted:
    with _named_errors():
        clips = await service.clips_without_signals(session, settings.data_dir, project_id)
    ids = [
        await queue.enqueue(EDIT_SIGNALS_JOB_TYPE, project_id=clip.project_id, sha256=clip.sha256)
        for clip in clips
    ]
    return JobStarted(job_ids=ids)


__all__ = ["router"]
