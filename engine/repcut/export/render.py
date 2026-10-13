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
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path

from repcut.captions import ass
from repcut.captions.styles import CaptionStyle
from repcut.export.check import Expected, platform_problems
from repcut.media import ffmpeg_builder as builder
from repcut.media.artifacts import PROXY_RECIPE
from repcut.media.ffmpeg_builder import CropBox, ExportEncode, GradeSpec

RENDER_VERSION = 1


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


@dataclass(frozen=True, slots=True)
class Segment:
    index: int
    shot: ShotSource
    frame_in: int
    frame_out: int
    start_seconds: float
    subtitles: str | None
    key: str

    @property
    def frames(self) -> int:
        return self.frame_out - self.frame_in


@dataclass(slots=True)
class ExportResult:
    output: Path
    segments_total: int
    segments_rendered: int
    segments_reused: int
    timings_ms: dict[str, int] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

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
        # The source moves with the quantised edge, so picture and timeline agree.
        start = max(0.0, shot.source_in + (local_start - shot.timeline_in))
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
            }
        )
        out.append(Segment(index, shot, frame_in, frame_out, start, subtitles, key))
    return out


ProgressFn = Callable[[str, float], None]


async def render_export(
    inputs: ExportInputs,
    work_dir: Path,
    output: Path,
    *,
    on_progress: ProgressFn | None = None,
) -> ExportResult:
    """Render (or resume) the export into ``output``. Raises ``FFmpegError`` on failure."""
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
    clock = time.perf_counter()
    for segment in segments:
        target = segments_dir / f"{segment.key}.mkv"
        if target.is_file() and target.stat().st_size > 0:
            reused += 1
        else:
            subtitle_file: Path | None = None
            if segment.subtitles is not None:
                subtitle_file = segments_dir / f"{segment.key}.ass"
                subtitle_file.write_text(segment.subtitles, encoding="utf-8")
            shot = segment.shot
            command = builder.build_export_segment(
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
            )
            base = done_frames

            def progress(fraction: float, base: int = base, frames: int = segment.frames) -> None:
                report("rendering shots", 0.9 * (base + fraction * frames) / total_frames)

            # The plan is dry-run once per export, on the first shot it renders
            # (ffmpeg.md: fail fast on a bad graph, not after the whole timeline).
            await builder.render(
                command,
                dry_run_first=first_new,
                on_progress=progress,
                total_seconds=segment.frames / inputs.encode.fps,
            )
            first_new = False
            rendered += 1
            if subtitle_file is not None:
                subtitle_file.unlink(missing_ok=True)
        done_frames += segment.frames
        report("rendering shots", 0.9 * done_frames / total_frames)
    timings["segments"] = round((time.perf_counter() - clock) * 1000)

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
    "MusicInput",
    "Segment",
    "ShotSource",
    "frame_grid",
    "plan_segments",
    "render_export",
]
