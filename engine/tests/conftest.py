"""Shared pytest fixtures. CPU only, no network, no real media.

Every clip a test needs is generated at test time by ``ffmpeg -f lavfi``. No
media file is ever committed - the repository is public, and P4 says footage
stays on the machine (`.claude/rules/testing.md`).
"""

import shutil
import subprocess
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from repcut.config import Settings
from repcut.db import Base, create_engine, create_session_factory
from repcut.main import app

# Frames where n%3==0 or n%7==0. The gaps are uneven, so the timestamps are
# genuinely irregular rather than merely sparse - which is what makes the clip a
# VFR fixture instead of a low-frame-rate one.
_IRREGULAR_FRAMES = "select='not(mod(n,3))+not(mod(n,7))'"


@pytest.fixture
def make_clip(tmp_path: Path) -> Callable[..., Path]:
    """Factory for synthetic clips: choose duration, size, rate and VFR-ness.

    ``variable_frame_rate=True`` keeps the source frames' own timestamps instead
    of regenerating them, producing uneven gaps - the phone-footage trap that
    beat sync and interpolation drift on. It is written to MP4 because that is
    what phones produce, and because the container matters: see
    ``docs/reports/prompt-02.md``, Matroska reports the same clip as constant.

    Skips rather than fails when FFmpeg is absent, so the suite still runs on a
    machine that has not finished `make setup`. CI installs FFmpeg, so nothing
    that depends on this is skipped where it counts.
    """
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg is not on PATH; `make check-env` reports it with a fix")

    def _make(
        name: str = "clip.mp4",
        *,
        seconds: float = 3.0,
        fps: int = 30,
        width: int = 640,
        height: int = 360,
        audio: bool = True,
        variable_frame_rate: bool = False,
    ) -> Path:
        destination = tmp_path / name
        argv = [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size={width}x{height}:rate={fps}",
        ]
        if audio:
            argv += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100"]
        argv += ["-t", str(seconds)]
        if variable_frame_rate:
            argv += ["-vf", _IRREGULAR_FRAMES, "-fps_mode", "passthrough"]
        # crf 30 and ultrafast: these are throwaway fixtures, and every test that
        # builds one pays for the encode.
        argv += ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "30"]
        argv += ["-c:a", "aac", "-shortest"] if audio else ["-an"]
        argv += [destination.as_posix()]

        # check=True: a fixture that silently failed to generate turns every
        # assertion that follows into a confusing file-not-found.
        subprocess.run(argv, capture_output=True, check=True, timeout=120)
        return destination

    return _make


@pytest_asyncio.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    """In-process async client against the FastAPI app (no socket, no server)."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as async_client:
        yield async_client


@pytest_asyncio.fixture
async def db_engine(tmp_path: Path) -> AsyncIterator[AsyncEngine]:
    """A scratch database with the schema applied, thrown away after the test.

    Built from ``Base.metadata`` rather than by running migrations: this is the
    fast path for model behaviour, and ``test_migrations.py`` separately proves
    the migration and the models describe the same schema.
    """
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'test.db').as_posix()}",
    )
    engine = create_engine(settings)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    yield engine

    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """One session against the scratch database."""
    factory = create_session_factory(db_engine)
    async with factory() as session:
        yield session
