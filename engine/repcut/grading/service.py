"""Grading for one clip in one project: suggestions, choices, overrides, renders.

The routes (``api/grading.py``) and the graded-preview job both call this. It
owns the reads and writes of ``grade_choices`` and ``taste_events`` and three
caches under ``$DATA_DIR/grading/``: per-scene measurements (numbers only),
graded stills and graded previews. Every cache path is derived from database
ids and a digest of what the file contains, never from anything a caller
passed in (`.claude/rules/security.md`), so a stale file cannot be served for
a new choice: a new choice is a new name.

How a scene's grade is decided (P2, every step an overridable default):

- **Lighting tags**: the scene's own override, else what analysis said.
- **Theme**: the scene's choice, else the clip's choice, else the clip's
  recommendation. One recommendation per clip, not per scene: one video, one
  look. Scenes adapt through their correction, not by switching themes.
- **Intensity**: the scene's choice, else the clip's, else the theme's default.
- **Correction**: measured locally on the proxy, then the theme's adaptation
  rules for the scene's tags, then the hard bounds (``corrector.py``).

Every change a person makes writes exactly one ``taste_events`` row per field
that actually changed (P3); a no-op writes nothing.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repcut.analysis.params import SCENE_PARAMS_VERSION
from repcut.analysis.pipeline import GEMINI_PROMPT_VERSION
from repcut.db.models import (
    ChoiceScope,
    DerivedArtifact,
    GeminiSceneCache,
    GradeChoice,
    MediaBlob,
    MediaFile,
    Scene,
    TasteEvent,
    TasteEventKind,
)
from repcut.grading.apply import (
    Recommendation,
    measure_timestamps,
    read_measure_frames,
    render_preview,
    render_still,
)
from repcut.grading.corrector import Correction, Measurements, SceneTags, correct, measure
from repcut.grading.lut import look_digest
from repcut.grading.packs import LoadedPack, PackLibrary
from repcut.grading.theme import Theme
from repcut.media.artifacts import PARAMS_VERSION, ArtifactKind
from repcut.media.store import UnsafeStorePathError, absolute

DEFAULT_PACK_ID = "gym"
TASTE_SCHEMA_VERSION = 1
# Bumped when what a cached measurement means changes (the measure-frame recipe
# or ``corrector.measure``); part of every measurement file's name.
MEASURE_VERSION = 1

LIGHTING_FIELDS = ("lighting_temperature", "lighting_quality")
CHOICE_FIELDS = ("theme_id", "intensity", *LIGHTING_FIELDS)

_KIND_FOR_FIELD = {
    "theme_id": TasteEventKind.THEME,
    "intensity": TasteEventKind.INTENSITY,
    "lighting_temperature": TasteEventKind.LIGHTING_TEMPERATURE,
    "lighting_quality": TasteEventKind.LIGHTING_QUALITY,
}

_HEX = re.compile(r"^[0-9a-f]{64}$")
_KEY = re.compile(r"^[0-9a-z-]{1,40}$")
CLIP_KEY = "clip"


class GradingNotReadyError(LookupError):
    """The clip has no current proxy yet, so there is nothing to measure or grade."""


class GradeChoiceError(ValueError):
    """A choice that cannot apply here (e.g. a lighting override on the whole clip)."""


class GradeSceneNotFoundError(LookupError):
    """No current scene with that id for this clip."""


# --- paths ------------------------------------------------------------------


def grading_root(data_dir: Path) -> Path:
    return data_dir / "grading"


def luts_root(data_dir: Path) -> Path:
    return grading_root(data_dir) / "luts"


def _cache_file(data_dir: Path, area: str, sha256: str, name: str) -> Path:
    """``$DATA_DIR/grading/<area>/<sha>/<name>``, shape-checked and contained."""
    if not _HEX.match(sha256):
        raise UnsafeStorePathError("sha256 is not a hex digest")
    stem, _, suffix = name.partition(".")
    if not _KEY.match(stem.replace("_", "-")) and not _HEX.match(stem):
        raise UnsafeStorePathError("cache name has an unexpected shape")
    if suffix not in {"json", "jpg", "mp4"}:
        raise UnsafeStorePathError("cache name has an unexpected suffix")
    root = grading_root(data_dir).resolve()
    candidate = (root / area / sha256 / name).resolve()
    if not candidate.is_relative_to(root):
        raise UnsafeStorePathError("resolves outside the grading cache")
    return candidate


def _digest(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


# --- the clip and its scenes ------------------------------------------------


@dataclass(frozen=True, slots=True)
class SceneUnit:
    """A span of the clip that gets one correction. ``id`` None: the whole clip, unscened."""

    id: str | None
    index: int
    start_seconds: float
    end_seconds: float
    analysed: SceneTags
    energy_score: float | None

    @property
    def key(self) -> str:
        return self.id or CLIP_KEY

    @property
    def duration(self) -> float:
        return max(self.end_seconds - self.start_seconds, 0.0)


@dataclass(frozen=True, slots=True)
class ClipRef:
    media_file_id: str
    project_id: str
    sha256: str
    proxy: Path
    duration_seconds: float


async def load_clip(session: AsyncSession, data_dir: Path, media_file: MediaFile) -> ClipRef:
    statement = select(DerivedArtifact).where(
        DerivedArtifact.sha256 == media_file.sha256,
        DerivedArtifact.artifact_kind == ArtifactKind.PROXY.value,
        DerivedArtifact.params_version == PARAMS_VERSION[ArtifactKind.PROXY],
    )
    artifact = (await session.execute(statement)).scalars().first()
    if artifact is None:
        raise GradingNotReadyError("this clip's preview has not been generated yet")
    proxy = absolute(data_dir, artifact.stored_path)
    if not proxy.is_file():
        raise GradingNotReadyError("this clip's preview has not been generated yet")
    blob = await session.get(MediaBlob, media_file.sha256)
    duration = blob.duration_seconds if blob is not None else None
    return ClipRef(
        media_file_id=media_file.id,
        project_id=media_file.project_id,
        sha256=media_file.sha256,
        proxy=proxy,
        duration_seconds=duration or 0.0,
    )


async def load_units(session: AsyncSession, clip: ClipRef) -> list[SceneUnit]:
    """Current scenes with their analysed lighting tags; one whole-clip unit if none yet."""
    statement = (
        select(Scene, GeminiSceneCache)
        .outerjoin(
            GeminiSceneCache,
            (GeminiSceneCache.scene_id == Scene.id)
            & (GeminiSceneCache.gemini_prompt_version == GEMINI_PROMPT_VERSION),
        )
        .where(Scene.sha256 == clip.sha256, Scene.detector_params_version == SCENE_PARAMS_VERSION)
        .order_by(Scene.sequence_index)
    )
    units = [
        SceneUnit(
            id=scene.id,
            index=scene.sequence_index,
            start_seconds=scene.start_seconds,
            end_seconds=scene.end_seconds,
            analysed=SceneTags(
                lighting_temperature=_tag(cache, "lighting_temperature"),
                lighting_quality=_tag(cache, "lighting_quality"),
            ),
            energy_score=scene.energy_score,
        )
        for scene, cache in (await session.execute(statement)).all()
    ]
    if units:
        return units
    return [
        SceneUnit(
            id=None,
            index=0,
            start_seconds=0.0,
            end_seconds=max(clip.duration_seconds, 0.1),
            analysed=SceneTags(),
            energy_score=None,
        )
    ]


def _tag(cache: GeminiSceneCache | None, name: str) -> str | None:
    if cache is None or cache.raw_response_json is None:
        return None
    value = getattr(cache, name)
    return value if isinstance(value, str) else None


# --- measurements (cached: numbers only, P4) ---------------------------------


async def scene_measurements(data_dir: Path, clip: ClipRef, unit: SceneUnit) -> Measurements | None:
    """Median luma, cast and noise of a scene, measured once per proxy version."""
    name = _digest(
        {
            "proxy": PARAMS_VERSION[ArtifactKind.PROXY],
            "measure": MEASURE_VERSION,
            "unit": unit.key,
            "span": [round(unit.start_seconds, 3), round(unit.end_seconds, 3)],
        }
    )
    path = _cache_file(data_dir, "measure", clip.sha256, f"{name}.json")
    if path.is_file():
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
            return None if stored.get("none") else Measurements(**stored)
        except (ValueError, TypeError):
            pass  # a corrupt cache entry is re-measured below
    frames = await read_measure_frames(
        clip.proxy, measure_timestamps(unit.start_seconds, unit.end_seconds)
    )
    measured = measure(frames)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = (
        {"none": True}
        if measured is None
        else {
            "median_luma": measured.median_luma,
            "cast_kelvin": measured.cast_kelvin,
            "noise_sigma": measured.noise_sigma,
            "frames": measured.frames,
        }
    )
    path.write_text(json.dumps(body), encoding="utf-8")
    return measured


# --- the decided grade -------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Choice:
    theme_id: str | None = None
    intensity: int | None = None
    lighting_temperature: str | None = None
    lighting_quality: str | None = None

    @classmethod
    def of(cls, row: GradeChoice | None) -> Choice:
        if row is None:
            return cls()
        return cls(
            theme_id=row.theme_id,
            intensity=row.intensity,
            lighting_temperature=row.lighting_temperature,
            lighting_quality=row.lighting_quality,
        )

    def is_empty(self) -> bool:
        return all(getattr(self, name) is None for name in CHOICE_FIELDS)

    def set_fields(self) -> dict[str, object]:
        return {n: getattr(self, n) for n in CHOICE_FIELDS if getattr(self, n) is not None}


@dataclass(frozen=True, slots=True)
class SceneGrade:
    unit: SceneUnit
    choice: Choice
    effective_tags: SceneTags
    scores: dict[str, float]
    theme: Theme
    intensity: int
    measurements: Measurements | None
    correction: Correction

    def render_key(self, pack_id: str) -> dict[str, Any]:
        """Everything a graded render of this scene depends on."""
        return {
            "proxy": PARAMS_VERSION[ArtifactKind.PROXY],
            "pack": pack_id,
            "theme": self.theme.id,
            "look": look_digest(self.theme.look),
            "grain": self.theme.finish.grain,
            "intensity": self.intensity,
            "correction": self.correction.as_features(),
            "unit": self.unit.key,
            "span": [round(self.unit.start_seconds, 3), round(self.unit.end_seconds, 3)],
        }


@dataclass(frozen=True, slots=True)
class ClipGrade:
    clip: ClipRef
    pack: LoadedPack
    recommendation: Recommendation
    choice: Choice
    theme_id: str
    intensity: int
    scenes: list[SceneGrade]

    def scene(self, scene_id: str) -> SceneGrade:
        for scene in self.scenes:
            if scene.unit.key == scene_id:
                return scene
        raise GradeSceneNotFoundError("no scene with that id exists for this clip")


def _scores(pack: LoadedPack, tags: SceneTags) -> dict[str, float]:
    scores: dict[str, float] = {}
    for theme in pack.themes:
        score = theme.recommend.base
        if tags.lighting_quality is not None:
            score += theme.recommend.lighting_quality.get(tags.lighting_quality, 0.0)  # type: ignore[call-overload]
        if tags.lighting_temperature is not None:
            score += theme.recommend.lighting_temperature.get(tags.lighting_temperature, 0.0)  # type: ignore[call-overload]
        scores[theme.id] = round(score, 4)
    return scores


def clip_recommendation(
    pack: LoadedPack, units: list[tuple[SceneUnit, SceneTags]]
) -> Recommendation:
    """Duration-weighted over the scenes' effective tags; no tags anywhere: the pack default."""
    total = sum(max(u.duration, 1e-3) for u, _ in units)
    combined = dict.fromkeys(pack.manifest.themes, 0.0)
    for unit, tags in units:
        weight = max(unit.duration, 1e-3) / total
        for theme_id, score in _scores(pack, tags).items():
            combined[theme_id] += weight * score
    combined = {k: round(v, 4) for k, v in combined.items()}
    tagged = any(t.lighting_quality or t.lighting_temperature for _, t in units)
    if not tagged:
        return Recommendation(theme_id=pack.manifest.default_theme, scores=combined)
    best = max(combined.values())
    winner = next(t for t in pack.manifest.themes if combined[t] == best)
    return Recommendation(theme_id=winner, scores=combined)


async def _choices(
    session: AsyncSession, clip: ClipRef
) -> tuple[GradeChoice | None, dict[str, GradeChoice]]:
    statement = select(GradeChoice).where(
        GradeChoice.project_id == clip.project_id, GradeChoice.sha256 == clip.sha256
    )
    clip_row: GradeChoice | None = None
    scene_rows: dict[str, GradeChoice] = {}
    for row in (await session.execute(statement)).scalars().all():
        if row.scene_id is None:
            clip_row = row
        else:
            scene_rows[row.scene_id] = row
    return clip_row, scene_rows


async def clip_grade(
    session: AsyncSession,
    data_dir: Path,
    media_file: MediaFile,
    *,
    library: PackLibrary | None = None,
    pack_id: str = DEFAULT_PACK_ID,
) -> ClipGrade:
    pack = (library or PackLibrary()).load(pack_id)
    clip = await load_clip(session, data_dir, media_file)
    units = await load_units(session, clip)
    clip_row, scene_rows = await _choices(session, clip)
    clip_choice = Choice.of(clip_row)

    tagged: list[tuple[SceneUnit, Choice, SceneTags]] = []
    for unit in units:
        choice = Choice.of(scene_rows.get(unit.id)) if unit.id else Choice()
        effective = SceneTags(
            lighting_temperature=choice.lighting_temperature or unit.analysed.lighting_temperature,
            lighting_quality=choice.lighting_quality or unit.analysed.lighting_quality,
        )
        tagged.append((unit, choice, effective))

    recommendation = clip_recommendation(pack, [(u, t) for u, _, t in tagged])
    clip_theme_id = clip_choice.theme_id or recommendation.theme_id
    clip_theme = pack.theme(clip_theme_id)
    clip_intensity = (
        clip_choice.intensity if clip_choice.intensity is not None else clip_theme.default_intensity
    )

    scenes: list[SceneGrade] = []
    for unit, choice, effective in tagged:
        theme = pack.theme(choice.theme_id) if choice.theme_id else clip_theme
        if choice.intensity is not None:
            intensity = choice.intensity
        elif clip_choice.intensity is not None:
            intensity = clip_choice.intensity
        else:
            intensity = theme.default_intensity
        measurements = await scene_measurements(data_dir, clip, unit)
        scenes.append(
            SceneGrade(
                unit=unit,
                choice=choice,
                effective_tags=effective,
                scores=_scores(pack, effective),
                theme=theme,
                intensity=intensity,
                measurements=measurements,
                correction=correct(measurements, effective, theme.adapt),
            )
        )
    return ClipGrade(
        clip=clip,
        pack=pack,
        recommendation=recommendation,
        choice=clip_choice,
        theme_id=clip_theme_id,
        intensity=clip_intensity,
        scenes=scenes,
    )


def with_theme(
    grade: ClipGrade, scene: SceneGrade, theme_id: str, intensity: int | None
) -> SceneGrade:
    """The same scene under another theme: what the selector's thumbnails show."""
    theme = grade.pack.theme(theme_id)
    if intensity is None:
        intensity = scene.intensity if theme.id == scene.theme.id else theme.default_intensity
    return replace(
        scene,
        theme=theme,
        intensity=intensity,
        correction=correct(scene.measurements, scene.effective_tags, theme.adapt),
    )


# --- taste events (P3) -------------------------------------------------------


def _tags(tags: SceneTags) -> dict[str, str | None]:
    return {
        "lighting_temperature": tags.lighting_temperature,
        "lighting_quality": tags.lighting_quality,
    }


def scene_features(grade: ClipGrade, scene: SceneGrade) -> dict[str, Any]:
    """Numbers and tags only: no path, no frame, no footage (P4)."""
    m = scene.measurements
    return {
        "analysed": _tags(scene.unit.analysed),
        "effective": _tags(scene.effective_tags),
        "correction": scene.correction.as_features(),
        "measured": None
        if m is None
        else {
            "median_luma": round(m.median_luma, 4),
            "cast_kelvin": None if m.cast_kelvin is None else round(m.cast_kelvin, 1),
            "noise_sigma": round(m.noise_sigma, 5),
        },
        "energy_score": scene.unit.energy_score,
        "duration_seconds": round(scene.unit.duration, 3),
        "theme_before": scene.theme.id,
        "intensity_before": scene.intensity,
        "clip_theme": grade.theme_id,
    }


def clip_features(grade: ClipGrade) -> dict[str, Any]:
    return {
        "scene_count": len(grade.scenes),
        "duration_seconds": round(sum(s.unit.duration for s in grade.scenes), 3),
        "theme_before": grade.theme_id,
        "intensity_before": grade.intensity,
        "scenes": [
            {
                "effective": _tags(s.effective_tags),
                "correction": s.correction.as_features(),
                "energy_score": s.unit.energy_score,
                "duration_seconds": round(s.unit.duration, 3),
            }
            for s in grade.scenes
        ],
    }


def _suggestion(grade: ClipGrade, scene: SceneGrade | None, field_name: str) -> dict[str, Any]:
    """What the person would get for this field if they had not touched it."""
    if field_name == "theme_id":
        return {"value": grade.recommendation.theme_id, "scores": grade.recommendation.scores}
    if field_name == "intensity":
        theme = scene.theme if scene is not None else grade.pack.theme(grade.theme_id)
        return {"value": theme.default_intensity}
    if scene is None:
        raise GradeChoiceError("a lighting override applies to one scene, not the whole clip")
    return {"value": getattr(scene.unit.analysed, field_name)}


def _event(
    grade: ClipGrade,
    scene: SceneGrade | None,
    kind: TasteEventKind,
    recommended: Mapping[str, Any],
    chosen: Mapping[str, Any],
) -> TasteEvent:
    return TasteEvent(
        schema_version=TASTE_SCHEMA_VERSION,
        kind=kind.value,
        scope=(ChoiceScope.SCENE if scene is not None else ChoiceScope.CLIP).value,
        project_id=grade.clip.project_id,
        sha256=grade.clip.sha256,
        scene_id=scene.unit.id if scene is not None else None,
        pack_id=grade.pack.manifest.id,
        recommended_json=json.dumps(recommended, sort_keys=True),
        chosen_json=json.dumps(chosen, sort_keys=True),
        features_json=json.dumps(
            scene_features(grade, scene) if scene is not None else clip_features(grade),
            sort_keys=True,
        ),
    )


async def _row(session: AsyncSession, grade: ClipGrade, scene_id: str | None) -> GradeChoice | None:
    statement = select(GradeChoice).where(
        GradeChoice.project_id == grade.clip.project_id,
        GradeChoice.sha256 == grade.clip.sha256,
        GradeChoice.scene_id.is_(None) if scene_id is None else GradeChoice.scene_id == scene_id,
    )
    return (await session.execute(statement)).scalars().first()


async def apply_choice(
    session: AsyncSession,
    grade: ClipGrade,
    *,
    scene_id: str | None,
    changes: Mapping[str, object],
) -> int:
    """Store the changed fields; one taste event per field that changed. Returns events written."""
    scene = grade.scene(scene_id) if scene_id is not None else None
    if scene is not None and scene.unit.id is None:
        raise GradeChoiceError("this clip has no scenes yet; choose for the whole clip")
    unknown = set(changes) - set(CHOICE_FIELDS)
    if unknown:
        raise GradeChoiceError(f"unknown grading fields: {sorted(unknown)}")
    if scene is None and any(f in changes for f in LIGHTING_FIELDS):
        raise GradeChoiceError("a lighting override applies to one scene, not the whole clip")
    theme_id = changes.get("theme_id")
    if theme_id is not None:
        grade.pack.theme(str(theme_id))  # raises ThemeNotFoundError by name

    row = await _row(session, grade, scene_id)
    current = Choice.of(row)
    written = 0
    for field_name in CHOICE_FIELDS:
        if field_name not in changes:
            continue
        value = changes[field_name]
        if value is None or getattr(current, field_name) == value:
            continue
        if row is None:
            row = GradeChoice(
                project_id=grade.clip.project_id,
                sha256=grade.clip.sha256,
                scene_id=scene_id,
                pack_id=grade.pack.manifest.id,
            )
            session.add(row)
        setattr(row, field_name, value)
        session.add(
            _event(
                grade,
                scene,
                _KIND_FOR_FIELD[field_name],
                _suggestion(grade, scene, field_name),
                {"value": value, "previous": getattr(current, field_name)},
            )
        )
        written += 1
    await session.commit()
    return written


async def reset(
    session: AsyncSession, grade: ClipGrade, *, scene_id: str | None, everything: bool
) -> int:
    """Back to the suggestion. Deletes choices (never writes the suggestion back); one event."""
    scene = grade.scene(scene_id) if scene_id is not None else None
    rows: list[GradeChoice] = []
    if scene_id is None and everything:
        statement = select(GradeChoice).where(
            GradeChoice.project_id == grade.clip.project_id,
            GradeChoice.sha256 == grade.clip.sha256,
        )
        rows = list((await session.execute(statement)).scalars().all())
    else:
        row = await _row(session, grade, scene_id)
        rows = [row] if row is not None else []
    cleared = [
        {"scene_id": r.scene_id, **Choice.of(r).set_fields()}
        for r in rows
        if not Choice.of(r).is_empty()
    ]
    for r in rows:
        await session.delete(r)
    if not cleared:
        await session.commit()
        return 0
    session.add(
        _event(
            grade,
            scene,
            TasteEventKind.RESET,
            {"theme_id": grade.recommendation.theme_id, "scores": grade.recommendation.scores},
            {"cleared": cleared},
        )
    )
    await session.commit()
    return 1


# --- renders -----------------------------------------------------------------


def still_path(data_dir: Path, grade: ClipGrade, scene: SceneGrade) -> Path:
    name = _digest({"still": 1, **scene.render_key(grade.pack.manifest.id)})
    return _cache_file(data_dir, "stills", grade.clip.sha256, f"{name}.jpg")


def preview_path(data_dir: Path, grade: ClipGrade, scene: SceneGrade) -> Path:
    name = _digest({"preview": 1, **scene.render_key(grade.pack.manifest.id)})
    return _cache_file(data_dir, "previews", grade.clip.sha256, f"{name}.mp4")


async def ensure_still(data_dir: Path, grade: ClipGrade, scene: SceneGrade) -> Path:
    path = still_path(data_dir, grade, scene)
    if path.is_file():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    middle = scene.unit.start_seconds + scene.unit.duration / 2
    return await render_still(
        grade.clip.proxy,
        path,
        timestamp_seconds=middle,
        correction=scene.correction,
        theme=scene.theme,
        pack_id=grade.pack.manifest.id,
        intensity=scene.intensity,
        luts_root=luts_root(data_dir),
    )


async def ensure_previews(
    data_dir: Path,
    grade: ClipGrade,
    *,
    on_scene: Callable[[int, int], Awaitable[None]] | None = None,
    on_progress: Callable[[float], None] | None = None,
) -> list[str]:
    """Render every scene whose current grade has no preview yet. Returns the keys rendered.

    A lighting override changes one scene's correction and so one scene's
    name: only that scene is rendered again.
    """
    missing = [s for s in grade.scenes if not preview_path(data_dir, grade, s).is_file()]
    rendered: list[str] = []
    for position, scene in enumerate(missing):
        if on_scene is not None:
            await on_scene(position, len(missing))
        path = preview_path(data_dir, grade, scene)
        path.parent.mkdir(parents=True, exist_ok=True)
        await render_preview(
            grade.clip.proxy,
            path,
            start_seconds=scene.unit.start_seconds,
            end_seconds=scene.unit.end_seconds,
            correction=scene.correction,
            theme=scene.theme,
            pack_id=grade.pack.manifest.id,
            intensity=scene.intensity,
            luts_root=luts_root(data_dir),
            on_progress=on_progress,
        )
        rendered.append(scene.unit.key)
    return rendered


__all__ = [
    "CLIP_KEY",
    "DEFAULT_PACK_ID",
    "Choice",
    "ClipGrade",
    "GradeChoiceError",
    "GradeSceneNotFoundError",
    "GradingNotReadyError",
    "SceneGrade",
    "apply_choice",
    "clip_features",
    "clip_grade",
    "clip_recommendation",
    "ensure_previews",
    "ensure_still",
    "luts_root",
    "preview_path",
    "reset",
    "scene_features",
    "still_path",
    "with_theme",
]
