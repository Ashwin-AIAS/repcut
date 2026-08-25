"""Serving derived artifacts, and the Range handling the player depends on.

Two layers, deliberately separated.

``parse_byte_range`` is tested as a pure function against a declared size, with
no file involved. A Range header is request input, and the cases that matter -
a suffix range, a range past the end, a malformed one - are exactly the cases
where opening the file first would let a wrong answer look like a working one.

The route tests then measure real bytes: the proxy the ingest job actually
rendered, sliced by real HTTP requests, compared against the file on disk.
"""

from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx
import pytest
from conftest import Harness
from sqlalchemy import select

from repcut.api.media import ByteRange, RangeNotSatisfiableError, parse_byte_range
from repcut.db.models import DerivedArtifact
from repcut.media.artifacts import PARAMS_VERSION, ArtifactKind
from repcut.media.store import absolute

# --- the parser, with no file anywhere near it ------------------------------


def test_no_header_means_the_whole_file() -> None:
    assert parse_byte_range(None, 1000) is None


@pytest.mark.parametrize(
    "header",
    [
        "bytes=abc-def",
        "bytes=",
        "bytes=-",
        "megabytes=0-1",
        "bytes 0-1",
        # Multi-range. RFC 9110 §14.2 permits ignoring it, and no video element
        # asks for one - answering properly would mean multipart/byteranges.
        "bytes=0-99,200-299",
        # Past int64's reach: the digit bound rejects it before `int()` sees it.
        "bytes=" + "9" * 25 + "-",
    ],
)
def test_a_malformed_range_is_ignored_not_rejected(header: str) -> None:
    """RFC 9110 §14.1.2 requires this.

    Rejecting instead would turn a slightly odd client into one that cannot play
    the file at all, rather than one that merely cannot seek.
    """
    assert parse_byte_range(header, 1000) is None


def test_a_closed_range() -> None:
    assert parse_byte_range("bytes=0-99", 1000) == ByteRange(start=0, end=99)


def test_an_open_ended_range_runs_to_the_last_byte() -> None:
    assert parse_byte_range("bytes=900-", 1000) == ByteRange(start=900, end=999)


def test_a_suffix_range_is_the_tail_not_the_head() -> None:
    """`bytes=-100` is the *last* 100 bytes.

    Reading it as an offset serves the wrong part of the file behind a 206 that
    claims to be correct - a silent corruption rather than a visible failure.
    """
    assert parse_byte_range("bytes=-100", 1000) == ByteRange(start=900, end=999)


def test_a_suffix_longer_than_the_file_is_the_whole_file() -> None:
    assert parse_byte_range("bytes=-5000", 1000) == ByteRange(start=0, end=999)


def test_an_end_past_the_file_is_clamped() -> None:
    """A client may legitimately overshoot at the tail; that is not an error."""
    assert parse_byte_range("bytes=900-5000", 1000) == ByteRange(start=900, end=999)


def test_a_range_starting_past_the_end_is_unsatisfiable() -> None:
    with pytest.raises(RangeNotSatisfiableError):
        parse_byte_range("bytes=1000-", 1000)


def test_a_zero_length_suffix_is_unsatisfiable() -> None:
    with pytest.raises(RangeNotSatisfiableError):
        parse_byte_range("bytes=-0", 1000)


def test_a_backwards_range_is_ignored() -> None:
    assert parse_byte_range("bytes=500-100", 1000) is None


def test_an_empty_file_is_served_whole() -> None:
    """No range is satisfiable against zero bytes, and 416 would be unhelpful."""
    assert parse_byte_range("bytes=0-10", 0) is None


def test_the_length_is_inclusive() -> None:
    """HTTP counts both ends. An off-by-one here is an off-by-one in every 206."""
    assert ByteRange(start=0, end=99).length == 100
    assert ByteRange(start=5, end=5).length == 1


# --- the routes, against bytes the ingest job really produced ----------------


async def _ingested_clip(
    api: Harness,
    upload_clip: Callable[..., Awaitable[httpx.Response]],
    source: Path,
) -> tuple[str, str]:
    """Upload, ingest, and return ``(media_file_id, sha256)``."""
    project = await api.client.post("/projects", json={"name": "session"})
    finalized = await upload_clip(project.json()["id"], source)
    assert finalized.status_code == 200, finalized.text
    await api.queue.drain()
    body = finalized.json()
    media_file_id: str = body["media_file_id"]
    digest: str = body["sha256"]
    return media_file_id, digest


async def _artifact_path(api: Harness, digest: str, kind: ArtifactKind) -> Path:
    async with api.session_factory() as session:
        row = (
            (
                await session.execute(
                    select(DerivedArtifact).where(
                        DerivedArtifact.sha256 == digest,
                        DerivedArtifact.artifact_kind == kind.value,
                        DerivedArtifact.params_version == PARAMS_VERSION[kind],
                    )
                )
            )
            .scalars()
            .first()
        )
    assert row is not None, f"ingest produced no {kind.value}"
    return absolute(api.settings.data_dir, row.stored_path)


async def test_the_proxy_is_served_whole(
    api: Harness,
    make_clip: Callable[..., Path],
    upload_clip: Callable[..., Awaitable[httpx.Response]],
) -> None:
    media_file_id, digest = await _ingested_clip(api, upload_clip, make_clip())
    on_disk = (await _artifact_path(api, digest, ArtifactKind.PROXY)).read_bytes()

    response = await api.client.get(f"/media/{media_file_id}/proxy")

    assert response.status_code == 200
    assert response.headers["content-type"] == "video/mp4"
    # Without this header a browser will not even attempt to seek.
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["content-length"] == str(len(on_disk))
    assert response.content == on_disk


async def test_a_range_request_returns_exactly_that_slice(
    api: Harness,
    make_clip: Callable[..., Path],
    upload_clip: Callable[..., Awaitable[httpx.Response]],
) -> None:
    """The measurement that matters: the bytes returned are the bytes asked for.

    A 206 with the right length and the wrong offset plays as a corrupt file,
    and nothing in the status line says so.
    """
    media_file_id, digest = await _ingested_clip(api, upload_clip, make_clip())
    on_disk = (await _artifact_path(api, digest, ArtifactKind.PROXY)).read_bytes()

    response = await api.client.get(
        f"/media/{media_file_id}/proxy", headers={"range": "bytes=1024-2047"}
    )

    assert response.status_code == 206
    assert response.headers["content-range"] == f"bytes 1024-2047/{len(on_disk)}"
    assert response.headers["content-length"] == "1024"
    assert response.content == on_disk[1024:2048]


async def test_a_suffix_range_returns_the_tail(
    api: Harness,
    make_clip: Callable[..., Path],
    upload_clip: Callable[..., Awaitable[httpx.Response]],
) -> None:
    media_file_id, digest = await _ingested_clip(api, upload_clip, make_clip())
    on_disk = (await _artifact_path(api, digest, ArtifactKind.PROXY)).read_bytes()

    response = await api.client.get(
        f"/media/{media_file_id}/proxy", headers={"range": "bytes=-512"}
    )

    assert response.status_code == 206
    assert response.content == on_disk[-512:]


async def test_a_range_past_the_end_is_416_with_the_real_size(
    api: Harness,
    make_clip: Callable[..., Path],
    upload_clip: Callable[..., Awaitable[httpx.Response]],
) -> None:
    media_file_id, digest = await _ingested_clip(api, upload_clip, make_clip())
    size = (await _artifact_path(api, digest, ArtifactKind.PROXY)).stat().st_size

    response = await api.client.get(
        f"/media/{media_file_id}/proxy", headers={"range": f"bytes={size + 10}-"}
    )

    assert response.status_code == 416
    # The client needs the real size to recover; `*` is where it learns it.
    assert response.headers["content-range"] == f"bytes */{size}"


async def test_a_malformed_range_still_plays(
    api: Harness,
    make_clip: Callable[..., Path],
    upload_clip: Callable[..., Awaitable[httpx.Response]],
) -> None:
    media_file_id, digest = await _ingested_clip(api, upload_clip, make_clip())
    size = (await _artifact_path(api, digest, ArtifactKind.PROXY)).stat().st_size

    response = await api.client.get(
        f"/media/{media_file_id}/proxy", headers={"range": "bytes=not-a-range"}
    )

    assert response.status_code == 200
    assert response.headers["content-length"] == str(size)


async def test_the_thumbnail_strip_is_served_as_jpeg(
    api: Harness,
    make_clip: Callable[..., Path],
    upload_clip: Callable[..., Awaitable[httpx.Response]],
) -> None:
    media_file_id, digest = await _ingested_clip(api, upload_clip, make_clip())
    on_disk = (await _artifact_path(api, digest, ArtifactKind.THUMBNAIL_STRIP)).read_bytes()

    response = await api.client.get(f"/media/{media_file_id}/thumbnail-strip")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content == on_disk
    assert response.content[:2] == b"\xff\xd8"  # a real JPEG, not an empty file


async def test_an_unknown_clip_is_a_named_error(api: Harness) -> None:
    response = await api.client.get("/media/00000000-0000-4000-8000-000000000000/proxy")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "media_file_not_found"


async def test_a_malformed_id_never_reaches_the_database(api: Harness) -> None:
    """Bounded at the boundary, so `../` is a 422 rather than a lookup."""
    response = await api.client.get("/media/..%2F..%2Fetc%2Fpasswd/proxy")
    assert response.status_code in (404, 422)


async def test_a_clip_whose_ingest_has_not_run_says_so(
    api: Harness,
    make_clip: Callable[..., Path],
    upload_clip: Callable[..., Awaitable[httpx.Response]],
) -> None:
    """The race the library grid will actually hit.

    Finalize returns as soon as the reference exists; the proxy is rendered by a
    job that has not started yet. That is not "clip missing", and the UI needs
    the two apart to know whether to wait or to offer a reingest.
    """
    project = await api.client.post("/projects", json={"name": "session"})
    finalized = await upload_clip(project.json()["id"], make_clip())
    assert finalized.status_code == 200
    # Deliberately no `queue.drain()`.

    response = await api.client.get(f"/media/{finalized.json()['media_file_id']}/proxy")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "artifact_not_ready"


async def test_a_stored_path_that_escapes_the_store_is_refused(
    api: Harness,
    make_clip: Callable[..., Path],
    upload_clip: Callable[..., Awaitable[httpx.Response]],
) -> None:
    """A corrupt row is not a way out of $DATA_DIR.

    `absolute()` is what enforces it; this asserts the route actually routes
    through it rather than opening `stored_path` directly, and that the failure
    is a named error instead of a 500 carrying a traceback.
    """
    media_file_id, digest = await _ingested_clip(api, upload_clip, make_clip())

    async with api.session_factory() as session:
        row = (
            (
                await session.execute(
                    select(DerivedArtifact).where(
                        DerivedArtifact.sha256 == digest,
                        DerivedArtifact.artifact_kind == ArtifactKind.PROXY.value,
                    )
                )
            )
            .scalars()
            .first()
        )
        assert row is not None
        row.stored_path = "../../../../etc/passwd"
        await session.commit()

    response = await api.client.get(f"/media/{media_file_id}/proxy")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "artifact_not_ready"


async def test_no_error_message_carries_a_filesystem_path(
    api: Harness,
    make_clip: Callable[..., Path],
    upload_clip: Callable[..., Awaitable[httpx.Response]],
) -> None:
    """`secrets.md`: a path on this machine contains the OS username."""
    project = await api.client.post("/projects", json={"name": "session"})
    finalized = await upload_clip(project.json()["id"], make_clip())
    response = await api.client.get(f"/media/{finalized.json()['media_file_id']}/proxy")

    message = response.json()["error"]["message"]
    assert "/" not in message
    assert "\\" not in message
    assert str(api.settings.data_dir) not in message
