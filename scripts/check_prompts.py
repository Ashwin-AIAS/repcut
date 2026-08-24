#!/usr/bin/env python3
"""CLI view of Repcut build plan completion status.

The plan is read at runtime from the private guide (``REPCUT_GUIDE_PATH``); it
is not part of this repository. Without it this prints a named message and exits
0 - a clone that has no guide is not a broken checkout.

Usage:
    python scripts/check_prompts.py
"""

import sys
from pathlib import Path

# Windows consoles default to a legacy code page; the wave titles are UTF-8.
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        # Named: a stream that is not reconfigurable (a pipe wrapper under some
        # runners). Losing the nicer encoding is not worth failing the command.
        pass

# Add engine directory to sys.path so the repcut package imports cleanly.
repo_root = Path(__file__).resolve().parent.parent
engine_dir = repo_root / "engine"
if str(engine_dir) not in sys.path:
    sys.path.insert(0, str(engine_dir))

from repcut.prompt_tracker import PromptsTrackerResponse, get_all_prompts_status  # noqa: E402


def main() -> None:
    """Print a formatted terminal table of prompt completion status."""
    report: PromptsTrackerResponse = get_all_prompts_status(repo_root)

    print("=" * 80)
    print(" REPCUT - PROMPT COMPLETION DASHBOARD")
    print("=" * 80)

    if not report.guide_available:
        print(" Build plan unavailable.")
        print()
        for line in (report.unavailable_reason or "No reason given.").split(". "):
            if line.strip():
                print(f"   {line.strip().rstrip('.')}.")
        print("=" * 80)
        return

    print(
        f" Total Prompts: {report.total_prompts} | "
        f"Passed: {report.passed_count} | "
        f"In Progress: {report.in_progress_count} | "
        f"Pending: {report.pending_count}"
    )
    print(f" Overall Completion: [{report.overall_completion_percentage:.1f}%]")
    print("-" * 80)

    print(" WAVES SUMMARY:")
    for wave in report.waves:
        passed_str = f"{wave.passed_prompts}/{wave.total_prompts}"
        pct_bar = f"{wave.completion_percentage:5.1f}%"
        bar_len = int(wave.completion_percentage // 10)
        progress_bar = "#" * bar_len + "-" * (10 - bar_len)
        print(
            f"   {wave.wave_title:<28}: "
            f"[{progress_bar}] {passed_str:>5} prompts ({pct_bar}) "
            f"-- Est: {wave.estimated_timeline}"
        )

    print("\n" + "=" * 80)
    print(f" {'ID':<4} | {'STATUS':<13} | {'NAME':<36} | {'GATE COMMAND':<15}")
    print("-" * 80)

    for item in report.prompts:
        meta = item.metadata
        status_symbol = {
            "PASSED": "[PASS]       ",
            "IN_PROGRESS": "[IN PROGRESS]",
            "PENDING": "[PENDING]    ",
        }.get(item.status, item.status)

        human_tag = " *" if meta.human_review else "  "
        name_display = (meta.name[:33] + "...") if len(meta.name) > 36 else meta.name

        print(
            f" {meta.id:<4} | {status_symbol} | {name_display:<36}{human_tag} | "
            f"{meta.gate_command:<15}"
        )

    print("=" * 80)
    print(" Note: * indicates a Human Review checkpoint (taste gate or wave gate)")
    print(" Run 'make verify-XX' to run the verification gate for Prompt XX")
    print("=" * 80)


if __name__ == "__main__":
    main()
