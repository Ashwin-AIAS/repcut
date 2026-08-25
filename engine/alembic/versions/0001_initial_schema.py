"""initial schema

The six tables of amendment 004: content-addressed ``media_blobs`` and their
``derived_artifacts``, per-project ``media_files`` references, ``projects``,
``upload_sessions`` and ``jobs``.

Datetime columns read ``sa.DateTime(timezone=True)`` here and ``UTCDateTime`` in
the models. They are the same column: ``UTCDateTime`` is a ``TypeDecorator``
over exactly this type, and everything it adds - rejecting a naive write,
re-attaching UTC on read - happens in Python, above the DDL. Migrations
deliberately do not import the application's types, so this file states the
storage type and ``repcut.db.types`` states the invariant.

Revision ID: 0001
Revises:
Create Date: 2026-08-05

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "media_blobs",
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("stored_path", sa.String(length=512), nullable=False),
        sa.Column("container_format", sa.String(length=64), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("display_width", sa.Integer(), nullable=True),
        sa.Column("display_height", sa.Integer(), nullable=True),
        sa.Column("rotation_degrees", sa.Integer(), nullable=True),
        sa.Column("fps_source", sa.Float(), nullable=True),
        sa.Column("fps_normalized", sa.Float(), nullable=True),
        sa.Column("is_variable_frame_rate", sa.Boolean(), nullable=True),
        sa.Column("video_codec", sa.String(length=32), nullable=True),
        sa.Column("audio_codec", sa.String(length=32), nullable=True),
        sa.Column("audio_sample_rate", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(sha256) = 64", name=op.f("ck_media_blobs_sha256_is_a_digest")),
        sa.CheckConstraint("size_bytes >= 0", name=op.f("ck_media_blobs_size_bytes_non_negative")),
        sa.PrimaryKeyConstraint("sha256", name=op.f("pk_media_blobs")),
    )
    op.create_table(
        "projects",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_projects")),
    )
    op.create_table(
        "derived_artifacts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("artifact_kind", sa.String(length=32), nullable=False),
        sa.Column("params_version", sa.Integer(), nullable=False),
        sa.Column("stored_path", sa.String(length=512), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "params_version >= 1", name=op.f("ck_derived_artifacts_params_version_positive")
        ),
        sa.ForeignKeyConstraint(
            ["sha256"],
            ["media_blobs.sha256"],
            name=op.f("fk_derived_artifacts_sha256_media_blobs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_derived_artifacts")),
        sa.UniqueConstraint(
            "sha256", "artifact_kind", "params_version", name="uq_derived_artifacts_key"
        ),
    )
    with op.batch_alter_table("derived_artifacts", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_derived_artifacts_sha256"), ["sha256"], unique=False)

    op.create_table(
        "jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("job_type", sa.String(length=32), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "queued",
                "running",
                "succeeded",
                "failed",
                "cancelled",
                name="job_status",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("progress", sa.Float(), nullable=False),
        sa.Column("step", sa.String(length=120), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("project_id", sa.String(length=36), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name=op.f("ck_jobs_status_is_a_known_state"),
        ),
        sa.CheckConstraint(
            "progress >= 0.0 AND progress <= 1.0", name=op.f("ck_jobs_progress_is_a_fraction")
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_jobs_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["sha256"],
            ["media_blobs.sha256"],
            name=op.f("fk_jobs_sha256_media_blobs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_jobs")),
    )
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_jobs_project_id"), ["project_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_jobs_sha256"), ["sha256"], unique=False)
        batch_op.create_index(batch_op.f("ix_jobs_status"), ["status"], unique=False)

    op.create_table(
        "media_files",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_media_files_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["sha256"],
            ["media_blobs.sha256"],
            name=op.f("fk_media_files_sha256_media_blobs"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_media_files")),
        sa.UniqueConstraint("project_id", "sha256", name="uq_media_files_project_sha256"),
    )
    with op.batch_alter_table("media_files", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_media_files_project_id"), ["project_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_media_files_sha256"), ["sha256"], unique=False)

    op.create_table(
        "upload_sessions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("declared_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("chunk_size_bytes", sa.Integer(), nullable=False),
        sa.Column("bytes_received", sa.BigInteger(), nullable=False),
        sa.Column("declared_sha256", sa.String(length=64), nullable=True),
        sa.Column("part_path", sa.String(length=512), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "in_progress",
                "completed",
                "aborted",
                name="upload_status",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('in_progress', 'completed', 'aborted')",
            name=op.f("ck_upload_sessions_status_is_a_known_state"),
        ),
        sa.CheckConstraint(
            "bytes_received >= 0", name=op.f("ck_upload_sessions_bytes_received_non_negative")
        ),
        sa.CheckConstraint(
            "chunk_size_bytes > 0", name=op.f("ck_upload_sessions_chunk_size_positive")
        ),
        sa.CheckConstraint(
            "declared_size_bytes >= 0", name=op.f("ck_upload_sessions_declared_size_non_negative")
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_upload_sessions_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_upload_sessions")),
    )
    with op.batch_alter_table("upload_sessions", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_upload_sessions_project_id"), ["project_id"], unique=False
        )
        # Partial: only in-progress sessions. It is the resume-lookup path for a
        # client that lost its session id, and it makes a second concurrent
        # transfer of the same clip into the same project impossible rather than
        # merely unlikely. See UploadSession for why it is scoped this way.
        batch_op.create_index(
            "uq_upload_sessions_in_progress",
            ["project_id", "declared_sha256"],
            unique=True,
            sqlite_where=sa.text("status = 'in_progress'"),
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("upload_sessions", schema=None) as batch_op:
        batch_op.drop_index("uq_upload_sessions_in_progress")
        batch_op.drop_index(batch_op.f("ix_upload_sessions_project_id"))

    op.drop_table("upload_sessions")
    with op.batch_alter_table("media_files", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_media_files_sha256"))
        batch_op.drop_index(batch_op.f("ix_media_files_project_id"))

    op.drop_table("media_files")
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_jobs_status"))
        batch_op.drop_index(batch_op.f("ix_jobs_sha256"))
        batch_op.drop_index(batch_op.f("ix_jobs_project_id"))

    op.drop_table("jobs")
    with op.batch_alter_table("derived_artifacts", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_derived_artifacts_sha256"))

    op.drop_table("derived_artifacts")
    op.drop_table("projects")
    op.drop_table("media_blobs")
