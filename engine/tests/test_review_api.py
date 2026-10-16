"""The hook, the before/after and the summary through the real engine (P2, P3, P4)."""

from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx
from conftest import Harness
from test_edit_api import _events, _library, _project, _scan

from repcut.review.compare import first_frame
from repcut.review.summary import Claim, Fact, verify


def _hooks(edit: dict[str, object]) -> list[dict[str, object]]:
    return [s for s in edit["shots"] if s["hook"]]  # type: ignore[attr-defined, index]


async def test_the_hook_opens_the_edit_and_every_change_resyncs_and_is_logged(
    api: Harness,
    upload_clip: Callable[..., Awaitable[httpx.Response]],
    make_clip: Callable[..., Path],
) -> None:
    project = await _project(api, upload_clip, make_clip)
    _library(api.data_dir)
    await _scan(api)
    url = f"/projects/{project}/edit"
    edit = (await api.client.get(url)).json()
    assert edit["hook_mode"] == "on"
    candidates = edit["hook_candidates"]
    assert 1 <= len(candidates) <= 3, "the generated clips move: there are heroes"
    hero_ids = {h["segment_id"] for h in edit["heroes"]}
    assert {c["segment_id"] for c in candidates} <= hero_ids
    teaser = _hooks(edit)
    assert len(teaser) == 1 and teaser[0]["timeline_in"] == 0.0
    assert teaser[0]["segment_id"] == candidates[0]["segment_id"] and candidates[0]["chosen"]
    assert edit["hook_placement"] in ("duplicated", "moved")
    length = edit["duration_seconds"]
    assert abs(length - edit["target_seconds"]) <= 0.05 * edit["target_seconds"]

    off = await api.client.post(f"{url}/overrides", json={"kind": "hook_off"})
    assert off.status_code == 200, off.text
    assert _hooks(off.json()) == [] and off.json()["hook_mode"] == "off"
    assert off.json()["duration_seconds"] == length

    if len(candidates) > 1:
        pick = candidates[-1]["segment_id"]
        chosen = await api.client.post(
            f"{url}/overrides", json={"kind": "hook_choose", "segment_id": pick}
        )
        assert chosen.status_code == 200, chosen.text
        assert chosen.json()["hook_mode"] == "manual"
        assert _hooks(chosen.json())[0]["segment_id"] == pick

    back = await api.client.post(f"{url}/overrides", json={"kind": "hook_on"})
    assert _hooks(back.json())[0]["segment_id"] == candidates[0]["segment_id"]

    kinds = [e.kind for e in await _events(api)]
    assert "hook_off" in kinds and "hook_on" in kinds
    if len(candidates) > 1:
        assert "hook_choose" in kinds
    bad = await api.client.post(f"{url}/overrides", json={"kind": "hook_choose"})
    assert bad.status_code == 422


async def test_the_summary_only_says_what_the_record_backs(
    api: Harness,
    upload_clip: Callable[..., Awaitable[httpx.Response]],
    make_clip: Callable[..., Path],
) -> None:
    project = await _project(api, upload_clip, make_clip)
    _library(api.data_dir)
    await _scan(api)
    response = await api.client.get(f"/projects/{project}/edit/summary")
    assert response.status_code == 200, response.text
    body = response.json()
    record, claims = body["record"], body["claims"]
    assert claims, "an edit with music and heroes has something to say"
    parsed = [
        Claim(c["id"], c["text"], tuple(Fact(f["pointer"], f["value"]) for f in c["facts"]))
        for c in claims
    ]
    assert verify(record, parsed) == []
    edit = (await api.client.get(f"/projects/{project}/edit")).json()
    assert record["plan"]["shots"] == [
        {k: v for k, v in s.items() if k in record["plan"]["shots"][0]} for s in edit["shots"]
    ]
    ids = {c["id"] for c in claims}
    assert "cuts" in ids and "music" in ids
    # Nothing in the record points at a file.
    assert "/" not in str(record.get("clip_names", {})).replace("://", "")


async def test_the_comparison_names_both_sides_and_how_they_line_up(
    api: Harness,
    upload_clip: Callable[..., Awaitable[httpx.Response]],
    make_clip: Callable[..., Path],
) -> None:
    project = (await api.client.post("/projects", json={"name": "compare"})).json()["id"]
    finalized = await upload_clip(project, make_clip("c.mp4", seconds=6.0))
    media_file_id = finalized.json()["media_file_id"]
    await api.queue.drain()
    response = await api.client.get(f"/media/{media_file_id}/compare")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["before_url"] == f"/media/{media_file_id}/proxy"
    assert body["after_url"].startswith(f"/media/{media_file_id}/grade/preview?scene=")
    assert body["offset_frames"] == first_frame(body["start_seconds"], body["fps"])
    assert body["frame_count"] >= 1 and body["preview_ready"] is False
    assert body["captions_on"] is True
    missing = await api.client.get("/media/00000000-0000-0000-0000-000000000000/compare")
    assert missing.status_code == 404
