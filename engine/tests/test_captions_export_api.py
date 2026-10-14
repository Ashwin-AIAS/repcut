"""Captions and the export through the real engine: edit words, choose, render, download.

There is no speech-to-text model in CI (it is fetched on the person's machine),
so transcripts are written into the cache the way the model would, and the
model folder is stubbed for the one test that needs captions burned in. The
recogniser itself is tested in ``test_captions.py`` with a fake.
"""

import json
from collections.abc import Awaitable, Callable
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from conftest import Harness
from sqlalchemy import select
from test_edit_api import _library, _project, _scan

from repcut.captions import service as captions
from repcut.captions import transcribe as stt
from repcut.captions.types import CaptionSegment, Transcript, Word
from repcut.db.models import MediaFile, TasteEvent
from repcut.export import presets
from repcut.export import service as export_service


async def _kinds(api: Harness) -> list[str]:
    async with api.session_factory() as session:
        rows = await session.execute(select(TasteEvent).order_by(TasteEvent.created_at))
        return [row.kind for row in rows.scalars()]


async def _write_transcripts(api: Harness, project: str) -> None:
    async with api.session_factory() as session:
        clips = (
            await session.execute(select(MediaFile).where(MediaFile.project_id == project))
        ).scalars()
        for clip in clips:
            words = tuple(
                Word(t, t + 0.4, text, 0.9)
                for t, text in zip(
                    [x * 0.5 for x in range(24)], ['{"<b>', "drei", "\\N", "noch"] * 6, strict=True
                )
            )
            segment = CaptionSegment(0, 0.0, 12.0, words)
            transcript = Transcript(clip.sha256, "test", "de", (segment,))
            path = captions.transcript_path(api.data_dir, clip.sha256)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(transcript.to_json()), encoding="utf-8")


def _stub_model(data_dir: Path) -> None:
    folder = stt.model_dir(data_dir)
    folder.mkdir(parents=True, exist_ok=True)
    for name in (*stt.MODEL_FILES, stt.PINS_FILENAME):
        (folder / name).write_text("{}", encoding="utf-8")


@pytest.fixture
def small_exports(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 180x320 reel encoded ultrafast: the pipeline, not x264, is under test."""
    real = presets.config()
    small = replace(
        real,
        encode=replace(real.encode, preset="ultrafast", crf=30),
        presets=tuple(replace(p, width=p.width // 6, height=p.height // 6) for p in real.presets),
    )
    monkeypatch.setattr(export_service, "config", lambda: small)
    monkeypatch.setattr("repcut.api.export.config", lambda: small)


async def test_captions_are_edited_by_id_and_every_change_is_a_taste_event(
    api: Harness,
    upload_clip: Callable[..., Awaitable[httpx.Response]],
    make_clip: Callable[..., Path],
) -> None:
    project = await _project(api, upload_clip, make_clip)
    url = f"/projects/{project}/captions"
    empty = (await api.client.get(url)).json()
    assert empty["lines"] == [] and empty["model_installed"] is False
    assert "fetch_models" in empty["model_message"]
    assert [s["id"] for s in empty["styles"]] == ["punch", "classic", "minimal"]
    assert len(empty["clips_untranscribed"]) == 3
    # Nothing to transcribe without the model: the job fails with the fixed sentence.
    started = (await api.client.post(f"{url}/transcribe")).json()["job_ids"]
    assert len(started) == 3
    await api.queue.drain()

    await _write_transcripts(api, project)
    state = (await api.client.get(url)).json()
    assert state["lines"] and state["clips_untranscribed"] == [] and state["languages"] == ["de"]
    texts = {w["text"] for line in state["lines"] for w in line["words"]}
    assert '{"<b>' in texts and "\\N" in texts  # data, returned as data
    word = state["lines"][0]["words"][0]

    change = {"kind": "word", "word_id": word["id"], "text": " Hi\n"}
    edited = await api.client.post(url, json=change)
    assert edited.status_code == 200, edited.text
    first = edited.json()["lines"][0]["words"][0]
    assert first["text"] == "Hi" and first["original"] == word["text"] and first["edited"]
    line = state["lines"][0]["id"]
    hide = {"kind": "line", "line_id": line, "hidden": True}
    assert (await api.client.post(url, json=hide)).json()["lines"][0]["hidden"]
    minimal = {"kind": "style", "style_id": "minimal"}
    assert (await api.client.post(url, json=minimal)).json()["style_id"] == "minimal"
    off = {"kind": "enabled", "enabled": False}
    assert (await api.client.post(url, json=off)).json()["enabled"] is False
    for bad in (
        {"kind": "word", "word_id": "0" * 16 + "-9-9", "text": "x"},
        {"kind": "style", "style_id": "comic-sans"},
        {"kind": "line", "line_id": "0" * 16 + "-99", "hidden": True},
        {"kind": "word", "word_id": "../../etc", "text": "x"},
        {"kind": "style", "style_id": "punch", "extra": 1},
    ):
        response = await api.client.post(url, json=bad)
        assert response.status_code == 422, bad
    assert await _kinds(api) == [
        "caption_word",
        "caption_line_hidden",
        "caption_style",
        "captions_off",
    ]


async def test_an_export_renders_with_captions_downloads_and_resumes(
    api: Harness,
    upload_clip: Callable[..., Awaitable[httpx.Response]],
    make_clip: Callable[..., Path],
    small_exports: None,
) -> None:
    project = await _project(api, upload_clip, make_clip)
    _library(api.data_dir)
    await _scan(api)
    short = await api.client.post(
        f"/projects/{project}/edit/overrides", json={"kind": "target_duration", "target_seconds": 6}
    )
    assert short.status_code == 200, short.text
    await _write_transcripts(api, project)
    _stub_model(api.data_dir)

    url = f"/projects/{project}/export"
    settings = (await api.client.get(url)).json()
    assert settings["preset_id"] == "reel" and settings["latest"] is None
    assert [p["id"] for p in settings["presets"]] == ["reel", "square", "landscape"]
    scene = next(s for s in settings["scenes"] if s["in_edit"])
    nudged = await api.client.post(f"{url}/settings", json={"scene": scene["key"], "offset": 0.5})
    assert next(s for s in nudged.json()["scenes"] if s["key"] == scene["key"])["offset"] == 0.5
    for bad in ({"scene": "0" * 16 + ":clip", "offset": 0.1}, {"preset_id": "imax"}):
        assert (await api.client.post(f"{url}/settings", json=bad)).status_code == 422

    job = (await api.client.post(url)).json()["job_ids"]
    assert len(job) == 1
    await api.queue.drain()
    latest = (await api.client.get(url)).json()["latest"]
    assert latest is not None, (await api.client.get(f"/jobs/{job[0]}")).text
    assert latest["problems"] == [] and latest["segments_rendered"] == latest["segments_total"]
    assert set(latest["timings_ms"]) == {"plan", "segments", "music", "mux", "check"}

    file = await api.client.get(f"{url}/reel/file")
    assert file.status_code == 200 and file.headers["content-type"] == "video/mp4"
    assert "repcut-reel.mp4" in file.headers["content-disposition"]
    assert file.content[4:8] == b"ftyp"
    partial = await api.client.get(f"{url}/reel/file", headers={"range": "bytes=0-99"})
    assert partial.status_code == 206 and len(partial.content) == 100
    assert (await api.client.get(f"{url}/square/file")).status_code == 409
    assert (await api.client.get(f"{url}/..%2F..%2Fx/file")).status_code in (404, 422)

    # Again, unchanged: every shot is reused, nothing re-rendered.
    await api.client.post(url)
    await api.queue.drain()
    again = (await api.client.get(url)).json()["latest"]
    assert again["segments_rendered"] == 0 and again["segments_reused"] == latest["segments_total"]
    assert "reframe_offset" in await _kinds(api)
