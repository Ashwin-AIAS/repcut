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

import traceback

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from repcut.logging import get_logger
from repcut.redaction import redact_paths

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


class MediaBlobNotFoundError(RepcutAPIError):
    """No blob with that digest in the media library.

    Distinct from ``MediaFileNotFoundError``: scenes are keyed on the blob's
    ``sha256`` (``db/models.py``'s ``Scene`` docstring), not on a project's
    ``media_files`` reference to it, so the id this route looks up by is not
    the same id that error is about.
    """

    status_code = status.HTTP_404_NOT_FOUND
    code = "media_blob_not_found"


class SceneNotFoundError(RepcutAPIError):
    """No scene with that id for this clip.

    One error for "wrong scene id", "wrong clip" and "both" - telling them
    apart would either leak which clips exist to a caller that only guessed a
    scene id, or cost a second lookup this route has no other reason to make.
    """

    status_code = status.HTTP_404_NOT_FOUND
    code = "scene_not_found"


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


class MediaToolingUnavailableError(RepcutAPIError):
    """FFmpeg could not be run. The machine's problem, not the clip's.

    Deliberately not ``not_a_video``, and deliberately a 5xx. The two are easy to
    conflate - both surface at the same line of ``finalize`` - and conflating
    them cost real bytes: an engine that could not start ffprobe told the user
    their footage was unreadable and aborted the transfer, so a completed 2GB
    upload had to be re-sent. A 503 says "try again once the engine is fixed",
    which is true, and leaves the session open, which is what makes trying again
    free.
    """

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "media_tooling_unavailable"


class UnexpectedEngineError(RepcutAPIError):
    """A failure the engine did not name. The boundary's answer, never raised directly.

    Exists so that "unhandled" still has a shape the UI can parse. Everything
    reaching it is a bug, and the bug is in the log with its traceback - what
    must not happen is the traceback going to the client, or a JSON-speaking
    client getting 21 bytes of ``text/plain`` it cannot decode.
    """

    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    code = "internal_error"


_UNEXPECTED_MESSAGE = "the engine hit an unexpected problem handling this request"


async def handle_api_error(request: Request, error: Exception) -> JSONResponse:
    """Render a named error. Registered for ``RepcutAPIError`` only."""
    if not isinstance(error, RepcutAPIError):
        raise error
    logger.info("api_error", code=error.code, status_code=error.status_code, path=request.url.path)
    return JSONResponse(
        status_code=error.status_code,
        content={"error": {"code": error.code, "message": error.message}},
    )


class UnexpectedErrorBoundary:
    """Last resort: turn anything unnamed into a named error, redacted.

    Pure ASGI rather than a Starlette exception handler, and that is the whole
    reason it exists. ``ServerErrorMiddleware`` re-raises after calling a
    handler - "so servers can log the error" - and what uvicorn then logs is a
    full traceback. On this machine every frame in it is an absolute path under
    the user's OneDrive directory, so the traceback carries the OS username
    (`.claude/rules/secrets.md`), and the client meanwhile gets Starlette's
    21-byte ``text/plain`` "Internal Server Error", which the UI's Zod parse
    cannot read as an error at all.

    Outside everything, so it sees exceptions from routes, dependencies and the
    other middleware alike. Four properties, each load-bearing:

    - **Cancellation passes through.** ``CancelledError`` derives from
      ``BaseException``, not ``Exception``, so a browser navigating away is not
      caught here. That is relied on, not incidental: swallowing it would break
      the shutdown path ``JobQueue`` depends on.
    - **A started response is not rewritten.** Media streams send their head
      before their body. Once bytes are on the wire, swallowing would leave the
      client a truncated body under a 200, which is worse than a broken
      connection - so the exception is re-raised and the transfer visibly fails.
    - **The log keeps the traceback, scrubbed.** Suppressing it would trade a
      leak for a blind spot. ``redact_paths`` reduces each frame to its filename,
      which is what makes the trace readable *and* publishable.
    - **Known residual:** the re-raise on a started response does reach uvicorn's
      own logger, which prints an unredacted trace to the console. It cannot be
      both signalled and silent, and by then this boundary has already written
      the scrubbed copy. No response body is affected.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            # WebSocket and lifespan failures are not HTTP responses and cannot
            # be answered with one. `api/jobs.py` owns the socket's own closing.
            await self.app(scope, receive, send)
            return

        started = False

        async def _send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, _send)
        # The one place in the engine that catches Exception rather than a named
        # failure, and the reason `.claude/rules/code-style.md` can require named
        # catches everywhere else: this is the net under them. Everything it
        # catches is a bug, and its job is to make that bug legible instead of
        # letting it choose the response.
        except Exception as error:
            self._log(scope, error)
            if started:
                raise
            response = JSONResponse(
                status_code=UnexpectedEngineError.status_code,
                content={
                    "error": {
                        "code": UnexpectedEngineError.code,
                        "message": _UNEXPECTED_MESSAGE,
                    }
                },
            )
            await response(scope, receive, send)

    def _log(self, scope: Scope, error: Exception) -> None:
        """One ERROR line carrying the type and a scrubbed traceback."""
        trace = "".join(traceback.format_exception(error))
        logger.error(
            "unhandled_request_error",
            error_type=type(error).__name__,
            path=scope.get("path"),
            method=scope.get("method"),
            traceback=redact_paths(trace),
        )


def install_error_handler(app: FastAPI) -> None:
    """Wire the renderer for every named error, then the net beneath it."""
    app.add_exception_handler(RepcutAPIError, handle_api_error)
    # Added last, so it is outermost: Starlette applies middleware in reverse.
    # It must wrap the security middleware too - a TrustedHost rejection is a
    # response, but a bug *inside* a middleware would otherwise bypass this.
    app.add_middleware(UnexpectedErrorBoundary)


__all__ = [
    "ArtifactNotReadyError",
    "ChunkOffsetError",
    "HashMismatchError",
    "MediaBlobNotFoundError",
    "MediaFileNotFoundError",
    "MediaToolingUnavailableError",
    "NotAVideoError",
    "ProjectNotFoundError",
    "RepcutAPIError",
    "SceneNotFoundError",
    "UnexpectedEngineError",
    "UnexpectedErrorBoundary",
    "UnsupportedMediaTypeError",
    "UploadClosedError",
    "UploadIncompleteError",
    "UploadNotFoundError",
    "UploadTooLargeError",
    "install_error_handler",
]
