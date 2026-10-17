"""Where the subject is in a clip, over time: the input to subject-tracked reframing.

Two detectors, one interface:

- ``PersonDetector``: an Ultralytics YOLO person detector (AGPL-3.0, like
  Repcut), when torch, ultralytics and the pinned weights are all present.
  Detection only - boxes of people - never a pose: the pose model runs on hero
  segments only (``gym.reps``), never on a whole clip.
- ``MovementDetector``: no model at all. OpenCV's background subtractor on the
  downscaled proxy; the largest moving region is taken as the subject. The CPU
  fallback every GPU path must have (gpu-vram.md), and honest about what it
  is: it follows movement, so on a handheld clip (everything moves) it reports
  nothing rather than guessing.

A track is sampled at ``SAMPLE_FPS`` from the **proxy** (its CFR timeline makes
sample times exact), stored as numbers only - normalised centres and a
confidence - in the derived store. No frame is kept and nothing leaves the
machine (P4).
"""

from __future__ import annotations

import importlib
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import cv2
import numpy as np

from repcut.gym.weights import DETECT, WeightsMissingError, verify_weights
from repcut.logging import get_logger

logger = get_logger(__name__)

TRACK_VERSION = 1
SAMPLE_FPS = 5.0
_WIDTH = 320
# A moving region smaller than this share of the frame is noise; larger than
# the upper share means the camera itself moved.
_MIN_AREA = 0.002
_MAX_AREA = 0.6
_MIN_PERSON_CONFIDENCE = 0.35

Box = tuple[float, float, float, float, float]  # x0, y0, x1, y1 (0-1), confidence


class Detector(Protocol):
    name: str

    def boxes(self, frame: np.ndarray) -> list[Box]: ...


@dataclass(frozen=True, slots=True)
class SubjectSample:
    t: float
    cx: float | None
    cy: float | None
    confidence: float


@dataclass(frozen=True, slots=True)
class SubjectTrack:
    detector: str
    version: int
    samples: tuple[SubjectSample, ...]

    def to_json(self) -> str:
        return json.dumps(
            {
                "detector": self.detector,
                "version": self.version,
                "samples": [[s.t, s.cx, s.cy, s.confidence] for s in self.samples],
            }
        )

    @classmethod
    def from_json(cls, raw: str) -> SubjectTrack | None:
        """A stored track, or None if it is not one this version wrote."""
        try:
            data = json.loads(raw)
            samples = tuple(
                SubjectSample(
                    float(t),
                    None if cx is None else float(cx),
                    None if cy is None else float(cy),
                    float(c),
                )
                for t, cx, cy, c in data["samples"]
            )
            track = cls(str(data["detector"]), int(data["version"]), samples)
        except (ValueError, TypeError, KeyError):
            return None
        return track if track.version == TRACK_VERSION else None


class MovementDetector:
    """The largest moving region, by background subtraction. Stateful: one per clip."""

    name = "movement"

    def __init__(self) -> None:
        self._subtractor = cv2.createBackgroundSubtractorMOG2(
            history=50, varThreshold=25, detectShadows=False
        )
        self._kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))

    def boxes(self, frame: np.ndarray) -> list[Box]:
        mask = self._subtractor.apply(frame)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, self._kernel)
        mask = cv2.dilate(mask, self._kernel, iterations=2)
        h, w = mask.shape[:2]
        moving = float(np.count_nonzero(mask)) / (h * w)
        if moving > _MAX_AREA:
            return []  # the camera moved: everything is "moving", nothing is the subject
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        out: list[Box] = []
        for contour in contours:
            x, y, bw, bh = cv2.boundingRect(contour)
            area = (bw * bh) / (w * h)
            if area < _MIN_AREA:
                continue
            out.append((x / w, y / h, (x + bw) / w, (y + bh) / h, min(1.0, area * 10)))
        return out


class PersonDetector:
    """People, by a pinned YOLO detection model. Construct through ``open_person_detector``."""

    name = "person"

    def __init__(self, model: Any, device: str) -> None:  # noqa: ANN401 - ultralytics' own type
        self._model = model
        self._device = device

    def boxes(self, frame: np.ndarray) -> list[Box]:
        results = self._model.predict(
            frame, classes=[0], conf=_MIN_PERSON_CONFIDENCE, verbose=False, device=self._device
        )
        h, w = frame.shape[:2]
        out: list[Box] = []
        for result in results:
            for xyxy, conf in zip(
                result.boxes.xyxy.tolist(), result.boxes.conf.tolist(), strict=True
            ):
                x0, y0, x1, y1 = xyxy
                out.append((x0 / w, y0 / h, x1 / w, y1 / h, float(conf)))
        return out


class DetectorUnavailableError(RuntimeError):
    """The person detector cannot run here. A fixed sentence."""


NO_ULTRALYTICS = (
    "subject tracking by person detection needs PyTorch and ultralytics (make setup-gpu); "
    "following movement instead"
)


def open_person_detector(weights: Path, device_preference: str = "auto") -> PersonDetector:
    """The YOLO detector, after the weights' pinned digest is checked. Raises named."""
    try:
        verify_weights(weights, DETECT)
    except WeightsMissingError as error:
        raise DetectorUnavailableError(str(error)) from error
    try:
        torch = importlib.import_module("torch")
        ultralytics = importlib.import_module("ultralytics")
    except ImportError as error:
        raise DetectorUnavailableError(NO_ULTRALYTICS) from error
    device = "cuda:0" if device_preference != "cpu" and torch.cuda.is_available() else "cpu"
    return PersonDetector(ultralytics.YOLO(str(weights)), device)


def _pick(boxes: Sequence[Box], previous: tuple[float, float] | None) -> Box | None:
    """The subject: the biggest region, or the one nearest where the subject just was."""
    if not boxes:
        return None

    def size(b: Box) -> float:
        return (b[2] - b[0]) * (b[3] - b[1])

    if previous is None:
        return max(boxes, key=size)

    def cost(b: Box) -> float:
        cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
        return math.hypot(cx - previous[0], cy - previous[1]) - 0.5 * size(b)

    return min(boxes, key=cost)


def track(proxy: Path, detector: Detector, sample_fps: float = SAMPLE_FPS) -> SubjectTrack:
    """Sample the proxy at ``sample_fps`` and find the subject in each sample. Blocking."""
    capture = cv2.VideoCapture(str(proxy))
    if not capture.isOpened():
        raise OSError("the proxy could not be opened for subject tracking")
    try:
        fps = capture.get(cv2.CAP_PROP_FPS) or 30.0
        step = max(1, round(fps / sample_fps))
        samples: list[SubjectSample] = []
        previous: tuple[float, float] | None = None
        index = 0
        while True:
            if not capture.grab():
                break
            # The movement detector learns the background from every frame it is
            # shown, so it sees every frame; only sampled frames are reported.
            sampled = index % step == 0
            if sampled or isinstance(detector, MovementDetector):
                ok, frame = capture.retrieve()
                if not ok:
                    break
                scale = _WIDTH / frame.shape[1]
                small = cv2.resize(frame, (_WIDTH, max(1, round(frame.shape[0] * scale))))
                boxes = detector.boxes(small)
                if sampled:
                    chosen = _pick(boxes, previous)
                    if chosen is None:
                        samples.append(SubjectSample(round(index / fps, 4), None, None, 0.0))
                    else:
                        cx, cy = (chosen[0] + chosen[2]) / 2, (chosen[1] + chosen[3]) / 2
                        previous = (cx, cy)
                        samples.append(
                            SubjectSample(round(index / fps, 4), cx, cy, round(chosen[4], 3))
                        )
            index += 1
    finally:
        capture.release()
    return SubjectTrack(detector.name, TRACK_VERSION, tuple(samples))


__all__ = [
    "NO_ULTRALYTICS",
    "SAMPLE_FPS",
    "TRACK_VERSION",
    "Detector",
    "DetectorUnavailableError",
    "MovementDetector",
    "PersonDetector",
    "SubjectSample",
    "SubjectTrack",
    "open_person_detector",
    "track",
]
