"""Repcut engine FastAPI application.

Importable as ``repcut.main:app`` for uvicorn.

Startup state - the database engine, the session factory and the job worker -
lives on ``app.state`` and is installed by ``start_engine`` rather than inline in
the lifespan. That is not indirection for its own sake: httpx's
``ASGITransport`` never opens a lifespan scope, so anything wired *only* inside
the lifespan is absent from every test and every gate while being present in
production. ``config.get_settings`` documents the same trap for the cloud-sync
warning, which is where it was first paid for. Tests call ``start_engine`` and
``stop_engine`` directly, against a scratch ``$DATA_DIR``, and therefore exercise
the same wiring the real boot does.
"""

import asyncio
import importlib.util
import tempfile
from collections.abc import AsyncIterator, Iterable, Iterator
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.routing import APIWebSocketRoute
from starlette.routing import BaseRoute

from repcut import __version__
from repcut.analysis.pipeline import ANALYSIS_JOB_TYPE, run_analysis
from repcut.api import jobs as jobs_api
from repcut.api import media as media_api
from repcut.api import projects as projects_api
from repcut.api import scenes as scenes_api
from repcut.api import uploads as uploads_api
from repcut.api.errors import install_error_handler
from repcut.api.jobs import JOBS_SOCKET_PATH
from repcut.config import Settings, get_settings
from repcut.db import create_engine, create_session_factory
from repcut.db.migrations import upgrade_to_head
from repcut.jobs import JobQueue
from repcut.logging import configure_logging, get_logger
from repcut.loop import can_spawn_subprocesses, check_running_loop, running_loop_name
from repcut.media.ingest import INGEST_JOB_TYPE, run_ingest
from repcut.models import HealthResponse
from repcut.probes import probe_ffmpeg, probe_torch
from repcut.prompt_tracker import PromptsTrackerResponse, get_all_prompts_status
from repcut.security import install_security_middleware, warn_if_bound_publicly

logger = get_logger(__name__)


def _check_data_dir_writable(data_dir: Path) -> bool:
    """Create the data directory if needed and confirm it accepts a write.

    Blocking filesystem work - callers run it via ``asyncio.to_thread``.
    """
    probe_path: Path | None = None
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        try:
            with tempfile.NamedTemporaryFile(
                dir=data_dir, prefix=".repcut-write-probe-", delete=False
            ) as handle:
                probe_path = Path(handle.name)
                handle.write(b"ok")
        finally:
            # delete=False means the file outlives a mid-write failure (ENOSPC,
            # quota). Cleaning up only on the success path would leave a stray
            # .repcut-write-probe-* in DATA_DIR for every failed /health hit.
            if probe_path is not None:
                probe_path.unlink(missing_ok=True)
    except PermissionError:
        # Named: the directory exists but this user cannot write to it
        # (read-only mount, a sync client holding a lock, ACL). Report, do not raise.
        logger.warning("data_dir_not_writable")
        return False
    except OSError:
        # Named: mkdir/write failures below PermissionError - ENOSPC, ENOENT on
        # a missing drive letter, path too long on Windows.
        logger.warning("data_dir_probe_os_error")
        return False
    return True


@lru_cache(maxsize=1)
def _websocket_protocol_installed() -> bool:
    """Whether uvicorn has anything to speak the WebSocket protocol with.

    ``uvicorn --ws auto`` silently degrades to ``none`` when neither library is
    importable, and then every handshake is refused by the *server*, before any
    application code runs. Nothing in the engine would notice; the only symptom
    is a jobs panel that never connects.
    """
    return any(importlib.util.find_spec(name) is not None for name in ("websockets", "wsproto"))


def _walk_routes(routes: Iterable[BaseRoute]) -> Iterator[BaseRoute]:
    """Every route, including those inside an included router.

    ``app.routes`` is not flat: this FastAPI keeps each ``include_router`` as a
    single ``_IncludedRouter`` entry holding the real routes on
    ``original_router``. A scan over the top level therefore finds ``/health``
    and nothing else - it reported the jobs socket as unmounted on a perfectly
    healthy engine, which is a red status row for a working product and exactly
    as misleading as the green one it replaces.

    Both shapes are followed because both exist across FastAPI versions.
    ``test_health_reports_the_jobs_socket_ready_on_a_booted_engine`` is what
    keeps this honest: if a future version nests them differently again, that
    test fails rather than the status page quietly going red.
    """
    for route in routes:
        yield route
        for attribute in ("routes", "original_router"):
            nested = getattr(route, attribute, None)
            if isinstance(nested, list):
                yield from _walk_routes(nested)
            elif nested is not None and hasattr(nested, "routes"):
                yield from _walk_routes(nested.routes)


def jobs_socket_ready(app: FastAPI) -> bool:
    """Whether ``/ws/jobs`` can actually serve, as opposed to merely existing.

    Three things have to hold, and each has been false at some point in a
    running engine: the route is mounted at the path the UI asks for, the job
    worker is alive to have anything to say, and the server can speak the
    protocol at all.

    This is deliberately the *engine's* half of the answer. It cannot see a
    browser refusing the connection under a Content-Security-Policy, which is
    what actually broke the socket in Prompt 02 - the status page pairs this
    with a probe made from the browser itself, because a green row here with a
    red row there is precisely the distinction the user needs.
    """
    mounted = any(
        isinstance(route, APIWebSocketRoute) and route.path == JOBS_SOCKET_PATH
        for route in _walk_routes(app.routes)
    )
    queue: JobQueue | None = getattr(app.state, "job_queue", None)
    worker_alive = queue is not None and queue.is_running
    return mounted and worker_alive and _websocket_protocol_installed()


async def start_engine(app: FastAPI, settings: Settings) -> None:
    """Migrate, then install the database and job worker onto ``app.state``.

    Called by the lifespan in production and directly by tests. The handler
    registry is built here so a job type cannot exist in the database without
    something able to run it.

    Migrations run first and are not optional. The job worker queries ``jobs``
    the moment it starts, so against an unmigrated ``$DATA_DIR`` the engine died
    during boot with ``no such table: jobs`` - which is how this ordering was
    found. See ``repcut.db.migrations`` for why the engine migrates itself.
    """
    await asyncio.to_thread(upgrade_to_head, settings)
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    queue = JobQueue(
        settings=settings,
        session_factory=session_factory,
        handlers={INGEST_JOB_TYPE: run_ingest, ANALYSIS_JOB_TYPE: run_analysis},
    )
    await queue.start()

    app.state.settings = settings
    app.state.db_engine = engine
    app.state.session_factory = session_factory
    app.state.job_queue = queue


async def stop_engine(app: FastAPI) -> None:
    """Stop the worker and dispose the connection pool."""
    queue: JobQueue | None = getattr(app.state, "job_queue", None)
    if queue is not None:
        await queue.stop()
    engine = getattr(app.state, "db_engine", None)
    if engine is not None:
        await engine.dispose()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Configure logging, then bring the database and job worker up."""
    # Logging is configured twice on purpose. Resolving settings emits the
    # cloud-sync warning (see get_settings), and that line has to render as JSON
    # like every other engine line - but the level it renders at is itself a
    # setting. So: defaults first, resolve, then the configured level.
    # configure_logging is idempotent by contract.
    configure_logging()
    settings = get_settings()
    configure_logging(settings.log_level)
    # After logging is configured, so the warning is actually rendered.
    warn_if_bound_publicly(settings.engine_host)
    # Reads the loop the server is actually serving on, which is the only way to
    # catch a launcher that bypassed `python -m repcut`. Logs which loop it got
    # either way, so the answer is in the boot output rather than in a guess.
    check_running_loop()
    await start_engine(app, settings)
    logger.info(
        "engine_started",
        engine_version=__version__,
        event_loop=running_loop_name(),
        torch_device_preference=settings.torch_device,
        gemini_api_key_set=settings.gemini_api_key_set,
    )
    yield
    await stop_engine(app)
    logger.info("engine_stopped")


app = FastAPI(
    title="Repcut Engine",
    version=__version__,
    summary="Local-first video editing engine for Repcut.",
    lifespan=lifespan,
)

# Attached at import time, not in the lifespan: Starlette freezes the middleware
# stack when the app first handles a request, and httpx's ASGITransport never
# opens a lifespan scope - so middleware added there would be absent from every
# test and every gate while being present in production. That is the same trap
# `get_settings` documents for the cloud-sync warning, and it is worse here,
# because the thing that silently would not run is the security boundary.
install_security_middleware(app, get_settings())
install_error_handler(app)

app.include_router(projects_api.router)
app.include_router(uploads_api.router)
app.include_router(media_api.router)
app.include_router(scenes_api.router)
app.include_router(jobs_api.router)


@app.get("/health", response_model=HealthResponse, tags=["system"])
async def health(request: Request) -> HealthResponse:
    """Report engine and local toolchain capability.

    Always 200, even with no FFmpeg and no torch - the UI needs to render the
    gap rather than see a failed request. Every probe blocks, so all three run
    in threads concurrently and none touches the event loop.
    """
    settings: Settings = get_settings()

    ffmpeg, torch_info, data_dir_writable = await asyncio.gather(
        asyncio.to_thread(probe_ffmpeg),
        asyncio.to_thread(probe_torch, settings.torch_device),
        asyncio.to_thread(_check_data_dir_writable, settings.data_dir),
    )

    # Asked of the loop serving this very request, so it reports the engine's
    # real capability rather than what its launcher intended. An FFmpeg on PATH
    # is not enough: on a loop with no subprocess transport the engine can see
    # the binary and still be unable to run it, which is exactly the state that
    # made every upload fail while /health said the toolchain was fine.
    event_loop = asyncio.get_running_loop()

    return HealthResponse(
        engine_version=__version__,
        event_loop=type(event_loop).__name__,
        event_loop_can_spawn=can_spawn_subprocesses(event_loop),
        jobs_socket_ready=jobs_socket_ready(request.app),
        ffmpeg_version=ffmpeg.version,
        ffmpeg_has_libx264=ffmpeg.has_libx264,
        cuda_available=torch_info.cuda_available,
        gpu_name=torch_info.gpu_name,
        vram_free_mb=torch_info.vram_free_mb,
        vram_total_mb=torch_info.vram_total_mb,
        torch_device_active=torch_info.device,
        data_dir_writable=data_dir_writable,
        gemini_api_key_set=settings.gemini_api_key_set,
    )


@app.get("/prompts", response_model=PromptsTrackerResponse, tags=["system"])
async def prompts_status() -> PromptsTrackerResponse:
    """Report live Repcut build plan prompt completion status and metrics."""
    return await asyncio.to_thread(get_all_prompts_status)
