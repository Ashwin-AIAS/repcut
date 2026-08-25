"""Criterion 13: a 2GB transfer must not become 2GB of memory.

Amendment 004 §3. Marked ``slow``, so it is excluded from CI and from
``make test``; ``make verify-02`` runs it and reports SKIPPED **with the reason**
rather than passing silently, because a memory budget nobody measured is a
memory budget nobody has.

The number this asserts is the *engine's* RSS, and the engine here is this
process - the suite drives the ASGI app in-process. So the test itself has to
hold to the same discipline it measures: the 2GB never exists in Python, only
one 8MB slice at a time. ``conftest``'s ``upload_clip`` reads the whole file
with ``read_bytes``, which is right for a 100KB fixture and would fail this test
on the test's own allocation rather than the engine's.

Two places could plausibly load the whole clip, and both are covered here: the
chunk endpoint's write loop, and finalize's hash of the assembled file.
"""

import asyncio
import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import psutil
import pytest
from conftest import Harness

TARGET_BYTES = 2 * 1024**3
RSS_BUDGET_BYTES = 500 * 1024**2
FREE_DISK_REQUIRED_BYTES = 5 * 1024**3

# The browser's chunk size (amendment 004), so this measures the transfer shape
# the UI actually produces rather than a friendlier one.
CHUNK_BYTES = 8 * 1024 * 1024

# 1280x720 yuv420p, one frame. Raw video is the cheap way to 2GB: no encoder,
# so the fixture costs a disk write rather than half an hour of x264.
_FRAME_BYTES = 1280 * 720 * 3 // 2
_FRAMES = -(-TARGET_BYTES // _FRAME_BYTES)  # ceil


def _skip_reason(scratch: Path) -> str | None:
    """Why this cannot run here, or None. Reported by the gate, never swallowed."""
    if os.environ.get("REPCUT_SLOW") == "0":
        return "REPCUT_SLOW=0"
    if shutil.which("ffmpeg") is None:
        return "ffmpeg is not on PATH"
    free = shutil.disk_usage(scratch).free
    if free < FREE_DISK_REQUIRED_BYTES:
        return f"needs 5GB free, has {free / 1024**3:.1f}GB"
    return None


def _generate_large_clip(destination: Path) -> None:
    """A real, probe-able 2GB video, built at test time.

    Raw frames rather than an encode, and built directly rather than through
    ``ffmpeg_builder``: this is a fixture, not a pipeline invocation, and
    ``conftest.make_clip`` sets the same precedent. The rule
    (`.claude/rules/ffmpeg.md`) binds what the *engine* runs.
    """
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=1280x720:rate=30",
            "-frames:v",
            str(_FRAMES),
            "-c:v",
            "rawvideo",
            "-pix_fmt",
            "yuv420p",
            destination.as_posix(),
        ],
        check=True,
        capture_output=True,
    )


class _PeakRss:
    """Samples this process's RSS until the block ends. Peak, not average."""

    def __init__(self) -> None:
        self._process = psutil.Process()
        self.baseline = self._process.memory_info().rss
        self.peak = self.baseline
        self._task: asyncio.Task[None] | None = None

    async def _sample(self) -> None:
        while True:
            self.peak = max(self.peak, self._process.memory_info().rss)
            await asyncio.sleep(0.05)

    async def __aenter__(self) -> "_PeakRss":
        self._task = asyncio.create_task(self._sample())
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._task is not None:
            self._task.cancel()
        # One last reading: the peak may have landed between two samples.
        self.peak = max(self.peak, self._process.memory_info().rss)


@pytest.fixture
def large_clip(tmp_path: Path) -> Iterator[Path]:
    """2GB of real video, deleted afterwards whatever the test did."""
    reason = _skip_reason(tmp_path)
    if reason is not None:
        pytest.skip(reason)

    destination = tmp_path / "large.avi"
    _generate_large_clip(destination)
    try:
        yield destination
    finally:
        destination.unlink(missing_ok=True)


@pytest.mark.slow
async def test_a_two_gigabyte_upload_stays_under_the_memory_budget(
    api: Harness, large_clip: Path
) -> None:
    """The whole transfer, measured: 2GB in, peak RSS under 500MB."""
    size = (await asyncio.to_thread(large_clip.stat)).st_size
    assert size >= TARGET_BYTES, "the fixture is smaller than the criterion measures"

    project = await api.client.post("/projects", json={"name": "big session"})
    assert project.status_code == 201, project.text
    project_id = project.json()["id"]

    async with _PeakRss() as rss:
        opened = await api.client.post(
            f"/projects/{project_id}/uploads",
            json={
                "display_name": "large.avi",
                "size_bytes": size,
                "chunk_size_bytes": CHUNK_BYTES,
            },
        )
        assert opened.status_code == 201, opened.text
        upload_id = opened.json()["id"]
        offset = opened.json()["bytes_received"]

        handle = await asyncio.to_thread(large_clip.open, "rb")
        try:
            while offset < size:
                await asyncio.to_thread(handle.seek, offset)
                piece = await asyncio.to_thread(handle.read, CHUNK_BYTES)
                sent = await api.client.put(
                    f"/uploads/{upload_id}/chunk", params={"offset": offset}, content=piece
                )
                assert sent.status_code == 200, sent.text
                offset = sent.json()["bytes_received"]
        finally:
            await asyncio.to_thread(handle.close)

        finalized = await api.client.post(f"/uploads/{upload_id}/finalize")
        assert finalized.status_code == 200, finalized.text

    # The ingest of a 2GB raw clip is not what this criterion measures, and
    # leaving it running would hold the fixture's teardown behind a full proxy
    # encode. Cancelling it also exercises the path that kills the child.
    job_id = finalized.json()["job_id"]
    if job_id is not None:
        await api.client.post(f"/jobs/{job_id}/cancel")

    peak_mb = rss.peak / 1024**2
    print(
        f"MEASURED: peak RSS {peak_mb:.0f}MB "
        f"(baseline {rss.baseline / 1024**2:.0f}MB) "
        f"over a {size / 1024**3:.2f}GB upload in {CHUNK_BYTES // 1024**2}MB chunks"
    )

    assert rss.peak < RSS_BUDGET_BYTES, (
        f"peak RSS {peak_mb:.0f}MB exceeded the {RSS_BUDGET_BYTES / 1024**2:.0f}MB budget"
    )
    assert finalized.json()["duplicate"] is False
    assert finalized.json()["media_file_id"]
