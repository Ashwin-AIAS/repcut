"""Dynamic status detector and tracker for Repcut build plan prompts.

Scans workspace filesystem to ascertain prompt verification state.
"""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from repcut.prompts_data import PROMPTS_METADATA, PromptMetadata

PromptStatus = Literal["PASSED", "IN_PROGRESS", "PENDING"]


class PromptStatusItem(BaseModel):
    """Dynamic status report item for a single prompt."""

    metadata: PromptMetadata
    status: PromptStatus
    report_exists: bool
    gate_script_exists: bool
    notes: str


class WaveSummary(BaseModel):
    """Completion metrics for a single build wave."""

    wave_number: int
    wave_title: str
    total_prompts: int
    passed_prompts: int
    in_progress_prompts: int
    pending_prompts: int
    completion_percentage: float
    estimated_timeline: str


class PromptsTrackerResponse(BaseModel):
    """Overall build plan status report."""

    total_prompts: int
    passed_count: int
    in_progress_count: int
    pending_count: int
    overall_completion_percentage: float
    prompts: list[PromptStatusItem]
    waves: list[WaveSummary]


def _detect_prompt_status(
    meta: PromptMetadata, repo_root: Path
) -> tuple[PromptStatus, bool, bool, str]:
    """Detect prompt status based on workspace filesystem evidence."""
    report_path = repo_root / meta.report_file
    gate_path = repo_root / f"scripts/verify_{meta.id}.sh"

    report_exists = report_path.is_file()
    gate_script_exists = gate_path.is_file()

    if report_exists and gate_script_exists:
        status: PromptStatus = "PASSED"
        notes = f"Report present ({meta.report_file}) & gate script verified"
    elif meta.id == "01":
        # Prompt 01 scaffold check: engine/ repcut module and ui/ app exist
        engine_exists = (repo_root / "engine/repcut/main.py").is_file()
        ui_exists = (repo_root / "ui/app/page.tsx").is_file()
        if engine_exists and ui_exists:
            status = "PASSED" if (report_exists or gate_script_exists) else "IN_PROGRESS"
            notes = (
                "Engine & UI scaffold present"
                if status == "PASSED"
                else "Scaffold built, gate/report pending"
            )
        else:
            status = "PENDING"
            notes = "Pending execution"
    elif report_exists or gate_script_exists:
        status = "IN_PROGRESS"
        notes = "Report or gate script in progress"
    else:
        status = "PENDING"
        notes = "Pending execution"

    return status, report_exists, gate_script_exists, notes


def get_all_prompts_status(repo_root: Path | None = None) -> PromptsTrackerResponse:
    """Evaluate and aggregate live build plan prompt completion status."""
    if repo_root is None:
        # Default to repo root (three levels up from engine/repcut/prompt_tracker.py)
        repo_root = Path(__file__).resolve().parent.parent.parent

    items: list[PromptStatusItem] = []
    for meta in PROMPTS_METADATA:
        status, report_exists, gate_exists, notes = _detect_prompt_status(meta, repo_root)
        items.append(
            PromptStatusItem(
                metadata=meta,
                status=status,
                report_exists=report_exists,
                gate_script_exists=gate_exists,
                notes=notes,
            )
        )

    # Calculate overall metrics
    total = len(items)
    passed = sum(1 for i in items if i.status == "PASSED")
    in_progress = sum(1 for i in items if i.status == "IN_PROGRESS")
    pending = sum(1 for i in items if i.status == "PENDING")
    overall_pct = round((passed / total) * 100.0, 1) if total > 0 else 0.0

    # Group by Wave
    wave_titles = {
        0: ("Wave 0", "1 week"),
        1: ("Wave 1", "6-8 weeks"),
        2: ("Wave 2", "4-5 weeks"),
        3: ("Wave 3", "5-6 weeks"),
        4: ("Wave 4", "1-2 weeks"),
        5: ("Wave 5", "1 week"),
    }

    wave_summaries: list[WaveSummary] = []
    for w_num in range(6):
        w_title, w_time = wave_titles[w_num]
        w_items = [i for i in items if i.metadata.wave_number == w_num]
        w_total = len(w_items)
        w_passed = sum(1 for i in w_items if i.status == "PASSED")
        w_prog = sum(1 for i in w_items if i.status == "IN_PROGRESS")
        w_pend = sum(1 for i in w_items if i.status == "PENDING")
        w_pct = round((w_passed / w_total) * 100.0, 1) if w_total > 0 else 0.0

        wave_summaries.append(
            WaveSummary(
                wave_number=w_num,
                wave_title=w_title,
                total_prompts=w_total,
                passed_prompts=w_passed,
                in_progress_prompts=w_prog,
                pending_prompts=w_pend,
                completion_percentage=w_pct,
                estimated_timeline=w_time,
            )
        )

    return PromptsTrackerResponse(
        total_prompts=total,
        passed_count=passed,
        in_progress_count=in_progress,
        pending_count=pending,
        overall_completion_percentage=overall_pct,
        prompts=items,
        waves=wave_summaries,
    )
