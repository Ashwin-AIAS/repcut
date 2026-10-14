"""The edit end to end through the real engine: measure, scan, plan, override.

Music is generated (`music/synthetic.py`); nothing is downloaded or committed.
The harness has no Gemini key and no speech model, so scene tags are null and
speech is "not measured" - both are normal inputs the edit must handle.
"""

import contextlib
import json
import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from conftest import Harness
from sqlalchemy import select

from repcut.db.models import TasteEvent
from repcut.edit.service import NO_LIBRARY
from repcut.music.synthetic import generate_track, to_wav_bytes

SNAP = 0.040


async def _project(
    api: Harness,
    upload_clip: Callable[..., Awaitable[httpx.Response]],
    make_clip: Callable[..., Path],
    clips: int = 3,
) -> str:
    project = (await api.client.post("/projects", json={"name": "edit"})).json()["id"]
    for index in range(clips):
        # Different lengths: identical bytes would be one blob, one clip.
        clip = make_clip(f"clip{index}.mp4", seconds=12.0 + index)
        finalized = await upload_clip(project, clip)
        assert finalized.status_code == 200, finalized.text
    await api.queue.drain()
    started = await api.client.post(f"/projects/{project}/edit/signals")
    assert started.status_code == 202 and len(started.json()["job_ids"]) == clips
    await api.queue.drain()
    assert (await api.client.post(f"/projects/{project}/edit/signals")).json()["job_ids"] == []
    return project  # type: ignore[no-any-return]


def _library(data_dir: Path) -> dict[str, Path]:
    music = data_dir / "music"
    music.mkdir(parents=True, exist_ok=True)
    tracks = {
        "fast drop.wav": generate_track(
            bpm=140.0, bars=48, intro_bars=4, build_bars=4, drop_bars=24
        ),
        # A name FFmpeg would read as an option if it were ever passed bare.
        "-y slow.wav": generate_track(bpm=96.0, bars=40, intro_bars=4, build_bars=4, drop_bars=20),
    }
    paths = {}
    for name, track in tracks.items():
        path = music / name
        path.write_bytes(to_wav_bytes(track.samples, track.sample_rate))
        paths[name] = path
    (music / "notes.txt").write_text("not audio", encoding="utf-8")
    return paths


async def _scan(api: Harness) -> list[dict[str, Any]]:
    assert (await api.client.post("/music/scan")).status_code == 202
    await api.queue.drain()
    tracks = (await api.client.get("/music/tracks")).json()["tracks"]
    return tracks  # type: ignore[no-any-return]


def _beat_error(edit: dict[str, Any], cuts: list[float]) -> float:
    beats = edit["beats"]
    return max((min(abs(c - b) for b in beats) for c in cuts), default=0.0)


async def _events(api: Harness) -> list[TasteEvent]:
    async with api.session_factory() as session:
        result = await session.execute(select(TasteEvent).order_by(TasteEvent.created_at))
        return list(result.scalars())


async def test_the_library_is_scanned_safely_and_analysed_once(api: Harness) -> None:
    _library(api.data_dir)
    if hasattr(os, "symlink"):
        outside = api.data_dir.parent / "outside.wav"
        outside.write_bytes(b"RIFF")
        # Windows without the symlink privilege refuses; then there is nothing to test.
        with contextlib.suppress(OSError):
            (api.data_dir / "music" / "escape.wav").symlink_to(outside)
    tracks = await _scan(api)
    assert sorted(t["display_name"] for t in tracks) == ["-y slow.wav", "fast drop.wav"]
    assert all(t["status"] == "analysed" for t in tracks), tracks
    assert {round(t["bpm"]) for t in tracks} == {140, 96}
    assert (api.data_dir / "music" / "README.md").is_file()
    # No path anywhere in the store is named after a track.
    for path in api.data_dir.rglob("*"):
        if path.parent.name != "music":
            assert "slow" not in path.name and "drop" not in path.name, path.name
    again = await _scan(api)
    assert again == tracks


async def test_an_edit_cuts_on_the_beat_and_every_override_resyncs_and_is_logged(
    api: Harness,
    upload_clip: Callable[..., Awaitable[httpx.Response]],
    make_clip: Callable[..., Path],
) -> None:
    project = await _project(api, upload_clip, make_clip)
    _library(api.data_dir)
    tracks = await _scan(api)
    url = f"/projects/{project}/edit"

    edit = (await api.client.get(url)).json()
    assert edit["snapped"] and edit["music"] == "suggested"
    assert 1 <= len(edit["recommendations"]) <= 5
    assert all(r["reason"] for r in edit["recommendations"])
    assert edit["track_sha256"] == edit["recommendations"][0]["sha256"]
    cuts = [s["timeline_in"] for s in edit["shots"][1:]]
    assert _beat_error(edit, cuts) <= SNAP
    assert abs(edit["duration_seconds"] - 30.0) <= 1.5

    locked = edit["shots"][2]
    edit = (
        await api.client.post(
            f"{url}/overrides", json={"kind": "cut_locked", "shot_id": locked["id"]}
        )
    ).json()
    other = next(t["sha256"] for t in tracks if t["sha256"] != edit["track_sha256"])
    changed = await api.client.post(
        f"{url}/overrides", json={"kind": "music_track", "track_sha256": other}
    )
    assert changed.status_code == 200, changed.text
    edit = changed.json()
    assert edit["track_sha256"] == other and edit["music"] == "chosen"
    assert {**locked, "locked": True} in edit["shots"]
    edges = {locked["timeline_in"], locked["timeline_out"]}
    unlocked = [s["timeline_in"] for s in edit["shots"][1:] if s["timeline_in"] not in edges]
    assert unlocked and _beat_error(edit, unlocked) <= SNAP
    assert edit["resync_ms"] < edit["resync_budget_ms"]

    removed = edit["shots"][0]
    edit = (
        await api.client.post(
            f"{url}/overrides", json={"kind": "cut_removed", "shot_id": removed["id"]}
        )
    ).json()
    for shot in edit["shots"]:
        if shot["sha256"] == removed["sha256"]:
            overlaps = (
                shot["source_in"] < removed["source_out"]
                and shot["source_out"] > removed["source_in"]
            )
            assert not overlaps, "removed footage came back"
    assert {**locked, "locked": True} in edit["shots"]

    edit = (await api.client.post(f"{url}/overrides", json={"kind": "music_off"})).json()
    assert not edit["snapped"] and edit["track_sha256"] is None and edit["gain"] == []

    events = await _events(api)
    assert [e.kind for e in events] == ["cut_locked", "music_track", "cut_removed", "music_off"]
    assert all(e.scope == "edit" and e.pack_id is None for e in events)
    assert json.loads(events[1].chosen_json)["value"] == other
    assert "track_sha256" in json.loads(events[1].features_json)

    # The decisions survive a reload: the stored edit is the same edit.
    assert (await api.client.get(url)).json()["shots"] == edit["shots"]


async def test_an_empty_library_still_makes_an_edit(
    api: Harness,
    upload_clip: Callable[..., Awaitable[httpx.Response]],
    make_clip: Callable[..., Path],
) -> None:
    project = await _project(api, upload_clip, make_clip, clips=2)
    response = await api.client.get(f"/projects/{project}/edit")
    assert response.status_code == 200
    edit = response.json()
    assert not edit["snapped"] and edit["shots"] and NO_LIBRARY in edit["warnings"]


@pytest.mark.parametrize(
    "body",
    [
        {"kind": "cut_locked", "shot_id": "0123456789abcdef"},
        {"kind": "music_track"},
        {"kind": "target_duration", "target_seconds": 500},
        {"kind": "cut_locked", "shot_id": "../../etc"},
        {"kind": "explode"},
    ],
)
async def test_bad_overrides_are_named_errors(
    api: Harness,
    upload_clip: Callable[..., Awaitable[httpx.Response]],
    make_clip: Callable[..., Path],
    body: dict[str, Any],
) -> None:
    project = await _project(api, upload_clip, make_clip, clips=1)
    response = await api.client.post(f"/projects/{project}/edit/overrides", json=body)
    assert response.status_code == 422
    assert await _events(api) == []


async def test_an_unknown_project_has_no_edit(api: Harness) -> None:
    response = await api.client.get("/projects/00000000-0000-4000-8000-000000000000/edit")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "edit_not_ready"
