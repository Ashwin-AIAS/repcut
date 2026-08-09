"""Serving the derived bytes the library grid and the player actually render.

``MediaFileResponse`` has reported ``has_proxy`` and ``has_thumbnail_strip`` as
booleans since the ingest job landed, and until this module existed there was
nothing behind them: the grid had a flag saying a thumbnail was ready and no way
to fetch it. These are the two routes that close that gap.

Three properties this module is responsible for.

**The store stays the only path builder.** The path served comes from
``derived_artifacts.stored_path`` and is resolved by ``store.absolute()``.
Nothing here concatenates a path, and the resolution is not a formality: a
column read back is request input like any other
(`.claude/rules/security.md`), so it is checked rather than trusted because the
engine is what wrote it.

**Range requests are answered.** Starlette 0.38.6 - the version pinned here -
ships a ``FileResponse`` with no Range handling at all. Without ``206 Partial
Content`` a browser cannot seek within a video, so the player's arrow-key frame
stepping would silently degrade to refetching the whole proxy from byte 0 on
every keypress. It would still *look* correct on a five-second synthetic
fixture and fall apart on real footage, which is the kind of bug worth writing
forty lines to prevent.

**The version scope matches the flag.** ``has_proxy`` counts only artifacts at
the *current* ``params_version`` (``api/projects.py``). These routes resolve the
same way, so the flag and the bytes can never disagree about what is available.
"""

import asyncio
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Request, Response, status
from fastapi import Path as PathParam
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from repcut.api.deps import SessionDep, SettingsDep
from repcut.api.errors import ArtifactNotReadyError, MediaFileNotFoundError
from repcut.db.models import DerivedArtifact, MediaFile
from repcut.logging import get_logger
from repcut.media.artifacts import PARAMS_VERSION, ArtifactKind
from repcut.media.store import UnsafeStorePathError, absolute

logger = get_logger(__name__)

router = APIRouter(tags=["media"])

# Ids are `uuid4()` (`db/models.new_id`). Bounding the shape here means a
# malformed id is a 422 from the framework rather than a database round-trip,
# and it keeps the identifier's shape asserted at the boundary even though this
# value never becomes a path component - the path comes from `stored_path`.
#
# `^...$` rather than `\A...\Z`: pydantic compiles `pattern` with Rust's regex
# crate, which rejects `\A` outright (same note as `api/schemas.py`).
_UUID_PATTERN = r"^[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$"

MediaFileId = Annotated[str, PathParam(pattern=_UUID_PATTERN)]

# 64KiB per read. Small enough that a seek during playback is answered promptly,
# large enough that a multi-hundred-megabyte proxy is not millions of thread
# hops. Nothing here ever holds more than this much of a file in memory.
STREAM_CHUNK_BYTES = 64 * 1024

# `\d{1,19}` rather than `\d+`: a Range header is request input and every field
# of it gets a bound (`.claude/rules/security.md`). 19 digits is int64's reach,
# well past any file this will ever serve, and it keeps a megabyte of digits out
# of `int()`.
_RANGE_HEADER = re.compile(r"^bytes=(\d{0,19})-(\d{0,19})$")

_MEDIA_TYPES: dict[ArtifactKind, str] = {
    ArtifactKind.PROXY: "video/mp4",
    ArtifactKind.THUMBNAIL_STRIP: "image/jpeg",
}


class RangeNotSatisfiableError(Exception):
    """A syntactically valid range lying entirely beyond the end of the file.

    Distinct from a malformed header, which RFC 9110 §14.1.2 requires be
    *ignored* rather than rejected. Conflating the two is how a slightly odd
    client ends up unable to play a file it could have played.
    """


@dataclass(frozen=True)
class ByteRange:
    """A resolved, satisfiable range. ``end`` is inclusive, as HTTP counts."""

    start: int
    end: int

    @property
    def length(self) -> int:
        return self.end - self.start + 1


def parse_byte_range(header: str | None, size: int) -> ByteRange | None:
    """Resolve a ``Range`` header against a known file size.

    Returns ``None`` when the whole file should be sent - no header, a malformed
    one, or a multi-range request. All three are deliberate:

    - **Malformed must be ignored.** RFC 9110 §14.1.2 says so explicitly, and
      the failure mode of rejecting instead is a video that will not play at all
      rather than one that merely cannot seek.
    - **Multi-range may be ignored** (§14.2). No browser asks a video element
      for one, and answering properly means `multipart/byteranges`, which is a
      lot of surface for a case that does not arise.

    Raises ``RangeNotSatisfiableError`` for a *valid* range past the end, which
    is the one case that earns a 416.
    """
    if header is None or size <= 0:
        return None

    match = _RANGE_HEADER.match(header.strip())
    if match is None:
        return None

    first, last = match.group(1), match.group(2)

    if not first and not last:
        # `bytes=-` names nothing.
        return None

    if not first:
        # Suffix form: `bytes=-500` is the *last* 500 bytes, not the first 500.
        # Reading it as an offset serves the wrong part of the file with a 206
        # that claims to be right, so it is worth the separate branch.
        suffix = int(last)
        if suffix == 0:
            raise RangeNotSatisfiableError("a zero-length suffix range names nothing")
        return ByteRange(start=max(0, size - suffix), end=size - 1)

    start = int(first)
    if start >= size:
        raise RangeNotSatisfiableError("range starts past the end of the file")

    # An open-ended `bytes=N-` runs to EOF; a closed one is clamped, because a
    # client may legitimately ask for more than exists at the tail of a file.
    end = size - 1 if not last else min(int(last), size - 1)
    if end < start:
        # Backwards after clamping - malformed, so ignored rather than rejected.
        return None
    return ByteRange(start=start, end=end)


def _file_size(path: Path) -> int | None:
    """Size in bytes, or ``None`` when the file is not there."""
    try:
        return path.stat().st_size
    except (FileNotFoundError, NotADirectoryError):
        # The row survives its file: a half-cleaned $DATA_DIR, or a render that
        # was interrupted between the move and the insert.
        return None


async def _stream_file(path: Path, start: int, length: int) -> AsyncIterator[bytes]:
    """Yield ``length`` bytes from ``start``, reading off the event loop.

    Every read goes through a thread. A synchronous read of a 300MB proxy inside
    the loop would stall every other request and the job worker's progress
    events with it (`.claude/rules/code-style.md`: all I/O async).
    """
    remaining = length
    handle = await asyncio.to_thread(path.open, "rb")
    try:
        await asyncio.to_thread(handle.seek, start)
        while remaining > 0:
            chunk = await asyncio.to_thread(handle.read, min(STREAM_CHUNK_BYTES, remaining))
            if not chunk:
                # Truncated under us. Ending the body short is the honest
                # answer; content-length already told the client what to expect.
                break
            remaining -= len(chunk)
            yield chunk
    finally:
        await asyncio.to_thread(handle.close)


async def _resolve_artifact(
    media_file_id: str,
    kind: ArtifactKind,
    session: SessionDep,
    settings: SettingsDep,
) -> tuple[Path, int]:
    """The on-disk path and size of one clip's artifact, or a named error."""
    reference = await session.get(MediaFile, media_file_id)
    if reference is None:
        raise MediaFileNotFoundError("that clip is not in this library")

    statement = select(DerivedArtifact).where(
        DerivedArtifact.sha256 == reference.sha256,
        DerivedArtifact.artifact_kind == kind.value,
        DerivedArtifact.params_version == PARAMS_VERSION[kind],
    )
    artifact = (await session.execute(statement)).scalars().first()
    if artifact is None:
        raise ArtifactNotReadyError("that clip's preview has not been generated yet")

    try:
        path = absolute(settings.data_dir, artifact.stored_path)
    except UnsafeStorePathError as error:
        # A stored_path that will not resolve inside $DATA_DIR is a corrupt row,
        # not a client mistake. It is logged by kind and answered as "not ready"
        # - the path itself never reaches the response, because it carries the
        # OS username (`.claude/rules/secrets.md`).
        logger.warning("artifact_path_rejected", artifact_kind=kind.value, reason=str(error))
        raise ArtifactNotReadyError("that clip's preview has not been generated yet") from error

    size = await asyncio.to_thread(_file_size, path)
    if size is None:
        raise ArtifactNotReadyError("that clip's preview has not been generated yet")
    return path, size


async def _serve(
    request: Request,
    media_file_id: str,
    kind: ArtifactKind,
    session: SessionDep,
    settings: SettingsDep,
) -> Response:
    """Answer a GET for one artifact, honouring ``Range``."""
    path, size = await _resolve_artifact(media_file_id, kind, session, settings)

    # `private`: this is the user's footage and must not be held by anything
    # shared. `must-revalidate`: the URL is keyed by clip and kind, not by
    # content, so a reingest at a new params_version changes what lives here.
    headers = {
        "accept-ranges": "bytes",
        "cache-control": "private, max-age=0, must-revalidate",
    }

    try:
        byte_range = parse_byte_range(request.headers.get("range"), size)
    except RangeNotSatisfiableError:
        return Response(
            status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE,
            headers={**headers, "content-range": f"bytes */{size}"},
        )

    if byte_range is None:
        return StreamingResponse(
            _stream_file(path, 0, size),
            media_type=_MEDIA_TYPES[kind],
            headers={**headers, "content-length": str(size)},
        )

    return StreamingResponse(
        _stream_file(path, byte_range.start, byte_range.length),
        status_code=status.HTTP_206_PARTIAL_CONTENT,
        media_type=_MEDIA_TYPES[kind],
        headers={
            **headers,
            "content-length": str(byte_range.length),
            "content-range": f"bytes {byte_range.start}-{byte_range.end}/{size}",
        },
    )


@router.get(
    "/media/{media_file_id}/proxy",
    summary="The clip's 720p preview proxy",
    response_class=StreamingResponse,
)
async def get_proxy(
    request: Request,
    media_file_id: MediaFileId,
    session: SessionDep,
    settings: SettingsDep,
) -> Response:
    """Stream the proxy, with Range support so the player can seek.

    Preview only. Every real render reads the original bytes - a proxy is 720p
    and CRF 23, and grading a proxy would compound loss into the export.
    """
    return await _serve(request, media_file_id, ArtifactKind.PROXY, session, settings)


@router.get(
    "/media/{media_file_id}/thumbnail-strip",
    summary="The clip's tiled thumbnail strip",
    response_class=StreamingResponse,
)
async def get_thumbnail_strip(
    request: Request,
    media_file_id: MediaFileId,
    session: SessionDep,
    settings: SettingsDep,
) -> Response:
    """Stream the tiled JPEG the grid and the scrubber both read.

    One image rather than N files: a clip's strip is a single row of cells at
    one frame per two seconds, so the grid makes one request per clip and the
    database holds one row per clip.
    """
    return await _serve(request, media_file_id, ArtifactKind.THUMBNAIL_STRIP, session, settings)


__all__ = [
    "STREAM_CHUNK_BYTES",
    "ByteRange",
    "RangeNotSatisfiableError",
    "get_proxy",
    "get_thumbnail_strip",
    "parse_byte_range",
    "router",
]
