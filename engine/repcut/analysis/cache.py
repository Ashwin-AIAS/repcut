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
3. **Rate limiter, before every request, fails closed.** One token per HTTP
   request actually sent - backoff retries and the malformed-JSON retry
   included - not one per scene. A bucket exhausted at either the per-minute
   or the per-day budget stops the next request from being made, and the
   scene degrades with no cache row.
4. **Call :func:`~repcut.analysis.gemini_client.analyze_frame`.** On any
   completed round trip - a parsed success, or "reached the API but the body
   never parsed even after its own one retry" - write a cache row. This is
   the line ``GeminiSceneCache``'s own docstring draws: a row means an attempt
   was made, not that the attempt found anything.
5. **A transport error or a 429/5xx is retried, capped - never within the
   same second.** Gemini's own ``RetryInfo`` sets the wait when it sends one;
   otherwise exponential backoff with jitter. A 429 naming a per-day quota is
   not retried at all, and spends the limiter's day so the rest of the job
   sends nothing. If no attempt reaches Gemini with a usable HTTP response,
   degrade - no cache row, because the scene was never actually analyzed and
   the next run must try again (``freshness.analysis_current`` is what
   schedules that run).
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import httpx
from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repcut.analysis.gemini_client import (
    GeminiAPIError,
    GeminiRateLimitedError,
    GeminiSceneResult,
    GeminiTransportError,
    SceneContext,
    analyze_frame,
)
from repcut.config import Settings
from repcut.db.models import GeminiSceneCache, Scene, utcnow
from repcut.logging import get_logger
from repcut.media.store import absolute, gemini_rate_limit_state_path

logger = get_logger(__name__)

# The skill's own number (`.claude/skills/gemini-free-tier`: "capped at ~3
# attempts"). The recovery mechanism for a genuinely exhausted quota is
# graceful degradation to heuristic tags after the cap, not a multi-minute
# retry loop that stalls the whole analysis job for one scene.
_MAX_BACKOFF_ATTEMPTS = 3
_BACKOFF_JITTER_FRACTION = 0.5

# A request that never reached Gemini (offline, DNS, reset): a short blip is
# the only case a retry can fix, so the waits stay short.
_TRANSPORT_BACKOFF_BASE_SECONDS = 0.2
_TRANSPORT_BACKOFF_MAX_SECONDS = 2.0

# Gemini answered 429 or 5xx: the server is pushing back, and asking again
# inside the same second only spends another request on the same refusal - which
# is what the old 0.2s schedule did, three times per scene in ~1.3s. Gemini's
# own `RetryInfo` wins when it sends one; the exponential schedule is only the
# fallback for a body that does not say.
_PUSHBACK_BACKOFF_BASE_SECONDS = 2.0
_PUSHBACK_BACKOFF_MAX_SECONDS = 30.0
_PUSHBACK_MIN_SECONDS = 1.0
# The longest `RetryInfo` this job will sit through for one scene. A longer ask
# (a per-minute window with most of the minute left is ~60s) degrades the scene
# instead: no cache row, so the next run asks again.
_MAX_RETRY_WAIT_SECONDS = 60.0


def _sleep(seconds: float) -> Awaitable[None]:
    """``asyncio.sleep``, behind a seam tests replace to observe the waits."""
    return asyncio.sleep(seconds)


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
        return GeminiSceneResult.model_validate(document)
    except (json.JSONDecodeError, ValidationError):
        # Named: a row that is not JSON, or JSON that no longer fits the
        # schema (a field tightened since it was written). One stale row must
        # not fail the whole analysis job; it reads as a cached null answer.
        logger.warning("gemini_cache_row_unparseable", scene_id=row.scene_id)
        return None


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


def _jittered(base: float) -> float:
    # A retry delay's jitter, not a cryptographic use.
    return base + base * _BACKOFF_JITTER_FRACTION * random.random()  # noqa: S311


def _backoff_delay(attempt: int) -> float:
    """Exponential delay with jitter, capped - for a request that never arrived."""
    # `2.0 ** attempt`, not `2 ** attempt`: int.__pow__ with a non-literal
    # exponent types as `Any` in typeshed (it can be negative), which would
    # silently make every downstream float here `Any` too.
    return _jittered(
        min(_TRANSPORT_BACKOFF_MAX_SECONDS, _TRANSPORT_BACKOFF_BASE_SECONDS * (2.0**attempt))
    )


def _pushback_delay(attempt: int, retry_delay_seconds: float | None) -> float | None:
    """Seconds to wait before retrying a 429/5xx, or None to stop asking.

    Gemini's ``RetryInfo`` is honoured as a floor - the jitter only ever adds -
    and a request it asks to hold for longer than one scene is worth ends the
    retries instead. Without one, capped exponential backoff with jitter. Never
    under a second either way (`.claude/rules/gemini-usage.md`).
    """
    if retry_delay_seconds is not None:
        if retry_delay_seconds > _MAX_RETRY_WAIT_SECONDS:
            return None
        base = max(_PUSHBACK_MIN_SECONDS, retry_delay_seconds)
    else:
        base = min(_PUSHBACK_BACKOFF_MAX_SECONDS, _PUSHBACK_BACKOFF_BASE_SECONDS * (2.0**attempt))
    return _jittered(base)


def _retry_delay(error: GeminiTransportError | GeminiAPIError, attempt: int) -> float | None:
    """How long to wait before the next attempt, or None if there must not be one."""
    if isinstance(error, GeminiTransportError):
        return _backoff_delay(attempt)
    pushback = (
        error.status_code == httpx.codes.TOO_MANY_REQUESTS
        or error.status_code >= httpx.codes.INTERNAL_SERVER_ERROR
    )
    # A 4xx other than 429 (bad key, bad request) answers the same way every
    # time; retrying it only spends tokens on a known refusal. And a per-day
    # quota is not a blip: nothing inside this job outlasts it.
    if not pushback or error.quota.scope == "per_day":
        return None
    return _pushback_delay(attempt, error.quota.retry_delay_seconds)


async def _call_with_backoff(
    frame_path: Path,
    *,
    context: SceneContext,
    settings: Settings,
    client: httpx.AsyncClient,
    limiter: GeminiRateLimiter,
    on_send: Callable[[], Awaitable[None]] | None,
    on_retry_wait: Callable[[float], Awaitable[None]] | None,
) -> tuple[GeminiSceneResult | None, bool]:
    """Call ``analyze_frame``, retrying only transport/HTTP failures.

    A malformed-JSON result (``analyze_frame`` returning ``None`` *without*
    raising) is not retried here - it already got its one retry inside
    ``analyze_frame`` itself. This loop exists for the other failure class:
    the request never got a usable HTTP response from Gemini at all (offline,
    429, 5xx), where asking again after a wait might succeed. How long that
    wait is, and whether there is one at all, is :func:`_retry_delay`'s call.

    A 429 naming a *per-day* quota also spends the limiter's day
    (:meth:`GeminiRateLimiter.exhaust_today`): Gemini has just said the real
    daily budget is gone, so every later scene in this job - and every job
    until the date turns - degrades without sending a frame to be refused.

    Returns ``(result, reached_api)``. ``reached_api`` is False only when
    every attempt failed to reach Gemini with a usable response - that is what
    tells the caller not to write a cache row. A limiter refusal ends the loop
    at once, the same way: retrying would only ask the same empty bucket.

    ``on_send`` fires after a token is granted and immediately before the
    request it pays for - the one point where a frame is certainly about to
    leave, which is what the P4 disclosure must mark (never a cache hit, a
    missing key, or a refused token). ``on_retry_wait`` fires before each wait,
    with its length, so a job paused on a quota says so instead of looking hung.
    """

    async def acquire_and_announce() -> bool:
        if not await limiter.try_acquire():
            return False
        if on_send is not None:
            await on_send()
        return True

    last_error_type: str | None = None
    for attempt in range(_MAX_BACKOFF_ATTEMPTS):
        try:
            result = await analyze_frame(
                frame_path,
                context=context,
                settings=settings,
                client=client,
                acquire_token=acquire_and_announce,
            )
        except GeminiRateLimitedError:
            logger.info("gemini_rate_limit_exhausted", attempt=attempt + 1)
            return None, False
        except (GeminiTransportError, GeminiAPIError) as error:
            last_error_type = type(error).__name__
            if isinstance(error, GeminiAPIError) and error.quota.scope == "per_day":
                await limiter.exhaust_today()
                logger.warning(
                    "gemini_daily_quota_exhausted",
                    quota_id=error.quota.quota_id,
                    quota_value=error.quota.quota_value,
                    configured_daily_limit=limiter.daily_limit,
                )
            delay = None if attempt + 1 >= _MAX_BACKOFF_ATTEMPTS else _retry_delay(error, attempt)
            logger.warning(
                "gemini_call_failed",
                attempt=attempt + 1,
                max_attempts=_MAX_BACKOFF_ATTEMPTS,
                error_type=last_error_type,
                giving_up=delay is None,
                retry_in_seconds=None if delay is None else round(delay, 2),
            )
            if delay is None:
                break
            if on_retry_wait is not None:
                await on_retry_wait(delay)
            await _sleep(delay)
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
                # Named: an unreadable or hand-edited state file. It must not
                # block startup, but zero would hand back a full day's quota -
                # so today's budget counts as spent. Tomorrow starts fresh.
                logger.warning("gemini_rate_limit_state_unreadable")
                return _DailyState(date=today, count=self.daily_limit)
        return _DailyState(date=today, count=0)

    def _save_daily_state(self) -> None:
        if self._state_path is None:
            return
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            # Temp file then rename: a crash mid-write must leave the previous
            # count, never a truncated file (which would read as unreadable).
            partial = self._state_path.with_name(self._state_path.name + ".partial")
            partial.write_text(
                json.dumps({"date": self._daily.date, "count": self._daily.count}),
                encoding="utf-8",
            )
            partial.replace(self._state_path)
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

    async def exhaust_today(self) -> None:
        """Count today's budget as spent, because Gemini said its daily quota is.

        Persisted like any other spend, so a restart does not hand back a day
        the provider has already closed. The provider's day and this counter's
        UTC day need not line up; the cost of that is at most one refused
        request after the UTC date turns, which lands back here.
        """
        async with self._lock:
            today = _utc_date_str()
            if today != self._daily.date:
                self._daily = _DailyState(date=today, count=0)
            self._daily.count = max(self._daily.count, self.daily_limit)
            await asyncio.to_thread(self._save_daily_state)

    def has_budget_today(self) -> bool:
        """Whether a request could still be granted today. Reads; never spends."""
        if _utc_date_str() != self._daily.date:
            return self.daily_limit > 0
        return self._daily.count < self.daily_limit


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
            state_path=absolute(settings.data_dir, gemini_rate_limit_state_path()),
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
    on_send: Callable[[], Awaitable[None]] | None = None,
    on_retry_wait: Callable[[float], Awaitable[None]] | None = None,
) -> SceneAnalysisOutcome:
    """Cache-first, rate-limited Gemini analysis for one scene. See module docstring."""
    cached = await _lookup_cache(session, scene.id, prompt_version)
    if cached is not None:
        return SceneAnalysisOutcome(result=_row_to_result(cached), source="cache")

    if not settings.gemini_api_key_set:
        logger.info("gemini_analysis_skipped_no_key", scene_id=scene.id)
        return SceneAnalysisOutcome(result=None, source="degraded")

    context = _scene_context(scene)
    result, reached_api = await _call_with_backoff(
        frame_path,
        context=context,
        settings=settings,
        client=client,
        limiter=get_rate_limiter(settings),
        on_send=on_send,
        on_retry_wait=on_retry_wait,
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
