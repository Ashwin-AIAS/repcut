"""A project's captions: transcribe its clips, keep the person's edits, log taste.

Transcripts are a cache per clip (content-addressed, like the edit's signals);
``caption_settings`` is the record of what the person decided. Every change is
one taste event (P3), with what the engine had suggested beside what was chosen.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np
from sqlalchemy.ext.asyncio import AsyncSession

from repcut.captions import transcribe as stt
from repcut.captions.styles import DEFAULT_STYLE, CaptionStyle, style, styles
from repcut.captions.timeline import clean_text, timeline_lines
from repcut.captions.types import TRANSCRIPT_VERSION, CaptionEdits, CaptionLine, Transcript
from repcut.db.models import CaptionSettings, ChoiceScope, MediaFile, TasteEvent, TasteEventKind
from repcut.edit import service as edit_service
from repcut.edit.signals import speech_regions
from repcut.edit.vad import MODEL_STORED_PATH as VAD_MODEL
from repcut.grading.service import load_clip
from repcut.media.ffmpeg_builder import build_pcm_decode, run, temp_target
from repcut.media.store import absolute, derived_path
from repcut.profile import service as profile_service
from repcut.profile.types import StyleProfile

TRANSCRIPT_KIND = "transcript"
TRANSCRIPT_FILENAME = "transcript.json"
TASTE_SCHEMA_VERSION = 1


class CaptionOverrideError(ValueError):
    """An override naming something that does not exist. A fixed sentence."""


def transcript_path(data_dir: Path, sha256: str) -> Path:
    return absolute(
        data_dir, derived_path(sha256, TRANSCRIPT_KIND, TRANSCRIPT_VERSION, TRANSCRIPT_FILENAME)
    )


def read_transcript(data_dir: Path, sha256: str) -> Transcript | None:
    path = transcript_path(data_dir, sha256)
    if not path.is_file():
        return None
    transcript = Transcript.from_json(json.loads(path.read_text(encoding="utf-8")))
    return transcript if transcript.version == TRANSCRIPT_VERSION else None


async def transcribe_clip(
    session: AsyncSession, data_dir: Path, media_file: MediaFile
) -> Transcript:
    """Transcribe one clip's proxy audio, guard it, cache it. Idempotent per clip."""
    cached = read_transcript(data_dir, media_file.sha256)
    if cached is not None:
        return cached
    if not stt.model_installed(data_dir):
        raise stt.CaptionModelUnavailableError(stt.MODEL_MISSING)
    clip = await load_clip(session, data_dir, media_file)
    target = transcript_path(data_dir, clip.sha256)
    target.parent.mkdir(parents=True, exist_ok=True)
    pcm = temp_target(target.with_name("audio16k.f32"))
    try:
        await run(build_pcm_decode(clip.proxy, pcm, sample_rate=stt.SAMPLE_RATE))
        samples = await asyncio.to_thread(np.fromfile, pcm, dtype=np.float32)
    finally:
        pcm.unlink(missing_ok=True)
    speech = await asyncio.to_thread(speech_regions, samples, absolute(data_dir, VAD_MODEL))
    transcript = await stt.transcribe(data_dir, samples, sha256=clip.sha256, speech=speech)
    partial = temp_target(target)
    partial.write_text(json.dumps(transcript.to_json()), encoding="utf-8")
    partial.replace(target)
    return transcript


# --- settings ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Settings:
    style_id: str
    enabled: bool
    edits: CaptionEdits

    @classmethod
    def of(cls, row: CaptionSettings | None, default_style: str = DEFAULT_STYLE) -> Settings:
        if row is None:
            return cls(default_style, True, CaptionEdits())
        words = {str(k): str(v) for k, v in json.loads(row.edits_json or "{}").items()}
        hidden = frozenset(str(v) for v in json.loads(row.hidden_json or "[]"))
        return cls(row.style_id, row.enabled, CaptionEdits(words=words, hidden=hidden))


async def suggested_style(
    session: AsyncSession, data_dir: Path | None = None, profile: StyleProfile | None = None
) -> str:
    """The caption style a new edit starts with: the style profile's, else the default."""
    if profile is None:
        profile = await profile_service.current(session, data_dir)
    return profile.caption_default(DEFAULT_STYLE, [s.id for s in styles()])


async def load_settings(
    session: AsyncSession,
    project_id: str,
    data_dir: Path | None = None,
    profile: StyleProfile | None = None,
) -> Settings:
    row = await session.get(CaptionSettings, project_id)
    if row is not None:
        return Settings.of(row)
    return Settings.of(None, await suggested_style(session, data_dir, profile))


@dataclass(frozen=True, slots=True)
class CaptionState:
    settings: Settings
    style: CaptionStyle
    lines: tuple[CaptionLine, ...]
    model_installed: bool
    clips_untranscribed: tuple[str, ...]
    languages: tuple[str, ...]
    dropped: int


async def state(
    session: AsyncSession,
    data_dir: Path,
    project_id: str,
    edit: edit_service.EditState | None = None,
    profile: StyleProfile | None = None,
) -> CaptionState:
    edit = edit or await edit_service.current(session, data_dir, project_id)
    clips = await edit_service.project_clips(session, project_id)
    transcripts: dict[str, Transcript] = {}
    missing: list[str] = []
    for clip in clips:
        transcript = read_transcript(data_dir, clip.sha256)
        if transcript is None:
            missing.append(clip.id)
        else:
            transcripts[clip.sha256] = transcript
    settings = await load_settings(session, project_id, data_dir, profile)
    try:
        chosen = style(settings.style_id)
    except ValueError:
        chosen = style(DEFAULT_STYLE)
    used = {s.sha256 for s in edit.plan.shots}
    return CaptionState(
        settings=settings,
        style=chosen,
        lines=tuple(timeline_lines(edit.plan, transcripts, settings.edits)),
        model_installed=stt.model_installed(data_dir),
        clips_untranscribed=tuple(missing),
        languages=tuple(sorted({t.language for t in transcripts.values() if t.language})),
        dropped=sum(t.dropped for s, t in transcripts.items() if s in used),
    )


# --- overrides -----------------------------------------------------------------

OverrideKind = Literal["style", "enabled", "word", "line"]


@dataclass(frozen=True, slots=True)
class CaptionOverride:
    kind: OverrideKind
    style_id: str | None = None
    enabled: bool | None = None
    word_id: str | None = None
    text: str | None = None
    line_id: str | None = None
    hidden: bool | None = None


def _event(
    project_id: str,
    kind: TasteEventKind,
    recommended: object,
    chosen: object,
    features: dict[str, Any],
) -> TasteEvent:
    return TasteEvent(
        schema_version=TASTE_SCHEMA_VERSION,
        kind=kind.value,
        scope=ChoiceScope.EDIT.value,
        project_id=project_id,
        sha256=None,
        pack_id=None,
        recommended_json=json.dumps({"value": recommended}),
        chosen_json=json.dumps({"value": chosen}),
        features_json=json.dumps(features),
    )


async def apply_override(
    session: AsyncSession, data_dir: Path, project_id: str, override: CaptionOverride
) -> CaptionState:
    before = await state(session, data_dir, project_id)
    suggested = await suggested_style(session, data_dir)
    row = await session.get(CaptionSettings, project_id)
    if row is None:
        row = CaptionSettings(
            project_id=project_id,
            style_id=suggested,
            enabled=True,
            edits_json="{}",
            hidden_json="[]",
        )
        session.add(row)
    words = dict(before.settings.edits.words)
    hidden = set(before.settings.edits.hidden)
    features: dict[str, Any] = {
        "style": before.style.id,
        "lines": len(before.lines),
        "languages": list(before.languages),
    }
    if override.kind == "style":
        if override.style_id not in {s.id for s in styles()}:
            raise CaptionOverrideError("no caption style with that id exists")
        row.style_id = override.style_id
        event = _event(
            project_id, TasteEventKind.CAPTION_STYLE, suggested, override.style_id, features
        )
    elif override.kind == "enabled":
        row.enabled = bool(override.enabled)
        kind = TasteEventKind.CAPTIONS_ON if override.enabled else TasteEventKind.CAPTIONS_OFF
        event = _event(project_id, kind, True, bool(override.enabled), features)
    elif override.kind == "word":
        found = next(
            (w for line in before.lines for w in line.words if w.id == override.word_id), None
        )
        if found is None or override.text is None:
            raise CaptionOverrideError("no caption word with that id is in this edit")
        text = clean_text(override.text)
        if text == found.original:
            words.pop(found.id, None)
        else:
            words[found.id] = text
        features["probability"] = found.probability
        event = _event(project_id, TasteEventKind.CAPTION_WORD, found.original, text, features)
    else:
        if override.line_id is None or override.line_id not in {line.id for line in before.lines}:
            raise CaptionOverrideError("no caption line with that id is in this edit")
        if override.hidden:
            hidden.add(override.line_id)
            kind = TasteEventKind.CAPTION_LINE_HIDDEN
        else:
            hidden.discard(override.line_id)
            kind = TasteEventKind.CAPTION_LINE_SHOWN
        event = _event(project_id, kind, False, bool(override.hidden), features)
    row.edits_json = json.dumps(words, sort_keys=True)
    row.hidden_json = json.dumps(sorted(hidden))
    session.add(event)
    await session.commit()
    return await state(session, data_dir, project_id)


__all__ = [
    "TRANSCRIPT_KIND",
    "CaptionOverride",
    "CaptionOverrideError",
    "CaptionState",
    "Settings",
    "apply_override",
    "load_settings",
    "read_transcript",
    "state",
    "transcribe_clip",
    "transcript_path",
]
