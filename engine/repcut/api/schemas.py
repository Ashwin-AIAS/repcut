"""Request and response shapes. Mirrored by Zod schemas in ``ui/``.

Every field is explicit and every optional one is optional for a stated reason.
Three-valued fields say so in their description, because a UI that renders
``None`` as "no" is the bug the three values exist to prevent.
"""

from datetime import datetime

from pydantic import BaseModel, Field

from repcut.db.models import JobStatus, UploadStatus

# Amendment 004: 8MB chunks. Large enough that per-request overhead disappears
# against a multi-gigabyte transfer, small enough that a resumed upload re-sends
# little and a chunk fits comfortably under any proxy's body limit.
DEFAULT_CHUNK_SIZE_BYTES = 8 * 1024 * 1024

# `^...$`, not `\A...\Z`: pydantic compiles `pattern` with Rust's regex crate,
# which rejects `\A` outright. Rust's `$` is end-of-haystack by default - it does
# not admit a trailing newline the way Python's does - so the anchoring is exact.
SHA256_PATTERN = r"^[0-9a-f]{64}$"

# A ceiling on what one transfer may declare. `size_bytes` was bounded below
# (`ge=0`) and not above, and `_write_body` uses the declared size as its own
# write limit - so a client declaring 10TB was granted a 10TB budget and the
# engine wrote until the disk filled. Filling `$DATA_DIR` does not just fail the
# upload: it takes out the SQLite database and every render in flight beside it.
#
# 64GiB is far above any real phone clip (an hour of 4K60 HEVC is ~30GB) and far
# below a disk. The point is that a bound exists, not where exactly it sits.
MAX_UPLOAD_BYTES = 64 * 1024 * 1024 * 1024

# Same reasoning one level down. The chunk size is the client's declared
# appetite; `_write_body` flushes every megabyte regardless, so this bounds what
# the client may *claim*, keeping a nonsense value out of the database and out
# of the resume arithmetic.
MAX_CHUNK_BYTES = 256 * 1024 * 1024


class ProjectCreate(BaseModel):
    """A new gym session to edit."""

    name: str = Field(min_length=1, max_length=200)


class ProjectResponse(BaseModel):
    """A project as the library renders it."""

    id: str
    name: str
    created_at: datetime
    updated_at: datetime


class UploadCreate(BaseModel):
    """Declare a transfer. Answered with an existing session when one is open."""

    display_name: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(ge=0, le=MAX_UPLOAD_BYTES)
    chunk_size_bytes: int = Field(default=DEFAULT_CHUNK_SIZE_BYTES, gt=0, le=MAX_CHUNK_BYTES)
    sha256: str | None = Field(
        default=None,
        pattern=SHA256_PATTERN,
        description=(
            "The client's own digest, if it computed one. Verified at finalize "
            "against the digest the engine computes; also the key that lets a "
            "client which lost its upload id find its own transfer again."
        ),
    )


class UploadResponse(BaseModel):
    """Where a transfer stands. ``bytes_received`` is where to send from next."""

    id: str
    project_id: str
    display_name: str
    declared_size_bytes: int
    chunk_size_bytes: int
    bytes_received: int = Field(
        description=(
            "The authoritative resume offset: the lesser of what the database "
            "recorded and what is actually on disk."
        )
    )
    status: UploadStatus
    resumed: bool = Field(
        default=False,
        description="True when this answer reopened a transfer already in flight.",
    )


class UploadFinalizeResponse(BaseModel):
    """The result of assembling a transfer into the media library."""

    sha256: str
    media_file_id: str
    project_id: str
    job_id: str | None = Field(
        description=(
            "The ingest job, or null when this clip's artifacts already exist "
            "and nothing needs deriving."
        )
    )
    duplicate: bool = Field(
        description="True when these bytes were already in the store and were not re-written."
    )


class MediaFileResponse(BaseModel):
    """One project's clip, with everything known about the bytes behind it."""

    id: str
    project_id: str
    sha256: str
    display_name: str
    position: int
    added_at: datetime

    size_bytes: int
    container_format: str | None
    duration_seconds: float | None
    display_width: int | None
    display_height: int | None
    rotation_degrees: int | None
    fps_source: float | None
    fps_normalized: float | None
    is_variable_frame_rate: bool | None = Field(
        description=(
            "True measured variable, false measured constant, **null unknown**. "
            "Null is not 'no': some containers cannot answer the question, and "
            "treating null as constant reintroduces the drift this column exists "
            "to prevent."
        )
    )
    video_codec: str | None
    audio_codec: str | None
    audio_sample_rate: int | None

    has_proxy: bool
    has_thumbnail_strip: bool


class SceneVLMResponse(BaseModel):
    """One scene's cached Gemini tagging - the fields ``GeminiSceneCache`` stores.

    Every field is optional, mirroring ``analysis.gemini_client.GeminiSceneResult``:
    a partial answer is still a usable one, and a field the model was not
    confident about renders as null rather than failing the whole scene.
    """

    content_type: str | None
    exercise_guess: str | None
    environment: str | None
    lighting_quality: str | None
    lighting_temperature: str | None
    lighting_direction: str | None
    energy_level: str | None
    aesthetic_notes: str | None


class SceneResponse(BaseModel):
    """One detected scene, with its Gemini tagging if analysis reached it.

    ``sampled_frame_path`` is never exposed - it is a stored path, which on
    this machine carries the OS username (`.claude/rules/secrets.md`).
    ``has_sampled_frame`` is the boolean the UI needs to decide whether
    ``GET /media/{sha256}/scenes/{id}/frame`` will serve an image yet.
    """

    id: str
    sha256: str
    sequence_index: int
    start_seconds: float
    end_seconds: float
    start_frame_source: int
    end_frame_source: int
    has_sampled_frame: bool
    motion_energy: float | None
    audio_energy: float | None
    energy_score: float | None
    vlm: SceneVLMResponse | None = Field(
        description=(
            "This scene's Gemini tagging, or null when analysis has not reached "
            "it yet, degraded (offline, rate-limited, no key), or the response "
            "never parsed. The UI cannot and need not tell these apart from the "
            "field alone."
        )
    )
    created_at: datetime


class JobResponse(BaseModel):
    """A job's current state, as ``/jobs/{id}`` and ``/ws/jobs`` both report it."""

    id: str
    job_type: str
    status: JobStatus
    progress: float
    step: str | None
    error: str | None
    project_id: str | None
    sha256: str | None
    created_at: datetime
    updated_at: datetime


__all__ = [
    "DEFAULT_CHUNK_SIZE_BYTES",
    "MAX_CHUNK_BYTES",
    "MAX_UPLOAD_BYTES",
    "JobResponse",
    "MediaFileResponse",
    "ProjectCreate",
    "ProjectResponse",
    "SceneResponse",
    "SceneVLMResponse",
    "UploadCreate",
    "UploadFinalizeResponse",
    "UploadResponse",
]
