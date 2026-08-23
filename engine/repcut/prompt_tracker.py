"""Dynamic status detector for Repcut build plan prompts.

The plan itself comes from the guide at runtime (``prompts_data``); this module
adds only what the filesystem can answer - which reports and gate scripts exist.
When the guide is unavailable the response says so in a named way rather than
rendering an empty dashboard.
"""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from repcut.prompts_data import (
    GuideUnavailableError,
    PromptMetadata,
    WaveDefinition,
    load_prompts_metadata,
)

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
    """Overall build plan status report.

    ``guide_available`` is false on any machine without the private build plan -
    a fresh clone, or CI. That is the expected state there, not an error, so the
    response is still a 200 with an empty plan and a reason the UI can render.
    """

    guide_available: bool
    unavailable_reason: str | None = None
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
        notes = "Report present and gate script verified"
        return "PASSED", report_exists, gate_script_exists, notes
    if report_exists or gate_script_exists:
        return "IN_PROGRESS", report_exists, gate_script_exists, "Report or gate script in progress"
    return "PENDING", report_exists, gate_script_exists, "Pending execution"


def _summarise_waves(
    items: list[PromptStatusItem], waves: dict[int, WaveDefinition]
) -> list[WaveSummary]:
    """Roll per-prompt status up into the waves the guide defines."""
    summaries: list[WaveSummary] = []
    for number in sorted(waves):
        wave = waves[number]
        members = [item for item in items if item.metadata.wave_number == number]
        total = len(members)
        passed = sum(1 for item in members if item.status == "PASSED")
        in_progress = sum(1 for item in members if item.status == "IN_PROGRESS")
        pending = sum(1 for item in members if item.status == "PENDING")

        summaries.append(
            WaveSummary(
                wave_number=number,
                wave_title=wave.title,
                total_prompts=total,
                passed_prompts=passed,
                in_progress_prompts=in_progress,
                pending_prompts=pending,
                completion_percentage=round((passed / total) * 100.0, 1) if total else 0.0,
                estimated_timeline=wave.estimated_timeline,
            )
        )
    return summaries


def _unavailable(reason: str) -> PromptsTrackerResponse:
    """An empty, honest report for a machine that does not have the plan."""
    return PromptsTrackerResponse(
        guide_available=False,
        unavailable_reason=reason,
        total_prompts=0,
        passed_count=0,
        in_progress_count=0,
        pending_count=0,
        overall_completion_percentage=0.0,
        prompts=[],
        waves=[],
    )


def get_all_prompts_status(repo_root: Path | None = None) -> PromptsTrackerResponse:
    """Evaluate and aggregate live build plan prompt completion status."""
    if repo_root is None:
        # engine/repcut/prompt_tracker.py -> engine/repcut -> engine -> repo root.
        repo_root = Path(__file__).resolve().parents[2]

    try:
        metadata = load_prompts_metadata()
    except GuideUnavailableError as error:
        # Named: this machine has no readable copy of the private build plan.
        # Expected on a fresh clone and in CI - report it, do not raise.
        return _unavailable(error.reason)

    items: list[PromptStatusItem] = []
    waves: dict[int, WaveDefinition] = {}
    for meta in metadata:
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
        if meta.wave_number not in waves:
            waves[meta.wave_number] = WaveDefinition(
                number=meta.wave_number,
                title=meta.wave,
                prompt_ids=[],
                estimated_timeline=meta.estimated_timeline,
            )

    total = len(items)
    passed = sum(1 for item in items if item.status == "PASSED")
    in_progress = sum(1 for item in items if item.status == "IN_PROGRESS")
    pending = sum(1 for item in items if item.status == "PENDING")

    return PromptsTrackerResponse(
        guide_available=True,
        unavailable_reason=None,
        total_prompts=total,
        passed_count=passed,
        in_progress_count=in_progress,
        pending_count=pending,
        overall_completion_percentage=round((passed / total) * 100.0, 1) if total else 0.0,
        prompts=items,
        waves=_summarise_waves(items, waves),
    )
