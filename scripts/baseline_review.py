"""Build the Phase A colour-baseline review page (amendment 012, STOP A).

Usage:
    python scripts/baseline_review.py HDR[:TWIN] [HDR[:TWIN] ...]

Each argument names a clip already in the library by the first characters of
its sha256 - never a path. ``TWIN`` is the HDR-off take of the same scene: the
phone's own SDR rendering, and the reference every candidate is judged against.

For each pair, at matched relative timestamps, the page shows the twin, the
proxy under every candidate tone-map operator, the frame Gemini is sent, and
the v1 proxy, each with mean luma, mean saturation, black level (1st percentile
luma) and clipped highlights. Weakest clip first. It never says anything looks
good: it lays out what to compare and the numbers, and a person decides.

**Where it writes.** The stills are the user's footage. The repository sits in
a cloud-synced folder on this machine, so the page and its stills go to
``$DATA_DIR/reviews/prompt-04/baseline.html`` - outside the repository, and
outside any sync root (amendment 004; re-checked here). Nothing is written
into the repository, and nothing leaves the machine.

**No absolute path, anywhere.** A ``$DATA_DIR`` path carries the OS username
(`.claude/rules/secrets.md`), so the page names its stills relative to itself,
the terminal prints ``$DATA_DIR`` literally rather than expanding it, and the
page is checked for absolute paths before this exits - and deleted if one got
in.
"""

from __future__ import annotations

import asyncio
import html
import json
import re
import sqlite3
import subprocess
import sys
from dataclasses import dataclass, replace
from pathlib import Path

import cv2
import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "engine"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import verify_04_checks as v4  # noqa: E402 - after the sys.path insert it depends on
from repcut.config import detect_sync_root, get_settings  # noqa: E402 - same
from repcut.media.artifacts import NORMALISATION, NormalisationRecipe  # noqa: E402 - same
from repcut.media.ffmpeg_builder import (  # noqa: E402 - same
    build_frame_extraction,
    normalise_to_sdr,
    proxy_dimensions,
    run,
)
from repcut.media.metadata import parse_color_properties  # noqa: E402 - same
from repcut.media.store import absolute  # noqa: E402 - same

PAGE_NAME = "baseline.html"
# Anything that would put this machine's layout, and so the username, on the page.
_ABSOLUTE = re.compile(r"[A-Za-z]:[\\/]|file:|/[Uu]sers/|/home/")
RELATIVE_TIMES = (0.2, 0.5, 0.8)
# Every CPU `tonemap` operator at the current nominal peak, plus the two most
# plausible at BT.2408's HLG reference white (amendment 012 row 4).
_OPERATORS = ("hable", "mobius", "reinhard", "clip", "linear", "gamma")
_PREFIX = re.compile(r"^[0-9a-f]{6,64}$")
_FFMPEG = ("ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y")


# The page's own style and prose. HTML ignores the line breaks.
_HEAD = """<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Colour baseline</title><style>
:root{color-scheme:dark;--bg:#0f1115;--panel:#181b22;--line:#2a2f3a;
  --fg:#e8eaf0;--muted:#9aa3b2}
body{margin:0;padding:24px 16px;background:var(--bg);color:var(--fg);
  font:14px/1.5 system-ui,sans-serif}
main{max-width:1600px;margin:auto} h1{font-size:22px}
h2{font-size:17px;margin-top:40px} h3{font-size:14px;color:var(--muted)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:12px}
figure{margin:0;background:var(--panel);border:1px solid var(--line);
  border-radius:8px;overflow:hidden}
img{width:100%;display:block;background:#000}
figcaption{padding:8px;font-size:12px} figcaption span{color:var(--muted)}
table{border-collapse:collapse}
td,th{border:1px solid var(--line);padding:4px 10px;text-align:left}
.note{background:var(--panel);border:1px solid var(--line);border-radius:8px;
  padding:12px 16px}
</style></head><body><main>
"""

_INTRO = """<h1>Colour baseline - pick the tone-map operator</h1>
<div class=note>
<p>For each HDR clip: its HDR-off twin (the phone's own SDR rendering of the
same scene), the new proxy under every candidate operator, the frame Gemini is
sent (current operator), and the old v1 proxy. Timestamps are matched by
position in the clip. Clips are ordered by how far the current operator lands
from the twin, largest first. Numbers: mean luma, mean HSV saturation, black
level (1st-percentile luma), % of pixels at luma &ge; 250. All on 0-255.
&Delta; twin sums the luma, saturation and black-level gaps.</p>
<p>What to look at: blacks (milky or crushed), skin (grey, orange or right),
bright lights (rolled off, or flat white patches), and whether the whole
picture sits at the twin's brightness. Judge on the images; the numbers say
where to look.</p>
</div>
<h2>On a synthetic HLG pattern (verify-04 criterion 1)</h2>
<p>A lavfi pattern encoded to 10-bit HLG with SDR white at 203 cd/m&sup2;
(BT.2408), compared with the SDR original. It has no highlights above diffuse
white, so it rewards operators that leave SDR-range content alone and penalises
ones that keep headroom for highlights. Real footage has highlights; the twins
below are the real judge.</p>
"""


def candidates() -> list[tuple[str, NormalisationRecipe]]:
    current = NORMALISATION
    found = [
        (f"{op} @ npl {current.nominal_peak}", replace(current, operator=op)) for op in _OPERATORS
    ]
    found += [
        (f"{op} @ npl 203", replace(current, operator=op, nominal_peak=203))
        for op in ("hable", "mobius")
    ]
    label = f"{current.operator} @ npl {current.nominal_peak}"
    found.sort(key=lambda item: item[0] != label)
    return found


@dataclass
class Clip:
    sha256: str
    source: Path
    width: int
    height: int
    duration: float
    primaries: str | None
    transfer: str | None
    codec: str
    matrix: str | None
    colour_range: str | None


def resolve(prefix: str, connection: sqlite3.Connection, data_dir: Path) -> Clip:
    if not _PREFIX.match(prefix):
        raise SystemExit(f"{prefix!r} is not a sha256 prefix (6-64 lowercase hex characters)")
    rows = connection.execute(
        "select sha256, stored_path, display_width, display_height, duration_seconds, video_codec "
        "from media_blobs where sha256 like ?",
        (f"{prefix}%",),
    ).fetchall()
    if len(rows) != 1:
        raise SystemExit(f"{prefix} matches {len(rows)} clips; give more characters")
    sha, stored, width, height, duration, codec = rows[0]
    source = absolute(data_dir, stored)
    document = json.loads(
        subprocess.run(
            ["ffprobe", "-v", "error", "-show_streams", "-of", "json", source.as_posix()],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        ).stdout
    )
    colour = parse_color_properties(document)
    return Clip(
        sha,
        source,
        int(width),
        int(height),
        float(duration),
        colour.color_primaries,
        colour.color_transfer,
        str(codec),
        colour.color_space,
        colour.color_range,
    )


def _still(source: Path, at: float, graph: str, destination: Path) -> Path:
    subprocess.run(
        [
            *_FFMPEG,
            "-ss",
            f"{at:.3f}",
            "-i",
            source.as_posix(),
            "-frames:v",
            "1",
            "-vf",
            graph,
            destination.as_posix(),
        ],
        capture_output=True,
        check=True,
        timeout=300,
    )
    return destination


_TO_RGB = "scale=in_color_matrix=bt709:in_range=tv:out_range=pc,format=rgb24"


def render_candidate(clip: Clip, at: float, recipe: NormalisationRecipe, destination: Path) -> Path:
    """The v2 proxy's picture at ``at`` under ``recipe``: the proxy graph, then read as bt709."""
    width, height = proxy_dimensions(clip.width, clip.height)
    stages = [f"scale={width}:{height}"]
    normalise = normalise_to_sdr(clip.primaries, clip.transfer, recipe, output_range="tv")
    if normalise is not None:
        stages.append(normalise)
    return _still(clip.source, at, ",".join([*stages, _TO_RGB]), destination)


def render_v1(clip: Clip, at: float, destination: Path) -> Path:
    """What Prompt 03's proxy showed: no tone-map, pixels read under its bt709 tag."""
    return _still(
        clip.source,
        at,
        f"{v4._V1_PROXY_FILTER.split(',')[0]},format=yuv420p,{_TO_RGB}",
        destination,
    )


def render_twin(clip: Clip, at: float, destination: Path) -> Path:
    width, height = proxy_dimensions(clip.width, clip.height)
    return _still(
        clip.source, at, f"scale={width}:{height},scale=out_range=pc,format=rgb24", destination
    )


def render_gemini_frame(clip: Clip, at: float, destination: Path) -> Path:
    """The frame the analysis actually sends: the shipped extraction, unchanged."""
    command = build_frame_extraction(
        clip.source,
        destination,
        timestamp_seconds=at,
        color_primaries=clip.primaries,
        color_transfer=clip.transfer,
        color_space=clip.matrix,
        color_range=clip.colour_range,
    )
    asyncio.run(run(command))
    return destination


def _read_bgr(image: Path) -> np.ndarray:
    """A still as BGR, or a named failure: ``cv2.imread`` returns None rather than raising."""
    bgr = cv2.imread(str(image), cv2.IMREAD_COLOR)
    if bgr is None:
        raise SystemExit(f"could not read the rendered still {image.name}")
    return bgr


def metrics(image: Path) -> dict[str, float]:
    bgr = _read_bgr(image)
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB).astype(np.float32)
    luma = 0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]
    saturation = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)[..., 1].astype(np.float32)
    return {
        "luma": float(luma.mean()),
        "saturation": float(saturation.mean()),
        "black": float(np.percentile(luma, 1)),
        "clipped": float((luma >= 250).mean() * 100),
    }


def distance(a: dict[str, float], b: dict[str, float]) -> float:
    return (
        abs(a["luma"] - b["luma"])
        + abs(a["saturation"] - b["saturation"])
        + abs(a["black"] - b["black"])
    )


def synthetic_errors(out: Path) -> list[tuple[str, float, float]]:
    """Each candidate's error on criterion 1's synthetic HLG fixture, and v1's."""
    reference = v4.encode_sdr_reference(out / "fixture-sdr.mp4")
    hlg = out / "fixture-hlg.mp4"
    v4.encode_hlg(reference, hlg)
    truth = v4.frame_rgb(reference, width=1280, height=720)
    fixture = Clip(
        "fixture", hlg, 1280, 720, 3.0, "bt2020", "arib-std-b67", "hevc", "bt2020nc", "tv"
    )
    v1 = v4._mae(
        v4.frame_rgb(v4.render_v1_proxy(hlg, out / "fixture-v1.mp4"), width=1280, height=720), truth
    )
    rows = [("v1 proxy (no tone-map)", v1, 1.0)]
    for label, recipe in candidates():
        still = render_candidate(
            fixture, v4._COMPARE_AT_S, recipe, out / f"fixture-{_slug(label)}.png"
        )
        image = cv2.cvtColor(_read_bgr(still), cv2.COLOR_BGR2RGB).astype(np.float32)
        error = float(np.abs(image - truth).mean())
        rows.append((label, error, error / v1))
    return rows


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _card(label: str, image: Path, values: dict[str, float], note: str = "") -> str:
    numbers = (
        f"luma {values['luma']:.0f} · sat {values['saturation']:.0f} · "
        f"black {values['black']:.0f} · clipped {values['clipped']:.1f}%"
    )
    return (
        f'<figure><img src="{html.escape(image.name)}" alt="{html.escape(label)}" loading="lazy">'
        f"<figcaption><b>{html.escape(label)}</b>{html.escape(note)}<br><span>{numbers}</span></figcaption></figure>"
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

    pairs: list[tuple[Clip, Clip | None]] = []
    for argument in arguments:
        hdr_prefix, _, twin_prefix = argument.partition(":")
        hdr = resolve(hdr_prefix, connection, settings.data_dir)
        twin = resolve(twin_prefix, connection, settings.data_dir) if twin_prefix else None
        pairs.append((hdr, twin))

    sections: list[tuple[float, str]] = []
    for hdr, twin in pairs:
        span = min(hdr.duration, twin.duration if twin else hdr.duration)
        rows: list[str] = []
        gaps: list[float] = []
        for fraction in RELATIVE_TIMES:
            at = span * fraction
            tag = f"{hdr.sha256[:8]}-{int(fraction * 100)}"
            cards: list[str] = []
            twin_values = None
            if twin is not None:
                still = render_twin(twin, at, out / f"{tag}-twin.png")
                twin_values = metrics(still)
                cards.append(_card("HDR-off twin (reference)", still, twin_values))
            for index, (label, recipe) in enumerate(candidates()):
                still = render_candidate(hdr, at, recipe, out / f"{tag}-{_slug(label)}.png")
                values = metrics(still)
                note = " — current" if index == 0 else ""
                if twin_values is not None:
                    gap = distance(values, twin_values)
                    note += f" · Δ twin {gap:.0f}"
                    if index == 0:
                        gaps.append(gap)
                cards.append(_card(f"v2 proxy · {label}", still, values, note))
            frame = render_gemini_frame(hdr, at, out / f"{tag}-gemini.jpg")
            cards.append(_card("Frame sent to Gemini (current operator)", frame, metrics(frame)))
            v1 = render_v1(hdr, at, out / f"{tag}-v1.png")
            cards.append(_card("v1 proxy (Prompt 03)", v1, metrics(v1)))
            rows.append(
                f"<h3>{fraction:.0%} through · {at:.1f}s</h3><div class=grid>{''.join(cards)}</div>"
            )
        score = sum(gaps) / len(gaps) if gaps else float("-inf")
        twin_line = (
            f"twin {twin.sha256[:8]} · {twin.codec} · {twin.primaries}/{twin.transfer}"
            if twin
            else "no twin given — nothing to judge against; listed last"
        )
        sections.append(
            (
                score,
                f"<section><h2>{hdr.sha256[:8]} · {hdr.codec} · {hdr.primaries}/{hdr.transfer} · "
                f"{hdr.width}x{hdr.height} · {hdr.duration:.0f}s</h2><p>{html.escape(twin_line)}"
                + (f" · mean Δ twin under the current operator: {score:.0f}" if gaps else "")
                + f"</p>{''.join(rows)}</section>",
            )
        )
    sections.sort(key=lambda item: item[0], reverse=True)

    synthetic = synthetic_errors(out)
    table = "".join(
        f"<tr><td>{html.escape(label)}</td><td>{error:.1f}</td><td>{ratio:.2f}</td></tr>"
        for label, error, ratio in synthetic
    )
    rule = f"ratio <= {v4.ERROR_FRACTION_MAX} and error <= {v4.ERROR_ABSOLUTE_MAX}"
    page = (
        _HEAD
        + _INTRO
        + f"<p>Criterion 1 passes an operator at {rule}.</p>"
        + "<table><tr><th>operator</th><th>mean abs error</th><th>ratio to v1</th></tr>"
        + table
        + "</table>"
        + "".join(body for _, body in sections)
        + "</main></body></html>"
    )
    leaks = {settings.data_dir.as_posix(), str(settings.data_dir), str(Path.home())}
    if _ABSOLUTE.search(page) or any(leak in page for leak in leaks):
        print("the page would contain an absolute path; not written")
        return 1
    (out / PAGE_NAME).write_text(page, encoding="utf-8")
    print(f"wrote the review for {len(pairs)} clip(s).")
    print(f"open $DATA_DIR/reviews/prompt-04/{PAGE_NAME} in a browser")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
