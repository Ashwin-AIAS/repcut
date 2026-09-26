"""Cache-first, rate-limited Gemini scene analysis.

The one module in this prompt that touches the database or the rate-limiter's
own state - by design (`.claude/skills/gemini-free-tier`: "client + response
schemas + SQLite cache + rate limiter" is the whole remit). Everything in
``gemini_client.py`` is a pure function of its arguments; this module is what
decides whether to call it at all.

Five steps, in order, per `.claude/rules/gemini-usage.md`:

1. **Cache check first, unconditionally.** A hit costs zero API calls,
   whatever the reason a scene was already analyzed.
2. **No key configured -> degrade immediately.** Nothing to call; this must
   not consume rate-limiter budget or touch the network at all.
3. **Rate limiter, before the request, fails closed.** A bucket exhausted at
   either the per-minute or the per-day budget makes zero requests.
4. **Call :func:`~repcut.analysis.gemini_client.analyze_frame`.** On any
   completed round trip - a parsed success, or "reached the API but the body
   never parsed even after its own one retry" - write a cache row. This is
   the line ``GeminiSceneCache``'s own docstring draws: a row means an attempt
   was made, not that the attempt found anything.
5. **A transport error or a non-2xx response (429 included) is retried with
   exponential backoff and jitter, capped.** If every attempt still fails to
   reach Gemini with a usable HTTP response, degrade - no cache row, because
   the scene was never actually analyzed and the next run must try again.
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import httpx
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repcut.analysis.gemini_client import (
    GeminiAPIError,
    GeminiSceneResult,
    GeminiTransportError,
    SceneContext,
    analyze_frame,
)
from repcut.config import Settings
from repcut.db.models import GeminiSceneCache, Scene, utcnow
from repcut.logging import get_logger

logger = get_logger(__name__)

# The skill's own number (`.claude/skills/gemini-free-tier`: "capped at ~3
# attempts"). Deliberately small delays, not a literal wait-out-the-quota
# window: this backoff exists to ride out a transient blip, and the real
# recovery mechanism for a genuinely exhausted quota is graceful degradation
# to heuristic tags after the cap, not a multi-minute retry loop that would
# stall the whole analysis job for one scene.
_MAX_BACKOFF_ATTEMPTS = 3
_BACKOFF_BASE_SECONDS = 0.2
_BACKOFF_MAX_SECONDS = 2.0
_BACKOFF_JITTER_FRACTION = 0.5

_RATE_LIMIT_STATE_FILENAME = "gemini_rate_limit_state.json"


class SceneAnalysisOutcome(BaseModel):
    """What a caller gets back for one scene: the result, and where it came from.

    ``source`` is UI-facing (P4/P2: the interface discloses what happened,
    never silently substitutes): "cache" cost nothing and was instant, "api"
    was a real round trip just now (whether or not it produced a non-null
    result), "degraded" means Gemini was skipped or never answered and the
    caller should fall back to heuristic tags and say so plainly.
    """

    result: GeminiSceneResult | None
    source: Literal["cache", "api", "degraded"]


def _row_to_result(row: GeminiSceneCache) -> GeminiSceneResult | None:
    """Rebuild a result from a cache row, or None for a cached "malformed" answer.

    A row written after two malformed responses (see ``GeminiSceneCache``'s own
    docstring) has ``raw_response_json`` null - that is a legitimate cached
    answer ("Gemini was asked and never produced anything usable"), not a
    lookup miss, so it comes back as ``result=None, source="cache"`` rather
    than triggering a fresh call.
    """
    if row.raw_response_json is None:
        return None
    try:
        document = json.loads(row.raw_response_json)
    except json.JSONDecodeError:
        logger.warning("gemini_cache_row_unparseable", scene_id=row.scene_id)
        return None
    return GeminiSceneResult.model_validate(document)


async def _lookup_cache(
    session: AsyncSession, scene_id: str, prompt_version: int
) -> GeminiSceneCache | None:
    statement = select(GeminiSceneCache).where(
        GeminiSceneCache.scene_id == scene_id,
        GeminiSceneCache.gemini_prompt_version == prompt_version,
    )
    return (await session.execute(statement)).scalars().first()


async def _write_cache_row(
    session: AsyncSession,
    scene_id: str,
    prompt_version: int,
    result: GeminiSceneResult | None,
) -> None:
    """Insert the cache row for one completed round trip. Never called otherwise.

    ``raw_response_json`` holds the validated *response*, never the request -
    so it can never carry the API key (``GeminiSceneCache``'s own docstring).
    """
    row = GeminiSceneCache(
        scene_id=scene_id,
        gemini_prompt_version=prompt_version,
        content_type=result.content_type if result else None,
        exercise_guess=result.exercise_guess if result else None,
        environment=result.environment if result else None,
        lighting_quality=result.lighting_quality if result else None,
        lighting_temperature=result.lighting_temperature if result else None,
        lighting_direction=result.lighting_direction if result else None,
        energy_level=result.energy_level if result else None,
        aesthetic_notes=result.aesthetic_notes if result else None,
        raw_response_json=result.model_dump_json() if result else None,
        retrieved_at=utcnow(),
    )
    session.add(row)
    await session.commit()


def _scene_context(scene: Scene) -> SceneContext:
    """The scene's own columns, mapped straight across - no lookups elsewhere.

    ``position_seconds`` is the scene's start against the source's timebase,
    the only "position" this module can know without reading anything besides
    the ``Scene`` row itself. A position normalised against the *whole clip*
    (a fraction of total duration, say) would need the clip's own duration,
    which is not a column on this row - that enrichment, if ever wanted, is
    ``pipeline.py``'s job, per this prompt's own brief: this module's boundary
    is "given a scene row and a frame path, get an outcome," not "know how a
    Scene row's fields map to a richer prompt."
    """
    return SceneContext(
        duration_seconds=scene.end_seconds - scene.start_seconds,
        position_seconds=scene.start_seconds,
        motion_energy=scene.motion_energy,
        audio_energy=scene.audio_energy,
    )


def _backoff_delay(attempt: int) -> float:
    """Exponential delay with jitter, capped - `.claude/rules/gemini-usage.md`."""
    # `2.0 ** attempt`, not `2 ** attempt`: int.__pow__ with a non-literal
    # exponent types as `Any` in typeshed (it can be negative), which would
    # silently make every downstream float here `Any` too.
    base = min(_BACKOFF_MAX_SECONDS, _BACKOFF_BASE_SECONDS * (2.0**attempt))
    # A retry delay's jitter, not a cryptographic use.
    jitter = base * _BACKOFF_JITTER_FRACTION * random.random()  # noqa: S311
    return base + jitter


async def _call_with_backoff(
    frame_path: Path,
    *,
    context: SceneContext,
    settings: Settings,
    client: httpx.AsyncClient,
) -> tuple[GeminiSceneResult | None, bool]:
    """Call ``analyze_frame``, retrying only transport/HTTP failures.

    A malformed-JSON result (``analyze_frame`` returning ``None`` *without*
    raising) is not retried here - it already got its one retry inside
    ``analyze_frame`` itself. This loop exists for the other failure class:
    the request never got a usable HTTP response from Gemini at all (offline,
    429, 5xx), where asking again after a short wait might succeed.

    Returns ``(result, reached_api)``. ``reached_api`` is False only when
    every attempt failed to reach Gemini with a usable response - that is what
    tells the caller not to write a cache row.
    """
    last_error_type: str | None = None
    for attempt in range(_MAX_BACKOFF_ATTEMPTS):
        try:
            result = await analyze_frame(
                frame_path, context=context, settings=settings, client=client
            )
        except (GeminiTransportError, GeminiAPIError) as error:
            last_error_type = type(error).__name__
            is_last_attempt = attempt + 1 >= _MAX_BACKOFF_ATTEMPTS
            logger.warning(
                "gemini_call_failed",
                attempt=attempt + 1,
                max_attempts=_MAX_BACKOFF_ATTEMPTS,
                error_type=last_error_type,
                giving_up=is_last_attempt,
            )
            if is_last_attempt:
                break
            await asyncio.sleep(_backoff_delay(attempt))
            continue
        return result, True

    logger.warning("gemini_degraded_after_retries", error_type=last_error_type)
    return None, False


# --- rate limiting -------------------------------------------------------------


def _utc_date_str() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


@dataclass
class _DailyState:
    date: str
    count: int


class GeminiRateLimiter:
    """Token bucket for RPM, plus a calendar-day counter for the daily quota.

    **Durability choice** (recorded in the session report): the *daily*
    counter is persisted to a small JSON file at
    ``$DATA_DIR/gemini_rate_limit_state.json`` - the free tier's quota is
    per-day, so a restart late in the day must not hand back a full day's
    budget, which is exactly what `.claude/skills/gemini-free-tier` asks for
    ("Persist the daily counter; a restart must not reset the budget"). The
    *RPM* bucket stays in-memory only: a per-minute window is short enough
    that losing it across a restart costs at most one minute of throughput,
    and persisting it would put a disk write on every single request instead
    of only the (much rarer) daily rollover, for a guarantee nothing asked
    for.

    Not a new database table: this counts *attempts*, including attempts that
    never produced an answer (429, offline) and therefore never write a
    ``gemini_scene_cache`` row - folding it into the schema
    ``engine-architect`` owns for a different purpose (cached *answers*) would
    conflate two things that are allowed to disagree.
    """

    def __init__(self, *, rpm_limit: int, daily_limit: int, state_path: Path | None) -> None:
        self.rpm_limit = rpm_limit
        self.daily_limit = daily_limit
        self._state_path = state_path
        self._lock = asyncio.Lock()
        self._rpm_tokens = float(rpm_limit)
        self._rpm_updated_monotonic = time.monotonic()
        self._daily = self._load_daily_state()

    def _load_daily_state(self) -> _DailyState:
        today = _utc_date_str()
        if self._state_path is not None and self._state_path.is_file():
            try:
                document = json.loads(self._state_path.read_text(encoding="utf-8"))
                if isinstance(document, dict) and document.get("date") == today:
                    return _DailyState(date=today, count=int(document.get("count", 0)))
            except (OSError, json.JSONDecodeError, ValueError, TypeError):
                # Named: a missing, unreadable or hand-edited state file must
                # never block startup - it just starts today's count at zero,
                # the safe direction to be wrong in.
                logger.warning("gemini_rate_limit_state_unreadable")
        return _DailyState(date=today, count=0)

    def _save_daily_state(self) -> None:
        if self._state_path is None:
            return
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            self._state_path.write_text(
                json.dumps({"date": self._daily.date, "count": self._daily.count}),
                encoding="utf-8",
            )
        except OSError:
            logger.warning("gemini_rate_limit_state_write_failed")

    def _refill_rpm(self) -> None:
        now = time.monotonic()
        elapsed = max(0.0, now - self._rpm_updated_monotonic)
        self._rpm_updated_monotonic = now
        refill = elapsed * (self.rpm_limit / 60.0)
        self._rpm_tokens = min(float(self.rpm_limit), self._rpm_tokens + refill)

    async def try_acquire(self) -> bool:
        """Consume one token if both the daily and per-minute budgets allow it.

        Never blocks and never raises: a bucket that fails closed on the first
        check, before any request is built, is what makes "zero requests when
        exhausted" assertable against a mocked transport rather than a log
        line (`.claude/rules/gemini-usage.md`, gate criterion 6).
        """
        async with self._lock:
            today = _utc_date_str()
            if today != self._daily.date:
                self._daily = _DailyState(date=today, count=0)
            if self._daily.count >= self.daily_limit:
                return False
            self._refill_rpm()
            if self._rpm_tokens < 1.0:
                return False
            self._rpm_tokens -= 1.0
            self._daily.count += 1
            await asyncio.to_thread(self._save_daily_state)
            return True


# Keyed by `$DATA_DIR` rather than a bare module global: tests (and the gate)
# build a fresh scratch `Settings` per run, and a single shared instance would
# leak one run's daily count into the next. Recreated whenever the configured
# limits change, so a `.env` edit takes effect without a process restart.
_rate_limiters: dict[str, GeminiRateLimiter] = {}


def get_rate_limiter(settings: Settings) -> GeminiRateLimiter:
    """The process-wide limiter for this ``$DATA_DIR``, created once and reused."""
    key = str(settings.data_dir)
    limiter = _rate_limiters.get(key)
    if (
        limiter is None
        or limiter.rpm_limit != settings.gemini_rpm_limit
        or limiter.daily_limit != settings.gemini_daily_limit
    ):
        limiter = GeminiRateLimiter(
            rpm_limit=settings.gemini_rpm_limit,
            daily_limit=settings.gemini_daily_limit,
            state_path=settings.data_dir / _RATE_LIMIT_STATE_FILENAME,
        )
        _rate_limiters[key] = limiter
    return limiter


# --- the public entry point -----------------------------------------------------


async def analyze_scene_cached(
    session: AsyncSession,
    scene: Scene,
    frame_path: Path,
    *,
    settings: Settings,
    client: httpx.AsyncClient,
    prompt_version: int,
) -> SceneAnalysisOutcome:
    """Cache-first, rate-limited Gemini analysis for one scene. See module docstring."""
    cached = await _lookup_cache(session, scene.id, prompt_version)
    if cached is not None:
        return SceneAnalysisOutcome(result=_row_to_result(cached), source="cache")

    if not settings.gemini_api_key_set:
        logger.info("gemini_analysis_skipped_no_key", scene_id=scene.id)
        return SceneAnalysisOutcome(result=None, source="degraded")

    limiter = get_rate_limiter(settings)
    if not await limiter.try_acquire():
        logger.info("gemini_rate_limit_exhausted", scene_id=scene.id)
        return SceneAnalysisOutcome(result=None, source="degraded")

    context = _scene_context(scene)
    result, reached_api = await _call_with_backoff(
        frame_path, context=context, settings=settings, client=client
    )
    if not reached_api:
        return SceneAnalysisOutcome(result=None, source="degraded")

    await _write_cache_row(session, scene.id, prompt_version, result)
    return SceneAnalysisOutcome(result=result, source="api")


__all__ = [
    "GeminiRateLimiter",
    "SceneAnalysisOutcome",
    "analyze_scene_cached",
    "get_rate_limiter",
]
