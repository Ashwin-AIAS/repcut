"""A reference clip, end to end: measure it once, match a project to it, detach any part.

A reference is uploaded through the ordinary upload route and marked
``role = reference`` (``set_role``), so it gets exactly the probe, size
ceiling and refusals of any upload (`.claude/rules/security.md` §7: hostile
input), then the same ingest and analysis jobs as footage: proxy, scenes,
sampled frames, the Gemini scene cache. Nothing here re-implements any of
that; this module reads what that pipeline left and measures three things on
top of it, locally:

- **look** on measurement frames of the proxy (``look.py``),
- **rhythm** from the detected scene cuts, the reference's own beat and a
  frame-repeat estimate of slow motion (``rhythm.py``),
- **captions** on the scenes' sampled frames, plus at most
  ``MAX_GEMINI_FRAMES`` Gemini questions when a key is set (``captions.py``).

The result is cached per blob (``reference_profiles``). Matching a project
applies up to three parts - the look as a saved theme on every footage clip,
the pacing as the edit's planner numbers, the caption style - and records
what each replaced, so each can be detached alone and puts the person's own
choice back (P2). Every match and detach is one taste event (P3).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import httpx
import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repcut.analysis.cache import get_rate_limiter
from repcut.analysis.gemini_client import (
    GeminiAPIError,
    GeminiRateLimitedError,
    GeminiTransportError,
    ask_about_frame,
)
from repcut.analysis.params import SCENE_PARAMS_VERSION
from repcut.captions.styles import DEFAULT_STYLE
from repcut.config import Settings
from repcut.db.models import (
    CaptionSettings,
    ChoiceScope,
    GradeChoice,
    MediaFile,
    MediaRole,
    ReferenceMatch,
    ReferenceProfile,
    Scene,
    TasteEvent,
    TasteEventKind,
)
from repcut.edit.types import Pace
from repcut.grading import saved
from repcut.grading.apply import measure_timestamps, read_measure_frames
from repcut.grading.service import DEFAULT_PACK_ID, GradingNotReadyError, load_clip
from repcut.grading.theme import Look, PaceSpec, Recommend, Theme
from repcut.logging import get_logger
from repcut.media.ffmpeg_builder import build_pcm_decode, run, temp_target
from repcut.media.store import absolute
from repcut.music.analysis import ANALYSIS_SAMPLE_RATE, TrackAnalysisError
from repcut.music.analysis import analyse as analyse_audio
from repcut.reference import captions as cap
from repcut.reference import look as lk
from repcut.reference import rhythm as rh

logger = get_logger(__name__)

PROFILE_VERSIONS = (
    f"look{lk.LOOK_EXTRACTOR_VERSION}-rhythm{rh.RHYTHM_VERSION}-captions{cap.CAPTIONS_VERSION}"
)
TASTE_SCHEMA_VERSION = 1
# Measurement frames per reference, spread over its scenes (all local).
MAX_LOOK_FRAMES = 24
# Footage frames the project's own baseline is taken from.
MAX_BASELINE_FRAMES = 24
# Slow-motion estimate: this many seconds of consecutive frames per scene, at most this many scenes.
SLOWMO_WINDOW_SECONDS = 1.0
SLOWMO_MAX_SCENES = 12

Part = Literal["look", "pacing", "captions"]
PARTS: tuple[Part, ...] = ("look", "pacing", "captions")


class ReferenceError_(ValueError):
    """A reference request that cannot apply. A fixed sentence, safe to show."""


class ReferenceNotFoundError(LookupError):
    """No reference with that id in this project."""


@dataclass(frozen=True, slots=True)
class Analysis:
    look: lk.LookFit
    rhythm: rh.PacingProfile
    captions: cap.CaptionProfile

    @property
    def pace(self) -> PaceSpec:
        return rh.to_pace(self.rhythm)

    @property
    def caption_match(self) -> cap.CaptionMatch:
        return cap.closest_style(self.captions)


# --- roles ----------------------------------------------------------------------


async def set_role(session: AsyncSession, media_file: MediaFile, role: MediaRole) -> MediaFile:
    if media_file.role == role.value:
        return media_file
    if role is MediaRole.FOOTAGE:
        match = await session.get(ReferenceMatch, media_file.project_id)
        if match is not None and match.reference_media_file_id == media_file.id:
            raise ReferenceError_(
                "detach this reference from the project before using it as footage"
            )
    media_file.role = role.value
    await session.commit()
    return media_file


async def _reference(session: AsyncSession, project_id: str, media_file_id: str) -> MediaFile:
    media_file = await session.get(MediaFile, media_file_id)
    if (
        media_file is None
        or media_file.project_id != project_id
        or media_file.role != MediaRole.REFERENCE.value
    ):
        raise ReferenceNotFoundError("no reference with that id in this project")
    return media_file


async def footage(session: AsyncSession, project_id: str) -> list[MediaFile]:
    statement = (
        select(MediaFile)
        .where(MediaFile.project_id == project_id, MediaFile.role == MediaRole.FOOTAGE.value)
        .order_by(MediaFile.position, MediaFile.added_at)
    )
    return list((await session.execute(statement)).scalars())


# --- measuring ------------------------------------------------------------------


async def _scenes(session: AsyncSession, sha256: str) -> list[Scene]:
    statement = (
        select(Scene)
        .where(Scene.sha256 == sha256, Scene.detector_params_version == SCENE_PARAMS_VERSION)
        .order_by(Scene.sequence_index)
    )
    return list((await session.execute(statement)).scalars())


def _spread(timestamps: list[list[float]], limit: int) -> list[float]:
    """Round-robin across scenes so every scene contributes before any contributes twice."""
    out: list[float] = []
    depth = 0
    while len(out) < limit and any(depth < len(t) for t in timestamps):
        for scene_times in timestamps:
            if depth < len(scene_times) and len(out) < limit:
                out.append(scene_times[depth])
        depth += 1
    return sorted(out)


async def _look_frames(
    proxy: Path, spans: list[tuple[float, float]], limit: int
) -> list[np.ndarray]:
    times = _spread([measure_timestamps(a, b) for a, b in spans], limit)
    return await read_measure_frames(proxy, times)


async def footage_baseline(
    session: AsyncSession, data_dir: Path, project_id: str
) -> np.ndarray | None:
    """Pixels of the project's own footage, for a look fitted against what it will be applied to."""
    frames: list[np.ndarray] = []
    clips = await footage(session, project_id)
    per_clip = max(MAX_BASELINE_FRAMES // max(len(clips), 1), 3)
    for clip in clips:
        try:
            ref = await load_clip(session, data_dir, clip)
        except GradingNotReadyError:
            continue
        spans = [(s.start_seconds, s.end_seconds) for s in await _scenes(session, clip.sha256)]
        frames.extend(
            await _look_frames(ref.proxy, spans or [(0.0, ref.duration_seconds)], per_clip)
        )
        if len(frames) >= MAX_BASELINE_FRAMES:
            break
    return lk.pixels_of(frames) if frames else None


async def _beats(proxy: Path) -> tuple[list[float], float | None]:
    pcm = temp_target(proxy.with_name("reference-audio.f32"))
    try:
        await run(build_pcm_decode(proxy, pcm, sample_rate=ANALYSIS_SAMPLE_RATE))
        samples = await asyncio.to_thread(np.fromfile, pcm, dtype=np.float32)
    except Exception:
        logger.info("reference_audio_unreadable")
        return [], None
    finally:
        pcm.unlink(missing_ok=True)
    try:
        track = await asyncio.to_thread(analyse_audio, samples)
    except TrackAnalysisError:
        return [], None
    return [float(b) for b in track.beats], float(track.bpm)


def _repeat_diffs(proxy: Path, spans: list[tuple[float, float]]) -> list[float]:
    """Mean absolute differences between consecutive small grey frames, per scene window."""
    import cv2

    capture = cv2.VideoCapture(str(proxy))
    try:
        fps = capture.get(cv2.CAP_PROP_FPS) or 30.0
        diffs: list[float] = []
        for start, end in spans[:SLOWMO_MAX_SCENES]:
            middle = (start + end) / 2
            capture.set(
                cv2.CAP_PROP_POS_MSEC, max(middle - SLOWMO_WINDOW_SECONDS / 2, start) * 1000
            )
            previous = None
            for _ in range(int(fps * SLOWMO_WINDOW_SECONDS)):
                ok, frame = capture.read()
                if not ok:
                    break
                grey = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (64, 36)).astype(
                    np.float64
                )
                if previous is not None:
                    diffs.append(float(np.abs(grey - previous).mean()))
                previous = grey
        return diffs
    finally:
        capture.release()


async def _caption_answers(
    frames: Iterable[Path], settings: Settings, client: httpx.AsyncClient | None
) -> list[cap.GeminiCaptionAnswer]:
    if not settings.gemini_api_key_set:
        return []
    limiter = get_rate_limiter(settings)
    answers: list[cap.GeminiCaptionAnswer] = []
    own = client is None
    http = client or httpx.AsyncClient()
    try:
        for path in list(frames)[: cap.MAX_GEMINI_FRAMES]:
            try:
                answer = await ask_about_frame(
                    path,
                    prompt_text=cap.CAPTION_PROMPT,
                    response_schema=cap.CAPTION_RESPONSE_SCHEMA,
                    result_type=cap.GeminiCaptionAnswer,
                    settings=settings,
                    client=http,
                    acquire_token=limiter.try_acquire,
                )
            except (GeminiRateLimitedError, GeminiTransportError, GeminiAPIError) as error:
                logger.info("reference_captions_gemini_stopped", error_type=type(error).__name__)
                break
            if answer is not None:
                answers.append(answer)
    finally:
        if own:
            await http.aclose()
    return answers


async def _measure(
    session: AsyncSession,
    settings: Settings,
    media_file: MediaFile,
    client: httpx.AsyncClient | None,
) -> Analysis:
    data_dir = settings.data_dir
    clip = await load_clip(session, data_dir, media_file)
    scenes = await _scenes(session, media_file.sha256)
    duration = clip.duration_seconds or (scenes[-1].end_seconds if scenes else 0.0)
    if duration <= 0:
        raise GradingNotReadyError("this reference has not been measured yet")
    spans = [(s.start_seconds, s.end_seconds) for s in scenes] or [(0.0, duration)]

    frames = await _look_frames(clip.proxy, spans, MAX_LOOK_FRAMES)
    if not frames:
        raise GradingNotReadyError("this reference's frames could not be read")
    fit = await asyncio.to_thread(lk.extract_look, lk.pixels_of(frames), lk.neutral_baseline())

    beats, bpm = await _beats(clip.proxy)
    diffs = await asyncio.to_thread(_repeat_diffs, clip.proxy, spans)
    rhythm = rh.profile(
        [s.start_seconds for s in scenes[1:]],
        duration,
        beats=beats,
        bpm=bpm,
        slowmo_share=rh.duplicate_share(diffs) if diffs else None,
    )

    sampled: list[Path] = []
    for scene in scenes:
        if scene.sampled_frame_path:
            try:
                path = absolute(data_dir, scene.sampled_frame_path)
            except ValueError:
                continue
            if path.is_file():
                sampled.append(path)
    caption_frames = [_read_jpeg(p) for p in sampled]
    answers = await _caption_answers(sampled, settings, client)
    captions = cap.caption_profile([f for f in caption_frames if f is not None], answers)
    return Analysis(look=fit, rhythm=rhythm, captions=captions)


def _read_jpeg(path: Path) -> np.ndarray | None:
    import cv2

    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        return None
    return np.asarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))


def _from_row(row: ReferenceProfile) -> Analysis:
    look = json.loads(row.look_json)
    return Analysis(
        look=lk.LookFit(
            look=Look.model_validate(look["look"]),
            capped=tuple(look["capped"]),
            tone_residual=float(look["tone_residual"]),
            colour_residual=float(look["colour_residual"]),
        ),
        rhythm=rh.PacingProfile.from_json(json.loads(row.rhythm_json)),
        captions=cap.CaptionProfile.from_json(json.loads(row.captions_json)),
    )


async def analyse(
    session: AsyncSession,
    settings: Settings,
    media_file: MediaFile,
    *,
    client: httpx.AsyncClient | None = None,
    refresh: bool = False,
) -> Analysis:
    """The reference's profile: from the cache when current, else measured and cached."""
    row = await session.get(ReferenceProfile, media_file.sha256)
    if row is not None and row.versions == PROFILE_VERSIONS and not refresh:
        return _from_row(row)
    result = await _measure(session, settings, media_file, client)
    if row is None:
        row = ReferenceProfile(
            sha256=media_file.sha256,
            versions=PROFILE_VERSIONS,
            look_json="",
            rhythm_json="",
            captions_json="",
        )
        session.add(row)
    row.versions = PROFILE_VERSIONS
    row.look_json = json.dumps(result.look.as_json())
    row.rhythm_json = json.dumps(result.rhythm.as_json())
    row.captions_json = json.dumps(result.captions.as_json())
    await session.commit()
    return result


async def cached(session: AsyncSession, sha256: str) -> Analysis | None:
    row = await session.get(ReferenceProfile, sha256)
    if row is None or row.versions != PROFILE_VERSIONS:
        return None
    return _from_row(row)


# --- themes ---------------------------------------------------------------------


def theme_for(name: str, fit: lk.LookFit, pace: PaceSpec, taken: set[str]) -> Theme:
    return Theme(
        id=saved.slug_for(name, taken),
        name=name[:40] or "Matched look",
        description=(
            "Saved from a reference clip: its colour statistics as a look, inside "
            "Repcut's caps" + (" (some held at a cap)" if fit.capped else "") + "."
        )[:280],
        default_intensity=80,
        look=fit.look,
        recommend=Recommend(base=0.0),
        pace=pace,
    )


def default_name(media_file: MediaFile) -> str:
    stem = Path(media_file.display_name).stem.strip() or "reference"
    return f"Look of {stem}"[:40]


async def save_look(
    session: AsyncSession,
    settings: Settings,
    project_id: str,
    media_file_id: str,
    *,
    name: str | None = None,
) -> Theme:
    """Save the reference's look as a theme, fitted against this project's footage if any."""
    media_file = await _reference(session, project_id, media_file_id)
    result = await analyse(session, settings, media_file)
    fit = result.look
    baseline = await footage_baseline(session, settings.data_dir, project_id)
    if baseline is not None:
        frames = await _look_frames(
            (await load_clip(session, settings.data_dir, media_file)).proxy,
            [(s.start_seconds, s.end_seconds) for s in await _scenes(session, media_file.sha256)]
            or [(0.0, (await load_clip(session, settings.data_dir, media_file)).duration_seconds)],
            MAX_LOOK_FRAMES,
        )
        if frames:
            fit = await asyncio.to_thread(lk.extract_look, lk.pixels_of(frames), baseline)
    taken = {t.id for t in saved.list_saved(settings.data_dir)}
    theme = theme_for((name or default_name(media_file)).strip(), fit, result.pace, taken)
    return saved.save(settings.data_dir, theme)


# --- matching -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MatchState:
    reference_media_file_id: str
    theme_id: str | None
    look_on: bool
    pacing_on: bool
    captions_on: bool
    pace: PaceSpec | None
    caption_style_id: str | None
    capped: tuple[str, ...]
    caption_differences: tuple[str, ...]
    rhythm: rh.PacingProfile | None


def _event(
    project_id: str,
    sha256: str,
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
        sha256=sha256,
        pack_id=DEFAULT_PACK_ID,
        recommended_json=json.dumps({"value": recommended}),
        chosen_json=json.dumps({"value": chosen}),
        features_json=json.dumps(features),
    )


async def _clip_choice(session: AsyncSession, project_id: str, sha256: str) -> GradeChoice | None:
    statement = select(GradeChoice).where(
        GradeChoice.project_id == project_id,
        GradeChoice.sha256 == sha256,
        GradeChoice.scene_id.is_(None),
    )
    return (await session.execute(statement)).scalars().first()


async def _set_clip_theme(
    session: AsyncSession, project_id: str, sha256: str, theme_id: str | None
) -> None:
    row = await _clip_choice(session, project_id, sha256)
    if row is None:
        if theme_id is None:
            return
        row = GradeChoice(
            project_id=project_id, sha256=sha256, scene_id=None, pack_id=DEFAULT_PACK_ID
        )
        session.add(row)
    row.theme_id = theme_id


async def state(session: AsyncSession, project_id: str) -> MatchState | None:
    row = await session.get(ReferenceMatch, project_id)
    if row is None:
        return None
    media_file = await session.get(MediaFile, row.reference_media_file_id)
    result = await cached(session, media_file.sha256) if media_file is not None else None
    pace_data = json.loads(row.pace_json or "{}")
    return MatchState(
        reference_media_file_id=row.reference_media_file_id,
        theme_id=row.theme_id,
        look_on=row.look_on,
        pacing_on=row.pacing_on,
        captions_on=row.captions_on,
        pace=PaceSpec.model_validate(pace_data) if pace_data else None,
        caption_style_id=row.caption_style_id,
        capped=result.look.capped if result else (),
        caption_differences=result.caption_match.differences if result else (),
        rhythm=result.rhythm if result else None,
    )


async def match(
    session: AsyncSession,
    settings: Settings,
    project_id: str,
    media_file_id: str,
    parts: Iterable[Part] = PARTS,
) -> MatchState:
    """Apply the reference's parts to the project. Re-matching the same parts is a no-op."""
    wanted = set(parts) & set(PARTS)
    if not wanted:
        raise ReferenceError_("choose at least one part of the reference to match")
    media_file = await _reference(session, project_id, media_file_id)
    result = await analyse(session, settings, media_file)
    row = await session.get(ReferenceMatch, project_id)
    if row is not None and row.reference_media_file_id != media_file.id:
        # Another reference was matched: take its parts off first, restoring the person's choices.
        for part in PARTS:
            await _detach(session, row, part, reason="replaced by another reference")
        await session.delete(row)
        await session.flush()
        row = None
    if row is None:
        row = ReferenceMatch(
            project_id=project_id,
            reference_media_file_id=media_file.id,
            previous_json="{}",
            pace_json="{}",
        )
        session.add(row)
    previous: dict[str, Any] = json.loads(row.previous_json or "{}")
    features = {
        "reference_sha256": media_file.sha256,
        "rhythm": result.rhythm.as_json(),
        "captions": result.captions.as_json(),
        "look_capped": list(result.look.capped),
    }

    if "look" in wanted and not row.look_on:
        theme_id = row.theme_id
        known = {t.id for t in saved.list_saved(settings.data_dir)}
        if theme_id is None or theme_id not in known:
            theme_id = (await save_look(session, settings, project_id, media_file.id)).id
        clips = await footage(session, project_id)
        before: dict[str, str | None] = {}
        for clip in clips:
            choice = await _clip_choice(session, project_id, clip.sha256)
            before[clip.sha256] = choice.theme_id if choice else None
            await _set_clip_theme(session, project_id, clip.sha256, theme_id)
        previous["look"] = before
        row.theme_id = theme_id
        row.look_on = True
        session.add(
            _event(
                project_id,
                media_file.sha256,
                TasteEventKind.REFERENCE_MATCHED,
                "look",
                theme_id,
                {**features, "part": "look", "replaced": before},
            )
        )

    if "pacing" in wanted and not row.pacing_on:
        row.pace_json = json.dumps(result.pace.model_dump(mode="json"))
        row.pacing_on = True
        session.add(
            _event(
                project_id,
                media_file.sha256,
                TasteEventKind.REFERENCE_MATCHED,
                "pacing",
                result.pace.model_dump(mode="json"),
                {**features, "part": "pacing"},
            )
        )

    if "captions" in wanted and not row.captions_on:
        chosen = result.caption_match
        settings_row = await session.get(CaptionSettings, project_id)
        previous["captions"] = (
            {"style_id": settings_row.style_id, "enabled": settings_row.enabled}
            if settings_row is not None
            else None
        )
        if settings_row is None:
            settings_row = CaptionSettings(
                project_id=project_id,
                style_id=DEFAULT_STYLE,
                enabled=True,
                edits_json="{}",
                hidden_json="[]",
            )
            session.add(settings_row)
        settings_row.style_id = chosen.style_id
        settings_row.enabled = chosen.enabled
        row.caption_style_id = chosen.style_id
        row.captions_on = True
        session.add(
            _event(
                project_id,
                media_file.sha256,
                TasteEventKind.REFERENCE_MATCHED,
                "captions",
                chosen.as_json(),
                {**features, "part": "captions"},
            )
        )

    row.previous_json = json.dumps(previous, sort_keys=True)
    await session.commit()
    out = await state(session, project_id)
    if out is None:
        raise ReferenceError_("the match could not be read back")
    return out


async def _detach(session: AsyncSession, row: ReferenceMatch, part: Part, *, reason: str) -> bool:
    previous: dict[str, Any] = json.loads(row.previous_json or "{}")
    media_file = await session.get(MediaFile, row.reference_media_file_id)
    sha = media_file.sha256 if media_file is not None else ""
    restored: object = None
    if part == "look" and row.look_on:
        before: dict[str, str | None] = previous.pop("look", {}) or {}
        for sha256, theme_id in before.items():
            choice = await _clip_choice(session, row.project_id, sha256)
            # Only put back what the match changed: a theme the person picked since stays.
            if choice is not None and choice.theme_id == row.theme_id:
                choice.theme_id = theme_id
        row.look_on = False
        restored = before
    elif part == "pacing" and row.pacing_on:
        row.pacing_on = False
        row.pace_json = "{}"
    elif part == "captions" and row.captions_on:
        before_captions = previous.pop("captions", None)
        settings_row = await session.get(CaptionSettings, row.project_id)
        if settings_row is not None and settings_row.style_id == row.caption_style_id:
            if before_captions is None:
                settings_row.style_id = DEFAULT_STYLE
                settings_row.enabled = True
            else:
                settings_row.style_id = before_captions["style_id"]
                settings_row.enabled = bool(before_captions["enabled"])
        row.captions_on = False
        restored = before_captions
    else:
        return False
    row.previous_json = json.dumps(previous, sort_keys=True)
    session.add(
        _event(
            row.project_id,
            sha,
            TasteEventKind.REFERENCE_DETACHED,
            part,
            restored,
            {"part": part, "reason": reason},
        )
    )
    return True


async def detach(session: AsyncSession, project_id: str, part: Part) -> MatchState | None:
    """Take one part of the matched reference off; the others stay."""
    if part not in PARTS:
        raise ReferenceError_("that is not a part of a reference")
    row = await session.get(ReferenceMatch, project_id)
    if row is None:
        return None
    await _detach(session, row, part, reason="detached by the person")
    if not (row.look_on or row.pacing_on or row.captions_on):
        await session.delete(row)
        await session.commit()
        return None
    await session.commit()
    return await state(session, project_id)


async def matched_pace(
    session: AsyncSession, project_id: str
) -> tuple[Pace, rh.PacingProfile | None] | None:
    """The planner numbers a matched reference's pacing sets, if that part is on."""
    row = await session.get(ReferenceMatch, project_id)
    if row is None or not row.pacing_on:
        return None
    spec = PaceSpec.model_validate(json.loads(row.pace_json))
    media_file = await session.get(MediaFile, row.reference_media_file_id)
    result = await cached(session, media_file.sha256) if media_file is not None else None
    pace = Pace(
        energy=spec.energy,
        calm_beats=spec.calm_beats,
        peak_beats=spec.peak_beats,
        slowmo_every_seconds=spec.slowmo_every_seconds,
    )
    return pace, (result.rhythm if result else None)


__all__ = [
    "PARTS",
    "PROFILE_VERSIONS",
    "Analysis",
    "MatchState",
    "ReferenceError_",
    "ReferenceNotFoundError",
    "analyse",
    "cached",
    "detach",
    "footage",
    "footage_baseline",
    "match",
    "matched_pace",
    "save_look",
    "set_role",
    "state",
]
