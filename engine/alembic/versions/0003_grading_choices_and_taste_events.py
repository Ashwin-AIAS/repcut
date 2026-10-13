"""grading choices and taste events

Prompt 04 Phase B (amendment 012). ``grade_choices`` holds a person's standing
grading choices per clip and per scene (P2: a null field follows the
suggestion). ``taste_events`` records each override once, with what was
suggested, what was chosen and the scene's features at that moment, for the
style profile to read (P3). See ``repcut.db.models.GradeChoice`` and
``TasteEvent`` for the reasoning.

Datetime columns read ``sa.DateTime(timezone=True)`` here and ``UTCDateTime``
in the models, as in 0001 and 0002.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-10

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "grade_choices",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("scene_id", sa.String(length=36), nullable=True),
        sa.Column("pack_id", sa.String(length=40), nullable=False),
        sa.Column("theme_id", sa.String(length=40), nullable=True),
        sa.Column("intensity", sa.Integer(), nullable=True),
        sa.Column("lighting_temperature", sa.String(length=32), nullable=True),
        sa.Column("lighting_quality", sa.String(length=32), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "intensity IS NULL OR (intensity >= 0 AND intensity <= 100)",
            name=op.f("ck_grade_choices_intensity_is_a_percentage"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_grade_choices_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["scene_id"],
            ["scenes.id"],
            name=op.f("fk_grade_choices_scene_id_scenes"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["sha256"],
            ["media_blobs.sha256"],
            name=op.f("fk_grade_choices_sha256_media_blobs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_grade_choices")),
    )
    with op.batch_alter_table("grade_choices", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_grade_choices_project_id"), ["project_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_grade_choices_sha256"), ["sha256"], unique=False)
        batch_op.create_index(
            "uq_grade_choices_clip",
            ["project_id", "sha256"],
            unique=True,
            sqlite_where=sa.text("scene_id IS NULL"),
        )
        batch_op.create_index(
            "uq_grade_choices_scene",
            ["project_id", "scene_id"],
            unique=True,
            sqlite_where=sa.text("scene_id IS NOT NULL"),
        )

    op.create_table(
        "taste_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("scope", sa.String(length=8), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("scene_id", sa.String(length=36), nullable=True),
        sa.Column("pack_id", sa.String(length=40), nullable=False),
        sa.Column("recommended_json", sa.Text(), nullable=False),
        sa.Column("chosen_json", sa.Text(), nullable=False),
        sa.Column("features_json", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('theme', 'intensity', 'lighting_temperature', 'lighting_quality', 'reset')",
            name=op.f("ck_taste_events_kind_is_a_known_override"),
        ),
        sa.CheckConstraint(
            "scope IN ('clip', 'scene')", name=op.f("ck_taste_events_scope_is_clip_or_scene")
        ),
        sa.CheckConstraint(
            "schema_version >= 1", name=op.f("ck_taste_events_schema_version_positive")
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_taste_events_project_id_projects"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["scene_id"],
            ["scenes.id"],
            name=op.f("fk_taste_events_scene_id_scenes"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["sha256"],
            ["media_blobs.sha256"],
            name=op.f("fk_taste_events_sha256_media_blobs"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_taste_events")),
    )
    with op.batch_alter_table("taste_events", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_taste_events_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_taste_events_kind"), ["kind"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_taste_events_project_id"), ["project_id"], unique=False
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("taste_events", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_taste_events_project_id"))
        batch_op.drop_index(batch_op.f("ix_taste_events_kind"))
        batch_op.drop_index(batch_op.f("ix_taste_events_created_at"))

    op.drop_table("taste_events")
    with op.batch_alter_table("grade_choices", schema=None) as batch_op:
        batch_op.drop_index("uq_grade_choices_scene", sqlite_where=sa.text("scene_id IS NOT NULL"))
        batch_op.drop_index("uq_grade_choices_clip", sqlite_where=sa.text("scene_id IS NULL"))
        batch_op.drop_index(batch_op.f("ix_grade_choices_sha256"))
        batch_op.drop_index(batch_op.f("ix_grade_choices_project_id"))

    op.drop_table("grade_choices")
