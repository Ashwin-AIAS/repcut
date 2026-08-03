"""Repcut engine FastAPI application.

Importable as ``repcut.main:app`` for uvicorn.
"""

import asyncio
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from repcut import __version__
from repcut.config import Settings, get_settings
from repcut.logging import configure_logging, get_logger
from repcut.models import HealthResponse
from repcut.probes import probe_ffmpeg, probe_torch

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


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Configure logging on startup; nothing to tear down yet."""
    settings = get_settings()
    configure_logging(settings.log_level)
    logger.info(
        "engine_started",
        engine_version=__version__,
        torch_device_preference=settings.torch_device,
        gemini_api_key_set=settings.gemini_api_key_set,
    )
    yield
    logger.info("engine_stopped")


app = FastAPI(
    title="Repcut Engine",
    version=__version__,
    summary="Local-first video editing engine for Repcut.",
    lifespan=lifespan,
)


@app.get("/health", response_model=HealthResponse, tags=["system"])
async def health() -> HealthResponse:
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

    return HealthResponse(
        engine_version=__version__,
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
