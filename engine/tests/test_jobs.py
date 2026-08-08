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
