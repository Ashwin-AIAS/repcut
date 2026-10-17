"""the style profile's switches, gym set captions, and their taste kinds

``style_profile`` holds one row: whether the profile is on and when each of its
items was last reset (the profile itself is recomputed from ``taste_events``).
``gym_sets`` holds one row per counted set on a hero segment: the counter's
suggestion and the person's confirmed numbers. ``taste_events`` is rebuilt in
batch mode for two new kinds, as in 0005-0008.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-17

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | Sequence[str] | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BEFORE_VALUES = (
    "theme",
    "intensity",
    "lighting_temperature",
    "lighting_quality",
    "reset",
    "music_track",
    "music_off",
    "music_suggested",
    "target_duration",
    "cut_removed",
    "cut_locked",
    "cut_unlocked",
    "segment_forced",
    "segment_unforced",
    "regenerate",
    "scene_boundary",
    "caption_style",
    "captions_off",
    "captions_on",
    "caption_word",
    "caption_line_hidden",
    "caption_line_shown",
    "export_preset",
    "reframe_offset",
    "slowmo_on",
    "slowmo_off",
    "reference_matched",
    "reference_detached",
    "hook_on",
    "hook_off",
    "hook_choose",
)
_NEW_VALUES = ("gym_caption_confirmed", "gym_caption_dismissed")


def _kinds(values: Sequence[str]) -> str:
    return ", ".join(f"'{kind}'" for kind in values)


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "style_profile",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("resets_json", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("id = 1", name=op.f("ck_style_profile_single_row")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_style_profile")),
    )
    op.create_table(
        "gym_sets",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("segment_id", sa.String(length=80), nullable=False),
        sa.Column("start_seconds", sa.Float(), nullable=False),
        sa.Column("end_seconds", sa.Float(), nullable=False),
        sa.Column("counter_version", sa.Integer(), nullable=False),
        sa.Column("reps_suggested", sa.Integer(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("exercise_suggested", sa.String(length=60), nullable=True),
        sa.Column("reps", sa.Integer(), nullable=True),
        sa.Column("exercise", sa.String(length=60), nullable=True),
        sa.Column("weight", sa.Float(), nullable=True),
        sa.Column("weight_unit", sa.String(length=4), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('suggested', 'confirmed', 'dismissed')",
            name=op.f("ck_gym_sets_status_is_known"),
        ),
        sa.CheckConstraint(
            "weight_unit IN ('kg', 'lb')", name=op.f("ck_gym_sets_weight_unit_is_known")
        ),
        sa.CheckConstraint(
            "reps IS NULL OR (reps >= 0 AND reps <= 200)", name=op.f("ck_gym_sets_reps_in_range")
        ),
        sa.CheckConstraint(
            "weight IS NULL OR (weight >= 0 AND weight <= 1000)",
            name=op.f("ck_gym_sets_weight_in_range"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_gym_sets_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["sha256"],
            ["media_blobs.sha256"],
            name=op.f("fk_gym_sets_sha256_media_blobs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_gym_sets")),
        sa.UniqueConstraint("project_id", "segment_id", name="uq_gym_sets_one_set_per_segment"),
    )
    op.create_index(op.f("ix_gym_sets_project_id"), "gym_sets", ["project_id"], unique=False)
    with op.batch_alter_table("taste_events", schema=None, recreate="always") as batch_op:
        batch_op.drop_constraint("kind_is_a_known_override", type_="check")
        batch_op.create_check_constraint(
            "kind_is_a_known_override", f"kind IN ({_kinds((*_BEFORE_VALUES, *_NEW_VALUES))})"
        )


def downgrade() -> None:
    """Downgrade schema. Gym caption taste events cannot survive the old CHECK."""
    taste = sa.table("taste_events", sa.column("kind"))
    op.execute(sa.delete(taste).where(taste.c.kind.in_(_NEW_VALUES)))
    with op.batch_alter_table("taste_events", schema=None, recreate="always") as batch_op:
        batch_op.drop_constraint("kind_is_a_known_override", type_="check")
        batch_op.create_check_constraint(
            "kind_is_a_known_override", f"kind IN ({_kinds(_BEFORE_VALUES)})"
        )
    op.drop_index(op.f("ix_gym_sets_project_id"), table_name="gym_sets")
    op.drop_table("gym_sets")
    op.drop_table("style_profile")
