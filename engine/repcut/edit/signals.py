"""Per-clip measurements the segment selector reads: loudness, motion, shake, speech.

Read from the **proxy** (CFR, bt709, one audio rate), in fixed windows of
``WINDOW_SECONDS``, like ``analysis/motion.py``'s per-scene energy but finer:
a scene is the wrong unit for "the camera shook for two seconds in the middle".

- **audio_db**: RMS level of the window, dBFS.
- **motion**: mean absolute difference between consecutive sampled frames, in
  8-bit codes at ``FRAME_WIDTH`` px. Near zero means nothing moves.
- **shake**: the high-frequency part of the frame-to-frame camera translation
  (tracked corners, RANSAC), as a fraction of the frame width. A pan is low
  frequency and is not shake; jitter around it is.
- **speech**: silero-vad regions in clip seconds, or None when the model is not
  installed (the caller says so instead of pretending there was no speech).

Everything stays on the machine (P4). The result is cached as JSON beside the
clip's other derived files, keyed by ``SIGNALS_VERSION``.
"""

import json
import math
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from repcut.edit.vad import SAMPLE_RATE as VAD_RATE
from repcut.edit.vad import SpeechRegion, VadUnavailableError, detect_speech

SIGNALS_VERSION = 1
SIGNALS_KIND = "edit_signals"
SIGNALS_FILENAME = "signals.json"

WINDOW_SECONDS = 0.5
SAMPLES_PER_SECOND = 6.0
FRAME_WIDTH = 160
SILENCE_DB = -90.0
# Fewer tracked corners than this cannot be trusted to fit a camera motion.
MIN_TRACKED = 8


class SignalsReadError(RuntimeError):
    """The proxy could not be read for measurement. A fixed sentence."""


@dataclass(frozen=True, slots=True)
class ClipSignals:
    version: int
    duration_seconds: float
    window_seconds: float
    audio_db: tuple[float, ...]
    motion: tuple[float, ...]
    shake: tuple[float, ...]
    speech: tuple[SpeechRegion, ...] | None

    def to_json(self) -> str:
        return json.dumps(
            {
                "version": self.version,
                "duration_seconds": self.duration_seconds,
                "window_seconds": self.window_seconds,
                "audio_db": list(self.audio_db),
                "motion": list(self.motion),
                "shake": list(self.shake),
                "speech": None
                if self.speech is None
                else [[region.start, region.end] for region in self.speech],
            }
        )

    @classmethod
    def from_json(cls, text: str) -> "ClipSignals":
        data = json.loads(text)
        speech = data.get("speech")
        return cls(
            version=int(data["version"]),
            duration_seconds=float(data["duration_seconds"]),
            window_seconds=float(data["window_seconds"]),
            audio_db=tuple(float(v) for v in data["audio_db"]),
            motion=tuple(float(v) for v in data["motion"]),
            shake=tuple(float(v) for v in data["shake"]),
            speech=None
            if speech is None
            else tuple(SpeechRegion(float(a), float(b)) for a, b in speech),
        )


def window_count(duration: float) -> int:
    return max(1, math.ceil(duration / WINDOW_SECONDS - 1e-9))


def audio_windows(samples: np.ndarray, sample_rate: int, duration: float) -> tuple[float, ...]:
    """RMS dBFS per window. A window with no samples is silence."""
    per = int(WINDOW_SECONDS * sample_rate)
    levels = []
    for index in range(window_count(duration)):
        chunk = samples[index * per : (index + 1) * per]
        rms = float(np.sqrt(np.mean(np.square(chunk, dtype=np.float64)))) if chunk.size else 0.0
        levels.append(round(20 * math.log10(rms), 2) if rms > 1e-9 else SILENCE_DB)
    return tuple(levels)


def _gray(frame: np.ndarray) -> np.ndarray:
    height, width = frame.shape[:2]
    size = (FRAME_WIDTH, max(1, round(height * FRAME_WIDTH / width)))
    small = cv2.resize(frame, size, interpolation=cv2.INTER_AREA)
    return np.asarray(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), dtype=np.uint8)


def _phase_shift(previous: np.ndarray, current: np.ndarray) -> tuple[float, float]:
    """Whole-frame translation by phase correlation (copies: it windows in place)."""
    (dx, dy), _ = cv2.phaseCorrelate(
        previous.astype(np.float32).copy(), current.astype(np.float32).copy()
    )
    return float(dx), float(dy)


def global_shift(previous: np.ndarray, current: np.ndarray) -> tuple[float, float]:
    """The camera's translation between two frames, ignoring things moving in them.

    Corners are tracked with pyramidal Lucas-Kanade and a similarity transform
    is fitted with RANSAC, so a lifter moving through the frame is an outlier
    and the camera's own motion is what remains. Phase correlation alone read
    moving content as shake (see the report), so it is only the fallback:

    when too few corners survive to fit - which is exactly what violent shake
    does to a frame - the shift is measured by phase correlation instead.
    Returning zero there would report the worst shake as a steady camera.
    """
    corners = cv2.goodFeaturesToTrack(previous, maxCorners=120, qualityLevel=0.01, minDistance=6)
    if corners is None or len(corners) < MIN_TRACKED:
        return _phase_shift(previous, current)
    # The bundled cv2 stubs type `nextPts` as required, though None is the
    # documented "no initial guess" (the same stub gap as motion.py's ignore).
    tracked, status, _ = cv2.calcOpticalFlowPyrLK(  # type: ignore[call-overload]
        previous, current, corners, None
    )
    good = status.reshape(-1) == 1
    if int(good.sum()) < MIN_TRACKED:
        return _phase_shift(previous, current)
    matrix, _ = cv2.estimateAffinePartial2D(
        corners[good], tracked[good], method=cv2.RANSAC, ransacReprojThreshold=1.5
    )
    if matrix is None:
        return _phase_shift(previous, current)
    return float(matrix[0, 2]), float(matrix[1, 2])


def visual_windows(proxy: Path, duration: float) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """(motion, shake) per window, from frames sampled at ``SAMPLES_PER_SECOND``."""
    capture = cv2.VideoCapture(proxy.as_posix())
    try:
        if not capture.isOpened():
            raise SignalsReadError("this clip's preview could not be opened for measurement")
        fps = capture.get(cv2.CAP_PROP_FPS)
        if fps <= 0:
            raise SignalsReadError("this clip's preview reports no usable frame rate")
        stride = max(1, round(fps / SAMPLES_PER_SECOND))
        times: list[float] = []
        motion: list[float] = []
        shifts: list[tuple[float, float]] = []
        previous: np.ndarray | None = None
        index = 0
        while capture.grab():
            if index % stride == 0:
                ok, frame = capture.retrieve()
                if ok:
                    gray = _gray(frame)
                    if previous is not None:
                        times.append(index / fps)
                        difference = np.abs(gray.astype(np.int16) - previous.astype(np.int16))
                        motion.append(float(difference.mean()))
                        shifts.append(global_shift(previous, gray))
                    previous = gray
            index += 1
    finally:
        capture.release()

    count = window_count(duration)
    if not times:
        return (0.0,) * count, (0.0,) * count
    shift = np.asarray(shifts)
    # Shake: the translation minus its own moving average (a pan survives the
    # average, jitter does not), as a fraction of the frame width.
    kernel = np.ones(5) / 5
    smooth = np.column_stack(
        [np.convolve(shift[:, axis], kernel, mode="same") for axis in range(2)]
    )
    jitter = np.hypot(*(shift - smooth).T) / FRAME_WIDTH
    stamp = np.asarray(times)
    motion_array = np.asarray(motion)
    motion_out, shake_out = [], []
    for slot in range(count):
        mask = (stamp >= slot * WINDOW_SECONDS) & (stamp < (slot + 1) * WINDOW_SECONDS)
        motion_out.append(round(float(motion_array[mask].mean()), 4) if mask.any() else 0.0)
        shake_out.append(round(float(jitter[mask].mean()), 5) if mask.any() else 0.0)
    return tuple(motion_out), tuple(shake_out)


def speech_regions(samples: np.ndarray, model_path: Path) -> tuple[SpeechRegion, ...] | None:
    try:
        return detect_speech(samples, model_path)
    except VadUnavailableError:
        # Named: the model is not installed. None, not (), so nobody reads
        # "no speech" where the truth is "not measured".
        return None


def measure(proxy: Path, audio_16k: np.ndarray, duration: float, model_path: Path) -> ClipSignals:
    """All four signals for one clip. Blocking: run in a thread."""
    motion, shake = visual_windows(proxy, duration)
    return ClipSignals(
        version=SIGNALS_VERSION,
        duration_seconds=round(duration, 4),
        window_seconds=WINDOW_SECONDS,
        audio_db=audio_windows(audio_16k, VAD_RATE, duration),
        motion=motion,
        shake=shake,
        speech=speech_regions(audio_16k, model_path),
    )


__all__ = [
    "SIGNALS_FILENAME",
    "SIGNALS_KIND",
    "SIGNALS_VERSION",
    "WINDOW_SECONDS",
    "ClipSignals",
    "SignalsReadError",
    "audio_windows",
    "measure",
    "visual_windows",
    "window_count",
]
