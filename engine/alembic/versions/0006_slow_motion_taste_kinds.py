"""slow motion overrides as taste events

Prompt 07. ``taste_events`` learns two kinds: a hero shot's slow motion turned
off (``slowmo_off``) and slow motion asked for on other footage
(``slowmo_on``). The decisions themselves live in ``edit_plans.decisions_json``
beside the rest of the edit's, so no table changes. SQLite cannot alter a CHECK,
so ``taste_events`` is rebuilt in batch mode, as in 0005.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-14

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
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
)
_NEW_VALUES = ("slowmo_on", "slowmo_off")


def _kinds(values: Sequence[str]) -> str:
    return ", ".join(f"'{kind}'" for kind in values)


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("taste_events", schema=None, recreate="always") as batch_op:
        batch_op.drop_constraint("kind_is_a_known_override", type_="check")
        batch_op.create_check_constraint(
            "kind_is_a_known_override", f"kind IN ({_kinds((*_BEFORE_VALUES, *_NEW_VALUES))})"
        )


def downgrade() -> None:
    """Downgrade schema. Slow-motion taste events cannot survive the old CHECK."""
    taste = sa.table("taste_events", sa.column("kind"))
    op.execute(sa.delete(taste).where(taste.c.kind.in_(_NEW_VALUES)))
    with op.batch_alter_table("taste_events", schema=None, recreate="always") as batch_op:
        batch_op.drop_constraint("kind_is_a_known_override", type_="check")
        batch_op.create_check_constraint(
            "kind_is_a_known_override", f"kind IN ({_kinds(_BEFORE_VALUES)})"
        )
