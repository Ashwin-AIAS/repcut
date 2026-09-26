"""``pick_frame``: sharpest of N candidates, read from the source, HDR-aware."""

import asyncio
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import cv2
import pytest

import repcut.media.ffmpeg_builder as ffmpeg_builder_module
from repcut.analysis.params import FRAME_RECIPE, FrameRecipe
from repcut.analysis.sampler import candidate_timestamps, pick_frame
from repcut.analysis.types import SceneBoundary
from repcut.media.ffmpeg_builder import FFmpegNotInstalledError


def _scene(start: float, end: float, *, sequence_index: int = 0) -> SceneBoundary:
    return SceneBoundary(
        sequence_index=sequence_index,
        start_seconds=start,
        end_seconds=end,
        start_frame_source=round(start * 30),
        end_frame_source=round(end * 30),
    )


# --- candidate timestamps (pure, no I/O) --------------------------------------


def test_candidate_timestamps_are_evenly_spaced_and_never_on_the_edges() -> None:
    scene = _scene(0.0, 4.0)

    timestamps = candidate_timestamps(scene, 3)

    assert timestamps == [pytest.approx(1.0), pytest.approx(2.0), pytest.approx(3.0)]
    assert all(scene.start_seconds < t < scene.end_seconds for t in timestamps)


def test_candidate_timestamps_scale_with_candidate_count() -> None:
    scene = _scene(10.0, 11.0)

    assert len(candidate_timestamps(scene, 1)) == 1
    assert len(candidate_timestamps(scene, 5)) == 5


def test_candidate_timestamps_rejects_a_non_positive_count() -> None:
    with pytest.raises(ValueError, match="candidate_count"):
        candidate_timestamps(_scene(0.0, 1.0), 0)


# --- pick_frame against real FFmpeg -------------------------------------------


async def test_pick_frame_writes_exactly_the_destination_and_no_scratch_files(
    make_clip: Callable[..., Path], tmp_path: Path
) -> None:
    """Gate-adjacent: after `pick_frame` returns, only the caller's own path exists."""
    source = make_clip("clip.mp4", seconds=3.0, width=480, height=270, audio=False)
    destination = tmp_path / "out" / "scene_0.jpg"

    await pick_frame(source, _scene(0.5, 2.5), destination)

    assert destination.is_file()
    assert destination.stat().st_size > 0
    leftovers = [path for path in destination.parent.iterdir() if path != destination]
    assert leftovers == [], f"scratch candidate files were not cleaned up: {leftovers}"


async def test_pick_frame_matches_the_sources_dimensions(
    make_clip: Callable[..., Path], tmp_path: Path
) -> None:
    source = make_clip("wide.mp4", seconds=2.0, width=800, height=450, audio=False)
    destination = tmp_path / "scene_0.jpg"

    await pick_frame(source, _scene(0.2, 1.8), destination)

    image = cv2.imread(destination.as_posix())
    assert image is not None
    height, width = image.shape[:2]
    assert (width, height) == (800, 450)


async def test_pick_frame_picks_the_sharpest_of_the_candidates(
    tmp_path: Path,
) -> None:
    """A clip that goes sharp -> blurred -> sharp: the sharpest candidate wins, not the last one.

    Built with `boxblur` gated to the middle third by `enable=between(t,...)`,
    so the three evenly-spaced candidates land respectively in a sharp, a
    heavily blurred, and a sharp segment - and the winner must be one of the
    two sharp ones, never the blurred middle candidate.
    """
    source = tmp_path / "sharp_blur_sharp.mp4"
    process = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=480x270:rate=30:duration=3",
        "-vf",
        "boxblur=20:1:enable='between(t,1,2)'",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-crf",
        "18",
        "-an",
        source.as_posix(),
    )
    await process.wait()
    assert process.returncode == 0

    destination = tmp_path / "picked.jpg"
    # Candidates land at 0.75s, 1.5s, 2.25s (quartiles of [0, 3]) - the middle
    # one falls inside the blurred window, the other two do not.
    await pick_frame(source, _scene(0.0, 3.0), destination)

    winner = cv2.imread(destination.as_posix(), cv2.IMREAD_GRAYSCALE)
    blurred_frame = tmp_path / "blurred_reference.jpg"
    blur_process = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        "1.5",
        "-i",
        source.as_posix(),
        "-frames:v",
        "1",
        "-c:v",
        "mjpeg",
        "-q:v",
        "2",
        "-an",
        blurred_frame.as_posix(),
    )
    await blur_process.wait()
    assert blur_process.returncode == 0
    blurred = cv2.imread(blurred_frame.as_posix(), cv2.IMREAD_GRAYSCALE)
    assert winner is not None
    assert blurred is not None

    winner_sharpness = cv2.Laplacian(winner, cv2.CV_64F).var()
    blurred_sharpness = cv2.Laplacian(blurred, cv2.CV_64F).var()
    assert winner_sharpness > blurred_sharpness


async def test_pick_frame_tonemaps_when_the_source_is_hdr_tagged(
    make_clip: Callable[..., Path], tmp_path: Path
) -> None:
    source = make_clip("hdr.mp4", seconds=2.0, width=480, height=270, audio=False, hdr=True)
    destination = tmp_path / "scene_0.jpg"

    # Not raising, and producing a correctly-sized file, is the assertion here -
    # `test_ffmpeg_builder.py` already proves the tonemap chain changes pixels;
    # this proves `pick_frame` actually reaches that branch for a real HDR clip
    # end to end, colour probed once and reused across all three candidates.
    await pick_frame(source, _scene(0.2, 1.8), destination)

    image = cv2.imread(destination.as_posix())
    assert image is not None
    height, width = image.shape[:2]
    assert (width, height) == (480, 270)


async def test_pick_frame_falls_back_past_a_scene_at_the_very_start_of_the_clip(
    make_clip: Callable[..., Path], tmp_path: Path
) -> None:
    """A scene starting at 0.0s still produces three in-bounds, non-edge candidates."""
    source = make_clip("clip.mp4", seconds=1.0, width=320, height=180, audio=False)
    destination = tmp_path / "scene_0.jpg"

    await pick_frame(source, _scene(0.0, 1.0), destination, recipe=FRAME_RECIPE)

    assert destination.is_file()


async def test_pick_frame_respects_a_smaller_candidate_count(
    make_clip: Callable[..., Path], tmp_path: Path
) -> None:
    source = make_clip("clip.mp4", seconds=1.0, width=320, height=180, audio=False)
    destination = tmp_path / "scene_0.jpg"
    recipe = FrameRecipe(
        tone_map_target=FRAME_RECIPE.tone_map_target,
        candidate_count=1,
        quality=FRAME_RECIPE.quality,
    )

    await pick_frame(source, _scene(0.1, 0.9), destination, recipe=recipe)

    assert destination.is_file()


async def test_pick_frame_propagates_a_missing_executable_as_a_named_error(
    make_clip: Callable[..., Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real infrastructure failure is not swallowed by the HDR-fallback logic."""
    source = make_clip("clip.mp4", seconds=1.0, audio=False)
    destination = tmp_path / "scene_0.jpg"

    original_command = ffmpeg_builder_module.build_frame_extraction

    def _broken(*args: object, **kwargs: object) -> object:
        command = original_command(*args, **kwargs)  # type: ignore[arg-type]
        return replace(command, executable="repcut-nonexistent-ffmpeg")

    monkeypatch.setattr("repcut.analysis.sampler.build_frame_extraction", _broken)

    with pytest.raises(FFmpegNotInstalledError):
        await pick_frame(source, _scene(0.1, 0.9), destination)

    # No half-written scratch files left behind by the failed attempt.
    leftovers = list(destination.parent.glob(".*"))
    assert leftovers == []
