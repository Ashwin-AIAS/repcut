#!/usr/bin/env python3
"""Measurements behind ``scripts/verify_12.sh``: one check per criterion.

Every check prints ``MEASURED: <value>`` and exits 0 (pass) or 1 (fail), with
``FAILED: <reason>`` on failure. Exit 3 means "bound to an input only the real
machine has": the real-footage fixtures and their labels, the models, or a CI
run on GitHub. Pending in ``--auto``, a FAIL in the gate (``gate_stop_bound``,
amendment 017). No absolute path is printed; Gemini is never called.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ENGINE = REPO_ROOT / "engine"
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(ENGINE))
sys.path.insert(0, str(SCRIPTS))

from verify_03_checks import failed, measured  # noqa: E402
from verify_05_checks import _pytest  # noqa: E402
from verify_07_checks import _vitest  # noqa: E402

CI_BUDGET_S = 10 * 60
SUITE_TIMEOUT_S = 3 * 3600


def _script(
    *argv: str, timeout: float = 3600, env: dict[str, str] | None = None
) -> tuple[int, str, str]:
    """Run a repo script; (exit, MEASURED line, FAILED line)."""
    completed = subprocess.run(
        [sys.executable, *argv],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env={**os.environ, "GEMINI_API_KEY": "", **(env or {})},
    )
    out = completed.stdout
    value = next((ln[10:] for ln in out.splitlines() if ln.startswith("MEASURED: ")), "")
    why = next((ln[8:] for ln in out.splitlines() if ln.startswith("FAILED: ")), "")
    if completed.returncode not in (0, 3) and not why:
        why = (completed.stderr.strip().splitlines() or ["no output"])[-1][:200]
    return completed.returncode, value, why


def check_engine_suite() -> int:
    """1a. The whole CPU suite, shuffled (order-independent), with coverage
    floors of 80% on edit, grading and analysis."""
    seed = os.environ.get("REPCUT_TEST_SEED") or datetime.now(UTC).strftime("%Y%m%d")
    data = Path(tempfile.gettempdir()) / f"repcut-gate12-{os.getpid()}.coverage"
    began = time.monotonic()
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "-m",
            "not gpu",
            "--cov=repcut",
            "--cov-report=",
            "tests",
        ],
        cwd=ENGINE,
        capture_output=True,
        text=True,
        timeout=SUITE_TIMEOUT_S,
        check=False,
        env={
            **os.environ,
            "REPCUT_TEST_ORDER": f"shuffle:{seed}",
            "COVERAGE_FILE": str(data),
            "GEMINI_API_KEY": "",
        },
    )
    summary = next(
        (
            ln
            for ln in reversed(completed.stdout.splitlines())
            if re.search(r"\d+ (passed|failed)", ln)
        ),
        "no summary",
    )
    floor, value, why = _script(str(SCRIPTS / "coverage_floor.py"), "--data", str(data))
    data.unlink(missing_ok=True)
    measured(
        f"shuffle seed {seed}: {summary} in {time.monotonic() - began:.0f} s; coverage {value}"
    )
    if completed.returncode != 0:
        failed("the engine suite failed in this order (re-run with REPCUT_TEST_SEED=" + seed + ")")
        return 1
    if floor != 0:
        failed(why or "a coverage floor was missed")
        return 1
    return 0


def check_ui_suite() -> int:
    """1b. vitest (hooks, components), tsc and eslint in ui/."""
    code, summary = _vitest()
    ui = REPO_ROOT / "ui"
    npx = "npx.cmd" if sys.platform == "win32" else "npx"
    tsc = subprocess.run(
        [npx, "tsc", "--noEmit"], cwd=ui, capture_output=True, text=True, timeout=600, check=False
    ).returncode
    lint = subprocess.run(
        [npx, "eslint", ".", "--max-warnings", "0"],
        cwd=ui,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    ).returncode
    clean = re.sub(r"\x1b\[[0-9;]*m", "", summary)
    measured(f"vitest {clean}; tsc exit {tsc}; eslint exit {lint}")
    if code or tsc or lint:
        failed("the UI suite, the type check or the linter failed")
        return 1
    return 0


def check_browser_e2e() -> int:
    """1c. Theme, edit, copilot off, export: clicked through the real UI on make dev."""
    code, value, why = _script(str(SCRIPTS / "e2e_browser.py"), timeout=1800)
    measured(value or "no measurement")
    if code != 0:
        failed(why or "the browser happy path failed")
        return 1
    return 0


def check_eval_scorecard() -> int:
    """2. make eval on the real fixtures: every claim's gate PASS, the scorecard
    written. Bound until the fixtures, labels and models are on the machine."""
    out = Path(tempfile.gettempdir()) / f"repcut-gate12-{os.getpid()}.json"
    code, value, why = _script(str(SCRIPTS / "eval_pipeline.py"), "--json", str(out), timeout=7200)
    card = Path(tempfile.gettempdir()) / f"repcut-gate12-{os.getpid()}.md"
    report = _script(str(SCRIPTS / "eval_report.py"), "--results", str(out), "--out", str(card))
    written = card.is_file() and "| Gate |" in card.read_text(encoding="utf-8")
    out.unlink(missing_ok=True)
    card.unlink(missing_ok=True)
    measured(f"{value}; scorecard {'written' if written else 'missing'} (report exit {report[0]})")
    if not written:
        failed("the scorecard was not written")
        return 1
    if code == 3:
        failed(
            "bound gates: build the fixtures and labels (VERIFY-LATER.md item 11), then `make eval`"
        )
        return 3
    if code != 0:
        failed(why or "a claim failed its gate")
        return 1
    return 0


def check_fixtures() -> int:
    """2b. The corpus: real clips, cut locally, < 100 MiB, kept out of git; the
    committed manifest names clips by hash only."""
    probe = subprocess.run(
        ["git", "check-ignore", "-q", "data/fixtures/session-0-x.mp4"], cwd=REPO_ROOT, check=False
    ).returncode
    test_code, summary = _pytest(
        "tests/test_eval_pipeline.py::test_the_manifest_names_clips_by_hash_only"
    )
    run = subprocess.run(
        [sys.executable, str(SCRIPTS / "eval_fixtures.py"), "status"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    code, status = run.returncode, run.stdout
    measured(
        " | ".join(ln.strip() for ln in status.splitlines()[-3:]) + f"; manifest test: {summary}"
    )
    if probe != 0:
        failed("data/fixtures/ is not ignored by git")
        return 1
    if test_code != 0:
        failed("the committed manifest carries more than hashes and spans")
        return 1
    if code == 3:
        failed("fixtures not labelled or not built yet (VERIFY-LATER.md item 11)")
        return 3
    return 0 if code == 0 else 1


def check_eval_honesty() -> int:
    """2c. The verdict logic on known answers: metrics, BOUND never PASS, a crash
    is a named FAIL, the scorecard scrubbed, Gemini only behind its flag."""
    code, summary = _pytest("tests/test_eval_pipeline.py")
    measured(summary)
    if code != 0:
        failed("the eval's own tests failed")
        return 1
    return 0


def check_mutation() -> int:
    """3. A deliberately broken beat-sync (music 100 ms late) fails the beat gate,
    and the same render unbroken passes it."""
    code, value, why = _script(str(SCRIPTS / "eval_pipeline.py"), "--harness-check", timeout=1800)
    measured(value or "no measurement")
    if code != 0:
        failed(why or "the harness check failed")
        return 1
    return 0


def check_ci_shape() -> int:
    """3b. CI: unit tests and vitest on every PR, no GPU, coverage floors, audits."""
    ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    needles = {
        "runs on PRs": "pull_request:",
        "CPU tests only": '-m "not gpu and not slow"',
        "coverage floors": "coverage_floor.py",
        "vitest": "npm run test",
        "pip-audit": "pip-audit --strict",
        "npm audit": "npm audit --omit=dev --audit-level=high",
    }
    missing = [name for name, needle in needles.items() if needle not in ci]
    measured(f"{len(needles) - len(missing)} of {len(needles)} present")
    steps = "\n".join(ln for ln in ci.splitlines() if not ln.strip().startswith("#"))
    gpu_steps = "make test-gpu" in steps or "-m gpu" in steps or '-m "gpu"' in steps
    if missing or gpu_steps:
        failed("ci.yml is missing: " + ", ".join(missing) if missing else "a GPU step in CI")
        return 1
    return 0


def check_ci_time() -> int:
    """3c. The newest CI run on GitHub finished in under 10 minutes (read with gh)."""
    gh = shutil.which("gh")
    if gh is None:
        measured("gh not installed")
        failed("read the PR's CI duration on GitHub, or install gh (VERIFY-LATER.md item 11)")
        return 3
    listing = subprocess.run(
        [
            gh,
            "run",
            "list",
            "--workflow",
            "ci.yml",
            "--limit",
            "1",
            "--json",
            "status,conclusion,createdAt,updatedAt",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    runs = json.loads(listing.stdout or "[]") if listing.returncode == 0 else []
    if not runs or runs[0].get("status") != "completed":
        measured("no completed CI run visible to gh")
        failed("open the PR so CI runs, then re-run this gate")
        return 3
    run = runs[0]
    start = datetime.fromisoformat(run["createdAt"].replace("Z", "+00:00"))
    end = datetime.fromisoformat(run["updatedAt"].replace("Z", "+00:00"))
    seconds = (end - start).total_seconds()
    measured(f"{seconds / 60:.1f} min, {run.get('conclusion')} (budget 10 min)")
    if seconds >= CI_BUDGET_S or run.get("conclusion") != "success":
        failed("CI over budget or not green")
        return 1
    return 0


def check_security() -> int:
    """4. The standing security suite: a regression test for every finding to
    date (the ledger), the original 44, and the UI's two."""
    code, summary = _pytest("tests/test_security.py", "tests/test_security_findings.py")
    ui, ui_summary = _vitest("lib/env.test.ts", "lib/api/csp.test.ts")
    measured(f"engine {summary}; ui {re.sub(chr(27) + r'[[0-9;]*m', '', ui_summary)}")
    if code or ui:
        failed("a security regression test failed")
        return 1
    return 0


def check_gemini_mocked() -> int:
    """5. No test reaches Gemini: the key is blanked and the base dead-ended for
    every test and child process; the one live call is opt-in."""
    conftest = (ENGINE / "tests" / "conftest.py").read_text(encoding="utf-8")
    pinned = '"GEMINI_API_KEY": ""' in conftest and "_DEAD_END_GEMINI_BASE" in conftest
    # A test may name Google's host only behind a mock transport.
    hosts = [
        p.name
        for p in (ENGINE / "tests").glob("test_*.py")
        if "generativelanguage.googleapis.com" in (text := p.read_text(encoding="utf-8"))
        and "MockTransport" not in text
    ]
    code, summary = _pytest("tests/test_eval_pipeline.py::test_the_live_gemini_gate_is_opt_in")
    measured(f"conftest pins key+base: {pinned}; unmocked Google hosts: {len(hosts)}; {summary}")
    if not pinned or hosts or code:
        failed("a path to the real Gemini exists in the test suite")
        return 1
    return 0


def check_gc() -> int:
    """6. Orphan collection (owed since amendment 004): zero unreferenced bytes after."""
    code, summary = _pytest("tests/test_media_gc.py")
    measured(summary)
    if code != 0:
        failed("orphan collection left something or removed something referenced")
        return 1
    return 0


def check_lint() -> int:
    """7. ruff and mypy pinned, and clean on engine and scripts."""
    pyproject = (ENGINE / "pyproject.toml").read_text(encoding="utf-8")
    pins = all(re.search(rf'"{tool}==[0-9.]+"', pyproject) for tool in ("ruff", "mypy"))
    runs = [
        [sys.executable, "-m", "ruff", "check", "engine", "scripts"],
        [sys.executable, "-m", "ruff", "format", "--check", "engine", "scripts"],
        [sys.executable, "-m", "mypy", "--config-file", "engine/pyproject.toml", "engine"],
    ]
    codes = [
        subprocess.run(r, cwd=REPO_ROOT, capture_output=True, timeout=900, check=False).returncode
        for r in runs
    ]
    measured(f"pinned: {pins}; ruff {codes[0]}, format {codes[1]}, mypy {codes[2]}")
    if not pins or any(codes):
        failed("a linter is unpinned or not clean")
        return 1
    return 0


CHECKS: dict[str, Callable[[], int]] = {
    "engine-suite": check_engine_suite,
    "ui-suite": check_ui_suite,
    "browser-e2e": check_browser_e2e,
    "eval-scorecard": check_eval_scorecard,
    "fixtures": check_fixtures,
    "eval-honesty": check_eval_honesty,
    "mutation": check_mutation,
    "ci-shape": check_ci_shape,
    "ci-time": check_ci_time,
    "security": check_security,
    "gemini-mocked": check_gemini_mocked,
    "gc": check_gc,
    "lint": check_lint,
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
