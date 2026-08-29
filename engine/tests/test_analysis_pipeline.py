"""``analysis/pipeline.py`` - the analysis job, end to end.

Gemini is always mocked (`.claude/rules/testing.md`): every test here
monkeypatches ``pipeline._build_http_client`` *before* its first upload, so no
job this file's ``api`` fixture drives can ever reach a real Gemini call -
regardless of how the job worker's background task happens to interleave with
the upload request that enqueues it.

This file overrides ``conftest.py``'s ``api`` fixture with one that configures
a fixture Gemini key. Since Prompt 03, finalize auto-enqueues an analysis job
after every upload (`api/uploads.py`); without a key that job's own "no key
configured" branch (`analysis/cache.py`) would short-circuit before ever
reaching the caching, rate-limiting or malformed-response behaviour this file
exists to check.
"""

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from conftest import Harness
from pydantic import SecretStr
from sqlalchemy import select

from repcut.analysis import pipeline
from repcut.analysis.params import SCENE_PARAMS_VERSION
from repcut.config import Settings
from repcut.db.models import GeminiSceneCache, Job, JobStatus, MediaBlob, Scene
from repcut.jobs import JobEvent
from repcut.main import app, start_engine, stop_engine
from repcut.media.store import absolute

# Fixture-only. Never a real key (`.claude/rules/secrets.md`).
FIXTURE_GEMINI_KEY = "repcut-test-fixture-key-not-real"


@pytest_asyncio.fixture
async def api(tmp_path: Path) -> AsyncIterator[Harness]:
    """``conftest.api``, but with a Gemini key configured - this file's own override.

    Every test below monkeypatches ``pipeline._build_http_client`` before its
    first upload, so a key being present never risks a real network call; it
    only means the analysis job's "no key configured -> degrade before any
    request" branch is not silently what these tests measure instead of the
    caching/rate-limit/malformed-response behaviour they actually target.
    """
    settings = Settings(
        data_dir=tmp_path / "data",
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'engine.db').as_posix()}",
        gemini_api_key=SecretStr(FIXTURE_GEMINI_KEY),
    )
    await start_engine(app, settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
        yield Harness(
            client=client,
            settings=settings,
            session_factory=app.state.session_factory,
            queue=app.state.job_queue,
        )
    await stop_engine(app)


def _gemini_response(document: dict[str, object]) -> dict[str, object]:
    return {"candidates": [{"content": {"parts": [{"text": json.dumps(document)}]}}]}


def _mock_transport(
    responses: list[tuple[int, object]],
) -> tuple[httpx.MockTransport, list[dict[str, object]]]:
    """A queued, request-capturing stand-in for Gemini's endpoint.

    Duplicated from ``test_gemini_client.py``/``test_gemini_cache.py`` rather
    than shared - different test files, the same convention those two already
    establish for the same reason.
    """
    queue = list(responses)
    captured: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        try:
            body = json.loads(request.content.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            body = {"_raw": request.content.decode("utf-8", errors="replace")}
        captured.append(body)
        index = min(len(captured), len(queue)) - 1
        status_code, content = queue[index] if queue else (200, {"candidates": []})
        if isinstance(content, bytes):
            return httpx.Response(status_code, content=content)
        return httpx.Response(status_code, json=content)

    return httpx.MockTransport(handler), captured


def _connect_error_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused (test fixture)", request=request)

    return httpx.MockTransport(handler)


async def _latest_job(api: Harness, sha256: str, job_type: str) -> Job:
    async with api.session_factory() as session:
        statement = (
            select(Job)
            .where(Job.sha256 == sha256, Job.job_type == job_type)
            .order_by(Job.created_at.desc())
        )
        job = (await session.execute(statement)).scalars().first()
    assert job is not None, f"no {job_type} job was ever enqueued for this clip"
    return job


async def _ingest_and_analyze(
    api: Harness, upload_clip: Callable[..., Awaitable[httpx.Response]], source: Path
) -> str:
    """Upload a clip, wait for ingest and its auto-enqueued analysis, assert both succeeded."""
    project = await api.client.post("/projects", json={"name": "session"})
    finalized = await upload_clip(project.json()["id"], source)
    assert finalized.status_code == 200, finalized.text
    await api.queue.drain()
    digest: str = finalized.json()["sha256"]

    ingest_job = await _latest_job(api, digest, "ingest")
    assert ingest_job.status == JobStatus.SUCCEEDED, ingest_job.error
    analysis_job = await _latest_job(api, digest, pipeline.ANALYSIS_JOB_TYPE)
    assert analysis_job.status == JobStatus.SUCCEEDED, analysis_job.error
    return digest


async def _scenes(api: Harness, sha256: str) -> list[Scene]:
    async with api.session_factory() as session:
        statement = (
            select(Scene)
            .where(Scene.sha256 == sha256, Scene.detector_params_version == SCENE_PARAMS_VERSION)
            .order_by(Scene.sequence_index)
        )
        return list((await session.execute(statement)).scalars().all())


async def _cache_rows(api: Harness, scene_ids: list[str]) -> list[GeminiSceneCache]:
    async with api.session_factory() as session:
        statement = select(GeminiSceneCache).where(GeminiSceneCache.scene_id.in_(scene_ids))
        return list((await session.execute(statement)).scalars().all())


# --- the full pipeline ---------------------------------------------------------


async def test_pipeline_detects_scenes_samples_frames_measures_energy_and_tags_with_gemini(
    api: Harness,
    make_motion_loudness_clip: Callable[..., Path],
    upload_clip: Callable[..., Awaitable[httpx.Response]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One populated `Scene` row per detected scene, with everything the job promises."""
    transport, requests = _mock_transport(
        [(200, _gemini_response({"content_type": "exercise", "exercise_guess": "squat"}))]
    )
    monkeypatch.setattr(
        pipeline, "_build_http_client", lambda: httpx.AsyncClient(transport=transport)
    )

    clip = make_motion_loudness_clip(segment_seconds=1.5)
    digest = await _ingest_and_analyze(api, upload_clip, clip)

    scenes = await _scenes(api, digest)
    assert len(scenes) >= 2, "the fixture's hard cut should produce at least two scenes"
    assert len(requests) == len(scenes), "exactly one Gemini request per scene"

    for scene in scenes:
        assert scene.sampled_frame_path is not None
        assert Path(scene.sampled_frame_path).name == f"scene_{scene.sequence_index}.jpg"
        assert absolute(api.data_dir, scene.sampled_frame_path).is_file()
        assert scene.motion_energy is not None
        assert scene.audio_energy is not None
        assert scene.energy_score is not None

    cache_rows = await _cache_rows(api, [scene.id for scene in scenes])
    assert len(cache_rows) == len(scenes)
    assert all(row.content_type == "exercise" for row in cache_rows)
    assert all(row.exercise_guess == "squat" for row in cache_rows)


async def test_a_repeat_analysis_run_makes_zero_gemini_calls(
    api: Harness,
    make_motion_loudness_clip: Callable[..., Path],
    upload_clip: Callable[..., Awaitable[httpx.Response]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport, requests = _mock_transport([(200, _gemini_response({"content_type": "exercise"}))])
    monkeypatch.setattr(
        pipeline, "_build_http_client", lambda: httpx.AsyncClient(transport=transport)
    )

    clip = make_motion_loudness_clip(segment_seconds=1.5)
    digest = await _ingest_and_analyze(api, upload_clip, clip)
    assert requests, "the first run should have called Gemini at least once"

    second_transport, second_requests = _mock_transport(
        [(200, _gemini_response({"content_type": "exercise"}))]
    )
    monkeypatch.setattr(
        pipeline, "_build_http_client", lambda: httpx.AsyncClient(transport=second_transport)
    )

    await api.queue.enqueue(pipeline.ANALYSIS_JOB_TYPE, sha256=digest)
    await api.queue.drain()

    rerun = await _latest_job(api, digest, pipeline.ANALYSIS_JOB_TYPE)
    assert rerun.status == JobStatus.SUCCEEDED, rerun.error
    assert second_requests == []


async def test_bumping_the_prompt_version_forces_fresh_gemini_calls(
    api: Harness,
    make_motion_loudness_clip: Callable[..., Path],
    upload_clip: Callable[..., Awaitable[httpx.Response]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport, requests = _mock_transport([(200, _gemini_response({"content_type": "exercise"}))])
    monkeypatch.setattr(
        pipeline, "_build_http_client", lambda: httpx.AsyncClient(transport=transport)
    )

    clip = make_motion_loudness_clip(segment_seconds=1.5)
    digest = await _ingest_and_analyze(api, upload_clip, clip)
    assert requests

    bumped_transport, bumped_requests = _mock_transport(
        [(200, _gemini_response({"content_type": "exercise"}))]
    )
    monkeypatch.setattr(
        pipeline, "_build_http_client", lambda: httpx.AsyncClient(transport=bumped_transport)
    )
    monkeypatch.setattr(pipeline, "GEMINI_PROMPT_VERSION", 2)

    await api.queue.enqueue(pipeline.ANALYSIS_JOB_TYPE, sha256=digest)
    await api.queue.drain()

    rerun = await _latest_job(api, digest, pipeline.ANALYSIS_JOB_TYPE)
    assert rerun.status == JobStatus.SUCCEEDED, rerun.error
    scenes = await _scenes(api, digest)
    assert len(bumped_requests) == len(scenes), "a version bump must call Gemini again per scene"


async def test_offline_still_completes_with_local_features_and_null_vlm(
    api: Harness,
    make_motion_loudness_clip: Callable[..., Path],
    upload_clip: Callable[..., Awaitable[httpx.Response]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A connection error degrades Gemini, not the whole job (`.claude/rules/gemini-usage.md`)."""
    monkeypatch.setattr(
        pipeline,
        "_build_http_client",
        lambda: httpx.AsyncClient(transport=_connect_error_transport()),
    )

    clip = make_motion_loudness_clip(segment_seconds=1.5)
    digest = await _ingest_and_analyze(api, upload_clip, clip)

    scenes = await _scenes(api, digest)
    assert len(scenes) >= 1
    assert all(
        scene.motion_energy is not None and scene.audio_energy is not None for scene in scenes
    )
    cache_rows = await _cache_rows(api, [scene.id for scene in scenes])
    assert cache_rows == [], "an unreachable Gemini must never write a cache row"


# --- resumability ----------------------------------------------------------------


async def test_a_missing_sampled_frame_file_is_resampled_without_redoing_finished_scenes(
    api: Harness,
    make_motion_loudness_clip: Callable[..., Path],
    upload_clip: Callable[..., Awaitable[httpx.Response]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A crash that lost one scene's frame file must not force redetection, and the
    still-cached Gemini answers must not be re-fetched either - the same
    per-artifact resumability `media.ingest.run_ingest` proves, at the
    per-scene granularity this job adds.
    """
    transport, _requests = _mock_transport([(200, _gemini_response({"content_type": "exercise"}))])
    monkeypatch.setattr(
        pipeline, "_build_http_client", lambda: httpx.AsyncClient(transport=transport)
    )

    clip = make_motion_loudness_clip(segment_seconds=1.5)
    digest = await _ingest_and_analyze(api, upload_clip, clip)
    scenes = await _scenes(api, digest)
    assert len(scenes) >= 2
    victim = scenes[0]
    assert victim.sampled_frame_path is not None
    frame_path = absolute(api.data_dir, victim.sampled_frame_path)
    assert frame_path.is_file()
    frame_path.unlink()

    second_transport, second_requests = _mock_transport(
        [(200, _gemini_response({"content_type": "exercise"}))]
    )
    monkeypatch.setattr(
        pipeline, "_build_http_client", lambda: httpx.AsyncClient(transport=second_transport)
    )
    await api.queue.enqueue(pipeline.ANALYSIS_JOB_TYPE, sha256=digest)
    await api.queue.drain()

    rerun = await _latest_job(api, digest, pipeline.ANALYSIS_JOB_TYPE)
    assert rerun.status == JobStatus.SUCCEEDED, rerun.error

    rescanned = await _scenes(api, digest)
    assert len(rescanned) == len(scenes), "detection must not have re-run - same scene count"
    assert frame_path.is_file(), "the missing frame must have been re-sampled"
    assert second_requests == [], "every scene's Gemini answer was already cached"


async def test_analysis_fails_readably_before_ingest_has_produced_a_proxy(
    api: Harness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A named cause, never a crash, when analysis is queued ahead of its own ingest."""
    monkeypatch.setattr(
        pipeline,
        "_build_http_client",
        lambda: httpx.AsyncClient(transport=_connect_error_transport()),
    )

    digest = "a" * 64
    stored = absolute(api.data_dir, f"media/blobs/{digest[:2]}/{digest}/source.mp4")
    stored.parent.mkdir(parents=True, exist_ok=True)
    stored.write_bytes(b"not a real video - this row is never probed in this test")

    async with api.session_factory() as session:
        session.add(
            MediaBlob(
                sha256=digest,
                size_bytes=stored.stat().st_size,
                stored_path=f"media/blobs/{digest[:2]}/{digest}/source.mp4",
            )
        )
        await session.commit()

    await api.queue.enqueue(pipeline.ANALYSIS_JOB_TYPE, sha256=digest)
    await api.queue.drain()

    job = await _latest_job(api, digest, pipeline.ANALYSIS_JOB_TYPE)
    assert job.status == JobStatus.FAILED
    assert job.error is not None and "ingest" in job.error.lower()
    assert "Traceback" not in (job.error or "")


# --- progress events ---------------------------------------------------------------


async def test_progress_events_include_the_gemini_disclosure_step(
    api: Harness,
    make_motion_loudness_clip: Callable[..., Path],
    upload_clip: Callable[..., Awaitable[httpx.Response]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every job emits progress (CLAUDE.md's own non-negotiable), monotonically, and the
    per-scene Gemini step is named plainly enough for the UI to render the P4 disclosure
    at the moment it actually happens (`.claude/rules/frontend-and-licensing.md`).
    """
    transport, _requests = _mock_transport([(200, _gemini_response({"content_type": "exercise"}))])
    monkeypatch.setattr(
        pipeline, "_build_http_client", lambda: httpx.AsyncClient(transport=transport)
    )
    clip = make_motion_loudness_clip(segment_seconds=1.5)
    digest = await _ingest_and_analyze(api, upload_clip, clip)

    second_transport, _ = _mock_transport([(200, _gemini_response({"content_type": "exercise"}))])
    monkeypatch.setattr(
        pipeline, "_build_http_client", lambda: httpx.AsyncClient(transport=second_transport)
    )

    with api.queue.subscribe() as events:
        job_id = await api.queue.enqueue(pipeline.ANALYSIS_JOB_TYPE, sha256=digest)
        seen: list[JobEvent] = []
        terminal: JobEvent | None = None
        while terminal is None:
            event = await asyncio.wait_for(events.get(), timeout=30.0)
            if event.job_id != job_id:
                continue
            seen.append(event)
            if event.status in (JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED):
                terminal = event

    assert terminal is not None
    assert terminal.status == JobStatus.SUCCEEDED, terminal.error
    assert terminal.progress == 1.0
    steps = [event.step for event in seen if event.step]
    assert any("gemini" in step.lower() for step in steps), steps
    progresses = [event.progress for event in seen]
    assert progresses == sorted(progresses), "progress must never go backwards"
