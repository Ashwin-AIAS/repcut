"""scenes and gemini scene cache

Amendment 008: two new tables for Prompt 03's analysis stage.

``scenes`` holds one row per detected shot boundary, content-addressed on
``sha256`` like ``derived_artifacts`` but not stored there - a clip has one
proxy and one thumbnail strip but *N* scenes, and ``derived_artifacts``'
unique key assumes at most one row per key. ``sampled_frame_path`` lives on
this table instead of as a fourth ``derived_artifacts`` row for the same
reason (amendment 008 resolution 2).

``gemini_scene_cache`` holds one row per scene per prompt version, written
only after a real round trip to the Gemini API succeeds or fails with a
parsed (if malformed) response - never for a request that never reached the
API. See ``repcut.db.models.GeminiSceneCache`` for the full reasoning.

Datetime columns read ``sa.DateTime(timezone=True)`` here and ``UTCDateTime``
in the models, for the same reason 0001 states: they are the same column,
and this file states the storage type while ``repcut.db.types`` states the
invariant.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-28

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "scenes",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("detector_params_version", sa.Integer(), nullable=False),
        sa.Column("sequence_index", sa.Integer(), nullable=False),
        sa.Column("start_seconds", sa.Float(), nullable=False),
        sa.Column("end_seconds", sa.Float(), nullable=False),
        sa.Column("start_frame_source", sa.Integer(), nullable=False),
        sa.Column("end_frame_source", sa.Integer(), nullable=False),
        sa.Column("sampled_frame_path", sa.String(length=512), nullable=True),
        sa.Column("motion_energy", sa.Float(), nullable=True),
        sa.Column("audio_energy", sa.Float(), nullable=True),
        sa.Column("energy_score", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "detector_params_version >= 1",
            name=op.f("ck_scenes_detector_params_version_positive"),
        ),
        sa.CheckConstraint(
            "end_seconds > start_seconds", name=op.f("ck_scenes_scene_has_positive_duration")
        ),
        sa.CheckConstraint(
            "sequence_index >= 0", name=op.f("ck_scenes_sequence_index_non_negative")
        ),
        sa.ForeignKeyConstraint(
            ["sha256"],
            ["media_blobs.sha256"],
            name=op.f("fk_scenes_sha256_media_blobs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scenes")),
        sa.UniqueConstraint(
            "sha256", "detector_params_version", "sequence_index", name="uq_scenes_key"
        ),
    )
    with op.batch_alter_table("scenes", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_scenes_sha256"), ["sha256"], unique=False)

    op.create_table(
        "gemini_scene_cache",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("scene_id", sa.String(length=36), nullable=False),
        sa.Column("gemini_prompt_version", sa.Integer(), nullable=False),
        sa.Column("content_type", sa.String(length=64), nullable=True),
        sa.Column("exercise_guess", sa.String(length=120), nullable=True),
        sa.Column("environment", sa.String(length=64), nullable=True),
        sa.Column("lighting_quality", sa.String(length=32), nullable=True),
        sa.Column("lighting_temperature", sa.String(length=32), nullable=True),
        sa.Column("lighting_direction", sa.String(length=32), nullable=True),
        sa.Column("energy_level", sa.String(length=16), nullable=True),
        sa.Column("aesthetic_notes", sa.Text(), nullable=True),
        sa.Column("raw_response_json", sa.Text(), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "gemini_prompt_version >= 1",
            name=op.f("ck_gemini_scene_cache_gemini_prompt_version_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["scene_id"],
            ["scenes.id"],
            name=op.f("fk_gemini_scene_cache_scene_id_scenes"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_gemini_scene_cache")),
        sa.UniqueConstraint("scene_id", "gemini_prompt_version", name="uq_gemini_scene_cache_key"),
    )
    with op.batch_alter_table("gemini_scene_cache", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_gemini_scene_cache_scene_id"), ["scene_id"], unique=False
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("gemini_scene_cache", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_gemini_scene_cache_scene_id"))

    op.drop_table("gemini_scene_cache")
    with op.batch_alter_table("scenes", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_scenes_sha256"))

    op.drop_table("scenes")
