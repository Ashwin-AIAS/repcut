#!/usr/bin/env python3
"""Measurements behind ``scripts/verify_10.sh``: one check per criterion.

Every check prints ``MEASURED: <value>`` and exits 0 (pass) or 1 (fail), with
``FAILED: <reason>`` on failure. Exit 2 with ``SKIPPED: NO_GPU <why>`` where a
criterion can only be true on a CUDA GPU (amendment 014). Exit 3 means "bound
to a human input that does not exist yet" (the rep labels): pending in
``--auto``, a FAIL in the gate (``gate_stop_bound``). Clips and taste
histories are generated; no footage, nothing downloaded. No absolute path is
printed.
"""

from __future__ import annotations

import ast
import asyncio
import importlib
import json
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ENGINE = REPO_ROOT / "engine"
sys.path.insert(0, str(ENGINE))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from repcut.config import get_settings  # noqa: E402
from verify_03_checks import failed, measured  # noqa: E402
from verify_05_checks import _pytest, _pytest_check  # noqa: E402
from verify_07_checks import _vitest  # noqa: E402

SCRATCH = Path(tempfile.gettempdir()) / "repcut-gate10"
REP_LABELS = ENGINE / "tests" / "fixtures" / "rep_labels.json"
VRAM_BUDGET_BYTES = int(3.2 * 1024**3)


def skipped(condition: str, why: str) -> None:
    print(f"SKIPPED: {condition} {why}")


def check_profile_shift() -> int:
    """1. A moody, fast-cut taste history measurably shifts every recommendation."""
    return _pytest_check(
        "profile shift",
        "tests/test_profile.py::test_a_moody_fast_cut_person_shifts_every_recommendation",
        "tests/test_profile.py::test_one_or_two_clicks_are_a_choice_not_a_style",
        "tests/test_profile.py::test_every_active_item_says_why_with_its_evidence",
        "tests/test_profile.py::test_a_reset_forgets_one_item_and_keeps_the_rest",
    )


def check_cold_start() -> int:
    """1b. No events (or the profile off) == the edit from before profiles, exactly."""
    return _pytest_check(
        "cold start",
        "tests/test_profile.py::test_cold_start_changes_nothing_at_all",
        "tests/test_profile.py::test_profile_on_and_off_through_the_engine",
        "tests/test_recommend.py",
    )


def check_reframe() -> int:
    """2. A walking subject stays in the middle third of a reel in >= 95% of frames;
    nobody found -> the centre crop; a lost subject eases back; a hand-placed scene stays."""
    return _pytest_check(
        "reframe",
        "tests/test_reframe.py",
        "tests/test_platforms.py::test_a_scene_placed_by_hand_keeps_its_placement",
    )


def check_reps_labelled() -> int:
    """3. On three labelled real hero segments, the counter is within one rep."""
    data = json.loads(REP_LABELS.read_text(encoding="utf-8")) if REP_LABELS.is_file() else {}
    labels = data.get("segments", [])
    if len(labels) < 3:
        measured(f"{len(labels)} of 3 labelled segments in engine/tests/fixtures/rep_labels.json")
        failed(
            "Ashwin counts the reps of three hero moments once (docs/manual-checks/prompt-10.md)"
        )
        return 3
    return asyncio.run(_labelled(labels))


async def _labelled(labels: list[dict[str, object]]) -> int:
    from repcut.db import create_engine, create_session_factory
    from repcut.db.models import MediaFile
    from repcut.grading.service import load_clip
    from repcut.gym.reps import PoseEstimator, PoseUnavailableError, count_reps, segment_keypoints
    from repcut.gym.weights import POSE
    from repcut.media.store import absolute
    from sqlalchemy import select

    settings = get_settings()
    try:
        estimator = PoseEstimator(absolute(settings.data_dir, POSE.stored_path), "auto")
    except PoseUnavailableError as error:
        measured("pose model unavailable")
        failed(str(error))
        return 1
    engine = create_engine(settings)
    sessions = create_session_factory(engine)
    found, problems = [], []
    try:
        async with sessions() as session:
            with estimator:
                for label in labels:
                    sha = str(label["sha256"])
                    statement = select(MediaFile).where(MediaFile.sha256 == sha)
                    clip = (await session.execute(statement)).scalars().first()
                    if clip is None:
                        problems.append(f"labelled clip {sha[:8]} is not in this machine's library")
                        continue
                    proxy = (await load_clip(session, settings.data_dir, clip)).proxy
                    start, end = float(label["start"]), float(label["end"])  # type: ignore[arg-type]
                    frames = await asyncio.to_thread(
                        segment_keypoints, proxy, start, end, estimator
                    )
                    result = count_reps(frames)
                    truth = int(label["reps"])  # type: ignore[call-overload]
                    found.append(f"{sha[:8]}@{start:.0f}s: {result.reps} vs {truth}")
                    if result.reps is None or abs(result.reps - truth) > 1:
                        problems.append(
                            f"{sha[:8]}@{start:.0f}s counted {result.reps}, truth {truth}"
                        )
    finally:
        await engine.dispose()
    measured("; ".join(found) + f"; peak VRAM {estimator.peak_vram_bytes / 1e6:.0f} MB")
    if problems:
        failed("; ".join(problems))
        return 1
    return 0


def check_reps_generated() -> int:
    """3a. Three generated, labelled sets (squat, curl, hinge) within one rep; no number
    where nobody moves; the pose model refuses a whole clip; the product boundary holds."""
    return _pytest_check(
        "reps on generated sets",
        "tests/test_gym.py::test_reps_are_counted_within_one_of_the_label",
        "tests/test_gym.py::test_nobody_or_no_movement_gives_no_number",
        "tests/test_gym.py::test_the_pose_model_never_runs_over_a_whole_clip",
        "tests/test_gym.py::test_the_product_boundary_is_named_and_kept",
    )


def check_gpu_models() -> int:
    """3b. Both pinned YOLO models load and run on the GPU, under the 3.2 GB budget."""
    try:
        torch = importlib.import_module("torch")
    except ImportError:
        torch = None
    if torch is None or not torch.cuda.is_available():
        skipped("NO_GPU", "the YOLO models' VRAM is measured on a CUDA GPU only")
        measured("no CUDA device visible")
        return 2
    import numpy as np
    from repcut.export.subjects import DetectorUnavailableError, open_person_detector
    from repcut.gym.reps import PoseEstimator, PoseUnavailableError
    from repcut.gym.weights import DETECT, POSE
    from repcut.media.store import absolute

    data_dir = get_settings().data_dir
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    try:
        torch.cuda.reset_peak_memory_stats()
        detector = open_person_detector(absolute(data_dir, DETECT.stored_path), "auto")
        detector.boxes(frame)
        detect_peak = torch.cuda.max_memory_allocated()
        del detector
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()
        with PoseEstimator(absolute(data_dir, POSE.stored_path), "auto") as pose:
            pose.keypoints(frame)
        pose_peak = pose.peak_vram_bytes
    except (DetectorUnavailableError, PoseUnavailableError) as error:
        measured("a YOLO model could not be opened")
        failed(f"{error} (make setup-gpu, then scripts/fetch_models.py)")
        return 1
    measured(f"detector peak {detect_peak / 1e6:.0f} MB, pose peak {pose_peak / 1e6:.0f} MB")
    if max(detect_peak, pose_peak) > VRAM_BUDGET_BYTES:
        failed("a YOLO model exceeds the 3.2 GB budget")
        return 1
    return 0


def check_platforms() -> int:
    """4. Export all platforms: 1080x1920, 1080x1080, 1920x1080, each through the platform check."""
    return _pytest_check(
        "three platforms",
        "tests/test_platforms.py::test_export_all_at_full_resolution",
        "tests/test_platforms.py::test_export_all_platforms_from_one_edit",
    )


def check_review_artifact() -> int:
    """5 (the [HUMAN] criterion's artifact). The review renders profile off and on."""
    return _pytest_check(
        "review artifact",
        "tests/test_profile.py::test_the_review_renders_profile_off_and_on_from_one_session",
    )


UNSAFE = {"pickle", "marshal", "shelve", "dill", "cloudpickle"}


def check_schema_only() -> int:
    """6. The style profile is read through a schema: no pickle, no eval (guide section 7)."""
    found: list[str] = []
    files = [
        *sorted((ENGINE / "repcut" / "profile").glob("*.py")),
        ENGINE / "repcut" / "api" / "profile.py",
    ]
    for path in files:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                found += [f"{path.name}:{a.name}" for a in node.names if a.name in UNSAFE]
            elif isinstance(node, ast.ImportFrom) and node.module in UNSAFE:
                found.append(f"{path.name}:{node.module}")
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in ("eval", "exec")
            ):
                found.append(f"{path.name}:{node.func.id}()")
    code, summary = _pytest(
        "tests/test_profile.py::test_the_profile_is_read_through_a_schema_never_pickle"
    )
    measured(f"{len(files)} files; unsafe loaders: {found or 'none'}; schema test: {summary}")
    if found or code != 0:
        failed("the profile is read by something other than its schema")
        return 1
    return 0


BANNED = ("httpx", "requests", "urllib", "diffusers", "transformers", "gemini")
WATCHED = ("repcut/profile", "repcut/gym", "repcut/export/subjects.py", "repcut/export/tracking.py",
           "repcut/export/reframe.py")  # fmt: skip


def check_local_and_real() -> int:
    """7. Nothing new leaves the machine, nothing is generated, no body is judged (P1, P4)."""
    files: list[Path] = []
    for name in WATCHED:
        path = ENGINE / name
        files.extend(sorted(path.glob("*.py")) if path.is_dir() else [path])
    found: list[str] = []
    for path in files:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            found += [f"{path.name}:{n}" for n in names if any(b in n for b in BANNED)]
    code, summary = _pytest(
        "tests/test_gym.py::test_the_product_boundary_is_named_and_kept",
        "tests/test_gym.py::test_sets_are_suggested_confirmed_and_burned_only_when_confirmed",
        "tests/test_reframe.py::test_the_crop_follows_without_chasing_jitter",
    )
    measured(f"{len(files)} files, network/generative imports: {found or 'none'}; {summary}")
    if found or code != 0:
        failed("a new module reaches beyond the machine, invents pixels or judges a body")
        return 1
    return 0


def check_overrides_and_ui() -> int:
    """8. Profile switch/reset, set confirm/dismiss, export all: re-sync, logged, in the UI."""
    code, summary = _pytest(
        "tests/test_profile.py::test_profile_on_and_off_through_the_engine",
        "tests/test_gym.py::test_sets_are_suggested_confirmed_and_burned_only_when_confirmed",
        "tests/test_gym.py::test_a_set_caption_says_only_what_was_confirmed",
    )
    ui_code, ui_summary = _vitest(
        "components/profile/StylePanel.test.tsx",
        "components/gym/SetCaptions.test.tsx",
        "components/export/ExportPanel.test.tsx",
        "components/captions/CaptionPanel.test.tsx",
        "components/edit/EditPanel.test.tsx",
        "components/grading/GradePanel.test.tsx",
    )
    measured(f"engine: {summary}; UI: {ui_summary}")
    if code != 0 or ui_code != 0:
        failed("a switch, a reset, a confirm or a panel does not do what it says")
        return 1
    return 0


def check_migration() -> int:
    """9. Migration 0009 round-trips and matches the models."""
    return _pytest_check("migrations", "tests/test_migrations.py", "tests/test_db_models.py")


CHECKS: dict[str, Callable[[], int]] = {
    "profile-shift": check_profile_shift,
    "cold-start": check_cold_start,
    "reframe": check_reframe,
    "reps-labelled": check_reps_labelled,
    "reps-generated": check_reps_generated,
    "gpu-models": check_gpu_models,
    "platforms": check_platforms,
    "review-artifact": check_review_artifact,
    "schema-only": check_schema_only,
    "local-and-real": check_local_and_real,
    "overrides-and-ui": check_overrides_and_ui,
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
    raise SystemExit(main())
