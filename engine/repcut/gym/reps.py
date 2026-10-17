"""Counting reps from joint angles, on hero segments only.

The PoseCoach approach: a joint angle over time (elbow, knee or hip, whichever
moves most), smoothed, then counted with hysteresis - a rep is one full trip
from the extended end of the movement to the flexed end and back. Thresholds
come from the segment's own range, so a half-squat and a deep squat both count.

PRODUCT BOUNDARY, named on purpose: this module counts repetitions. It never
scores form, depth or technique, and never measures or describes a body.
Repcut edits video; it does not judge bodies. A rep count is only ever a
*suggestion* the person confirms before it is burned in (P2): a wrong count
posted under someone's name destroys trust faster than a missing one.

The pose model runs on hero segments only (``MAX_POSE_SECONDS``), never on a
whole clip (VRAM and time budget). Keypoints are COCO-17, as YOLO pose
returns them; only angles are kept, never the keypoints or a frame.
"""

from __future__ import annotations

import importlib
import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from repcut.gym.weights import POSE, WeightsMissingError, verify_weights

COUNTER_VERSION = 1
PRODUCT_BOUNDARY = "Repcut counts reps; it never scores form or analyses a body."
MAX_POSE_SECONDS = 60.0
POSE_FPS = 15.0
MIN_AMPLITUDE_DEGREES = 25.0
MIN_REP_SECONDS = 0.5
MIN_VISIBLE = 0.5
_HYSTERESIS = 0.3

# COCO-17 indices.
SHOULDER = (5, 6)
ELBOW = (7, 8)
WRIST = (9, 10)
HIP = (11, 12)
KNEE = (13, 14)
ANKLE = (15, 16)
JOINTS: dict[str, tuple[tuple[int, int], tuple[int, int], tuple[int, int]]] = {
    "elbow": (SHOULDER, ELBOW, WRIST),
    "knee": (HIP, KNEE, ANKLE),
    "hip": (SHOULDER, HIP, KNEE),
}

NO_POSE = (
    "rep counting needs PyTorch and ultralytics (make setup-gpu); "
    "type the reps yourself, or leave them out"
)
NOBODY = "no one was clearly visible in this moment; type the reps yourself"
NO_REPS = "no repeated movement was found in this moment; type the reps yourself"
TOO_LONG = "this moment is too long to count reps in"

# One frame's keypoints: 17 x (x, y, confidence), or None when nobody was found.
Keypoints = Sequence[tuple[float, float, float]] | None


@dataclass(frozen=True, slots=True)
class RepCount:
    reps: int | None
    joint: str | None
    amplitude_degrees: float
    reason: str | None = None


def _angle(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]) -> float:
    v1 = (a[0] - b[0], a[1] - b[1])
    v2 = (c[0] - b[0], c[1] - b[1])
    n1, n2 = math.hypot(*v1), math.hypot(*v2)
    if n1 == 0 or n2 == 0:
        return math.nan
    cos = max(-1.0, min(1.0, (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)))
    return math.degrees(math.acos(cos))


def joint_series(frames: Sequence[Keypoints], joint: str) -> list[float]:
    """The joint's angle per frame, both sides averaged where visible; NaN where hidden."""
    (sa, sb), (ea, eb), (wa, wb) = JOINTS[joint]
    out: list[float] = []
    for kp in frames:
        if kp is None:
            out.append(math.nan)
            continue
        values = []
        for a, b, c in ((sa, ea, wa), (sb, eb, wb)):
            if min(kp[a][2], kp[b][2], kp[c][2]) >= MIN_VISIBLE:
                values.append(_angle(kp[a][:2], kp[b][:2], kp[c][:2]))
        values = [v for v in values if not math.isnan(v)]
        out.append(sum(values) / len(values) if values else math.nan)
    return out


def _fill_and_smooth(series: list[float], fps: float) -> np.ndarray | None:
    values = np.array(series, dtype=float)
    good = ~np.isnan(values)
    if good.sum() < max(4, 0.5 * len(values)):
        return None
    index = np.arange(len(values))
    values = np.interp(index, index[good], values[good])
    width = max(1, round(fps * 0.2))  # ~0.2 s moving average: kills keypoint jitter
    kernel = np.ones(width) / width
    padded = np.pad(values, (width, width), mode="edge")
    return np.convolve(padded, kernel, mode="same")[width:-width]


def count_series(series: list[float], fps: float) -> tuple[int, float]:
    """Full extended -> flexed -> extended cycles, with hysteresis. (reps, amplitude)."""
    smooth = _fill_and_smooth(series, fps)
    if smooth is None:
        return 0, 0.0
    lo, hi = float(np.percentile(smooth, 5)), float(np.percentile(smooth, 95))
    amplitude = hi - lo
    if amplitude < MIN_AMPLITUDE_DEGREES:
        return 0, amplitude
    flexed_below = lo + _HYSTERESIS * amplitude
    extended_above = hi - _HYSTERESIS * amplitude
    state: str | None = None
    reps = 0
    last_rep_frame = -(10**9)
    min_frames = MIN_REP_SECONDS * fps
    for i, angle in enumerate(smooth):
        if angle >= extended_above:
            if state == "flexed" and i - last_rep_frame >= min_frames:
                reps += 1
                last_rep_frame = i
            state = "extended"
        elif angle <= flexed_below and state is not None:
            state = "flexed"
    return reps, amplitude


def count_reps(frames: Sequence[Keypoints], fps: float = POSE_FPS) -> RepCount:
    """The best joint's count. Frames: COCO-17 keypoints per sampled frame."""
    if not frames or sum(kp is not None for kp in frames) < 0.5 * len(frames):
        return RepCount(None, None, 0.0, NOBODY)
    best: tuple[float, int, str] | None = None
    for joint in JOINTS:
        reps, amplitude = count_series(joint_series(frames, joint), fps)
        if reps and (best is None or amplitude > best[0]):
            best = (amplitude, reps, joint)
    if best is None:
        return RepCount(None, None, 0.0, NO_REPS)
    return RepCount(best[1], best[2], round(best[0], 1))


# --- the pose model --------------------------------------------------------------


class PoseUnavailableError(RuntimeError):
    """Pose estimation cannot run here. A fixed sentence."""


class PoseEstimator:
    """YOLO pose, one person per frame (the largest). Use as a context manager."""

    def __init__(self, weights: Path, device_preference: str = "auto") -> None:
        try:
            verify_weights(weights, POSE)
        except WeightsMissingError as error:
            raise PoseUnavailableError(str(error)) from error
        try:
            self._torch = importlib.import_module("torch")
            ultralytics = importlib.import_module("ultralytics")
        except ImportError as error:
            raise PoseUnavailableError(NO_POSE) from error
        cuda = device_preference != "cpu" and self._torch.cuda.is_available()
        self.device = "cuda:0" if cuda else "cpu"
        self._model: Any = ultralytics.YOLO(str(weights))
        self.peak_vram_bytes = 0

    def __enter__(self) -> PoseEstimator:
        return self

    def __exit__(self, *_: object) -> None:
        if self.device.startswith("cuda"):
            self.peak_vram_bytes = int(self._torch.cuda.max_memory_allocated())
        del self._model
        if self.device.startswith("cuda"):
            self._torch.cuda.empty_cache()

    def keypoints(self, frame: np.ndarray) -> Keypoints:
        results = self._model.predict(
            frame, verbose=False, device=self.device, half=self.device.startswith("cuda")
        )
        best: tuple[float, list[tuple[float, float, float]]] | None = None
        for result in results:
            if result.keypoints is None or result.boxes is None:
                continue
            for box, xy, conf in zip(
                result.boxes.xyxy.tolist(),
                result.keypoints.xy.tolist(),
                result.keypoints.conf.tolist(),
                strict=True,
            ):
                area = (box[2] - box[0]) * (box[3] - box[1])
                points = [(p[0], p[1], c) for p, c in zip(xy, conf, strict=True)]
                if best is None or area > best[0]:
                    best = (area, points)
        return best[1] if best else None


def segment_keypoints(
    proxy: Path, start: float, end: float, estimator: PoseEstimator, fps: float = POSE_FPS
) -> list[Keypoints]:
    """Keypoints of one hero segment at ``fps``, streamed: no frame is kept.

    Refuses a window longer than ``MAX_POSE_SECONDS``: the pose model never
    runs over a whole clip.
    """
    if end - start > MAX_POSE_SECONDS:
        raise ValueError(TOO_LONG)
    capture = cv2.VideoCapture(str(proxy))
    if not capture.isOpened():
        raise OSError("the proxy could not be opened for rep counting")
    try:
        capture.set(cv2.CAP_PROP_POS_MSEC, start * 1000)
        source_fps = capture.get(cv2.CAP_PROP_FPS) or 30.0
        step = max(1, round(source_fps / fps))
        out: list[Keypoints] = []
        index = 0
        while capture.get(cv2.CAP_PROP_POS_MSEC) / 1000 < end:
            ok, frame = capture.read()
            if not ok:
                break
            if index % step == 0:
                out.append(estimator.keypoints(frame))
            index += 1
    finally:
        capture.release()
    return out


__all__ = [
    "COUNTER_VERSION",
    "MAX_POSE_SECONDS",
    "NOBODY",
    "NO_POSE",
    "NO_REPS",
    "PRODUCT_BOUNDARY",
    "PoseEstimator",
    "PoseUnavailableError",
    "RepCount",
    "count_reps",
    "count_series",
    "joint_series",
    "segment_keypoints",
]
