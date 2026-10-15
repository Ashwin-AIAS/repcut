"""reference clips: a media role, measured profiles, per-project matches

Prompt 08. ``media_files.role`` tells footage (cut into the edit) from a
reference (analysed, never cut in); every existing row is footage.
``reference_profiles`` caches what a reference measured as, per blob;
``reference_matches`` records which parts of a reference a project applied
and what they replaced. ``taste_events`` learns two kinds (rebuilt in batch
mode, as in 0005 and 0006).

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-15

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
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
)
_NEW_VALUES = ("reference_matched", "reference_detached")


def _kinds(values: Sequence[str]) -> str:
    return ", ".join(f"'{kind}'" for kind in values)


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("media_files", schema=None, recreate="always") as batch_op:
        batch_op.add_column(
            sa.Column("role", sa.String(length=16), server_default="footage", nullable=False)
        )
        batch_op.create_check_constraint("role_is_known", "role IN ('footage', 'reference')")
    op.create_table(
        "reference_profiles",
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("versions", sa.String(length=64), nullable=False),
        sa.Column("look_json", sa.Text(), nullable=False),
        sa.Column("rhythm_json", sa.Text(), nullable=False),
        sa.Column("captions_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["sha256"],
            ["media_blobs.sha256"],
            name=op.f("fk_reference_profiles_sha256_media_blobs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("sha256", name=op.f("pk_reference_profiles")),
    )
    op.create_table(
        "reference_matches",
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("reference_media_file_id", sa.String(length=36), nullable=False),
        sa.Column("theme_id", sa.String(length=40), nullable=True),
        sa.Column("look_on", sa.Boolean(), nullable=False),
        sa.Column("pacing_on", sa.Boolean(), nullable=False),
        sa.Column("captions_on", sa.Boolean(), nullable=False),
        sa.Column("pace_json", sa.Text(), nullable=False),
        sa.Column("caption_style_id", sa.String(length=40), nullable=True),
        sa.Column("previous_json", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_reference_matches_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["reference_media_file_id"],
            ["media_files.id"],
            name=op.f("fk_reference_matches_reference_media_file_id_media_files"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("project_id", name=op.f("pk_reference_matches")),
    )
    op.create_index(
        op.f("ix_reference_matches_reference_media_file_id"),
        "reference_matches",
        ["reference_media_file_id"],
        unique=False,
    )
    with op.batch_alter_table("taste_events", schema=None, recreate="always") as batch_op:
        batch_op.drop_constraint("kind_is_a_known_override", type_="check")
        batch_op.create_check_constraint(
            "kind_is_a_known_override", f"kind IN ({_kinds((*_BEFORE_VALUES, *_NEW_VALUES))})"
        )


def downgrade() -> None:
    """Downgrade schema. Reference taste events cannot survive the old CHECK."""
    taste = sa.table("taste_events", sa.column("kind"))
    op.execute(sa.delete(taste).where(taste.c.kind.in_(_NEW_VALUES)))
    with op.batch_alter_table("taste_events", schema=None, recreate="always") as batch_op:
        batch_op.drop_constraint("kind_is_a_known_override", type_="check")
        batch_op.create_check_constraint(
            "kind_is_a_known_override", f"kind IN ({_kinds(_BEFORE_VALUES)})"
        )
    op.drop_index(
        op.f("ix_reference_matches_reference_media_file_id"), table_name="reference_matches"
    )
    op.drop_table("reference_matches")
    op.drop_table("reference_profiles")
    media = sa.table("media_files", sa.column("role"))
    op.execute(sa.delete(media).where(media.c.role == "reference"))
    with op.batch_alter_table("media_files", schema=None, recreate="always") as batch_op:
        batch_op.drop_constraint("role_is_known", type_="check")
        batch_op.drop_column("role")
