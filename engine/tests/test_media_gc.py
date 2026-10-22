"""Orphan collection: everything unreferenced goes, everything referenced stays.

The gate owed since amendment 004: zero unreferenced blobs, derived folders,
abandoned transfers and killed-render partials after a collection, and the
clip that is still in the library still plays from its own bytes.
"""

from __future__ import annotations

import os
import time
from collections.abc import Awaitable, Callable
from datetime import timedelta
from pathlib import Path

import httpx
from conftest import Harness
from sqlalchemy import select, update

from repcut.db.models import UploadSession, UploadStatus, utcnow
from repcut.media.gc import collect

ORPHAN = "f" * 64
ORPHAN_DERIVED = "e" * 64


def _touch(path: Path, data: bytes = b"x" * 100, age_s: float = 0.0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    if age_s:
        then = time.time() - age_s
        os.utime(path, (then, then))
    return path


async def test_collection_removes_orphans_and_keeps_what_is_referenced(
    api: Harness,
    make_clip: Callable[..., Path],
    upload_clip: Callable[..., Awaitable[httpx.Response]],
) -> None:
    data = api.settings.data_dir
    project = (await api.client.post("/projects", json={"name": "gc"})).json()["id"]
    uploaded = await upload_clip(project, make_clip(seconds=1.0))
    await api.queue.drain()
    sha = uploaded.json()["sha256"]
    kept_blob = next((data / "media" / "blobs" / sha[:2] / sha).iterdir())
    kept_derived = data / "media" / "derived" / sha[:2] / sha
    assert kept_blob.is_file() and kept_derived.is_dir()

    orphan_blob = _touch(data / "media" / "blobs" / ORPHAN[:2] / ORPHAN / "source.mp4")
    orphan_derived = _touch(
        data / "media" / "derived" / ORPHAN_DERIVED[:2] / ORPHAN_DERIVED / "proxy" / "1" / "p.mp4"
    )
    stray_part = _touch(data / "uploads" / "00000000-0000-4000-8000-000000000000.part")
    old_partial = _touch(kept_derived / ".proxy.0123abcd.partial.mp4", age_s=7200)
    fresh_partial = _touch(kept_derived / ".proxy.4567cdef.partial.mp4")

    # A live transfer, and one nobody touched for a fortnight.
    body = {"display_name": "a.mp4", "size_bytes": 10_000}
    live = (await api.client.post(f"/projects/{project}/uploads", json=body)).json()
    other = await api.client.post("/projects", json={"name": "gc2"})
    stale = (await api.client.post(f"/projects/{other.json()['id']}/uploads", json=body)).json()
    async with api.session_factory() as session:
        await session.execute(
            update(UploadSession)
            .where(UploadSession.id == stale["id"])
            .values(updated_at=utcnow() - timedelta(days=14))
        )
        await session.commit()
        parts = dict(
            (await session.execute(select(UploadSession.id, UploadSession.part_path))).all()
        )
    live_part, stale_part = (_touch(data / parts[s["id"]]) for s in (live, stale))

    async with api.session_factory() as session:
        report = await collect(session, data)
    assert report.blob_dirs == [f"media/blobs/{ORPHAN[:2]}/{ORPHAN}"]
    assert report.derived_dirs == [f"media/derived/{ORPHAN_DERIVED[:2]}/{ORPHAN_DERIVED}"]
    assert sorted(report.parts) == sorted([f"uploads/{stray_part.name}", parts[stale["id"]]])
    assert report.partials == [old_partial.relative_to(data).as_posix()]
    assert report.bytes > 0 and orphan_blob.exists()  # a dry run removes nothing

    async with api.session_factory() as session:
        applied = await collect(session, data, apply=True)
        status = (
            await session.execute(
                select(UploadSession.status).where(UploadSession.id == stale["id"])
            )
        ).scalar_one()
    assert applied.count == report.count
    for gone in (
        orphan_blob.parent,
        orphan_derived.parents[2],
        stray_part,
        stale_part,
        old_partial,
    ):
        assert not gone.exists(), gone
    for kept in (kept_blob, kept_derived, fresh_partial, live_part):
        assert kept.exists(), kept
    assert status == UploadStatus.ABORTED

    async with api.session_factory() as session:
        assert (await collect(session, data)).count == 0  # nothing unreferenced remains
    served = await api.client.get(f"/media/{uploaded.json()['media_file_id']}/proxy")
    assert served.status_code in (200, 206)


async def test_collection_never_reaches_outside_the_data_dir(api: Harness, tmp_path: Path) -> None:
    outside = _touch(tmp_path / "outside" / ".x.0123abcd.partial.mp4", age_s=7200)
    async with api.session_factory() as session:
        report = await collect(session, api.settings.data_dir, apply=True)
    assert outside.exists()
    assert all(not p.startswith("/") and ".." not in p for p in report.partials + report.parts)
