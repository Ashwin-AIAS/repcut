#!/usr/bin/env python3
"""The reference review: their clip beside yours, matched, weakest scenes first.

For the deferred check (docs/manual-checks/prompt-08.md): "does mine feel like
theirs?". The page shows the reference and your exported edit side by side
(press play on both), then per scene of your footage its graded still under
the matched look beside the reference frame nearest to it in colour, worst
match first, with the measured numbers: the reference's pacing and what the
edit achieved, the look parameters and any held at a cap, the caption style
chosen and what still differs. Every matched part is listed so an override
carries information.

Two sources:

- your newest project with a matched reference (or ``--project UUID``). Stays
  on this machine; the page is written to ``$DATA_DIR/reviews/prompt-08/``
  (outside the repo, outside any synced folder).
- ``--synthetic``: generated clips only (a reference graded with Old School,
  plain footage), to see the page's shape. Nothing personal.

    .venv/Scripts/python.exe scripts/reference_review.py [--synthetic] [--project UUID]
"""

from __future__ import annotations

import argparse
import asyncio
import html
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "engine"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from repcut.config import detect_sync_root, get_settings  # noqa: E402
from repcut.grading.apply import read_measure_frames  # noqa: E402
from repcut.grading.lut import apply_look  # noqa: E402
from repcut.grading.packs import PackLibrary  # noqa: E402
from repcut.reference import look as lk  # noqa: E402

THUMB_HEIGHT = 300


@dataclass
class Row:
    label: str
    mine: np.ndarray
    theirs: np.ndarray
    distance: float


def _part(on: bool, what: str) -> str:
    return f"on, {what}" if on else "detached"


def _stats_distance(a: np.ndarray, b: np.ndarray) -> float:
    sa, _ = lk.colour_stats(lk.pixels_of([a]))
    sb, _ = lk.colour_stats(lk.pixels_of([b]))
    return float(np.abs(sa - sb).mean())


def _thumb(frame: np.ndarray) -> np.ndarray:
    h, w = frame.shape[:2]
    scale = THUMB_HEIGHT / h
    return cv2.resize(frame, (max(int(w * scale), 1), THUMB_HEIGHT))


def _pair(row: Row) -> np.ndarray:
    a, b = _thumb(row.mine), _thumb(row.theirs)
    gap = np.full((THUMB_HEIGHT, 8, 3), 24, np.uint8)
    return np.concatenate([a, gap, b], axis=1)


def _page(
    out: Path, rows: list[Row], facts: list[str], videos: list[tuple[str, str]], source: str
) -> Path:
    rows = sorted(rows, key=lambda r: -r.distance)
    parts = [
        "<!doctype html><meta charset='utf-8'><title>Reference review</title>",
        "<style>body{background:#111;color:#ddd;font:14px system-ui;margin:24px}"
        "img{display:block;margin:6px 0 18px}video{height:420px;margin-right:12px}"
        "li{margin:3px 0}.q{font-size:18px;color:#fff}</style>",
        f"<h1>Reference review</h1><p>Source: {html.escape(source)}.</p>",
        "<p class='q'>The question: does mine feel like theirs? Not just the same hue:"
        " the same look, the same rhythm, the same caption feel.</p>",
    ]
    if videos:
        parts.append("<h2>Side by side</h2><div>")
        for label, name in videos:
            parts.append(
                f"<figure style='display:inline-block'><video src='{name}' controls></video>"
                f"<figcaption>{html.escape(label)}</figcaption></figure>"
            )
        parts.append(
            "</div><button onclick=\"document.querySelectorAll('video')"
            '.forEach(v=>{v.currentTime=0;v.play()})">'
            "Play both from the start</button>"
        )
    parts.append("<h2>Every decision, one line each</h2><ul>")
    parts += [f"<li>{html.escape(f)}</li>" for f in facts]
    parts.append("</ul><h2>Scenes, weakest match first (left: yours, matched; right: theirs)</h2>")
    for index, row in enumerate(rows):
        name = f"pair-{index:02d}.jpg"
        cv2.imwrite(str(out / name), cv2.cvtColor(_pair(row), cv2.COLOR_RGB2BGR))
        parts.append(
            f"<p>{html.escape(row.label)} - colour distance {row.distance:.4f}</p>"
            f"<img src='{name}'>"
        )
    page = out / "index.html"
    page.write_text("\n".join(parts), encoding="utf-8")
    return page


def _synthetic(tmp: Path) -> tuple[list[Row], list[str]]:
    from reference_fixture import natural_frames

    look = PackLibrary().theme("gym", "old-school").look
    theirs = [
        (apply_look(f.reshape(-1, 3) / 255.0, look).reshape(f.shape) * 255).astype(np.uint8)
        for f in natural_frames(360, 640, 2.0, seed=4)[::15]
    ]
    mine_raw = natural_frames(360, 640, 2.0, seed=9)[::15]
    fit = lk.extract_look(lk.pixels_of(theirs), lk.pixels_of(mine_raw))
    rows = []
    for i, frame in enumerate(mine_raw):
        graded = (
            apply_look(frame.reshape(-1, 3) / 255.0, fit.look).reshape(frame.shape) * 255
        ).astype(np.uint8)
        nearest = min(theirs, key=lambda t: _stats_distance(graded, t))
        rows.append(
            Row(f"generated scene {i + 1}", graded, nearest, _stats_distance(graded, nearest))
        )
    facts = [
        f"look (fitted): {fit.look.model_dump(exclude={'pivot'})}",
        f"held at a cap: {list(fit.capped) or 'none'}",
    ]
    return rows, facts


async def _library(
    project: str | None, out: Path
) -> tuple[list[Row], list[str], list[tuple[str, str]], str] | None:
    from repcut.db import create_engine, create_session_factory
    from repcut.db.models import MediaFile, ReferenceMatch
    from repcut.edit import service as edit_service
    from repcut.grading import service as grading
    from repcut.reference import service as ref
    from sqlalchemy import select

    settings = get_settings()
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            statement = select(ReferenceMatch).order_by(ReferenceMatch.updated_at.desc())
            if project is not None:
                statement = statement.where(ReferenceMatch.project_id == project)
            match = (await session.execute(statement)).scalars().first()
            if match is None:
                return None
            reference = await session.get(MediaFile, match.reference_media_file_id)
            if reference is None:
                return None
            analysis = await ref.analyse(session, settings, reference)
            ref_clip = await grading.load_clip(session, settings.data_dir, reference)
            scenes = await ref._scenes(session, reference.sha256)
            spans = [(s.start_seconds, s.end_seconds) for s in scenes] or [
                (0.0, ref_clip.duration_seconds)
            ]
            theirs = await read_measure_frames(ref_clip.proxy, [(a + b) / 2 for a, b in spans])
            rows: list[Row] = []
            for clip in await ref.footage(session, match.project_id):
                try:
                    grade = await grading.clip_grade(session, settings.data_dir, clip)
                except grading.GradingNotReadyError:
                    continue
                for scene in grade.scenes:
                    still = await grading.ensure_still(settings.data_dir, grade, scene)
                    mine = cv2.cvtColor(cv2.imread(str(still)), cv2.COLOR_BGR2RGB)
                    nearest = min(theirs, key=lambda t: _stats_distance(mine, t))
                    rows.append(
                        Row(
                            f"{clip.display_name}, scene {scene.unit.index + 1} "
                            f"({scene.theme.name})",
                            mine,
                            nearest,
                            _stats_distance(mine, nearest),
                        )
                    )
            state = await edit_service.current(session, settings.data_dir, match.project_id)
            lengths = [s.timeline_out - s.timeline_in for s in state.plan.shots]
            facts = [
                f"look: {'on, theme ' + str(match.theme_id) if match.look_on else 'detached'}",
                f"pacing: {'on' if match.pacing_on else 'detached'}; reference shots median "
                f"{analysis.rhythm.shot_median:.2f} s, "
                f"{analysis.rhythm.cuts_per_10s:.1f} cuts/10 s; "
                f"edit shots median {float(np.median(lengths)) if lengths else 0:.2f} s",
                f"planner numbers: {analysis.pace.model_dump()}",
                f"captions: {_part(match.captions_on, 'style ' + str(match.caption_style_id))}; "
                f"reference: {analysis.captions.as_json()}",
                *[f"caption difference: {d}" for d in analysis.caption_match.differences],
                f"look held at a cap: {list(analysis.look.capped) or 'none'}",
                *[f"edit warning: {w}" for w in state.warnings],
            ]
            videos: list[tuple[str, str]] = []
            shutil.copyfile(ref_clip.proxy, out / "theirs.mp4")
            videos.append(("theirs (reference preview)", "theirs.mp4"))
            from repcut.export.service import export_dir

            exports = sorted(
                export_dir(settings.data_dir, match.project_id).glob("repcut-*.mp4"),
                key=lambda p: p.stat().st_mtime,
            )
            if exports:
                shutil.copyfile(exports[-1], out / "mine.mp4")
                videos.append(("yours (latest export)", "mine.mp4"))
            else:
                facts.append("no export yet: export the project once to see it side by side")
            return rows, facts, videos, "your newest matched project"
    finally:
        await engine.dispose()


async def run(arguments: argparse.Namespace) -> int:
    settings = get_settings()
    out = settings.data_dir / "reviews" / "prompt-08"
    if detect_sync_root(out) is not None:
        print("DATA_DIR is inside a cloud-sync folder; the review holds real footage. Not run.")
        return 1
    out.mkdir(parents=True, exist_ok=True)
    found = None if arguments.synthetic else await _library(arguments.project, out)
    with tempfile.TemporaryDirectory() as scratch:
        if found is None:
            rows, facts = _synthetic(Path(scratch))
            page = _page(out, rows, facts, [], "generated clips")
        else:
            rows, facts, videos, source = found
            page = _page(out, rows, facts, videos, source)
    print(
        f"wrote {page.relative_to(settings.data_dir).as_posix()} under DATA_DIR "
        f"({len(rows)} scenes)"
    )
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--synthetic", action="store_true")
    parser.add_argument("--project")
    return asyncio.run(run(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
