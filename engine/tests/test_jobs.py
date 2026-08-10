"""Job lifecycle over the WebSocket. Gate criterion 9.

The socket is exercised through Starlette's own test client rather than httpx:
``ASGITransport`` speaks HTTP only, so a websocket test written against it would
pass by never opening a socket at all.
"""

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from conftest import Harness
from fastapi import FastAPI
from starlette.testclient import TestClient

from repcut.api import jobs as jobs_api
from repcut.api import projects as projects_api
from repcut.api import uploads as uploads_api
from repcut.api.errors import install_error_handler
from repcut.config import Settings
from repcut.db.models import Job, JobStatus
from repcut.jobs import JobEvent, JobQueue, JobRecord, ProgressReporter, describe_failure
from repcut.main import start_engine, stop_engine
from repcut.media.ffmpeg_builder import FFmpegEncodeError, FFmpegFilterGraphError
from repcut.media.metadata import ProbeParseError

# Long enough for a 2s clip to ingest on a loaded laptop, short enough that a
# genuinely stuck job fails the suite rather than hanging it.
_LIFECYCLE_TIMEOUT_S = 120.0


def _collect_until_terminal(messages: list[dict[str, object]]) -> list[dict[str, object]]:
    """Drop the keepalives; keep the job events."""
    return [message for message in messages if message.get("type") != "ping"]


async def test_an_ingest_reports_queued_running_then_succeeded(
    api: Harness,
    make_clip: Callable[..., Path],
    upload_clip: Callable[..., Awaitable[httpx.Response]],
) -> None:
    """The whole observable lifecycle, in order, with a step name throughout.

    Asserted as a *subsequence* rather than an exact list: the number of running
    ticks depends on how fast FFmpeg gets through the clip, and pinning it would
    make the test a measurement of this laptop's speed.
    """
    source = make_clip("clip.mp4", seconds=2.0)
    project = await api.client.post("/projects", json={"name": "session"})
    project_id = project.json()["id"]

    observed: list[JobEvent] = []
    with api.queue.subscribe() as events:
        finalized = await upload_clip(project_id, source)
        assert finalized.status_code == 200
        job_id = finalized.json()["job_id"]
        assert job_id is not None

        async with asyncio.timeout(_LIFECYCLE_TIMEOUT_S):
            while True:
                event = await events.get()
                if event.job_id != job_id:
                    continue
                observed.append(event)
                if event.status in (JobStatus.SUCCEEDED, JobStatus.FAILED):
                    break

    statuses = [event.status for event in observed]
    assert statuses[0] is JobStatus.QUEUED
    assert JobStatus.RUNNING in statuses
    assert statuses[-1] is JobStatus.SUCCEEDED

    running = [event for event in observed if event.status is JobStatus.RUNNING]
    assert running, "a job that never reported running is a spinner with no information"
    assert all(event.step for event in running if event.progress > 0), "every tick names a step"

    progress = [event.progress for event in observed]
    assert progress == sorted(progress), f"progress went backwards: {progress}"
    assert observed[-1].progress == 1.0
    assert observed[-1].error is None


async def test_a_failing_job_reports_a_cause_and_no_traceback(api: Harness) -> None:
    """A forced failure ends in ``failed`` with a sentence a person can read."""
    api.queue.handlers["exploding"] = _explode

    observed: list[JobEvent] = []
    with api.queue.subscribe() as events:
        job_id = await api.queue.enqueue("exploding")
        async with asyncio.timeout(_LIFECYCLE_TIMEOUT_S):
            while True:
                event = await events.get()
                if event.job_id != job_id:
                    continue
                observed.append(event)
                if event.status is JobStatus.FAILED:
                    break

    failure = observed[-1]
    assert failure.status is JobStatus.FAILED
    assert failure.error == "the drive holding the media store ran out of space"
    assert "Traceback" not in (failure.error or "")
    assert "\n" not in (failure.error or "")

    stored = await api.client.get(f"/jobs/{job_id}")
    assert stored.json()["status"] == "failed"
    assert stored.json()["error"] == failure.error


async def _explode(context: object) -> None:
    """A handler that fails the way FFmpeg fails."""
    raise FFmpegEncodeError("the drive holding the media store ran out of space", "ENOSPC")


async def test_an_unknown_job_type_fails_rather_than_hanging(api: Harness) -> None:
    """A row with no handler must not sit in ``running`` forever."""
    job_id = await api.queue.enqueue("no-such-type")
    await api.queue.drain()

    response = await api.client.get(f"/jobs/{job_id}")

    assert response.json()["status"] == "failed"
    assert "not supported" in response.json()["error"]


def _scratch_app(settings: Settings) -> FastAPI:
    """The real routers on a lifespan pointed at a scratch ``$DATA_DIR``.

    Not ``repcut.main.app``: ``TestClient`` runs whatever lifespan the app it is
    given declares, and the real one resolves the real settings - so the socket
    test would build its queue against the developer's own database before the
    test could redirect it.
    """

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        await start_engine(application, settings)
        yield
        await stop_engine(application)

    scratch = FastAPI(lifespan=lifespan)
    install_error_handler(scratch)
    scratch.include_router(projects_api.router)
    scratch.include_router(uploads_api.router)
    scratch.include_router(jobs_api.router)
    return scratch


def test_the_socket_reports_a_whole_ingest_lifecycle(
    tmp_path: Path, make_clip: Callable[..., Path]
) -> None:
    """Criterion 9 through a real websocket handshake and a real ingest.

    Synchronous because Starlette's ``TestClient`` runs the app on its own event
    loop, and driving it from an async test would nest two loops. The upload goes
    over HTTP on the same client while the socket is open, which is exactly the
    shape the browser uses.
    """
    source = make_clip("clip.mp4", seconds=1.0)
    payload = source.read_bytes()
    settings = Settings(
        data_dir=tmp_path / "data",
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'ws.db').as_posix()}",
    )

    with (
        TestClient(_scratch_app(settings), base_url="http://localhost") as client,
        client.websocket_connect("/ws/jobs") as socket,
    ):
        project_id = client.post("/projects", json={"name": "session"}).json()["id"]
        opened = client.post(
            f"/projects/{project_id}/uploads",
            json={
                "display_name": "clip.mp4",
                "size_bytes": len(payload),
                "chunk_size_bytes": len(payload),
            },
        ).json()
        client.put(f"/uploads/{opened['id']}/chunk", params={"offset": 0}, content=payload)
        job_id = client.post(f"/uploads/{opened['id']}/finalize").json()["job_id"]

        seen: list[dict[str, object]] = []
        deadline = time.monotonic() + _LIFECYCLE_TIMEOUT_S
        while time.monotonic() < deadline:
            message = socket.receive_json()
            if message.get("type") == "ping" or message.get("job_id") != job_id:
                continue
            seen.append(message)
            if message.get("status") in (JobStatus.SUCCEEDED.value, JobStatus.FAILED.value):
                break

    events = _collect_until_terminal(seen)
    assert events, "the socket delivered no events for the job"
    assert events[-1]["status"] == JobStatus.SUCCEEDED.value, events[-1].get("error")
    assert events[-1]["progress"] == 1.0

    running = [event for event in events if event["status"] == JobStatus.RUNNING.value]
    assert running, "no running event: the UI would have a spinner and nothing else"
    assert any(event["step"] for event in running), "no step name was ever reported"

    percentages = [float(event["progress"]) for event in events]  # type: ignore[arg-type]
    assert percentages == sorted(percentages), f"progress went backwards: {percentages}"


# --- the reporter's own guarantees ------------------------------------------


def _reporter(api: Harness) -> ProgressReporter:
    record = JobRecord(id="job", job_type="ingest", project_id=None, sha256=None)
    return ProgressReporter(api.queue, record)


async def test_progress_never_goes_backwards(api: Harness) -> None:
    """A bar that jumps back reads as a restart to the person watching it."""
    reporter = _reporter(api)
    await reporter.step("encoding", 0.4, until=0.9)
    reporter.fraction(0.8)
    high = reporter.progress

    reporter.fraction(0.1)

    assert high == pytest.approx(0.8)
    assert reporter.progress == high


async def test_a_later_step_cannot_lower_the_bar(api: Harness) -> None:
    """Steps are declared with a position; a mis-ordered one must not rewind."""
    reporter = _reporter(api)
    await reporter.step("encoding", 0.9)

    await reporter.step("a step someone put in the wrong place", 0.2)

    assert reporter.progress == pytest.approx(0.9)


async def test_progress_is_clamped_to_one(api: Harness) -> None:
    """FFmpeg overshoots its own declared duration on the final packet."""
    reporter = _reporter(api)
    await reporter.step("encoding", 0.0, until=1.0)

    reporter.fraction(1.4)

    assert reporter.progress == 1.0


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            FFmpegFilterGraphError("the video filter chain was rejected", "detail"),
            "the video filter chain was rejected",
        ),
        (ProbeParseError("this file contains no video track"), "this file contains no video track"),
        (OSError(28, "No space left on device"), "the media store could not be read or written"),
        (
            ValueError("an index was out of range"),
            "this job failed unexpectedly - the engine log has the details",
        ),
    ],
)
def test_every_failure_becomes_a_readable_cause(error: BaseException, expected: str) -> None:
    """Including the one nobody anticipated, which is the point.

    A bug still reaches the user as a sentence; the traceback goes to the log,
    where it is useful, rather than to the UI, where `.claude/rules/code-style.md`
    forbids it.
    """
    described = describe_failure(error)

    assert described == expected
    assert "Traceback" not in described
    assert "\n" not in described


async def test_an_interrupted_job_is_requeued_on_start(api: Harness) -> None:
    """A restart re-runs what a kill interrupted, rather than stranding it.

    Ingest is a pure function of (source bytes, recipe), so re-running is safe -
    which is what lets the queue prefer re-running to failing.
    """
    async with api.session_factory() as session:
        stranded = Job(job_type="no-such-type", status=JobStatus.RUNNING, progress=0.6)
        session.add(stranded)
        await session.commit()
        job_id = stranded.id

    restarted = JobQueue(settings=api.settings, session_factory=api.session_factory, handlers={})
    await restarted.start()
    await restarted.drain()
    await restarted.stop()

    async with api.session_factory() as session:
        job = await session.get(Job, job_id)

    assert job is not None
    assert job.status is JobStatus.FAILED, "it was picked up again rather than left running"


async def test_cancelling_a_running_job_ends_it_as_cancelled(api: Harness) -> None:
    """Cancel reaches a job already inside its handler.

    `.claude/rules/frontend-and-licensing.md` requires cancel on every long job,
    and the long job here is an FFmpeg encode: without this the only way out of a
    render that hits the timeout is restarting the engine.
    """
    started = asyncio.Event()

    async def _never_finishes(context: object) -> None:
        started.set()
        await asyncio.sleep(3600)

    api.queue.handlers["never-finishes"] = _never_finishes

    with api.queue.subscribe() as events:
        job_id = await api.queue.enqueue("never-finishes")
        async with asyncio.timeout(_LIFECYCLE_TIMEOUT_S):
            await started.wait()

            response = await api.client.post(f"/jobs/{job_id}/cancel")
            assert response.status_code == 200

            while True:
                event = await events.get()
                if event.job_id == job_id and event.status is JobStatus.CANCELLED:
                    break

    stored = await api.client.get(f"/jobs/{job_id}")
    assert stored.json()["status"] == "cancelled"
    assert stored.json()["error"] is None, "a cancellation is not a failure"


async def test_cancelling_a_queued_job_means_it_never_runs(api: Harness) -> None:
    """A job cancelled while waiting must not start when its turn comes.

    The worker is serial, so occupying it with a held job is what keeps the
    second one queued long enough to cancel - and is also the case that matters:
    a user cancels the fourth clip of a batch, not the one being encoded.
    """
    release = asyncio.Event()
    ran = False

    async def _held(context: object) -> None:
        await release.wait()

    async def _records_that_it_ran(context: object) -> None:
        nonlocal ran
        ran = True

    api.queue.handlers["held"] = _held
    api.queue.handlers["records"] = _records_that_it_ran

    await api.queue.enqueue("held")
    waiting_id = await api.queue.enqueue("records")

    response = await api.client.post(f"/jobs/{waiting_id}/cancel")
    assert response.status_code == 200

    release.set()
    async with asyncio.timeout(_LIFECYCLE_TIMEOUT_S):
        await api.queue.drain()

    stored = await api.client.get(f"/jobs/{waiting_id}")
    assert stored.json()["status"] == "cancelled"
    assert ran is False, "the worker ran a job that had been cancelled"


async def test_cancelling_a_finished_job_changes_nothing(api: Harness) -> None:
    """Idempotent: a client that lost the response must be able to retry."""
    api.queue.handlers["exploding"] = _explode
    job_id = await api.queue.enqueue("exploding")
    await api.queue.drain()

    first = await api.client.post(f"/jobs/{job_id}/cancel")
    second = await api.client.post(f"/jobs/{job_id}/cancel")

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["status"] == "failed"
    assert second.json()["error"] == first.json()["error"]


async def test_cancelling_an_unknown_job_is_a_named_error(api: Harness) -> None:
    """Not a 500 and not a silent 200 - the UI branches on the code."""
    response = await api.client.post("/jobs/00000000-0000-0000-0000-000000000000/cancel")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "job_not_found"


def test_the_socket_payload_carries_the_fields_the_ui_parses() -> None:
    """Pins the wire contract of ``/ws/jobs`` from the engine's side.

    The UI drops any frame that fails its Zod parse, because a frame that is not
    a job event is a keepalive. That is the right behaviour and it is also why a
    renamed field here would surface as *no jobs ever appearing* rather than as
    an error - the failure would be invisible on both sides.

    The other half of this contract is ``jobEventSchema`` in
    ``ui/lib/api/schemas.ts``; the same field list is asserted there against a
    payload built by this model. Rename a field and one of the two fails.
    """
    assert set(JobEvent.model_fields) == {
        "job_id",
        "job_type",
        "status",
        "progress",
        "step",
        "error",
        "project_id",
        "sha256",
        "updated_at",
    }


async def test_a_cancel_between_running_and_the_task_still_stops_the_job(
    api: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The window that made Cancel a no-op: running row, no task to interrupt.

    ``_mark_running`` commits ``running`` before ``_execute`` creates the handler
    task. A cancel arriving in between finds no task and no queued row, so the
    first version of this feature did nothing, reported success, and let the job
    run to completion with the user watching a Cancel they had already pressed.

    Found by the 2GB upload test, whose ingest job carried on probing after being
    cancelled. Reproduced here by holding ``_mark_running`` open, which is the
    only way to make the window wide enough to aim at.
    """
    marked = asyncio.Event()
    release = asyncio.Event()
    ran = False

    original = api.queue._mark_running

    async def _hold_open(job_id: str) -> JobRecord | None:
        record = await original(job_id)
        marked.set()
        await release.wait()
        return record

    async def _records_that_it_ran(context: object) -> None:
        nonlocal ran
        ran = True

    monkeypatch.setattr(api.queue, "_mark_running", _hold_open)
    api.queue.handlers["records"] = _records_that_it_ran

    job_id = await api.queue.enqueue("records")
    async with asyncio.timeout(_LIFECYCLE_TIMEOUT_S):
        await marked.wait()

        cancelled = await api.queue.cancel(job_id)
        assert cancelled is True

        release.set()
        await api.queue.drain()

    assert ran is False, "the handler ran despite a cancel the engine accepted"
    stored = await api.client.get(f"/jobs/{job_id}")
    assert stored.json()["status"] == "cancelled"
