"""the undo stack, the copilot's conversation and unmet requests, taste event source

``edit_history`` is one row per change from either door (UI or copilot), with
the project's decision rows before and after it. ``copilot_messages`` is the
conversation, per project. ``copilot_unmet_requests`` records what the copilot
could not or would not do. ``taste_events`` gains ``source`` ("ui" for every
existing row) in batch mode, as in 0005-0009.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-20

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | Sequence[str] | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SOURCES = "source IN ('ui', 'copilot')"


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "edit_history",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=8), nullable=False),
        sa.Column("operation", sa.String(length=40), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("before_json", sa.Text(), nullable=False),
        sa.Column("after_json", sa.Text(), nullable=False),
        sa.Column("event_ids_json", sa.Text(), nullable=False),
        sa.Column("undone", sa.Boolean(), nullable=False),
        sa.CheckConstraint(_SOURCES, name=op.f("ck_edit_history_source_is_known")),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_edit_history_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_edit_history")),
    )
    op.create_index(
        op.f("ix_edit_history_project_id"), "edit_history", ["project_id"], unique=False
    )
    op.create_index(
        op.f("ix_edit_history_created_at"), "edit_history", ["created_at"], unique=False
    )
    op.create_table(
        "copilot_messages",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("role", sa.String(length=10), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("actions_json", sa.Text(), nullable=False),
        sa.Column("outcome", sa.String(length=12), nullable=True),
        sa.CheckConstraint(
            "role IN ('user', 'assistant')", name=op.f("ck_copilot_messages_role_is_known")
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_copilot_messages_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_copilot_messages")),
    )
    op.create_index(
        op.f("ix_copilot_messages_project_id"), "copilot_messages", ["project_id"], unique=False
    )
    op.create_index(
        op.f("ix_copilot_messages_created_at"), "copilot_messages", ["created_at"], unique=False
    )
    op.create_table(
        "copilot_unmet_requests",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("request", sa.Text(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "kind IN ('unsupported', 'principle', 'blocked_call')",
            name=op.f("ck_copilot_unmet_requests_kind_is_known"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_copilot_unmet_requests_project_id_projects"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_copilot_unmet_requests")),
    )
    op.create_index(
        op.f("ix_copilot_unmet_requests_project_id"),
        "copilot_unmet_requests",
        ["project_id"],
        unique=False,
    )
    with op.batch_alter_table("taste_events", schema=None, recreate="always") as batch_op:
        batch_op.add_column(
            sa.Column("source", sa.String(length=8), nullable=False, server_default="ui")
        )
        batch_op.create_check_constraint("source_is_known", _SOURCES)
    with op.batch_alter_table("taste_events", schema=None) as batch_op:
        batch_op.alter_column("source", server_default=None)


def downgrade() -> None:
    """Downgrade schema. Copilot taste events become UI ones; the stack is dropped."""
    with op.batch_alter_table("taste_events", schema=None, recreate="always") as batch_op:
        batch_op.drop_constraint("source_is_known", type_="check")
        batch_op.drop_column("source")
    op.drop_index(op.f("ix_copilot_unmet_requests_project_id"), table_name="copilot_unmet_requests")
    op.drop_table("copilot_unmet_requests")
    op.drop_index(op.f("ix_copilot_messages_created_at"), table_name="copilot_messages")
    op.drop_index(op.f("ix_copilot_messages_project_id"), table_name="copilot_messages")
    op.drop_table("copilot_messages")
    op.drop_index(op.f("ix_edit_history_created_at"), table_name="edit_history")
    op.drop_index(op.f("ix_edit_history_project_id"), table_name="edit_history")
    op.drop_table("edit_history")
