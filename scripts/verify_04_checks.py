"""Measurements behind `make verify-04`. One subcommand per gate criterion.

Same contract as `verify_02_checks.py` and `verify_03_checks.py`:

- exactly one ``MEASURED: <value>`` line on stdout, always
- ``FAILED: <reason>`` then exit 1; ``SKIPPED: <reason>`` then exit 2
- exit 0 only when the criterion actually holds

**Every colour claim is read from a rendered file** - ffprobe for tags, decoded
pixels for values - never from an argv. That is the lesson of
`docs/future-prompts/prompt-04-colour-baseline.md`: the argv was right and the
file was wrong, and a snapshot test of the argv passed the whole time.

**Fixtures** are generated at test time and never committed. Colour accuracy is
measured against a fixture that is *actually* HLG: a lavfi pattern encoded to
BT.2020/HLG 10-bit with ``zscale``, whose lavfi original is the SDR reference
(amendment 012). Prompt 03's fixture - SDR pixels with an HLG tag - has no
honest SDR reference, so it is used only where a tag is the point.

No absolute path is ever printed (`.claude/rules/secrets.md`).
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from numpy.typing import NDArray

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "engine"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import verify_02_checks as v2  # noqa: E402 - after the sys.path insert it depends on
import verify_03_checks as v3  # noqa: E402 - same
from verify_03_checks import failed, measured, skipped  # noqa: E402 - same

# The tag Prompt 03 closed on. Criterion 4 reads the versions it shipped from
# here rather than from a literal, so "above Prompt 03's" stays true when a later
# prompt bumps again (`.claude/rules/testing.md`).
PROMPT_03_TAG = "prompt-03-done"

# Criterion 1's tolerance, stated before the BT.2408 fixture was first measured
# (docs/reports/prompt-04.md): an HLG round trip through a tone-map is not an
# identity, so no absolute band is picked from a result. The v1 proxy's error
# against the SDR reference is the yardstick, and v2 must remove most of it AND
# land inside an absolute sanity bound. Mean absolute error, 0-255 RGB codes.
ERROR_FRACTION_MAX = 0.30
ERROR_ABSOLUTE_MAX = 20.0

# verify-03 criterion 11's band for a tone-mapped HLG fixture, reused as the
# criterion text requires.
LUMA_BAND = (40.0, 235.0)

# "SDR unchanged": an SDR source's proxy moves no more than this in mean luma
# from v1 to v2. Two codes absorbs x264's rate control on a different frame size.
SDR_LUMA_TOLERANCE = 2.0

# Criterion 6: what a person notices as "washed out" is lifted blacks and lost
# saturation. The v1 proxy of the same fixture misses both by far more than
# this (criterion 1 prints its error); v2 must land within it of the SDR
# reference, as the browser draws each.
BROWSER_SATURATION_TOLERANCE = 15.0
BROWSER_BLACK_TOLERANCE = 8.0
# The video must visibly play, not merely load: at least this many seconds of
# playback observed in 1.5s of wall clock.
BROWSER_MIN_PLAYBACK_S = 0.3

EXPECTED_TAGS = {
    "color_primaries": "bt709",
    "color_transfer": "bt709",
    "color_space": "bt709",
    "color_range": "tv",
    "pix_fmt": "yuv420p",
}

# Prompt 03's proxy filter, as frozen in engine/tests/test_ffmpeg_builder.py's
# RECIPE_ARGV[(PROXY, 1)]. Only the gate still renders it: it is criterion 1's
# negative control and yardstick.
_V1_PROXY_FILTER = "scale=-2:720,fps=30"

_FFMPEG = ("ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y")
_COMPARE_AT_S = 1.0


# --- fixtures -------------------------------------------------------------------


def encode_sdr_reference(
    destination: Path, *, width: int = 1280, height: int = 720, seconds: float = 3.0
) -> Path:
    """A bt709 lavfi pattern, near-lossless: the picture an HLG fixture should come back to."""
    subprocess.run(
        [
            *_FFMPEG,
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size={width}x{height}:rate=30",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000",
            "-t",
            str(seconds),
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "12",
            "-pix_fmt",
            "yuv420p",
            "-color_primaries",
            "bt709",
            "-color_trc",
            "bt709",
            "-colorspace",
            "bt709",
            "-color_range",
            "tv",
            "-x264-params",
            "colorprim=bt709:transfer=bt709:colormatrix=bt709",
            "-c:a",
            "aac",
            "-shortest",
            destination.as_posix(),
        ],
        capture_output=True,
        check=True,
        timeout=180,
    )
    return destination


def _has_encoder(name: str) -> bool:
    listed = subprocess.run(
        ["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=60
    ).stdout
    return re.search(rf"\s{re.escape(name)}\s", listed) is not None


def encode_hlg(reference: Path, destination: Path) -> str:
    """``reference`` re-encoded as real HLG/BT.2020 10-bit. Returns the codec used.

    ``npl=203`` on the *encode* side places SDR reference white at 203 cd/m², as
    ITU-R BT.2408 specifies for SDR content carried in HLG - the convention phone
    and broadcast HLG follow. HEVC Main 10 through libx265 when this FFmpeg has
    it, matching phone source; x264 High 10 otherwise. The colour description is
    written into the bitstream's VUI as well as the container, for the reason
    `engine/tests/conftest.py`'s ``_write_hdr_tags`` records.
    """
    vui = "colorprim=bt2020:transfer=arib-std-b67:colormatrix=bt2020nc"
    if _has_encoder("libx265"):
        codec = "hevc-main10"
        video = [
            "-c:v",
            "libx265",
            "-preset",
            "ultrafast",
            "-crf",
            "12",
            "-x265-params",
            f"{vui}:range=limited:log-level=error",
            "-tag:v",
            "hvc1",
        ]
    else:
        codec = "h264-high10"
        video = [
            "-c:v",
            "libx264",
            "-profile:v",
            "high10",
            "-preset",
            "ultrafast",
            "-crf",
            "12",
            "-x264-params",
            vui,
        ]
    subprocess.run(
        [
            *_FFMPEG,
            "-i",
            reference.as_posix(),
            "-map",
            "0",
            "-vf",
            "zscale=tin=bt709:min=bt709:pin=bt709:rin=tv:"
            "t=arib-std-b67:p=bt2020:m=bt2020nc:r=tv:npl=203,format=yuv420p10le",
            *video,
            "-color_primaries",
            "bt2020",
            "-color_trc",
            "arib-std-b67",
            "-colorspace",
            "bt2020nc",
            "-color_range",
            "tv",
            "-c:a",
            "copy",
            destination.as_posix(),
        ],
        capture_output=True,
        check=True,
        timeout=300,
    )
    return codec


def render_v1_proxy(source: Path, destination: Path) -> Path:
    """Prompt 03's proxy recipe, verbatim. The yardstick, never a product path."""
    subprocess.run(
        [
            *_FFMPEG,
            "-i",
            source.as_posix(),
            "-vf",
            _V1_PROXY_FILTER,
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            "-colorspace",
            "bt709",
            "-color_primaries",
            "bt709",
            "-color_trc",
            "bt709",
            "-color_range",
            "tv",
            "-fps_mode",
            "cfr",
            "-an",
            destination.as_posix(),
        ],
        capture_output=True,
        check=True,
        timeout=300,
    )
    return destination


# --- reading rendered files ---------------------------------------------------------


def probe_colour(path: Path) -> dict[str, str]:
    stream = v2.ffprobe_json(
        path,
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=color_primaries,color_transfer,color_space,color_range,pix_fmt,width,height",
    )["streams"][0]  # type: ignore[index]
    return {key: str(value) for key, value in stream.items()}


def frame_rgb(
    path: Path, *, width: int, height: int, at: float = _COMPARE_AT_S
) -> NDArray[np.float32]:
    """One decoded frame as full-range RGB, read the way a bt709 player reads it."""
    completed = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-ss",
            f"{at:.3f}",
            "-i",
            path.as_posix(),
            "-frames:v",
            "1",
            "-vf",
            f"scale={width}:{height}:in_color_matrix=bt709:in_range=tv:out_range=pc,format=rgb24",
            "-f",
            "rawvideo",
            "-",
        ],
        capture_output=True,
        check=True,
        timeout=120,
    )
    return np.frombuffer(completed.stdout, np.uint8).reshape(height, width, 3).astype(np.float32)


def mean_luma(path: Path) -> float:
    """``signalstats`` YAVG averaged over every frame - verify-03 criterion 11's measure."""
    completed = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"movie={path.name},signalstats",
            "-show_entries",
            "frame_tags=lavfi.signalstats.YAVG",
            "-of",
            "csv=p=0",
        ],
        cwd=path.parent,
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    values = [
        float(line.strip().rstrip(",")) for line in completed.stdout.splitlines() if line.strip()
    ]
    return sum(values) / len(values)


def _mae(a: NDArray[np.float32], b: NDArray[np.float32]) -> float:
    return float(np.abs(a - b).mean())


def _tags_wrong(tags: dict[str, str]) -> list[str]:
    return [
        f"{key}={tags.get(key)}" for key, want in EXPECTED_TAGS.items() if tags.get(key) != want
    ]


# --- the product path, in process -------------------------------------------------


async def _current_proxy(engine: v3.Engine, sha256: str) -> Path:
    from repcut.db.models import DerivedArtifact
    from repcut.media.artifacts import PARAMS_VERSION, ArtifactKind
    from repcut.media.store import absolute
    from sqlalchemy import select

    async with engine.session_factory() as session:
        row = (
            await session.execute(
                select(DerivedArtifact).where(
                    DerivedArtifact.sha256 == sha256,
                    DerivedArtifact.artifact_kind == ArtifactKind.PROXY.value,
                    DerivedArtifact.params_version == PARAMS_VERSION[ArtifactKind.PROXY],
                )
            )
        ).scalar_one()
    return absolute(engine.data_dir, row.stored_path)


async def _ingest(clips: list[Path], root: Path) -> dict[str, Path]:
    """Upload each clip through the real engine; the current proxy of each, by name.

    No Gemini key: analysis runs, but nothing can leave the machine.
    """
    proxies: dict[str, Path] = {}
    async with v3.in_process_engine(root / "data", gemini_api_key=None) as engine:
        project_id = await v3.new_project(engine, "gate-04")
        for clip in clips:
            body = await v3.upload_clip(engine, project_id, clip)
            await engine.queue.drain()
            proxies[clip.name] = await _current_proxy(engine, str(body["sha256"]))
    return proxies


# --- criteria -------------------------------------------------------------------------


def check_proxy_colour() -> int:
    """1. HDR proxy reads bt709 from the file and comes back to its SDR reference."""
    from repcut.media.artifacts import NORMALISATION

    with TemporaryDirectory(prefix="repcut-gate04-", ignore_cleanup_errors=True) as scratch:
        root = Path(scratch)
        reference = encode_sdr_reference(root / "sdr-reference.mp4")
        hlg = root / "hlg.mp4"
        codec = encode_hlg(reference, hlg)
        source_tags = probe_colour(hlg)
        proxies = asyncio.run(_ingest([hlg, reference], root))
        v2_hdr, v2_sdr = proxies[hlg.name], proxies[reference.name]
        v1_hdr = render_v1_proxy(hlg, root / "v1-hdr.mp4")
        v1_sdr = render_v1_proxy(reference, root / "v1-sdr.mp4")

        tags_v2_hdr, tags_v2_sdr, tags_v1_hdr = (
            probe_colour(v2_hdr),
            probe_colour(v2_sdr),
            probe_colour(v1_hdr),
        )
        width, height = int(tags_v2_hdr["width"]), int(tags_v2_hdr["height"])
        truth = frame_rgb(reference, width=width, height=height)
        error_v1 = _mae(frame_rgb(v1_hdr, width=width, height=height), truth)
        error_v2 = _mae(frame_rgb(v2_hdr, width=width, height=height), truth)
        luma_hdr = mean_luma(v2_hdr)
        luma_sdr_v1, luma_sdr_v2 = mean_luma(v1_sdr), mean_luma(v2_sdr)

    ratio = error_v2 / error_v1 if error_v1 > 0 else float("inf")
    triple = "/".join(
        tags_v2_hdr.get(key, "?") for key in ("color_primaries", "color_transfer", "color_space")
    )
    measured(
        f"fixture={codec} {source_tags.get('color_transfer')}/{source_tags.get('pix_fmt')} "
        f"operator={NORMALISATION.operator} npl={NORMALISATION.nominal_peak} | "
        f"HDR proxy {triple} {tags_v2_hdr.get('color_range')} {tags_v2_hdr.get('pix_fmt')} "
        f"luma={luma_hdr:.1f} | error v1={error_v1:.1f} v2={error_v2:.1f} "
        f"ratio={ratio:.2f} (max {ERROR_FRACTION_MAX}, abs max {ERROR_ABSOLUTE_MAX}) | "
        f"SDR luma v1={luma_sdr_v1:.1f} v2={luma_sdr_v2:.1f}"
    )

    problems: list[str] = []
    if source_tags.get("color_transfer") != "arib-std-b67" or "10" not in source_tags.get(
        "pix_fmt", ""
    ):
        problems.append("the fixture is not real 10-bit HLG, so nothing below is measured")
    if not _tags_wrong(tags_v1_hdr):
        problems.append("negative control: the v1 recipe's HDR proxy passes the tag check")
    if wrong := _tags_wrong(tags_v2_hdr):
        problems.append(f"HDR proxy tags wrong: {', '.join(wrong)}")
    if wrong := _tags_wrong(tags_v2_sdr):
        problems.append(f"SDR proxy tags wrong: {', '.join(wrong)}")
    if not LUMA_BAND[0] < luma_hdr < LUMA_BAND[1]:
        problems.append(f"HDR proxy mean luma {luma_hdr:.1f} outside {LUMA_BAND}")
    if ratio > ERROR_FRACTION_MAX:
        problems.append(
            f"v2 removes too little of v1's error: {error_v2:.1f} is {ratio:.0%} of "
            f"{error_v1:.1f}, above {ERROR_FRACTION_MAX:.0%} (operator {NORMALISATION.operator})"
        )
    if error_v2 > ERROR_ABSOLUTE_MAX:
        problems.append(f"v2 error {error_v2:.1f} above the absolute bound {ERROR_ABSOLUTE_MAX}")
    if abs(luma_sdr_v2 - luma_sdr_v1) > SDR_LUMA_TOLERANCE:
        problems.append(
            f"SDR proxy moved {abs(luma_sdr_v2 - luma_sdr_v1):.1f} luma codes from v1 "
            f"(max {SDR_LUMA_TOLERANCE})"
        )
    if problems:
        failed("; ".join(problems))
        return 1
    return 0


def check_one_normalisation() -> int:
    """2. Proxy and frame call the same function with the same recipe object."""
    import repcut.media.ffmpeg_builder as builder
    from repcut.analysis.params import FRAME_RECIPE
    from repcut.media.artifacts import NORMALISATION, PROXY_RECIPE

    source, output = Path("gate/source.mp4"), Path("gate/output")
    hdr = {"color_primaries": "bt2020", "color_transfer": "arib-std-b67"}

    def proxy_argv(recipe: object = PROXY_RECIPE) -> list[str]:
        return builder.build_proxy(
            source,
            output,
            display_width=2160,
            display_height=3840,
            recipe=recipe,
            **hdr,  # type: ignore[arg-type]
        ).argv

    def frame_argv(recipe: object = FRAME_RECIPE) -> list[str]:
        return builder.build_frame_extraction(
            source,
            output,
            timestamp_seconds=1.0,
            recipe=recipe,
            **hdr,  # type: ignore[arg-type]
        ).argv

    shared = PROXY_RECIPE.normalisation is FRAME_RECIPE.normalisation is NORMALISATION
    calls: list[str] = []
    real = builder.normalise_to_sdr

    def spy(*args: object, **kwargs: object) -> str | None:
        calls.append(str(kwargs.get("output_range")))
        return real(*args, **kwargs)  # type: ignore[arg-type]

    builder.normalise_to_sdr = spy  # type: ignore[assignment]
    try:
        proxy_now, frame_now = proxy_argv(), frame_argv()
    finally:
        builder.normalise_to_sdr = real

    other_operator = "mobius" if NORMALISATION.operator != "mobius" else "reinhard"
    other = replace(NORMALISATION, operator=other_operator)
    proxy_moved = proxy_argv(replace(PROXY_RECIPE, normalisation=other)) != proxy_now
    frame_moved = frame_argv(replace(FRAME_RECIPE, normalisation=other)) != frame_now
    measured(
        f"operator={NORMALISATION.operator} shared_recipe_object={shared} "
        f"normalise_to_sdr calls={calls} | operator->{other_operator}: "
        f"proxy argv moves={proxy_moved} frame argv moves={frame_moved}"
    )
    if not shared:
        failed("the proxy and frame recipes hold different normalisation objects")
        return 1
    if calls != ["tv", "pc"]:
        failed(f"expected one normalise_to_sdr call per builder (tv, pc), saw {calls}")
        return 1
    if not (proxy_moved and frame_moved):
        failed("changing the operator did not change both argvs")
        return 1
    return 0


def check_short_side_cap() -> int:
    """3. Portrait 720x1280, landscape 1280x720, a small source untouched - on disk."""
    with TemporaryDirectory(prefix="repcut-gate04-", ignore_cleanup_errors=True) as scratch:
        root = Path(scratch)
        cases = {
            "portrait.mp4": (
                v2.make_clip(
                    root / "portrait.mp4", seconds=1.0, width=1280, height=720, rotation=90
                ),
                (720, 1280),
            ),
            "landscape.mp4": (
                v2.make_clip(root / "landscape.mp4", seconds=1.0, width=1920, height=1080),
                (1280, 720),
            ),
            "small.mp4": (
                v2.make_clip(root / "small.mp4", seconds=1.0, width=640, height=360),
                (640, 360),
            ),
        }
        proxies = asyncio.run(_ingest([clip for clip, _ in cases.values()], root))
        seen = {}
        for name, (_clip, _expected) in cases.items():
            tags = probe_colour(proxies[name])
            seen[name] = (int(tags["width"]), int(tags["height"]))

    measured(" ".join(f"{name.removesuffix('.mp4')}={w}x{h}" for name, (w, h) in seen.items()))
    wrong = [
        f"{name} is {seen[name][0]}x{seen[name][1]}, expected {expected[0]}x{expected[1]}"
        for name, (_clip, expected) in cases.items()
        if seen[name] != expected
    ]
    if wrong:
        failed("; ".join(wrong))
        return 1
    return 0


def _shipped_version(path: str, pattern: str) -> int | None:
    """A version constant as the Prompt 03 tag shipped it, or None if the tag is absent."""
    shown = subprocess.run(
        ["git", "show", f"{PROMPT_03_TAG}:{path}"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if shown.returncode != 0:
        return None
    found = re.search(pattern, shown.stdout)
    return int(found.group(1)) if found else None


def check_versions_move_together() -> int:
    """4. Proxy and scene versions above Prompt 03's; the guards catch an un-bumped change."""
    from repcut.analysis.params import SCENE_PARAMS_VERSION
    from repcut.fingerprints import check_pins, proxy_fingerprint
    from repcut.media.artifacts import PARAMS_VERSION, PROXY_RECIPE, ArtifactKind

    shipped_proxy = _shipped_version(
        "engine/repcut/media/artifacts.py", r"ArtifactKind\.PROXY:\s*(\d+)"
    )
    shipped_scene = _shipped_version(
        "engine/repcut/analysis/params.py", r"SCENE_PARAMS_VERSION\s*=\s*(\d+)"
    )
    proxy_now = PARAMS_VERSION[ArtifactKind.PROXY]
    consistent = check_pins()
    changed = replace(PROXY_RECIPE, crf=PROXY_RECIPE.crf + 1)
    unbumped = check_pins(proxy=changed)
    proxy_only = check_pins(
        proxy=changed,
        proxy_version=proxy_now + 1,
        proxy_pins={proxy_now + 1: proxy_fingerprint(changed)},
    )
    measured(
        f"proxy {shipped_proxy}->{proxy_now} scene {shipped_scene}->{SCENE_PARAMS_VERSION} | "
        f"pins now: {len(consistent)} problems | negative controls: un-bumped proxy change -> "
        f"{len(unbumped)} problems, proxy bumped without scenes -> {len(proxy_only)} problem"
    )
    if shipped_proxy is None or shipped_scene is None:
        skipped(f"tag {PROMPT_03_TAG} is not in this clone; cannot derive Prompt 03's versions")
        return 2
    problems = []
    if proxy_now <= shipped_proxy:
        problems.append(
            f"PARAMS_VERSION[proxy] {proxy_now} is not above Prompt 03's {shipped_proxy}"
        )
    if shipped_scene >= SCENE_PARAMS_VERSION:
        problems.append(f"SCENE_PARAMS_VERSION {SCENE_PARAMS_VERSION} is not above {shipped_scene}")
    problems.extend(consistent)
    if not any("SCENE_PARAMS_VERSION" in problem for problem in unbumped):
        problems.append("negative control passed: an un-bumped proxy change went unnoticed")
    if not (len(proxy_only) == 1 and "SCENE_PARAMS_VERSION" in proxy_only[0]):
        problems.append("negative control passed: a proxy bump without a scene bump went unnoticed")
    if problems:
        failed("; ".join(problems))
        return 1
    return 0


async def _stale_regeneration(root: Path) -> dict[str, object]:
    from repcut.analysis.params import SCENE_PARAMS_VERSION
    from repcut.db.models import DerivedArtifact, Job, Scene
    from repcut.media.artifacts import PARAMS_VERSION, ArtifactKind
    from repcut.media.ingest import PROXY_FILENAME
    from repcut.media.store import absolute, derived_path
    from sqlalchemy import func, select, update

    clip = v2.make_clip(root / "stale.mp4", seconds=2.0)
    previous_proxy = PARAMS_VERSION[ArtifactKind.PROXY] - 1
    previous_scene = SCENE_PARAMS_VERSION - 1

    async with v3.in_process_engine(root / "data", gemini_api_key=None) as engine:
        project_id = await v3.new_project(engine, "gate-04 stale")
        body = await v3.upload_clip(engine, project_id, clip)
        await engine.queue.drain()
        sha256, media_file_id = str(body["sha256"]), str(body["media_file_id"])

        # The state a Prompt 03 library is in: the proxy under the previous
        # version's directory and key, the scenes under the previous detector.
        async with engine.session_factory() as session:
            row = (
                await session.execute(
                    select(DerivedArtifact).where(
                        DerivedArtifact.sha256 == sha256,
                        DerivedArtifact.artifact_kind == ArtifactKind.PROXY.value,
                    )
                )
            ).scalar_one()
            old_stored = derived_path(
                sha256, ArtifactKind.PROXY.value, previous_proxy, PROXY_FILENAME
            )
            old_file = absolute(engine.data_dir, old_stored)
            old_file.parent.mkdir(parents=True, exist_ok=True)
            absolute(engine.data_dir, row.stored_path).replace(old_file)
            row.stored_path, row.params_version = str(old_stored), previous_proxy
            await session.execute(
                update(Scene)
                .where(Scene.sha256 == sha256)
                .values(detector_params_version=previous_scene)
            )
            await session.commit()

        async def open_clip() -> int:
            response = await engine.client.post(f"/media/{media_file_id}/ensure-current")
            response.raise_for_status()
            return len(response.json()["enqueued_job_ids"])

        first, second = await open_clip(), await open_clip()
        await engine.queue.drain()
        third = await open_clip()
        current_proxy = await _current_proxy(engine, sha256)
        async with engine.session_factory() as session:
            scene_versions = set(
                (
                    await session.execute(
                        select(Scene.detector_params_version).where(Scene.sha256 == sha256)
                    )
                )
                .scalars()
                .all()
            )
            jobs_before = int((await session.execute(select(func.count(Job.id)))).scalar_one())
        duplicate = await v3.upload_clip(engine, project_id, clip)
        await engine.queue.drain()
        async with engine.session_factory() as session:
            jobs_after = int((await session.execute(select(func.count(Job.id)))).scalar_one())

        return {
            "opens": (first, second, third),
            "v2_proxy": current_proxy.is_file(),
            "v1_proxy_kept": old_file.is_file(),
            "scene_versions": sorted(scene_versions),
            "expected_scene_versions": [previous_scene, SCENE_PARAMS_VERSION],
            "duplicate": bool(duplicate.get("duplicate")),
            "duplicate_jobs": jobs_after - jobs_before,
        }


def check_stale_regeneration() -> int:
    """5. A v1 clip is regenerated on open, once; v1 files stay; duplicates enqueue nothing."""
    with TemporaryDirectory(prefix="repcut-gate04-", ignore_cleanup_errors=True) as scratch:
        result = asyncio.run(_stale_regeneration(Path(scratch)))

    first, second, third = result["opens"]  # type: ignore[misc]
    measured(
        f"jobs per open: {first}, {second}, then after the queue drained {third} | "
        f"v2 proxy={result['v2_proxy']} v1 proxy kept={result['v1_proxy_kept']} "
        f"scene versions={result['scene_versions']} | "
        f"duplicate upload jobs={result['duplicate_jobs']}"
    )
    problems = []
    if (first, second, third) != (2, 0, 0):
        problems.append(f"expected 2, 0, 0 jobs per open, saw {first}, {second}, {third}")
    if not result["v2_proxy"]:
        problems.append("no current-version proxy after regeneration")
    if not result["v1_proxy_kept"]:
        problems.append("the superseded proxy file was deleted")
    if result["scene_versions"] != result["expected_scene_versions"]:
        problems.append(
            f"scene versions {result['scene_versions']}, "
            f"expected {result['expected_scene_versions']}"
        )
    if not result["duplicate"] or result["duplicate_jobs"] != 0:
        problems.append(f"a duplicate upload enqueued {result['duplicate_jobs']} jobs")
    if problems:
        failed("; ".join(problems))
        return 1
    return 0


# Draws the product's own playing <video> into a canvas and reports what a person
# would notice. Throws - and so reports - if the pixels are tainted, which is
# what happens when a media response lacks the CORS header.
_DRAW_THE_PLAYER = """
(async () => {
  const video = document.querySelector("video");
  if (!video) return { error: "no <video> on the page" };
  const wait = (event, ms) => new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`no ${event} within ${ms}ms`)), ms);
    video.addEventListener(event, () => { clearTimeout(timer); resolve(); }, { once: true });
  });
  video.muted = true;
  await video.play();
  const start = video.currentTime;
  await new Promise((resolve) => setTimeout(resolve, 1500));
  const advanced = video.currentTime - start;
  video.pause();
  video.currentTime = 1.0;
  await wait("seeked", 15000);
  const canvas = document.createElement("canvas");
  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  const context = canvas.getContext("2d", { willReadFrequently: true });
  context.drawImage(video, 0, 0);
  const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
  const histogram = new Array(256).fill(0);
  const count = pixels.length / 4;
  let luma = 0, saturation = 0, clipped = 0;
  for (let i = 0; i < pixels.length; i += 4) {
    const r = pixels[i], g = pixels[i + 1], b = pixels[i + 2];
    const y = 0.2126 * r + 0.7152 * g + 0.0722 * b;
    luma += y;
    histogram[Math.min(255, Math.round(y))] += 1;
    const high = Math.max(r, g, b), low = Math.min(r, g, b);
    saturation += high === 0 ? 0 : (255 * (high - low)) / high;
    if (y >= 250) clipped += 1;
  }
  let seen = 0, black = 0;
  for (; black < 255; black += 1) { seen += histogram[black]; if (seen >= count * 0.01) break; }
  return {
    advanced, width: canvas.width, height: canvas.height,
    meanLuma: luma / count, meanSaturation: saturation / count,
    blackLevel: black, clippedPercent: (100 * clipped) / count,
  };
})()
"""


def _wait_for_proxy(stack: object, project_id: str, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status, media = stack.engine_request("GET", f"/projects/{project_id}/media")  # type: ignore[attr-defined]
        if status == 200 and isinstance(media, list) and media and media[0].get("has_proxy"):
            return True
        time.sleep(1.0)
    return False


def check_someone_can_see_it() -> int:
    """6. In a real browser against `make dev`, the HDR proxy looks like its SDR reference."""
    import dev_stack
    from cdp_browser import BrowserNotFoundError, inspect_page

    drawn: dict[str, object] = {}
    with (
        dev_stack.DevStack(gemini_enabled=False) as stack,
        TemporaryDirectory(prefix="repcut-gate04-", ignore_cleanup_errors=True) as scratch,
    ):
        root = Path(scratch)
        reference = encode_sdr_reference(root / "sdr-reference.mp4", seconds=4.0)
        hlg = root / "hlg.mp4"
        encode_hlg(reference, hlg)

        stack.start()
        if not stack.wait_ready():
            measured("stack did not start")
            failed("`make dev` never reached both ports; see the launcher output")
            return 1

        for label, clip in (("hdr", hlg), ("sdr", reference)):
            status, project = stack.engine_request(
                "POST", "/projects", {"name": f"gate-04 {label}"}
            )
            if status != 201 or not isinstance(project, dict):
                measured(f"POST /projects -> HTTP {status}")
                failed("could not create a project against the running stack")
                return 1
            project_id = str(project["id"])
            finalized = v2.upload(stack.engine_port, project_id, clip)
            if not finalized.get("sha256"):
                measured("upload failed")
                failed(f"the {label} fixture did not upload")
                return 1
            if not _wait_for_proxy(stack, project_id, v2.INGEST_TIMEOUT_S):
                measured(f"{label}: no proxy")
                failed(f"the {label} fixture's proxy never became available")
                return 1
            if stack.ui_get(f"/projects/{project_id}") != 200:
                measured(f"{label}: editor page did not render")
                failed("the project page did not render")
                return 1
            try:
                report = asyncio.run(
                    inspect_page(
                        f"http://localhost:{stack.ui_port}/projects/{project_id}",
                        observe_seconds=8.0,
                        evaluate=_DRAW_THE_PLAYER,
                    )
                )
            except BrowserNotFoundError as error:
                measured("no browser")
                failed(f"cannot look at the player without a browser: {error}")
                return 1
            drawn[label] = report.evaluated

    hdr, sdr = drawn.get("hdr"), drawn.get("sdr")
    for label, value in (("hdr", hdr), ("sdr", sdr)):
        if not isinstance(value, dict) or "meanSaturation" not in value:
            measured(f"{label}: {json.dumps(value)[:160]}")
            failed(f"the {label} player could not be drawn: {json.dumps(value)[:160]}")
            return 1
    assert isinstance(hdr, dict) and isinstance(sdr, dict)
    saturation_gap = abs(float(hdr["meanSaturation"]) - float(sdr["meanSaturation"]))
    black_gap = abs(float(hdr["blackLevel"]) - float(sdr["blackLevel"]))
    measured(
        f"HDR proxy: sat={hdr['meanSaturation']:.1f} black={hdr['blackLevel']} "
        f"luma={hdr['meanLuma']:.1f} played={hdr['advanced']:.2f}s | "
        f"SDR reference: sat={sdr['meanSaturation']:.1f} black={sdr['blackLevel']} "
        f"luma={sdr['meanLuma']:.1f} played={sdr['advanced']:.2f}s | "
        f"gaps sat={saturation_gap:.1f} (max {BROWSER_SATURATION_TOLERANCE}) "
        f"black={black_gap:.0f} (max {BROWSER_BLACK_TOLERANCE})"
    )
    problems = []
    for label, value in (("HDR", hdr), ("SDR", sdr)):
        if float(value["advanced"]) < BROWSER_MIN_PLAYBACK_S:
            problems.append(f"the {label} player did not play ({value['advanced']:.2f}s in 1.5s)")
    if saturation_gap > BROWSER_SATURATION_TOLERANCE:
        problems.append(f"saturation differs by {saturation_gap:.1f}")
    if black_gap > BROWSER_BLACK_TOLERANCE:
        problems.append(f"black level differs by {black_gap:.0f}")
    if problems:
        failed("; ".join(problems))
        return 1
    return 0


CHECKS: dict[str, Callable[[], int]] = {
    "proxy-colour": check_proxy_colour,
    "one-normalisation": check_one_normalisation,
    "short-side-cap": check_short_side_cap,
    "versions-move-together": check_versions_move_together,
    "stale-regeneration": check_stale_regeneration,
    "someone-can-see-it": check_someone_can_see_it,
}


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in CHECKS:
        print(f"usage: {Path(__file__).name} <{'|'.join(CHECKS)}>", file=sys.stderr)
        # 1, not 2: the gate reads 2 as SKIP, and an unknown name is a broken gate.
        return 1
    if shutil.which("ffmpeg") is None:
        measured("no ffmpeg")
        failed("ffmpeg is not on PATH")
        return 1
    return CHECKS[sys.argv[1]]()


if __name__ == "__main__":
    raise SystemExit(main())
