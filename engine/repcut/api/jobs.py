"""Job status over HTTP, and job progress over WebSocket.

`.claude/rules/frontend-and-licensing.md`: "Never a spinner with no
information." Every job therefore reports queued, running with a percentage and
a named current step, then succeeded, failed-with-cause or cancelled. The
percentage is monotonic by construction (``jobs.ProgressReporter``), and the
failure carries a sentence rather than a traceback.

The socket replays each job's current state on connect before streaming live
events. Without that, a client connecting one tick after a job finished would
wait forever for a terminal event that had already been sent - which is exactly
what a page refresh during an ingest looks like.
"""

import asyncio

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from repcut.api.deps import (
    JobQueueDep,
    SessionDep,
    get_job_queue,
    get_session_factory,
    get_settings_from_state,
)
from repcut.api.errors import RepcutAPIError
from repcut.api.schemas import JobResponse
from repcut.db.models import Job, JobStatus
from repcut.jobs import JobEvent
from repcut.logging import get_logger
from repcut.security import is_allowed_origin

logger = get_logger(__name__)

router = APIRouter(tags=["jobs"])

# Sent when nothing has happened for this long, so a dead connection is noticed
# by both ends rather than sitting open looking healthy.
_IDLE_PING_SECONDS = 20.0

_ACTIVE_STATUSES = (JobStatus.QUEUED, JobStatus.RUNNING)


class JobNotFoundError(RepcutAPIError):
    """No job with that id."""

    status_code = 404
    code = "job_not_found"


def _as_response(job: Job) -> JobResponse:
    return JobResponse(
        id=job.id,
        job_type=job.job_type,
        status=job.status,
        progress=job.progress,
        step=job.step,
        error=job.error,
        project_id=job.project_id,
        sha256=job.sha256,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


@router.get("/jobs/{job_id}", response_model=JobResponse, summary="One job's state")
async def get_job(job_id: str, session: SessionDep) -> JobResponse:
    """The polling fallback for a client with no WebSocket."""
    job = await session.get(Job, job_id)
    if job is None:
        raise JobNotFoundError("that job does not exist")
    return _as_response(job)


@router.get("/jobs", response_model=list[JobResponse], summary="Recent jobs")
async def list_jobs(
    session: SessionDep,
    # Bounded by the framework, not by `min()`. `min(limit, 200)` capped the top
    # and let anything through underneath: `?limit=-1` reached SQLite as
    # `LIMIT -1`, which SQLite reads as *no limit* and returns every job row.
    # A validated range refuses the value instead of reinterpreting it.
    limit: int = Query(default=50, ge=1, le=200),
) -> list[JobResponse]:
    """Newest first. Bounded, because this is a debugging surface too."""
    statement = select(Job).order_by(Job.created_at.desc()).limit(limit)
    return [_as_response(job) for job in (await session.execute(statement)).scalars().all()]


@router.post("/jobs/{job_id}/cancel", response_model=JobResponse, summary="Cancel a job")
async def cancel_job(job_id: str, session: SessionDep, queue: JobQueueDep) -> JobResponse:
    """Stop a job that is running or waiting. Cancelling a finished job is a no-op.

    Returns the row as it stands *now*, which for a running job is still
    ``running``: the handler task has been cancelled but unwinds on its own, and
    the terminal ``cancelled`` event arrives over ``/ws/jobs`` a moment later.
    Reporting the transition before it happened would make the response the one
    place in the system that lies about job state.

    Idempotent - cancelling twice, or cancelling something already succeeded,
    returns the current row rather than an error. A client that lost the first
    response must be able to retry.

    Reachable from any page the browser has open, like every route here. The
    worst it can do is stop a job the user can restart with Re-ingest, and job
    ids are random UUIDs, so this does not need more than the host and origin
    checks in `repcut/security.py`.
    """
    job = await session.get(Job, job_id)
    if job is None:
        raise JobNotFoundError("that job does not exist")

    await queue.cancel(job_id)
    await session.refresh(job)
    return _as_response(job)


@router.websocket("/ws/jobs")
async def jobs_socket(websocket: WebSocket) -> None:
    """Stream every job event, after replaying the state of the live ones.

    Subscribing *before* the replay, not after: a job that finishes between the
    replay query and the subscription would otherwise have its terminal event
    fall in the gap, and the client would show a bar stuck at 60% forever.
    Ordering it this way can duplicate an event instead, which a client that
    keys on ``job_id`` absorbs without noticing.
    """
    # Checked before `accept()`, so a rejected handshake never becomes an open
    # socket. CORSMiddleware cannot do this - it never sees a WebSocket scope -
    # which is why every "it's behind CORS" local service ships this hole.
    origin = websocket.headers.get("origin")
    if not is_allowed_origin(origin, get_settings_from_state(websocket)):
        logger.warning("jobs_socket_rejected_origin")
        # 1008 policy violation, and no body: an attacking page learns that it
        # was refused and nothing else.
        await websocket.close(code=1008)
        return

    queue = get_job_queue(websocket)
    session_factory = get_session_factory(websocket)

    await websocket.accept()
    logger.info("jobs_socket_opened")

    with queue.subscribe() as events:
        async with session_factory() as session:
            statement = select(Job).where(Job.status.in_(_ACTIVE_STATUSES))
            for job in (await session.execute(statement)).scalars().all():
                await websocket.send_json(_event_from_row(job).model_dump(mode="json"))

        try:
            await _pump(websocket, events)
        except WebSocketDisconnect:
            # Named: the client closed the tab. Not an error, and not worth a
            # warning - it is the normal end of every socket's life.
            logger.info("jobs_socket_closed")


def _event_from_row(job: Job) -> JobEvent:
    """The same payload shape a live event has, built from a stored row."""
    return JobEvent(
        job_id=job.id,
        job_type=job.job_type,
        status=job.status,
        progress=job.progress,
        step=job.step,
        error=job.error,
        project_id=job.project_id,
        sha256=job.sha256,
        updated_at=job.updated_at,
    )


async def _pump(websocket: WebSocket, events: asyncio.Queue[JobEvent]) -> None:
    """Forward events until the client goes away, pinging through the quiet."""
    while True:
        try:
            event = await asyncio.wait_for(events.get(), timeout=_IDLE_PING_SECONDS)
        except TimeoutError:
            # Named: nothing happened for a while. A ping proves the socket is
            # still there; without it a half-open connection looks like a job
            # that stopped reporting.
            await websocket.send_json({"type": "ping"})
            continue
        await websocket.send_json(event.model_dump(mode="json"))


__all__ = ["JobNotFoundError", "router"]
