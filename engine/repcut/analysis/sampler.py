"""One representative frame per scene: sharpest of N candidates, read from the SOURCE.

Pure in the sense this whole package is pure: given a source path, a
:class:`~repcut.analysis.types.SceneBoundary` and a destination path, this
writes exactly one JPEG to ``destination`` and returns nothing. No SQLAlchemy
import - the caller (a later, separate persistence pass) decides whether this
needs to run at all and what row records the result; this module does not know
``Scene`` exists.

Amendment 008 resolution 3 is the reason every extraction here reads
``source``, never a proxy: `docs/future-prompts/prompt-03-frame-source.md`
measured the proxy at 406x720 for portrait phone source, a thumbnail of what
Gemini is supposed to see, with no error anywhere to catch it. ``sampler.py``
is the module that measurement is about - every ``build_frame_extraction``
call below takes ``source`` and nothing else.
"""

import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4

import cv2

from repcut.analysis.params import FRAME_RECIPE, FrameRecipe
from repcut.analysis.types import SceneBoundary
from repcut.logging import get_logger
from repcut.media.ffmpeg_builder import (
    FFmpegFilterGraphError,
    build_frame_extraction,
    build_probe,
    render,
    run,
    source_is_hdr,
)
from repcut.media.metadata import ColorProperties, parse_color_properties

logger = get_logger(__name__)


class FrameScoringError(RuntimeError):
    """A rendered candidate frame could not be read back for sharpness scoring.

    Not expected in practice - ``render`` already proved the file exists and is
    non-empty before this runs - but ``cv2.imread`` returning ``None`` for a
    file it cannot decode is a real, silent-by-default failure mode, and a
    ``None`` fed to ``cv2.Laplacian`` raises an opaque OpenCV error several
    frames away from the file that caused it.
    """


def candidate_timestamps(scene: SceneBoundary, candidate_count: int) -> list[float]:
    """``candidate_count`` timestamps, evenly spaced, never on the scene's own edges.

    Fractions ``1/(n+1), 2/(n+1), ..., n/(n+1)`` of the span - for the guide's
    own count of three, the quartiles 0.25/0.5/0.75. Never 0.0 or 1.0: a cut
    transition can still be resolving (a fade, a motion blur smear) right at a
    detected boundary, and a candidate landing exactly there is the one most
    likely to be the blurriest of the three by construction, not by content.
    """
    if candidate_count < 1:
        raise ValueError("candidate_count must be at least 1")
    span = scene.end_seconds - scene.start_seconds
    return [
        scene.start_seconds + span * (index + 1) / (candidate_count + 1)
        for index in range(candidate_count)
    ]


def _candidate_path(destination: Path, index: int, run_token: str) -> Path:
    """A scratch path beside ``destination``, one per candidate, never the final name.

    Kept beside ``destination`` rather than in a separate temp directory so the
    final "promote the winner" step (`os.replace`) is a same-filesystem rename -
    the same reason `ffmpeg_builder.temp_target` colocates its own temp names
    with the artifact they will become.

    ``run_token`` is one random value shared by every candidate of one
    `pick_frame` call, not derived from `destination` alone - the same
    collision `ffmpeg_builder.temp_target`'s own random token exists to avoid:
    two `pick_frame` calls racing on the same scene (a retried job racing one
    that is not dead yet) would otherwise read, score and clean up each
    other's candidate files.
    """
    return destination.with_name(
        f".{destination.stem}.candidate{index}.{run_token}{destination.suffix}"
    )


async def _probe_source_color(source: Path) -> ColorProperties:
    """The source's colour tags, read once - never per candidate frame.

    A dedicated read of ``build_probe``'s existing JSON rather than a second,
    narrower probe command: one ffprobe invocation already answers this, and a
    malformed or unreadable response degrades to "no signal" (plain SDR
    handling) rather than failing frame sampling over a secondary read - the
    extraction itself, moments later, is the real test of whether this source
    can be read at all.
    """
    stdout = await run(build_probe(source))
    try:
        document = json.loads(stdout)
    except json.JSONDecodeError:
        logger.warning("frame_color_probe_unparseable")
        return ColorProperties(color_primaries=None, color_transfer=None)
    if not isinstance(document, dict):
        return ColorProperties(color_primaries=None, color_transfer=None)
    return parse_color_properties(document)


async def _extract_candidate(
    source: Path,
    destination: Path,
    *,
    timestamp_seconds: float,
    color: ColorProperties,
    recipe: FrameRecipe,
    tonemap: bool,
) -> None:
    """Render one candidate. ``tonemap=False`` extracts with no colour filter at all."""
    command = build_frame_extraction(
        source,
        destination,
        timestamp_seconds=timestamp_seconds,
        color_primaries=color.color_primaries if tonemap else None,
        color_transfer=color.color_transfer if tonemap else None,
        recipe=recipe,
    )
    # No dry run: a single `-frames:v 1` extraction is already about as cheap
    # as the two-second dry-run slice would be, so validating the graph twice
    # buys nothing - the same reasoning `ingest.py`'s thumbnail-strip render
    # already applies to a single-frame filter graph.
    await render(command, dry_run_first=False)


def _laplacian_variance(path: Path) -> float:
    """Sharpness score: variance of the Laplacian, higher is sharper.

    Blocking OpenCV work - always called through ``asyncio.to_thread``, never
    awaited directly (`.claude/rules/code-style.md`: blocking work never blocks
    the event loop).
    """
    image = cv2.imread(path.as_posix(), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise FrameScoringError("a rendered candidate frame could not be read back for scoring")
    variance = cv2.Laplacian(image, cv2.CV_64F).var()
    return float(variance)


def _cleanup_candidates(paths: list[Path]) -> None:
    """Remove every candidate scratch file. Safe to call after the winner is gone.

    Blocking; always called through ``asyncio.to_thread``. ``missing_ok=True``
    because the winning candidate has already been renamed onto ``destination``
    by the time this runs and no longer exists at its candidate path.
    """
    for path in paths:
        path.unlink(missing_ok=True)


async def pick_frame(
    source: Path,
    scene: SceneBoundary,
    destination: Path,
    *,
    recipe: FrameRecipe = FRAME_RECIPE,
) -> None:
    """Extract ``recipe.candidate_count`` frames from ``scene``, keep the sharpest.

    Writes the winner to ``destination`` - a path the caller builds (the store
    module's job, per amendment 008 resolution 2) and this function only
    writes to, never invents.

    Colour is probed once for the whole call, not once per candidate
    (`docs/future-prompts/prompt-03-frame-source.md`'s own instruction): three
    extractions from the same source share one HDR/SDR decision. If the real
    tone-map chain (`zscale`+`tonemap`) is rejected by this FFmpeg build - no
    ``libzimg`` - the first candidate's failure is caught once, logged, and
    every remaining candidate (including a retry of the first) falls back to
    plain extraction with no colour filter at all, rather than retrying a
    doomed filter graph three times. This is the documented fallback
    `.claude/rules/ffmpeg.md` asks for: a still-usable, un-tone-mapped frame
    beats no frame, and P1 is untouched either way - nothing is generated,
    only how existing pixels are colour-converted differs.
    """
    color = await _probe_source_color(source)
    timestamps = candidate_timestamps(scene, recipe.candidate_count)
    run_token = uuid4().hex[:8]
    candidate_paths = [
        _candidate_path(destination, index, run_token) for index in range(len(timestamps))
    ]
    tonemap = source_is_hdr(color.color_primaries, color.color_transfer)

    try:
        for path, timestamp_seconds in zip(candidate_paths, timestamps, strict=True):
            try:
                await _extract_candidate(
                    source,
                    path,
                    timestamp_seconds=timestamp_seconds,
                    color=color,
                    recipe=recipe,
                    tonemap=tonemap,
                )
            except FFmpegFilterGraphError:
                if not tonemap:
                    raise  # the SDR path has no filter graph to blame this on
                logger.warning(
                    "hdr_tonemap_unavailable_falling_back",
                    reason="zscale/tonemap filter chain was rejected by this FFmpeg build",
                )
                tonemap = False
                await _extract_candidate(
                    source,
                    path,
                    timestamp_seconds=timestamp_seconds,
                    color=color,
                    recipe=recipe,
                    tonemap=False,
                )

        scores = await asyncio.gather(
            *(asyncio.to_thread(_laplacian_variance, path) for path in candidate_paths)
        )
        winner = candidate_paths[max(range(len(scores)), key=lambda index: scores[index])]
        await asyncio.to_thread(_promote, winner, destination)
    finally:
        await asyncio.to_thread(_cleanup_candidates, candidate_paths)

    logger.debug(
        "frame_sampled",
        sequence_index=scene.sequence_index,
        candidate_count=len(candidate_paths),
        tonemapped=tonemap,
    )


def _promote(winner: Path, destination: Path) -> None:
    """Atomically rename the sharpest candidate onto its final name. Blocking."""
    os.replace(winner, destination)


__all__ = ["FrameScoringError", "candidate_timestamps", "pick_frame"]
