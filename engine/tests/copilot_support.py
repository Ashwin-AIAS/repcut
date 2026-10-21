"""Shared set-up for the copilot tests: a small real project, a scripted model.

The project is real (uploaded, analysed, measured, planned) but small: two
generated clips, one generated music track, transcripts written into the cache
the way the speech model would. The model is a script: each turn's answer is
given up front, so a test states what a model *said* and asserts what Repcut
*did* with it. Nothing reaches the network (conftest's ``_no_real_gemini``).
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from conftest import Harness
from sqlalchemy import select

from repcut.captions import service as captions
from repcut.captions.types import CaptionSegment, Transcript, Word
from repcut.copilot import history, tools
from repcut.db.models import MediaFile, TasteEvent
from repcut.music.synthetic import generate_track, to_wav_bytes

Call = tuple[str, Mapping[str, object]]


async def make_project(
    api: Harness,
    upload_clip: Callable[..., Awaitable[httpx.Response]],
    make_clip: Callable[..., Path],
    *,
    clips: int = 2,
    seconds: float = 8.0,
) -> str:
    """A planned project with a music library and transcripts. Returns its id."""
    project = (await api.client.post("/projects", json={"name": "copilot"})).json()["id"]
    for index in range(clips):
        clip = make_clip(f"clip{index}.mp4", seconds=seconds + index)
        finalized = await upload_clip(project, clip)
        assert finalized.status_code == 200, finalized.text
    await api.queue.drain()
    music = api.data_dir / "music"
    music.mkdir(parents=True, exist_ok=True)
    track = generate_track(bpm=128.0, bars=24, intro_bars=2, build_bars=2, drop_bars=12)
    (music / "drop.wav").write_bytes(to_wav_bytes(track.samples, track.sample_rate))
    assert (await api.client.post("/music/scan")).status_code == 202
    await api.queue.drain()
    assert (await api.client.post(f"/projects/{project}/edit/signals")).status_code == 202
    await api.queue.drain()
    await write_transcripts(api, project)
    assert (await api.client.get(f"/projects/{project}/edit")).status_code == 200
    return project  # type: ignore[no-any-return]


async def write_transcripts(api: Harness, project: str) -> None:
    async with api.session_factory() as session:
        rows = (
            await session.execute(select(MediaFile).where(MediaFile.project_id == project))
        ).scalars()
        for clip in rows:
            words = tuple(
                Word(t, t + 0.4, text, 0.9)
                for t, text in zip(
                    [x * 0.5 for x in range(12)], ["eins", "zwei", "drei", "vier"] * 3, strict=True
                )
            )
            segment = CaptionSegment(0, 0.0, 6.0, words)
            transcript = Transcript(clip.sha256, "test", "de", (segment,))
            path = captions.transcript_path(api.data_dir, clip.sha256)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(transcript.to_json()), encoding="utf-8")


async def state_text(api: Harness, project: str) -> str:
    """The project's decision rows, canonical: what 'byte-identical' is judged on."""
    async with api.session_factory() as session:
        return history.canonical(await history.snapshot(session, project))


async def event_ids(api: Harness, project: str) -> set[str]:
    async with api.session_factory() as session:
        return await history.event_ids(session, project)


async def events(api: Harness, project: str) -> list[TasteEvent]:
    async with api.session_factory() as session:
        statement = (
            select(TasteEvent)
            .where(TasteEvent.project_id == project)
            .order_by(TasteEvent.created_at, TasteEvent.id)
        )
        return list((await session.execute(statement)).scalars())


async def run(
    api: Harness,
    project: str,
    name: str,
    args: Mapping[str, object] | None = None,
    *,
    source: str = "ui",
    copilot: bool = False,
) -> tools.Outcome:
    """One registry operation in its own session, the way one request runs."""
    async with api.session_factory() as session:
        ctx = tools.ToolContext(session, api.data_dir, project, source=source)
        return await tools.execute(ctx, name, args or {}, copilot=copilot)


def answer(*calls: Call, text: str | None = None) -> dict[str, Any]:
    """One generateContent response: these function calls, or a text part."""
    parts: list[dict[str, Any]] = [{"functionCall": {"name": n, "args": dict(a)}} for n, a in calls]
    if text is not None:
        parts.append({"text": text})
    return {"candidates": [{"content": {"role": "model", "parts": parts}}]}


@dataclass
class ScriptedModel:
    """Answers each request with the next scripted response; records every request."""

    script: list[Mapping[str, Any]]
    requests: list[Mapping[str, Any]] = field(default_factory=list)
    unavailable_reason: str | None = None

    async def generate(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        self.requests.append(json.loads(json.dumps(request)))
        if not self.script:
            return answer(text="Done.")
        return self.script.pop(0)


def turns(*per_step: Sequence[Call]) -> list[Mapping[str, Any]]:
    """A script: one response per step, then a closing sentence."""
    return [*(answer(*step) for step in per_step), answer(text="Done.")]
