"""``analysis/cache.py`` - cache-first, rate-limited Gemini scene analysis.

Gemini is always mocked (`.claude/rules/testing.md`). The API key used
throughout is a fixture string, never a real credential
(`.claude/rules/secrets.md`). ``db_session`` comes from ``conftest.py``.
"""

import json
import os
from collections.abc import Mapping
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from structlog.testing import capture_logs

from repcut.analysis import cache
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
from repcut.media.store import absolute, gemini_rate_limit_state_path

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


async def test_cache_row_that_no_longer_fits_the_schema_reads_as_null(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    scene = await _persisted_scene(db_session, "b" * 64)
    db_session.add(
        GeminiSceneCache(
            scene_id=scene.id,
            gemini_prompt_version=1,
            # Valid JSON, wrong shape: an int where the schema wants a string.
            raw_response_json=json.dumps({"content_type": 5}),
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
    assert outcome.result is None


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


async def test_every_backoff_retry_spends_its_own_token(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    scene = await _persisted_scene(db_session, "2" * 64)
    frame_path = tmp_path / "frame.jpg"
    frame_path.write_bytes(b"frame-bytes")
    transport, calls = _connect_error_transport()
    budget = _MAX_BACKOFF_ATTEMPTS - 1

    async with httpx.AsyncClient(transport=transport) as client:
        outcome = await analyze_scene_cached(
            db_session,
            scene,
            frame_path,
            settings=_settings(tmp_path / "settings", rpm_limit=budget),
            client=client,
            prompt_version=1,
        )

    assert len(calls) == budget
    assert outcome.source == "degraded"
    assert await _cache_row(db_session, scene.id, 1) is None


async def test_json_retry_spends_its_own_token_and_degrades_without_one(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    scene = await _persisted_scene(db_session, "3" * 64)
    frame_path = tmp_path / "frame.jpg"
    frame_path.write_bytes(b"frame-bytes")
    transport, requests = _mock_transport([(200, b"garbage {{{"), (200, b"garbage {{{")])

    async with httpx.AsyncClient(transport=transport) as client:
        outcome = await analyze_scene_cached(
            db_session,
            scene,
            frame_path,
            settings=_settings(tmp_path / "settings", rpm_limit=1),
            client=client,
            prompt_version=1,
        )

    assert len(requests) == 1
    assert outcome.source == "degraded"
    # One malformed answer is not the "asked twice" answer a null row records.
    assert await _cache_row(db_session, scene.id, 1) is None


async def test_a_permanent_4xx_is_not_retried(db_session: AsyncSession, tmp_path: Path) -> None:
    scene = await _persisted_scene(db_session, "4" * 64)
    frame_path = tmp_path / "frame.jpg"
    frame_path.write_bytes(b"frame-bytes")
    transport, requests = _mock_transport([(401, {"error": "unauthorized"})])

    async with httpx.AsyncClient(transport=transport) as client:
        outcome = await analyze_scene_cached(
            db_session,
            scene,
            frame_path,
            settings=_settings(tmp_path / "settings"),
            client=client,
            prompt_version=1,
        )

    assert len(requests) == 1
    assert outcome.source == "degraded"


async def test_on_send_fires_once_per_request_actually_sent(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    scene = await _persisted_scene(db_session, "5" * 64)
    frame_path = tmp_path / "frame.jpg"
    frame_path.write_bytes(b"frame-bytes")
    transport, requests = _mock_transport([(200, b"garbage {{{"), (200, b"garbage {{{")])
    sends: list[int] = []

    async def on_send() -> None:
        sends.append(len(requests))

    async with httpx.AsyncClient(transport=transport) as client:
        await analyze_scene_cached(
            db_session,
            scene,
            frame_path,
            settings=_settings(tmp_path / "settings"),
            client=client,
            prompt_version=1,
            on_send=on_send,
        )
    # Each announcement lands before the request it pays for.
    assert sends == [0, 1]
    assert len(requests) == 2

    # A cache hit, a missing key and an empty limiter all send nothing - so
    # none of them may announce a send.
    silent: list[int] = []

    async def must_not_fire() -> None:
        silent.append(1)

    cold = await _persisted_scene(db_session, "6" * 64)
    async with httpx.AsyncClient(transport=_unreachable_transport()) as client:
        for target, settings in (
            (scene, _settings(tmp_path / "settings")),
            (cold, _settings(tmp_path / "nokey", api_key=None)),
            (cold, _settings(tmp_path / "empty", rpm_limit=0, daily_limit=0)),
        ):
            await analyze_scene_cached(
                db_session,
                target,
                frame_path,
                settings=settings,
                client=client,
                prompt_version=1,
                on_send=must_not_fire,
            )
    assert silent == []


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


# --- a 429 is a wait, or the end of the day - never a retry in the same second ---

PER_DAY_QUOTA = "GenerateRequestsPerDayPerProjectPerModel-FreeTier"
PER_MINUTE_QUOTA = "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"


def _quota_429(quota_id: str | None, retry_delay: str | None = None) -> tuple[int, object]:
    """A 429 in the shape Google's API sends, naming ``quota_id`` if given."""
    details: list[object] = []
    if quota_id is not None:
        details.append(
            {
                "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                "violations": [{"quotaId": quota_id, "quotaValue": "20"}],
            }
        )
    if retry_delay is not None:
        details.append(
            {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry_delay}
        )
    return 429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "details": details}}


@pytest.fixture
def waits(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Every backoff wait the cache asks for, recorded instead of slept."""
    recorded: list[float] = []

    async def _record(seconds: float) -> None:
        recorded.append(seconds)

    monkeypatch.setattr(cache, "_sleep", _record)
    return recorded


async def test_a_per_minute_429_waits_as_long_as_gemini_asks_then_succeeds(
    db_session: AsyncSession, tmp_path: Path, waits: list[float]
) -> None:
    scene = await _persisted_scene(db_session, "5" * 64)
    frame_path = tmp_path / "frame.jpg"
    frame_path.write_bytes(b"frame-bytes")
    transport, requests = _mock_transport(
        [_quota_429(PER_MINUTE_QUOTA, "7s"), (200, _gemini_response({"content_type": "rest"}))]
    )
    announced: list[float] = []

    async def on_retry_wait(seconds: float) -> None:
        announced.append(seconds)

    async with httpx.AsyncClient(transport=transport) as client:
        outcome = await analyze_scene_cached(
            db_session,
            scene,
            frame_path,
            settings=_settings(tmp_path / "settings"),
            client=client,
            prompt_version=1,
            on_retry_wait=on_retry_wait,
        )

    assert len(requests) == 2
    assert outcome.source == "api"
    assert len(waits) == 1
    assert 7.0 <= waits[0] <= 7.0 * 1.5, "RetryInfo is a floor; jitter only adds, and boundedly"
    assert announced == waits, "the job says it is waiting, and for how long"
    assert await _cache_row(db_session, scene.id, 1) is not None


async def test_a_429_that_names_no_delay_is_never_retried_within_a_second(
    db_session: AsyncSession, tmp_path: Path, waits: list[float]
) -> None:
    """The review's finding: three attempts per scene in ~1.3s."""
    scene = await _persisted_scene(db_session, "6" * 64)
    frame_path = tmp_path / "frame.jpg"
    frame_path.write_bytes(b"frame-bytes")
    transport, requests = _mock_transport([_quota_429(None)])

    async with httpx.AsyncClient(transport=transport) as client:
        outcome = await analyze_scene_cached(
            db_session,
            scene,
            frame_path,
            settings=_settings(tmp_path / "settings"),
            client=client,
            prompt_version=1,
        )

    assert len(requests) == _MAX_BACKOFF_ATTEMPTS
    assert len(waits) == _MAX_BACKOFF_ATTEMPTS - 1
    assert all(wait >= 1.0 for wait in waits), waits
    assert waits == sorted(waits), "exponential: each wait at least the one before"
    assert outcome.source == "degraded"
    assert await _cache_row(db_session, scene.id, 1) is None


async def test_a_retry_delay_longer_than_a_scene_is_worth_degrades_without_waiting(
    db_session: AsyncSession, tmp_path: Path, waits: list[float]
) -> None:
    scene = await _persisted_scene(db_session, "7" * 64)
    frame_path = tmp_path / "frame.jpg"
    frame_path.write_bytes(b"frame-bytes")
    transport, requests = _mock_transport([_quota_429(PER_MINUTE_QUOTA, "3600s")])

    async with httpx.AsyncClient(transport=transport) as client:
        outcome = await analyze_scene_cached(
            db_session,
            scene,
            frame_path,
            settings=_settings(tmp_path / "settings"),
            client=client,
            prompt_version=1,
        )

    assert len(requests) == 1
    assert waits == []
    assert outcome.source == "degraded"


async def test_a_per_day_429_is_not_retried_and_closes_the_day_for_later_scenes(
    db_session: AsyncSession, tmp_path: Path, waits: list[float]
) -> None:
    first = await _persisted_scene(db_session, "8" * 64)
    second = await _persisted_scene(db_session, "9" * 64)
    frame_path = tmp_path / "frame.jpg"
    frame_path.write_bytes(b"frame-bytes")
    settings = _settings(tmp_path / "settings")
    transport, requests = _mock_transport([_quota_429(PER_DAY_QUOTA, "30s")])

    with capture_logs() as logs:
        async with httpx.AsyncClient(transport=transport) as client:
            outcome = await analyze_scene_cached(
                db_session, first, frame_path, settings=settings, client=client, prompt_version=1
            )
    assert len(requests) == 1, "a per-day quota is not a blip; asking again is a wasted request"
    assert waits == []
    assert outcome.source == "degraded"
    assert await _cache_row(db_session, first.id, 1) is None
    exhausted = [entry for entry in logs if entry["event"] == "gemini_daily_quota_exhausted"]
    assert exhausted and exhausted[0]["quota_id"] == PER_DAY_QUOTA

    async with httpx.AsyncClient(transport=_unreachable_transport()) as client:
        later = await analyze_scene_cached(
            db_session, second, frame_path, settings=settings, client=client, prompt_version=1
        )
    assert later.source == "degraded", "the next scene must not send a frame to be refused"

    restarted = GeminiRateLimiter(
        rpm_limit=settings.gemini_rpm_limit,
        daily_limit=settings.gemini_daily_limit,
        state_path=absolute(settings.data_dir, gemini_rate_limit_state_path()),
    )
    assert restarted.has_budget_today() is False, "a restart must not reopen a closed day"


async def test_a_quota_degraded_scene_is_asked_again_on_a_later_run(
    db_session: AsyncSession, tmp_path: Path, waits: list[float]
) -> None:
    """Degraded is not an answer: no ``vlm: null`` row, so the next run asks."""
    scene = await _persisted_scene(db_session, "0" * 64)
    frame_path = tmp_path / "frame.jpg"
    frame_path.write_bytes(b"frame-bytes")
    refused, _ = _mock_transport([_quota_429(PER_MINUTE_QUOTA, "5s")])

    async with httpx.AsyncClient(transport=refused) as client:
        degraded = await analyze_scene_cached(
            db_session,
            scene,
            frame_path,
            settings=_settings(tmp_path / "settings"),
            client=client,
            prompt_version=1,
        )
    assert degraded.source == "degraded"
    assert await _cache_row(db_session, scene.id, 1) is None

    answered, requests = _mock_transport([(200, _gemini_response({"content_type": "setup"}))])
    async with httpx.AsyncClient(transport=answered) as client:
        later = await analyze_scene_cached(
            db_session,
            scene,
            frame_path,
            settings=_settings(tmp_path / "settings"),
            client=client,
            prompt_version=1,
        )
    assert len(requests) == 1
    assert later.source == "api"
    assert later.result is not None and later.result.content_type == "setup"


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


async def test_rate_limiter_unreadable_state_counts_today_as_spent(tmp_path: Path) -> None:
    state_path = tmp_path / "gemini_rate_limit_state.json"
    state_path.write_text("{truncated", encoding="utf-8")

    limiter = GeminiRateLimiter(rpm_limit=100, daily_limit=2, state_path=state_path)
    assert await limiter.try_acquire() is False


async def test_rate_limiter_state_write_leaves_no_partial_file(tmp_path: Path) -> None:
    state_path = tmp_path / "gemini_rate_limit_state.json"
    limiter = GeminiRateLimiter(rpm_limit=100, daily_limit=2, state_path=state_path)
    assert await limiter.try_acquire() is True

    assert json.loads(state_path.read_text(encoding="utf-8"))["count"] == 1
    assert os.listdir(tmp_path) == [state_path.name]


async def test_get_rate_limiter_is_reused_per_data_dir(tmp_path: Path) -> None:
    settings_a = _settings(tmp_path / "a")
    settings_b = _settings(tmp_path / "a")
    settings_c = _settings(tmp_path / "b")

    assert get_rate_limiter(settings_a) is get_rate_limiter(settings_b)
    assert get_rate_limiter(settings_a) is not get_rate_limiter(settings_c)
