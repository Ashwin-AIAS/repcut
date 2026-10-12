"""music tracks, edit plans, and edit overrides as taste events

Prompt 05. ``music_tracks`` is the analysed library, keyed by content.
``edit_plans`` holds one project's edit decisions and the plan made from them.
``taste_events`` learns the edit's override kinds and an ``edit`` scope, and its
``pack_id`` becomes nullable: a pack is a grading idea, an edit override has
none. SQLite cannot alter a CHECK, so the table is rebuilt in batch mode.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-12

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_GRADING_KINDS = "'theme', 'intensity', 'lighting_temperature', 'lighting_quality', 'reset'"
_EDIT_KINDS = (
    "'music_track', 'music_off', 'music_suggested', 'target_duration', 'cut_removed', "
    "'cut_locked', 'cut_unlocked', 'segment_forced', 'segment_unforced', 'regenerate', "
    "'scene_boundary'"
)


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "music_tracks",
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending", "analysed", "rejected", "failed",
                name="music_track_status", native_enum=False, length=32,
            ),
            nullable=False,
        ),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("present", sa.Boolean(), nullable=False),
        sa.Column("analysis_version", sa.Integer(), nullable=True),
        sa.Column("bpm", sa.Float(), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("analysis_json", sa.Text(), nullable=True),
        sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("analysed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'analysed', 'rejected', 'failed')",
            name=op.f("ck_music_tracks_status_is_a_known_state"),
        ),
        sa.CheckConstraint("size_bytes >= 0", name=op.f("ck_music_tracks_size_bytes_non_negative")),
        sa.CheckConstraint(
            "length(sha256) = 64", name=op.f("ck_music_tracks_sha256_is_a_digest")
        ),
        sa.PrimaryKeyConstraint("sha256", name=op.f("pk_music_tracks")),
    )  # fmt: skip
    op.create_table(
        "edit_plans",
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("decisions_json", sa.Text(), nullable=False),
        sa.Column("plan_json", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_edit_plans_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("project_id", name=op.f("pk_edit_plans")),
    )
    with op.batch_alter_table("taste_events", schema=None, recreate="always") as batch_op:
        batch_op.drop_constraint("kind_is_a_known_override", type_="check")
        batch_op.drop_constraint("scope_is_clip_or_scene", type_="check")
        batch_op.create_check_constraint(
            "kind_is_a_known_override", f"kind IN ({_GRADING_KINDS}, {_EDIT_KINDS})"
        )
        batch_op.create_check_constraint(
            "scope_is_clip_or_scene", "scope IN ('clip', 'scene', 'edit')"
        )
        batch_op.alter_column("pack_id", existing_type=sa.String(length=40), nullable=True)


def downgrade() -> None:
    """Downgrade schema. Edit-kind taste events cannot survive the old CHECK."""
    op.execute(f"DELETE FROM taste_events WHERE kind IN ({_EDIT_KINDS}) OR pack_id IS NULL")
    with op.batch_alter_table("taste_events", schema=None, recreate="always") as batch_op:
        batch_op.drop_constraint("kind_is_a_known_override", type_="check")
        batch_op.drop_constraint("scope_is_clip_or_scene", type_="check")
        batch_op.create_check_constraint("kind_is_a_known_override", f"kind IN ({_GRADING_KINDS})")
        batch_op.create_check_constraint("scope_is_clip_or_scene", "scope IN ('clip', 'scene')")
        batch_op.alter_column("pack_id", existing_type=sa.String(length=40), nullable=False)
    op.drop_table("edit_plans")
    op.drop_table("music_tracks")
