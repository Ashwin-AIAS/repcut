"""The "Your style" routes: what the profile learned, its switch, per-item reset.

P2 applies to the profile itself: everything it learned is shown with the
evidence behind it, it can be switched off, and any item can be forgotten.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from repcut.api.deps import SessionDep, SettingsDep
from repcut.profile import service
from repcut.profile.types import ProfileItem, StyleProfile

router = APIRouter(tags=["profile"])


class ProfileItemResponse(BaseModel):
    item: ProfileItem
    active: bool = Field(description="Enough evidence to change a suggestion")
    events: int
    why: str
    value: float | None = Field(description="The bias, for the single-number items")
    leanings: dict[str, float] = Field(
        default_factory=dict, description="Per theme or caption style: the prior added"
    )
    reset_at: str | None


class ProfileResponse(BaseModel):
    enabled: bool
    items: list[ProfileItemResponse]
    notes: list[str] = Field(description="What the profile is changing right now, in words")


class ProfileChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


class ProfileReset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item: ProfileItem | Literal["all"]


def _value(profile: StyleProfile, item: ProfileItem) -> tuple[float | None, dict[str, float]]:
    if item is ProfileItem.THEME:
        return None, dict(profile.theme)
    if item is ProfileItem.CAPTION_STYLE:
        return None, dict(profile.caption_style)
    if item is ProfileItem.INTENSITY:
        return profile.intensity, {}
    if item is ProfileItem.PACING:
        return profile.pacing, {}
    return profile.music_energy, {}


def _response(view: service.ProfileView) -> ProfileResponse:
    items = []
    for item in ProfileItem:
        evidence = view.learned.evidence.get(item)
        value, leanings = _value(view.learned, item)
        reset = view.resets.items.get(item)
        items.append(
            ProfileItemResponse(
                item=item,
                active=bool(evidence and evidence.active),
                events=evidence.events if evidence else 0,
                why=evidence.why if evidence else "Nothing learned yet.",
                value=value,
                leanings=leanings,
                reset_at=reset.isoformat() if reset else None,
            )
        )
    return ProfileResponse(enabled=view.enabled, items=items, notes=view.effective.notes())


@router.get("/profile", response_model=ProfileResponse, summary="Your style: what was learned")
async def get_profile(session: SessionDep, settings: SettingsDep) -> ProfileResponse:
    return _response(await service.view(session, settings.data_dir))


@router.post("/profile", response_model=ProfileResponse, summary="Switch the style profile")
async def post_profile(
    body: ProfileChange, session: SessionDep, settings: SettingsDep
) -> ProfileResponse:
    return _response(await service.set_enabled(session, settings.data_dir, body.enabled))


@router.post(
    "/profile/reset", response_model=ProfileResponse, summary="Forget one item, or all of them"
)
async def post_profile_reset(
    body: ProfileReset, session: SessionDep, settings: SettingsDep
) -> ProfileResponse:
    item = None if body.item == "all" else ProfileItem(body.item)
    return _response(await service.reset(session, settings.data_dir, item))


__all__ = ["router"]
