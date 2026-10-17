"""The style profile against the database: read it, switch it, reset an item.

``current`` recomputes the profile from every taste event each time it is
asked (a few hundred rows at most; milliseconds), so the profile can never
disagree with its evidence and a reset takes effect on the very next read.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repcut.captions.styles import styles
from repcut.db.models import MusicTrack, StyleProfileState, TasteEvent
from repcut.grading.packs import PackLibrary
from repcut.grading.saved import with_saved
from repcut.music.library import stored_analysis
from repcut.profile.learn import EventRow, LearnContext, learn
from repcut.profile.types import ProfileItem, Resets, StyleProfile

PACK_ID = "gym"
_ROW_ID = 1


@dataclass(frozen=True, slots=True)
class ProfileView:
    """What the "Your style" panel shows: the switch and the learned profile."""

    enabled: bool
    learned: StyleProfile
    resets: Resets

    @property
    def effective(self) -> StyleProfile:
        """What the recommenders read: nothing at all while the profile is off."""
        return self.learned if self.enabled else StyleProfile()


def _resets(raw: str) -> Resets:
    try:
        return Resets.model_validate_json(raw or "{}")
    except ValidationError:
        # Named: an unreadable row is treated as "never reset"; it is rewritten
        # whole by the next reset, never interpreted.
        return Resets()


async def _state(session: AsyncSession) -> StyleProfileState | None:
    return await session.get(StyleProfileState, _ROW_ID)


async def _context(session: AsyncSession, data_dir: Path | None) -> LearnContext:
    pack = with_saved(PackLibrary().load(PACK_ID), data_dir)
    energy: dict[str, float] = {}
    rows = (await session.execute(select(MusicTrack))).scalars()
    for row in rows:
        analysis = stored_analysis(row)
        if analysis is not None and analysis.bar_energy:
            energy[row.sha256] = sum(analysis.bar_energy) / len(analysis.bar_energy)
    return LearnContext(
        theme_names={t.id: t.name for t in pack.themes},
        track_energy=energy,
        caption_names={s.id: s.name for s in styles()},
    )


async def view(session: AsyncSession, data_dir: Path | None) -> ProfileView:
    state = await _state(session)
    resets = _resets(state.resets_json) if state is not None else Resets()
    statement = select(
        TasteEvent.kind,
        TasteEvent.created_at,
        TasteEvent.recommended_json,
        TasteEvent.chosen_json,
        TasteEvent.features_json,
    ).order_by(TasteEvent.created_at)
    rows = [EventRow.from_json(*row) for row in (await session.execute(statement)).all()]
    learned = learn(rows, await _context(session, data_dir), resets) if rows else StyleProfile()
    return ProfileView(
        enabled=state.enabled if state is not None else True, learned=learned, resets=resets
    )


async def current(session: AsyncSession, data_dir: Path | None) -> StyleProfile:
    """The profile the recommenders apply. Off, or no events: ``StyleProfile()``."""
    return (await view(session, data_dir)).effective


async def _row(session: AsyncSession) -> StyleProfileState:
    state = await _state(session)
    if state is None:
        state = StyleProfileState(id=_ROW_ID, enabled=True, resets_json="{}")
        session.add(state)
    return state


async def set_enabled(session: AsyncSession, data_dir: Path | None, enabled: bool) -> ProfileView:
    state = await _row(session)
    state.enabled = enabled
    await session.commit()
    return await view(session, data_dir)


async def reset(
    session: AsyncSession,
    data_dir: Path | None,
    item: ProfileItem | None,
    now: datetime | None = None,
) -> ProfileView:
    """Forget one item (or all of them): events up to now no longer count for it.

    The events themselves are kept; they are the person's history, and other
    items may still be learning from them.
    """
    state = await _row(session)
    when = now or datetime.now(UTC)
    items = dict(_resets(state.resets_json).items)
    for each in [item] if item is not None else list(ProfileItem):
        items[each] = when
    state.resets_json = Resets(items=items).model_dump_json()
    await session.commit()
    return await view(session, data_dir)


__all__ = ["ProfileView", "current", "reset", "set_enabled", "view"]
