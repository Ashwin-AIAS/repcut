"""Background jobs, and the event stream the UI watches them through.

One in-process worker, one asyncio queue, one row per job in ``jobs``. There is
no broker and no second process: this is one person's laptop, and a queue that
survives a restart is a table, not a service (P5).

Two properties the UI depends on, both enforced here rather than by convention:

- **Progress never goes backwards.** `.claude/rules/frontend-and-licensing.md`
  requires a percentage and a current step, and a bar that jumps back reads as a
  restart. ``ProgressReporter`` clamps to the highest value already reported, so
  a step that recomputes its fraction cannot un-inform the user.
- **A failure has a cause, never a traceback.** Handlers are run as child tasks
  and their outcome is read with ``Task.exception()`` rather than caught. That is
  why there is no ``except`` in the worker loop: a broad catch would swallow
  failures nobody thought about, and no catch at all would let one bad job kill
  the worker and silently strand every job after it. Inspecting the task does
  neither - `.claude/rules/code-style.md`.

The database holds step boundaries; the websocket gets every FFmpeg progress
tick. The split is deliberate: a row rewritten twice a second is contention for
state that only matters after a crash, and after a crash the job restarts from
its beginning anyway.
"""

import asyncio
from collections.abc import AsyncIterator, Callable, Coroutine, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from repcut.config import Settings
from repcut.db.models import Job, JobStatus, utcnow
from repcut.logging import get_logger
from repcut.media.ffmpeg_builder import FFmpegError
from repcut.media.metadata import ProbeParseError

logger = get_logger(__name__)

# Bounded, so one stalled websocket cannot grow without limit. On overflow the
# oldest event is dropped rather than the newest: progress ticks are disposable,
# and the terminal event is the one a client cannot do without.
SUBSCRIBER_QUEUE_SIZE = 256

_ACTIVE_STATUSES = (JobStatus.QUEUED, JobStatus.RUNNING)

_UNEXPECTED_CAUSE = "this job failed unexpectedly - the engine log has the details"
_STORE_IO_CAUSE = "the media store could not be read or written"


class JobFailedError(Exception):
    """A job failure whose message is already written for a person to read.

    Handlers raise this (or a subclass) when they know something the generic
    classifier does not. It is checked *before* ``OSError`` in
    ``describe_failure`` because a domain error that also happens to be an
    ``OSError`` - "this clip's file is missing from the media library" is a
    ``FileNotFoundError`` - would otherwise be flattened into the generic disk
    message and lose the only part that told the user what to do.
    """


class JobEvent(BaseModel):
    """One observation of a job. The websocket payload and nothing more.

    Carries no path and no filename: a client that needs the artifact asks for
    it by id. ``.claude/rules/secrets.md`` - a stored path contains the OS
    username on this machine.
    """

    job_id: str
    job_type: str
    status: JobStatus
    progress: float
    step: str | None
    error: str | None
    project_id: str | None
    sha256: str | None
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class JobRecord:
    """The parts of a job row a handler needs, detached from any session."""

    id: str
    job_type: str
    project_id: str | None
    sha256: str | None


class ProgressReporter:
    """Reports one job's progress, monotonically.

    A step spans a range of the overall bar - ``step("encoding proxy", 0.55,
    until=0.95)`` - and ``fraction()`` maps FFmpeg's own 0-1 output into it. That
    is what turns three coarse steps into a bar that actually moves during the
    long one.
    """

    def __init__(self, queue: "JobQueue", record: JobRecord) -> None:
        self._queue = queue
        self._record = record
        self._progress = 0.0
        self._step: str | None = None
        self._span_start = 0.0
        self._span_end = 1.0

    @property
    def progress(self) -> float:
        """The highest fraction reported so far."""
        return self._progress

    @property
    def step_name(self) -> str | None:
        """The current step, or None before the first one."""
        return self._step

    async def step(self, name: str, at: float, *, until: float | None = None) -> None:
        """Enter a named step. Persists the boundary and publishes it."""
        self._step = name
        self._span_start = at
        self._span_end = until if until is not None else at
        self._advance(at)
        await self._queue.record_progress(self._record, self._step, self._progress)

    def fraction(self, within_step: float) -> None:
        """Sub-step progress from FFmpeg. Publishes only; never touches the DB.

        Synchronous because it is called from inside the subprocess stdout drain
        loop, where awaiting would let the pipe fill and stall the encode this
        is reporting on.
        """
        span = self._span_end - self._span_start
        self._advance(self._span_start + within_step * span)
        self._queue.publish(
            self._queue.event_for(self._record, JobStatus.RUNNING, self._progress, self._step, None)
        )

    def _advance(self, value: float) -> None:
        """Clamp into 0-1 and never below what was already reported."""
        self._progress = min(1.0, max(self._progress, value))


@dataclass(frozen=True, slots=True)
class JobContext:
    """Everything a handler is given. No globals, so a handler is testable."""

    record: JobRecord
    settings: Settings
    session_factory: async_sessionmaker[AsyncSession]
    report: ProgressReporter


# A coroutine function, not merely an awaitable: the worker runs each handler as
# its own task so it can read the outcome with Task.exception() instead of
# catching it, and create_task takes a coroutine.
JobHandler = Callable[[JobContext], Coroutine[Any, Any, None]]


def describe_failure(error: BaseException) -> str:
    """A cause the UI can render, for any way a job can fail.

    The named classes carry their own human-readable cause. Anything else is a
    bug rather than a condition, so the *log* gets the exception and the user
    gets a sentence - never a traceback (`.claude/rules/code-style.md`).
    """
    if isinstance(error, JobFailedError):
        return str(error)
    if isinstance(error, FFmpegError):
        return error.cause
    if isinstance(error, ProbeParseError):
        return str(error)
    if isinstance(error, OSError):
        # Named: disk full, a file removed underneath the job, a permission
        # change. errno is in the log; the path never is.
        logger.warning("job_store_io_error", errno=error.errno)
        return _STORE_IO_CAUSE
    logger.error("job_failed_unexpectedly", error_type=type(error).__name__, exc_info=error)
    return _UNEXPECTED_CAUSE


@dataclass
class JobQueue:
    """The single in-process worker and its subscribers."""

    settings: Settings
    session_factory: async_sessionmaker[AsyncSession]
    handlers: dict[str, JobHandler]
    _pending: asyncio.Queue[str] = field(default_factory=asyncio.Queue, init=False)
    _subscribers: set[asyncio.Queue[JobEvent]] = field(default_factory=set, init=False)
    _worker_task: asyncio.Task[None] | None = field(default=None, init=False)
    # The handler task of whatever is running right now, so ``cancel`` has
    # something to act on. At most one entry - the worker is serial.
    _running: dict[str, asyncio.Task[None]] = field(default_factory=dict, init=False)
    # Cancels that arrived in the window between a job's row saying ``running``
    # and its handler task existing. See ``cancel``.
    _cancel_requested: set[str] = field(default_factory=set, init=False)

    # --- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        """Requeue anything a previous run left mid-flight, then take work.

        Jobs found ``queued`` or ``running`` are re-queued rather than failed:
        every job type here is a pure function of (source bytes, recipe), so
        re-running one is safe and produces the same artifact. That is the
        idempotence `.claude/rules/code-style.md` asks of every script.
        """
        await self._requeue_interrupted()
        self._worker_task = asyncio.create_task(self._work(), name="repcut-job-worker")

    async def stop(self) -> None:
        """Stop taking work and let the in-flight job unwind."""
        if self._worker_task is None:
            return
        self._worker_task.cancel()
        with suppress(asyncio.CancelledError):
            await self._worker_task
        self._worker_task = None

    # --- producing ---------------------------------------------------------

    async def enqueue(
        self, job_type: str, *, project_id: str | None = None, sha256: str | None = None
    ) -> str:
        """Persist a queued job and hand it to the worker. Returns its id."""
        job = Job(job_type=job_type, project_id=project_id, sha256=sha256)
        async with self.session_factory() as session:
            session.add(job)
            await session.commit()

        record = JobRecord(
            id=job.id, job_type=job.job_type, project_id=job.project_id, sha256=job.sha256
        )
        self.publish(self.event_for(record, JobStatus.QUEUED, 0.0, None, None))
        self._pending.put_nowait(job.id)
        logger.info("job_enqueued", job_id=job.id, job_type=job_type)
        return job.id

    async def cancel(self, job_id: str) -> bool:
        """Stop a job that is running or still waiting. False if it is already over.

        `.claude/rules/frontend-and-licensing.md` requires cancel on every long
        job, and an FFmpeg encode is the longest thing here: without this, a job
        that hits the render timeout gives the user nothing to do but restart the
        engine.

        Three paths, because a job is cancellable in three different shapes:

        - **Running, with a task.** Cancel the handler task. ``_execute`` already
          reads ``Task.cancelled()`` and writes the terminal ``cancelled`` row,
          so the status transition stays in the one place that owns it.
        - **Queued.** A conditional UPDATE, not a read-then-write: the worker may
          be popping this exact id concurrently, and ``WHERE status = 'queued'``
          makes the database decide which of the two happened. Losing that race
          is harmless - the worker's ``_mark_running`` refuses a row that is no
          longer queued.
        - **Running, with no task yet.** ``_mark_running`` commits ``running``
          before ``_execute`` creates the handler task, and a cancel landing in
          that window found neither a task nor a queued row - so it did nothing,
          reported success, and the job ran to completion with the user watching
          a Cancel they had already pressed. Measured, not theorised: it is what
          the 2GB upload test hit. The request is recorded and ``_execute``
          honours it the moment the task exists.
        """
        task = self._running.get(job_id)
        if task is not None:
            task.cancel()
            return True

        async with self.session_factory() as session:
            # RETURNING rather than rowcount: it is the typed result, and it says
            # whether *this* statement was the one that matched.
            claimed = (
                await session.execute(
                    update(Job)
                    .where(Job.id == job_id, Job.status == JobStatus.QUEUED)
                    .values(status=JobStatus.CANCELLED, finished_at=utcnow())
                    .returning(Job.id)
                )
            ).scalar_one_or_none()
            await session.commit()
            job = await session.get(Job, job_id)
            if claimed is None:
                if job is not None and job.status is JobStatus.RUNNING:
                    self._cancel_requested.add(job_id)
                    logger.info("job_cancel_requested_before_task", job_id=job_id)
                    return True
                return False
            if job is None:  # pragma: no cover - the UPDATE just matched it
                return False
            record = JobRecord(
                id=job.id, job_type=job.job_type, project_id=job.project_id, sha256=job.sha256
            )
            progress, step = job.progress, job.step

        logger.info("job_cancelled_before_start", job_id=job_id)
        self.publish(self.event_for(record, JobStatus.CANCELLED, progress, step, None))
        return True

    # --- subscribing -------------------------------------------------------

    @contextmanager
    def subscribe(self) -> Iterator[asyncio.Queue[JobEvent]]:
        """A bounded queue receiving every event while the block is open."""
        queue: asyncio.Queue[JobEvent] = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_SIZE)
        self._subscribers.add(queue)
        try:
            yield queue
        finally:
            self._subscribers.discard(queue)

    def publish(self, event: JobEvent) -> None:
        """Fan an event out. Never blocks, never waits on a slow subscriber."""
        for queue in list(self._subscribers):
            if queue.full():
                with suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            with suppress(asyncio.QueueFull):
                queue.put_nowait(event)

    def event_for(
        self,
        record: JobRecord,
        status: JobStatus,
        progress: float,
        step: str | None,
        error: str | None,
    ) -> JobEvent:
        """Build the wire payload for one observation of a job."""
        return JobEvent(
            job_id=record.id,
            job_type=record.job_type,
            status=status,
            progress=progress,
            step=step,
            error=error,
            project_id=record.project_id,
            sha256=record.sha256,
            updated_at=utcnow(),
        )

    # --- consuming ---------------------------------------------------------

    async def _work(self) -> None:
        """Take one job at a time, forever.

        Serial on purpose: every job here is an FFmpeg encode, and two at once on
        a four-core laptop finish no sooner while making both progress bars lie.
        """
        while True:
            job_id = await self._pending.get()
            task = asyncio.create_task(self._execute(job_id), name=f"repcut-job-{job_id}")
            try:
                await asyncio.wait({task})
            finally:
                # Reached when the worker itself is cancelled at shutdown. The
                # child does not stop on its own, and an orphaned encode would
                # outlive the engine.
                if not task.done():
                    task.cancel()
                    # Awaited, not merely cancelled: the job is part-way through
                    # a database write, and letting the loop close underneath it
                    # is how shutdown produced a torn connection and a stack
                    # trace instead of a clean stop.
                    with suppress(asyncio.CancelledError):
                        await task
                self._pending.task_done()

            if not task.cancelled() and (error := task.exception()) is not None:
                # The job's own failures are handled inside _execute; reaching
                # here means _execute's own bookkeeping failed, which must not
                # take the worker down with it.
                logger.error(
                    "job_bookkeeping_failed",
                    job_id=job_id,
                    error_type=type(error).__name__,
                    exc_info=error,
                )

    async def _execute(self, job_id: str) -> None:
        """Run one job and record how it ended."""
        record = await self._mark_running(job_id)
        if record is None:
            return

        handler = self.handlers.get(record.job_type)
        if handler is None:
            # Reachable only by a row from a newer build of the engine, or a
            # handler someone forgot to register. Fail the job with a cause
            # rather than leaving it running forever.
            await self._finish(
                record, JobStatus.FAILED, 0.0, None, "this job type is not supported by the engine"
            )
            return

        reporter = ProgressReporter(self, record)
        context = JobContext(
            record=record,
            settings=self.settings,
            session_factory=self.session_factory,
            report=reporter,
        )
        task = asyncio.create_task(handler(context), name=f"repcut-handler-{job_id}")
        self._running[job_id] = task
        # A cancel that arrived while this job was marked running but had no task
        # to interrupt. Applied before the first step runs, so the handler never
        # starts rather than being stopped part-way.
        if job_id in self._cancel_requested:
            task.cancel()
        try:
            await asyncio.wait({task})
        finally:
            self._running.pop(job_id, None)
            self._cancel_requested.discard(job_id)
            if not task.done():
                task.cancel()

        if task.cancelled():
            await self._finish(record, JobStatus.CANCELLED, reporter.progress, reporter.step_name)
            return

        error = task.exception()
        if error is None:
            await self._finish(record, JobStatus.SUCCEEDED, 1.0, reporter.step_name)
        else:
            await self._finish(
                record,
                JobStatus.FAILED,
                reporter.progress,
                reporter.step_name,
                describe_failure(error),
            )

    async def _mark_running(self, job_id: str) -> JobRecord | None:
        """Move a job to running, or return None if it is no longer runnable."""
        async with self.session_factory() as session:
            job = await session.get(Job, job_id)
            if job is None or job.status is not JobStatus.QUEUED:
                logger.info("job_skipped", job_id=job_id)
                return None
            job.status = JobStatus.RUNNING
            job.started_at = utcnow()
            job.progress = 0.0
            await session.commit()
            record = JobRecord(
                id=job.id, job_type=job.job_type, project_id=job.project_id, sha256=job.sha256
            )

        self.publish(self.event_for(record, JobStatus.RUNNING, 0.0, None, None))
        return record

    async def record_progress(self, record: JobRecord, step: str | None, progress: float) -> None:
        """Persist a step boundary and publish it."""
        async with self.session_factory() as session:
            job = await session.get(Job, record.id)
            if job is not None:
                job.step = step
                job.progress = progress
                await session.commit()
        self.publish(self.event_for(record, JobStatus.RUNNING, progress, step, None))

    async def _finish(
        self,
        record: JobRecord,
        status: JobStatus,
        progress: float,
        step: str | None,
        error: str | None = None,
    ) -> None:
        """Write a terminal status and publish it."""
        async with self.session_factory() as session:
            job = await session.get(Job, record.id)
            if job is not None:
                job.status = status
                job.progress = progress
                job.step = step
                job.error = error
                job.finished_at = utcnow()
                await session.commit()

        logger.info("job_finished", job_id=record.id, status=status.value, failed=error is not None)
        self.publish(self.event_for(record, status, progress, step, error))

    async def _requeue_interrupted(self) -> None:
        """Reset jobs a previous process left queued or running."""
        async with self.session_factory() as session:
            interrupted = list(
                (await session.execute(select(Job).where(Job.status.in_(_ACTIVE_STATUSES))))
                .scalars()
                .all()
            )
            for job in interrupted:
                job.status = JobStatus.QUEUED
                job.progress = 0.0
                job.step = None
                job.started_at = None
            await session.commit()
            ids = [job.id for job in interrupted]

        for job_id in ids:
            self._pending.put_nowait(job_id)
        if ids:
            logger.info("jobs_requeued", count=len(ids))

    async def drain(self) -> None:
        """Wait until every queued job has finished. Tests and the gate only."""
        await self._pending.join()


async def iterate_events(queue: asyncio.Queue[JobEvent]) -> AsyncIterator[JobEvent]:
    """Yield events from a subscription until the caller stops asking."""
    while True:
        yield await queue.get()


__all__ = [
    "SUBSCRIBER_QUEUE_SIZE",
    "JobContext",
    "JobEvent",
    "JobFailedError",
    "JobHandler",
    "JobQueue",
    "JobRecord",
    "ProgressReporter",
    "describe_failure",
    "iterate_events",
]
