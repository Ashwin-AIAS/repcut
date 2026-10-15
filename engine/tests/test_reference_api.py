"""Reference clips end to end: mark, measure, match, detach - through the real engine.

The reference goes through the ordinary upload route (the same probe and
refusals as footage, security §7), then the ordinary ingest and analysis jobs.
"""

import json
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx
from conftest import Harness
from sqlalchemy import select

from repcut.db.models import CaptionSettings, GradeChoice, TasteEvent

_UNKNOWN = "00000000-0000-4000-8000-000000000000"


async def _setup(
    api: Harness,
    upload_clip: Callable[..., Awaitable[httpx.Response]],
    make_motion_loudness_clip: Callable[..., Path],
    make_clip: Callable[..., Path],
) -> tuple[str, str, str]:
    project = (await api.client.post("/projects", json={"name": "matching"})).json()["id"]
    footage = await upload_clip(project, make_clip("mine.mp4", seconds=3.0))
    assert footage.status_code == 200, footage.text
    reference = await upload_clip(project, make_motion_loudness_clip(segment_seconds=1.5))
    assert reference.status_code == 200, reference.text
    await api.queue.drain()
    ref_id = reference.json()["media_file_id"]
    marked = await api.client.put(f"/media/{ref_id}/role", json={"role": "reference"})
    assert marked.status_code == 200 and marked.json()["role"] == "reference"
    started = await api.client.post(f"/projects/{project}/references/{ref_id}/analyse")
    assert started.status_code == 202, started.text
    await api.queue.drain()
    return project, footage.json()["media_file_id"], ref_id


async def _events(api: Harness, kind: str) -> list[TasteEvent]:
    async with api.session_factory() as session:
        rows = await session.execute(select(TasteEvent).where(TasteEvent.kind == kind))
        return list(rows.scalars())


async def test_match_applies_three_parts_and_each_detaches_alone(
    api: Harness,
    upload_clip: Callable[..., Awaitable[httpx.Response]],
    make_motion_loudness_clip: Callable[..., Path],
    make_clip: Callable[..., Path],
) -> None:
    project, footage_id, ref_id = await _setup(api, upload_clip, make_motion_loudness_clip, make_clip)

    profile = await api.client.get(f"/projects/{project}/references/{ref_id}")
    assert profile.status_code == 200, profile.text
    body = profile.json()
    assert body["rhythm"]["shot_count"] >= 2
    assert set(body["pace"]) >= {"energy", "calm_beats", "peak_beats", "slowmo_every_seconds"}
    assert body["caption_match"]["style_id"]

    listed = (await api.client.get(f"/projects/{project}/media")).json()
    roles = {item["id"]: item["role"] for item in listed}
    assert roles == {footage_id: "footage", ref_id: "reference"}
    ref_sha = next(item["sha256"] for item in listed if item["id"] == ref_id)

    matched = await api.client.put(
        f"/projects/{project}/reference-match", json={"media_file_id": ref_id}
    )
    assert matched.status_code == 200, matched.text
    state = matched.json()
    assert state["look_on"] and state["pacing_on"] and state["captions_on"]
    theme_id = state["theme_id"]
    assert theme_id.startswith("ref-")

    # The saved look is a theme like any other: in the selector, on the footage.
    pack = (await api.client.get("/grading/packs/gym")).json()
    assert theme_id in [t["id"] for t in pack["themes"]]
    grade = (await api.client.get(f"/media/{footage_id}/grade")).json()
    assert grade["theme_id"] == theme_id
    async with api.session_factory() as session:
        captions = await session.get(CaptionSettings, project)
        assert captions is not None and captions.style_id == state["caption_style_id"]
    # The edit cuts the footage only, at the reference's pace.
    edit = (await api.client.get(f"/projects/{project}/edit")).json()
    # Its clips are the footage alone (neither is measured yet in this harness).
    assert edit["clips_pending"] == [footage_id]
    assert all(segment["sha256"] != ref_sha for segment in edit["segments"])
    assert all(shot["sha256"] != ref_sha for shot in edit["shots"])
    matched_events = await _events(api, "reference_matched")
    assert sorted(json.loads(e.recommended_json)["value"] for e in matched_events) == [
        "captions",
        "look",
        "pacing",
    ]

    # Re-matching is a no-op: no new events.
    again = await api.client.put(f"/projects/{project}/reference-match", json={"media_file_id": ref_id})
    assert again.status_code == 200
    assert len(await _events(api, "reference_matched")) == 3

    # Detach the look: the footage goes back to the person's own (none: the suggestion).
    after = (await api.client.delete(f"/projects/{project}/reference-match/look")).json()
    assert after["look_on"] is False and after["pacing_on"] and after["captions_on"]
    async with api.session_factory() as session:
        rows = (await session.execute(select(GradeChoice))).scalars().all()
        assert all(row.theme_id != theme_id for row in rows)
    grade = (await api.client.get(f"/media/{footage_id}/grade")).json()
    assert grade["theme_id"] == grade["recommended_theme_id"]

    # Captions and pacing detach too; with nothing left the match is gone.
    await api.client.delete(f"/projects/{project}/reference-match/captions")
    gone = await api.client.delete(f"/projects/{project}/reference-match/pacing")
    assert gone.status_code == 200 and gone.json() is None
    assert len(await _events(api, "reference_detached")) == 3
    # The saved theme stays saved: detaching a look does not delete it.
    pack = (await api.client.get("/grading/packs/gym")).json()
    assert theme_id in [t["id"] for t in pack["themes"]]


async def test_a_person_s_own_theme_comes_back_after_detach(
    api: Harness,
    upload_clip: Callable[..., Awaitable[httpx.Response]],
    make_motion_loudness_clip: Callable[..., Path],
    make_clip: Callable[..., Path],
) -> None:
    project, footage_id, ref_id = await _setup(api, upload_clip, make_motion_loudness_clip, make_clip)
    chose = await api.client.put(f"/media/{footage_id}/grade", json={"theme_id": "moody"})
    assert chose.status_code == 200, chose.text
    await api.client.put(
        f"/projects/{project}/reference-match", json={"media_file_id": ref_id, "parts": ["look"]}
    )
    assert (await api.client.get(f"/media/{footage_id}/grade")).json()["theme_id"].startswith("ref-")
    await api.client.delete(f"/projects/{project}/reference-match/look")
    assert (await api.client.get(f"/media/{footage_id}/grade")).json()["theme_id"] == "moody"


async def test_reference_requests_are_refused_by_name(
    api: Harness,
    upload_clip: Callable[..., Awaitable[httpx.Response]],
    make_motion_loudness_clip: Callable[..., Path],
    make_clip: Callable[..., Path],
) -> None:
    project, footage_id, ref_id = await _setup(api, upload_clip, make_motion_loudness_clip, make_clip)
    # Footage is not a reference.
    wrong = await api.client.put(
        f"/projects/{project}/reference-match", json={"media_file_id": footage_id}
    )
    assert wrong.status_code == 404 and wrong.json()["error"]["code"] == "reference_not_found"
    missing = await api.client.get(f"/projects/{project}/references/{_UNKNOWN}")
    assert missing.status_code == 404
    none = await api.client.put(
        f"/projects/{project}/reference-match", json={"media_file_id": ref_id, "parts": []}
    )
    assert none.status_code == 422
    bad_part = await api.client.delete(f"/projects/{project}/reference-match/everything")
    assert bad_part.status_code == 422
    # A matched reference cannot silently become footage.
    await api.client.put(f"/projects/{project}/reference-match", json={"media_file_id": ref_id, "parts": ["pacing"]})
    back = await api.client.put(f"/media/{ref_id}/role", json={"role": "footage"})
    assert back.status_code == 422 and back.json()["error"]["code"] == "invalid_reference_request"
    weird = await api.client.put(f"/media/{ref_id}/role", json={"role": "b-roll"})
    assert weird.status_code == 422
