"""Where bytes live on disk, and what the database stores instead of a path.

Amendment 004 §6 fixes the layout:

```
$DATA_DIR/
  media/blobs/<sha[:2]>/<sha>/source<ext>
  media/derived/<sha[:2]>/<sha>/<artifact_kind>/<params_version>/<name>
  projects/<project_id>/                 # project-scoped output; no source bytes
  uploads/<upload_session_id>.part       # in-flight only
  repcut.db
```

Two rules this module exists to hold:

- **Every stored path is relative to ``$DATA_DIR`` and POSIX-separated.** An
  absolute path in the database carries the OS username (`.claude/rules/secrets.md`)
  and pins the store to one machine; a backslash-separated one pins it to one
  operating system.
- **No path component derives from anything the user typed.** The user's
  filename survives as ``media_files.display_name`` and nowhere else. Even the
  blob's extension comes from the container ffprobe reported, not from what the
  upload was called - a file named `.mp4` that is really Matroska gets `.mkv`
  here, because the store describes the bytes.

  This is now **enforced, not documented**. Every helper validates its
  identifier against the shape it is supposed to have - hex digest, UUID, slug -
  and ``absolute`` refuses to return a path outside ``$DATA_DIR``. Previously
  both were promises kept by the callers that happened to exist, which is not a
  control: a ``sha256`` of ``../../..`` built exactly that path, and
  ``PurePosixPath`` normalises nothing.

The two-character shard keeps any one directory to a few hundred entries on a
filesystem that slows down with tens of thousands.
"""

import re
from pathlib import Path, PurePosixPath

# Long enough to shard, short enough to stay readable when browsing the store.
SHARD_LENGTH = 2

# Length of a hex-encoded SHA-256 digest. Mirrors db.models.SHA256_LENGTH;
# duplicated rather than imported so this module stays free of the ORM.
SHA256_LENGTH = 64


class UnsafeStorePathError(ValueError):
    """An identifier was rejected before it could become a filesystem path.

    Raised, never returned, and never rendered to the UI verbatim: reaching this
    means a value arrived somewhere it should not have, and the caller turns it
    into a generic 400 rather than echoing what was sent.
    """


_HEX_DIGEST = re.compile(rf"\A[0-9a-f]{{{SHA256_LENGTH}}}\Z")
_UUID = re.compile(r"\A[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}\Z")
_SLUG = re.compile(r"\A[a-z0-9][a-z0-9_]{0,63}\Z")


def _checked_digest(value: str, field: str) -> str:
    """A lowercase hex SHA-256, or refuse.

    The module docstring promises no path component derives from user input.
    That promise held by convention only - every helper below interpolated its
    argument straight into a path - and a convention is not a control. A
    ``sha256`` of ``../../../../etc/passwd`` produced exactly that path, and
    ``PurePosixPath`` does not normalise ``..`` away, so nothing downstream
    would have noticed.

    Digests are engine-computed, but ``UploadSession.declared_sha256`` is
    client-supplied by design, and resume looks a row up by it. That is one
    refactor away from being the value a path is built from.
    """
    if not _HEX_DIGEST.fullmatch(value):
        raise UnsafeStorePathError(f"{field} is not a hex SHA-256 digest")
    return value


def _checked_uuid(value: str, field: str) -> str:
    """A UUID, or refuse. Project and upload-session ids are ``uuid4()``."""
    if not _UUID.fullmatch(value):
        raise UnsafeStorePathError(f"{field} is not a UUID")
    return value


def _checked_slug(value: str, field: str) -> str:
    """A lowercase identifier safe as a single path component, or refuse.

    ``artifact_kind`` is deliberately an open set (``db.models``), validated in
    Python rather than by a CHECK. This is that validation, at the point it
    becomes a directory name.
    """
    if not _SLUG.fullmatch(value):
        raise UnsafeStorePathError(f"{field} is not a safe path component")
    return value


# The blob is only ever an FFmpeg *input*, so its extension is for humans and
# for double-clicking, not for muxer selection. Keyed by ffprobe's
# ``format_name``, which is a comma-joined list of every format the demuxer
# covers rather than the one it picked.
_CONTAINER_SUFFIX: dict[str, str] = {
    "mov,mp4,m4a,3gp,3g2,mj2": ".mp4",
    "matroska,webm": ".mkv",
    "avi": ".avi",
    "mpegts": ".ts",
    "flv": ".flv",
    "asf": ".wmv",
}

# Anything outside this cannot become part of a filename. ffprobe's output is
# not user input, but it is still text arriving from a subprocess.
_UNSAFE_IN_SUFFIX = re.compile(r"[^a-z0-9]")

_FALLBACK_SUFFIX = ".bin"


def container_suffix(format_name: str) -> str:
    """The file extension for a container, derived from what ffprobe reported.

    Unknown containers fall back to their first format token, sanitised. A
    format ffprobe named but this table does not know still produces a
    browsable file rather than an opaque one.
    """
    known = _CONTAINER_SUFFIX.get(format_name)
    if known is not None:
        return known
    first = format_name.split(",")[0].strip().casefold()
    cleaned = _UNSAFE_IN_SUFFIX.sub("", first)
    return f".{cleaned}" if cleaned else _FALLBACK_SUFFIX


def blob_directory(sha256: str) -> PurePosixPath:
    """Directory holding one blob's source bytes, relative to ``$DATA_DIR``."""
    digest = _checked_digest(sha256, "sha256")
    return PurePosixPath("media", "blobs", digest[:SHARD_LENGTH], digest)


def blob_path(sha256: str, suffix: str) -> PurePosixPath:
    """Path to one blob's source bytes, relative to ``$DATA_DIR``.

    ``suffix`` is re-sanitised rather than trusted. It normally arrives from
    ``container_suffix`` and is already safe; this covers the caller that builds
    one by hand from ffprobe output and puts a separator in it.
    """
    cleaned = _UNSAFE_IN_SUFFIX.sub("", suffix.lstrip(".").casefold())
    return blob_directory(sha256) / f"source.{cleaned or 'bin'}"


def derived_directory(sha256: str, artifact_kind: str, params_version: int) -> PurePosixPath:
    """Directory for one derived artifact key, relative to ``$DATA_DIR``.

    ``params_version`` is a path component, not a filename suffix, so a recipe
    bump lands beside the superseded render rather than on top of it. That is
    what makes a bump non-destructive (amendment 004 §6).
    """
    digest = _checked_digest(sha256, "sha256")
    kind = _checked_slug(artifact_kind, "artifact_kind")
    if params_version < 1:
        raise UnsafeStorePathError("params_version must be positive")
    return (
        PurePosixPath("media", "derived", digest[:SHARD_LENGTH], digest)
        / kind
        / str(params_version)
    )


def derived_path(
    sha256: str, artifact_kind: str, params_version: int, filename: str
) -> PurePosixPath:
    """Path to one derived artifact, relative to ``$DATA_DIR``.

    ``filename`` is checked as a single component. It is a builder-chosen
    constant today; a later prompt naming an export after the user's project
    title is exactly how that stops being true.
    """
    if "/" in filename or "\\" in filename or filename in ("", ".", ".."):
        raise UnsafeStorePathError("filename is not a single path component")
    return derived_directory(sha256, artifact_kind, params_version) / filename


def part_path(upload_session_id: str) -> PurePosixPath:
    """Path to an in-flight transfer's ``.part`` file, relative to ``$DATA_DIR``."""
    return PurePosixPath("uploads", f"{_checked_uuid(upload_session_id, 'upload_session_id')}.part")


def project_directory(project_id: str) -> PurePosixPath:
    """Project-scoped output directory, relative to ``$DATA_DIR``.

    Holds rendered output and project metadata. Never source bytes - those are
    content-addressed and shared between projects.
    """
    return PurePosixPath("projects", _checked_uuid(project_id, "project_id"))


def absolute(data_dir: Path, stored: PurePosixPath | str) -> Path:
    """Resolve a stored relative path against ``$DATA_DIR``, refusing to escape it.

    The containment check is not redundant with the validators above. This
    function's argument is a ``stored_path`` **read back from the database**, and
    the database is not a trust boundary: the column is a free ``String(512)``
    with no CHECK that it is relative, so a row written by an older build, a
    hand-edited SQLite file, or a future writer that skips these helpers can
    hold anything.

    Two ways the naive join escapes, both silent:

    - ``Path("/data") / Path("/etc/shadow")`` is ``/etc/shadow``. Joining an
      absolute path *replaces* the base; pathlib does not treat this as an
      error, and on Windows a bare drive letter does the same.
    - ``..`` segments are not normalised by ``PurePosixPath``, so
      ``media/../../..`` survives the join and resolves out of the store.

    Resolving both sides before comparing is what makes the check real: a
    symlink planted inside ``$DATA_DIR`` and pointing outside is followed by
    ``resolve()``, so it is caught here rather than at the ``open()``.
    """
    candidate = Path(str(stored))
    if candidate.is_absolute() or candidate.drive or candidate.anchor:
        raise UnsafeStorePathError("stored path must be relative to DATA_DIR")

    root = data_dir.resolve()
    resolved = (root / candidate).resolve()
    if resolved != root and root not in resolved.parents:
        raise UnsafeStorePathError("stored path resolves outside DATA_DIR")
    return resolved


__all__ = [
    "SHA256_LENGTH",
    "SHARD_LENGTH",
    "UnsafeStorePathError",
    "absolute",
    "blob_directory",
    "blob_path",
    "container_suffix",
    "derived_directory",
    "derived_path",
    "part_path",
    "project_directory",
]
