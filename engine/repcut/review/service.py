"""Builds the edit record ``summary`` reads, and the before/after for one scene.

The record is assembled from what is stored and measured - the saved plan and
decisions, each used clip's grade, the caption settings and lines, the dead
space the edit skipped, the track - never from what a template expects. Only
scenes the edit actually shows are listed under a clip's grade, so "corrected
white balance on this clip" is about footage the viewer sees.
"""

from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from repcut.captions import service as captions_service
from repcut.db.models import MediaFile, MusicTrack
from repcut.edit import service as edit_service
from repcut.edit.segments import dead_stretches
from repcut.grading.service import GradingNotReadyError, clip_grade
from repcut.review.summary import Claim, summarise, verify


class SummaryInconsistentError(RuntimeError):
    """A claim the record does not back. Never shown as a claim; it is a bug."""


def _used_ranges(state: edit_service.EditState, sha256: str) -> list[tuple[float, float]]:
    return [s.footprint() for s in state.plan.shots if s.sha256 == sha256]


def _overlaps(ranges: list[tuple[float, float]], start: float, end: float) -> bool:
    return any(a < end and b > start for a, b in ranges)


async def _grading(
    session: AsyncSession, data_dir: Path, state: edit_service.EditState, clips: list[MediaFile]
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for clip in clips:
        used = _used_ranges(state, clip.sha256)
        if not used:
            continue
        try:
            grade = await clip_grade(session, data_dir, clip)
        except GradingNotReadyError:
            continue
        scenes = [
            {
                "key": scene.unit.key,
                "start_seconds": scene.unit.start_seconds,
                "end_seconds": scene.unit.end_seconds,
                "theme_id": scene.theme.id,
                "theme_name": scene.theme.name,
                "intensity": scene.intensity,
                "temperature_shift_k": round(scene.correction.temperature_shift_k, 1),
                "exposure_stops": round(scene.correction.exposure_stops, 4),
            }
            for scene in grade.scenes
            if _overlaps(used, scene.unit.start_seconds, scene.unit.end_seconds)
        ]
        if scenes:
            out.append({"sha256": clip.sha256, "scenes": scenes})
    return out


def _dead_space(
    data_dir: Path, state: edit_service.EditState, clips: list[MediaFile]
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for clip in clips:
        if not _used_ranges(state, clip.sha256):
            continue
        signals = edit_service.read_signals(data_dir, clip.sha256)
        if signals is None:
            continue
        out.extend(
            {"sha256": clip.sha256, "start": start, "end": end}
            for start, end in dead_stretches(signals)
        )
    return out


async def record(session: AsyncSession, data_dir: Path, project_id: str) -> dict[str, Any]:
    """The edit record: everything the summary may state, as stored and measured."""
    state = await edit_service.current(session, data_dir, project_id)
    clips = await edit_service.project_clips(session, project_id)
    music: dict[str, Any] | None = None
    if state.plan.track_sha256 is not None:
        track = await session.get(MusicTrack, state.plan.track_sha256)
        if track is not None:
            music = {
                "sha256": track.sha256,
                "display_name": track.display_name,
                "bpm": round(float(track.bpm or 0.0), 1),
            }
    captions = await captions_service.state(session, data_dir, project_id, edit=state)
    return {
        "plan": state.plan.to_json(),
        "decisions": state.decisions.to_json(),
        "theme": {"id": state.theme.id, "name": state.theme.name},
        "clip_names": {clip.sha256: clip.display_name for clip in clips},
        "music": music,
        "grading": await _grading(session, data_dir, state, clips),
        "dead_space": _dead_space(data_dir, state, clips),
        "captions": {
            "enabled": captions.settings.enabled,
            "style_id": captions.style.id,
            "style_name": captions.style.name,
            "lines": [
                {"id": line.id, "start": round(line.start, 3), "end": round(line.end, 3)}
                for line in captions.lines
                if not line.hidden
            ],
        },
    }


def checked(edit_record: dict[str, Any]) -> list[Claim]:
    """The summary of ``edit_record``, refused if any claim is not backed by it."""
    claims = summarise(edit_record)
    problems = verify(edit_record, claims)
    if problems:
        raise SummaryInconsistentError("; ".join(problems))
    return claims


__all__ = ["SummaryInconsistentError", "checked", "record"]
