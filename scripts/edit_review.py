#!/usr/bin/env python3
"""Make the deferred edit-taste review (verify-05 criterion 14): a real edit to judge.

Runs against your own library: the newest project with clips (or --project), a
30 s edit (--seconds) in the Moody look (--theme), cut to the top-recommended
track. If your music folder has no analysed track, a track is GENERATED into it
(``repcut generated 128 bpm.wav``) - Repcut never downloads music.

Writes ``$DATA_DIR/reviews/prompt-05/``: ``edit.mp4`` and ``edit.html`` (the
gate's question, every AI decision on one line, the cut list with its beat
error and footage scores, and the ducking measured on the rendered music). The
page names files relative to itself, and is deleted if an absolute path got in.
Nothing leaves the machine; no grading choice or taste event is written (the
theme is applied for this render only).

    .venv/Scripts/python.exe scripts/edit_review.py [--project UUID] [--seconds 30] [--theme moody]
"""

from __future__ import annotations

import argparse
import asyncio
import html
import re
import sys
import wave
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "engine"))

from repcut.config import detect_sync_root, get_settings  # noqa: E402
from repcut.db import create_engine, create_session_factory  # noqa: E402
from repcut.db.migrations import upgrade_to_head  # noqa: E402
from repcut.db.models import MediaFile, MusicTrack, MusicTrackStatus, Project  # noqa: E402
from repcut.edit import ducking, service  # noqa: E402
from repcut.edit.types import Decisions  # noqa: E402
from repcut.grading import apply as grading  # noqa: E402
from repcut.grading.packs import PackLibrary  # noqa: E402
from repcut.grading.service import clip_grade, luts_root  # noqa: E402
from repcut.media.ffmpeg_builder import build_edit_mux, build_music_stem, render  # noqa: E402
from repcut.music import library  # noqa: E402
from repcut.music.recommend import BPM_HIGH, BPM_LOW  # noqa: E402
from repcut.music.synthetic import generate_track, to_wav_bytes  # noqa: E402
from sqlalchemy import select  # noqa: E402 - after the path insert
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

GENERATED_NAME = "repcut generated {bpm} bpm.wav"
QUESTION = "Do the cuts land where you would have cut, or merely where the beat is?"


def _rms_db(path: Path, start: float, end: float) -> float | None:
    with wave.open(str(path)) as handle:
        rate = handle.getframerate()
        frames = np.frombuffer(handle.readframes(handle.getnframes()), dtype="<i2")
    chunk = frames.reshape(-1, 2).astype(float)[int(start * rate) : int(end * rate)]
    if chunk.size == 0:
        return None
    rms = float(np.sqrt(np.mean(chunk**2)))
    return 20 * np.log10(rms) if rms > 0 else None


async def _ensure_music(session: AsyncSession, data_dir: Path, bpm: int) -> None:
    files = await library.scan(session, data_dir)
    if not await service.library(session):
        has_pending = any(
            row.status == MusicTrackStatus.PENDING
            for row in (await session.execute(select(MusicTrack))).scalars()
        )
        if not has_pending:
            track = generate_track(
                bpm=float(bpm), bars=64, intro_bars=8, build_bars=8, drop_bars=24
            )
            target = library.library_root(data_dir) / GENERATED_NAME.format(bpm=bpm)
            target.write_bytes(to_wav_bytes(track.samples, track.sample_rate))
            print(f"no analysed track in your music folder: generated one ({bpm} BPM)")
            files = await library.scan(session, data_dir)
    pending = (
        await session.execute(
            select(MusicTrack).where(
                MusicTrack.status == MusicTrackStatus.PENDING, MusicTrack.present.is_(True)
            )
        )
    ).scalars()
    for row in list(pending):
        if row.sha256 in files:
            print(f"analysing track: {row.display_name}")
            await library.analyse_track(session, data_dir, row, files[row.sha256])


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
    out = data_dir / "reviews" / "prompt-05"
    shots_dir = out / "shots"
    shots_dir.mkdir(parents=True, exist_ok=True)
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
            theme = PackLibrary().load(service.PACK_ID).theme(arguments.theme)
            # A generated track at the theme's own tempo, so the review judges the
            # cutting, not a tempo the recommender already calls a stretch.
            await _ensure_music(
                session, data_dir, round(BPM_LOW + (BPM_HIGH - BPM_LOW) * theme.pace.energy)
            )
            for clip in await service.clips_without_signals(session, data_dir, project.id):
                print(f"measuring clip: {clip.display_name}")
                await service.measure_clip(session, data_dir, clip)

            decisions = Decisions(target_seconds=float(arguments.seconds))
            state = await service.build(session, data_dir, project.id, decisions, theme=theme)
            plan = state.plan
            clips = {c.sha256: c for c in await service.project_clips(session, project.id)}
            grades = {sha: await clip_grade(session, data_dir, clip) for sha, clip in clips.items()}

            lines = []
            for index, shot in enumerate(plan.shots):
                grade = grades[shot.sha256]
                scene = next(
                    (
                        s
                        for s in grade.scenes
                        if s.unit.start_seconds <= shot.source_in < s.unit.end_seconds
                    ),
                    grade.scenes[0],
                )
                target = shots_dir / f"shot_{index:03d}.mp4"
                print(f"rendering shot {index + 1} of {len(plan.shots)}")
                await grading.render_preview(
                    grade.clip.proxy,
                    target,
                    start_seconds=shot.source_in,
                    end_seconds=shot.source_out,
                    correction=scene.correction,
                    theme=theme,
                    pack_id=service.PACK_ID,
                    intensity=theme.default_intensity,
                    luts_root=luts_root(data_dir),
                )
                lines.append(f"file 'shots/{target.name}'")
            listing = out / "shots.txt"
            listing.write_text("\n".join(lines) + "\n", encoding="utf-8")

            stem: Path | None = None
            track_name = "none (cut to the theme's pace)"
            reason = ""
            if plan.track_sha256 is not None:
                track = await library.find_file(data_dir, plan.track_sha256)
                if track is None:
                    print("the chosen track is no longer in the music folder")
                    return 1
                track_name = track.display_name
                reason = next(
                    (r.reason for r in state.recommendations if r.sha256 == plan.track_sha256), ""
                )
                stem = out / "music.wav"
                plain = out / "music-undocked.wav"
                for target_stem, expression in (
                    (stem, ducking.gain_expression(list(state.ramps))),
                    (plain, "0"),
                ):
                    await render(
                        build_music_stem(
                            track.path,
                            target_stem,
                            start_seconds=plan.music_start_seconds,
                            duration_seconds=plan.duration_seconds,
                            gain_db_expression=expression,
                        ),
                        dry_run_first=False,
                    )
            await render(
                build_edit_mux(
                    listing, stem, out / "edit.mp4", duration_seconds=plan.duration_seconds
                ),
                dry_run_first=False,
            )
    finally:
        await engine.dispose()

    speech = [r for r in state.ramps if r.gain_db < 0]
    ducks = []
    if stem is not None:
        # The same window of the same track, ducked and not: the music's own
        # dynamics cancel out, which a "before vs during" comparison would not.
        for ramp in speech:
            under = _rms_db(stem, ramp.start, ramp.end)
            undocked = _rms_db(out / "music-undocked.wav", ramp.start, ramp.end)
            if under is not None and undocked is not None:
                ducks.append(undocked - under)
    by_id = {s.id: s for s in state.segments}
    weakest = sorted(
        plan.shots, key=lambda s: by_id[s.segment_id].quality if s.segment_id in by_id else 0
    )
    rows = "\n".join(
        f"<tr><td>{plan.shots.index(shot) + 1}</td>"
        f"<td>{html.escape(clips[shot.sha256].display_name)}</td>"
        f"<td>{shot.source_in:.2f}-{shot.source_out:.2f}</td>"
        f"<td>{shot.timeline_in:.2f}</td>"
        f"<td>{by_id[shot.segment_id].quality:.2f}</td>"
        f"<td>{by_id[shot.segment_id].energy:.2f}</td></tr>"
        for shot in weakest
        if shot.segment_id in by_id
    )
    notes = "".join(f"<li>{html.escape(w)}</li>" for w in state.warnings) or "<li>none</li>"
    made = (
        f"target {plan.target_seconds:.0f} s, made {plan.duration_seconds:.2f} s, "
        f"{len(plan.shots)} shots, music from {plan.music_start_seconds:.2f} s of the track"
    )
    snapped = "yes" if plan.snapped else "no (no music)"
    measured_speech = (
        "measured" if state.speech_measured else "NOT measured (run scripts/fetch_models.py)"
    )
    depth = ", ".join(f"{d:.1f} dB" for d in ducks) or "n/a"
    decisions_html = "".join(
        f"<li>{item}</li>"
        for item in (
            f"Theme: {html.escape(theme.name)} (for this review only; no choice was written)",
            f"Track: {html.escape(track_name)} - {html.escape(reason)}",
            f"Length: {made}",
            f"Cuts on the beat grid: {snapped}; usable footage: {len(state.segments)} segments",
            f"Speech: {measured_speech}; ducks: {len(speech)}; measured depth: {depth}",
        )
    )
    page = f"""<!doctype html><meta charset="utf-8"><title>Repcut - edit review</title>
<style>body{{background:#111;color:#ddd;font:14px system-ui;max-width:980px;margin:2em auto}}
td,th{{padding:2px 10px;text-align:left}} video{{width:100%;max-height:70vh}}</style>
<h1>{html.escape(QUESTION)}</h1>
<video src="edit.mp4" controls></video>
<h2>Every AI decision</h2><ul>{decisions_html}</ul>
<h2>Notes from the engine</h2><ul>{notes}</ul>
<h2>Shots, weakest footage first</h2>
<table><tr><th>#</th><th>clip</th><th>source s</th><th>at s</th><th>quality</th><th>energy</th></tr>
{rows}</table>
<p>Built on unverified: the tone-map operator (STOP A) and the themes (STOP B) are unsigned.</p>
"""
    page_path = out / "edit.html"
    page_path.write_text(page, encoding="utf-8")
    if str(data_dir) in page or re.search(r"[A-Za-z]:\\\\|/home/|/Users/", page):
        page_path.unlink()
        print("the page would contain an absolute path; not written")
        return 1
    print("written: $DATA_DIR/reviews/prompt-05/edit.html and edit.mp4")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--project", default=None)
    parser.add_argument("--seconds", type=float, default=30.0)
    parser.add_argument("--theme", default="moody")
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
