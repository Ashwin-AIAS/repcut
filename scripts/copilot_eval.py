#!/usr/bin/env python3
"""The copilot's 20-intent eval on a generated project: replayed, or live.

    python scripts/copilot_eval.py            # replay: the fixture's scripted answers
    python scripts/copilot_eval.py --live     # the real model (free tier, ~30 requests)

Both build the same small project from generated clips and generated music in
a scratch directory (no footage, nothing downloaded), with two gym sets so
"the second one" really could mean a cut or a set. The engine that builds it
has no Gemini key - analysis never sends a frame - and in ``--live`` only the
copilot is given the key from ``.env``: what it sends is the request text and
the edit summary. The transcript is printed (``--out`` also writes it);
the exit code is 0 when the eval criterion holds.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ENGINE = REPO_ROOT / "engine"
sys.path.insert(0, str(ENGINE))

import httpx  # noqa: E402
from repcut.captions import service as captions  # noqa: E402
from repcut.captions.types import CaptionSegment, Transcript, Word  # noqa: E402
from repcut.config import Settings  # noqa: E402
from repcut.copilot import agent, evaluation  # noqa: E402
from repcut.db.models import GymSet, MediaFile  # noqa: E402
from repcut.main import app, start_engine, stop_engine  # noqa: E402
from repcut.music.synthetic import generate_track, to_wav_bytes  # noqa: E402
from sqlalchemy import select  # noqa: E402

INTENTS = ENGINE / "tests" / "fixtures" / "copilot_intents.json"


def _clip(path: Path, seconds: float) -> None:
    argv = [
        "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
        "-t", str(seconds), "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30",
        "-c:a", "aac", "-shortest", path.as_posix(),
    ]  # fmt: skip
    subprocess.run(argv, capture_output=True, check=True, timeout=120)


async def _upload(client: httpx.AsyncClient, project: str, path: Path) -> None:
    payload = await asyncio.to_thread(path.read_bytes)
    opened = await client.post(
        f"/projects/{project}/uploads",
        json={
            "display_name": path.name,
            "size_bytes": len(payload),
            "chunk_size_bytes": 1024 * 1024,
            "sha256": hashlib.sha256(payload).hexdigest(),
        },
    )
    opened.raise_for_status()
    upload = opened.json()["id"]
    offset = opened.json()["bytes_received"]
    while offset < len(payload):
        sent = await client.put(
            f"/uploads/{upload}/chunk",
            params={"offset": offset},
            content=payload[offset : offset + 1024 * 1024],
        )
        sent.raise_for_status()
        offset = sent.json()["bytes_received"]
    (await client.post(f"/uploads/{upload}/finalize")).raise_for_status()


async def _project(client: httpx.AsyncClient, scratch: Path, data_dir: Path) -> str:
    project = (await client.post("/projects", json={"name": "copilot eval"})).json()["id"]
    queue = app.state.job_queue
    for index in range(2):
        path = scratch / f"clip{index}.mp4"
        _clip(path, 8.0 + index)
        await _upload(client, project, path)
    await queue.drain()
    music = data_dir / "music"
    music.mkdir(parents=True, exist_ok=True)
    track = generate_track(bpm=128.0, bars=24, intro_bars=2, build_bars=2, drop_bars=12)
    (music / "drop.wav").write_bytes(to_wav_bytes(track.samples, track.sample_rate))
    (await client.post("/music/scan")).raise_for_status()
    await queue.drain()
    (await client.post(f"/projects/{project}/edit/signals")).raise_for_status()
    await queue.drain()
    async with app.state.session_factory() as session:
        clips = list(
            (
                await session.execute(select(MediaFile).where(MediaFile.project_id == project))
            ).scalars()
        )
        for number, clip in enumerate(clips):
            words = tuple(
                Word(t, t + 0.4, text, 0.9)
                for t, text in zip(
                    [x * 0.5 for x in range(12)], ["eins", "zwei", "drei", "vier"] * 3, strict=True
                )
            )
            transcript = Transcript(
                clip.sha256, "test", "de", (CaptionSegment(0, 0.0, 6.0, words),)
            )
            path = captions.transcript_path(data_dir, clip.sha256)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(transcript.to_json()), encoding="utf-8")
            # Two sets, so "the second one" is a real ambiguity for a real model.
            session.add(
                GymSet(
                    project_id=project,
                    sha256=clip.sha256,
                    segment_id=f"{clip.sha256[:16]}-0-{8000 + number * 1000}",
                    start_seconds=1.0,
                    end_seconds=6.0,
                    counter_version=1,
                )
            )
        await session.commit()
    (await client.get(f"/projects/{project}/edit")).raise_for_status()
    return str(project)


async def main(live: bool) -> evaluation.Result:
    scratch = Path(tempfile.mkdtemp(prefix="repcut-copilot-eval-"))
    settings = Settings(
        data_dir=scratch / "data",
        database_url=f"sqlite+aiosqlite:///{(scratch / 'engine.db').as_posix()}",
        gemini_api_key=None,
    )
    model: agent.Model | None = None
    if live:
        keyed = Settings()
        reason = agent.availability(keyed)
        if reason is not None:
            raise SystemExit(f"--live cannot run: {reason}")
        model = agent.GeminiModel(keyed)
    await start_engine(app, settings)
    result = evaluation.Result()
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
            project = await _project(client, scratch, settings.data_dir)
            for intent in evaluation.load(INTENTS):
                async with app.state.session_factory() as session:
                    verdict = await evaluation.judge(
                        session, settings.data_dir, project, intent, model
                    )
                result.verdicts.append(verdict)
    finally:
        await stop_engine(app)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--live", action="store_true", help="ask the real model (free tier)")
    parser.add_argument("--out", type=Path, help="also write the transcript here")
    args = parser.parse_args()
    outcome = asyncio.run(main(args.live))
    text = evaluation.transcript(outcome)
    print(text)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    problems = evaluation.failures(outcome)
    for problem in problems:
        print(f"FAILED: {problem}")
    print(f"MEASURED: {outcome.rate:.0%} correct ({'live' if args.live else 'replay'})")
    sys.exit(1 if problems else 0)
