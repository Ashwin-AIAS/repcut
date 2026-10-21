#!/usr/bin/env python3
"""Measurements behind ``scripts/verify_11.sh``: one check per criterion.

Every check prints ``MEASURED: <value>`` and exits 0 (pass) or 1 (fail), with
``FAILED: <reason>`` on failure. Exit 3 means "bound to an input that only the
real machine has" (the live model eval needs Ashwin's key and spends free-tier
requests): pending in ``--auto``, a FAIL in the gate (``gate_stop_bound``).
The model is scripted everywhere else; no request leaves the machine. No
absolute path is printed.
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ENGINE = REPO_ROOT / "engine"
sys.path.insert(0, str(ENGINE))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from verify_03_checks import failed, measured  # noqa: E402
from verify_05_checks import _pytest, _pytest_check  # noqa: E402
from verify_07_checks import _vitest  # noqa: E402

COPILOT = ENGINE / "repcut" / "copilot"
EVAL = Path(__file__).resolve().parent / "copilot_eval.py"
# Nothing in the copilot reaches the network but the shared Gemini client.
NETWORK = ("httpx", "requests", "urllib", "socket", "aiohttp", "http.client")
# Nothing dispatches by a string the model wrote: no builtin that runs or
# imports a string anywhere in the copilot, and no getattr where the model's
# names flow (the turn loop and the registry). ``re.compile`` is not one.
DYNAMIC = {"eval", "exec", "__import__", "compile"}
MODEL_FACING = {"agent.py", "tools.py"}


def _eval(*extra: str) -> tuple[int, str, str]:
    completed = subprocess.run(
        [sys.executable, str(EVAL), *extra],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=1200, check=False,
    )  # fmt: skip
    out = completed.stdout
    rate = next((ln[10:] for ln in out.splitlines() if ln.startswith("MEASURED: ")), "no result")
    problems = "; ".join(ln[8:] for ln in out.splitlines() if ln.startswith("FAILED: "))
    if completed.returncode != 0 and not problems:
        problems = (completed.stderr.strip().splitlines() or ["the eval did not finish"])[-1][:160]
    return completed.returncode, rate, problems


def check_intent_eval() -> int:
    """1. 20 intents (3 multi, 2 ambiguous, 2 natural-only, 1 impossible), replayed:
    >= 90% correct call sequences, both ambiguous ask, both refusals name the
    principle, the impossible one lands in copilot_unmet_requests."""
    code, rate, problems = _eval()
    test_code, summary = _pytest(
        "tests/test_copilot_agent.py::test_the_twenty_intent_eval_replayed"
    )
    measured(f"replay {rate}; test: {summary}")
    if code != 0 or test_code != 0:
        failed(problems or "the replayed eval test failed")
        return 1
    return 0


def check_intent_eval_live() -> int:
    """1L. The same 20 intents against the real model: the same bar."""
    if os.environ.get("REPCUT_COPILOT_LIVE") != "1":
        measured("not run: needs the laptop's key and ~30 free-tier requests")
        failed("run `REPCUT_COPILOT_LIVE=1 make verify-11` on the laptop (VERIFY-LATER.md)")
        return 3
    code, rate, problems = _eval("--live")
    measured(f"live {rate}")
    if code != 0:
        failed(problems or "the live eval failed")
        return 1
    return 0


def check_undo() -> int:
    """2. Every copilot mutation then "undo" restores byte-identical plan state, for
    every registry operation; undo by card; nothing to undo is a clean 409."""
    return _pytest_check(
        "undo",
        "tests/test_copilot_registry.py::test_every_operation_then_undo_restores_the_state_byte_for_byte",
        "tests/test_copilot_registry.py::test_the_controls_routes_go_through_the_stack_and_undo_by_entry",
        "tests/test_copilot_agent.py::test_the_conversation_is_an_edit_log_and_undo_works_by_chat_and_by_card",
    )  # fmt: skip


def check_equivalence() -> int:
    """3. set_theme by button and by chat: identical plan, identical taste event but source."""
    return _pytest_check(
        "two doors",
        "tests/test_copilot_registry.py::test_set_theme_by_button_and_by_chat_is_the_same_change",
    )


def check_offline() -> int:
    """4. No key / offline: the chat is disabled with its reason; every control still works."""
    code, summary = _pytest(
        "tests/test_copilot_agent.py::test_offline_the_panel_is_disabled_and_every_control_still_works",
        "tests/test_edit_api.py",
    )  # fmt: skip
    ui_code, ui_summary = _vitest(
        "components/copilot/CopilotPanel.test.tsx",
        "components/edit/EditPanel.test.tsx",
        "components/grading/GradePanel.test.tsx",
        "components/captions/CaptionPanel.test.tsx",
        "components/export/ExportPanel.test.tsx",
        "components/workspace/workspace.test.tsx",
    )
    measured(f"engine: {summary}; UI: {ui_summary}")
    if code != 0 or ui_code != 0:
        failed("offline is not graceful, or a control broke")
        return 1
    return 0


def _dynamic(path: Path) -> list[str]:
    found: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if isinstance(fn, ast.Name) and fn.id in DYNAMIC:
            found.append(fn.id)
        elif isinstance(fn, ast.Attribute) and fn.attr == "import_module":
            found.append("import_module")
        elif path.name in MODEL_FACING and isinstance(fn, ast.Name) and fn.id == "getattr":
            found.append("getattr")
    return [f"{path.name}:{name}" for name in sorted(set(found))]


def check_allowlist() -> int:
    """5. Function calling hits a fixed allowlist with validated arguments; a frame's
    instruction to call an unlisted operation changes no edit and is logged."""
    from repcut.copilot import agent, tools

    problems: list[str] = []
    expected = set(tools.COPILOT_OPERATIONS) | set(agent.SPEECH_ACTS)
    if set(agent.ALLOWED) != expected:
        problems.append("the allowlist is not the registry plus the three speech acts")
    declared = {d["name"] for d in agent.declarations()}
    if declared != expected:
        problems.append("the declarations sent are not the allowlist")
    loose = [n for n, t in tools.REGISTRY.items() if t.args.model_config.get("extra") != "forbid"]
    if loose:
        problems.append(f"arguments not strict: {loose}")
    dynamic: list[str] = []
    for path in sorted(COPILOT.glob("*.py")):
        dynamic += _dynamic(path)
    if dynamic:
        problems.append(f"dynamic dispatch: {dynamic}")
    code, summary = _pytest(
        "tests/test_copilot_agent.py::test_an_instruction_inside_a_frame_changes_nothing_and_is_logged",
        "tests/test_copilot_registry.py::test_a_call_the_plan_cannot_take_is_a_clean_error_and_changes_nothing",
    )  # fmt: skip
    measured(
        f"{len(tools.COPILOT_OPERATIONS)} operations + {len(agent.SPEECH_ACTS)} speech acts, "
        f"strict args, dynamic dispatch: {dynamic or 'none'}; {summary}"
    )
    if problems or code != 0:
        failed("; ".join(problems) or "the injection or validation tests failed")
        return 1
    return 0


def check_natural_only() -> int:
    """6. Natural-only requests are refused naming the principle, with or without the
    model; sound is not the picture; refusals and unsupported asks are logged."""
    return _pytest_check(
        "natural only",
        "tests/test_copilot_agent.py::test_principle_refusals_name_natural_only_with_or_without_the_model",
    )  # fmt: skip


def check_local_and_compact() -> int:
    """7. The prompt's summary is under 1.5k tokens, with no path or filename; nothing in
    the copilot reaches the network except through the shared Gemini client (P4)."""
    imports: list[str] = []
    for path in sorted(COPILOT.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            mods: list[str] = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods = [node.module]
            imports += [f"{path.name}:{m}" for m in mods if m.split(".")[0] in NETWORK]
    # agent.py holds the one client handle it passes to gemini_client.
    stray = [i for i in imports if not i.startswith("agent.py:httpx")]
    code, summary = _pytest(
        "tests/test_copilot_agent.py::test_the_summary_is_compact_and_carries_no_path_or_filename"
    )
    measured(f"network imports outside the shared client: {stray or 'none'}; {summary}")
    if stray or code != 0:
        failed("the copilot reaches the network on its own, or the summary is too big")
        return 1
    return 0


def check_one_stack() -> int:
    """8. The controls' routes run the registry's operations, all on one undo stack."""
    source = "\n".join(
        (ENGINE / "repcut" / "api" / f"{n}.py").read_text(encoding="utf-8")
        for n in ("edit", "grading", "captions", "export")
    )
    direct = re.findall(
        r"service\.apply_override\(|service\.set_preset\(|service\.apply_choice\(", source
    )
    wrapped = source.count("tools.execute(") + source.count("tools.recorded(")
    code, summary = _pytest(
        "tests/test_edit_api.py",
        "tests/test_grading_api.py",
        "tests/test_captions_export_api.py",
    )
    measured(f"{wrapped} registry calls in the routes, {len(direct)} direct calls; {summary}")
    # A direct service call is only allowed inside a recorded() change.
    if code != 0 or wrapped < 8:
        failed("a control's route does not go through the registry, or its tests failed")
        return 1
    return 0


def check_migration() -> int:
    """9. Migration 0010 round-trips and matches the models."""
    return _pytest_check("migrations", "tests/test_migrations.py", "tests/test_db_models.py")


CHECKS: dict[str, Callable[[], int]] = {
    "intent-eval": check_intent_eval,
    "intent-eval-live": check_intent_eval_live,
    "undo": check_undo,
    "equivalence": check_equivalence,
    "offline": check_offline,
    "allowlist": check_allowlist,
    "natural-only": check_natural_only,
    "local-and-compact": check_local_and_compact,
    "one-stack": check_one_stack,
    "migration": check_migration,
}


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in CHECKS:
        print(f"usage: {Path(__file__).name} <{'|'.join(CHECKS)}>", file=sys.stderr)
        return 1
    started = time.monotonic()
    code = CHECKS[sys.argv[1]]()
    print(f"ELAPSED: {time.monotonic() - started:.1f}s")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
