"""``analysis/cache.py`` - cache-first, rate-limited Gemini scene analysis.

Gemini is always mocked (`.claude/rules/testing.md`). The API key used
throughout is a fixture string, never a real credential
(`.claude/rules/secrets.md`). ``db_session`` comes from ``conftest.py``.
"""

import json
from collections.abc import Mapping
from pathlib import Path

import httpx
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from structlog.testing import capture_logs

from repcut.analysis.cache import (
    _MAX_BACKOFF_ATTEMPTS,
    GeminiRateLimiter,
    analyze_scene_cached,
    get_rate_limiter,
)
from repcut.analysis.gemini_client import GeminiSceneResult
from repcut.analysis.params import SCENE_PARAMS_VERSION
from repcut.config import Settings
from repcut.db.models import GeminiSceneCache, MediaBlob, Scene

# Fixture-only. Never a real key (`.claude/rules/secrets.md`).
FIXTURE_GEMINI_KEY = "repcut-test-fixture-key-not-real"


def _blob(sha256: str) -> MediaBlob:
    return MediaBlob(
        sha256=sha256, size_bytes=1024, stored_path=f"media/blobs/{sha256[:2]}/{sha256}/source.mp4"
    )


def _scene(sha256: str, sequence_index: int = 0, **overrides: object) -> Scene:
    scene = Scene(
        sha256=sha256,
        detector_params_version=SCENE_PARAMS_VERSION,
        sequence_index=sequence_index,
        start_seconds=float(sequence_index) * 2.0,
        end_seconds=float(sequence_index) * 2.0 + 2.0,
        start_frame_source=sequence_index * 60,
        end_frame_source=sequence_index * 60 + 60,
        motion_energy=0.4,
        audio_energy=0.3,
    )
    for field, value in overrides.items():
        setattr(scene, field, value)
    return scene


async def _persisted_scene(
    session: AsyncSession, sha256: str, sequence_index: int = 0, **overrides: object
) -> Scene:
    """Insert and commit a blob + scene, so the cache row's FK has somewhere to point."""
    session.add(_blob(sha256))
    scene = _scene(sha256, sequence_index, **overrides)
    session.add(scene)
    await session.commit()
    return scene


async def _cache_row(
    session: AsyncSession, scene_id: str, prompt_version: int
) -> GeminiSceneCache | None:
    statement = select(GeminiSceneCache).where(
        GeminiSceneCache.scene_id == scene_id,
        GeminiSceneCache.gemini_prompt_version == prompt_version,
    )
    return (await session.execute(statement)).scalars().first()


def _settings(
    tmp_path: Path,
    *,
    rpm_limit: int = 10,
    daily_limit: int = 1400,
    api_key: str | None = FIXTURE_GEMINI_KEY,
) -> Settings:
    return Settings(
        data_dir=tmp_path,
        gemini_api_key=SecretStr(api_key) if api_key is not None else None,
        gemini_rpm_limit=rpm_limit,
        gemini_daily_limit=daily_limit,
    )


def _gemini_response(document: Mapping[str, object]) -> dict[str, object]:
    return {"candidates": [{"content": {"parts": [{"text": json.dumps(document)}]}}]}


def _mock_transport(
    responses: list[tuple[int, object]],
) -> tuple[httpx.MockTransport, list[dict[str, object]]]:
    """A queued, request-capturing stand-in for Gemini's endpoint.

    Duplicated from ``test_gemini_client.py`` / ``scripts/verify_03_checks.py``
    rather than shared - different processes and different test files, the
    same convention ``conftest.py`` already follows for its own fixtures.
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
        status, content = queue[index] if queue else (200, {"candidates": []})
        if isinstance(content, bytes):
            return httpx.Response(status, content=content)
        return httpx.Response(status, json=content)

    return httpx.MockTransport(handler), captured


def _unreachable_transport() -> httpx.MockTransport:
    """Fails the test outright if a request is ever made through it."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("Gemini must not be called for this scenario")

    return httpx.MockTransport(handler)


def _connect_error_transport() -> tuple[httpx.MockTransport, list[int]]:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        raise httpx.ConnectError("connection refused (test fixture)", request=request)

    return httpx.MockTransport(handler), calls


async def test_cache_hit_makes_zero_requests(db_session: AsyncSession, tmp_path: Path) -> None:
    scene = await _persisted_scene(db_session, "a" * 64)
    db_session.add(
        GeminiSceneCache(
            scene_id=scene.id,
            gemini_prompt_version=1,
            content_type="exercise",
            raw_response_json=GeminiSceneResult(content_type="exercise").model_dump_json(),
        )
    )
    await db_session.commit()

    frame_path = tmp_path / "frame.jpg"
    frame_path.write_bytes(b"frame-bytes")

    async with httpx.AsyncClient(transport=_unreachable_transport()) as client:
        outcome = await analyze_scene_cached(
            db_session,
            scene,
            frame_path,
            settings=_settings(tmp_path / "settings"),
            client=client,
            prompt_version=1,
        )

    assert outcome.source == "cache"
    assert outcome.result is not None
    assert outcome.result.content_type == "exercise"


async def test_prompt_version_bump_forces_a_fresh_call(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    scene = await _persisted_scene(db_session, "b" * 64)
    frame_path = tmp_path / "frame.jpg"
    frame_path.write_bytes(b"frame-bytes")
    document = {"content_type": "exercise"}

    transport1, requests1 = _mock_transport([(200, _gemini_response(document))])
    async with httpx.AsyncClient(transport=transport1) as client:
        first = await analyze_scene_cached(
            db_session,
            scene,
            frame_path,
            settings=_settings(tmp_path / "settings"),
            client=client,
            prompt_version=1,
        )

    transport2, requests2 = _mock_transport([(200, _gemini_response(document))])
    async with httpx.AsyncClient(transport=transport2) as client:
        repeat = await analyze_scene_cached(
            db_session,
            scene,
            frame_path,
            settings=_settings(tmp_path / "settings"),
            client=client,
            prompt_version=1,
        )

    transport3, requests3 = _mock_transport([(200, _gemini_response(document))])
    async with httpx.AsyncClient(transport=transport3) as client:
        bumped = await analyze_scene_cached(
            db_session,
            scene,
            frame_path,
            settings=_settings(tmp_path / "settings"),
            client=client,
            prompt_version=2,
        )

    assert first.source == "api"
    assert len(requests1) == 1
    assert repeat.source == "cache"
    assert len(requests2) == 0
    assert bumped.source == "api"
    assert len(requests3) == 1


async def test_bucket_exhausted_makes_zero_requests_and_no_cache_row(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    scene = await _persisted_scene(db_session, "c" * 64)
    frame_path = tmp_path / "frame.jpg"
    frame_path.write_bytes(b"frame-bytes")

    async with httpx.AsyncClient(transport=_unreachable_transport()) as client:
        outcome = await analyze_scene_cached(
            db_session,
            scene,
            frame_path,
            settings=_settings(tmp_path / "settings", rpm_limit=0, daily_limit=0),
            client=client,
            prompt_version=1,
        )

    assert outcome.source == "degraded"
    assert outcome.result is None
    assert await _cache_row(db_session, scene.id, 1) is None


async def test_malformed_json_writes_a_null_cache_row_after_one_retry(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    scene = await _persisted_scene(db_session, "d" * 64)
    frame_path = tmp_path / "frame.jpg"
    frame_path.write_bytes(b"frame-bytes")
    garbage = b"not json at all {{{"

    transport, requests = _mock_transport([(200, garbage), (200, garbage)])
    async with httpx.AsyncClient(transport=transport) as client:
        outcome = await analyze_scene_cached(
            db_session,
            scene,
            frame_path,
            settings=_settings(tmp_path / "settings"),
            client=client,
            prompt_version=1,
        )

    assert len(requests) == 2
    assert outcome.source == "api"
    assert outcome.result is None

    row = await _cache_row(db_session, scene.id, 1)
    assert row is not None
    assert row.raw_response_json is None

    # A row now exists, so a repeat run must not call Gemini again - the
    # "malformed twice" answer is itself cached (gemini-usage.md).
    async with httpx.AsyncClient(transport=_unreachable_transport()) as client:
        repeat = await analyze_scene_cached(
            db_session,
            scene,
            frame_path,
            settings=_settings(tmp_path / "settings"),
            client=client,
            prompt_version=1,
        )
    assert repeat.source == "cache"
    assert repeat.result is None


async def test_transport_error_backs_off_then_degrades_with_no_cache_row(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    scene = await _persisted_scene(db_session, "e" * 64)
    frame_path = tmp_path / "frame.jpg"
    frame_path.write_bytes(b"frame-bytes")
    transport, calls = _connect_error_transport()

    async with httpx.AsyncClient(transport=transport) as client:
        outcome = await analyze_scene_cached(
            db_session,
            scene,
            frame_path,
            settings=_settings(tmp_path / "settings"),
            client=client,
            prompt_version=1,
        )

    assert outcome.source == "degraded"
    assert outcome.result is None
    assert len(calls) == _MAX_BACKOFF_ATTEMPTS
    assert await _cache_row(db_session, scene.id, 1) is None


async def test_key_never_appears_in_logs_across_cache_failure_paths(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    malformed_scene = await _persisted_scene(db_session, "f" * 64)
    offline_scene = await _persisted_scene(db_session, "1" * 64)
    frame_path = tmp_path / "frame.jpg"
    frame_path.write_bytes(b"frame-bytes")

    with capture_logs() as logs:
        transport, _ = _mock_transport([(200, b"garbage {{{"), (200, b"garbage {{{")])
        async with httpx.AsyncClient(transport=transport) as client:
            await analyze_scene_cached(
                db_session,
                malformed_scene,
                frame_path,
                settings=_settings(tmp_path / "settings1"),
                client=client,
                prompt_version=1,
            )

        offline_transport, _ = _connect_error_transport()
        async with httpx.AsyncClient(transport=offline_transport) as client:
            await analyze_scene_cached(
                db_session,
                offline_scene,
                frame_path,
                settings=_settings(tmp_path / "settings2"),
                client=client,
                prompt_version=1,
            )

    serialized = json.dumps(logs)
    assert FIXTURE_GEMINI_KEY not in serialized


# --- the rate limiter, directly ------------------------------------------------


async def test_rate_limiter_fails_closed_at_zero() -> None:
    limiter = GeminiRateLimiter(rpm_limit=0, daily_limit=0, state_path=None)
    assert await limiter.try_acquire() is False


async def test_rate_limiter_rpm_bucket_blocks_then_refills(tmp_path: Path) -> None:
    limiter = GeminiRateLimiter(rpm_limit=1, daily_limit=100, state_path=None)
    assert await limiter.try_acquire() is True
    assert await limiter.try_acquire() is False

    # Simulate a minute having passed, without an actual `asyncio.sleep(60)`.
    limiter._rpm_updated_monotonic -= 61.0
    assert await limiter.try_acquire() is True


async def test_rate_limiter_daily_count_persists_across_a_restart(tmp_path: Path) -> None:
    state_path = tmp_path / "gemini_rate_limit_state.json"
    first = GeminiRateLimiter(rpm_limit=100, daily_limit=2, state_path=state_path)
    assert await first.try_acquire() is True
    assert await first.try_acquire() is True
    assert await first.try_acquire() is False  # daily budget spent

    # A fresh instance against the same state file is what a process restart
    # looks like - the skill's own requirement: "a restart must not reset the
    # budget" (`.claude/skills/gemini-free-tier`).
    second = GeminiRateLimiter(rpm_limit=100, daily_limit=2, state_path=state_path)
    assert await second.try_acquire() is False


async def test_rate_limiter_daily_count_resets_on_a_new_utc_date(tmp_path: Path) -> None:
    state_path = tmp_path / "gemini_rate_limit_state.json"
    state_path.write_text(json.dumps({"date": "2000-01-01", "count": 999}), encoding="utf-8")

    limiter = GeminiRateLimiter(rpm_limit=100, daily_limit=2, state_path=state_path)
    assert await limiter.try_acquire() is True


async def test_get_rate_limiter_is_reused_per_data_dir(tmp_path: Path) -> None:
    settings_a = _settings(tmp_path / "a")
    settings_b = _settings(tmp_path / "a")
    settings_c = _settings(tmp_path / "b")

    assert get_rate_limiter(settings_a) is get_rate_limiter(settings_b)
    assert get_rate_limiter(settings_a) is not get_rate_limiter(settings_c)
