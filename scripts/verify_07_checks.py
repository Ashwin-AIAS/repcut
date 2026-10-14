#!/usr/bin/env python3
"""Measurements behind ``scripts/verify_07.sh``: one check per criterion.

Every check prints ``MEASURED: <value>`` and exits 0 (pass) or 1 (fail), with
``FAILED: <reason>`` on failure; ``SKIPPED: NO_GPU <why>`` with exit 2 where a
criterion is about the GPU and none is visible (amendment 014: the gate checks
the condition independently). Exit 3 means "bound to a human input that does
not exist yet" (the labelled session, criterion 1): pending in ``--auto``, a
FAIL in the gate (``gate_stop_bound``).

Clips are generated (``slowmo_fixture.py``); RIFE needs torch and its weights
(``make setup-gpu``, ``scripts/fetch_models.py``). Without them the RIFE
criteria FAIL with that instruction - a missing model is not a skip condition.
No absolute path is printed.
"""

from __future__ import annotations

import asyncio
import dataclasses
import importlib
import itertools
import json
import re
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
ENGINE = REPO_ROOT / "engine"
sys.path.insert(0, str(ENGINE))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from repcut.config import get_settings  # noqa: E402
from repcut.export.check import Expected, platform_problems  # noqa: E402
from repcut.export.reframe import crop_for  # noqa: E402
from repcut.export.render import ExportInputs, ShotSource, render_export  # noqa: E402
from repcut.media.ffmpeg_builder import CropBox, ExportEncode, GradeSpec  # noqa: E402
from repcut.media.store import absolute  # noqa: E402
from repcut.slowmo import confidence  # noqa: E402
from repcut.slowmo.rife import RifeInterpolator  # noqa: E402
from repcut.slowmo.runtime import InterpolationSetup  # noqa: E402
from repcut.slowmo.weights import RIFE_STORED_PATH, WeightsUnavailableError, verify  # noqa: E402
from slowmo_fixture import FPS, frames, write_clip  # noqa: E402
from verify_03_checks import failed, measured, skipped  # noqa: E402
from verify_05_checks import _pytest, _pytest_check  # noqa: E402

SCRATCH = Path(tempfile.gettempdir()) / "repcut-gate07"
LABELS = ENGINE / "tests" / "fixtures" / "hero_labels.json"
REEL = (1080, 1920)
GPU_BUDGET_S = 60.0
# How far from a labelled lift a hero's peak may sit and still be that lift.
LABEL_SLACK_S = 1.0
NEEDS_MODEL = "install torch (make setup-gpu) and fetch the weights (scripts/fetch_models.py)"


def _weights() -> Path:
    return absolute(get_settings().data_dir, str(RIFE_STORED_PATH))


def _torch() -> ModuleType | None:
    try:
        return importlib.import_module("torch")
    except ImportError:
        return None


def _model_ready() -> str | None:
    """None when RIFE can run here, else what is missing."""
    if _torch() is None:
        return "torch is not installed"
    try:
        verify(_weights())
    except WeightsUnavailableError as error:
        return str(error)
    return None


def _cuda(torch: ModuleType) -> bool:
    return bool(torch.cuda.is_available())


# --- 1. the hero detector -----------------------------------------------------------


def check_hero_synthetic() -> int:
    """1a. On a generated session the hardest set's last reps rank first, with reasons."""
    return _pytest_check("hero detector", "tests/test_highlights.py")


def check_hero_labelled() -> int:
    """1. On a real, human-labelled session, the top 3 moments include the heaviest lift."""
    data = json.loads(LABELS.read_text(encoding="utf-8")) if LABELS.is_file() else {}
    labels = data.get("sessions", [])
    if not labels:
        measured("no labelled session in engine/tests/fixtures/hero_labels.json yet")
        failed("Ashwin labels the heaviest lift of one real session once (prompt-07.md)")
        return 3
    return asyncio.run(_labelled(labels))


async def _labelled(labels: list[dict[str, object]]) -> int:
    from repcut.db import create_engine, create_session_factory
    from repcut.db.models import MediaFile
    from repcut.edit import service as edit_service
    from repcut.edit.types import Decisions
    from sqlalchemy import select

    settings = get_settings()
    engine = create_engine(settings)
    sessions = create_session_factory(engine)
    found, problems = [], []
    try:
        async with sessions() as session:
            for label in labels:
                sha = str(label["sha256"])
                bounds = label["heaviest_lift_seconds"]
                if not isinstance(bounds, list) or len(bounds) != 2:
                    problems.append(f"label for {sha[:8]} has no [start, end] in seconds")
                    continue
                start, end = float(bounds[0]), float(bounds[1])
                clip = (
                    (await session.execute(select(MediaFile).where(MediaFile.sha256 == sha)))
                    .scalars()
                    .first()
                )
                if clip is None or clip.project_id is None:
                    problems.append(f"labelled clip {sha[:8]} is not in this machine's library")
                    continue
                state = await edit_service.build(
                    session, settings.data_dir, clip.project_id, Decisions()
                )
                hit = [
                    h
                    for h in state.heroes[:3]
                    if h.sha256 == sha and start - LABEL_SLACK_S <= h.peak <= end + LABEL_SLACK_S
                ]
                found.append(f"{sha[:8]}: {'rank ' + str(hit[0].rank) if hit else 'not in top 3'}")
                if not hit:
                    problems.append(f"the heaviest lift of {sha[:8]} is not in the top 3")
    finally:
        await engine.dispose()
    measured("; ".join(found) or "no labelled clip could be read")
    if problems:
        failed("; ".join(problems))
        return 1
    return 0


# --- 2. RIFE speed and memory -----------------------------------------------------------


def _interpolate_frames(device: str, seconds: float, size: tuple[int, int]) -> dict[str, float]:
    torch = _torch()
    assert torch is not None  # callers checked _model_ready()
    captured = frames("gentle", size[0], size[1], round(seconds * FPS / 2) + 1)
    with RifeInterpolator(torch, _weights(), preference=device) as rife:
        started = time.perf_counter()
        for first, second in itertools.pairwise(captured):
            rife.midpoint(first, second)
        elapsed = time.perf_counter() - started
        stats = rife.stats
    return {
        "seconds": elapsed,
        "pairs": float(stats.pairs),
        "peak_vram_mb": float(stats.peak_vram_mb or 0.0),
        "oom_retries": float(stats.oom_retries),
        "cpu_fallback": float(stats.fell_back_to_cpu),
        "flow_scale": stats.flow_scale,
    }


def check_rife_gpu_speed() -> int:
    """2. RIFE 2x on a 3 s 1080p segment in under 60 s on the GPU, no OOM, VRAM logged."""
    torch = _torch()
    if torch is None or not _cuda(torch):
        measured("no CUDA device visible to torch")
        skipped("NO_GPU", "RIFE's speed is measured on the GPU only")
        return 2
    missing = _model_ready()
    if missing:
        measured(missing)
        failed(NEEDS_MODEL)
        return 1
    result = _interpolate_frames("cuda", 3.0, REEL)
    measured(
        f"{int(result['pairs'])} pairs at 1080x1920 in {result['seconds']:.1f} s "
        f"(budget {GPU_BUDGET_S:.0f} s), peak VRAM {result['peak_vram_mb']:.0f} MB, "
        f"flow scale {result['flow_scale']}, OOM retries {int(result['oom_retries'])}"
    )
    if result["cpu_fallback"] or result["oom_retries"]:
        failed("the GPU ran out of memory on a 1080p segment")
        return 1
    if result["seconds"] >= GPU_BUDGET_S:
        failed("slower than the budget")
        return 1
    return 0


def check_rife_cpu() -> int:
    """2b. The CPU path runs the verified weights at 1080p (the mandatory fallback)."""
    missing = _model_ready()
    if missing:
        measured(missing)
        failed(NEEDS_MODEL)
        return 1
    result = _interpolate_frames("cpu", 2 / FPS, REEL)
    measured(f"{int(result['pairs'])} pair(s) at 1080x1920 on the CPU in {result['seconds']:.1f} s")
    return 0 if result["pairs"] >= 1 else 1


# --- 3, 4: through the export ----------------------------------------------------------


def _slow_export(kind: str, out: Path) -> tuple[dict[str, object], list[str], Path]:
    """A 3-shot Reel whose middle shot is half speed, from a generated clip."""
    clip = write_clip(kind, out / f"{kind}.mp4", REEL[0], REEL[1], 4.0)
    crop = crop_for(REEL[0], REEL[1], REEL[0] / REEL[1])
    encode = ExportEncode(REEL[0], REEL[1], FPS, "veryfast", 18, "high", "4.2", 2)

    def shot(timeline_in: float, timeline_out: float, source_in: float) -> ShotSource:
        return ShotSource(
            sha256="7" * 64,
            source=clip,
            source_in=source_in,
            timeline_in=timeline_in,
            timeline_out=timeline_out,
            width=REEL[0],
            height=REEL[1],
            has_audio=True,
            crop=CropBox(crop.width, crop.height, crop.x, crop.y),
            grade=GradeSpec(0.0, (1.0, 1.0, 1.0), 0.0, None, 0, 0.0),
            lut_digest=None,
            color_primaries="bt709",
            color_transfer="bt709",
            color_space="bt709",
            color_range="tv",
        )

    slow = dataclasses.replace(shot(0.6, 1.6, 1.25), speed=0.5, fallback_in=1.0)
    inputs = ExportInputs(
        shots=(shot(0.0, 0.6, 0.1), slow, shot(1.6, 2.2, 3.0)),
        duration_seconds=2.2,
        encode=encode,
        audio_bitrate="192k",
        music=None,
        captions=(),
        style=None,
        interpolation=InterpolationSetup(weights=_weights()),
    )
    target = out / f"{kind}-export.mp4"
    result = asyncio.run(render_export(inputs, out / f"work-{kind}", target))
    decision = result.slowmo[0] if result.slowmo else {}
    return decision, result.problems, target


def check_barbell_reverts() -> int:
    """3. A fast barbell pan is refused: normal speed, the reason logged, exit 0."""
    missing = _model_ready()
    if missing:
        measured(missing)
        failed(NEEDS_MODEL)
        return 1
    out = SCRATCH / "barbell"
    decision, problems, _ = _slow_export("barbell", out)
    measured(
        f"confidence {decision.get('confidence')} (threshold {confidence.THRESHOLD}), "
        f"played at {decision.get('played')}: {decision.get('reason')}"
    )
    if decision.get("accepted") is not False or decision.get("played") != "normal speed":
        failed("the artifact-prone clip was shown in slow motion")
        return 1
    if decision.get("reason") not in (confidence.WARP_FAILED, confidence.POP_FAILED):
        failed("the fallback has no logged reason")
        return 1
    if problems:
        failed("; ".join(problems))
        return 1
    return 0


def check_export_with_slowmo() -> int:
    """4. An export with an accepted slow shot passes Prompt 06's platform checks."""
    missing = _model_ready()
    if missing:
        measured(missing)
        failed(NEEDS_MODEL)
        return 1
    out = SCRATCH / "gentle"
    decision, problems, target = _slow_export("gentle", out)
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=codec_type,codec_name,width,height,pix_fmt,color_space,color_range,"
         "color_primaries,color_transfer,sample_rate:format=duration", "-of", "json", str(target)],
        capture_output=True, text=True, timeout=60, check=True,
    )  # fmt: skip
    problems += platform_problems(probe.stdout, target, Expected(REEL[0], REEL[1], 2.2))
    data = json.loads(probe.stdout)
    video = next(s for s in data["streams"] if s["codec_type"] == "video")
    measured(
        f"slow shot {decision.get('played')} (confidence {decision.get('confidence')}); "
        f"{video['codec_name']} {video['pix_fmt']} {video['width']}x{video['height']} "
        f"{video.get('color_space')}/{video.get('color_range')}, "
        f"{float(data['format']['duration']):.2f} s"
    )
    if decision.get("accepted") is not True:
        failed("the gentle clip's slow motion was refused, so this export has no slow shot")
        return 1
    if problems:
        failed("; ".join(problems))
        return 1
    return 0


# --- 5. weights security ------------------------------------------------------------------

_LOAD = re.compile(r"\btorch\.load\(")


def check_weights_security() -> int:
    """5. Weights load from a pinned path with a recorded checksum; no torch.load unverified."""
    sites = []
    # This file names the call in order to find it; it never loads anything.
    scanner = Path(__file__).resolve()
    sources = [
        *ENGINE.joinpath("repcut").rglob("*.py"),
        *(p for p in REPO_ROOT.joinpath("scripts").glob("*.py") if p.resolve() != scanner),
    ]
    for path in sources:
        text = path.read_text(encoding="utf-8")
        for match in _LOAD.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            sites.append((path.relative_to(REPO_ROOT).as_posix(), line, text))
    allowed = "engine/repcut/slowmo/weights.py"
    bad = [f"{name}:{line}" for name, line, _ in sites if name != allowed]
    weights_text = next((text for name, _, text in sites if name == allowed), "")
    body = weights_text[weights_text.find("def load_verified") :]
    verified_first = body.find("verify(path)") != -1 and body.find("verify(path)") < body.find(
        "torch.load("
    )
    code, summary = _pytest(
        "tests/test_slowmo.py::test_missing_weights_are_named_and_never_loaded",
        "tests/test_slowmo.py::test_tampered_weights_are_refused_before_torch_load",
        "tests/test_slowmo.py::test_verified_weights_load_with_weights_only",
    )
    measured(
        f"{len(sites)} torch.load call site(s), outside the loader: {bad or 'none'}; "
        f"verify before load: {verified_first}; weights_only: {'weights_only=True' in body}; "
        f"tests: {summary}"
    )
    if bad or not verified_first or "weights_only=True" not in body or code != 0:
        failed("a weights load is not pinned, checked and weights-only")
        return 1
    return 0


# --- 6. inherited from Prompt 01 (amendment 003) ---------------------------------------------


def check_health_cuda() -> int:
    """6. With CUDA torch, /health reports cuda_available, the GPU's name and its VRAM."""
    torch = _torch()
    if torch is None or not _cuda(torch):
        measured("no CUDA device visible to torch")
        skipped("NO_GPU", "the CUDA fields can only be true on a GPU")
        return 2
    from repcut.probes import probe_torch

    probe = probe_torch("auto")
    measured(
        f"cuda_available={probe.cuda_available}, gpu_name={probe.gpu_name}, "
        f"vram {probe.vram_free_mb}/{probe.vram_total_mb} MB"
    )
    ok = probe.cuda_available and bool(probe.gpu_name) and probe.vram_total_mb
    if not ok:
        failed("/health would not report the GPU")
        return 1
    return _pytest_check("status page", "tests/test_health.py")


# --- 7-9: the ceiling, the plan and the UI, the migration ----------------------------------


def check_ceiling() -> int:
    """7. 2x is a hard ceiling: no speed, setting or stored plan can exceed it (P1)."""
    return _pytest_check(
        "2x ceiling",
        "tests/test_slowmo.py::test_no_speed_beyond_2x_or_between_exists",
        "tests/test_slowmo.py::test_the_ceiling_is_two_and_half_speed_is_the_slowest",
        "tests/test_slowmo.py::test_a_stored_plan_cannot_ask_for_more_than_2x",
        "tests/test_slowmo.py::test_no_setting_reaches_the_slowdown",
        "tests/test_slowmo.py::test_only_the_midpoint_exists",
    )


def _vitest(*files: str) -> tuple[int, str]:
    ui = REPO_ROOT / "ui"
    if not (ui / "node_modules").is_dir():
        return 1, "ui/node_modules is missing: run `npm ci` in ui/"
    npx = "npx.cmd" if sys.platform == "win32" else "npx"
    completed = subprocess.run(
        [npx, "vitest", "run", *files], cwd=ui, capture_output=True, text=True,
        timeout=600, check=False,
    )  # fmt: skip
    lines = [ln for ln in completed.stdout.splitlines() if re.search(r"Tests\s+\d+", ln)]
    return completed.returncode, (lines[-1].strip() if lines else "no vitest summary")


def check_planner_and_ui() -> int:
    """8. Heroes become drop-aligned slow cuts; badges, reasons and the toggle re-sync and log."""
    code, summary = _pytest(
        "tests/test_slowmo_planner.py",
        "tests/test_slowmo_api.py",
        "tests/test_export_slowmo.py",
        "tests/test_slowmo.py",
    )
    ui_code, ui_summary = _vitest("components/edit/EditPanel.test.tsx")
    measured(f"engine: {summary}; UI: {ui_summary}")
    if code != 0 or ui_code != 0:
        failed("the plan, the API or the edit panel does not do what it says")
        return 1
    return 0


def check_migration() -> int:
    """9. Migration 0006 round-trips and matches the models."""
    return _pytest_check("migrations", "tests/test_migrations.py", "tests/test_db_models.py")


CHECKS: dict[str, Callable[[], int]] = {
    "hero-synthetic": check_hero_synthetic,
    "hero-labelled": check_hero_labelled,
    "rife-gpu-speed": check_rife_gpu_speed,
    "rife-cpu": check_rife_cpu,
    "barbell-reverts": check_barbell_reverts,
    "export-with-slowmo": check_export_with_slowmo,
    "weights-security": check_weights_security,
    "health-cuda": check_health_cuda,
    "ceiling": check_ceiling,
    "planner-and-ui": check_planner_and_ui,
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
