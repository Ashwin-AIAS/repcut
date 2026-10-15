#!/usr/bin/env python3
"""Measurements behind ``scripts/verify_08.sh``: one check per criterion.

Every check prints ``MEASURED: <value>`` and exits 0 (pass) or 1 (fail), with
``FAILED: <reason>`` on failure. Clips are generated (``reference_fixture.py``):
no footage, nothing downloaded. No absolute path is printed.
"""

from __future__ import annotations

import ast
import asyncio
import re
import shutil
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
ENGINE = REPO_ROOT / "engine"
sys.path.insert(0, str(ENGINE))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from reference_fixture import cut_frames, natural_frames, write  # noqa: E402
from repcut.analysis.scenes import detect_scenes  # noqa: E402
from repcut.grading.apply import read_measure_frames, render_still  # noqa: E402
from repcut.grading.corrector import Correction  # noqa: E402
from repcut.grading.packs import PackLibrary  # noqa: E402
from repcut.grading.theme import Look, PaceSpec  # noqa: E402
from repcut.reference import look as lk  # noqa: E402
from repcut.reference import rhythm as rh  # noqa: E402
from verify_03_checks import failed, measured  # noqa: E402
from verify_05_checks import _pytest, _pytest_check  # noqa: E402

SCRATCH = Path(tempfile.gettempdir()) / "repcut-gate08"

# "Within tolerance of Moody": per parameter. Wide enough for the encode, the
# JPEG and the grain on the graded frames; narrow enough that Hardstyle (the
# nearest other dark theme) falls outside it (asserted below, so the tolerance
# cannot quietly grow to accept anything).
TOLERANCE = {
    "contrast": 0.1,
    "black_lift": 0.03,
    "black_crush": 0.025,
    "highlight_rolloff": 0.06,
    "saturation": 0.08,
    "warmth": 0.012,
}
SPLIT_STRENGTH = 0.025
SPLIT_HUE_DEGREES = 30.0
ROUND_TRIP_TIMES = (0.5, 1.5, 2.5, 3.5)


def _misses(fit: Look, want: Look) -> list[str]:
    out = [n for n, tol in TOLERANCE.items() if abs(getattr(fit, n) - getattr(want, n)) > tol]
    for name in ("shadows", "highlights"):
        a, b = getattr(fit, name), getattr(want, name)
        sa, sb = (a.strength if a else 0.0), (b.strength if b else 0.0)
        if abs(sa - sb) > SPLIT_STRENGTH:
            out.append(f"{name}.strength")
        elif (
            a
            and b
            and sb >= SPLIT_STRENGTH
            and abs((a.hue - b.hue + 180) % 360 - 180) > SPLIT_HUE_DEGREES
        ):
            out.append(f"{name}.hue")
    return out


def _decode_jpeg(path: Path) -> np.ndarray:
    import cv2

    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise RuntimeError("graded still could not be read")
    return np.asarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))


async def _round_trip(theme_id: str) -> tuple[Look, Look, Look]:
    pack = PackLibrary().load("gym")
    theme = pack.theme(theme_id)
    clip = write(natural_frames(360, 640, 4.0), SCRATCH / "clip-a.mp4")
    graded: list[np.ndarray] = []
    for index, t in enumerate(ROUND_TRIP_TIMES):
        still = SCRATCH / f"graded-{theme_id}-{index}.jpg"
        await render_still(
            clip,
            still,
            timestamp_seconds=t,
            correction=Correction(),
            theme=theme,
            pack_id="gym",
            intensity=100,
            luts_root=SCRATCH / "luts",
        )
        graded.append(_decode_jpeg(still))
    baseline = await read_measure_frames(clip, ROUND_TRIP_TIMES)
    fit = lk.extract_look(lk.pixels_of(graded), lk.pixels_of(baseline))
    return fit.look, theme.look, pack.theme("hardstyle").look


def check_moody_round_trip() -> int:
    """1. Grade clip A with Moody -> extract the look -> within tolerance of Moody's numbers."""
    fit, moody, hardstyle = asyncio.run(_round_trip("moody"))
    misses = _misses(fit, moody)
    shown = ", ".join(
        f"{n} {getattr(fit, n):.3f}/{getattr(moody, n):.3f}"
        for n in ("contrast", "black_crush", "highlight_rolloff", "saturation", "warmth")
    )
    shadows = (
        f"shadows {fit.shadows.hue:.0f}deg {fit.shadows.strength:.3f}"
        if fit.shadows
        else "shadows none"
    )
    measured(f"extracted/Moody: {shown}, {shadows}; outside tolerance: {misses or 'none'}")
    if misses:
        failed(f"the extracted look is outside tolerance of Moody on {misses}")
        return 1
    if not _misses(fit, hardstyle):
        failed("the tolerance is too wide: Hardstyle would also pass")
        return 1
    return 0


def _profile(shot_seconds: float, name: str) -> rh.PacingProfile:
    seconds = 16.0
    clip = write(
        cut_frames(320, 568, seconds, shot_seconds, seed=int(shot_seconds * 10)), SCRATCH / name
    )
    scenes = detect_scenes(clip, fps_source=30.0)
    return rh.profile([s.start_seconds for s in scenes[1:]], seconds, bpm=None)


def check_pacing_differs() -> int:
    """2. A fast-cut and a slow, cinematic reference give measurably different planner numbers."""
    fast, slow = _profile(0.8, "fast.mp4"), _profile(4.0, "slow.mp4")
    pf, ps = rh.to_pace(fast), rh.to_pace(slow)
    measured(
        f"fast: {fast.shot_count} shots, median {fast.shot_median:.2f} s, "
        f"{fast.cuts_per_10s:.1f}/10s -> "
        f"energy {pf.energy}, beats {pf.peak_beats}/{pf.calm_beats}; "
        f"slow: {slow.shot_count} shots, "
        f"median {slow.shot_median:.2f} s, {slow.cuts_per_10s:.1f}/10s -> energy {ps.energy}, "
        f"beats {ps.peak_beats}/{ps.calm_beats}"
    )
    problems = []
    if abs(fast.shot_median - 0.8) > 0.15 or abs(slow.shot_median - 4.0) > 0.3:
        problems.append("the cut detection did not find the generated cuts")
    if not pf.energy > ps.energy + 0.3:
        problems.append("energy does not separate them")
    if not (pf.calm_beats < ps.calm_beats and pf.peak_beats < ps.peak_beats):
        problems.append("shot lengths in beats do not separate them")
    if problems:
        failed("; ".join(problems))
        return 1
    return 0


async def _boot_twice() -> tuple[bool, bool, str]:
    import httpx
    from repcut.config import Settings
    from repcut.grading import saved
    from repcut.main import app, start_engine, stop_engine
    from repcut.reference.service import theme_for

    root = SCRATCH / "engine"
    shutil.rmtree(root, ignore_errors=True)
    settings = Settings(
        data_dir=root / "data",
        database_url=f"sqlite+aiosqlite:///{(root / 'engine.db').as_posix()}",
        gemini_api_key=None,
    )
    fit = lk.extract_look(
        __import__("repcut.grading.lut", fromlist=["apply_look"]).apply_look(
            lk.neutral_baseline(seed=1), PackLibrary().theme("gym", "old-school").look
        ),
        lk.neutral_baseline(seed=2),
    )
    seen: list[bool] = []
    theme_id = ""
    for run in range(2):
        await start_engine(app, settings)
        try:
            if run == 0:
                theme_id = saved.save(
                    settings.data_dir, theme_for("Gate look", fit, PaceSpec(), set())
                ).id
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://localhost"
            ) as client:
                pack = (await client.get("/grading/packs/gym")).json()
            seen.append(theme_id in [t["id"] for t in pack["themes"]])
        finally:
            await stop_engine(app)
    return seen[0], seen[1], theme_id


def check_saved_themes_persist() -> int:
    """3. A saved reference look appears in the theme selector, and still does after a restart."""
    first, second, theme_id = asyncio.run(_boot_twice())
    code, summary = _pytest(
        "tests/test_reference.py::test_a_saved_look_is_a_theme_file_that_survives_a_restart",
        "tests/test_reference_api.py::test_match_applies_three_parts_and_each_detaches_alone",
    )
    measured(f"{theme_id}: in the selector {first}, after restart {second}; {summary}")
    if not (first and second) or code != 0:
        failed("a saved look is missing from the theme selector, or lost on restart")
        return 1
    return 0


def check_hostile_reference() -> int:
    """5. The reference is hostile input: the same probe, ceiling and refusals as an upload."""
    return _pytest_check(
        "hostile reference",
        "tests/test_reference_api.py::test_a_reference_gets_every_upload_refusal",
        "tests/test_reference_api.py::test_reference_requests_are_refused_by_name",
    )


BANNED_IMPORTS = ("torch", "onnxruntime", "tensorflow", "diffusers", "transformers", "cv2.dnn")


def check_statistical_only() -> int:
    """6. P1: the look transfer is statistical colour mapping inside the caps, nothing else."""
    found: list[str] = []
    for path in sorted((ENGINE / "repcut" / "reference").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            found.extend(f"{path.name}:{n}" for n in names if n.split(".")[0] in BANNED_IMPORTS)
        if re.search(r"\bdnn\b|style[_ ]transfer", path.read_text(encoding="utf-8")):
            found.append(f"{path.name}: neural transfer reference")
    code, summary = _pytest(
        "tests/test_reference.py::test_a_garish_reference_degrades_to_a_capped_look_and_says_so",
        "tests/test_reference.py::test_a_built_in_look_round_trips_through_extraction",
    )
    measured(f"model imports in reference/: {found or 'none'}; caps and round trips: {summary}")
    if found or code != 0:
        failed("the reference look is not plain, capped statistical colour mapping")
        return 1
    return 0


def check_match_and_ui() -> int:
    """7. One click applies look, pacing and captions; each detaches alone, restores, logged."""
    code, summary = _pytest("tests/test_reference_api.py", "tests/test_reference.py")
    from verify_07_checks import _vitest

    ui_code, ui_summary = _vitest("components/reference/ReferencePanel.test.tsx")
    measured(f"engine: {summary}; UI: {ui_summary}")
    if code != 0 or ui_code != 0:
        failed("matching, detaching or the panel does not do what it says")
        return 1
    return 0


def check_migration() -> int:
    """8. Migration 0007 round-trips and matches the models."""
    return _pytest_check("migrations", "tests/test_migrations.py", "tests/test_db_models.py")


CHECKS: dict[str, Callable[[], int]] = {
    "moody-round-trip": check_moody_round_trip,
    "pacing-differs": check_pacing_differs,
    "saved-themes-persist": check_saved_themes_persist,
    "hostile-reference": check_hostile_reference,
    "statistical-only": check_statistical_only,
    "match-and-ui": check_match_and_ui,
    "migration": check_migration,
}


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in CHECKS:
        print(f"usage: {Path(__file__).name} <{'|'.join(CHECKS)}>", file=sys.stderr)
        return 1
    SCRATCH.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    code = CHECKS[sys.argv[1]]()
    print(f"ELAPSED: {time.monotonic() - started:.1f}s")
    return code


if __name__ == "__main__":
    np.seterr(all="ignore")
    raise SystemExit(main())
