"""Ingest: what gets probed, what gets derived, and what stays unknown.

Gate criteria 6, 7 and 8. Everything here is measured on the artifacts and rows
the job actually produced - ffprobe of the proxy, pixels of the strip - because
the failure being guarded against is a pipeline that reports success while
producing something subtly wrong.
"""

import json
import subprocess
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from conftest import Harness
from sqlalchemy import select

from repcut.db.models import DerivedArtifact, MediaBlob
from repcut.media.artifacts import PROXY_RECIPE, THUMBNAIL_STRIP_RECIPE, ArtifactKind
from repcut.media.ffmpeg_builder import thumbnail_frame_count
from repcut.media.store import absolute

# Beat sync, cut timing and interpolation all assume constant spacing;
# `.claude/rules/testing.md` fixes the tolerance at 40ms.
MAX_DRIFT_MS = 40.0


def _probe(path: Path, entries: str, *, stream: str = "v:0") -> dict[str, Any]:
    """Read stream properties off a rendered file.

    ``Any`` because ffprobe's JSON is genuinely mixed - ``width`` is an int,
    ``sample_rate`` is a string of digits, ``r_frame_rate`` is a rational in a
    string - and narrowing it here would mean casting at every call site
    instead of one.
    """
    completed = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            stream,
            "-show_entries",
            f"stream={entries}",
            "-of",
            "json",
            path.as_posix(),
        ],
        capture_output=True,
        check=True,
        timeout=60,
        text=True,
    )
    streams = json.loads(completed.stdout)["streams"]
    return dict(streams[0]) if streams else {}


def _format_duration(path: Path) -> float:
    completed = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "csv=p=0",
            path.as_posix(),
        ],
        capture_output=True,
        check=True,
        timeout=60,
        text=True,
    )
    return float(completed.stdout.strip())


def _stream_end_seconds(path: Path, stream: str) -> float:
    """When the last packet of a stream ends. The A/V drift measurement.

    Packet timestamps rather than the container's nominal duration: the nominal
    duration is metadata a muxer writes, and the question here is where the audio
    and video actually stop relative to each other.
    """
    completed = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            stream,
            "-show_entries",
            "packet=pts_time,duration_time",
            "-of",
            "csv=p=0",
            path.as_posix(),
        ],
        capture_output=True,
        check=True,
        timeout=120,
        text=True,
    )
    end = 0.0
    for line in completed.stdout.splitlines():
        parts = [part for part in line.strip().split(",") if part and part != "N/A"]
        if not parts:
            continue
        moment = float(parts[0]) + (float(parts[1]) if len(parts) > 1 else 0.0)
        end = max(end, moment)
    return end


async def _ingest(
    api: Harness, upload_clip: Callable[..., Awaitable[httpx.Response]], source: Path
) -> str:
    """Upload a clip, wait for its ingest, return the blob digest."""
    project = await api.client.post("/projects", json={"name": "session"})
    finalized = await upload_clip(project.json()["id"], source)
    assert finalized.status_code == 200, finalized.text
    await api.queue.drain()
    digest: str = finalized.json()["sha256"]
    return digest


async def _blob(api: Harness, digest: str) -> MediaBlob:
    async with api.session_factory() as session:
        blob = await session.get(MediaBlob, digest)
    assert blob is not None
    return blob


async def _artifact(api: Harness, digest: str, kind: ArtifactKind) -> Path:
    async with api.session_factory() as session:
        row = (
            (
                await session.execute(
                    select(DerivedArtifact).where(
                        DerivedArtifact.sha256 == digest,
                        DerivedArtifact.artifact_kind == kind.value,
                    )
                )
            )
            .scalars()
            .one()
        )
    return absolute(api.data_dir, row.stored_path)


# --- criterion 6: VFR normalization -----------------------------------------


async def test_a_vfr_source_becomes_a_cfr_proxy_within_drift_budget(
    api: Harness,
    make_clip: Callable[..., Path],
    upload_clip: Callable[..., Awaitable[httpx.Response]],
) -> None:
    """The trap the whole normalization exists for, measured end to end.

    The source's frames land at uneven intervals. The proxy's must not, and the
    audio must still end where the video ends - drift accumulates toward the end
    of a clip, so a short fixture understates it and a passing measurement here
    is the weaker claim, not the stronger one.
    """
    source = make_clip("vfr.mp4", seconds=4.0, variable_frame_rate=True)
    before = _probe(source, "r_frame_rate,avg_frame_rate")
    assert before["r_frame_rate"] != before["avg_frame_rate"], (
        "the fixture is not actually VFR - everything below would prove nothing"
    )

    digest = await _ingest(api, upload_clip, source)
    proxy = await _artifact(api, digest, ArtifactKind.PROXY)

    after = _probe(proxy, "r_frame_rate,avg_frame_rate")
    assert after["r_frame_rate"] == after["avg_frame_rate"] == f"{PROXY_RECIPE.fps}/1"

    drift_ms = abs(_stream_end_seconds(proxy, "v:0") - _stream_end_seconds(proxy, "a:0")) * 1000
    assert drift_ms < MAX_DRIFT_MS, f"A/V drift at end of clip was {drift_ms:.1f}ms"


async def test_a_vfr_source_is_recorded_as_variable(
    api: Harness,
    make_clip: Callable[..., Path],
    upload_clip: Callable[..., Awaitable[httpx.Response]],
) -> None:
    """MP4 can answer the question, so the column records a measurement."""
    source = make_clip("vfr.mp4", seconds=3.0, variable_frame_rate=True)

    blob = await _blob(api, await _ingest(api, upload_clip, source))

    assert blob.is_variable_frame_rate is True
    assert blob.fps_normalized == float(PROXY_RECIPE.fps)


async def test_a_cfr_source_is_recorded_as_constant(
    api: Harness,
    make_clip: Callable[..., Path],
    upload_clip: Callable[..., Awaitable[httpx.Response]],
) -> None:
    source = make_clip("cfr.mp4", seconds=2.0)

    blob = await _blob(api, await _ingest(api, upload_clip, source))

    assert blob.is_variable_frame_rate is False


async def test_a_container_that_cannot_answer_stores_null_not_false(
    api: Harness,
    make_clip: Callable[..., Path],
    upload_clip: Callable[..., Awaitable[httpx.Response]],
) -> None:
    """Matroska: same frames, same timestamps, and the heuristic says "constant".

    Measured, on this machine: one clip with deliberately uneven timestamps
    reports ``r_frame_rate`` 30/1 and ``avg_frame_rate`` 13/1 as MP4, and 13/1
    for *both* as Matroska. The r-versus-avg test is therefore a property of the
    container, not of the file, and its negative is a false negative.

    Writing ``False`` here would record "measured CFR" about a clip nobody
    measured. Prompt 05's beat sync reads this column to decide whether it can
    trust the frame cadence, so a false ``False`` reintroduces exactly the silent
    end-of-clip drift the column exists to prevent. ``None`` means unknown, and
    the sync path can fall back to timestamps.
    """
    matroska = make_clip("vfr.mkv", seconds=4.0, variable_frame_rate=True)
    rates = _probe(matroska, "r_frame_rate,avg_frame_rate")
    assert rates["r_frame_rate"] == rates["avg_frame_rate"], (
        "Matroska started reporting the two rates differently - if that is real, "
        "the container may now be answerable and _VFR_ANSWERABLE_FORMATS should say so"
    )

    blob = await _blob(api, await _ingest(api, upload_clip, matroska))

    assert blob.is_variable_frame_rate is None, (
        "an unanswerable container must store NULL (unknown), never False (measured CFR)"
    )
    assert blob.container_format is not None and "matroska" in blob.container_format


async def test_the_library_reports_unknown_vfr_as_null(
    api: Harness,
    make_clip: Callable[..., Path],
    upload_clip: Callable[..., Awaitable[httpx.Response]],
) -> None:
    """The three-valued column survives the API boundary intact.

    A schema that narrowed this to ``bool`` would collapse unknown into false at
    the one place the UI reads it.
    """
    matroska = make_clip("vfr.mkv", seconds=2.0, variable_frame_rate=True)
    project = await api.client.post("/projects", json={"name": "session"})
    project_id = project.json()["id"]
    await upload_clip(project_id, matroska)
    await api.queue.drain()

    library = await api.client.get(f"/projects/{project_id}/media")

    assert library.status_code == 200
    assert library.json()[0]["is_variable_frame_rate"] is None


# --- criterion 7: rotation metadata -----------------------------------------


@pytest.mark.parametrize(("degrees", "expected"), [(90, (360, 640)), (180, (640, 360))])
async def test_stored_resolution_is_the_display_resolution(
    api: Harness,
    make_clip: Callable[..., Path],
    upload_clip: Callable[..., Awaitable[httpx.Response]],
    degrees: int,
    expected: tuple[int, int],
) -> None:
    """Portrait phone video is landscape pixels plus a rotation tag.

    The 180 case is the one that catches an over-eager fix: a half turn rotates
    the picture without swapping its dimensions, so code that swaps on any
    non-zero rotation passes the 90 case and fails here.
    """
    source = make_clip("portrait.mp4", seconds=1.0, width=640, height=360, rotation=degrees)
    coded = _probe(source, "width,height")
    assert (coded["width"], coded["height"]) == (640, 360), "the tag must not re-encode the pixels"

    blob = await _blob(api, await _ingest(api, upload_clip, source))

    assert (blob.display_width, blob.display_height) == expected
    assert blob.rotation_degrees == degrees


async def test_a_rotated_source_proxies_at_its_display_height(
    api: Harness,
    make_clip: Callable[..., Path],
    upload_clip: Callable[..., Awaitable[httpx.Response]],
) -> None:
    """The proxy follows the display orientation, not the container's."""
    source = make_clip("portrait.mp4", seconds=1.0, width=1280, height=720, rotation=90)

    digest = await _ingest(api, upload_clip, source)
    proxy = await _artifact(api, digest, ArtifactKind.PROXY)

    rendered = _probe(proxy, "width,height")
    # Portrait: 720x1280 displayed, capped to 720 tall by the recipe.
    assert int(rendered["height"]) <= PROXY_RECIPE.height
    assert int(rendered["height"]) > int(rendered["width"]), "a portrait clip must stay portrait"


# --- criterion 8: ingest artifacts ------------------------------------------


async def test_the_proxy_is_720p_h264_with_audio_at_the_project_rate(
    api: Harness,
    make_clip: Callable[..., Path],
    upload_clip: Callable[..., Awaitable[httpx.Response]],
) -> None:
    """Playable, right duration, right dimensions, audio resampled once."""
    source = make_clip("clip.mp4", seconds=3.0, width=1920, height=1080)

    digest = await _ingest(api, upload_clip, source)
    proxy = await _artifact(api, digest, ArtifactKind.PROXY)

    video = _probe(proxy, "codec_name,height")
    audio = _probe(proxy, "codec_name,sample_rate,channels", stream="a:0")

    assert video["codec_name"] == "h264"
    assert int(video["height"]) == PROXY_RECIPE.height
    assert int(audio["sample_rate"]) == PROXY_RECIPE.audio_sample_rate
    assert int(audio["channels"]) == PROXY_RECIPE.audio_channels
    assert abs(_format_duration(proxy) - 3.0) <= 0.1


async def test_the_thumbnail_strip_has_one_cell_per_interval(
    api: Harness,
    make_clip: Callable[..., Path],
    upload_clip: Callable[..., Awaitable[httpx.Response]],
) -> None:
    """ceil(duration / 2) cells, asserted on the tiled image's own width."""
    source = make_clip("clip.mp4", seconds=5.0, width=320, height=180, audio=False)

    digest = await _ingest(api, upload_clip, source)
    strip = await _artifact(api, digest, ArtifactKind.THUMBNAIL_STRIP)

    tile = _probe(strip, "width,height")
    frames = thumbnail_frame_count(5.0, THUMBNAIL_STRIP_RECIPE)
    assert int(tile["height"]) == THUMBNAIL_STRIP_RECIPE.height
    assert int(tile["width"]) == frames * 320


async def test_ingest_records_every_probed_property(
    api: Harness,
    make_clip: Callable[..., Path],
    upload_clip: Callable[..., Awaitable[httpx.Response]],
) -> None:
    """No column left null by a job that reported success."""
    source = make_clip("clip.mp4", seconds=2.0, width=640, height=360)

    blob = await _blob(api, await _ingest(api, upload_clip, source))

    assert blob.container_format is not None
    assert blob.duration_seconds is not None and blob.duration_seconds > 0
    assert blob.video_codec == "h264"
    assert blob.audio_codec == "aac"
    assert blob.audio_sample_rate == 44100, "the *source* rate, not the proxy's"
    assert blob.fps_source == pytest.approx(30.0)
    assert blob.fps_normalized == float(PROXY_RECIPE.fps)


async def test_re_running_ingest_reuses_the_artifacts_it_already_made(
    api: Harness,
    make_clip: Callable[..., Path],
    upload_clip: Callable[..., Awaitable[httpx.Response]],
) -> None:
    """Idempotent: a second run re-encodes nothing and duplicates no row."""
    source = make_clip("clip.mp4", seconds=2.0)
    digest = await _ingest(api, upload_clip, source)
    proxy = await _artifact(api, digest, ArtifactKind.PROXY)
    first_written = proxy.stat().st_mtime_ns

    await api.queue.enqueue("ingest", sha256=digest)
    await api.queue.drain()

    async with api.session_factory() as session:
        rows = (
            (await session.execute(select(DerivedArtifact).where(DerivedArtifact.sha256 == digest)))
            .scalars()
            .all()
        )
    assert len(rows) == len(list(ArtifactKind))
    assert proxy.stat().st_mtime_ns == first_written, "the proxy was re-encoded"


async def test_a_missing_artifact_file_is_re_rendered(
    api: Harness,
    make_clip: Callable[..., Path],
    upload_clip: Callable[..., Awaitable[httpx.Response]],
) -> None:
    """A row whose file was deleted must not make ingest skip the render.

    Otherwise the library hands the player a path to nothing and nothing ever
    regenerates it - the artifact key is occupied by an absence.
    """
    source = make_clip("clip.mp4", seconds=2.0)
    digest = await _ingest(api, upload_clip, source)
    proxy = await _artifact(api, digest, ArtifactKind.PROXY)
    proxy.unlink()

    await api.queue.enqueue("ingest", sha256=digest)
    await api.queue.drain()

    assert proxy.is_file() and proxy.stat().st_size > 0
