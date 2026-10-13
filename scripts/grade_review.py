"""Build the Phase B grading review (amendment 012, STOP B): what Ashwin judges.

Usage:
    python scripts/grade_review.py SHA [SHA ...]

Each argument names a clip already in the library by the first characters of
its sha256 - never a path. For every clip, at three scenes (or three points in
an unscened clip), the page shows the corrected-only picture and all five gym
themes at their default intensity, graded exactly as the editor grades them
(the proxy, the scene's measured correction, the scene's lighting tags). Then,
for the first clip:

- ``sample-30s.mp4``: the first 30 s under the recommended theme, graded scene
  by scene and joined - what one video under one look actually looks like;
- full-resolution stills of one moment under every theme, normalised from the
  source the way export will (Prompt 06's contract), for judging grain;
- ``contact-sheet.jpg``: every still above in one image, for the phone.

Skin numbers: OpenCV's bundled face detector finds faces in the corrected-only
still; the page prints each theme's mean hue drift inside them (amendment 012
row 9: the real-footage half of the skin check). Numbers, never a verdict.

**Where it writes**: ``$DATA_DIR/reviews/prompt-04/``, never the repository
(the stills are the user's footage; the repository is in a synced folder).
Refuses a ``$DATA_DIR`` inside a sync root, prints ``$DATA_DIR`` literally,
and deletes the page if an absolute path got into it. Nothing leaves the
machine (P4). The tone-map operator under every still is PROVISIONAL until
STOP A is signed.
"""

from __future__ import annotations

import asyncio
import colorsys
import html
import json
import re
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "engine"))

from repcut.analysis.params import SCENE_PARAMS_VERSION  # noqa: E402 - after the path insert
from repcut.analysis.pipeline import GEMINI_PROMPT_VERSION  # noqa: E402 - same
from repcut.config import detect_sync_root, get_settings  # noqa: E402 - same
from repcut.grading import apply as grading  # noqa: E402 - same
from repcut.grading.corrector import SceneTags  # noqa: E402 - same
from repcut.grading.packs import PackLibrary, ensure_lut  # noqa: E402 - same
from repcut.grading.service import SceneUnit, clip_recommendation  # noqa: E402 - same
from repcut.media.artifacts import NORMALISATION, PARAMS_VERSION, ArtifactKind  # noqa: E402
from repcut.media.ffmpeg_builder import (  # noqa: E402 - same
    build_grade_filter,
    convert_colour,
    jfif,
    normalise_to_sdr,
    source_colour,
    working_space,
)
from repcut.media.metadata import parse_color_properties  # noqa: E402 - same
from repcut.media.store import absolute  # noqa: E402 - same

PAGE_NAME = "grading.html"
SAMPLE_SECONDS = 30.0
_ABSOLUTE = re.compile(r"[A-Za-z]:[\\/]|file:|/[Uu]sers/|/home/|/sessions/")
_PREFIX = re.compile(r"^[0-9a-f]{6,64}$")
_FFMPEG = ("ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y")

_HEAD = """<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Grading review</title><style>
:root{color-scheme:dark;--bg:#0f1115;--panel:#181b22;--line:#2a2f3a;
  --fg:#e8eaf0;--muted:#9aa3b2}
body{margin:0;padding:24px 16px;background:var(--bg);color:var(--fg);
  font:14px/1.5 system-ui,sans-serif}
main{max-width:1600px;margin:auto} h1{font-size:22px}
h2{font-size:17px;margin-top:40px} h3{font-size:14px;color:var(--muted)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:12px}
figure{margin:0;background:var(--panel);border:1px solid var(--line);
  border-radius:8px;overflow:hidden}
img,video{width:100%;display:block;background:#000}
figcaption{padding:8px;font-size:12px} figcaption span{color:var(--muted)}
.note{background:var(--panel);border:1px solid var(--line);border-radius:8px;
  padding:12px 16px}
</style></head><body><main>
"""

_INTRO = """<h1>Grading review - STOP B</h1>
<div class=note>
<p><b>Tone-map operator: PROVISIONAL</b> (STOP A is unsigned). Every still
here sits on it; a different pick re-tunes the themes.</p>
<p>Per clip, three scenes: the corrected-only picture (exposure, white balance
and noise fixed, no look), then each theme at its default intensity. The
theme the engine would suggest for the clip is marked. Then a 30-second
sample under that theme, and full-resolution stills for grain.</p>
<p>Questions for the checklist: does the corrected-only picture read as the
footage with the lights fixed and nothing more? Does one theme hold across
the dim and the bright scenes? Would you post this grade? Judge on the phone
too (<code>contact-sheet.jpg</code>).</p>
</div>
"""


@dataclass
class Clip:
    sha256: str
    source: Path
    proxy: Path
    duration: float
    codec: str
    primaries: str | None
    transfer: str | None
    matrix: str | None
    colour_range: str | None
    units: list[SceneUnit]


def resolve(prefix: str, connection: sqlite3.Connection, data_dir: Path) -> Clip:
    if not _PREFIX.match(prefix):
        raise SystemExit(f"{prefix!r} is not a sha256 prefix (6-64 lowercase hex characters)")
    rows = connection.execute(
        "select sha256, stored_path, duration_seconds, video_codec from media_blobs "
        "where sha256 like ?",
        (f"{prefix}%",),
    ).fetchall()
    if len(rows) != 1:
        raise SystemExit(f"{prefix} matches {len(rows)} clips; give more characters")
    sha, stored, duration, codec = rows[0]
    proxy_row = connection.execute(
        "select stored_path from derived_artifacts where sha256 = ? and artifact_kind = ? "
        "and params_version = ?",
        (sha, ArtifactKind.PROXY.value, PARAMS_VERSION[ArtifactKind.PROXY]),
    ).fetchone()
    if proxy_row is None:
        raise SystemExit(f"{prefix}: no current proxy yet - open the clip in the editor first")
    scene_rows = connection.execute(
        "select s.id, s.sequence_index, s.start_seconds, s.end_seconds, s.energy_score, "
        "g.lighting_temperature, g.lighting_quality, g.raw_response_json "
        "from scenes s left join gemini_scene_cache g on g.scene_id = s.id "
        "and g.gemini_prompt_version = ? where s.sha256 = ? and s.detector_params_version = ? "
        "order by s.sequence_index",
        (GEMINI_PROMPT_VERSION, sha, SCENE_PARAMS_VERSION),
    ).fetchall()
    units = [
        SceneUnit(
            id=row[0], index=row[1], start_seconds=row[2], end_seconds=row[3],
            analysed=SceneTags(row[5], row[6]) if row[7] is not None else SceneTags(),
            energy_score=row[4],
        )
        for row in scene_rows
    ] or [
        SceneUnit(id=None, index=0, start_seconds=0.0, end_seconds=float(duration),
                  analysed=SceneTags(), energy_score=None)
    ]  # fmt: skip
    source = absolute(data_dir, stored)
    document = json.loads(
        subprocess.run(
            ["ffprobe", "-v", "error", "-show_streams", "-of", "json", source.as_posix()],
            capture_output=True, text=True, check=True, timeout=60,
        ).stdout
    )  # fmt: skip
    colour = parse_color_properties(document)
    return Clip(
        sha, source, absolute(data_dir, proxy_row[0]), float(duration), str(codec),
        colour.color_primaries, colour.color_transfer, colour.color_space, colour.color_range,
        units,
    )  # fmt: skip


def pick_units(units: list[SceneUnit]) -> list[SceneUnit]:
    """Three scenes spread across the clip: the darkest, middle and brightest are unknown yet."""
    if len(units) >= 3:
        return [units[0], units[len(units) // 2], units[-1]]
    if len(units) == 1:
        u = units[0]
        third = u.duration / 3
        return [
            SceneUnit(u.id, i, u.start_seconds + i * third, u.start_seconds + (i + 1) * third,
                      u.analysed, u.energy_score)
            for i in range(3)
        ]  # fmt: skip
    return units


def faces(image: np.ndarray) -> list[tuple[int, int, int, int]]:
    cascade = cv2.CascadeClassifier(
        str(Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml")  # type: ignore[attr-defined]
    )
    grey = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    found = cascade.detectMultiScale(grey, scaleFactor=1.1, minNeighbors=6, minSize=(40, 40))
    return [tuple(int(v) for v in box) for box in found]  # type: ignore[misc]


def face_hue(image: np.ndarray, boxes: list[tuple[int, int, int, int]]) -> float | None:
    if not boxes:
        return None
    pixels = np.concatenate(
        [image[y + h // 4 : y + 3 * h // 4, x + w // 4 : x + 3 * w // 4].reshape(-1, 3)
         for x, y, w, h in boxes]
    ).astype(np.float64) / 255.0  # fmt: skip
    b, g, r = pixels.mean(axis=0)
    return colorsys.rgb_to_hsv(r, g, b)[0] * 360.0


def full_res_still(clip: Clip, at: float, grade: object, destination: Path) -> Path:
    """The source, normalised as export will normalise it, then the same grade chain."""
    working = working_space(NORMALISATION, "tv")
    normalise = normalise_to_sdr(
        clip.primaries, clip.transfer, NORMALISATION, output_range="tv",
        color_space=clip.matrix, color_range=clip.colour_range,
    )  # fmt: skip
    if normalise is None:
        normalise = convert_colour(
            source_colour(working, color_space=clip.matrix, color_range=clip.colour_range,
                          color_primaries=clip.primaries, color_transfer=clip.transfer),
            working,
        )  # fmt: skip
    chain = (
        f"{normalise},format=yuv420p,{build_grade_filter(grade)},"  # type: ignore[arg-type]
        f"{convert_colour(working, jfif(NORMALISATION))},format=yuvj444p"
    )
    subprocess.run(
        [*_FFMPEG, "-ss", f"{at:.3f}", "-i", clip.source.as_posix(), "-vf", chain,
         "-frames:v", "1", "-q:v", "2", destination.as_posix()],
        check=True, timeout=300,
    )  # fmt: skip
    return destination


def sample(clip: Clip, theme_id: str, out: Path, library: PackLibrary) -> Path:
    """The first 30 s under one theme, each scene with its own correction, joined."""
    pack = library.load("gym")
    theme = pack.theme(theme_id)
    parts: list[Path] = []
    end = min(SAMPLE_SECONDS, clip.duration)
    for unit in clip.units:
        start, stop = unit.start_seconds, min(unit.end_seconds, end)
        if start >= end or stop - start < 0.2:
            continue
        correction = asyncio.run(
            grading.scene_correction(clip.proxy, start_seconds=start, end_seconds=stop,
                                     tags=unit.analysed, theme=theme)
        )  # fmt: skip
        part = out / f"part-{len(parts):03d}.mp4"
        asyncio.run(
            grading.render_preview(
                clip.proxy, part, start_seconds=start, end_seconds=stop, correction=correction,
                theme=theme, pack_id="gym", intensity=theme.default_intensity,
                luts_root=out / "luts",
            )
        )  # fmt: skip
        parts.append(part)
    listing = out / "parts.txt"
    listing.write_text("".join(f"file '{p.name}'\n" for p in parts), encoding="utf-8")
    target = out / "sample-30s.mp4"
    subprocess.run(
        [*_FFMPEG, "-f", "concat", "-safe", "0", "-i", listing.name, "-c", "copy", target.name],
        check=True, timeout=300, cwd=out,
    )  # fmt: skip
    for p in [*parts, listing]:
        p.unlink(missing_ok=True)
    return target


def _card(label: str, name: str, note: str = "") -> str:
    return (
        f"<figure><img src='{html.escape(name)}' alt='{html.escape(label)}' loading=lazy>"
        f"<figcaption>{html.escape(label)}<br><span>{html.escape(note)}</span></figcaption></figure>"
    )


def main(arguments: list[str]) -> int:
    if not arguments:
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    settings = get_settings()
    provider = detect_sync_root(settings.data_dir)
    if provider is not None:
        print(f"$DATA_DIR is inside a {provider} folder; refusing to write footage stills there")
        return 1
    out = settings.data_dir / "reviews" / "prompt-04"
    out.mkdir(parents=True, exist_ok=True)
    database = settings.resolved_database_url.split(":///", 1)[1]
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    clips = [resolve(prefix, connection, settings.data_dir) for prefix in arguments]
    library = PackLibrary()
    pack = library.load("gym")

    sections: list[str] = []
    sheet_rows: list[np.ndarray] = []
    skin_lines: list[str] = []
    first_recommended = ""
    for clip in clips:
        recommendation = clip_recommendation(pack, [(u, u.analysed) for u in clip.units])
        first_recommended = first_recommended or recommendation.theme_id
        rows: list[str] = []
        for unit in pick_units(clip.units):
            at = unit.start_seconds + unit.duration / 2
            tag = f"{clip.sha256[:8]}-{int(at * 10):05d}"
            cards: list[str] = []
            row_images: list[np.ndarray] = []
            base_boxes: list[tuple[int, int, int, int]] = []
            base_hue: float | None = None
            raw = pack.theme("raw-plus")
            for label, theme, intensity in [("corrected only", raw, 0)] + [
                (t.name, t, t.default_intensity) for t in pack.themes
            ]:
                correction = asyncio.run(
                    grading.scene_correction(clip.proxy, start_seconds=unit.start_seconds,
                                             end_seconds=unit.end_seconds, tags=unit.analysed,
                                             theme=theme)
                )  # fmt: skip
                name = f"{tag}-{'only' if intensity == 0 and theme is raw else theme.id}.jpg"
                asyncio.run(
                    grading.render_still(
                        clip.proxy, out / name, timestamp_seconds=at, correction=correction,
                        theme=theme, pack_id="gym", intensity=intensity, luts_root=out / "luts",
                    )
                )  # fmt: skip
                image = cv2.imread(str(out / name))
                note = (
                    f"{correction.exposure_stops:+.2f} stop {correction.temperature_shift_k:+.0f} K"
                    f" denoise {correction.denoise:.2f} · intensity {intensity}"
                )
                if image is not None:
                    row_images.append(
                        cv2.resize(image, (320, int(320 * image.shape[0] / image.shape[1])))
                    )
                    if base_hue is None and label == "corrected only":
                        base_boxes = faces(image)
                        base_hue = face_hue(image, base_boxes)
                    elif base_hue is not None:
                        hue = face_hue(image, base_boxes)
                        if hue is not None:
                            drift = abs((hue - base_hue + 180.0) % 360.0 - 180.0)
                            note += f" · face hue drift {drift:.1f} deg"
                            skin_lines.append(
                                f"{tag} {theme.id}: {drift:.1f} deg over {len(base_boxes)} face(s)"
                            )
                if theme.id == recommendation.theme_id and intensity:
                    label += " (suggested)"
                cards.append(_card(label, name, note))
            light = unit.analysed
            tags = f"{light.lighting_temperature or '-'} / {light.lighting_quality or '-'}"
            rows.append(
                f"<h3>scene at {at:.1f}s · lighting tags {html.escape(tags)} · "
                f"faces found: {len(base_boxes)}</h3><div class=grid>{''.join(cards)}</div>"
            )
            if row_images:
                height = min(i.shape[0] for i in row_images)
                sheet_rows.append(np.hstack([i[:height] for i in row_images]))
        sections.append(
            f"<section><h2>{clip.sha256[:8]} · {clip.codec} · {clip.primaries}/{clip.transfer} · "
            f"{clip.duration:.0f}s · suggested theme: {recommendation.theme_id}</h2>{''.join(rows)}"
            "</section>"
        )

    first = clips[0]
    video = sample(first, first_recommended, out, library)
    grain_cards: list[str] = []
    at = min(first.duration / 2, SAMPLE_SECONDS / 2)
    for theme in pack.themes:
        correction = asyncio.run(
            grading.scene_correction(first.proxy, start_seconds=max(at - 1, 0), end_seconds=at + 1,
                                     tags=SceneTags(), theme=theme)
        )  # fmt: skip
        lut = ensure_lut(out / "luts", "gym", theme)
        name = f"{first.sha256[:8]}-full-{theme.id}.jpg"
        full_res_still(first, at, grading.grade_spec(correction, theme,
                       intensity=theme.default_intensity, lut=lut), out / name)  # fmt: skip
        grain_cards.append(
            _card(f"{theme.name}, full resolution", name, f"grain {theme.finish.grain}")
        )
    if sheet_rows:
        width = max(r.shape[1] for r in sheet_rows)
        padded = [np.pad(r, ((0, 0), (0, width - r.shape[1]), (0, 0))) for r in sheet_rows]
        cv2.imwrite(
            str(out / "contact-sheet.jpg"), np.vstack(padded), [cv2.IMWRITE_JPEG_QUALITY, 88]
        )

    skin = "".join(f"<li>{html.escape(line)}</li>" for line in skin_lines) or (
        "<li>no faces found by the detector in the corrected-only stills</li>"
    )
    page = (
        _HEAD + _INTRO
        + f"<h2>Skin on real footage (face detector)</h2><ul>{skin}</ul>"
        + "".join(sections)
        + f"<h2>{first.sha256[:8]}: 30 s under {html.escape(first_recommended)}</h2>"
        + f"<video src='{video.name}' controls muted playsinline></video>"
        + "<h2>Full resolution, for grain</h2><div class=grid>" + "".join(grain_cards) + "</div>"
        + "</main></body></html>"
    )  # fmt: skip
    leaks = {settings.data_dir.as_posix(), str(settings.data_dir), str(Path.home())}
    if _ABSOLUTE.search(page) or any(leak in page for leak in leaks):
        print("the page would contain an absolute path; not written")
        return 1
    (out / PAGE_NAME).write_text(page, encoding="utf-8")
    print(f"wrote the grading review for {len(clips)} clip(s); skin lines: {len(skin_lines)}")
    for line in skin_lines:
        print(f"  {line}")
    print(f"open $DATA_DIR/reviews/prompt-04/{PAGE_NAME} (and contact-sheet.jpg on the phone)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
