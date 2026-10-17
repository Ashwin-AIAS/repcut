"""Every platform from one edit: three files, each its own crop, each safe to post."""

import json
import subprocess
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx
import pytest
from conftest import Harness
from test_captions_export_api import small_exports  # noqa: F401 - fixture
from test_edit_api import _project

from repcut.export import service as export_service
from repcut.export.subjects import TRACK_VERSION, SubjectSample, SubjectTrack
from repcut.export.tracking import track_path


def _size(path: Path) -> tuple[int, int]:
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    stream = json.loads(probe.stdout)["streams"][0]
    return stream["width"], stream["height"]


async def test_export_all_platforms_from_one_edit(
    api: Harness,
    upload_clip: Callable[..., Awaitable[httpx.Response]],
    make_clip: Callable[..., Path],
    small_exports: None,  # noqa: F811
) -> None:
    project = await _project(api, upload_clip, make_clip, clips=2)
    short = await api.client.post(
        f"/projects/{project}/edit/overrides", json={"kind": "target_duration", "target_seconds": 5}
    )
    assert short.status_code == 200, short.text
    url = f"/projects/{project}/export"
    started = await api.client.post(f"{url}/all")
    assert started.status_code == 202, started.text
    await api.queue.drain()
    job = (await api.client.get(f"/jobs/{started.json()['job_ids'][0]}")).json()
    assert job["status"] == "succeeded", job
    settings = (await api.client.get(url)).json()
    for preset in export_service.config().presets:
        path = export_service.output_path(api.data_dir, project, preset)
        assert path.is_file(), preset.id
        manifest = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
        assert manifest["problems"] == [], (preset.id, manifest["problems"])
        assert _size(path) == (preset.width, preset.height)
    assert [p["id"] for p in settings["presets"]] == ["reel", "square", "landscape"]
    # The narrow presets were tracked; a track is numbers only, in the derived store.
    edit = (await api.client.get(f"/projects/{project}/edit")).json()
    sha = edit["shots"][0]["sha256"]
    stored = track_path(api.data_dir, sha, "movement")
    assert stored.is_file()
    assert set(json.loads(stored.read_text(encoding="utf-8"))) == {"detector", "version", "samples"}


async def test_a_scene_placed_by_hand_keeps_its_placement(
    api: Harness,
    upload_clip: Callable[..., Awaitable[httpx.Response]],
    make_clip: Callable[..., Path],
    small_exports: None,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    walking = SubjectTrack(
        "movement",
        TRACK_VERSION,
        tuple(SubjectSample(t / 5, 0.1 + 0.8 * t / 75, 0.5, 0.9) for t in range(75)),
    )

    async def fake(*_: object, **__: object) -> SubjectTrack:
        return walking

    monkeypatch.setattr(export_service, "ensure_track", fake)
    project = await _project(api, upload_clip, make_clip, clips=1)
    async with api.session_factory() as session:
        inputs, _ = await export_service.gather(session, api.data_dir, project)
    assert any(len(s.crop.x_keys) >= 2 for s in inputs.shots), "the subject is followed"
    url = f"/projects/{project}/export"
    for scene in (await api.client.get(url)).json()["scenes"]:
        await api.client.post(f"{url}/settings", json={"scene": scene["key"], "offset": -0.4})
    async with api.session_factory() as session:
        placed, _ = await export_service.gather(session, api.data_dir, project)
    assert all(s.crop.x_keys == () for s in placed.shots), "a hand-placed crop is not moved"
