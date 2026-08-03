#!/usr/bin/env python3
"""CLI utility to check and display Repcut prompt completion status in terminal.

Usage:
    python scripts/check_prompts.py
    make prompts
"""

import sys
from pathlib import Path

# Fix UTF-8 encoding output on Windows terminals
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Add engine directory to sys.path so repcut package imports cleanly
repo_root = Path(__file__).resolve().parent.parent
engine_dir = repo_root / "engine"
if str(engine_dir) not in sys.path:
    sys.path.insert(0, str(engine_dir))

from repcut.prompt_tracker import get_all_prompts_status, PromptsTrackerResponse


def main() -> None:
    """Print a clean formatted terminal table of prompt completion status."""
    report: PromptsTrackerResponse = get_all_prompts_status(repo_root)

    print("=" * 80)
    print(" REPCUT - PROMPT COMPLETION DASHBOARD")
    print(" Reference: guide prompts.pdf (v1.0)")
    print("=" * 80)
    print(
        f" Total Prompts: {report.total_prompts} | "
        f"Passed: {report.passed_count} | "
        f"In Progress: {report.in_progress_count} | "
        f"Pending: {report.pending_count}"
    )
    print(f" Overall Completion: [{report.overall_completion_percentage:.1f}%]")
    print("-" * 80)

    # Print Wave Summary Bar
    print(" WAVES SUMMARY:")
    for wave in report.waves:
        passed_str = f"{wave.passed_prompts}/{wave.total_prompts}"
        pct_bar = f"{wave.completion_percentage:5.1f}%"
        bar_len = int(wave.completion_percentage // 10)
        progress_bar = "#" * bar_len + "-" * (10 - bar_len)
        w_name = wave.wave_title.split("—")[1].strip() if "—" in wave.wave_title else wave.wave_title
        print(
            f"   {w_name:<10}: "
            f"[{progress_bar}] {passed_str:>3} prompts ({pct_bar}) -- Est: {wave.estimated_timeline}"
        )

    print("\n" + "=" * 80)
    print(f" {'ID':<4} | {'STATUS':<12} | {'NAME':<36} | {'GATE COMMAND':<15}")
    print("-" * 80)

    for item in report.prompts:
        meta = item.metadata
        status_symbol = {
            "PASSED": "[PASS]      ",
            "IN_PROGRESS": "[IN PROGRESS]",
            "PENDING": "[PENDING]   ",
        }.get(item.status, item.status)

        human_tag = " *" if meta.human_review else "  "
        name_display = (meta.name[:33] + "...") if len(meta.name) > 36 else meta.name

        print(
            f" {meta.id:<4} | {status_symbol} | {name_display:<36}{human_tag} | {meta.gate_command:<15}"
        )

    print("=" * 80)
    print(" Note: * indicates a Human Review checkpoint (taste gate or wave gate)")
    print(" Run 'make verify-XX' to run the verification gate for Prompt XX")
    print("=" * 80)


if __name__ == "__main__":
    main()
