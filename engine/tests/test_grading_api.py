"""Grading routes and the graded-preview job, end to end through the real engine.

The ``api`` harness has no Gemini key, so every scene's tags are null (`vlm:
null` is a normal input, amendment 012 row 8); lighting tags reach the grade
through the person's override, which is exactly the path P2 asks for.
"""

import asyncio
import json
import subprocess
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx
from conftest import Harness
from sqlalchemy import func, select

from repcut.db.models import GradeChoice, MediaFile, TasteEvent
from repcut.grading import service

_UNKNOWN_UUID = "00000000-0000-4000-8000-000000000000"


async def _clip(
    api: Harness,
    upload_clip: Callable[..., Awaitable[httpx.Response]],
    make_motion_loudness_clip: Callable[..., Path],
) -> str:
    project = await api.client.post("/projects", json={"name": "grading"})
    finalized = await upload_clip(
        project.json()["id"], make_motion_loudness_clip(segment_seconds=1.5)
    )
    assert finalized.status_code == 200, finalized.text
    await api.queue.drain()
    return finalized.json()["media_file_id"]  # type: ignore[no-any-return]


async def _events(api: Harness) -> list[TasteEvent]:
    async with api.session_factory() as session:
        rows = await session.execute(select(TasteEvent).order_by(TasteEvent.created_at))
        return list(rows.scalars().all())


async def _grade(api: Harness, media_file_id: str) -> dict[str, Any]:
    response = await api.client.get(f"/media/{media_file_id}/grade")
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


async def test_choices_write_one_taste_event_each_and_reset_restores_the_suggestion(
    api: Harness,
    upload_clip: Callable[..., Awaitable[httpx.Response]],
    make_motion_loudness_clip: Callable[..., Path],
) -> None:
    media_file_id = await _clip(api, upload_clip, make_motion_loudness_clip)
    before = await _grade(api, media_file_id)
    assert before["suggested"] is True
    assert len(before["scenes"]) >= 2
    # No tags anywhere: the pack's default theme, as a default (P2).
    assert before["theme_id"] == before["recommended_theme_id"] == "raw-plus"
    first, second = before["scenes"][0]["key"], before["scenes"][1]["key"]

    # 1. a per-scene theme override
    after = (
        await api.client.put(
            f"/media/{media_file_id}/grade/scenes/{first}", json={"theme_id": "moody"}
        )
    ).json()
    assert after["scenes"][0]["theme_id"] == "moody"
    assert after["scenes"][1]["theme_id"] == "raw-plus"
    assert after["scenes"][0]["suggested"] is False
    events = await _events(api)
    assert [(e.kind, e.scope, e.scene_id) for e in events] == [("theme", "scene", first)]
    recommended = json.loads(events[0].recommended_json)
    chosen = json.loads(events[0].chosen_json)
    features = json.loads(events[0].features_json)
    assert recommended["value"] == "raw-plus" and set(recommended["scores"]) >= {"moody"}
    assert chosen == {"value": "moody", "previous": None}
    assert {"analysed", "effective", "correction", "measured", "theme_before"} <= set(features)
    assert features["theme_before"] == "raw-plus"

    # 2. an intensity change, clip-wide
    await api.client.put(f"/media/{media_file_id}/grade", json={"intensity": 40})
    events = await _events(api)
    assert [e.kind for e in events] == ["theme", "intensity"]
    assert events[1].scope == "clip"
    assert (await _grade(api, media_file_id))["scenes"][1]["intensity"] == 40

    # the same value again is not a new signal
    await api.client.put(f"/media/{media_file_id}/grade", json={"intensity": 40})
    assert len(await _events(api)) == 2

    # 3. a lighting-tag override on one scene
    response = await api.client.put(
        f"/media/{media_file_id}/grade/scenes/{second}",
        json={"lighting_temperature": "cool"},
    )
    assert response.status_code == 200, response.text
    graded = response.json()
    assert graded["scenes"][1]["effective"]["lighting_temperature"] == "cool"
    assert graded["scenes"][1]["analysed"]["lighting_temperature"] is None
    events = await _events(api)
    assert [e.kind for e in events][-1] == "lighting_temperature"
    assert json.loads(events[-1].recommended_json) == {"value": None}

    # reset one scene: back to the clip's grade, one event
    reset = await api.client.post(f"/media/{media_file_id}/grade/reset", json={"scene_id": second})
    assert reset.json()["scenes"][1]["effective"]["lighting_temperature"] is None
    assert [e.kind for e in await _events(api)][-1] == "reset"
    count = len(await _events(api))

    # reset everything: the suggestion again, rows gone (never written back)
    reset = await api.client.post(f"/media/{media_file_id}/grade/reset", json={"everything": True})
    assert reset.json()["suggested"] is True
    assert reset.json()["scenes"][0]["theme_id"] == "raw-plus"
    assert len(await _events(api)) == count + 1
    async with api.session_factory() as session:
        assert (await session.scalar(select(func.count()).select_from(GradeChoice))) == 0

    # resetting nothing records nothing
    await api.client.post(f"/media/{media_file_id}/grade/reset", json={"everything": True})
    assert len(await _events(api)) == count + 1


async def test_bad_choices_are_named_errors(
    api: Harness,
    upload_clip: Callable[..., Awaitable[httpx.Response]],
    make_motion_loudness_clip: Callable[..., Path],
) -> None:
    media_file_id = await _clip(api, upload_clip, make_motion_loudness_clip)
    url = f"/media/{media_file_id}/grade"

    unknown = await api.client.put(url, json={"theme_id": "neon"})
    assert unknown.status_code == 404 and unknown.json()["error"]["code"] == "theme_not_found"

    clip_lighting = await api.client.put(url, json={"lighting_quality": "dim"})
    assert clip_lighting.status_code == 422
    assert clip_lighting.json()["error"]["code"] == "invalid_grade_choice"

    not_a_tag = await api.client.put(url, json={"lighting_temperature": "purple"})
    assert not_a_tag.status_code == 422

    out_of_range = await api.client.put(url, json={"intensity": 101})
    assert out_of_range.status_code == 422

    scene = await api.client.put(f"{url}/scenes/{_UNKNOWN_UUID}", json={"theme_id": "moody"})
    assert scene.status_code == 404 and scene.json()["error"]["code"] == "scene_not_found"

    missing = await api.client.get(f"/media/{_UNKNOWN_UUID}/grade")
    assert missing.status_code == 404
    assert (await api.client.get("/grading/packs/nope")).status_code == 404
    assert not await _events(api), "a rejected choice is not a taste signal"


async def test_stills_previews_and_a_lighting_override_regrades_that_scene_only(
    api: Harness,
    upload_clip: Callable[..., Awaitable[httpx.Response]],
    make_motion_loudness_clip: Callable[..., Path],
) -> None:
    media_file_id = await _clip(api, upload_clip, make_motion_loudness_clip)

    pack = (await api.client.get("/grading/packs/gym")).json()
    assert len(pack["themes"]) == 5
    for theme in pack["themes"]:
        still = await api.client.get(
            f"/media/{media_file_id}/grade/still", params={"theme": theme["id"]}
        )
        assert still.status_code == 200, still.text
        assert still.headers["content-type"] == "image/jpeg"
        assert still.content[:2] == b"\xff\xd8"

    started = await api.client.post(f"/media/{media_file_id}/grade/preview")
    assert started.status_code == 202
    await api.queue.drain()
    grade = await _grade(api, media_file_id)
    assert all(scene["preview_ready"] for scene in grade["scenes"])
    preview = await api.client.get(
        f"/media/{media_file_id}/grade/preview", params={"scene": grade["scenes"][0]["key"]}
    )
    assert preview.status_code == 200 and preview.headers["content-type"] == "video/mp4"

    # override the second scene's lighting: its correction moves, the first's does not
    second = grade["scenes"][1]
    changed = (
        await api.client.put(
            f"/media/{media_file_id}/grade/scenes/{second['key']}",
            json={"lighting_temperature": "warm"},
        )
    ).json()
    assert changed["scenes"][0]["correction"] == grade["scenes"][0]["correction"]
    assert changed["scenes"][1]["correction"] != second["correction"]
    assert changed["scenes"][0]["preview_ready"] is True
    assert changed["scenes"][1]["preview_ready"] is False

    async with api.session_factory() as session:
        media_file = await session.get(MediaFile, media_file_id)
        assert media_file is not None
        clip = await service.clip_grade(session, api.settings.data_dir, media_file)
    rendered = await service.ensure_previews(api.settings.data_dir, clip)
    assert rendered == [second["key"]]

    path = service.preview_path(api.settings.data_dir, clip, clip.scenes[1])
    probed = await asyncio.to_thread(
        subprocess.run,
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_streams",
            "-of",
            "json",
            path.as_posix(),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    stream = json.loads(probed.stdout)["streams"][0]
    assert (stream["color_primaries"], stream["color_transfer"], stream["color_space"]) == (
        "bt709",
        "bt709",
        "bt709",
    )
