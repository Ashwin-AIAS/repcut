"""Named API errors and the one handler that renders them.

`.claude/rules/code-style.md`: "Errors surface to the UI with a human-readable
cause, never a raw traceback." Every failure this engine can produce on purpose
is a class here, with a stable ``code`` the UI can branch on and a ``message`` it
can show a person without translation.

Two things a message must never contain, both enforced by keeping messages as
fixed sentences rather than f-strings over inputs:

- a filesystem path, which on this machine carries the OS username
  (`.claude/rules/secrets.md`)
- anything echoed back from the request, which turns an error page into a
  reflection surface
"""

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from repcut.logging import get_logger

logger = get_logger(__name__)


class RepcutAPIError(Exception):
    """A failure the engine produced deliberately, with a cause the UI renders."""

    status_code: int = status.HTTP_400_BAD_REQUEST
    code: str = "error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ProjectNotFoundError(RepcutAPIError):
    """No project with that id."""

    status_code = status.HTTP_404_NOT_FOUND
    code = "project_not_found"


class UploadNotFoundError(RepcutAPIError):
    """No upload session with that id."""

    status_code = status.HTTP_404_NOT_FOUND
    code = "upload_not_found"


class MediaFileNotFoundError(RepcutAPIError):
    """No clip with that id in this library."""

    status_code = status.HTTP_404_NOT_FOUND
    code = "media_file_not_found"


class ArtifactNotReadyError(RepcutAPIError):
    """The clip exists; the derived preview it was asked for does not.

    Not the same failure as a missing clip, and the UI acts on it differently:
    ingest may still be running, or may have failed and left the reference
    without a proxy. ``reingest`` is the fix, so the code has to be tellable
    apart from ``media_file_not_found``.

    Covers the recipe bump as well. Artifacts are keyed by ``params_version``,
    so a clip whose proxy was rendered under a superseded recipe has a file on
    disk and a row in the table, and still nothing to serve at the current
    version - which is the intended behaviour, not a gap.
    """

    status_code = status.HTTP_404_NOT_FOUND
    code = "artifact_not_ready"


class UploadClosedError(RepcutAPIError):
    """The session already completed or was abandoned."""

    status_code = status.HTTP_409_CONFLICT
    code = "upload_closed"


class ChunkOffsetError(RepcutAPIError):
    """The client sent a chunk starting somewhere the server is not.

    Never patched over by seeking: the authoritative offset is the server's, and
    accepting a chunk at the wrong place writes a hole or duplicate bytes that
    only surface as a hash mismatch at the end of a multi-gigabyte transfer.
    """

    status_code = status.HTTP_409_CONFLICT
    code = "chunk_offset_mismatch"


class UploadTooLargeError(RepcutAPIError):
    """More bytes arrived than the session declared."""

    status_code = status.HTTP_413_CONTENT_TOO_LARGE
    code = "upload_too_large"


class UploadIncompleteError(RepcutAPIError):
    """Finalize was called before every declared byte arrived."""

    status_code = status.HTTP_409_CONFLICT
    code = "upload_incomplete"


class UnsupportedMediaTypeError(RepcutAPIError):
    """The filename's extension is not one Repcut accepts.

    A cheap first gate so a text file does not occupy disk for the length of a
    transfer. It is not the real check - an extension is client-controlled - and
    ``NotAVideoError`` is what actually decides.
    """

    status_code = status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
    code = "unsupported_media_type"


class NotAVideoError(RepcutAPIError):
    """ffprobe could not read the assembled file as video.

    The authoritative check, because it reads the bytes rather than the name.
    """

    status_code = status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
    code = "not_a_video"


class HashMismatchError(RepcutAPIError):
    """The assembled bytes do not hash to what the client declared."""

    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "hash_mismatch"


async def handle_api_error(request: Request, error: Exception) -> JSONResponse:
    """Render a named error. Registered for ``RepcutAPIError`` only."""
    if not isinstance(error, RepcutAPIError):
        raise error
    logger.info("api_error", code=error.code, status_code=error.status_code, path=request.url.path)
    return JSONResponse(
        status_code=error.status_code,
        content={"error": {"code": error.code, "message": error.message}},
    )


def install_error_handler(app: FastAPI) -> None:
    """Wire the renderer for every named error."""
    app.add_exception_handler(RepcutAPIError, handle_api_error)


__all__ = [
    "ArtifactNotReadyError",
    "ChunkOffsetError",
    "HashMismatchError",
    "MediaFileNotFoundError",
    "NotAVideoError",
    "ProjectNotFoundError",
    "RepcutAPIError",
    "UnsupportedMediaTypeError",
    "UploadClosedError",
    "UploadIncompleteError",
    "UploadNotFoundError",
    "UploadTooLargeError",
    "install_error_handler",
]
