"""One scene's motion and audio energy, combined into a 0-100 sparkline score.

Synchronous, unlike ``sampler.pick_frame``: this module's own signature has no
``async`` in it, and both halves below use blocking calls (OpenCV, a
synchronous FFmpeg subprocess) deliberately - the caller is expected to run it
through ``asyncio.to_thread`` the same way any other blocking work in this
codebase is kept off the event loop (`.claude/rules/code-style.md`), not to
have this module manage its own concurrency.

Reads the **proxy**, deliberately (amendment 008 resolution 6's own framing,
extended): this is a timing/energy signal for the UI's sparkline, not
something that leaves the machine, and the proxy's CFR timeline is what makes
frame-to-frame optical flow and a fixed-rate audio decode meaningful - VFR
gaps in the source would corrupt frame-to-frame motion measurement in a way
that has nothing to do with the footage's actual motion.
"""

import subprocess
from pathlib import Path

import cv2
import numpy as np

from repcut.analysis.types import EnergyMeasurement, SceneBoundary
from repcut.logging import get_logger
from repcut.media.ffmpeg_builder import (
    FFmpegCommand,
    FFmpegNotInstalledError,
    FFmpegTimeoutError,
    build_audio_energy_probe,
    classify_failure,
    parse_overall_rms_db,
)

logger = get_logger(__name__)

# How many frames are sampled across a scene's span for optical flow. Motion
# energy is a UI sparkline signal, not a precision measurement, so this stays
# small and fixed rather than scaling with scene duration - a 30-second scene
# does not need ten times the samples of a 3-second one to tell "static" from
# "moving" apart.
_MOTION_SAMPLE_COUNT = 8

# Frames are downscaled to this width before optical flow runs. Farneback's
# cost scales with pixel count; a sparkline signal does not need full
# resolution to tell a static shot from a moving one.
_MOTION_FRAME_WIDTH = 160

# Ceilings the two raw measurements are clamped against before being averaged
# into `energy_score`'s 0-100 scale. Not derived from a formula - chosen so
# that ordinary motion (a person moving in frame, not a whip-pan) and ordinary
# speech/gym-noise loudness land in the middle of the scale rather than pinned
# to one end; see the session report for the fixture measurements these were
# checked against (`make_motion_loudness_clip`'s static-vs-testsrc2,
# quiet-vs-loud pair, which must land clearly apart, not merely nonzero).
_MOTION_ENERGY_CEILING = 6.0
_AUDIO_ENERGY_CEILING = 0.5

# RMS level quieter than this is treated as inaudible for the purposes of the
# 0-100 score, matching how `astats` itself reports true digital silence as
# `-inf` rather than a very negative number.
_SILENCE_FLOOR_DB = -60.0


class MotionReadError(RuntimeError):
    """The proxy could not be opened, or reported no usable frame rate.

    Distinct from `scenes.SceneDetectionError`, which covers the same class of
    failure for PySceneDetect's own OpenCV wrapper - this module opens the
    proxy through `cv2.VideoCapture` directly, a different code path with its
    own failure shape.
    """


def _downscaled_gray(frame: np.ndarray) -> np.ndarray:
    """A frame, downscaled to `_MOTION_FRAME_WIDTH` wide and converted to grayscale.

    Optical flow only needs luma-scale structure, and a sparkline signal does
    not need full resolution - both the resize and the color conversion are
    strictly cost, not quality, for this measurement.
    """
    height, width = frame.shape[:2]
    scale = _MOTION_FRAME_WIDTH / width
    resized = cv2.resize(
        frame,
        (max(1, round(width * scale)), max(1, round(height * scale))),
        interpolation=cv2.INTER_AREA,
    )
    gray: np.ndarray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
    return gray


def sample_frame_indices(scene: SceneBoundary, fps: float, count: int) -> list[int]:
    """Up to ``count`` frame indices spread evenly across the scene's span.

    Against the PROXY's own frame rate, never the source's - the proxy is CFR,
    so its own frame index is a stable, evenly-spaced handle into the scene's
    time range in a way a VFR source's would not be.
    """
    start_frame = round(scene.start_seconds * fps)
    end_frame = max(start_frame + 1, round(scene.end_seconds * fps))
    span = end_frame - start_frame
    sample_count = max(2, min(count, span + 1))
    indices = {
        start_frame + round(index * span / (sample_count - 1)) for index in range(sample_count)
    }
    return sorted(indices)


def _motion_energy(proxy_path: Path, scene: SceneBoundary) -> float:
    """Mean Farneback optical-flow magnitude between frames sampled across the scene.

    A pure frame-difference metric (cheaper, no flow field) was the brief's own
    named alternative; Farneback is used instead because it is what "motion
    energy" names literally, and its cost - at `_MOTION_FRAME_WIDTH` px wide,
    `_MOTION_SAMPLE_COUNT` samples - is negligible against the FFmpeg encode
    steps the same job already pays for.
    """
    capture = cv2.VideoCapture(proxy_path.as_posix())
    try:
        if not capture.isOpened():
            raise MotionReadError("this proxy could not be opened for motion measurement")
        fps = capture.get(cv2.CAP_PROP_FPS)
        if fps <= 0:
            raise MotionReadError("this proxy reports no usable frame rate")

        magnitudes: list[float] = []
        previous: np.ndarray | None = None
        for index in sample_frame_indices(scene, fps, _MOTION_SAMPLE_COUNT):
            capture.set(cv2.CAP_PROP_POS_FRAMES, index)
            read_ok, frame = capture.read()
            if not read_ok:
                # Named: a frame index past the last decodable frame - the
                # scene's own end estimate rounded a fraction past the proxy's
                # true length. Skipped rather than failing the whole
                # measurement over one unreadable sample.
                logger.debug("motion_sample_unreadable", frame_index=index)
                continue
            gray = _downscaled_gray(frame)
            if previous is not None:
                # cv2-stubs' overloads for `calcOpticalFlowFarneback` don't
                # recognize a plain `uint8` ndarray (from `cvtColor`) against
                # either published signature, though both accept one at
                # runtime - the same bundled-stub gap `pyproject.toml`'s
                # `cv2.*` mypy override exists for; narrowed to one call
                # rather than silencing the whole module.
                flow = cv2.calcOpticalFlowFarneback(  # type: ignore[call-overload]
                    previous, gray, None, 0.5, 3, 15, 3, 5, 1.2, 0
                )
                magnitude, _ = cv2.cartToPolar(flow[..., 0], flow[..., 1])
                magnitudes.append(float(magnitude.mean()))
            previous = gray
    finally:
        capture.release()

    return sum(magnitudes) / len(magnitudes) if magnitudes else 0.0


def _run_audio_probe(command: FFmpegCommand) -> str:
    """Execute one audio-energy probe synchronously; return its stderr text.

    Not `ffmpeg_builder.run` - that is async, and `compute_scene_energy` is
    deliberately not. Failure classification is still shared:
    `ffmpeg_builder.classify_failure` is a pure function of stderr and
    returncode, so a broken filter graph or a missing codec raises the exact
    same typed exception here that an async render would.
    """
    try:
        # argv from ffmpeg_builder, shell=False - same discipline as
        # `ffmpeg_builder.run`'s `create_subprocess_exec`, just synchronous.
        result = subprocess.run(
            command.argv,
            capture_output=True,
            timeout=command.timeout_s,
            check=False,
        )
    except FileNotFoundError as error:
        # Named: the executable is not on PATH - the same condition
        # `ffmpeg_builder.run` reports as `FFmpegNotInstalledError`.
        raise FFmpegNotInstalledError(
            f"{command.executable} is not installed or not on PATH"
        ) from error
    except subprocess.TimeoutExpired as error:
        raise FFmpegTimeoutError(
            f"processing took longer than {int(command.timeout_s)}s and was stopped"
        ) from error

    stderr_text = result.stderr.decode("utf-8", errors="replace")
    if result.returncode != 0:
        raise classify_failure(stderr_text, result.returncode)
    return stderr_text


def _audio_energy(proxy_path: Path, scene: SceneBoundary) -> float:
    """Linear RMS amplitude ratio (0-1) of the proxy's audio within the scene's span.

    `astats`' own `RMS level dB` is converted from decibels
    (``10 ** (db / 20)``) rather than kept in dB, so the result is naturally
    non-negative and comparable to `_AUDIO_ENERGY_CEILING` on a linear scale -
    a scene with no audio stream, or one quieter than `_SILENCE_FLOOR_DB`,
    reads as exactly 0.0 rather than a large negative number.
    """
    command = build_audio_energy_probe(
        proxy_path, start_seconds=scene.start_seconds, end_seconds=scene.end_seconds
    )
    stderr_text = _run_audio_probe(command)
    rms_db = parse_overall_rms_db(stderr_text)
    if rms_db is None or rms_db < _SILENCE_FLOOR_DB:
        return 0.0
    return 10 ** (rms_db / 20)


def _combine(motion_energy: float, audio_energy: float) -> float:
    """Motion and audio energy, each clamped and averaged, onto a 0-100 scale.

    Deliberately simple - an even split of two independently normalized
    signals - because the only property this needs is *non-flat across
    genuinely different scenes* (the guide's own success criterion), not a
    perceptually tuned formula. `min(1.0, ...)` clamps a scene far above the
    ceiling (a whip-pan, a shouted rep count) to the top of the scale rather
    than letting one outlier scene compress everything else's range.
    """
    motion_component = min(1.0, motion_energy / _MOTION_ENERGY_CEILING)
    audio_component = min(1.0, audio_energy / _AUDIO_ENERGY_CEILING)
    return round(100.0 * (0.5 * motion_component + 0.5 * audio_component), 2)


def compute_scene_energy(proxy_path: Path, scene: SceneBoundary) -> EnergyMeasurement:
    """One scene's motion and audio energy, combined into a 0-100 comparable score.

    Raises `MotionReadError` if the proxy cannot be opened for the motion half,
    and an `ffmpeg_builder.FFmpegError` subclass if the audio half's FFmpeg
    invocation fails outright (missing executable, timeout, or a classified
    encode/filter failure) - both real infrastructure failures, not values this
    function should paper over with a default of zero.
    """
    motion_energy = _motion_energy(proxy_path, scene)
    audio_energy = _audio_energy(proxy_path, scene)
    energy_score = _combine(motion_energy, audio_energy)
    return EnergyMeasurement(
        motion_energy=motion_energy, audio_energy=audio_energy, energy_score=energy_score
    )


__all__ = ["MotionReadError", "compute_scene_energy", "sample_frame_indices"]
