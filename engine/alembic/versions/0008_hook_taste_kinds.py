"""the opening hook's overrides as taste events

The edit's opening teaser can be turned on, off, or swapped for another
candidate; each is a taste event like every other edit override. The decision
itself lives in ``edit_plans.decisions_json`` (``hook``, ``hook_segment``),
so no table changes. SQLite cannot alter a CHECK, so ``taste_events`` is
rebuilt in batch mode, as in 0005-0007.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-16

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
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
)
_NEW_VALUES = ("hook_on", "hook_off", "hook_choose")


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
    """Downgrade schema. Hook taste events cannot survive the old CHECK."""
    taste = sa.table("taste_events", sa.column("kind"))
    op.execute(sa.delete(taste).where(taste.c.kind.in_(_NEW_VALUES)))
    with op.batch_alter_table("taste_events", schema=None, recreate="always") as batch_op:
        batch_op.drop_constraint("kind_is_a_known_override", type_="check")
        batch_op.create_check_constraint(
            "kind_is_a_known_override", f"kind IN ({_kinds(_BEFORE_VALUES)})"
        )
