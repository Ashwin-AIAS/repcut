"""``detect_scenes`` against real FFmpeg-built fixtures, no mocking of PySceneDetect.

Gate criterion 12's foundation: given a fixture with known cuts, the boundary
count and rough timing are asserted the same way `test_ffmpeg_builder.py`
snapshot-tests a recipe - against real output, not a mocked detector.
"""

import subprocess
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

import pytest

from repcut.analysis.params import SCENE_DETECTOR_RECIPE, SceneDetectorRecipe
from repcut.analysis.scenes import SceneDetectionError, detect_scenes


def _run(argv: list[str]) -> None:
    # check=True: a fixture that silently failed to generate turns every
    # assertion that follows into a confusing "wrong scene count" instead of a
    # clear "ffmpeg failed" - the same discipline `conftest.py`'s own factories
    # use.
    subprocess.run(argv, capture_output=True, check=True, timeout=120)


@pytest.fixture
def make_hard_cuts_clip(tmp_path: Path) -> Callable[..., Path]:
    """A clip with exactly two hard cuts, three 2-second segments of solid colour.

    A throwaway local fixture, per the brief: `conftest.py` is not edited here.
    Solid colours (not `testsrc2`) give each segment zero internal motion, so
    `ContentDetector`'s content-aware threshold cannot mistake in-segment
    change for a cut - the only large per-frame difference in the whole clip is
    at the two boundaries this test asserts.
    """

    def _make(name: str = "cuts.mp4", *, segment_seconds: float = 2.0) -> Path:
        destination = tmp_path / name
        _run(
            [
                "ffmpeg",
                "-hide_banner",
                "-nostdin",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"color=c=red:size=320x240:rate=30:duration={segment_seconds}",
                "-f",
                "lavfi",
                "-i",
                f"color=c=blue:size=320x240:rate=30:duration={segment_seconds}",
                "-f",
                "lavfi",
                "-i",
                f"color=c=green:size=320x240:rate=30:duration={segment_seconds}",
                "-filter_complex",
                "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]",
                "-map",
                "[v]",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-crf",
                "20",
                destination.as_posix(),
            ]
        )
        return destination

    return _make


@pytest.fixture
def make_no_cuts_clip(tmp_path: Path) -> Callable[..., Path]:
    """One continuous shot, no cuts at all - the common "one exercise set" shape."""

    def _make(name: str = "nocuts.mp4", *, seconds: float = 3.0) -> Path:
        destination = tmp_path / name
        _run(
            [
                "ffmpeg",
                "-hide_banner",
                "-nostdin",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"testsrc2=size=320x240:rate=30:duration={seconds}",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-crf",
                "20",
                destination.as_posix(),
            ]
        )
        return destination

    return _make


def test_hard_cuts_are_detected_at_roughly_the_right_times(
    make_hard_cuts_clip: Callable[..., Path],
) -> None:
    """Three segments, two cuts, boundaries within one frame duration of the truth.

    "Within one frame duration" is gate criterion 12's own phrasing: at 30fps
    that is a tolerance of 1/30s, generous against detection landing on the
    boundary frame the encoder actually cut on.
    """
    clip = make_hard_cuts_clip(segment_seconds=2.0)

    boundaries = detect_scenes(clip, fps_source=30.0)

    assert len(boundaries) == 3
    frame_duration = 1.0 / 30.0
    assert boundaries[0].start_seconds == pytest.approx(0.0, abs=frame_duration)
    assert boundaries[0].end_seconds == pytest.approx(2.0, abs=frame_duration)
    assert boundaries[1].start_seconds == pytest.approx(2.0, abs=frame_duration)
    assert boundaries[1].end_seconds == pytest.approx(4.0, abs=frame_duration)
    assert boundaries[2].start_seconds == pytest.approx(4.0, abs=frame_duration)
    assert boundaries[2].end_seconds == pytest.approx(6.0, abs=frame_duration)


def test_boundaries_are_sequential_and_gapless(
    make_hard_cuts_clip: Callable[..., Path],
) -> None:
    """Every scene's start is the previous scene's end - no gap, no overlap."""
    clip = make_hard_cuts_clip(segment_seconds=1.5)

    boundaries = detect_scenes(clip, fps_source=30.0)

    for index in range(1, len(boundaries)):
        assert boundaries[index].sequence_index == index
        assert boundaries[index].start_seconds == pytest.approx(boundaries[index - 1].end_seconds)


def test_frame_handles_are_derived_from_seconds_against_fps_source(
    make_hard_cuts_clip: Callable[..., Path],
) -> None:
    """`start_frame_source`/`end_frame_source` track `fps_source`, not the proxy's rate.

    Detection runs against a 30fps proxy; asking with a different `fps_source`
    (as if the real source ran at 24fps) must change only the frame handles,
    never the authoritative seconds - amendment 008 resolution 4's own point.
    """
    clip = make_hard_cuts_clip(segment_seconds=1.0)

    at_24 = detect_scenes(clip, fps_source=24.0)

    for boundary in at_24:
        assert boundary.start_frame_source == round(boundary.start_seconds * 24.0)
        assert boundary.end_frame_source == round(boundary.end_seconds * 24.0)


def test_a_clip_with_no_cuts_is_one_scene_not_zero(
    make_no_cuts_clip: Callable[..., Path],
) -> None:
    """A single continuous take is a real, common shape - not an empty result.

    PySceneDetect's own `get_scene_list()` returns an empty list here unless
    asked with `start_in_scene=True` (measured; see the session report) - this
    is the regression that guards against losing that argument in a refactor.
    """
    clip = make_no_cuts_clip(seconds=2.0)

    boundaries = detect_scenes(clip, fps_source=30.0)

    assert len(boundaries) == 1
    assert boundaries[0].start_seconds == pytest.approx(0.0, abs=0.05)
    assert boundaries[0].end_seconds == pytest.approx(2.0, abs=0.1)


def test_a_missing_proxy_is_a_named_error(tmp_path: Path) -> None:
    with pytest.raises(SceneDetectionError):
        detect_scenes(tmp_path / "does-not-exist.mp4", fps_source=30.0)


def test_an_unreadable_proxy_is_a_named_error(tmp_path: Path) -> None:
    garbage = tmp_path / "garbage.mp4"
    garbage.write_bytes(b"not a real video file" * 64)

    with pytest.raises(SceneDetectionError):
        detect_scenes(garbage, fps_source=30.0)


def test_fps_source_must_be_positive(make_no_cuts_clip: Callable[..., Path]) -> None:
    clip = make_no_cuts_clip(seconds=1.0)

    with pytest.raises(ValueError, match="fps_source"):
        detect_scenes(clip, fps_source=0.0)
    with pytest.raises(ValueError, match="fps_source"):
        detect_scenes(clip, fps_source=-30.0)


def test_a_shorter_minimum_scene_length_finds_more_cuts(
    make_hard_cuts_clip: Callable[..., Path],
) -> None:
    """The recipe's threshold is real: a tighter one changes what is detected."""
    clip = make_hard_cuts_clip(segment_seconds=0.3)
    tight_recipe = SceneDetectorRecipe(
        threshold=SCENE_DETECTOR_RECIPE.threshold, minimum_scene_length=timedelta(seconds=0.1)
    )

    boundaries = detect_scenes(clip, fps_source=30.0, recipe=tight_recipe)

    assert len(boundaries) == 3
