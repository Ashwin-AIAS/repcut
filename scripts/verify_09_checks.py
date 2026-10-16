#!/usr/bin/env python3
"""Measurements behind ``scripts/verify_09.sh``: one check per criterion.

Every check prints ``MEASURED: <value>`` and exits 0 (pass) or 1 (fail), with
``FAILED: <reason>`` on failure. Exit 3 means "bound to a human input that does
not exist yet" (the labelled session of verify-07 criterion 1): pending in
``--auto``, a FAIL in the gate (``gate_stop_bound``). Clips are generated; no
footage, nothing downloaded. No absolute path is printed.
"""

from __future__ import annotations

import ast
import asyncio
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
from verify_07_checks import LABELS, _vitest  # noqa: E402

SCRATCH = Path(tempfile.gettempdir()) / "repcut-gate09"


def check_hook_labelled() -> int:
    """1. On the real, human-labelled session, the hook is one of the top three hero moments."""
    data = json.loads(LABELS.read_text(encoding="utf-8")) if LABELS.is_file() else {}
    labels = data.get("sessions", [])
    if not labels:
        measured("no labelled session in engine/tests/fixtures/hero_labels.json yet")
        failed("Ashwin labels one real session once (docs/manual-checks/prompt-07.md)")
        return 3
    return asyncio.run(_labelled([str(label["sha256"]) for label in labels]))


async def _labelled(digests: list[str]) -> int:
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
            for sha in digests:
                statement = select(MediaFile).where(MediaFile.sha256 == sha)
                clip = (await session.execute(statement)).scalars().first()
                if clip is None or clip.project_id is None:
                    problems.append(f"labelled clip {sha[:8]} is not in this machine's library")
                    continue
                state = await edit_service.build(
                    session, settings.data_dir, clip.project_id, Decisions()
                )
                top = [h.segment_id for h in state.heroes[:3]]
                hook = state.hook
                if hook is None:
                    problems.append(f"the session of {sha[:8]} has no hook")
                    continue
                rank = top.index(hook.segment_id) + 1 if hook.segment_id in top else None
                where = f"#{rank}" if rank else "outside top 3"
                found.append(f"{sha[:8]}: hook is hero {where}")
                if rank is None:
                    problems.append(f"the hook of {sha[:8]}'s session is not a top-3 hero")
    finally:
        await engine.dispose()
    measured("; ".join(found) or "no labelled clip could be read")
    if problems:
        failed("; ".join(problems))
        return 1
    return 0


def check_hook_synthetic() -> int:
    """1a. On a generated session, every hook candidate is a top-3 hero; the pick is the best."""
    return _pytest_check(
        "hook candidates",
        "tests/test_hook.py::test_every_candidate_is_a_top_three_hero_moment",
        "tests/test_hook.py::test_on_the_generated_session_the_hook_is_the_hardest_sets_finish",
        "tests/test_hook.py::test_candidates_rank_by_hero_score_times_visual_impact",
        "tests/test_hook.py::test_a_still_but_loud_moment_loses_to_a_moving_one",
    )


def check_duration() -> int:
    """2. With the hook on, the edit stays within 5% of its target (planner test extended)."""
    return _pytest_check(
        "duration with a hook",
        "tests/test_hook.py::test_with_a_hook_the_duration_stays_within_five_percent",
        "tests/test_hook.py::test_the_timeline_has_no_gap_and_no_overlap",
        "tests/test_hook.py::test_a_short_edit_moves_the_moment_a_long_one_may_show_it_again",
        "tests/test_planner.py",
    )


def check_frame_accurate() -> int:
    """3. Before/after: the same frame on both sides, by frame hash, at 3 positions."""
    code, summary = _pytest(
        "tests/test_review.py::test_before_and_after_show_the_same_frame_at_three_positions",
        "tests/test_review.py::test_the_preview_starts_on_the_first_proxy_frame_at_or_after_the_scene",
    )
    ui_code, ui_summary = _vitest("lib/compare.test.ts")
    measured(f"engine (real proxy + graded preview): {summary}; UI mapping: {ui_summary}")
    if code != 0 or ui_code != 0:
        failed("the two sides do not show the same frame")
        return 1
    return 0


def check_summary_true() -> int:
    """4. Every claim in the summary panel exists in the edit plan JSON."""
    return _pytest_check(
        "summary claims",
        "tests/test_review.py::test_every_claim_exists_in_the_edit_record",
        "tests/test_review.py::test_the_numbers_come_from_the_record_not_a_template",
        "tests/test_review.py::test_a_claim_about_an_old_record_fails_against_the_new_one",
        "tests/test_review.py::test_verify_refuses_unbacked_and_miscounted_claims",
        "tests/test_review_api.py::test_the_summary_only_says_what_the_record_backs",
    )


def check_overrides_and_ui() -> int:
    """5. Hook on/off/choose re-sync and are logged (P2, P3); strip, slider and panel work."""
    code, summary = _pytest(
        "tests/test_review_api.py",
        "tests/test_hook.py::test_hook_overrides_fold_into_the_decisions",
        "tests/test_hook.py::test_a_locked_hook_stays_exactly_as_it_is",
        "tests/test_hook.py::test_a_locked_cut_at_the_opening_wins_over_the_hook",
    )
    ui_code, ui_summary = _vitest(
        "components/edit/HookStrip.test.tsx",
        "components/edit/EditPanel.test.tsx",
        "components/review/review.test.tsx",
    )
    measured(f"engine: {summary}; UI: {ui_summary}")
    if code != 0 or ui_code != 0:
        failed("an override, the strip, the slider or the panel does not do what it says")
        return 1
    return 0


BANNED = ("torch", "onnxruntime", "diffusers", "transformers", "httpx", "requests", "urllib")
WATCHED = ("repcut/edit/hook.py", "repcut/review")


def check_local_and_real() -> int:
    """6. Nothing new leaves the machine and nothing is generated (P1, P4)."""
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
            for name in names:
                if name.split(".")[0] in BANNED or "gemini" in name:
                    found.append(f"{path.name}:{name}")
    code, summary = _pytest(
        "tests/test_hook.py::test_with_a_hook_the_duration_stays_within_five_percent",
    )
    measured(f"{len(files)} files, network/model imports: {found or 'none'}; teaser 1x: {summary}")
    if found or code != 0:
        failed("the hook or the trust layer reaches beyond the machine or the captured frames")
        return 1
    return 0


def check_migration() -> int:
    """7. Migration 0008 round-trips and matches the models."""
    return _pytest_check("migrations", "tests/test_migrations.py", "tests/test_db_models.py")


CHECKS: dict[str, Callable[[], int]] = {
    "hook-labelled": check_hook_labelled,
    "hook-synthetic": check_hook_synthetic,
    "duration": check_duration,
    "frame-accurate": check_frame_accurate,
    "summary-true": check_summary_true,
    "overrides-and-ui": check_overrides_and_ui,
    "local-and-real": check_local_and_real,
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
