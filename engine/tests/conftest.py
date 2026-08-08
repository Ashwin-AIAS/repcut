"""Shared pytest fixtures. CPU only, no network, no real media.

Every clip a test needs is generated at test time by ``ffmpeg -f lavfi``. No
media file is ever committed - the repository is public, and P4 says footage
stays on the machine (`.claude/rules/testing.md`).
"""

import asyncio
import hashlib
import shutil
import subprocess
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from repcut.config import Settings
from repcut.db import Base, create_engine, create_session_factory
from repcut.jobs import JobQueue
from repcut.main import app, start_engine, stop_engine

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
        rotation: int | None = None,
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

        if rotation is not None:
            _write_rotation(destination, rotation)
        return destination

    return _make


def _write_rotation(clip: Path, degrees: int) -> None:
    """Stamp a display-matrix rotation onto an existing clip.

    ``lavfi`` cannot produce one, and rotation is the trap that makes portrait
    phone video's stored dimensions a lie - so the fixture has to carry a real
    tag rather than a simulated one. ``-display_rotation`` on the *input* plus a
    stream copy writes the side data without re-encoding: measured to produce
    ``side_data_list: [{"rotation": 90}]``, which is exactly what a phone writes.
    """
    rotated = clip.with_name(f"rotated-{clip.name}")
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "error",
            "-y",
            "-display_rotation",
            str(degrees),
            "-i",
            clip.as_posix(),
            "-c",
            "copy",
            rotated.as_posix(),
        ],
        capture_output=True,
        check=True,
        timeout=120,
    )
    rotated.replace(clip)


@pytest_asyncio.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    """In-process async client against the FastAPI app (no socket, no server).

    ``base_url`` is a loopback name, not httpx's default ``http://test``, because
    the host allow-list in ``repcut.security`` is real middleware and would
    reject the latter with a 400. Pointing the fixture at a host the engine
    actually answers to keeps the boundary under test rather than around it - a
    test client exempted from the allow-list would prove nothing about it.
    """
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as async_client:
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


@dataclass(frozen=True, slots=True)
class Harness:
    """A running engine against a scratch ``$DATA_DIR``, plus its internals.

    Tests reach the session factory and the job queue directly rather than
    through the API, because some of what has to be asserted - a row that was
    *not* written, a proxy that was *not* re-encoded - has no endpoint by design.
    """

    client: httpx.AsyncClient
    settings: Settings
    session_factory: async_sessionmaker[AsyncSession]
    queue: JobQueue

    @property
    def data_dir(self) -> Path:
        return self.settings.data_dir


@pytest_asyncio.fixture
async def api(tmp_path: Path) -> AsyncIterator[Harness]:
    """The whole engine, wired the way ``main.lifespan`` wires it.

    Deliberately not a hand-assembled subset: ``start_engine`` is the same
    function the real boot calls, so a dependency someone forgets to install
    fails here rather than only under uvicorn. httpx's ``ASGITransport`` never
    opens a lifespan scope, which is why the wiring lives in a function both can
    call - see ``repcut.main``.

    The schema comes from ``start_engine``'s own migration step rather than from
    ``Base.metadata.create_all``. It costs a second per test and buys the thing
    that matters: the boot path that failed against an unmigrated ``$DATA_DIR``
    is the boot path under test. ``db_engine`` below still uses ``create_all``
    for model-level tests, where nothing boots.
    """
    settings = Settings(
        data_dir=tmp_path / "data",
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'engine.db').as_posix()}",
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


@pytest.fixture
def sha256_of() -> Callable[[Path], str]:
    """The digest the engine will compute for a file, for a test to declare."""

    def _digest(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    return _digest


@pytest.fixture
def upload_clip(api: Harness) -> Callable[..., Awaitable[httpx.Response]]:
    """Transfer a file the way a browser would: declare, chunk, finalize.

    Returns the finalize response, including its error responses - a test for a
    rejected upload needs the status code, not an exception.
    """

    async def _upload(
        project_id: str,
        path: Path,
        *,
        display_name: str | None = None,
        chunk_size: int = 64 * 1024,
        declare_hash: bool = True,
    ) -> httpx.Response:
        payload = await asyncio.to_thread(path.read_bytes)
        body: dict[str, object] = {
            "display_name": display_name or path.name,
            "size_bytes": len(payload),
            "chunk_size_bytes": chunk_size,
        }
        if declare_hash:
            body["sha256"] = hashlib.sha256(payload).hexdigest()

        opened = await api.client.post(f"/projects/{project_id}/uploads", json=body)
        if opened.status_code >= 400:
            return opened
        upload_id = opened.json()["id"]

        offset = opened.json()["bytes_received"]
        while offset < len(payload):
            chunk = payload[offset : offset + chunk_size]
            sent = await api.client.put(
                f"/uploads/{upload_id}/chunk",
                params={"offset": offset},
                content=chunk,
            )
            if sent.status_code >= 400:
                return sent
            offset = sent.json()["bytes_received"]

        return await api.client.post(f"/uploads/{upload_id}/finalize")

    return _upload
