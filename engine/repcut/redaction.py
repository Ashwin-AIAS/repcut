"""Path redaction, shared by everything that renders a failure.

On this machine ``$DATA_DIR`` and the repository both sit under a directory
named for the OS user, so any string carrying an absolute path carries the
username with it (`.claude/rules/secrets.md`). That is true of an FFmpeg stderr
line, and it is equally true of a Python traceback - which is how it escaped:
the redactor lived in ``media/ffmpeg_builder.py``, so only FFmpeg's output was
ever passed through it, and the one unhandled exception that reached the ASGI
layer printed nine frames of absolute paths.

It lives here rather than in ``ffmpeg_builder`` so ``api/errors.py`` can reach
it without the API layer importing the media layer. ``ffmpeg_builder`` re-exports
it, so its own callers are unchanged.
"""

import re

# Two or more path segments, optionally drive-qualified. Deliberately requires
# two so that filter arguments like `fps=1/2` are left alone.
_PATH_LIKE = re.compile(r"(?:[A-Za-z]:)?(?:[\\/][^\\/\s'\"]+){2,}")


def redact_paths(text: str) -> str:
    """Replace path-like runs with their final component only.

    Stored filenames are derived from the content hash or the session id, never
    from the user's filename (amendment 004), so the last segment is safe to
    keep and is the only part worth reading in a log.
    """
    return _PATH_LIKE.sub(
        lambda match: f".../{match.group(0).rsplit('/', 1)[-1]}", text.replace("\\", "/")
    )


__all__ = ["redact_paths"]
