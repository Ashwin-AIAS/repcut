"""The master render: shots from the full-resolution sources to one postable MP4.

Pure orchestration over values the export service gathered (``ExportInputs``):
it never opens the database. Three properties the guide asks for, all
structural:

- **Resumable at segment granularity.** Each shot renders to a segment file
  named by a digest of everything that decides its bytes (source, frames, crop,
  grade, captions, encode). ``ffmpeg_builder.render`` writes a temp file and
  renames it only on success, so a segment file that exists is a finished one.
  A crash at 90 % re-runs and renders only what is missing; changing one
  caption re-renders only the shots it appears in.
- **Frame-exact joins.** Shot edges are quantised to the export's frame grid
  once, so segment N ends on exactly the frame segment N+1 starts on; their
  audio is cut to the same sample count. Without that, rounding drifts the
  sound against the picture by up to a frame per cut.
- **Timed.** Every stage's milliseconds are recorded in the manifest and logged
  (render budget: 60 s of 1080p in under 5 minutes on the ROG).

Slow motion (Prompt 07): a half-speed shot is rendered from interpolated
frames (``slowmo``) when its confidence check passes, and from its normal-speed
fallback window when it does not - or when interpolation cannot run here at
all. Either way the segment has the same length, so the edit, the music and the
captions around it never move. The verdict, the reason and what the
interpolation cost are recorded beside the segment and in the manifest.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import asdict, dataclass, field
from pathlib import Path

import structlog

from repcut.captions import ass
from repcut.captions.styles import CaptionStyle
from repcut.export.check import Expected, platform_problems
from repcut.media import ffmpeg_builder as builder
from repcut.media.artifacts import PROXY_RECIPE
from repcut.media.ffmpeg_builder import CropBox, ExportEncode, GradeSpec
from repcut.slowmo import confidence
from repcut.slowmo.rife import Interpolator
from repcut.slowmo.runtime import InterpolationSetup, SlowmoUnavailableError, open_interpolator
from repcut.slowmo.shot import FrameShape, interpolate_shot, output_frames_needed, read_frames
from repcut.slowmo.weights import RIFE_SHA256

logger = structlog.get_logger(__name__)

RENDER_VERSION = 1

# What decides a slow-motion segment's bytes besides the shot itself.
_SLOWMO_KEY = {
    "weights": RIFE_SHA256,
    "confidence": confidence.CONFIDENCE_VERSION,
    "threshold": confidence.THRESHOLD,
}


@dataclass(frozen=True, slots=True)
class ShotSource:
    """One shot, resolved to its source file and look."""

    sha256: str
    source: Path
    source_in: float
    timeline_in: float
    timeline_out: float
    width: int
    height: int
    has_audio: bool
    crop: CropBox
    grade: GradeSpec
    lut_digest: str | None
    color_primaries: str | None = None
    color_transfer: str | None = None
    color_space: str | None = None
    color_range: str | None = None
    speed: float = 1.0
    fallback_in: float | None = None

    @property
    def slow(self) -> bool:
        return self.speed != 1.0


@dataclass(frozen=True, slots=True)
class MusicInput:
    source: Path
    sha256: str
    start_seconds: float
    gain_expression: str


@dataclass(frozen=True, slots=True)
class ExportInputs:
    shots: tuple[ShotSource, ...]
    duration_seconds: float
    encode: ExportEncode
    audio_bitrate: str
    music: MusicInput | None
    captions: tuple[ass.Event, ...]
    style: CaptionStyle | None
    warnings: tuple[str, ...] = ()
    interpolation: InterpolationSetup | None = None


@dataclass(frozen=True, slots=True)
class Segment:
    index: int
    shot: ShotSource
    frame_in: int
    frame_out: int
    start_seconds: float
    subtitles: str | None
    key: str
    # Where the normal-speed fallback of a slow shot starts; None for a normal shot.
    fallback_seconds: float | None = None

    @property
    def frames(self) -> int:
        return self.frame_out - self.frame_in

    @property
    def slow(self) -> bool:
        return self.fallback_seconds is not None


@dataclass(slots=True)
class ExportResult:
    output: Path
    segments_total: int
    segments_rendered: int
    segments_reused: int
    timings_ms: dict[str, int] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    slowmo: list[dict[str, object]] = field(default_factory=list)

    def to_json(self) -> dict[str, object]:
        data = asdict(self)
        data["output"] = self.output.name
        return data


def frame_grid(shots: Sequence[ShotSource], fps: int, duration: float) -> list[tuple[int, int]]:
    """Contiguous frame ranges, one per shot; the last ends at the edit's length."""
    starts = [round(s.timeline_in * fps) for s in shots]
    ends = [*starts[1:], round(duration * fps)]
    return [(a, max(b, a + 1)) for a, b in zip(starts, ends, strict=True)]


def _digest(payload: object) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[
        :32
    ]


def plan_segments(inputs: ExportInputs) -> list[Segment]:
    fps = inputs.encode.fps
    out: list[Segment] = []
    grid = frame_grid(inputs.shots, fps, inputs.duration_seconds)
    for index, (shot, (frame_in, frame_out)) in enumerate(zip(inputs.shots, grid, strict=True)):
        local_start = frame_in / fps
        local_end = frame_out / fps
        # The source moves with the quantised edge, so picture and timeline
        # agree; in a slow shot one timeline second is half a source second.
        moved = local_start - shot.timeline_in
        start = max(0.0, shot.source_in + moved * shot.speed)
        fallback = (
            max(0.0, shot.fallback_in + moved)
            if shot.slow and shot.fallback_in is not None
            else None
        )
        subtitles: str | None = None
        if inputs.style is not None:
            window = ass.events_between(inputs.captions, local_start, local_end)
            if window:
                subtitles = ass.document(
                    window,
                    inputs.style,
                    width=inputs.encode.width,
                    height=inputs.encode.height,
                    offset=local_start,
                    duration=local_end - local_start,
                )
        key = _digest(
            {
                "v": RENDER_VERSION,
                "recipe": asdict(PROXY_RECIPE.normalisation),
                "sha": shot.sha256,
                "start": round(start, 4),
                "frames": frame_out - frame_in,
                "crop": asdict(shot.crop),
                "grade": {**asdict(shot.grade), "lut": shot.lut_digest},
                "encode": asdict(inputs.encode),
                "subtitles": subtitles,
                "audio": shot.has_audio,
                **(
                    {"speed": shot.speed, "fallback": round(fallback, 4), "slowmo": _SLOWMO_KEY}
                    if fallback is not None
                    else {}
                ),
            }
        )
        out.append(Segment(index, shot, frame_in, frame_out, start, subtitles, key, fallback))
    return out


InterpolatorFactory = Callable[[], AbstractContextManager[Interpolator]]


class _Interpolation:
    """The interpolator for one export: opened at the first slow shot, closed before the mux.

    One model resident at a time (gpu-vram.md): it is released before the
    music stem and the mux run, and never opened at all for an edit without
    slow shots.
    """

    def __init__(
        self, setup: InterpolationSetup | None, factory: InterpolatorFactory | None
    ) -> None:
        self._setup = setup
        self._factory = factory
        self._context: AbstractContextManager[Interpolator] | None = None
        self._active: Interpolator | None = None
        self.unavailable: str | None = None

    def get(self) -> Interpolator | None:
        if self._active is not None or self.unavailable is not None:
            return self._active
        try:
            if self._factory is not None:
                context: AbstractContextManager[Interpolator] = self._factory()
            elif self._setup is not None:
                context = open_interpolator(self._setup)
            else:
                raise SlowmoUnavailableError("slow motion is not set up for this export")
            self._active = context.__enter__()
            self._context = context
        except SlowmoUnavailableError as error:
            # Named: no torch, no weights, or no setup. The shot plays at normal speed.
            self.unavailable = str(error)
        return self._active

    def stats(self) -> dict[str, object] | None:
        stats = getattr(self._active, "stats", None)
        to_json = getattr(stats, "to_json", None)
        return to_json() if callable(to_json) else None

    def close(self) -> None:
        if self._context is not None:
            self._context.__exit__(None, None, None)
        self._context = None
        self._active = None


def _decision_path(segments_dir: Path, segment: Segment) -> Path:
    return segments_dir / f"{segment.key}.slowmo.json"


async def _render_slow(
    segment: Segment,
    inputs: ExportInputs,
    segments_dir: Path,
    target: Path,
    subtitle_file: Path | None,
    interpolation: _Interpolation,
    first_new: bool,
) -> dict[str, object]:
    """Render one slow segment, from interpolated frames or its fallback. Returns the decision."""
    shot = segment.shot
    encode = inputs.encode
    started = time.perf_counter()
    decision: dict[str, object] = {"segment": segment.index, "shot_timeline_in": shot.timeline_in}
    interpolator = interpolation.get()
    verdict: confidence.Verdict | None = None
    if interpolator is None:
        decision.update(accepted=False, reason=interpolation.unavailable)
    else:
        captured = segments_dir / f"{segment.key}.captured.rgb"
        produced = segments_dir / f"{segment.key}.slow.rgb"
        try:
            await builder.render(
                builder.build_export_frames(
                    shot.source,
                    captured,
                    start_seconds=segment.start_seconds,
                    frames=output_frames_needed(segment.frames),
                    crop=shot.crop,
                    encode=encode,
                    color_primaries=shot.color_primaries,
                    color_transfer=shot.color_transfer,
                    color_space=shot.color_space,
                    color_range=shot.color_range,
                ),
                dry_run_first=False,
            )
            frames = read_frames(captured, FrameShape(encode.width, encode.height))
            verdict = await asyncio.to_thread(
                interpolate_shot, frames, interpolator, produced, frames_out=segment.frames
            )
            del frames
            decision.update(verdict.to_json())
            if verdict.accepted:
                await builder.render(
                    builder.build_export_segment_from_frames(
                        produced,
                        target,
                        frames=segment.frames,
                        encode=encode,
                        grade=shot.grade,
                        subtitles=subtitle_file,
                    ),
                    dry_run_first=first_new,
                )
        finally:
            captured.unlink(missing_ok=True)
            produced.unlink(missing_ok=True)
    if verdict is None or not verdict.accepted:
        assert segment.fallback_seconds is not None  # noqa: S101 - only slow segments come here
        await builder.render(
            builder.build_export_segment(
                shot.source,
                target,
                start_seconds=segment.fallback_seconds,
                frames=segment.frames,
                crop=shot.crop,
                encode=encode,
                grade=shot.grade,
                has_audio=shot.has_audio,
                subtitles=subtitle_file,
                color_primaries=shot.color_primaries,
                color_transfer=shot.color_transfer,
                color_space=shot.color_space,
                color_range=shot.color_range,
            ),
            dry_run_first=first_new,
        )
        decision["played"] = "normal speed"
    else:
        decision["played"] = "slow motion"
    decision["interpolation"] = interpolation.stats()
    decision["seconds"] = round(time.perf_counter() - started, 3)
    logger.info(
        "slowmo_segment",
        segment=segment.index,
        accepted=decision.get("accepted"),
        confidence=decision.get("confidence"),
        reason=decision.get("reason"),
    )
    _decision_path(segments_dir, segment).write_text(json.dumps(decision), encoding="utf-8")
    return decision


ProgressFn = Callable[[str, float], None]


async def render_export(
    inputs: ExportInputs,
    work_dir: Path,
    output: Path,
    *,
    on_progress: ProgressFn | None = None,
    interpolator_factory: InterpolatorFactory | None = None,
) -> ExportResult:
    """Render (or resume) the export into ``output``. Raises ``FFmpegError`` on failure.

    ``interpolator_factory`` replaces the real interpolator (tests); by default
    it is opened from ``inputs.interpolation`` at the first slow shot.
    """
    timings: dict[str, int] = {}
    report = on_progress or (lambda _step, _fraction: None)
    clock = time.perf_counter()

    segments_dir = work_dir / "segments"
    segments_dir.mkdir(parents=True, exist_ok=True)
    segments = plan_segments(inputs)
    timings["plan"] = round((time.perf_counter() - clock) * 1000)

    total_frames = sum(s.frames for s in segments) or 1
    done_frames = 0
    rendered = reused = 0
    first_new = True
    slowmo: list[dict[str, object]] = []
    interpolation = _Interpolation(inputs.interpolation, interpolator_factory)
    clock = time.perf_counter()
    try:
        for segment in segments:
            first_new, done = await _render_one(
                segment, inputs, segments_dir, first_new, interpolation, slowmo
            )
            if done:
                rendered += 1
            else:
                reused += 1
            done_frames += segment.frames
            report("rendering shots", 0.9 * done_frames / total_frames)
    finally:
        interpolation.close()
    timings["segments"] = round((time.perf_counter() - clock) * 1000)
    return await _finish(
        inputs, work_dir, output, segments, rendered, reused, slowmo, timings, report
    )


async def _render_one(
    segment: Segment,
    inputs: ExportInputs,
    segments_dir: Path,
    first_new: bool,
    interpolation: _Interpolation,
    slowmo: list[dict[str, object]],
) -> tuple[bool, bool]:
    """Render one segment unless it exists. Returns (first_new after it, rendered?)."""
    target = segments_dir / f"{segment.key}.mkv"
    if target.is_file() and target.stat().st_size > 0:
        if segment.slow:
            recorded = _decision_path(segments_dir, segment)
            if recorded.is_file():
                slowmo.append({**json.loads(recorded.read_text(encoding="utf-8")), "reused": True})
        return first_new, False
    subtitle_file: Path | None = None
    if segment.subtitles is not None:
        subtitle_file = segments_dir / f"{segment.key}.ass"
        subtitle_file.write_text(segment.subtitles, encoding="utf-8")
    try:
        if segment.slow:
            slowmo.append(
                await _render_slow(
                    segment, inputs, segments_dir, target, subtitle_file, interpolation, first_new
                )
            )
        else:
            shot = segment.shot
            await builder.render(
                builder.build_export_segment(
                    shot.source,
                    target,
                    start_seconds=segment.start_seconds,
                    frames=segment.frames,
                    crop=shot.crop,
                    encode=inputs.encode,
                    grade=shot.grade,
                    has_audio=shot.has_audio,
                    subtitles=subtitle_file,
                    color_primaries=shot.color_primaries,
                    color_transfer=shot.color_transfer,
                    color_space=shot.color_space,
                    color_range=shot.color_range,
                ),
                # The plan is dry-run once per export, on the first shot it
                # renders (ffmpeg.md: fail fast on a bad graph, not after the
                # whole timeline).
                dry_run_first=first_new,
                total_seconds=segment.frames / inputs.encode.fps,
            )
    finally:
        if subtitle_file is not None:
            subtitle_file.unlink(missing_ok=True)
    return False, True


async def _finish(
    inputs: ExportInputs,
    work_dir: Path,
    output: Path,
    segments: list[Segment],
    rendered: int,
    reused: int,
    slowmo: list[dict[str, object]],
    timings: dict[str, int],
    report: ProgressFn,
) -> ExportResult:
    clock = time.perf_counter()
    stem: Path | None = None
    if inputs.music is not None:
        music = inputs.music
        key = _digest(
            {
                "sha": music.sha256,
                "start": round(music.start_seconds, 4),
                "duration": round(inputs.duration_seconds, 4),
                "gain": music.gain_expression,
            }
        )
        stem = work_dir / "music" / f"{key}.wav"
        if not stem.is_file():
            stem.parent.mkdir(parents=True, exist_ok=True)
            await builder.render(
                builder.build_music_stem(
                    music.source,
                    stem,
                    start_seconds=music.start_seconds,
                    duration_seconds=inputs.duration_seconds,
                    gain_db_expression=music.gain_expression,
                ),
                dry_run_first=False,
            )
    timings["music"] = round((time.perf_counter() - clock) * 1000)
    report("mixing sound and joining shots", 0.92)

    clock = time.perf_counter()
    listing = work_dir / "segments.txt"
    listing.write_text(
        "".join(f"file 'segments/{s.key}.mkv'\n" for s in segments), encoding="utf-8"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    await builder.render(
        builder.build_export_mux(
            listing,
            stem,
            output,
            duration_seconds=inputs.duration_seconds,
            audio_bitrate=inputs.audio_bitrate,
        ),
        dry_run_first=False,
    )
    timings["mux"] = round((time.perf_counter() - clock) * 1000)
    report("checking the file", 0.97)

    clock = time.perf_counter()
    probe = await builder.run(builder.build_probe(output))
    problems = platform_problems(
        probe,
        output,
        Expected(
            width=inputs.encode.width,
            height=inputs.encode.height,
            duration_seconds=inputs.duration_seconds,
        ),
    )
    timings["check"] = round((time.perf_counter() - clock) * 1000)
    result = ExportResult(
        output=output,
        segments_total=len(segments),
        segments_rendered=rendered,
        segments_reused=reused,
        timings_ms=timings,
        problems=problems,
        warnings=list(inputs.warnings),
        slowmo=slowmo,
    )
    manifest = output.with_suffix(".json")
    await asyncio.to_thread(
        manifest.write_text, json.dumps(result.to_json(), indent=2), encoding="utf-8"
    )
    report("finished", 1.0)
    return result


__all__ = [
    "RENDER_VERSION",
    "ExportInputs",
    "ExportResult",
    "InterpolatorFactory",
    "MusicInput",
    "Segment",
    "ShotSource",
    "frame_grid",
    "plan_segments",
    "render_export",
]
