"""Slow motion through the real engine: hero badges, the per-cut toggle, taste events (P2, P3)."""

import json
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx
from conftest import Harness
from test_edit_api import _events, _library, _project, _scan


async def test_heroes_are_explained_and_the_slow_motion_toggle_resyncs_and_is_logged(
    api: Harness,
    upload_clip: Callable[..., Awaitable[httpx.Response]],
    make_clip: Callable[..., Path],
) -> None:
    project = await _project(api, upload_clip, make_clip)
    _library(api.data_dir)
    await _scan(api)
    url = f"/projects/{project}/edit"

    edit = (await api.client.get(url)).json()
    heroes = edit["heroes"]
    assert 1 <= len(heroes) <= 3
    assert [h["rank"] for h in heroes] == list(range(1, len(heroes) + 1))
    assert all(h["reasons"] and 0.0 <= h["score"] <= 1.0 for h in heroes)
    slow = [s for s in edit["shots"] if s["speed"] == 0.5]
    assert slow, "the best moment was not slowed"
    for shot in slow:
        assert shot["hero_reason"] and shot["fallback_in"] is not None
        assert (
            abs(
                (shot["source_out"] - shot["source_in"]) * 2
                - (shot["timeline_out"] - shot["timeline_in"])
            )
            < 0.01
        )
    hero = next(h for h in heroes if h["slow_in_edit"])

    off = await api.client.post(
        f"{url}/overrides", json={"kind": "slowmo_off", "segment_id": hero["segment_id"]}
    )
    assert off.status_code == 200, off.text
    edit = off.json()
    assert not any(
        s["speed"] == 0.5 and s["segment_id"] == hero["segment_id"] for s in edit["shots"]
    )
    turned = next(h for h in edit["heroes"] if h["segment_id"] == hero["segment_id"])
    assert turned["slowmo_off"] and not turned["slow_in_edit"]

    on = await api.client.post(
        f"{url}/overrides", json={"kind": "slowmo_on", "segment_id": hero["segment_id"]}
    )
    assert on.status_code == 200, on.text
    assert any(
        s["speed"] == 0.5 and s["segment_id"] == hero["segment_id"] for s in on.json()["shots"]
    )

    bad = await api.client.post(
        f"{url}/overrides", json={"kind": "slowmo_on", "segment_id": "../../etc"}
    )
    assert bad.status_code == 422
    missing = await api.client.post(f"{url}/overrides", json={"kind": "slowmo_off"})
    assert missing.status_code in (400, 409, 422)

    events = await _events(api)
    assert [e.kind for e in events][-2:] == ["slowmo_off", "slowmo_on"]
    recommended = json.loads(events[-2].recommended_json)
    assert recommended["value"] == hero["segment_id"]
    assert recommended["hero_score"] == hero["score"]
    assert json.loads(events[-2].chosen_json)["value"] == hero["segment_id"]
