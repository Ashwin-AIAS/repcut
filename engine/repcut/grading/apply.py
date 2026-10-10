"""Grading, end to end: measure a scene, correct it, apply a theme, render.

Composition only. The numbers come from ``corrector.py`` and ``lut.py``, the
argv from ``media.ffmpeg_builder`` (`.claude/rules/ffmpeg.md`: every
invocation is built there), the paths from ``packs.py``. Everything reads the
normalised proxy, never the source: full-resolution application is export's
(Prompt 06), which runs the same normalisation on the source and then this
same chain (``docs/future-prompts/prompt-06-export-colour.md``).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from repcut.grading.corrector import Correction, SceneTags, correct, measure
from repcut.grading.packs import LoadedPack, ensure_lut
from repcut.grading.theme import Theme
from repcut.media import ffmpeg_builder as builder
from repcut.media.ffmpeg_builder import GradeSpec, ProgressCallback

# Local measurement frames per scene: one every two seconds, at least three,
# at most twelve (amendment 012 row 10: as many as the scene needs, all local).
SECONDS_PER_MEASURE_FRAME = 2.0
MIN_MEASURE_FRAMES = 3
MAX_MEASURE_FRAMES = 12


def measure_timestamps(start_seconds: float, end_seconds: float) -> list[float]:
    """Evenly spread, never on a boundary (a cut's first frame can belong to either shot)."""
    duration = max(end_seconds - start_seconds, 0.0)
    count = int(duration / SECONDS_PER_MEASURE_FRAME)
    count = min(max(count, MIN_MEASURE_FRAMES), MAX_MEASURE_FRAMES)
    step = duration / (count + 1)
    return [start_seconds + step * (i + 1) for i in range(count)]


async def read_measure_frames(proxy: Path, timestamps: Sequence[float]) -> list[np.ndarray]:
    """Small RGB frames of the proxy, decoded locally and discarded after (P4)."""
    frames: list[np.ndarray] = []
    with TemporaryDirectory(prefix="repcut-measure-") as scratch:
        for index, timestamp in enumerate(timestamps):
            target = Path(scratch) / f"m{index}.rgb"
            await builder.run(
                builder.build_measure_frame(proxy, target, timestamp_seconds=timestamp)
            )
            raw = np.frombuffer(target.read_bytes(), dtype=np.uint8)
            row = builder.MEASURE_WIDTH * 3
            if raw.size == 0 or raw.size % row:
                continue
            frames.append(raw.reshape(-1, builder.MEASURE_WIDTH, 3))
    return frames


async def scene_correction(
    proxy: Path,
    *,
    start_seconds: float,
    end_seconds: float,
    tags: SceneTags,
    theme: Theme,
) -> Correction:
    frames = await read_measure_frames(proxy, measure_timestamps(start_seconds, end_seconds))
    return correct(measure(frames), tags, theme.adapt)


def grade_spec(
    correction: Correction, theme: Theme, *, intensity: int, lut: Path | None
) -> GradeSpec:
    return GradeSpec(
        exposure_stops=correction.exposure_stops,
        gains=correction.gains(),
        denoise=correction.denoise,
        lut=lut,
        intensity=intensity,
        grain=theme.finish.grain,
    )


@dataclass(frozen=True, slots=True)
class Recommendation:
    theme_id: str
    scores: dict[str, float]


def recommend_theme(pack: LoadedPack, tags: SceneTags) -> Recommendation:
    """The theme analysis suggests for these tags. No tags: the pack's default (P2: a default)."""
    scores: dict[str, float] = {}
    for theme in pack.themes:
        score = theme.recommend.base
        if tags.lighting_quality is not None:
            score += theme.recommend.lighting_quality.get(tags.lighting_quality, 0.0)  # type: ignore[call-overload]
        if tags.lighting_temperature is not None:
            score += theme.recommend.lighting_temperature.get(tags.lighting_temperature, 0.0)  # type: ignore[call-overload]
        scores[theme.id] = round(score, 4)
    if tags.lighting_quality is None and tags.lighting_temperature is None:
        return Recommendation(theme_id=pack.manifest.default_theme, scores=scores)
    best = max(scores.values())
    # Ties go to pack order, so the answer never depends on dict iteration.
    winner = next(t for t in pack.manifest.themes if scores[t] == best)
    return Recommendation(theme_id=winner, scores=scores)


async def render_still(
    proxy: Path,
    destination: Path,
    *,
    timestamp_seconds: float,
    correction: Correction,
    theme: Theme,
    pack_id: str,
    intensity: int,
    luts_root: Path,
) -> Path:
    lut = ensure_lut(luts_root, pack_id, theme)
    command = builder.build_graded_still(
        proxy,
        destination,
        timestamp_seconds=timestamp_seconds,
        grade=grade_spec(correction, theme, intensity=intensity, lut=lut),
    )
    return await builder.render(command, dry_run_first=False)


async def render_preview(
    proxy: Path,
    destination: Path,
    *,
    start_seconds: float,
    end_seconds: float,
    correction: Correction,
    theme: Theme,
    pack_id: str,
    intensity: int,
    luts_root: Path,
    on_progress: ProgressCallback | None = None,
) -> Path:
    lut = ensure_lut(luts_root, pack_id, theme)
    command = builder.build_graded_preview(
        proxy,
        destination,
        start_seconds=start_seconds,
        end_seconds=end_seconds,
        grade=grade_spec(correction, theme, intensity=intensity, lut=lut),
    )
    return await builder.render(
        command, on_progress=on_progress, total_seconds=end_seconds - start_seconds
    )


__all__ = [
    "Recommendation",
    "grade_spec",
    "measure_timestamps",
    "read_measure_frames",
    "recommend_theme",
    "render_preview",
    "render_still",
    "scene_correction",
]
