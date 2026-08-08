"""Chunked upload: rejection, resume and deduplication.

Gate criteria 3, 4 and 5 live here. Each one is asserted on what is *measurable
after the fact* - rows written, bytes on disk, jobs enqueued - rather than on the
endpoint returning the status code it was asked to return.
"""

import hashlib
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx
import pytest
from conftest import Harness
from sqlalchemy import func, select

from repcut.db.models import DerivedArtifact, MediaBlob, MediaFile, UploadSession, UploadStatus
from repcut.media.store import blob_directory


async def _project(api: Harness, name: str = "leg day") -> str:
    response = await api.client.post("/projects", json={"name": name})
    assert response.status_code == 201
    project_id: str = response.json()["id"]
    return project_id


async def _count(api: Harness, model: type[object]) -> int:
    async with api.session_factory() as session:
        total = await session.scalar(select(func.count()).select_from(model))
    return int(total or 0)


def _store_bytes(data_dir: Path) -> int:
    """Every byte under the content-addressed media store."""
    media = data_dir / "media"
    if not media.exists():
        return 0
    return sum(path.stat().st_size for path in media.rglob("*") if path.is_file())


# --- criterion 3: non-video rejected ----------------------------------------


async def test_a_text_file_is_rejected_by_its_extension(
    api: Harness, tmp_path: Path, upload_clip: Callable[..., Awaitable[httpx.Response]]
) -> None:
    """The cheap gate: a `.txt` never gets to occupy disk for a transfer."""
    note = tmp_path / "notes.txt"
    note.write_text("not a video", encoding="utf-8")
    project_id = await _project(api)

    response = await upload_clip(project_id, note)

    assert response.status_code == 415
    assert response.json()["error"]["code"] == "unsupported_media_type"
    assert await _count(api, MediaBlob) == 0
    assert await _count(api, MediaFile) == 0


async def test_a_non_video_named_mp4_is_rejected_by_its_bytes(
    api: Harness, tmp_path: Path, upload_clip: Callable[..., Awaitable[httpx.Response]]
) -> None:
    """The gate that actually decides. An extension is client-controlled.

    This is the case the extension check cannot reach, and the reason ffprobe
    runs before anything is written to ``media_blobs``.
    """
    impostor = tmp_path / "clip.mp4"
    impostor.write_bytes(b"PK\x03\x04 this is not video, it just says it is" * 64)
    project_id = await _project(api)

    response = await upload_clip(project_id, impostor)

    assert response.status_code == 415
    assert response.json()["error"]["code"] == "not_a_video"
    assert "traceback" not in response.text.casefold()
    assert await _count(api, MediaBlob) == 0
    assert await _count(api, MediaFile) == 0


async def test_a_rejected_transfer_is_closed_not_left_open(
    api: Harness, tmp_path: Path, upload_clip: Callable[..., Awaitable[httpx.Response]]
) -> None:
    """An aborted session releases its slot in the partial unique index.

    Left ``in_progress``, it would block every later attempt at the same clip
    with an integrity error the user cannot act on.
    """
    impostor = tmp_path / "clip.mp4"
    impostor.write_bytes(b"still not video" * 512)
    project_id = await _project(api)

    await upload_clip(project_id, impostor)

    async with api.session_factory() as session:
        sessions = (await session.execute(select(UploadSession))).scalars().all()
    assert [row.status for row in sessions] == [UploadStatus.ABORTED]


# --- criterion 4: resumability ----------------------------------------------


async def test_a_transfer_resumes_from_the_servers_offset(
    api: Harness, make_clip: Callable[..., Path], sha256_of: Callable[[Path], str]
) -> None:
    """Half the bytes, then resume - the offset comes from the server.

    The engine is not restarted here; ``test_resume_survives_a_restart`` does
    that. This asserts the property the restart depends on: the offset is
    reconciled from the row and the file, never taken from the client.
    """
    source = make_clip(seconds=1.0)
    payload = source.read_bytes()
    project_id = await _project(api)

    opened = await api.client.post(
        f"/projects/{project_id}/uploads",
        json={
            "display_name": source.name,
            "size_bytes": len(payload),
            "chunk_size_bytes": 32 * 1024,
            "sha256": sha256_of(source),
        },
    )
    upload_id = opened.json()["id"]

    half = len(payload) // 2
    await api.client.put(
        f"/uploads/{upload_id}/chunk", params={"offset": 0}, content=payload[:half]
    )

    # A client that lies about where it is must be refused, not believed.
    misplaced = await api.client.put(
        f"/uploads/{upload_id}/chunk", params={"offset": 0}, content=payload[:half]
    )
    assert misplaced.status_code == 409
    assert misplaced.json()["error"]["code"] == "chunk_offset_mismatch"

    resumed = await api.client.get(f"/uploads/{upload_id}")
    assert resumed.json()["bytes_received"] == half

    await api.client.put(
        f"/uploads/{upload_id}/chunk", params={"offset": half}, content=payload[half:]
    )
    finalized = await api.client.post(f"/uploads/{upload_id}/finalize")

    assert finalized.status_code == 200
    assert finalized.json()["sha256"] == sha256_of(source)
    assert await _count(api, MediaFile) == 1


async def test_the_offset_is_the_lesser_of_the_row_and_the_file(
    api: Harness, make_clip: Callable[..., Path], sha256_of: Callable[[Path], str]
) -> None:
    """A committed row whose write was lost must not skip bytes.

    This is the half of amendment 004 §7 a restart test cannot reach: after a
    kill the row can be ahead of the file or behind it, and only the minimum is
    safe in both directions. Simulated by truncating the ``.part`` behind the
    engine's back, which is exactly what a lost write looks like on restart.
    """
    source = make_clip(seconds=1.0)
    payload = source.read_bytes()
    project_id = await _project(api)

    opened = await api.client.post(
        f"/projects/{project_id}/uploads",
        json={
            "display_name": source.name,
            "size_bytes": len(payload),
            "chunk_size_bytes": 32 * 1024,
            "sha256": sha256_of(source),
        },
    )
    upload_id = opened.json()["id"]
    await api.client.put(
        f"/uploads/{upload_id}/chunk", params={"offset": 0}, content=payload[:4096]
    )

    part = api.data_dir / "uploads" / f"{upload_id}.part"
    with part.open("r+b") as handle:
        handle.truncate(1000)

    assert (await api.client.get(f"/uploads/{upload_id}")).json()["bytes_received"] == 1000


async def test_a_client_that_lost_its_id_finds_its_own_transfer(
    api: Harness, make_clip: Callable[..., Path], sha256_of: Callable[[Path], str]
) -> None:
    """A refreshed browser tab has the project and the hash, and nothing else.

    Without this path the tab retries as a new session and abandons the first
    ``.part`` with nothing referencing it - the case the partial unique index
    was added for.
    """
    source = make_clip(seconds=1.0)
    payload = source.read_bytes()
    digest = sha256_of(source)
    project_id = await _project(api)

    declaration = {
        "display_name": source.name,
        "size_bytes": len(payload),
        "chunk_size_bytes": 32 * 1024,
        "sha256": digest,
    }
    opened = await api.client.post(f"/projects/{project_id}/uploads", json=declaration)
    upload_id = opened.json()["id"]
    await api.client.put(
        f"/uploads/{upload_id}/chunk", params={"offset": 0}, content=payload[:2048]
    )

    found = await api.client.get(
        f"/projects/{project_id}/uploads/in-progress", params={"sha256": digest}
    )
    assert found.status_code == 200
    assert found.json()["id"] == upload_id
    assert found.json()["bytes_received"] == 2048

    # Re-declaring is the other half: a tab that does not know the lookup
    # endpoint exists still gets its own session back rather than a second one.
    reopened = await api.client.post(f"/projects/{project_id}/uploads", json=declaration)
    assert reopened.status_code == 200
    assert reopened.json()["id"] == upload_id
    assert reopened.json()["resumed"] is True
    assert await _count(api, UploadSession) == 1


async def test_a_corrupted_transfer_is_named_not_stored(
    api: Harness, make_clip: Callable[..., Path]
) -> None:
    """The declared hash is verified against the engine's own digest."""
    source = make_clip(seconds=1.0)
    payload = source.read_bytes()
    project_id = await _project(api)

    opened = await api.client.post(
        f"/projects/{project_id}/uploads",
        json={
            "display_name": source.name,
            "size_bytes": len(payload),
            "chunk_size_bytes": 1 << 20,
            "sha256": hashlib.sha256(b"a different file entirely").hexdigest(),
        },
    )
    upload_id = opened.json()["id"]
    await api.client.put(f"/uploads/{upload_id}/chunk", params={"offset": 0}, content=payload)

    finalized = await api.client.post(f"/uploads/{upload_id}/finalize")

    assert finalized.status_code == 422
    assert finalized.json()["error"]["code"] == "hash_mismatch"
    assert await _count(api, MediaBlob) == 0


async def test_finalizing_early_is_refused(api: Harness, make_clip: Callable[..., Path]) -> None:
    """A truncated transfer must not become a blob that hashes to itself."""
    source = make_clip(seconds=1.0)
    payload = source.read_bytes()
    project_id = await _project(api)

    opened = await api.client.post(
        f"/projects/{project_id}/uploads",
        json={"display_name": source.name, "size_bytes": len(payload), "chunk_size_bytes": 4096},
    )
    upload_id = opened.json()["id"]
    await api.client.put(
        f"/uploads/{upload_id}/chunk", params={"offset": 0}, content=payload[:4096]
    )

    finalized = await api.client.post(f"/uploads/{upload_id}/finalize")

    assert finalized.status_code == 409
    assert finalized.json()["error"]["code"] == "upload_incomplete"


async def test_uploading_the_same_clip_twice_writes_one_reference(
    api: Harness,
    make_clip: Callable[..., Path],
    upload_clip: Callable[..., Awaitable[httpx.Response]],
) -> None:
    """Idempotence: any number of retries, one blob and one reference."""
    source = make_clip(seconds=1.0)
    project_id = await _project(api)

    first = await upload_clip(project_id, source)
    await api.queue.drain()
    second = await upload_clip(project_id, source)
    await api.queue.drain()

    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["sha256"] == second.json()["sha256"]
    assert first.json()["media_file_id"] == second.json()["media_file_id"]
    assert await _count(api, MediaBlob) == 1
    assert await _count(api, MediaFile) == 1


# --- criterion 5: duplicate hash across two projects ------------------------


async def test_a_duplicate_across_projects_links_and_re_encodes_nothing(
    api: Harness,
    make_clip: Callable[..., Path],
    upload_clip: Callable[..., Awaitable[httpx.Response]],
) -> None:
    """One blob, two references, one set of derived artifacts.

    The measurement is bytes on disk and jobs enqueued, not the endpoint's own
    report of what it did. If the second upload re-encoded a proxy the byte
    count would move even though every row looked right.
    """
    source = make_clip(seconds=2.0)
    leg_day = await _project(api, "leg day")
    push_day = await _project(api, "push day")

    first = await upload_clip(leg_day, source)
    await api.queue.drain()
    bytes_after_first = _store_bytes(api.data_dir)
    artifacts_after_first = await _count(api, DerivedArtifact)

    second = await upload_clip(push_day, source)
    await api.queue.drain()

    assert first.json()["duplicate"] is False
    assert second.json()["duplicate"] is True
    assert second.json()["job_id"] is None, "the second upload must enqueue no ingest at all"
    assert second.json()["sha256"] == first.json()["sha256"]

    assert _store_bytes(api.data_dir) == bytes_after_first
    assert await _count(api, DerivedArtifact) == artifacts_after_first
    assert await _count(api, MediaBlob) == 1
    assert await _count(api, MediaFile) == 2

    # One directory of source bytes, not two.
    blobs = api.data_dir / "media" / "blobs"
    assert len([path for path in blobs.rglob("source.*") if path.is_file()]) == 1


async def test_the_stored_path_is_content_addressed_and_carries_no_user_name(
    api: Harness,
    make_clip: Callable[..., Path],
    upload_clip: Callable[..., Awaitable[httpx.Response]],
) -> None:
    """The user's filename is a display name and never a path component.

    ``secrets.md``: no stored path derives from user input. The clip below is
    named as a person would name it; the store must not repeat that anywhere.
    """
    source = make_clip("ashwin bench pr.mp4", seconds=1.0)
    project_id = await _project(api)

    finalized = await upload_clip(project_id, source)
    digest = finalized.json()["sha256"]

    async with api.session_factory() as session:
        blob = await session.get(MediaBlob, digest)
        reference = (await session.execute(select(MediaFile))).scalars().one()

    assert blob is not None
    assert blob.stored_path == f"{blob_directory(digest)}/source.mp4"
    assert "ashwin" not in blob.stored_path
    assert ":" not in blob.stored_path and "\\" not in blob.stored_path
    # The name survives exactly once, where the library reads it.
    assert reference.display_name == "ashwin bench pr.mp4"


async def test_an_unknown_project_is_a_named_error(api: Harness) -> None:
    response = await api.client.post(
        "/projects/00000000-0000-4000-8000-000000000000/uploads",
        json={"display_name": "clip.mp4", "size_bytes": 10},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "project_not_found"


@pytest.mark.parametrize("name", ["clip.txt", "clip", "clip.pdf", "clip.mp4.exe"])
async def test_only_video_extensions_open_a_transfer(api: Harness, name: str) -> None:
    project_id = await _project(api)

    response = await api.client.post(
        f"/projects/{project_id}/uploads", json={"display_name": name, "size_bytes": 10}
    )

    assert response.status_code == 415
