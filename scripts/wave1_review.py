#!/usr/bin/env python3
"""Make the Wave 1 review (verify-06 criterion 12): your session, exported, to judge.

The whole loop on your own library, the way the guide's gate describes it: the
newest project with clips (or --project), analysed footage, the Moody look (for
this render only: no grading choice or taste event is written), a 30 s edit cut
to the top-recommended track (generated into your music folder if it has no
analysed track - Repcut never downloads music), captions transcribed on this
machine, and a Reel (9:16) export.

Writes ``$DATA_DIR/reviews/prompt-06/``: ``wave1.mp4`` and ``wave1.html`` (the
question, every AI decision, the captions, the warnings and every stage's
time). The page names files relative to itself and is deleted if an absolute
path got in. Nothing leaves the machine.

    .venv/Scripts/python.exe scripts/fetch_models.py
    .venv/Scripts/python.exe scripts/wave1_review.py [--project UUID] [--seconds 30] [--theme moody]

Resumable: run it again after a crash and finished shots are reused.
"""

from __future__ import annotations

import argparse
import asyncio
import html
import json
import re
import shutil
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "engine"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from edit_review import _ensure_music  # noqa: E402
from repcut.captions import service as captions  # noqa: E402
from repcut.captions.transcribe import CaptionModelUnavailableError  # noqa: E402
from repcut.config import detect_sync_root, get_settings  # noqa: E402
from repcut.db import create_engine, create_session_factory  # noqa: E402
from repcut.db.migrations import upgrade_to_head  # noqa: E402
from repcut.db.models import MediaFile, Project  # noqa: E402
from repcut.edit import service as edit_service  # noqa: E402
from repcut.edit.types import Decisions  # noqa: E402
from repcut.export import service as export_service  # noqa: E402
from repcut.export.presets import config  # noqa: E402
from repcut.export.render import render_export  # noqa: E402
from repcut.grading.packs import PackLibrary  # noqa: E402
from repcut.music.recommend import BPM_HIGH, BPM_LOW  # noqa: E402
from sqlalchemy import select  # noqa: E402

QUESTION = "Would you post this tonight?"


async def run(arguments: argparse.Namespace) -> int:
    settings = get_settings()
    data_dir = settings.data_dir
    provider = detect_sync_root(data_dir)
    if provider is not None:
        print(f"DATA_DIR is inside a {provider} folder; the review holds real footage. Not run.")
        return 1
    await asyncio.to_thread(upgrade_to_head, settings)
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    out = data_dir / "reviews" / "prompt-06"
    out.mkdir(parents=True, exist_ok=True)
    stages: dict[str, float] = {}
    clock = time.perf_counter()

    def mark(stage: str) -> None:
        nonlocal clock
        stages[stage] = round(time.perf_counter() - clock, 1)
        clock = time.perf_counter()

    try:
        async with factory() as session:
            if arguments.project:
                project = await session.get(Project, arguments.project)
            else:
                project = (
                    (
                        await session.execute(
                            select(Project)
                            .join(MediaFile, MediaFile.project_id == Project.id)
                            .order_by(Project.updated_at.desc())
                            .limit(1)
                        )
                    )
                    .scalars()
                    .first()
                )
            if project is None:
                print("no project with clips; upload your session first")
                return 1
            theme = PackLibrary().load(edit_service.PACK_ID).theme(arguments.theme)
            await _ensure_music(
                session, data_dir, round(BPM_LOW + (BPM_HIGH - BPM_LOW) * theme.pace.energy)
            )
            for clip in await edit_service.clips_without_signals(session, data_dir, project.id):
                print(f"measuring clip: {clip.display_name}")
                await edit_service.measure_clip(session, data_dir, clip)
            mark("music + footage signals")
            notes: list[str] = []
            for clip in await edit_service.project_clips(session, project.id):
                try:
                    print(f"transcribing clip: {clip.display_name}")
                    await captions.transcribe_clip(session, data_dir, clip)
                except CaptionModelUnavailableError as error:
                    notes.append(str(error))
                    break
            mark("captions (transcription)")
            decisions = Decisions(target_seconds=float(arguments.seconds))
            state = await edit_service.build(session, data_dir, project.id, decisions, theme=theme)
            reel = config().preset("reel")
            inputs, _ = await export_service.gather(
                session, data_dir, project.id, preset=reel, theme_id=theme.id, edit=state
            )
            caption_state = await captions.state(session, data_dir, project.id, state)
            mark("edit + inputs")
            work = export_service.export_dir(data_dir, project.id) / "work"

            def progress(step: str, fraction: float) -> None:
                print(f"\r{step}: {fraction:5.1%}", end="", flush=True)

            result = await render_export(inputs, work, out / "wave1.mp4", on_progress=progress)
            print()
            mark("export")
    finally:
        await engine.dispose()

    plan = state.plan
    track = next((r for r in state.recommendations if r.sha256 == plan.track_sha256), None)
    decisions_html = "".join(
        f"<li>{html.escape(item)}</li>"
        for item in (
            f"Theme: {theme.name} (for this review only; no choice was written)",
            f"Track: {track.display_name} - {track.reason}" if track else "Track: none",
            f"Edit: {plan.duration_seconds:.1f} s, {len(plan.shots)} shots, "
            f"cut {'on the beat' if plan.snapped else 'at the theme pace'}",
            f"Captions: {caption_state.style.name}, {len(caption_state.lines)} lines, "
            f"languages {', '.join(caption_state.languages) or 'none'}, "
            f"{caption_state.dropped} likely-invented lines left out",
            f"Format: {reel.label} {reel.width}x{reel.height}, centre crop (no nudges)",
        )
    )
    lines_html = (
        "".join(
            f"<tr><td>{line.start:.1f}</td><td>{html.escape(line.text)}</td></tr>"
            for line in caption_state.lines
        )
        or "<tr><td colspan=2>no captions</td></tr>"
    )
    warnings = [*notes, *result.warnings, *result.problems]
    notes_html = "".join(f"<li>{html.escape(w)}</li>" for w in warnings) or "<li>none</li>"
    timing_rows = "".join(
        f"<tr><td>{html.escape(k)}</td><td>{v:.1f} s</td></tr>" for k, v in stages.items()
    ) + "".join(
        f"<tr><td>export: {html.escape(k)}</td><td>{v / 1000:.1f} s</td></tr>"
        for k, v in result.timings_ms.items()
    )
    page = f"""<!doctype html><meta charset="utf-8"><title>Repcut - Wave 1 review</title>
<style>body{{background:#111;color:#ddd;font:14px system-ui;max-width:980px;margin:2em auto}}
td,th{{padding:2px 10px;text-align:left}} video{{max-height:80vh}}</style>
<h1>{html.escape(QUESTION)}</h1>
<video src="wave1.mp4" controls></video>
<h2>Every AI decision</h2><ul>{decisions_html}</ul>
<h2>Notes from the engine</h2><ul>{notes_html}</ul>
<h2>Captions</h2><table><tr><th>at s</th><th>text</th></tr>{lines_html}</table>
<h2>Stage times</h2><table>{timing_rows}</table>
<p>Shots rendered {result.segments_rendered}, reused {result.segments_reused}.
Built on unverified: the tone-map operator (STOP A) and the themes (STOP B) are unsigned.</p>
"""
    page_path = out / "wave1.html"
    page_path.write_text(page, encoding="utf-8")
    (out / "wave1.json").write_text(json.dumps({"stages_s": stages, **result.to_json()}, indent=2))
    if str(data_dir) in page or re.search(r"[A-Za-z]:\\\\|/home/|/Users/", page):
        page_path.unlink()
        print("the page would contain an absolute path; not written")
        return 1
    shutil.rmtree(out / "shots", ignore_errors=True)
    print("written: $DATA_DIR/reviews/prompt-06/wave1.html and wave1.mp4")
    return 0 if not result.problems else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--project", default=None)
    parser.add_argument("--seconds", type=float, default=30.0)
    parser.add_argument("--theme", default="moody")
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
