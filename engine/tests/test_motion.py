"""``compute_scene_energy``: motion (optical flow) and audio (RMS) combined.

Gate criterion 13's foundation: energy curves must be non-flat across
genuinely different scenes. ``make_motion_loudness_clip`` (``conftest.py``,
`gate-runner`'s own fixture) is a clip built exactly for this - a static/quiet
segment followed by a moving/loud one - and is consumed here read-only.
"""

import inspect
from collections.abc import Callable
from pathlib import Path

import pytest

from repcut.analysis.motion import MotionReadError, compute_scene_energy, sample_frame_indices
from repcut.analysis.types import SceneBoundary
from repcut.media.ffmpeg_builder import FFmpegNotInstalledError


def _scene(start: float, end: float) -> SceneBoundary:
    return SceneBoundary(
        sequence_index=0,
        start_seconds=start,
        end_seconds=end,
        start_frame_source=round(start * 30),
        end_frame_source=round(end * 30),
    )


def test_sample_frame_indices_are_sorted_unique_and_in_range() -> None:
    indices = sample_frame_indices(_scene(1.0, 3.0), fps=30.0, count=8)

    assert indices == sorted(set(indices))
    assert indices[0] >= round(1.0 * 30)
    assert indices[-1] <= round(3.0 * 30)
    assert len(indices) >= 2  # optical flow needs at least a pair


def test_sample_frame_indices_never_collapse_to_one_frame_for_a_short_scene() -> None:
    """The recipe's own minimum scene length (0.5s) is the shortest real input."""
    indices = sample_frame_indices(_scene(0.0, 0.5), fps=30.0, count=8)

    assert len(indices) >= 2


async def test_energy_is_non_flat_between_a_static_quiet_and_a_moving_loud_segment(
    make_motion_loudness_clip: Callable[..., Path],
) -> None:
    """The gate's own criterion, stated directly: two genuinely different scenes
    must not land on the same energy score."""
    clip = make_motion_loudness_clip(segment_seconds=2.0)

    quiet_static = compute_scene_energy(clip, _scene(0.1, 1.9))
    loud_moving = compute_scene_energy(clip, _scene(2.1, 3.9))

    assert loud_moving.motion_energy > quiet_static.motion_energy
    assert loud_moving.audio_energy > quiet_static.audio_energy
    assert loud_moving.energy_score > quiet_static.energy_score
    # Not merely "greater than" by a rounding error - genuinely apart on the
    # 0-100 scale, which is what a sparkline needs to be legible at all.
    assert loud_moving.energy_score - quiet_static.energy_score > 10.0


async def test_energy_score_stays_within_its_documented_bounds(
    make_motion_loudness_clip: Callable[..., Path],
) -> None:
    clip = make_motion_loudness_clip(segment_seconds=1.5)

    for scene in (_scene(0.1, 1.4), _scene(1.6, 2.9)):
        measurement = compute_scene_energy(clip, scene)
        assert 0.0 <= measurement.energy_score <= 100.0
        assert measurement.motion_energy >= 0.0
        assert measurement.audio_energy >= 0.0


async def test_a_silent_video_only_scene_has_zero_audio_energy(
    make_clip: Callable[..., Path],
) -> None:
    """No audio stream at all - `astats` prints nothing, read as zero, not a failure."""
    clip = make_clip("silent.mp4", seconds=2.0, audio=False)

    measurement = compute_scene_energy(clip, _scene(0.1, 1.9))

    assert measurement.audio_energy == 0.0


def test_a_missing_proxy_is_a_named_error(tmp_path: Path) -> None:
    with pytest.raises(MotionReadError):
        compute_scene_energy(tmp_path / "does-not-exist.mp4", _scene(0.0, 1.0))


def test_an_unreadable_proxy_is_a_named_error(tmp_path: Path) -> None:
    garbage = tmp_path / "garbage.mp4"
    garbage.write_bytes(b"not a real video file" * 64)

    with pytest.raises(MotionReadError):
        compute_scene_energy(garbage, _scene(0.0, 1.0))


async def test_compute_scene_energy_is_synchronous(
    make_clip: Callable[..., Path],
) -> None:
    """The brief's own signature: no `async def`, so a caller can thread it."""
    assert not inspect.iscoroutinefunction(compute_scene_energy)


async def test_a_missing_ffmpeg_executable_is_a_named_error(
    make_clip: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The audio half's synchronous subprocess call is still typed-error disciplined."""
    clip = make_clip("clip.mp4", seconds=1.0)

    # String-target form: `subprocess` is an implementation detail imported
    # inside `motion.py`, not part of its own public re-export surface, so
    # patching through the module object's attribute fails `--strict`'s
    # implicit-reexport check even though it works at runtime.
    monkeypatch.setattr("repcut.analysis.motion.subprocess.run", _raise_file_not_found)

    with pytest.raises(FFmpegNotInstalledError):
        compute_scene_energy(clip, _scene(0.1, 0.9))


def _raise_file_not_found(*_args: object, **_kwargs: object) -> object:
    raise FileNotFoundError("ffmpeg")
