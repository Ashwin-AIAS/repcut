#!/usr/bin/env python3
"""End to end, zero manual steps: three clips in, a postable 9:16 MP4 out.

Runs the real engine in this process (the same ``start_engine`` the server
boots) against a scratch ``$DATA_DIR``, through its HTTP routes:

    upload 3 generated clips -> ingest + analysis (no Gemini key: tags are
    null, a normal input) -> Moody on every clip -> a generated track in the
    music folder -> scan -> measure footage -> a short edit -> transcribe
    (local model) -> Reel preset -> export -> the platform check

The clips are generated (testsrc2: landscape 30 fps, portrait, and a
variable-frame-rate one); the first speaks a known English sentence when the
gate's test voice is installed (``fetch_models.py --gate``), otherwise a tone.
The speech-to-text model is read from your real ``$DATA_DIR/models`` and never
copied; nothing else of your data is touched. ``--no-captions`` turns captions
off for a diagnostic run where the model is missing; the gate never passes it.

Prints ``MEASURED:`` JSON and writes ``e2e.mp4`` + ``e2e.json`` into ``--out``.
Exit 0 only if every job succeeded and the file passed the platform check.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

import httpx
import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "engine"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from repcut.captions import transcribe as stt  # noqa: E402
from repcut.config import Settings, get_settings  # noqa: E402
from repcut.edit.vad import MODEL_STORED_PATH as VAD_MODEL  # noqa: E402
from repcut.main import app, start_engine, stop_engine  # noqa: E402
from repcut.media.store import absolute  # noqa: E402
from repcut.music.synthetic import generate_track, to_wav_bytes  # noqa: E402
from speech_fixture import FixtureUnavailableError, synthesize  # noqa: E402

IRREGULAR = "select='not(mod(n,3))+not(mod(n,7))'"


def _ffmpeg(*argv: str) -> None:
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y", *argv],
        check=True,
        timeout=300,
    )


def make_clips(folder: Path, speech: np.ndarray | None) -> list[Path]:
    voice = folder / "voice.wav"
    if speech is not None:
        with wave.open(str(voice), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(16000)
            handle.writeframes((np.clip(speech, -1, 1) * 32767).astype("<i2").tobytes())
    clips = []
    specs = (
        ("landscape.mp4", "1920x1080", 30, 12.0, False),
        ("portrait.mp4", "1080x1920", 30, 11.0, False),
        ("phone-vfr.mp4", "1280x720", 30, 10.0, True),
    )
    for index, (name, size, fps, seconds, vfr) in enumerate(specs):
        audio = (
            ["-stream_loop", "-1", "-i", str(voice)]
            if speech is not None and index == 0
            else ["-f", "lavfi", "-i", f"sine=frequency={330 + 110 * index}:sample_rate=48000"]
        )
        target = folder / name
        _ffmpeg(
            "-f", "lavfi", "-i", f"testsrc2=size={size}:rate={fps}", *audio, "-t", str(seconds),
            *(["-vf", IRREGULAR, "-fps_mode", "passthrough"] if vfr else []),
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "26", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(target),
        )  # fmt: skip
        clips.append(target)
    return clips


async def upload(client: httpx.AsyncClient, project: str, path: Path) -> dict[str, object]:
    payload = await asyncio.to_thread(path.read_bytes)
    opened = await client.post(
        f"/projects/{project}/uploads",
        json={
            "display_name": path.name,
            "size_bytes": len(payload),
            "chunk_size_bytes": 1 << 20,
            "sha256": hashlib.sha256(payload).hexdigest(),
        },
    )
    opened.raise_for_status()
    upload_id = opened.json()["id"]
    offset = 0
    while offset < len(payload):
        sent = await client.put(
            f"/uploads/{upload_id}/chunk",
            params={"offset": offset},
            content=payload[offset : offset + (1 << 20)],
        )
        sent.raise_for_status()
        offset = sent.json()["bytes_received"]
    final = await client.post(f"/uploads/{upload_id}/finalize")
    final.raise_for_status()
    result: dict[str, object] = final.json()
    return result


async def run(arguments: argparse.Namespace) -> int:
    real = get_settings().data_dir
    out = Path(arguments.out)
    await asyncio.to_thread(out.mkdir, parents=True, exist_ok=True)
    timings: dict[str, float] = {}
    clock = time.perf_counter()

    def mark(stage: str) -> None:
        nonlocal clock
        timings[stage] = round(time.perf_counter() - clock, 2)
        clock = time.perf_counter()

    with tempfile.TemporaryDirectory(prefix="repcut-e2e-", ignore_cleanup_errors=True) as scratch:
        root = Path(scratch)
        data_dir = root / "data"
        (data_dir / "models").mkdir(parents=True)
        vad = absolute(real, VAD_MODEL)
        if vad.is_file():
            shutil.copy2(vad, absolute(data_dir, VAD_MODEL))
        # Read the speech-to-text model where it lives; never copy 480 MB.
        stt.model_dir = lambda _data_dir: real.joinpath(*stt.MODEL_STORED_DIR.parts)  # type: ignore[assignment]
        if arguments.captions and not stt.model_installed(data_dir):
            print("MEASURED: " + json.dumps({"captions": "model missing"}))
            print(f"FAILED: {stt.MODEL_MISSING}")
            return 1
        try:
            speech = synthesize(real, "en")
        except FixtureUnavailableError as error:
            if arguments.captions:
                print("MEASURED: " + json.dumps({"speech": "no test voice"}))
                print(f"FAILED: {error}")
                return 1
            speech = None
        clips = make_clips(root, speech)
        music = data_dir / "music"
        music.mkdir(parents=True)
        track = generate_track(bpm=120.0, bars=48, intro_bars=4, build_bars=4, drop_bars=24)
        (music / "e2e generated 120 bpm.wav").write_bytes(
            to_wav_bytes(track.samples, track.sample_rate)
        )
        mark("fixtures")

        settings = Settings(
            data_dir=data_dir,
            database_url=f"sqlite+aiosqlite:///{(root / 'engine.db').as_posix()}",
            gemini_api_key=None,
        )
        await start_engine(app, settings)
        queue = app.state.job_queue
        failures: list[str] = []
        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://localhost", timeout=600
            ) as client:
                project = (await client.post("/projects", json={"name": "e2e"})).json()["id"]
                media = [await upload(client, project, clip) for clip in clips]
                await queue.drain()
                mark("upload+ingest+analysis")
                for item in media:
                    graded = await client.put(
                        f"/media/{item['media_file_id']}/grade", json={"theme_id": "moody"}
                    )
                    graded.raise_for_status()
                await client.post("/music/scan")
                await client.post(f"/projects/{project}/edit/signals")
                await queue.drain()
                edit = await client.post(
                    f"/projects/{project}/edit/overrides",
                    json={"kind": "target_duration", "target_seconds": arguments.seconds},
                )
                edit.raise_for_status()
                mark("grade+music+edit")
                captions_url = f"/projects/{project}/captions"
                if arguments.captions:
                    await client.post(f"{captions_url}/transcribe")
                    await queue.drain()
                else:
                    await client.post(captions_url, json={"kind": "enabled", "enabled": False})
                captions = (await client.get(captions_url)).json()
                mark("captions")
                await client.post(
                    f"/projects/{project}/export/settings", json={"preset_id": "reel"}
                )
                started = await client.post(f"/projects/{project}/export")
                started.raise_for_status()
                await queue.drain()
                mark("export")
                # A fresh database: every job in it is this run's.
                rows = (await client.get("/jobs", params={"limit": 200})).json()
                failures = [
                    f"{row['job_type']}: {row.get('error')}"
                    for row in rows
                    if row.get("status") == "failed"
                ]
                latest = (await client.get(f"/projects/{project}/export")).json()["latest"]
                edit_json = edit.json()
        finally:
            await stop_engine(app)

        output = data_dir / "projects" / project / "export" / "repcut-reel.mp4"
        if output.is_file():
            shutil.copy2(output, out / "e2e.mp4")
        summary = {
            "clips": len(clips),
            "edit_seconds": round(edit_json["duration_seconds"], 2),
            "shots": len(edit_json["shots"]),
            "caption_lines": len(captions.get("lines", [])),
            "languages": captions.get("languages", []),
            "latest": latest,
            "stage_seconds": timings,
            "failed_jobs": failures,
        }
        (out / "e2e.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(
        "MEASURED: "
        + json.dumps(
            {k: summary[k] for k in ("edit_seconds", "shots", "caption_lines", "stage_seconds")}
        )
    )
    if failures:
        print("FAILED: jobs failed: " + "; ".join(failures)[:300])
        return 1
    if latest is None or latest["problems"]:
        print("FAILED: no export, or it failed the platform check: " + json.dumps(latest)[:300])
        return 1
    if arguments.captions and speech is not None and not summary["caption_lines"]:
        print("FAILED: a clip speaking a known sentence produced no captions")
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True)
    parser.add_argument("--seconds", type=float, default=15.0)
    parser.add_argument("--no-captions", dest="captions", action="store_false")
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
