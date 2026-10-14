#!/usr/bin/env python3
"""The slow-motion contact sheet: every candidate slowed, its verdict and its score.

For the deferred check (docs/manual-checks/prompt-07.md). For each moment it
shows the two captured frames, the invented frame between them, and the
leave-one-out check (the interpolation of a frame the camera DID record, next
to the real one), with the confidence and whether the shot was accepted or
reverted to normal speed. Accepted and reverted are listed apart.

Two sources:

- ``--synthetic`` (default when there is no library): the gate's generated
  gentle and barbell clips. Nothing personal; this is what the build report
  describes.
- your newest project (or ``--project UUID``): its top hero moments, from the
  real footage in your library. Stays on this machine; the page is written to
  ``$DATA_DIR/reviews/prompt-07/`` (outside the repo, outside OneDrive).

Needs torch (``make setup-gpu``) and the weights (``scripts/fetch_models.py``).

    .venv/Scripts/python.exe scripts/slowmo_review.py [--synthetic] [--project UUID]
"""

from __future__ import annotations

import argparse
import asyncio
import html
import importlib
import json
import re
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
from repcut.media import ffmpeg_builder as builder  # noqa: E402
from repcut.media.ffmpeg_builder import CropBox, ExportEncode  # noqa: E402
from repcut.media.store import absolute  # noqa: E402
from repcut.slowmo import confidence  # noqa: E402
from repcut.slowmo.rife import Interpolator, RifeInterpolator  # noqa: E402
from repcut.slowmo.shot import FrameShape, read_frames  # noqa: E402
from repcut.slowmo.weights import RIFE_STORED_PATH, WeightsUnavailableError, verify  # noqa: E402
from slowmo_fixture import FPS, write_clip  # noqa: E402

THUMB_HEIGHT = 320
# Captured frames per moment: half a second of source, one second slowed.
WINDOW_FRAMES = 16


@dataclass
class Moment:
    label: str
    source: Path
    start: float
    colour: dict[str, str | None]


@dataclass
class Row:
    label: str
    verdict: confidence.Verdict
    sheet: Path


def _thumb(frame: np.ndarray) -> np.ndarray:
    height, width = frame.shape[:2]
    size = (max(1, round(width * THUMB_HEIGHT / height)), THUMB_HEIGHT)
    small: np.ndarray = cv2.resize(frame, size, interpolation=cv2.INTER_AREA)
    return small


def _tile(frames: list[np.ndarray], captions: list[str]) -> np.ndarray:
    tiles = []
    for frame, caption in zip(frames, captions, strict=True):
        tile = _thumb(frame).copy()
        cv2.rectangle(tile, (0, 0), (tile.shape[1], 26), (0, 0, 0), -1)
        cv2.putText(tile, caption, (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
        tiles.append(tile)
    row: np.ndarray = np.concatenate(tiles, axis=1)
    return row


async def _frames(moment: Moment, encode: ExportEncode, crop: CropBox, raw: Path) -> np.ndarray:
    await builder.render(
        builder.build_export_frames(
            moment.source,
            raw,
            start_seconds=moment.start,
            frames=WINDOW_FRAMES,
            crop=crop,
            encode=encode,
            color_primaries=moment.colour.get("primaries"),
            color_transfer=moment.colour.get("transfer"),
            color_space=moment.colour.get("space"),
            color_range=moment.colour.get("range"),
        ),
        dry_run_first=False,
    )
    return read_frames(raw, FrameShape(encode.width, encode.height))


def _judge(captured: np.ndarray, rife: Interpolator, out: Path, label: str) -> Row:
    warp = confidence.warp_scores(captured, rife)
    middles = [rife.midpoint(captured[i], captured[i + 1]) for i in range(len(captured) - 1)]
    verdict = confidence.judge(warp, confidence.continuity_scores(captured, middles))
    centre = len(captured) // 2
    guess = rife.midpoint(captured[centre - 1], captured[centre + 1])
    sheet = _tile(
        [captured[centre - 1], middles[centre - 1], captured[centre], guess],
        ["captured", "invented (shown)", "captured (truth)", "check: invented"],
    )
    name = re.sub(r"[^a-z0-9]+", "-", label.casefold()).strip("-") + ".jpg"
    path = out / name
    cv2.imwrite(str(path), cv2.cvtColor(sheet, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 88])
    return Row(label, verdict, path)


def _page(rows: list[Row], out: Path, source: str) -> Path:
    def section(title: str, chosen: list[Row]) -> str:
        items = "".join(
            f"<li><h3>{html.escape(r.label)}: confidence {r.verdict.confidence:.3f}"
            f" (warp {r.verdict.warp_score:.3f}, continuity {r.verdict.continuity_score:.3f})</h3>"
            f"<p>{html.escape(r.verdict.reason)}</p><img src='{r.sheet.name}' alt=''></li>"
            for r in chosen
        )
        return f"<h2>{title} ({len(chosen)})</h2><ol>{items or '<li>none</li>'}</ol>"

    accepted = [r for r in rows if r.verdict.accepted]
    reverted = [r for r in rows if not r.verdict.accepted]
    page = out / "slowmo.html"
    page.write_text(
        "<!doctype html><meta charset='utf-8'><title>Slow-motion contact sheet</title>"
        "<style>body{font:14px system-ui;background:#111;color:#ddd;margin:24px}"
        "img{max-width:100%}li{margin:0 0 24px}</style>"
        f"<h1>Slow motion: accepted vs reverted ({html.escape(source)})</h1>"
        f"<p>Threshold {confidence.THRESHOLD} (PROVISIONAL: set on generated motion). "
        "Look at the invented frames: is anything bent, doubled or smeared that the "
        "camera did not see? Is a reverted moment one you would have wanted slowed?</p>"
        + section("Accepted: shown in slow motion", accepted)
        + section("Reverted: played at normal speed", reverted),
        encoding="utf-8",
    )
    if re.search(r"[A-Za-z]:[\\/]|/home/|/Users/", page.read_text(encoding="utf-8")):
        page.unlink()
        raise SystemExit("an absolute path got into the page; deleted it")
    return page


def _synthetic(tmp: Path, size: tuple[int, int]) -> list[Moment]:
    out = []
    for kind in ("gentle", "barbell"):
        clip = write_clip(kind, tmp / f"{kind}.mp4", size[0], size[1], 2.0)
        bt709: dict[str, str | None] = {
            "primaries": "bt709",
            "transfer": "bt709",
            "space": "bt709",
            "range": "tv",
        }
        out.append(Moment(f"generated {kind} clip", clip, 0.3, bt709))
    return out


async def _library_moments(project: str | None) -> list[Moment]:
    from repcut.db import create_engine, create_session_factory
    from repcut.db.models import MediaBlob, MediaFile, Project
    from repcut.edit import service
    from repcut.edit.types import Decisions
    from repcut.media.metadata import parse_color_properties
    from sqlalchemy import select

    settings = get_settings()
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    moments: list[Moment] = []
    try:
        async with factory() as session:
            if project is None:
                row = (
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
                project = row.id if row else None
            if project is None:
                return []
            state = await service.build(session, settings.data_dir, project, Decisions())
            names = {
                c.sha256: c.display_name for c in await service.project_clips(session, project)
            }
            for hero in state.heroes:
                blob = await session.get(MediaBlob, hero.sha256)
                if blob is None:
                    continue
                source = absolute(settings.data_dir, blob.stored_path)
                probe = await builder.run(builder.build_probe(source))
                colour = parse_color_properties(probe)
                moments.append(
                    Moment(
                        f"#{hero.rank} {names.get(hero.sha256, 'clip')} at {hero.peak:.1f} s",
                        source,
                        max(0.0, hero.peak - WINDOW_FRAMES / FPS / 2),
                        {
                            "primaries": colour.color_primaries,
                            "transfer": colour.color_transfer,
                            "space": colour.color_space,
                            "range": colour.color_range,
                        },
                    )
                )
    finally:
        await engine.dispose()
    return moments


async def run(arguments: argparse.Namespace) -> int:
    settings = get_settings()
    weights = absolute(settings.data_dir, str(RIFE_STORED_PATH))
    try:
        verify(weights)
        torch = importlib.import_module("torch")
    except (WeightsUnavailableError, ImportError) as error:
        print(f"cannot interpolate here: {error}")
        return 1
    width, height = (int(v) for v in arguments.size.split("x"))
    encode = ExportEncode(width, height, FPS, "veryfast", 18, "high", "4.2", 2)
    out = settings.data_dir / "reviews" / "prompt-07"
    if detect_sync_root(out) is not None:
        print("DATA_DIR is inside a cloud-sync folder; the review holds real footage. Not run.")
        return 1
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as scratch:
        tmp = Path(scratch)
        moments = [] if arguments.synthetic else await _library_moments(arguments.project)
        source = "your newest project" if moments else "generated clips"
        moments = moments or _synthetic(tmp, (width, height))
        rows: list[Row] = []
        with RifeInterpolator(torch, weights, preference=settings.torch_device) as rife:
            for index, moment in enumerate(moments):
                probe = await builder.run(builder.build_probe(moment.source))
                streams = json.loads(probe)["streams"]
                video = next(s for s in streams if s.get("codec_type") == "video")
                crop = _centre_crop(int(video["width"]), int(video["height"]), width / height)
                captured = await _frames(moment, encode, crop, tmp / f"{index}.rgb")
                print(f"judging {moment.label} ...")
                rows.append(_judge(captured, rife, out, moment.label))
        page = _page(rows, out, source)
    for row in rows:
        state = "accepted" if row.verdict.accepted else "reverted"
        print(f"{row.label}: {state}, confidence {row.verdict.confidence:.3f}")
    print(f"wrote {page.relative_to(settings.data_dir).as_posix()} under DATA_DIR")
    return 0


def _centre_crop(width: int, height: int, aspect: float) -> CropBox:
    from repcut.export.reframe import crop_for

    crop = crop_for(width, height, aspect)
    return CropBox(crop.width, crop.height, crop.x, crop.y)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Slow-motion contact sheet")
    parser.add_argument("--synthetic", action="store_true", help="generated clips only")
    parser.add_argument("--project", default=None)
    parser.add_argument("--size", default="1080x1920")
    arguments = parser.parse_args(argv)
    return asyncio.run(run(arguments))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
