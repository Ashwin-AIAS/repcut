"""The style profile: learned from overrides, bounded, explainable, resettable (P2, P3)."""

from __future__ import annotations

import importlib.util
import itertools
import json
from collections.abc import Awaitable, Callable
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from conftest import Harness
from pydantic import ValidationError
from test_captions_export_api import small_exports  # noqa: F401 - fixture
from test_edit_api import _project

from repcut.db.models import TasteEvent
from repcut.edit.types import Pace
from repcut.grading.corrector import SceneTags
from repcut.grading.packs import PackLibrary
from repcut.grading.service import SceneUnit, clip_recommendation
from repcut.profile.learn import MIN_EVENTS, LearnContext, learn
from repcut.profile.replay import moody_fast_cut
from repcut.profile.types import ProfileItem, Resets, StyleProfile

PACK = PackLibrary().load("gym")
QUALITIES = (None, "dim", "soft", "bright", "harsh", "mixed")
TEMPERATURES = (None, "warm", "neutral", "cool")


def _unit(seconds: float = 4.0) -> SceneUnit:
    return SceneUnit(
        id=None,
        index=0,
        start_seconds=0.0,
        end_seconds=seconds,
        analysed=SceneTags(),
        energy_score=None,
    )


def _tags() -> list[SceneTags]:
    return [
        SceneTags(lighting_quality=q, lighting_temperature=t)  # type: ignore[arg-type]
        for q, t in itertools.product(QUALITIES, TEMPERATURES)
    ]


def _ranking(scores: dict[str, float]) -> list[str]:
    order = list(PACK.manifest.themes)
    return sorted(scores, key=lambda t: (-scores[t], order.index(t)))


def test_cold_start_changes_nothing_at_all() -> None:
    empty = learn([])
    assert empty == StyleProfile()
    pace = Pace(energy=0.6, calm_beats=8, peak_beats=4)
    assert empty.apply_pace(pace) is pace
    assert empty.apply_intensity(73) == 73
    assert empty.music_energy_shift() == 0.0
    assert empty.caption_default("punch", ["classic", "minimal", "punch"]) == "punch"
    assert empty.favourite_theme(list(PACK.manifest.themes)) is None
    assert empty.notes() == []
    for tags in _tags():
        units = [(_unit(), tags)]
        assert clip_recommendation(PACK, units, empty) == clip_recommendation(PACK, units)


def test_a_moody_fast_cut_person_shifts_every_recommendation() -> None:
    names = {t.id: t.name for t in PACK.themes}
    profile = learn(moody_fast_cut(10), LearnContext(theme_names=names))
    assert all(profile.active(item) for item in ProfileItem if item is not ProfileItem.MUSIC_ENERGY)

    # Theme ranking: Moody moves up wherever it can, and never down.
    moved_up = 0
    for tags in _tags():
        before = clip_recommendation(PACK, [(_unit(), tags)])
        after = clip_recommendation(PACK, [(_unit(), tags)], profile)
        rank_before = _ranking(before.scores).index("moody")
        rank_after = _ranking(after.scores).index("moody")
        assert rank_after <= rank_before
        moved_up += rank_after < rank_before
        # A prior, not a rule: every theme is still offered.
        assert set(after.scores) == set(PACK.manifest.themes)
    assert moved_up >= len(_tags()) // 2
    untagged = clip_recommendation(PACK, [(_unit(), SceneTags())], profile)
    assert untagged.theme_id == "moody" and untagged.why and "Moody" in untagged.why
    # Strong lighting evidence still wins over the prior.
    harsh = clip_recommendation(PACK, [(_unit(), SceneTags(lighting_quality="harsh"))], profile)
    assert harsh.theme_id == "hardstyle"

    pace = Pace(energy=0.5, calm_beats=8, peak_beats=4)
    fast = profile.apply_pace(pace)
    assert fast.calm_beats < pace.calm_beats and fast.peak_beats <= pace.peak_beats
    assert fast.energy > pace.energy
    assert profile.apply_intensity(80) > 80
    assert profile.caption_default("punch", ["classic", "minimal", "punch"]) == "classic"


def test_one_or_two_clicks_are_a_choice_not_a_style() -> None:
    rows = [r for r in moody_fast_cut(10) if r.kind == "theme"][: MIN_EVENTS - 1]
    profile = learn(rows)
    assert not profile.active(ProfileItem.THEME)
    assert profile.favourite_theme(list(PACK.manifest.themes)) is None


def test_every_active_item_says_why_with_its_evidence() -> None:
    profile = learn(moody_fast_cut(10))
    for item in ProfileItem:
        evidence = profile.evidence[item]
        if evidence.active:
            assert evidence.events >= MIN_EVENTS
            assert any(ch.isdigit() for ch in evidence.why) or "twice" in evidence.why
    assert len(profile.notes()) == sum(e.active for e in profile.evidence.values())


def test_a_reset_forgets_one_item_and_keeps_the_rest() -> None:
    rows = moody_fast_cut(10)
    last = max(r.created_at for r in rows)
    profile = learn(rows, resets=Resets(items={ProfileItem.THEME: last + timedelta(seconds=1)}))
    assert not profile.active(ProfileItem.THEME) and profile.theme == {}
    assert profile.active(ProfileItem.PACING) and profile.active(ProfileItem.INTENSITY)


def test_the_profile_is_read_through_a_schema_never_pickle() -> None:
    profile = learn(moody_fast_cut(10))
    assert StyleProfile.model_validate_json(profile.model_dump_json()) == profile
    with pytest.raises(ValidationError):
        StyleProfile.model_validate_json('{"schema_version": 1, "surprise": true}')
    with pytest.raises(ValidationError):
        StyleProfile.model_validate_json('{"schema_version": 2}')
    package = Path(__file__).parents[1] / "repcut" / "profile"
    for source in package.glob("*.py"):
        text = source.read_text(encoding="utf-8")
        assert "import pickle" not in text and "eval(" not in text, source.name


async def _replay(api: Harness) -> None:
    async with api.session_factory() as session:
        for row in moody_fast_cut(10):
            session.add(
                TasteEvent(
                    kind=row.kind,
                    scope="edit",
                    created_at=row.created_at,
                    recommended_json=json.dumps(dict(row.recommended)),
                    chosen_json=json.dumps(dict(row.chosen)),
                    features_json=json.dumps(dict(row.features)),
                )
            )
        await session.commit()


async def test_profile_on_and_off_through_the_engine(
    api: Harness,
    upload_clip: Callable[..., Awaitable[httpx.Response]],
    make_clip: Callable[..., Path],
) -> None:
    cold = (await api.client.get("/profile")).json()
    assert cold["enabled"] and not any(i["active"] for i in cold["items"]) and cold["notes"] == []
    project = await _project(api, upload_clip, make_clip, clips=2)
    url = f"/projects/{project}/edit"
    before = (await api.client.get(url)).json()
    assert before["theme_id"] == "raw-plus" and before["profile_notes"] == []

    await _replay(api)
    learned = (await api.client.get("/profile")).json()
    active = {i["item"] for i in learned["items"] if i["active"]}
    assert {"theme", "pacing", "intensity", "caption_style"} <= active
    on = (await api.client.get(url)).json()
    assert on["theme_id"] == "moody"
    assert len(on["profile_notes"]) >= 2, on["profile_notes"]

    off = await api.client.post("/profile", json={"enabled": False})
    assert off.status_code == 200 and off.json()["notes"] == []
    plain = (await api.client.get(url)).json()
    assert plain["theme_id"] == "raw-plus" and plain["profile_notes"] == []
    assert [s["timeline_in"] for s in plain["shots"]] == [s["timeline_in"] for s in before["shots"]]

    await api.client.post("/profile", json={"enabled": True})
    reset = await api.client.post("/profile/reset", json={"item": "theme"})
    items = {i["item"]: i for i in reset.json()["items"]}
    assert not items["theme"]["active"] and items["theme"]["reset_at"]
    assert items["pacing"]["active"]
    assert (await api.client.post("/profile/reset", json={"item": "nonsense"})).status_code == 422


async def test_the_review_renders_profile_off_and_on_from_one_session(
    api: Harness,
    upload_clip: Callable[..., Awaitable[httpx.Response]],
    make_clip: Callable[..., Path],
    small_exports: None,  # noqa: F811
    tmp_path: Path,
) -> None:
    """The deferred check's artifact (manual-checks/prompt-10.md): two exports and a page."""
    spec = importlib.util.spec_from_file_location(
        "profile_review", Path(__file__).parents[2] / "scripts" / "profile_review.py"
    )
    assert spec is not None and spec.loader is not None
    review = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(review)
    project = await _project(api, upload_clip, make_clip, clips=2)
    await api.client.post(
        f"/projects/{project}/edit/overrides", json={"kind": "target_duration", "target_seconds": 5}
    )
    profile = learn(moody_fast_cut(10))
    async with api.session_factory() as session:
        off = await review._render(session, api.data_dir, project, StyleProfile(), "off", tmp_path)
        on = await review._render(session, api.data_dir, project, profile, "on", tmp_path)
    assert (tmp_path / "off.mp4").is_file() and (tmp_path / "on.mp4").is_file()
    assert off[0].theme.id == "raw-plus" and on[0].theme.id == "moody"
    assert off[0].profile_notes == () and on[0].profile_notes
    page = review._page(off, on, profile, replay=True)
    assert "SYNTHETIC" in page and "Profile on" in page and "<script" not in page
