"""The six tables of amendment 004, and the grading tables of Prompt 04.

The split that matters: ``media_blobs`` holds everything true of the *bytes*,
``media_files`` holds one project's *reference* to them. A new media property
belongs in blobs if it describes the bytes and in files if it describes one
project's use of them. Two references to one clip can then never disagree about
its frame rate.

No ORM relationships are declared. Under asyncio a lazy load raises at attribute
access rather than at query time, which turns a missing ``selectinload`` into a
runtime error far from its cause; joins are written explicitly where they are
needed instead of being implicitly available everywhere.
"""

from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from repcut.db.base import Base
from repcut.db.types import UTCDateTime

# Length of a hex-encoded SHA-256 digest.
SHA256_LENGTH = 64

# Long enough for the deepest content-addressed path plus a filename, short
# enough that a runaway value is caught rather than stored.
STORED_PATH_LENGTH = 512


def new_id() -> str:
    """Primary key for rows that are not content-addressed."""
    return str(uuid4())


def utcnow() -> datetime:
    """Timezone-aware now. The only clock any column default may read.

    Every datetime column is a ``UTCDateTime``, which rejects a naive value on
    write and re-attaches UTC on read - see ``repcut.db.types``.
    """
    return datetime.now(UTC)


class JobStatus(StrEnum):
    """The job lifecycle. A closed set - the UI renders every member."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class UploadStatus(StrEnum):
    """Lifecycle of one chunked transfer."""

    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    ABORTED = "aborted"


class JobType(StrEnum):
    """Job types Prompt 02 produces. Every later prompt adds more."""

    INGEST = "ingest"
    ANALYSIS = "analysis"
    GRADE_PREVIEW = "grade_preview"
    MUSIC_SCAN = "music_scan"
    EDIT_SIGNALS = "edit_signals"
    TRANSCRIBE = "transcribe"
    EXPORT = "export"


def _db_enum(enum_type: type[StrEnum], name: str) -> Enum:
    """A VARCHAR column storing the member *value*, not its name.

    ``native_enum=False`` keeps it portable; ``values_callable`` stores
    ``"queued"`` rather than ``"QUEUED"`` so the database reads in the same
    vocabulary as the API. The value check is declared separately by
    ``_one_of`` - see there for why.
    """
    return Enum(
        enum_type,
        name=name,
        native_enum=False,
        length=32,
        validate_strings=True,
        values_callable=lambda members: [member.value for member in members],
    )


def _one_of(column: str, enum_type: type[StrEnum], name: str) -> CheckConstraint:
    """CHECK that ``column`` holds one of ``enum_type``'s values.

    The SQL is generated from the members, so the constraint cannot drift from
    the Python type. It is declared here rather than through
    ``Enum(create_constraint=True)`` because a type-bound constraint is invisible
    to Alembic's autogenerate on the model side while reflection still finds it
    in the database - which makes every future migration look like it has
    pending changes, and trains everyone to ignore the one that matters.

    Only for **closed** sets. A CHECK over an open set costs a table rewrite per
    addition on SQLite, so ``job_type`` and ``artifact_kind`` - which every later
    prompt extends - are plain strings validated in Python instead.
    """
    values = ", ".join(f"'{member.value}'" for member in enum_type)
    return CheckConstraint(f"{column} IN ({values})", name=name)


class Project(Base):
    """A gym session being edited."""

    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class MediaBlob(Base):
    """One row per distinct byte sequence, keyed by content.

    Every column describes the bytes themselves. ``stored_path`` is relative to
    ``$DATA_DIR`` and derived from the hash, never from the user's filename -
    an absolute path here would carry the OS username into the database
    (secrets.md).

    The media properties are null until the ingest job succeeds; a blob exists
    from the moment its bytes land, and ffprobe runs afterwards.
    """

    __tablename__ = "media_blobs"

    sha256: Mapped[str] = mapped_column(String(SHA256_LENGTH), primary_key=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    stored_path: Mapped[str] = mapped_column(String(STORED_PATH_LENGTH))
    container_format: Mapped[str | None] = mapped_column(String(64), default=None)

    duration_seconds: Mapped[float | None] = mapped_column(Float, default=None)
    # Display dimensions: raw pixel dimensions plus a rotation tag is the phone
    # footage trap ffmpeg.md names, so what is stored is what a player shows.
    display_width: Mapped[int | None] = mapped_column(Integer, default=None)
    display_height: Mapped[int | None] = mapped_column(Integer, default=None)
    rotation_degrees: Mapped[int | None] = mapped_column(Integer, default=None)
    # Both frame rates, always: beat sync and interpolation drift silently when
    # the source was VFR and only the nominal rate was recorded.
    fps_source: Mapped[float | None] = mapped_column(Float, default=None)
    fps_normalized: Mapped[float | None] = mapped_column(Float, default=None)
    is_variable_frame_rate: Mapped[bool | None] = mapped_column(default=None)
    video_codec: Mapped[str | None] = mapped_column(String(32), default=None)
    audio_codec: Mapped[str | None] = mapped_column(String(32), default=None)
    audio_sample_rate: Mapped[int | None] = mapped_column(Integer, default=None)

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    __table_args__ = (
        CheckConstraint("size_bytes >= 0", name="size_bytes_non_negative"),
        CheckConstraint(f"length(sha256) = {SHA256_LENGTH}", name="sha256_is_a_digest"),
    )


class MediaFile(Base):
    """One project's reference to a blob.

    ``display_name`` is the user's filename. It is shown in the library and
    never used to build a path.

    The two delete rules are the refcount, enforced by the database instead of
    by a counter that can drift: deleting a project cascades its references
    away, and a blob cannot be deleted while any reference survives. Orphan
    collection is deferred - see amendment 004.
    """

    __tablename__ = "media_files"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    sha256: Mapped[str] = mapped_column(
        ForeignKey("media_blobs.sha256", ondelete="RESTRICT"), index=True
    )
    display_name: Mapped[str] = mapped_column(String(255))
    position: Mapped[int] = mapped_column(Integer, default=0)
    added_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    __table_args__ = (
        UniqueConstraint("project_id", "sha256", name="uq_media_files_project_sha256"),
    )


class DerivedArtifact(Base):
    """A proxy, thumbnail strip or later render, content-addressed like its source.

    The unique key is the whole point: a changed recipe bumps ``params_version``
    and therefore lands on a different row, so a superseded artifact becomes
    unreferenced rather than a stale file at a path that still looks current.
    """

    __tablename__ = "derived_artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sha256: Mapped[str] = mapped_column(
        ForeignKey("media_blobs.sha256", ondelete="CASCADE"), index=True
    )
    # One of ArtifactKind. An open set - Prompt 04 adds graded proxies, Prompt
    # 06 adds exports - so it is validated in Python, not by a CHECK.
    artifact_kind: Mapped[str] = mapped_column(String(32))
    params_version: Mapped[int] = mapped_column(Integer)
    stored_path: Mapped[str] = mapped_column(String(STORED_PATH_LENGTH))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, default=None)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    __table_args__ = (
        UniqueConstraint(
            "sha256", "artifact_kind", "params_version", name="uq_derived_artifacts_key"
        ),
        CheckConstraint("params_version >= 1", name="params_version_positive"),
    )


class Scene(Base):
    """One detected shot boundary within a clip, content-addressed like its source.

    A sibling of ``derived_artifacts`` in spirit but not stored there
    (amendment 008 resolution 2): a clip has exactly one of each
    ``ArtifactKind`` value, but *N* scenes, and ``derived_artifacts``'
    ``uq_derived_artifacts_key`` assumes its 3-column key resolves to at most
    one row - ``ingest.py``'s ``_existing_artifact``/``_record_artifact``
    already depend on that. A ``Scene`` row is its own discriminator instead:
    unique per ``(sha256, detector_params_version, sequence_index)``. Scenes
    are keyed on the blob, not on a project's reference to it, so a clip
    re-added to a second project reuses the scenes already detected for it,
    exactly as it already reuses its proxy.

    Boundaries are stored twice, deliberately (amendment 008 resolution 4).
    ``start_seconds``/``end_seconds`` are against the SOURCE's timebase and are
    authoritative: detection may read the CFR proxy while the source itself is
    VFR (ffmpeg.md's VFR warning), so a bare frame count does not say which
    file's frame-count it counts against, and the two disagree for the same
    wall-clock span. ``start_frame_source``/``end_frame_source`` are the
    source's own frame index, derived from the seconds value, for callers that
    need a frame handle rather than a timestamp.

    ``sampled_frame_path`` is null until the sampler runs (a later piece of
    this prompt); it is read from the source file, never the proxy (amendment
    008 resolution 3), and stored under
    ``media/derived/<sha[:2]>/<sha>/sampled_frame/<params_version>/scene_d<detector_params_version>_<sequence_index>.jpg``
    via the same content-addressed helpers in ``media/store.py`` every other
    derived path uses - without adding a row to ``derived_artifacts``.
    """

    __tablename__ = "scenes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sha256: Mapped[str] = mapped_column(
        ForeignKey("media_blobs.sha256", ondelete="CASCADE"), index=True
    )
    detector_params_version: Mapped[int] = mapped_column(Integer)
    sequence_index: Mapped[int] = mapped_column(Integer)
    start_seconds: Mapped[float] = mapped_column(Float)
    end_seconds: Mapped[float] = mapped_column(Float)
    start_frame_source: Mapped[int] = mapped_column(Integer)
    end_frame_source: Mapped[int] = mapped_column(Integer)
    sampled_frame_path: Mapped[str | None] = mapped_column(String(STORED_PATH_LENGTH), default=None)
    motion_energy: Mapped[float | None] = mapped_column(Float, default=None)
    audio_energy: Mapped[float | None] = mapped_column(Float, default=None)
    # 0-100: motion and audio energy combined into one comparable score.
    energy_score: Mapped[float | None] = mapped_column(Float, default=None)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    __table_args__ = (
        UniqueConstraint(
            "sha256", "detector_params_version", "sequence_index", name="uq_scenes_key"
        ),
        CheckConstraint("end_seconds > start_seconds", name="scene_has_positive_duration"),
        CheckConstraint("sequence_index >= 0", name="sequence_index_non_negative"),
        CheckConstraint("detector_params_version >= 1", name="detector_params_version_positive"),
    )


class GeminiSceneCache(Base):
    """A cached Gemini scene-tagging response, keyed by scene and prompt version.

    This is ``gemini-usage.md``'s cache key, ``(video_hash, scene_id,
    prompt_version)``, with ``video_hash`` folded into ``scene_id``: a
    ``Scene`` row is already unique per ``(sha256, detector_params_version,
    sequence_index)``, so the hash a scene was detected from is implied by
    which scene is being looked up, and does not need to be stored again here.

    **A row is written only after a real round trip to the API**: a parsed
    success, or a malformed-JSON-after-retry result recorded with the
    structured fields left null. A row is **never** written for a request that
    did not reach the API at all - offline, or refused by the client-side rate
    limiter. Those are not a cache miss with an empty answer; they are the
    absence of an attempt, and whatever later prompt writes the caching logic
    must retry them rather than let this table's presence be read as "already
    asked."
    """

    __tablename__ = "gemini_scene_cache"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    scene_id: Mapped[str] = mapped_column(ForeignKey("scenes.id", ondelete="CASCADE"), index=True)
    gemini_prompt_version: Mapped[int] = mapped_column(Integer)

    content_type: Mapped[str | None] = mapped_column(String(64), default=None)
    exercise_guess: Mapped[str | None] = mapped_column(String(120), default=None)
    environment: Mapped[str | None] = mapped_column(String(64), default=None)

    lighting_quality: Mapped[str | None] = mapped_column(String(32), default=None)
    lighting_temperature: Mapped[str | None] = mapped_column(String(32), default=None)
    lighting_direction: Mapped[str | None] = mapped_column(String(32), default=None)

    # "low" | "med" | "high" - a closed, small set, but constrained by the
    # Pydantic schema the Gemini response is validated against before this row
    # is ever written, not by a database CHECK: this table has no sibling
    # enum-backed column to match, and one CHECK for one field would be more
    # ceremony than the value it adds over the validation already done above
    # it.
    energy_level: Mapped[str | None] = mapped_column(String(16), default=None)

    aesthetic_notes: Mapped[str | None] = mapped_column(Text, default=None)
    # The response body, never the request - so never the API key.
    raw_response_json: Mapped[str | None] = mapped_column(Text, default=None)

    retrieved_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    __table_args__ = (
        UniqueConstraint("scene_id", "gemini_prompt_version", name="uq_gemini_scene_cache_key"),
        CheckConstraint("gemini_prompt_version >= 1", name="gemini_prompt_version_positive"),
    )


class UploadSession(Base):
    """Durable state for one chunked transfer.

    This table is what makes resume idempotent rather than best-effort. After a
    kill the engine knows the target, the declared size, the chunk size and how
    many bytes it committed; the authoritative offset is the *lesser* of
    ``bytes_received`` and the actual size of the part file, because either can
    be ahead of the other depending on where the kill landed.

    Durable state is only half of resume: the caller also has to be able to
    *find* the row. Keying resume on the session id alone works for a client
    that never forgets it - which is what the gate's test client is, holding the
    id in memory across the kill - and fails for a browser tab refreshed
    mid-upload, which retries as a new session and leaves the first ``.part``
    on disk with nothing referencing it. ``uq_upload_sessions_in_progress``
    below is the lookup path that makes the second case resolvable.
    """

    __tablename__ = "upload_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    display_name: Mapped[str] = mapped_column(String(255))
    declared_size_bytes: Mapped[int] = mapped_column(BigInteger)
    chunk_size_bytes: Mapped[int] = mapped_column(Integer)
    bytes_received: Mapped[int] = mapped_column(BigInteger, default=0)
    # Supplied by the client when it knows the hash up front; verified at
    # finalize against the digest the engine computes itself.
    declared_sha256: Mapped[str | None] = mapped_column(String(SHA256_LENGTH), default=None)
    part_path: Mapped[str] = mapped_column(String(STORED_PATH_LENGTH))
    status: Mapped[UploadStatus] = mapped_column(
        _db_enum(UploadStatus, "upload_status"), default=UploadStatus.IN_PROGRESS
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        CheckConstraint("bytes_received >= 0", name="bytes_received_non_negative"),
        CheckConstraint("declared_size_bytes >= 0", name="declared_size_non_negative"),
        CheckConstraint("chunk_size_bytes > 0", name="chunk_size_positive"),
        _one_of("status", UploadStatus, "status_is_a_known_state"),
        # Resume without the session id: a client that lost it - a refreshed
        # browser tab - re-declares (project, hash) and finds its own transfer
        # instead of starting a second one.
        #
        # Partial, on two counts. It covers only ``in_progress``, so the history
        # of completed and aborted transfers for the same clip is unconstrained
        # - re-uploading a clip that finished is a legitimate act, and a full
        # unique index would forbid it. And SQLite treats NULLs as distinct, so
        # sessions whose client declared no hash simply do not participate:
        # nothing identifies them, so nothing may claim they collide.
        Index(
            "uq_upload_sessions_in_progress",
            "project_id",
            "declared_sha256",
            unique=True,
            sqlite_where=text("status = 'in_progress'"),
        ),
    )


class Job(Base):
    """A background job and its progress.

    ``error`` holds a human-readable cause - a parsed FFmpeg failure class or a
    stderr tail - never a traceback, because it is rendered in the UI.
    """

    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_type: Mapped[str] = mapped_column(String(32))
    status: Mapped[JobStatus] = mapped_column(
        _db_enum(JobStatus, "job_status"), default=JobStatus.QUEUED, index=True
    )
    # 0.0 to 1.0. The API renders it as a percentage; storing the fraction keeps
    # rounding a presentation concern.
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    step: Mapped[str | None] = mapped_column(String(120), default=None)
    error: Mapped[str | None] = mapped_column(Text, default=None)

    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True, default=None
    )
    sha256: Mapped[str | None] = mapped_column(
        ForeignKey("media_blobs.sha256", ondelete="CASCADE"), index=True, default=None
    )

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)

    __table_args__ = (
        CheckConstraint("progress >= 0.0 AND progress <= 1.0", name="progress_is_a_fraction"),
        _one_of("status", JobStatus, "status_is_a_known_state"),
    )


class TasteEventKind(StrEnum):
    """What a person changed. Closed: Prompt 10 reads every kind by name."""

    THEME = "theme"
    INTENSITY = "intensity"
    LIGHTING_TEMPERATURE = "lighting_temperature"
    LIGHTING_QUALITY = "lighting_quality"
    RESET = "reset"
    # The edit (Prompt 05): one per override in ``edit.resync``.
    MUSIC_TRACK = "music_track"
    MUSIC_OFF = "music_off"
    MUSIC_SUGGESTED = "music_suggested"
    TARGET_DURATION = "target_duration"
    CUT_REMOVED = "cut_removed"
    CUT_LOCKED = "cut_locked"
    CUT_UNLOCKED = "cut_unlocked"
    SEGMENT_FORCED = "segment_forced"
    SEGMENT_UNFORCED = "segment_unforced"
    REGENERATE = "regenerate"
    SCENE_BOUNDARY = "scene_boundary"
    # Captions and the export (Prompt 06).
    CAPTION_STYLE = "caption_style"
    CAPTIONS_OFF = "captions_off"
    CAPTIONS_ON = "captions_on"
    CAPTION_WORD = "caption_word"
    CAPTION_LINE_HIDDEN = "caption_line_hidden"
    CAPTION_LINE_SHOWN = "caption_line_shown"
    EXPORT_PRESET = "export_preset"
    REFRAME_OFFSET = "reframe_offset"
    # Slow motion (Prompt 07): a hero shot's slow motion turned off, or asked for.
    SLOWMO_ON = "slowmo_on"
    SLOWMO_OFF = "slowmo_off"


class ChoiceScope(StrEnum):
    CLIP = "clip"
    SCENE = "scene"
    EDIT = "edit"


class MusicTrackStatus(StrEnum):
    """Where a library track is. Closed: the UI renders every member."""

    PENDING = "pending"
    ANALYSED = "analysed"
    REJECTED = "rejected"
    FAILED = "failed"


class GradeChoice(Base):
    """A person's standing grading choices for one clip in one project (P2).

    One clip-wide row (``scene_id`` null) and at most one row per scene. A null
    field means "follow the suggestion": ``theme_id`` null follows the
    recommended theme, ``intensity`` null the theme's default, a lighting
    override null the analysed tag. Reset is deleting the row's choice, never
    writing the suggestion back as if it were chosen - so a later, better
    suggestion still reaches a scene the person never touched.
    """

    __tablename__ = "grade_choices"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    sha256: Mapped[str] = mapped_column(
        ForeignKey("media_blobs.sha256", ondelete="CASCADE"), index=True
    )
    scene_id: Mapped[str | None] = mapped_column(
        ForeignKey("scenes.id", ondelete="CASCADE"), default=None
    )
    pack_id: Mapped[str] = mapped_column(String(40))
    theme_id: Mapped[str | None] = mapped_column(String(40), default=None)
    intensity: Mapped[int | None] = mapped_column(Integer, default=None)
    lighting_temperature: Mapped[str | None] = mapped_column(String(32), default=None)
    lighting_quality: Mapped[str | None] = mapped_column(String(32), default=None)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        CheckConstraint(
            "intensity IS NULL OR (intensity >= 0 AND intensity <= 100)",
            name="intensity_is_a_percentage",
        ),
        # SQLite treats NULLs as distinct, so one partial index per scope.
        Index(
            "uq_grade_choices_clip",
            "project_id",
            "sha256",
            unique=True,
            sqlite_where=text("scene_id IS NULL"),
        ),
        Index(
            "uq_grade_choices_scene",
            "project_id",
            "scene_id",
            unique=True,
            sqlite_where=text("scene_id IS NOT NULL"),
        ),
    )


class TasteEvent(Base):
    """One override, as a taste signal (P3). Written once, never updated.

    Designed for its reader, Prompt 10's style profile: every row says what was
    suggested, what the person chose instead, and what the scene looked like at
    that moment, as JSON with stable keys (``schema_version`` names the shape).

    - ``recommended_json`` / ``chosen_json``: ``{"value": ...}`` plus, for a
      theme, the suggestion's scores; for a reset, what was cleared.
    - ``features_json``: the analysed lighting tags, the correction the
      corrector computed, the scene's energy and duration, and the theme and
      intensity in force before the change.

    No footage, no path, no frame: features are numbers and tags only (P4).
    ``project_id`` survives a deleted scene (``SET NULL``): a taste signal does
    not stop being one because the clip it came from was removed.
    """

    __tablename__ = "taste_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    scope: Mapped[str] = mapped_column(String(8))
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), index=True, default=None
    )
    sha256: Mapped[str | None] = mapped_column(
        ForeignKey("media_blobs.sha256", ondelete="SET NULL"), default=None
    )
    scene_id: Mapped[str | None] = mapped_column(
        ForeignKey("scenes.id", ondelete="SET NULL"), default=None
    )
    # Null for an edit override: a pack is a grading idea (Prompt 05, migration 0004).
    pack_id: Mapped[str | None] = mapped_column(String(40), default=None)
    recommended_json: Mapped[str] = mapped_column(Text)
    chosen_json: Mapped[str] = mapped_column(Text)
    features_json: Mapped[str] = mapped_column(Text)

    __table_args__ = (
        _one_of("kind", TasteEventKind, "kind_is_a_known_override"),
        _one_of("scope", ChoiceScope, "scope_is_clip_or_scene"),
        CheckConstraint("schema_version >= 1", name="schema_version_positive"),
    )


class MusicTrack(Base):
    """One track in ``$DATA_DIR/music/``, keyed by its bytes (Prompt 05).

    ``display_name`` is the file's name, for the picker only: no path is ever
    built from it (``music/library.py`` re-finds the file by listing the folder
    and matching the digest). The analysis is stored whole, as JSON, under the
    analysis version it was made with; a version bump re-analyses on the next
    scan, it does not reinterpret.
    """

    __tablename__ = "music_tracks"

    sha256: Mapped[str] = mapped_column(String(SHA256_LENGTH), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(255))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[MusicTrackStatus] = mapped_column(
        _db_enum(MusicTrackStatus, "music_track_status"), default=MusicTrackStatus.PENDING
    )
    # A fixed sentence for a rejected or failed track; never a path or stderr.
    reason: Mapped[str | None] = mapped_column(Text, default=None)
    present: Mapped[bool] = mapped_column(default=True)
    analysis_version: Mapped[int | None] = mapped_column(Integer, default=None)
    bpm: Mapped[float | None] = mapped_column(Float, default=None)
    duration_seconds: Mapped[float | None] = mapped_column(Float, default=None)
    analysis_json: Mapped[str | None] = mapped_column(Text, default=None)
    discovered_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    analysed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)

    __table_args__ = (
        _one_of("status", MusicTrackStatus, "status_is_a_known_state"),
        CheckConstraint("size_bytes >= 0", name="size_bytes_non_negative"),
        CheckConstraint(f"length(sha256) = {SHA256_LENGTH}", name="sha256_is_a_digest"),
    )


class EditPlan(Base):
    """A project's edit: the person's decisions, and the plan made from them (P2).

    ``decisions_json`` is the record; ``plan_json`` is derived and can always be
    rebuilt from it. One edit per project in v1.
    """

    __tablename__ = "edit_plans"

    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    decisions_json: Mapped[str] = mapped_column(Text)
    plan_json: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class CaptionSettings(Base):
    """A project's captions: the style, on/off, and every word or line the person changed.

    The transcript itself is a cache per clip (``captions/service``); this row is
    the record of decisions (P2), and survives re-transcription because edits
    are keyed by the recogniser's segment and word index, not by text.
    """

    __tablename__ = "caption_settings"

    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    style_id: Mapped[str] = mapped_column(String(40))
    enabled: Mapped[bool] = mapped_column(default=True)
    edits_json: Mapped[str] = mapped_column(Text, default="{}")
    hidden_json: Mapped[str] = mapped_column(Text, default="[]")
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class ExportSettings(Base):
    """A project's export choices: the preset, and the per-scene reframe offsets."""

    __tablename__ = "export_settings"

    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    preset_id: Mapped[str] = mapped_column(String(40))
    offsets_json: Mapped[str] = mapped_column(Text, default="{}")
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
