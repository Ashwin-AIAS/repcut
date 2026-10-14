"""caption settings, export settings, and their overrides as taste events

Prompt 06. ``caption_settings`` holds a project's caption style, on/off and the
person's word and line edits; ``export_settings`` the preset and the per-scene
reframe offsets. ``taste_events`` learns the caption and export override kinds;
SQLite cannot alter a CHECK, so the table is rebuilt in batch mode.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-13

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
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
)
_NEW_VALUES = (
    "caption_style",
    "captions_off",
    "captions_on",
    "caption_word",
    "caption_line_hidden",
    "caption_line_shown",
    "export_preset",
    "reframe_offset",
)


def _kinds(values: Sequence[str]) -> str:
    return ", ".join(f"'{kind}'" for kind in values)


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "caption_settings",
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("style_id", sa.String(length=40), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("edits_json", sa.Text(), nullable=False),
        sa.Column("hidden_json", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_caption_settings_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("project_id", name=op.f("pk_caption_settings")),
    )
    op.create_table(
        "export_settings",
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("preset_id", sa.String(length=40), nullable=False),
        sa.Column("offsets_json", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_export_settings_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("project_id", name=op.f("pk_export_settings")),
    )
    with op.batch_alter_table("taste_events", schema=None, recreate="always") as batch_op:
        batch_op.drop_constraint("kind_is_a_known_override", type_="check")
        batch_op.create_check_constraint(
            "kind_is_a_known_override", f"kind IN ({_kinds((*_BEFORE_VALUES, *_NEW_VALUES))})"
        )


def downgrade() -> None:
    """Downgrade schema. Caption and export taste events cannot survive the old CHECK."""
    taste = sa.table("taste_events", sa.column("kind"))
    op.execute(sa.delete(taste).where(taste.c.kind.in_(_NEW_VALUES)))
    with op.batch_alter_table("taste_events", schema=None, recreate="always") as batch_op:
        batch_op.drop_constraint("kind_is_a_known_override", type_="check")
        batch_op.create_check_constraint(
            "kind_is_a_known_override", f"kind IN ({_kinds(_BEFORE_VALUES)})"
        )
    op.drop_table("export_settings")
    op.drop_table("caption_settings")
