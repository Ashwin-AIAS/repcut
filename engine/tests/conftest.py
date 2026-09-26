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
        hdr: bool = False,
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
        if hdr:
            _write_hdr_tags(destination)
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


def _write_hdr_tags(clip: Path) -> None:
    """Stamp BT.2020 primaries / HLG transfer / bt2020nc matrix onto an existing clip.

    This is *not* real HDR footage - real gym clips shot on a phone in HLG mode
    are HEVC Main 10, and Prompt 03's fixtures stay H.264 8-bit per
    ``docs/prompts/run-prompt-03.md``: this is ``lavfi`` plus a colour tag,
    deliberately, so nothing HDR-shaped is committed. It exists to give the
    frame extractor's tone-map path something to trigger on: a real header that
    says "this is not BT.709" rather than a filename convention.

    Unlike ``_write_rotation``, this is **not** a stream copy - measured, not
    assumed, on this repo's FFmpeg (8.1 at the time of writing):

    - A ``-c copy`` pass with ``-color_primaries``/``-color_trc``/``-colorspace``
      on the output *does* get picked up by ffprobe. That is not evidence a copy
      is enough: for MP4 these three are a container-level ``colr`` atom, and a
      copy only ever rewrites the container. A reader that decodes frames rather
      than trusting the container tag would still see whatever the bitstream's
      VUI parameters say - which a copy never touches.
    - A re-encode with only the generic ``-color_primaries``/``-color_trc``/
      ``-colorspace`` *output* options was measured to write the matrix
      coefficient into libx264's VUI but silently drop primaries and transfer:
      ffprobe read back ``color_primaries=unknown``, ``color_transfer=unknown``
      for a stream encoded that way, with no warning from FFmpeg. libx264 only
      emits the full ``colour_description`` triad through its own parameter
      block, so this passes it there too, via ``-x264-params``, alongside the
      generic flags so the container-level tag and the bitstream agree.

    Re-encodes video (cheap: these are throwaway fixtures, ``ultrafast``/``crf
    30`` like every other fixture here) and stream-copies audio, so duration and
    the audio track survive untouched.
    """
    tagged = clip.with_name(f"hdr-{clip.name}")
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "error",
            "-y",
            "-i",
            clip.as_posix(),
            "-map",
            "0",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "30",
            "-pix_fmt",
            "yuv420p",
            "-color_primaries",
            "bt2020",
            "-color_trc",
            "arib-std-b67",
            "-colorspace",
            "bt2020nc",
            "-color_range",
            "tv",
            "-x264-params",
            "colorprim=bt2020:transfer=arib-std-b67:colormatrix=bt2020nc",
            "-c:a",
            "copy",
            tagged.as_posix(),
        ],
        capture_output=True,
        check=True,
        timeout=120,
    )
    tagged.replace(clip)


@pytest.fixture
def make_motion_loudness_clip(tmp_path: Path) -> Callable[..., Path]:
    """Factory for a clip with a deliberate motion and loudness step, mid-clip.

    Two concatenated segments, built in one FFmpeg invocation via
    ``filter_complex concat`` rather than two encodes muxed afterwards, so
    there is exactly one file and one clean cut for a scene detector to find:

    - **Segment A** - a static black frame (zero inter-frame difference: no
      motion at all) with a quiet sine tone.
    - **Segment B** - ``testsrc2`` (continuously moving) at a louder tone.

    Measured, not assumed: per-frame mean luma (``signalstats.YAVG``) sits flat
    at 16 through segment A and varies frame to frame (~125.0-125.4) through
    segment B, and RMS level (``astats``) measures roughly -38.6dB in segment A
    versus roughly -25.0dB in segment B - a ~13.6dB step. Both differences are
    large enough that a per-segment energy measurement cannot land on the same
    value by accident, which is the point: this fixture is what backs the
    "energy curves are not flat" gate criterion.

    The hard cut from a solid colour to a full test pattern is also a strong,
    unambiguous scene boundary - deliberately, so the same fixture exercises
    scene detection and energy measurement together instead of needing two.
    """
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg is not on PATH; `make check-env` reports it with a fix")

    def _make(
        name: str = "motion.mp4",
        *,
        segment_seconds: float = 2.0,
        fps: int = 30,
        width: int = 640,
        height: int = 360,
        quiet_volume: float = 0.02,
        loud_volume: float = 0.9,
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
            f"color=c=black:size={width}x{height}:rate={fps}:duration={segment_seconds}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=220:sample_rate=44100:duration={segment_seconds}",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size={width}x{height}:rate={fps}:duration={segment_seconds}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=880:sample_rate=44100:duration={segment_seconds}",
            "-filter_complex",
            (
                f"[1:a]volume={quiet_volume}[a0];"
                f"[3:a]volume={loud_volume}[a1];"
                "[0:v][a0][2:v][a1]concat=n=2:v=1:a=1[v][a]"
            ),
            "-map",
            "[v]",
            "-map",
            "[a]",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "30",
            "-c:a",
            "aac",
            destination.as_posix(),
        ]
        # check=True: a fixture that silently failed to generate turns every
        # assertion that follows into a confusing file-not-found.
        subprocess.run(argv, capture_output=True, check=True, timeout=120)
        return destination

    return _make


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
        # Explicit, not merely the field's default: pydantic-settings still
        # reads a developer's real `.env` for any field this constructor does
        # not pass, and unlike every other harness in this suite, `api` boots a
        # real job worker that (since Prompt 03) auto-enqueues analysis on
        # every upload. Without this, a machine with a real GEMINI_API_KEY
        # configured for actual development would make live Gemini calls from
        # ordinary ingest/upload tests - `.claude/rules/testing.md` forbids
        # that unconditionally, not just for tests that mean to touch Gemini.
        gemini_api_key=None,
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
