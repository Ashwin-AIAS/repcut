"""A project's export: gather every decision into ``ExportInputs`` and render it.

Reads the edit (``edit.service``), the grade per scene (``grading.service``:
the correction measured on the proxy and stored, never re-measured at full
resolution - prompt-06-export-colour.md), the captions and the export settings.
Writes ``export_settings`` and one taste event per override (P3), and the
rendered file under the project's folder in ``$DATA_DIR``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from repcut.captions import ass
from repcut.captions import service as captions
from repcut.captions.timeline import visible
from repcut.db.models import (
    ChoiceScope,
    ExportSettings,
    MediaBlob,
    MediaFile,
    TasteEvent,
    TasteEventKind,
)
from repcut.edit import ducking
from repcut.edit import service as edit_service
from repcut.export.presets import Preset, config
from repcut.export.reframe import crop_for
from repcut.export.render import (
    ExportInputs,
    ExportResult,
    MusicInput,
    ProgressFn,
    ShotSource,
    render_export,
)
from repcut.grading.apply import grade_spec
from repcut.grading.lut import look_digest
from repcut.grading.packs import ensure_lut
from repcut.grading.service import ClipGrade, SceneGrade, clip_grade, luts_root, with_theme
from repcut.media import ffmpeg_builder as builder
from repcut.media.ffmpeg_builder import CropBox, ExportEncode
from repcut.media.metadata import parse_color_properties
from repcut.media.store import absolute, project_directory
from repcut.music import library

TASTE_SCHEMA_VERSION = 1
MAX_OFFSET = 1.0

CAPTIONS_OFF = "captions are switched off for this export"
CAPTION_MODEL_MISSING = "captions are left out: the speech-to-text model is not installed"
CLIPS_UNTRANSCRIBED = (
    "some clips have not been transcribed yet; their captions are not in this export"
)
TRACK_GONE = "the edit's track is no longer in your music folder; exported without music"


class ExportNotReadyError(LookupError):
    """Nothing to export yet. A fixed sentence."""


class ExportSettingsError(ValueError):
    """An override naming a preset or a scene that does not exist."""


@dataclass(frozen=True, slots=True)
class Settings:
    preset_id: str
    offsets: dict[str, float]

    @classmethod
    def of(cls, row: ExportSettings | None) -> Settings:
        if row is None:
            return cls(config().default, {})
        offsets = {str(k): float(v) for k, v in json.loads(row.offsets_json or "{}").items()}
        return cls(row.preset_id, offsets)


def scene_key(sha256: str, unit_key: str) -> str:
    return f"{sha256[:16]}:{unit_key}"


async def load_settings(session: AsyncSession, project_id: str) -> Settings:
    return Settings.of(await session.get(ExportSettings, project_id))


def export_dir(data_dir: Path, project_id: str) -> Path:
    return absolute(data_dir, project_directory(project_id)) / "export"


def output_path(data_dir: Path, project_id: str, preset: Preset) -> Path:
    return export_dir(data_dir, project_id) / f"repcut-{preset.id}.mp4"


def latest_export(data_dir: Path, project_id: str, preset: Preset) -> dict[str, Any] | None:
    path = output_path(data_dir, project_id, preset)
    manifest = path.with_suffix(".json")
    if not path.is_file() or not manifest.is_file():
        return None
    data: dict[str, Any] = json.loads(manifest.read_text(encoding="utf-8"))
    data["size_bytes"] = path.stat().st_size
    return data


@dataclass(frozen=True, slots=True)
class SceneRef:
    key: str
    sha256: str
    clip_name: str
    index: int
    start_seconds: float
    end_seconds: float
    in_edit: bool


async def scenes(session: AsyncSession, data_dir: Path, project_id: str) -> list[SceneRef]:
    edit = await edit_service.current(session, data_dir, project_id)
    used = {(s.sha256, round(s.source_in, 3)) for s in edit.plan.shots}
    out: list[SceneRef] = []
    for clip in await edit_service.project_clips(session, project_id):
        try:
            grade = await clip_grade(session, data_dir, clip)
        except LookupError:
            continue
        for scene in grade.scenes:
            unit = scene.unit
            in_edit = any(
                sha == clip.sha256 and unit.start_seconds <= t < unit.end_seconds for sha, t in used
            )
            out.append(
                SceneRef(
                    key=scene_key(clip.sha256, unit.key),
                    sha256=clip.sha256,
                    clip_name=clip.display_name,
                    index=unit.index,
                    start_seconds=unit.start_seconds,
                    end_seconds=unit.end_seconds,
                    in_edit=in_edit,
                )
            )
    return out


def _event(
    project_id: str,
    kind: TasteEventKind,
    recommended: object,
    chosen: object,
    features: dict[str, Any],
    sha256: str | None = None,
) -> TasteEvent:
    return TasteEvent(
        schema_version=TASTE_SCHEMA_VERSION,
        kind=kind.value,
        scope=ChoiceScope.CLIP.value if sha256 else ChoiceScope.EDIT.value,
        project_id=project_id,
        sha256=sha256,
        pack_id=None,
        recommended_json=json.dumps({"value": recommended}),
        chosen_json=json.dumps({"value": chosen}),
        features_json=json.dumps(features),
    )


async def set_preset(session: AsyncSession, project_id: str, preset_id: str) -> Settings:
    try:
        config().preset(preset_id)
    except KeyError as error:
        raise ExportSettingsError("no export preset with that id exists") from error
    row = await session.get(ExportSettings, project_id)
    before = Settings.of(row)
    if row is None:
        row = ExportSettings(project_id=project_id, preset_id=preset_id, offsets_json="{}")
        session.add(row)
    row.preset_id = preset_id
    session.add(
        _event(
            project_id,
            TasteEventKind.EXPORT_PRESET,
            config().default,
            preset_id,
            {"before": before.preset_id},
        )
    )
    await session.commit()
    return Settings.of(row)


async def set_offset(
    session: AsyncSession, data_dir: Path, project_id: str, key: str, offset: float
) -> Settings:
    known = {s.key: s for s in await scenes(session, data_dir, project_id)}
    if key not in known:
        raise ExportSettingsError("no scene with that id is in this project")
    offset = round(min(MAX_OFFSET, max(-MAX_OFFSET, offset)), 3)
    row = await session.get(ExportSettings, project_id)
    if row is None:
        row = ExportSettings(project_id=project_id, preset_id=config().default, offsets_json="{}")
        session.add(row)
    settings = Settings.of(row)
    offsets = dict(settings.offsets)
    if offset == 0.0:
        offsets.pop(key, None)
    else:
        offsets[key] = offset
    row.offsets_json = json.dumps(offsets, sort_keys=True)
    session.add(
        _event(
            project_id,
            TasteEventKind.REFRAME_OFFSET,
            0.0,
            {"scene": key, "offset": offset},
            {"preset": settings.preset_id, "scene_index": known[key].index},
            sha256=known[key].sha256,
        )
    )
    await session.commit()
    return Settings.of(row)


# --- inputs --------------------------------------------------------------------


def _scene_for(grade: ClipGrade, middle: float) -> SceneGrade:
    for scene in grade.scenes:
        if scene.unit.start_seconds <= middle < scene.unit.end_seconds:
            return scene
    return min(
        grade.scenes, key=lambda s: abs((s.unit.start_seconds + s.unit.end_seconds) / 2 - middle)
    )


async def _timeline_beats(session: AsyncSession, edit: edit_service.EditState) -> list[float]:
    plan = edit.plan
    if not plan.snapped or plan.track_sha256 is None:
        return []
    tracks = {t.sha256: t for _, t in await edit_service.library(session)}
    track = tracks.get(plan.track_sha256)
    if track is None:
        return []
    zero = plan.music_start_seconds
    return [t - zero for t in track.analysis.beats if 0.0 <= t - zero <= plan.duration_seconds]


async def gather(
    session: AsyncSession,
    data_dir: Path,
    project_id: str,
    *,
    preset: Preset | None = None,
    theme_id: str | None = None,
    edit: edit_service.EditState | None = None,
) -> tuple[ExportInputs, Preset]:
    """Every decision as render inputs. ``theme_id`` previews another theme (a review
    render) without writing a grading choice, so no taste event is invented; ``edit`` is
    that review's edit, planned at the previewed theme's pace."""
    edit = edit or await edit_service.current(session, data_dir, project_id)
    plan = edit.plan
    if not plan.shots:
        raise ExportNotReadyError("this edit has no shots to export yet")
    settings = await load_settings(session, project_id)
    cfg = config()
    preset = preset or cfg.preset(settings.preset_id)
    encode = ExportEncode(
        width=preset.width,
        height=preset.height,
        fps=cfg.fps,
        preset=cfg.encode.preset,
        crf=cfg.encode.crf,
        profile=cfg.encode.profile,
        level=cfg.encode.level,
        keyframe_seconds=cfg.encode.keyframe_seconds,
    )
    files: dict[str, MediaFile] = {
        c.sha256: c for c in await edit_service.project_clips(session, project_id)
    }
    grades: dict[str, ClipGrade] = {}
    blobs: dict[str, MediaBlob] = {}
    colours: dict[str, Any] = {}
    shots: list[ShotSource] = []
    for shot in plan.shots:
        sha = shot.sha256
        if sha not in grades:
            grades[sha] = await clip_grade(session, data_dir, files[sha])
            blob = await session.get(MediaBlob, sha)
            if blob is None:
                raise ExportNotReadyError("a clip in this edit is no longer in the library")
            blobs[sha] = blob
            probe = await builder.run(builder.build_probe(absolute(data_dir, blob.stored_path)))
            colours[sha] = parse_color_properties(json.loads(probe))
        grade, blob, colour = grades[sha], blobs[sha], colours[sha]
        scene = _scene_for(grade, (shot.source_in + shot.source_out) / 2)
        if theme_id is not None:
            scene = with_theme(grade, scene, theme_id, None)
        lut = (
            ensure_lut(luts_root(data_dir), grade.pack.manifest.id, scene.theme)
            if scene.intensity > 0
            else None
        )
        width = blob.display_width or 0
        height = blob.display_height or 0
        if width <= 0 or height <= 0:
            raise ExportNotReadyError("a clip's picture size is unknown; import it again")
        crop = crop_for(
            width, height, preset.aspect, settings.offsets.get(scene_key(sha, scene.unit.key), 0.0)
        )
        shots.append(
            ShotSource(
                sha256=sha,
                source=absolute(data_dir, blob.stored_path),
                source_in=shot.source_in,
                timeline_in=shot.timeline_in,
                timeline_out=shot.timeline_out,
                width=width,
                height=height,
                has_audio=blob.audio_codec is not None,
                crop=CropBox(crop.width, crop.height, crop.x, crop.y),
                grade=grade_spec(scene.correction, scene.theme, intensity=scene.intensity, lut=lut),
                lut_digest=look_digest(scene.theme.look) if lut is not None else None,
                color_primaries=colour.color_primaries,
                color_transfer=colour.color_transfer,
                color_space=colour.color_space,
                color_range=colour.color_range,
            )
        )

    warnings: list[str] = list(edit.warnings)
    caption_state = await captions.state(session, data_dir, project_id, edit)
    events: tuple[ass.Event, ...] = ()
    style = None
    if not caption_state.settings.enabled:
        warnings.append(CAPTIONS_OFF)
    elif not caption_state.model_installed:
        warnings.append(CAPTION_MODEL_MISSING)
    else:
        if caption_state.clips_untranscribed:
            warnings.append(CLIPS_UNTRANSCRIBED)
        style = caption_state.style
        beats = await _timeline_beats(session, edit)
        events = tuple(ass.events(visible(caption_state.lines), style, beats))

    music: MusicInput | None = None
    if plan.track_sha256 is not None:
        track = await library.find_file(data_dir, plan.track_sha256)
        if track is None:
            warnings.append(TRACK_GONE)
        else:
            music = MusicInput(
                source=track.path,
                sha256=plan.track_sha256,
                start_seconds=plan.music_start_seconds,
                gain_expression=ducking.gain_expression(list(edit.ramps)),
            )
    inputs = ExportInputs(
        shots=tuple(shots),
        duration_seconds=plan.duration_seconds,
        encode=encode,
        audio_bitrate=cfg.encode.audio_bitrate,
        music=music,
        captions=events,
        style=style,
        warnings=tuple(warnings),
    )
    return inputs, preset


async def export(
    session: AsyncSession,
    data_dir: Path,
    project_id: str,
    *,
    preset: Preset | None = None,
    on_progress: ProgressFn | None = None,
) -> ExportResult:
    inputs, chosen = await gather(session, data_dir, project_id, preset=preset)
    folder = export_dir(data_dir, project_id)
    return await render_export(
        inputs, folder / "work", output_path(data_dir, project_id, chosen), on_progress=on_progress
    )


__all__ = [
    "CAPTIONS_OFF",
    "CAPTION_MODEL_MISSING",
    "CLIPS_UNTRANSCRIBED",
    "ExportNotReadyError",
    "ExportSettingsError",
    "SceneRef",
    "Settings",
    "export",
    "export_dir",
    "gather",
    "latest_export",
    "load_settings",
    "output_path",
    "scene_key",
    "scenes",
    "set_offset",
    "set_preset",
]
