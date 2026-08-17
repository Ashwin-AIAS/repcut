"""What reaches the client when something the engine did not plan for goes wrong.

Written after a real-footage run: on Windows, `make dev` put the engine on an
event loop with no subprocess transport, so ``ffprobe`` raised
``NotImplementedError`` from inside ``finalize``. ``run()`` caught only
``FileNotFoundError`` and ``PermissionError``, so it escaped as an unhandled
exception - Starlette answered 21 bytes of ``text/plain`` the UI cannot parse,
and uvicorn logged nine frames of absolute paths, every one of them carrying the
OS username (`.claude/rules/secrets.md`).

Three properties are pinned here, and they fail independently:

1. An unexpected exception becomes a **named JSON error**, not a plain-text 500.
2. No ``/finalize`` body ever contains ``Users`` or a traceback frame.
3. A failure of the *engine* leaves the transfer **resumable**. This is the
   expensive one: ``_probe_or_reject`` used to answer "not a video" to any
   ``FFmpegError`` and abort the session, so an engine that could not start
   ffprobe would have made a finished 2GB upload unreachable.
"""

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx
import pytest
from conftest import Harness
from sqlalchemy import select

from repcut.db.models import MediaBlob, MediaFile, UploadSession, UploadStatus

# What a leaked absolute path looks like on this machine, and what a traceback
# frame looks like anywhere. Asserted as substrings of the raw body, because the
# failure being prevented produced a body that was not JSON at all.
_FORBIDDEN_IN_A_BODY = ("Users", "Traceback", 'File "', "asyncio/base_events", "site-packages")


async def _project(api: Harness, name: str = "boundary") -> str:
    response = await api.client.post("/projects", json={"name": name})
    assert response.status_code == 201
    project_id: str = response.json()["id"]
    return project_id


async def _read(path: Path) -> bytes:
    """Read a fixture off disk without blocking the loop the engine shares."""
    return await asyncio.to_thread(path.read_bytes)


async def _open_and_send(api: Harness, project_id: str, clip: Path) -> str:
    """Declare a transfer and send every byte, stopping short of finalize."""
    payload = await _read(clip)
    opened = await api.client.post(
        f"/projects/{project_id}/uploads",
        json={
            "display_name": clip.name,
            "size_bytes": len(payload),
            "chunk_size_bytes": 64 * 1024,
            "sha256": hashlib.sha256(payload).hexdigest(),
        },
    )
    assert opened.status_code == 201
    upload_id: str = opened.json()["id"]

    offset = 0
    while offset < len(payload):
        sent = await api.client.put(
            f"/uploads/{upload_id}/chunk",
            params={"offset": offset},
            content=payload[offset : offset + 64 * 1024],
        )
        assert sent.status_code == 200
        offset = sent.json()["bytes_received"]
    return upload_id


async def _session(api: Harness, upload_id: str) -> UploadSession:
    async with api.session_factory() as session:
        row = await session.get(UploadSession, upload_id)
        assert row is not None
        return row


def _no_subprocesses(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every subprocess spawn fail exactly as a selector loop makes it fail.

    Patched at ``asyncio.create_subprocess_exec`` rather than at ``probe_media``
    so the whole chain is exercised: the builder classifies it, ``finalize``
    decides what it means for the session, and the API renders it. Patching
    higher up would test the handler and skip the two decisions that were wrong.
    """

    async def _raise(*_: object, **__: object) -> None:
        raise NotImplementedError

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _raise)


# --- 1 and 2: what the client is told ----------------------------------------


async def test_an_unexpected_exception_becomes_a_named_json_error(
    api: Harness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The boundary's own job: nothing leaves the engine unnamed.

    A ``RuntimeError`` is used rather than the real bug, because the point is
    the case nobody anticipated. Its message deliberately carries a path, to
    prove the boundary does not pass a message through.
    """
    from repcut.api import uploads

    project_id = await _project(api)
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"\x00" * 4096)
    upload_id = await _open_and_send(api, project_id, clip)

    async def _explode(*_: object, **__: object) -> None:
        raise RuntimeError(r"C:\Users\someone\OneDrive\repcut-data\media\blobs\aa\source.mp4")

    monkeypatch.setattr(uploads, "probe_media", _explode)

    response = await api.client.post(f"/uploads/{upload_id}/finalize")

    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["error"]["code"] == "internal_error"
    for needle in _FORBIDDEN_IN_A_BODY:
        assert needle not in response.text, f"the response body leaked {needle!r}"


async def test_no_finalize_response_ever_carries_a_path_or_a_traceback(
    api: Harness,
    tmp_path: Path,
    make_clip: Callable[..., Path],
    upload_clip: Callable[..., Awaitable[httpx.Response]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every way finalize can answer, checked against the raw bytes.

    Success, rejection, an unfinished transfer, an unknown session and the loop
    failure that started this - one assertion over all of them, so a sixth
    outcome added later is covered by the same criterion the moment it is
    reached.
    """
    from repcut.api import uploads

    project_id = await _project(api)
    bodies: dict[str, httpx.Response] = {}

    # An unfinished transfer, and an id that was never issued.
    clip = make_clip("real.mp4", seconds=1.0)
    unfinished = await api.client.post(
        f"/projects/{project_id}/uploads",
        json={"display_name": "real.mp4", "size_bytes": 999_999, "chunk_size_bytes": 65536},
    )
    bodies["incomplete"] = await api.client.post(f"/uploads/{unfinished.json()['id']}/finalize")
    bodies["unknown"] = await api.client.post(
        "/uploads/00000000-0000-4000-8000-000000000000/finalize"
    )

    # A file that is not video, and one that is.
    impostor = tmp_path / "impostor.mp4"
    impostor.write_bytes(b"PK\x03\x04 not video" * 256)
    bodies["not_a_video"] = await upload_clip(project_id, impostor)
    bodies["success"] = await upload_clip(project_id, clip)
    assert bodies["success"].status_code == 200

    # And the loop failure itself, end to end.
    second = await _project(api, "loop failure")
    upload_id = await _open_and_send(api, second, clip)
    _no_subprocesses(monkeypatch)
    bodies["tooling_unavailable"] = await api.client.post(f"/uploads/{upload_id}/finalize")

    # An unexpected exception, for the boundary's own branch.
    async def _explode(*_: object, **__: object) -> None:
        raise RuntimeError(str(tmp_path / "leaky" / "path.mp4"))

    monkeypatch.setattr(uploads, "probe_media", _explode)
    third = await _project(api, "unexpected")
    unexpected_id = await _open_and_send(api, third, clip)
    bodies["unexpected"] = await api.client.post(f"/uploads/{unexpected_id}/finalize")

    for name, response in bodies.items():
        for needle in _FORBIDDEN_IN_A_BODY:
            assert needle not in response.text, f"the {name} body leaked {needle!r}"
        assert response.headers["content-type"].startswith("application/json"), (
            f"the {name} body is not JSON, so the UI cannot render its cause"
        )
        response.json()


# --- 3: the expensive property -----------------------------------------------


async def test_a_tooling_failure_is_a_503_and_not_a_verdict_on_the_footage(
    api: Harness, make_clip: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """An engine that cannot run ffprobe must not call the clip unreadable."""
    project_id = await _project(api)
    upload_id = await _open_and_send(api, project_id, make_clip("real.mp4", seconds=1.0))

    _no_subprocesses(monkeypatch)
    response = await api.client.post(f"/uploads/{upload_id}/finalize")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "media_tooling_unavailable"
    assert "not a video" not in response.text.casefold()


async def test_a_tooling_failure_leaves_the_transfer_resumable(
    api: Harness, make_clip: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The 2GB question: after the engine fails, are the bytes still usable?

    Asserted on the three things a resume depends on, not on the status code:
    the session is still ``IN_PROGRESS`` (the lookup a refreshed tab uses filters
    on it), the byte count is untouched, and the ``.part`` file is still on disk.
    Then the engine is repaired and the *same* session is finalized - no chunk
    re-sent - and it succeeds.
    """
    project_id = await _project(api)
    clip = make_clip("real.mp4", seconds=1.0)
    payload = await _read(clip)
    size = len(payload)
    upload_id = await _open_and_send(api, project_id, clip)

    with monkeypatch.context() as broken:
        _no_subprocesses(broken)
        assert (await api.client.post(f"/uploads/{upload_id}/finalize")).status_code == 503

        row = await _session(api, upload_id)
        assert row.status is UploadStatus.IN_PROGRESS, "the transfer was closed by an engine fault"
        assert row.bytes_received == size

    # The monkeypatch is undone here - the engine is "restarted onto a good loop".
    status = await api.client.get(f"/uploads/{upload_id}")
    assert status.json()["bytes_received"] == size, "the resume offset moved"

    finalized = await api.client.post(f"/uploads/{upload_id}/finalize")

    assert finalized.status_code == 200, finalized.text
    assert finalized.json()["sha256"] == hashlib.sha256(payload).hexdigest()

    async with api.session_factory() as session:
        references = (await session.execute(select(MediaFile))).scalars().all()
        blobs = (await session.execute(select(MediaBlob))).scalars().all()
    assert len(references) == 1, "a retried finalize wrote a second reference"
    assert len(blobs) == 1


async def test_a_genuinely_unreadable_file_still_closes_its_session(
    api: Harness, tmp_path: Path, upload_clip: Callable[..., Awaitable[httpx.Response]]
) -> None:
    """The other half of the same decision, so the fix did not swing too far.

    Keeping a session open for a file ffprobe *read* and rejected would leave a
    permanently unfinishable transfer holding its slot in
    ``uq_upload_sessions_in_progress``, and the user with no way to re-declare
    the same clip. Only engine faults get the benefit of the doubt.
    """
    project_id = await _project(api)
    impostor = tmp_path / "impostor.mp4"
    impostor.write_bytes(b"PK\x03\x04 definitely not video" * 256)

    response = await upload_clip(project_id, impostor)

    assert response.status_code == 415
    assert response.json()["error"]["code"] == "not_a_video"
    async with api.session_factory() as session:
        rows = (await session.execute(select(UploadSession))).scalars().all()
    assert [row.status for row in rows] == [UploadStatus.ABORTED]
