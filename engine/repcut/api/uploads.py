"""Chunked, resumable, hash-verified local upload.

Three properties, each with the failure it prevents:

- **Streamed, never buffered.** The body is written to disk as it arrives, a
  megabyte at a time, and the digest is computed by reading the finished file
  back in blocks. Nothing here ever holds a clip in memory - a 2GB session has
  to leave room on the machine for FFmpeg.
- **The offset is the server's.** Resume returns
  ``min(bytes_received, size of the .part file)``. Either can be ahead: a
  flushed write whose commit was lost leaves the file ahead, a commit whose
  write was lost leaves the row ahead, and only the lesser is safe in both
  directions (amendment 004 §7). A client-claimed offset is never trusted -
  believing one appends duplicate bytes that surface as a hash mismatch at the
  end of a multi-gigabyte transfer.
- **Bytes decide what a file is, not its name.** The extension is a cheap first
  gate; ffprobe reading the assembled file is the real one. A `.txt` renamed to
  `.mp4` is rejected with a named error and leaves no ``media_blobs`` row.

Finalize is idempotent. Re-running a completed transfer yields the same blob,
the same single ``media_files`` reference and no second copy on disk, whatever
the number of interruptions.
"""

import asyncio
import hashlib
import os
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from fastapi import APIRouter, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from repcut.api.deps import JobQueueDep, SessionDep, SettingsDep
from repcut.api.errors import (
    ChunkOffsetError,
    HashMismatchError,
    MediaToolingUnavailableError,
    NotAVideoError,
    ProjectNotFoundError,
    UnsupportedMediaTypeError,
    UploadClosedError,
    UploadIncompleteError,
    UploadNotFoundError,
    UploadTooLargeError,
)
from repcut.api.schemas import UploadCreate, UploadFinalizeResponse, UploadResponse
from repcut.config import Settings
from repcut.db.models import (
    DerivedArtifact,
    MediaBlob,
    MediaFile,
    Project,
    UploadSession,
    UploadStatus,
)
from repcut.logging import get_logger
from repcut.media.artifacts import PARAMS_VERSION, ArtifactKind
from repcut.media.ffmpeg_builder import FFmpegError, FFmpegUnavailableError
from repcut.media.ingest import INGEST_JOB_TYPE, probe_media
from repcut.media.metadata import MediaProperties, ProbeParseError
from repcut.media.store import absolute, blob_path, container_suffix, part_path

logger = get_logger(__name__)

router = APIRouter(tags=["uploads"])

# Written to disk this often while a chunk streams in. Bounds resident memory
# per in-flight request without paying a syscall per 64KB of network read.
_FLUSH_BYTES = 1024 * 1024

# Read back in these blocks to hash. Same reasoning, other direction.
_HASH_BLOCK_BYTES = 1024 * 1024

# The cheap first gate. Deliberately generous - it exists so a text file does
# not occupy disk for the length of a transfer, not to decide what is video.
# ffprobe reading the bytes is the check that actually decides.
VIDEO_SUFFIXES: frozenset[str] = frozenset(
    {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".3gp", ".mts", ".m2ts", ".ts", ".wmv"}
)

_NOT_A_VIDEO_MESSAGE = "this file is not a video Repcut can read"


# --- blocking filesystem helpers (always called through asyncio.to_thread) ---


def _open_at(path: Path, offset: int) -> BinaryIO:
    """Open the part file positioned at ``offset``, creating it if new."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)
    handle = path.open("r+b")
    handle.seek(offset)
    return handle


def _flush_and_truncate(handle: BinaryIO) -> None:
    """Commit the writes and drop anything past the write head.

    Truncating matters on a replay: a re-sent chunk shorter than the one before
    it would otherwise leave the tail of the old chunk in place, and the file
    would hash to something the client never sent.
    """
    handle.truncate()
    handle.flush()
    os.fsync(handle.fileno())


def _hash_file(path: Path) -> str:
    """SHA-256 of a file, read in blocks. Never loads it into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(_HASH_BLOCK_BYTES):
            digest.update(block)
    return digest.hexdigest()


def _file_size(path: Path) -> int:
    """Size on disk, or 0 when the file is not there yet."""
    try:
        return path.stat().st_size
    except OSError:
        # Named: the part file has not been created yet, or was cleaned up.
        return 0


def _move_into_store(temp: Path, final: Path) -> None:
    """Move an assembled upload into the content-addressed store.

    ``os.replace`` rather than a copy, so the transition is atomic and the store
    never holds a half-written blob at a finished-looking path.
    """
    final.parent.mkdir(parents=True, exist_ok=True)
    os.replace(temp, final)


# --- lookups -----------------------------------------------------------------


async def _require_project(session: AsyncSession, project_id: str) -> Project:
    project = await session.get(Project, project_id)
    if project is None:
        raise ProjectNotFoundError("that project does not exist")
    return project


async def _require_upload(session: AsyncSession, upload_id: str) -> UploadSession:
    upload = await session.get(UploadSession, upload_id)
    if upload is None:
        raise UploadNotFoundError("that upload session does not exist")
    return upload


async def _find_in_progress(
    session: AsyncSession, project_id: str, sha256: str | None
) -> UploadSession | None:
    """The open transfer of these bytes into this project, if there is one.

    This is the lookup path ``uq_upload_sessions_in_progress`` exists for: a
    browser tab that refreshed mid-upload has lost its session id but can always
    re-state the project and the hash. Without a declared hash there is nothing
    to look up by, which is exactly why the index treats those rows as distinct.
    """
    if sha256 is None:
        return None
    statement = select(UploadSession).where(
        UploadSession.project_id == project_id,
        UploadSession.declared_sha256 == sha256,
        UploadSession.status == UploadStatus.IN_PROGRESS,
    )
    return (await session.execute(statement)).scalars().first()


async def _resume_offset(settings: Settings, upload: UploadSession) -> int:
    """The authoritative offset: the lesser of the row and the file.

    Reconciled on every read rather than only after a restart, because from
    inside the process there is no way to tell which kind of interruption
    happened - or that one happened at all.
    """
    on_disk = await asyncio.to_thread(_file_size, absolute(settings.data_dir, upload.part_path))
    return min(upload.bytes_received, on_disk)


def _as_response(upload: UploadSession, bytes_received: int, *, resumed: bool) -> UploadResponse:
    return UploadResponse(
        id=upload.id,
        project_id=upload.project_id,
        display_name=upload.display_name,
        declared_size_bytes=upload.declared_size_bytes,
        chunk_size_bytes=upload.chunk_size_bytes,
        bytes_received=bytes_received,
        status=upload.status,
        resumed=resumed,
    )


# --- routes ------------------------------------------------------------------


@router.post(
    "/projects/{project_id}/uploads",
    response_model=UploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Begin or resume a chunked upload",
)
async def create_upload(
    project_id: str,
    body: UploadCreate,
    session: SessionDep,
    settings: SettingsDep,
    response: Response,
) -> UploadResponse:
    """Open a transfer, or hand back the one already open for these bytes.

    Never two sessions for one clip in one project: the partial unique index
    makes a second insert an ``IntegrityError``, and that error is the resume
    signal rather than a failure. Racing tabs therefore converge on one
    ``.part`` file instead of orphaning one of them.
    """
    await _require_project(session, project_id)

    if Path(body.display_name).suffix.casefold() not in VIDEO_SUFFIXES:
        raise UnsupportedMediaTypeError(
            "Repcut reads video files - this one's type is not among them"
        )

    existing = await _find_in_progress(session, project_id, body.sha256)
    if existing is not None:
        response.status_code = status.HTTP_200_OK
        return _as_response(existing, await _resume_offset(settings, existing), resumed=True)

    upload = UploadSession(
        project_id=project_id,
        display_name=body.display_name,
        declared_size_bytes=body.size_bytes,
        chunk_size_bytes=body.chunk_size_bytes,
        declared_sha256=body.sha256,
        part_path="",
    )
    session.add(upload)
    await session.flush()
    upload.part_path = str(part_path(upload.id))

    try:
        await session.commit()
    except IntegrityError:
        # Named: uq_upload_sessions_in_progress. Two tabs declared the same clip
        # at once; the loser adopts the winner's session rather than opening a
        # transfer nothing will ever reference.
        await session.rollback()
        raced = await _find_in_progress(session, project_id, body.sha256)
        if raced is None:
            raise
        response.status_code = status.HTTP_200_OK
        return _as_response(raced, await _resume_offset(settings, raced), resumed=True)

    logger.info("upload_opened", upload_id=upload.id, declared_size_bytes=body.size_bytes)
    return _as_response(upload, 0, resumed=False)


@router.get(
    "/projects/{project_id}/uploads/in-progress",
    response_model=UploadResponse,
    summary="Find an open transfer without its session id",
)
async def find_upload(
    project_id: str, sha256: str, session: SessionDep, settings: SettingsDep
) -> UploadResponse:
    """Resume path for a client that lost the id - a refreshed browser tab."""
    upload = await _find_in_progress(session, project_id, sha256)
    if upload is None:
        raise UploadNotFoundError("no transfer of that clip is in progress for this project")
    return _as_response(upload, await _resume_offset(settings, upload), resumed=True)


@router.get("/uploads/{upload_id}", response_model=UploadResponse, summary="Transfer status")
async def get_upload(upload_id: str, session: SessionDep, settings: SettingsDep) -> UploadResponse:
    """Where a transfer stands, including the offset to resume from."""
    upload = await _require_upload(session, upload_id)
    return _as_response(upload, await _resume_offset(settings, upload), resumed=False)


@router.put(
    "/uploads/{upload_id}/chunk",
    response_model=UploadResponse,
    summary="Append one chunk, streamed",
)
async def put_chunk(
    upload_id: str,
    offset: int,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
) -> UploadResponse:
    """Write the request body at ``offset``, streaming it to disk as it arrives.

    ``offset`` must equal the server's own resume offset. Rejecting a mismatch is
    the safety property the whole endpoint rests on: seeking to wherever the
    client asked would write a hole or duplicate bytes, and the corruption would
    only become visible as a hash mismatch after the entire clip had transferred.
    """
    upload = await _require_upload(session, upload_id)
    if upload.status is not UploadStatus.IN_PROGRESS:
        raise UploadClosedError("that transfer has already finished")

    expected = await _resume_offset(settings, upload)
    if offset != expected:
        raise ChunkOffsetError(
            "this chunk does not continue where the transfer left off - "
            "read the transfer's current offset and resend from there"
        )

    written = await _write_body(
        absolute(settings.data_dir, upload.part_path),
        offset,
        request,
        upload.declared_size_bytes,
    )

    upload.bytes_received = written
    await session.commit()
    return _as_response(upload, written, resumed=False)


async def _write_body(destination: Path, offset: int, request: Request, limit: int) -> int:
    """Stream the request body onto disk from ``offset``. Returns the new size.

    Buffered to ``_FLUSH_BYTES`` and never further: the whole point of chunked
    upload is that neither the client nor the engine holds the clip, and reading
    ``await request.body()`` here would undo that in one line.
    """
    handle = await asyncio.to_thread(_open_at, destination, offset)
    written = offset
    try:
        buffer = bytearray()
        async for piece in request.stream():
            buffer += piece
            written += len(piece)
            if written > limit:
                raise UploadTooLargeError("more data arrived than this transfer declared")
            if len(buffer) >= _FLUSH_BYTES:
                await asyncio.to_thread(handle.write, bytes(buffer))
                buffer.clear()
        if buffer:
            await asyncio.to_thread(handle.write, bytes(buffer))
        await asyncio.to_thread(_flush_and_truncate, handle)
    finally:
        await asyncio.to_thread(handle.close)
    return written


@router.post(
    "/uploads/{upload_id}/finalize",
    response_model=UploadFinalizeResponse,
    summary="Verify, store and enqueue ingest",
)
async def finalize_upload(
    upload_id: str,
    session: SessionDep,
    settings: SettingsDep,
    queue: JobQueueDep,
) -> UploadFinalizeResponse:
    """Hash, verify it is video, move into the store, reference it, ingest it.

    The order matters. Nothing reaches ``media_blobs`` until ffprobe has read the
    assembled bytes, so a rejected file leaves the database exactly as it found
    it - which is what criterion 3 measures.
    """
    upload = await _require_upload(session, upload_id)
    part = absolute(settings.data_dir, upload.part_path)

    if upload.status is UploadStatus.COMPLETED:
        # Idempotent: a client retrying finalize after a dropped response gets
        # the same answer rather than a second reference.
        return await _completed_result(session, upload)
    if upload.status is not UploadStatus.IN_PROGRESS:
        raise UploadClosedError("that transfer was abandoned and cannot be finished")

    size = await asyncio.to_thread(_file_size, part)
    if size != upload.declared_size_bytes:
        raise UploadIncompleteError("the transfer is not complete yet")

    digest = await asyncio.to_thread(_hash_file, part)
    if upload.declared_sha256 is not None and digest != upload.declared_sha256:
        await _abort(session, upload)
        raise HashMismatchError(
            "the uploaded file does not match the checksum the browser computed - "
            "the transfer was corrupted and needs to be repeated"
        )

    properties = await _probe_or_reject(session, upload, part)
    stored = blob_path(digest, container_suffix(properties.container_format))
    blob_file = absolute(settings.data_dir, stored)

    duplicate = await _blob_exists(session, digest) and blob_file.is_file()
    if duplicate:
        # These bytes are already in the store. Drop the copy that just arrived
        # rather than rewriting an identical file (amendment 004, Constraints).
        await asyncio.to_thread(part.unlink, True)
        logger.info("upload_deduplicated", sha256_prefix=digest[:8])
    else:
        await asyncio.to_thread(_move_into_store, part, blob_file)
        await _upsert_blob(session, digest, size, stored)

    media_file = await _reference_blob(session, upload, digest)
    upload.status = UploadStatus.COMPLETED
    # The engine's own digest, recorded so a retried finalize can find what this
    # session produced. Safe to write over a client-declared value: it was just
    # verified equal, and the partial unique index covers in-progress rows only,
    # so a completed row cannot collide with anything.
    upload.declared_sha256 = digest
    await session.commit()

    job_id = None
    if not await _artifacts_complete(session, digest):
        job_id = await queue.enqueue(INGEST_JOB_TYPE, project_id=upload.project_id, sha256=digest)

    return UploadFinalizeResponse(
        sha256=digest,
        media_file_id=media_file.id,
        project_id=upload.project_id,
        job_id=job_id,
        duplicate=duplicate,
    )


# --- finalize helpers --------------------------------------------------------


async def _probe_or_reject(
    session: AsyncSession, upload: UploadSession, part: Path
) -> MediaProperties:
    """ffprobe the assembled file, or reject it with a named error.

    Both halves of criterion 3 land here: a `.txt` that got past the extension
    gate and an `.mp4`-named non-video fail identically, because both are decided
    by the bytes rather than by the name.

    What must *not* land in the same branch is a failure of the engine. The
    ordering below is the correction: ``FFmpegUnavailableError`` is checked
    first, answers 503, and - the part that matters - does not call ``_abort``.
    Aborting closes the session, and the client's resume path looks sessions up
    by ``status == IN_PROGRESS``; a closed one cannot be resumed, so on a
    completed multi-gigabyte transfer the bytes on disk become unreachable and
    have to be sent again. The engine failing to start a subprocess is not
    grounds for making the user re-upload 2GB.
    """
    try:
        return await probe_media(part)
    except FFmpegUnavailableError as error:
        # Named: ffprobe could not be executed at all - missing from PATH, not
        # executable, or an event loop with no subprocess transport. Nothing is
        # known about the file, so nothing is decided about it and the transfer
        # stays open for a retry once the machine is fixed.
        logger.error("probe_tooling_unavailable", cause=error.cause)
        raise MediaToolingUnavailableError(
            "the engine cannot read video files right now - its media tools are unavailable"
        ) from error
    except ProbeParseError as error:
        # Named: ffprobe read the file and it is not video.
        await _abort(session, upload)
        raise NotAVideoError(_NOT_A_VIDEO_MESSAGE) from error
    except FFmpegError as error:
        # Named: ffprobe ran and could not open the file - an unreadable
        # container, a truncated file. A statement about the bytes, so the
        # session is closed as it is for any other rejected upload.
        await _abort(session, upload)
        raise NotAVideoError(_NOT_A_VIDEO_MESSAGE) from error


async def _abort(session: AsyncSession, upload: UploadSession) -> None:
    """Close a rejected transfer so its slot in the index is released."""
    upload.status = UploadStatus.ABORTED
    await session.commit()


async def _blob_exists(session: AsyncSession, sha256: str) -> bool:
    return await session.get(MediaBlob, sha256) is not None


async def _upsert_blob(
    session: AsyncSession, sha256: str, size_bytes: int, stored: PurePosixPath
) -> None:
    """Create the blob row. Media properties stay null until ingest fills them."""
    blob = await session.get(MediaBlob, sha256)
    if blob is None:
        session.add(MediaBlob(sha256=sha256, size_bytes=size_bytes, stored_path=str(stored)))
    else:
        blob.stored_path = str(stored)
        blob.size_bytes = size_bytes
    await session.flush()


async def _reference_blob(session: AsyncSession, upload: UploadSession, sha256: str) -> MediaFile:
    """One reference per (project, blob). Re-uploading the same clip is a no-op.

    The uniqueness is the database's, not this function's guess: the constraint
    is what makes "exactly one media_files row" true after any number of
    interrupted retries.
    """
    statement = select(MediaFile).where(
        MediaFile.project_id == upload.project_id, MediaFile.sha256 == sha256
    )
    existing = (await session.execute(statement)).scalars().first()
    if existing is not None:
        return existing

    used = await session.scalar(
        select(func.count()).select_from(MediaFile).where(MediaFile.project_id == upload.project_id)
    )
    media_file = MediaFile(
        project_id=upload.project_id,
        sha256=sha256,
        display_name=upload.display_name,
        position=used or 0,
    )
    session.add(media_file)
    await session.flush()
    return media_file


async def _artifacts_complete(session: AsyncSession, sha256: str) -> bool:
    """Whether every artifact kind already exists at its current version.

    When it does, the second project's upload enqueues no job at all - the
    measurable half of "a duplicate re-encodes no proxy".
    """
    statement = select(DerivedArtifact.artifact_kind).where(
        DerivedArtifact.sha256 == sha256,
        DerivedArtifact.params_version.in_(
            [PARAMS_VERSION[kind] for kind in ArtifactKind if kind in PARAMS_VERSION]
        ),
    )
    present = set((await session.execute(statement)).scalars().all())
    return all(kind.value in present for kind in ArtifactKind)


async def _completed_result(session: AsyncSession, upload: UploadSession) -> UploadFinalizeResponse:
    """The answer a completed session already produced."""
    if upload.declared_sha256 is None:
        raise UploadClosedError("that transfer has already finished")
    statement = select(MediaFile).where(
        MediaFile.project_id == upload.project_id,
        MediaFile.sha256 == upload.declared_sha256,
    )
    reference = (await session.execute(statement)).scalars().first()
    if reference is None:
        raise UploadClosedError("that transfer has already finished")
    return UploadFinalizeResponse(
        sha256=reference.sha256,
        media_file_id=reference.id,
        project_id=upload.project_id,
        job_id=None,
        duplicate=True,
    )


__all__ = ["VIDEO_SUFFIXES", "router"]
