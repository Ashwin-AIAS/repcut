"""One project's edit: gather the inputs, plan, re-sync on overrides, log taste.

The orchestration layer around the pure modules. It reads what the database and
the derived store hold - signals, scenes, analysed tracks, grading choices - and
hands plain values to ``segments``, ``recommend`` and ``planner``. It writes two
things: the project's ``edit_plans`` row (decisions + plan) and one
``taste_events`` row per override (P3).

Music is resolved here, not in the planner: "suggested" means the top
recommendation, "chosen" a track the person picked (if it is still in the
library and analysed), "off" means the pacing grid (an empty library behaves
like "off" and says so).
"""

import asyncio
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repcut.analysis.params import SCENE_PARAMS_VERSION
from repcut.analysis.pipeline import GEMINI_PROMPT_VERSION
from repcut.db.models import (
    ChoiceScope,
    EditPlan,
    GeminiSceneCache,
    GradeChoice,
    MediaFile,
    MusicTrack,
    Project,
    Scene,
    TasteEvent,
)
from repcut.edit import ducking
from repcut.edit.resync import Override, apply, timed_replan
from repcut.edit.segments import usable_segments
from repcut.edit.signals import (
    SIGNALS_FILENAME,
    SIGNALS_KIND,
    SIGNALS_VERSION,
    ClipSignals,
    measure,
)
from repcut.edit.types import BeatGrid, Decisions, Pace, Plan, Segment, Shot
from repcut.edit.vad import MODEL_STORED_PATH
from repcut.edit.vad import SAMPLE_RATE as VAD_RATE
from repcut.grading.packs import PackLibrary
from repcut.grading.service import GradingNotReadyError, load_clip
from repcut.grading.theme import Theme
from repcut.highlights.detector import HeroMoment, SceneNote, detect
from repcut.media.ffmpeg_builder import build_pcm_decode, run, temp_target
from repcut.media.store import absolute, derived_path
from repcut.music.library import stored_analysis
from repcut.music.recommend import (
    LibraryTrack,
    Recommendation,
    recommend,
    session_profile,
)

PACK_ID = "gym"
TASTE_SCHEMA_VERSION = 1

NO_LIBRARY = "your music library is empty; this edit is cut to the theme's pace, without music"
CHOSEN_GONE = "the track you chose is no longer in your library; using the suggestion"
SIGNALS_PENDING = "some clips are still being measured; their footage is not in this edit yet"


class EditNotReadyError(LookupError):
    """Nothing to edit yet. A fixed sentence."""


@dataclass(frozen=True, slots=True)
class EditState:
    decisions: Decisions
    plan: Plan
    theme: Theme
    recommendations: tuple[Recommendation, ...]
    segments: tuple[Segment, ...]
    ramps: tuple[ducking.Ramp, ...]
    speech_measured: bool
    clips_pending: tuple[str, ...]
    warnings: tuple[str, ...] = field(default=())
    resync_seconds: float = 0.0
    heroes: tuple[HeroMoment, ...] = field(default=())


# --- signals -------------------------------------------------------------------


def signals_path(data_dir: Path, sha256: str) -> Path:
    stored = derived_path(sha256, SIGNALS_KIND, SIGNALS_VERSION, SIGNALS_FILENAME)
    return absolute(data_dir, stored)


def read_signals(data_dir: Path, sha256: str) -> ClipSignals | None:
    path = signals_path(data_dir, sha256)
    if not path.is_file():
        return None
    signals = ClipSignals.from_json(path.read_text(encoding="utf-8"))
    return signals if signals.version == SIGNALS_VERSION else None


async def measure_clip(session: AsyncSession, data_dir: Path, media_file: MediaFile) -> ClipSignals:
    """Measure one clip's proxy and cache the result. Idempotent."""
    clip = await load_clip(session, data_dir, media_file)
    target = signals_path(data_dir, clip.sha256)
    target.parent.mkdir(parents=True, exist_ok=True)
    pcm = temp_target(target.with_name("audio16k.f32"))
    try:
        await run(build_pcm_decode(clip.proxy, pcm, sample_rate=VAD_RATE))
        audio = await asyncio.to_thread(np.fromfile, pcm, dtype=np.float32)
    finally:
        pcm.unlink(missing_ok=True)
    duration = clip.duration_seconds or len(audio) / VAD_RATE
    model = absolute(data_dir, MODEL_STORED_PATH)
    signals = await asyncio.to_thread(measure, clip.proxy, audio, duration, model)
    partial = temp_target(target)
    partial.write_text(signals.to_json(), encoding="utf-8")
    partial.replace(target)
    return signals


# --- inputs --------------------------------------------------------------------


async def project_clips(session: AsyncSession, project_id: str) -> list[MediaFile]:
    if await session.get(Project, project_id) is None:
        raise EditNotReadyError("no project with that id")
    statement = (
        select(MediaFile)
        .where(MediaFile.project_id == project_id)
        .order_by(MediaFile.position, MediaFile.added_at)
    )
    return list((await session.execute(statement)).scalars())


async def _scenes(
    session: AsyncSession, sha256: str
) -> list[tuple[Scene, GeminiSceneCache | None]]:
    statement = (
        select(Scene, GeminiSceneCache)
        .outerjoin(
            GeminiSceneCache,
            (GeminiSceneCache.scene_id == Scene.id)
            & (GeminiSceneCache.gemini_prompt_version == GEMINI_PROMPT_VERSION),
        )
        .where(Scene.sha256 == sha256, Scene.detector_params_version == SCENE_PARAMS_VERSION)
        .order_by(Scene.sequence_index)
    )
    return [(scene, cache) for scene, cache in (await session.execute(statement)).all()]


async def project_theme(session: AsyncSession, project_id: str) -> Theme:
    """The theme the person chose most for this project's clips, else the pack default."""
    pack = PackLibrary().load(PACK_ID)
    statement = select(GradeChoice.theme_id).where(
        GradeChoice.project_id == project_id,
        GradeChoice.scene_id.is_(None),
        GradeChoice.theme_id.is_not(None),
    )
    chosen = [theme for theme in (await session.execute(statement)).scalars() if theme]
    if chosen:
        most, _ = sorted(Counter(chosen).items(), key=lambda kv: (-kv[1], kv[0]))[0]
        return pack.theme(most)
    return pack.theme(pack.manifest.default_theme)


def pace_of(theme: Theme) -> Pace:
    return Pace(
        energy=theme.pace.energy, calm_beats=theme.pace.calm_beats, peak_beats=theme.pace.peak_beats
    )


async def library(session: AsyncSession) -> list[tuple[MusicTrack, LibraryTrack]]:
    rows = (await session.execute(select(MusicTrack).where(MusicTrack.present.is_(True)))).scalars()
    out = []
    for row in rows:
        analysis = stored_analysis(row)
        if analysis is not None:
            out.append((row, LibraryTrack(row.sha256, row.display_name, analysis)))
    return out


# --- planning ------------------------------------------------------------------


async def load_decisions(session: AsyncSession, project_id: str) -> tuple[Decisions, Plan | None]:
    row = await session.get(EditPlan, project_id)
    if row is None:
        return Decisions(), None
    decisions = Decisions.from_json(json.loads(row.decisions_json))
    return decisions, _plan_from_json(json.loads(row.plan_json))


def _plan_from_json(data: dict[str, Any]) -> Plan:
    return Plan(
        shots=tuple(Shot.from_json(item) for item in data["shots"]),
        duration_seconds=float(data["duration_seconds"]),
        target_seconds=float(data["target_seconds"]),
        music_start_seconds=float(data["music_start_seconds"]),
        snapped=bool(data["snapped"]),
        track_sha256=data.get("track_sha256"),
        seed=int(data["seed"]),
        warnings=tuple(str(w) for w in data.get("warnings", [])),
    )


async def build(
    session: AsyncSession,
    data_dir: Path,
    project_id: str,
    decisions: Decisions,
    *,
    theme: Theme | None = None,
) -> EditState:
    """Plan this project's edit under ``decisions``. Reads; does not write.

    ``theme`` overrides the project's for a preview (the review script's Moody
    edit) without writing a grading choice, so no taste event is invented.
    """
    clips = await project_clips(session, project_id)
    if not clips:
        raise EditNotReadyError("this project has no clips yet")
    theme = theme or await project_theme(session, project_id)
    pace = pace_of(theme)

    segments: list[Segment] = []
    pending: list[str] = []
    speech: dict[str, Any] = {}
    measured: dict[str, ClipSignals] = {}
    notes: list[SceneNote] = []
    scene_rows: list[tuple[float, float | None, str | None]] = []
    for clip in clips:
        scenes = await _scenes(session, clip.sha256)
        scene_rows.extend(
            (s.end_seconds - s.start_seconds, s.energy_score, c.energy_level if c else None)
            for s, c in scenes
        )
        notes.extend(
            SceneNote(
                sha256=clip.sha256,
                start=s.start_seconds,
                end=s.end_seconds,
                content_type=c.content_type if c else None,
                exercise_guess=c.exercise_guess if c else None,
                energy_level=c.energy_level if c else None,
            )
            for s, c in scenes
        )
        signals = read_signals(data_dir, clip.sha256)
        if signals is None:
            pending.append(clip.id)
            continue
        measured[clip.sha256] = signals
        speech[clip.sha256] = signals.speech
        starts = [s.start_seconds for s, _ in scenes]
        segments.extend(usable_segments(clip.sha256, signals, starts, decisions.boundaries))

    tracks = await library(session)
    recs = tuple(recommend([t for _, t in tracks], session_profile(scene_rows), pace))
    by_sha = {t.sha256: t for _, t in tracks}
    warnings: list[str] = []
    grid: BeatGrid | None = None
    if decisions.music == "chosen" and decisions.track_sha256 in by_sha:
        chosen = by_sha[decisions.track_sha256]
        grid = BeatGrid.from_analysis(chosen.analysis, chosen.sha256)
    elif decisions.music != "off":
        if decisions.music == "chosen":
            warnings.append(CHOSEN_GONE)
        if recs:
            top = by_sha[recs[0].sha256]
            grid = BeatGrid.from_analysis(top.analysis, top.sha256)
        else:
            warnings.append(NO_LIBRARY)
    if pending:
        warnings.append(SIGNALS_PENDING)

    heroes = detect(segments, measured, notes)
    result = timed_replan(tuple(segments), grid, pace, decisions, heroes)
    on_timeline = ducking.timeline_speech(result.plan, speech)
    energy = {segment.id: segment.energy for segment in segments}
    ramps = tuple(ducking.envelope(result.plan, on_timeline, energy)) if grid else ()
    return EditState(
        decisions=decisions,
        plan=result.plan,
        theme=theme,
        recommendations=recs,
        segments=tuple(segments),
        ramps=ramps,
        speech_measured=all(speech.get(c.sha256) is not None for c in clips if c.id not in pending),
        clips_pending=tuple(pending),
        warnings=(*warnings, *result.plan.warnings),
        resync_seconds=result.seconds,
        heroes=heroes,
    )


async def _save(session: AsyncSession, project_id: str, state: EditState) -> None:
    row = await session.get(EditPlan, project_id)
    decisions_json = json.dumps(state.decisions.to_json())
    plan_json = json.dumps(state.plan.to_json())
    if row is None:
        session.add(
            EditPlan(project_id=project_id, decisions_json=decisions_json, plan_json=plan_json)
        )
    else:
        row.decisions_json = decisions_json
        row.plan_json = plan_json
    await session.commit()


async def current(session: AsyncSession, data_dir: Path, project_id: str) -> EditState:
    """The edit as it stands, re-planned from the stored decisions and saved."""
    decisions, _ = await load_decisions(session, project_id)
    state = await build(session, data_dir, project_id, decisions)
    await _save(session, project_id, state)
    return state


def _features(state: EditState) -> dict[str, Any]:
    plan = state.plan
    return {
        "theme": state.theme.id,
        "target_seconds": state.decisions.target_seconds,
        "music": state.decisions.music,
        "track_sha256": plan.track_sha256,
        "bpm": next((r.bpm for r in state.recommendations if r.sha256 == plan.track_sha256), None),
        "shots": len(plan.shots),
        "duration_seconds": plan.duration_seconds,
        "segments_available": len(state.segments),
    }


def _shot_features(state: EditState, shot_id: str | None) -> dict[str, Any]:
    shot = next((s for s in state.plan.shots if s.id == shot_id), None)
    if shot is None:
        return {}
    segment = next((s for s in state.segments if s.id == shot.segment_id), None)
    return {
        "shot": shot.to_json(),
        "segment_quality": segment.quality if segment else None,
        "segment_energy": segment.energy if segment else None,
    }


def _recommended(state: EditState, override: Override) -> dict[str, Any]:
    kind = override.kind
    if kind.startswith("music"):
        top = state.recommendations[0] if state.recommendations else None
        return {"value": top.sha256 if top else None, "reason": top.reason if top else None}
    if kind == "target_duration":
        return {"value": state.decisions.target_seconds}
    if kind in ("slowmo_on", "slowmo_off"):
        hero = next((h for h in state.heroes if h.segment_id == override.segment_id), None)
        slow = any(s.slow and s.segment_id == override.segment_id for s in state.plan.shots)
        return {
            "value": override.segment_id if slow else None,
            "hero_score": hero.score if hero else None,
            "hero_signals": dict(hero.signals) if hero else None,
        }
    if kind in ("cut_removed", "cut_locked", "cut_unlocked"):
        return {"value": override.shot_id, **_shot_features(state, override.shot_id)}
    return {"value": None}


def _chosen(override: Override) -> dict[str, Any]:
    value: Any = (
        override.track_sha256
        or override.target_seconds
        or override.shot_id
        or override.segment_id
        or (
            {
                "sha256": override.boundary.sha256,
                "action": override.boundary.action,
                "seconds": override.boundary.seconds,
            }
            if override.boundary
            else None
        )
    )
    return {"value": value}


async def apply_override(
    session: AsyncSession, data_dir: Path, project_id: str, override: Override
) -> EditState:
    """Fold one override into the decisions, re-plan everything, log the taste event."""
    before = await current(session, data_dir, project_id)
    decisions = apply(before.decisions, before.plan, override)
    after = await build(session, data_dir, project_id, decisions)
    session.add(
        TasteEvent(
            schema_version=TASTE_SCHEMA_VERSION,
            kind=override.kind,
            scope=ChoiceScope.EDIT.value if override.boundary is None else ChoiceScope.CLIP.value,
            project_id=project_id,
            sha256=override.boundary.sha256 if override.boundary else None,
            pack_id=None,
            recommended_json=json.dumps(_recommended(before, override)),
            chosen_json=json.dumps(_chosen(override)),
            features_json=json.dumps(_features(before)),
        )
    )
    await _save(session, project_id, after)
    return after


async def clips_without_signals(
    session: AsyncSession, data_dir: Path, project_id: str
) -> list[MediaFile]:
    return [
        c
        for c in await project_clips(session, project_id)
        if read_signals(data_dir, c.sha256) is None
    ]


__all__ = [
    "CHOSEN_GONE",
    "NO_LIBRARY",
    "EditNotReadyError",
    "EditState",
    "GradingNotReadyError",
    "apply_override",
    "build",
    "clips_without_signals",
    "current",
    "measure_clip",
    "read_signals",
    "signals_path",
]
