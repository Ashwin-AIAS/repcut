"""The analysis job: scene detection, one sampled frame per scene, motion/audio
energy, and cache-first Gemini scene tagging.

Same shape as ``media.ingest.run_ingest``: one ``JobContext`` in, resumable,
per-stage progress via ``context.report.step(...)``, named exceptions, no bare
``except Exception:``. This module is the only place that ties together four
otherwise-pure packages (``scenes.py``, ``sampler.py``, ``motion.py``,
``cache.py``) that know nothing about jobs, sessions or each other - wiring
their inputs and outputs onto a ``Scene`` row is this module's whole job.

Runs once per *blob* (``context.record.sha256``), same as ingest, for the same
reason: a scene, a sampled frame and a motion/audio measurement are each a pure
function of (source or proxy bytes, recipe), so a clip re-added to a second
project reuses everything this job already produced.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repcut.analysis.cache import analyze_scene_cached
from repcut.analysis.motion import compute_scene_energy
from repcut.analysis.params import FRAME_PARAMS_VERSION, SCENE_PARAMS_VERSION
from repcut.analysis.sampler import pick_frame
from repcut.analysis.scenes import detect_scenes
from repcut.analysis.types import SceneBoundary
from repcut.db.models import DerivedArtifact, MediaBlob, Scene
from repcut.jobs import JobContext, JobFailedError
from repcut.logging import get_logger
from repcut.media.artifacts import PARAMS_VERSION, ArtifactKind
from repcut.media.ingest import BlobMissingError
from repcut.media.store import absolute, derived_path

logger = get_logger(__name__)

ANALYSIS_JOB_TYPE = "analysis"

# Kept as a plain slug, not an `ArtifactKind` member: amendment 008 resolution
# 2 is explicit that a sampled frame is not a `derived_artifacts` row -
# `Scene.sampled_frame_path` carries it instead. This is only the directory
# name `media/store.derived_path` writes it under, same shape as every other
# `artifact_kind` slug that table itself uses.
SAMPLED_FRAME_ARTIFACT_KIND = "sampled_frame"

# Bumping this deliberately invalidates every cached Gemini answer - separate
# from SCENE_PARAMS_VERSION/FRAME_PARAMS_VERSION, which invalidate boundaries
# and the frame itself. Owned here, not in `analysis/params.py`, because this
# module is the only caller of `cache.analyze_scene_cached`.
GEMINI_PROMPT_VERSION = 1

# Step boundaries on the overall bar. Detection and persistence are one-shot
# and cheap against an already-CFR proxy; sampling and the Gemini calls are
# the two steps whose duration scales with scene count, so they own the widest
# spans - same reasoning as `ingest.py`'s own split between probe/strip/proxy.
_READ_AT = 0.02
_DETECT_AT = 0.08
_SAMPLE_AT = 0.15
_SAMPLE_UNTIL = 0.45
_ENERGY_AT = 0.45
_ENERGY_UNTIL = 0.60
_GEMINI_AT = 0.60
_GEMINI_UNTIL = 0.98


class IngestIncompleteError(JobFailedError):
    """This clip has not finished ingest yet - analysis needs its preview proxy.

    Reachable if analysis is retried or queued before its own ingest job has
    succeeded (a race the queue's FIFO ordering already prevents in the normal
    upload path - see ``api/uploads.py`` - but a direct ``POST`` to a job or a
    hand-rolled test can still hit it), or if ingest itself failed and left the
    blob's probed properties null. Either way this is a state to retry from,
    not a crash: re-running ingest and then analysis again resolves it.
    """


@dataclass(frozen=True, slots=True)
class _SceneRecord:
    """One scene's state as this job sees it - never a live ORM row.

    Detached deliberately. This job spans many short, independently-committed
    sessions (one per write, for the same crash-resumability reason
    ``ingest.py`` commits per artifact rather than once at the end), and
    holding a ``Scene`` instance across a session boundary risks SQLAlchemy
    expiring its attributes at commit - the next read would then need the
    session that just closed, raising ``DetachedInstanceError`` far from
    anything that looks like the cause. A frozen snapshot, rebuilt after every
    write, sidesteps that outright rather than relying on ``expire_on_commit``
    being tuned a particular way.
    """

    id: str
    sequence_index: int
    start_seconds: float
    end_seconds: float
    start_frame_source: int
    end_frame_source: int
    sampled_frame_path: str | None
    motion_energy: float | None
    audio_energy: float | None

    @property
    def boundary(self) -> SceneBoundary:
        """This scene's timing, in the shape ``sampler.py``/``motion.py`` take."""
        return SceneBoundary(
            sequence_index=self.sequence_index,
            start_seconds=self.start_seconds,
            end_seconds=self.end_seconds,
            start_frame_source=self.start_frame_source,
            end_frame_source=self.end_frame_source,
        )


def _snapshot(row: Scene) -> _SceneRecord:
    """A detached copy of one persisted ``Scene`` row's fields."""
    return _SceneRecord(
        id=row.id,
        sequence_index=row.sequence_index,
        start_seconds=row.start_seconds,
        end_seconds=row.end_seconds,
        start_frame_source=row.start_frame_source,
        end_frame_source=row.end_frame_source,
        sampled_frame_path=row.sampled_frame_path,
        motion_energy=row.motion_energy,
        audio_energy=row.audio_energy,
    )


async def _load_blob(session: AsyncSession, sha256: str) -> MediaBlob:
    """Fetch the blob row, or say which invariant broke. Mirrors ``ingest._load_blob``."""
    blob = await session.get(MediaBlob, sha256)
    if blob is None:
        raise BlobMissingError("this clip is no longer in the media library")
    return blob


async def _proxy_path(session: AsyncSession, sha256: str, data_dir: Path) -> Path:
    """The ingest job's proxy artifact, or a named error if it is not there yet.

    Both the row and the file are checked, same discipline as
    ``ingest._existing_artifact`` - a row surviving a deleted file must not be
    read as "ready".
    """
    statement = select(DerivedArtifact).where(
        DerivedArtifact.sha256 == sha256,
        DerivedArtifact.artifact_kind == ArtifactKind.PROXY.value,
        DerivedArtifact.params_version == PARAMS_VERSION[ArtifactKind.PROXY],
    )
    artifact = (await session.execute(statement)).scalars().first()
    if artifact is None:
        raise IngestIncompleteError(
            "this clip's preview proxy has not been generated yet - ingest must finish first"
        )
    path = absolute(data_dir, artifact.stored_path)
    if not path.is_file():
        raise IngestIncompleteError(
            "this clip's preview proxy has not been generated yet - ingest must finish first"
        )
    return path


async def _load_scenes(session: AsyncSession, sha256: str) -> list[_SceneRecord]:
    """Every scene at the current detector version, in order, detached."""
    statement = (
        select(Scene)
        .where(Scene.sha256 == sha256, Scene.detector_params_version == SCENE_PARAMS_VERSION)
        .order_by(Scene.sequence_index)
    )
    rows = (await session.execute(statement)).scalars().all()
    return [_snapshot(row) for row in rows]


async def _detect_and_persist_scenes(
    context: JobContext, sha256: str, proxy_path: Path, fps_source: float
) -> list[_SceneRecord]:
    """Idempotent: an existing set for this key is reused; detection never repeats.

    The insert is one ``session.add`` per boundary followed by exactly one
    ``commit`` - never a commit per row - so the set lands atomically. A crash
    before that commit leaves zero rows for this key, and the next run detects
    and inserts the whole set again from scratch; a crash after it leaves the
    full set, and the next run's existence check above finds it and skips
    detection entirely. There is no state in between for a resumed run to see.
    """
    async with context.session_factory() as session:
        existing = await _load_scenes(session, sha256)
    if existing:
        logger.info("scenes_reused", scene_count=len(existing))
        return existing

    boundaries = await asyncio.to_thread(detect_scenes, proxy_path, fps_source=fps_source)

    async with context.session_factory() as session:
        # Re-checked under the write session: harmless if nothing raced this
        # (the job worker runs one job at a time), and correct if something
        # ever does - see `jobs.JobQueue._work`'s own "serial on purpose".
        already = await _load_scenes(session, sha256)
        if already:
            return already
        for boundary in boundaries:
            session.add(
                Scene(
                    sha256=sha256,
                    detector_params_version=SCENE_PARAMS_VERSION,
                    sequence_index=boundary.sequence_index,
                    start_seconds=boundary.start_seconds,
                    end_seconds=boundary.end_seconds,
                    start_frame_source=boundary.start_frame_source,
                    end_frame_source=boundary.end_frame_source,
                )
            )
        await session.commit()

    async with context.session_factory() as session:
        return await _load_scenes(session, sha256)


def _step_fraction(at: float, until: float, index: int, total: int) -> float:
    """Where scene ``index`` of ``total`` sits within ``[at, until)``."""
    return at + (until - at) * (index / max(1, total))


async def _sample_frames(
    context: JobContext, sha256: str, source: Path, scenes: list[_SceneRecord]
) -> list[_SceneRecord]:
    """One representative frame per scene, read from the SOURCE (amendment 008)."""
    data_dir = context.settings.data_dir
    total = len(scenes)
    updated: list[_SceneRecord] = []
    for index, scene in enumerate(scenes):
        stored = derived_path(
            sha256,
            SAMPLED_FRAME_ARTIFACT_KIND,
            FRAME_PARAMS_VERSION,
            f"scene_{scene.sequence_index}.jpg",
        )
        destination = absolute(data_dir, stored)
        if scene.sampled_frame_path is not None and destination.is_file():
            updated.append(scene)
            continue

        await context.report.step(
            f"sampling frame {index + 1} of {total}",
            _step_fraction(_SAMPLE_AT, _SAMPLE_UNTIL, index, total),
        )
        await pick_frame(source, scene.boundary, destination)

        async with context.session_factory() as session:
            row = await session.get(Scene, scene.id)
            if row is not None:
                row.sampled_frame_path = str(stored)
                await session.commit()
        updated.append(replace(scene, sampled_frame_path=str(stored)))
    return updated


async def _measure_energy(
    context: JobContext, proxy_path: Path, scenes: list[_SceneRecord]
) -> list[_SceneRecord]:
    """Motion and audio energy per scene, read from the PROXY (amendment 008)."""
    total = len(scenes)
    updated: list[_SceneRecord] = []
    for index, scene in enumerate(scenes):
        if scene.motion_energy is not None and scene.audio_energy is not None:
            updated.append(scene)
            continue

        await context.report.step(
            f"measuring energy for scene {index + 1} of {total}",
            _step_fraction(_ENERGY_AT, _ENERGY_UNTIL, index, total),
        )
        # Blocking (OpenCV optical flow, a synchronous FFmpeg subprocess) -
        # `motion.py`'s own docstring asks its caller to run it off the loop.
        measurement = await asyncio.to_thread(compute_scene_energy, proxy_path, scene.boundary)

        async with context.session_factory() as session:
            row = await session.get(Scene, scene.id)
            if row is not None:
                row.motion_energy = measurement.motion_energy
                row.audio_energy = measurement.audio_energy
                row.energy_score = measurement.energy_score
                await session.commit()
        updated.append(
            replace(
                scene,
                motion_energy=measurement.motion_energy,
                audio_energy=measurement.audio_energy,
            )
        )
    return updated


def _build_http_client() -> httpx.AsyncClient:
    """The real Gemini transport. Tests monkeypatch this factory directly."""
    return httpx.AsyncClient()


async def _analyze_with_gemini(
    context: JobContext, data_dir: Path, scenes: list[_SceneRecord]
) -> None:
    """Cache-first Gemini tagging per scene. Never fails the job on a null result.

    A ``None`` outcome - offline, rate-limited, or malformed after its own
    retry - is a populated pipeline with a gap (``vlm: null``), not a failure:
    ``cache.analyze_scene_cached`` already turns every reachable failure into a
    ``SceneAnalysisOutcome`` rather than an exception (`.claude/rules/
    gemini-usage.md`), so there is nothing here to catch.
    """
    total = len(scenes)
    async with _build_http_client() as client:
        for index, scene in enumerate(scenes):
            if scene.sampled_frame_path is None:
                # Sampling runs before this stage in `run_analysis`; reaching
                # here without a frame is a bug in this module's own ordering,
                # not a condition Gemini can be asked to explain.
                raise IngestIncompleteError(
                    "this scene has no sampled frame yet - sampling must run before Gemini analysis"
                )
            frame_path = absolute(data_dir, scene.sampled_frame_path)

            # The P4 disclosure hook: the UI reads this exact step name to show
            # "sending sampled frames to Gemini" at the moment it actually
            # happens, not before and not generically (`.claude/rules/
            # frontend-and-licensing.md`).
            await context.report.step(
                f"sending scene {index + 1} of {total} to Gemini for analysis",
                _step_fraction(_GEMINI_AT, _GEMINI_UNTIL, index, total),
            )

            async with context.session_factory() as session:
                row = await session.get(Scene, scene.id)
                if row is None:
                    continue
                await analyze_scene_cached(
                    session,
                    row,
                    frame_path,
                    settings=context.settings,
                    client=client,
                    prompt_version=GEMINI_PROMPT_VERSION,
                )


async def run_analysis(context: JobContext) -> None:
    """Detect scenes, sample frames, measure energy, tag with Gemini. The ``analysis`` handler."""
    sha256 = context.record.sha256
    if sha256 is None:
        raise BlobMissingError("this analysis job is not attached to a clip")

    data_dir = context.settings.data_dir

    await context.report.step("reading clip metadata", _READ_AT)
    async with context.session_factory() as session:
        blob = await _load_blob(session, sha256)
        source = absolute(data_dir, blob.stored_path)
        if not source.is_file():
            raise BlobMissingError("this clip's file is missing from the media library")
        if blob.fps_source is None:
            raise IngestIncompleteError(
                "this clip's ingest has not finished yet - its properties are not ready "
                "for analysis"
            )
        fps_source = blob.fps_source
        proxy_path = await _proxy_path(session, sha256, data_dir)

    await context.report.step("detecting scenes", _DETECT_AT)
    scenes = await _detect_and_persist_scenes(context, sha256, proxy_path, fps_source)

    await context.report.step("sampling scene frames", _SAMPLE_AT, until=_SAMPLE_UNTIL)
    scenes = await _sample_frames(context, sha256, source, scenes)

    await context.report.step("measuring scene energy", _ENERGY_AT, until=_ENERGY_UNTIL)
    scenes = await _measure_energy(context, proxy_path, scenes)

    await context.report.step(
        "sending sampled frames to Gemini for analysis", _GEMINI_AT, until=_GEMINI_UNTIL
    )
    await _analyze_with_gemini(context, data_dir, scenes)

    await context.report.step("finished", 1.0)


__all__ = [
    "ANALYSIS_JOB_TYPE",
    "GEMINI_PROMPT_VERSION",
    "SAMPLED_FRAME_ARTIFACT_KIND",
    "IngestIncompleteError",
    "run_analysis",
]
