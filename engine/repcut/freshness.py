"""Whether a clip's derived output is current, and the one place that brings it up to date.

A version bump changes a key; it never touches a file (amendment 004). So after
Prompt 04's proxy and scene bumps, a clip ingested under v1 simply has no v2
proxy and no v2 scenes until something asks for them. This module is that
something, and it is deliberately **lazy** (amendment 012): it runs when a clip
is opened or uploaded again, never as a startup sweep. A sweep would re-analyse
the whole library at once and spend the Gemini day doing it.

Superseded files stay on disk; orphan GC is Prompt 12's.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repcut.analysis.cache import get_rate_limiter
from repcut.analysis.params import SCENE_PARAMS_VERSION
from repcut.analysis.pipeline import ANALYSIS_JOB_TYPE, GEMINI_PROMPT_VERSION
from repcut.config import Settings
from repcut.db.models import DerivedArtifact, GeminiSceneCache, Job, JobStatus, Scene
from repcut.jobs import JobQueue
from repcut.media.artifacts import PARAMS_VERSION, ArtifactKind
from repcut.media.ingest import INGEST_JOB_TYPE

_ACTIVE = (JobStatus.QUEUED, JobStatus.RUNNING)


async def artifacts_current(session: AsyncSession, sha256: str) -> bool:
    """Whether every artifact kind already exists at its current version.

    When it does, a duplicate upload enqueues no ingest at all - the measurable
    half of "a duplicate re-encodes no proxy".
    """
    statement = select(DerivedArtifact.artifact_kind, DerivedArtifact.params_version).where(
        DerivedArtifact.sha256 == sha256
    )
    current = {kind.value: version for kind, version in PARAMS_VERSION.items()}
    present = {
        kind
        for kind, version in (await session.execute(statement)).all()
        if current.get(kind) == version
    }
    return all(kind.value in present for kind in ArtifactKind)


async def analysis_current(session: AsyncSession, sha256: str, settings: Settings) -> bool:
    """Whether this blob's analysis has nothing left that a new run could add.

    Scenes at the current detector version, and - when a run could actually
    reach Gemini - a cached answer for every one of them at the current prompt
    version. The second half is what makes a degraded scene (429, offline) try
    again: ``cache.py`` writes no row for a scene Gemini never answered, and
    without this check the scenes existing would read as "done" forever, so the
    next open would never ask.

    With no key, or with today's budget already spent, a new run could only
    degrade again without sending anything, so a missing answer does not count
    against the blob - otherwise every open would enqueue a job that does
    nothing. ``run_analysis`` is idempotent per stage, so a run that is enqueued
    redoes none of the detection, sampling or measuring already done.
    """
    statement = (
        select(Scene.id)
        .where(Scene.sha256 == sha256, Scene.detector_params_version == SCENE_PARAMS_VERSION)
        .limit(1)
    )
    if (await session.execute(statement)).first() is None:
        return False
    if not settings.gemini_api_key_set or not get_rate_limiter(settings).has_budget_today():
        return True
    unanswered = (
        select(Scene.id)
        .outerjoin(
            GeminiSceneCache,
            (GeminiSceneCache.scene_id == Scene.id)
            & (GeminiSceneCache.gemini_prompt_version == GEMINI_PROMPT_VERSION),
        )
        .where(
            Scene.sha256 == sha256,
            Scene.detector_params_version == SCENE_PARAMS_VERSION,
            GeminiSceneCache.id.is_(None),
        )
        .limit(1)
    )
    return (await session.execute(unanswered)).first() is None


async def has_active_job(session: AsyncSession, sha256: str) -> bool:
    """Whether an ingest or analysis job for this blob is queued or running.

    The reason a second open enqueues nothing while the first open's work is
    still in flight: the queued job will produce exactly what a new one would.
    """
    statement = (
        select(Job.id)
        .where(
            Job.sha256 == sha256,
            Job.job_type.in_((INGEST_JOB_TYPE, ANALYSIS_JOB_TYPE)),
            Job.status.in_(_ACTIVE),
        )
        .limit(1)
    )
    return (await session.execute(statement)).first() is not None


async def ensure_current(
    session: AsyncSession, queue: JobQueue, *, project_id: str, sha256: str
) -> list[str]:
    """Enqueue whatever this clip is missing at the current versions. Returns the job ids.

    Ingest before analysis, never the reverse: the worker runs one job at a
    time in enqueue order (`jobs.JobQueue._work`), so analysis reads the proxy
    ingest has just made. Nothing is enqueued while either is already active,
    and nothing at all for a clip that is already current - so a second open
    is always zero jobs.
    """
    if await has_active_job(session, sha256):
        return []
    enqueued: list[str] = []
    if not await artifacts_current(session, sha256):
        enqueued.append(await queue.enqueue(INGEST_JOB_TYPE, project_id=project_id, sha256=sha256))
    if not await analysis_current(session, sha256, queue.settings):
        enqueued.append(
            await queue.enqueue(ANALYSIS_JOB_TYPE, project_id=project_id, sha256=sha256)
        )
    return enqueued


__all__ = ["analysis_current", "artifacts_current", "ensure_current", "has_active_job"]
