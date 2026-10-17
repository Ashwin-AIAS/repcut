"""A clip's subject track, computed once and kept in the derived store.

The person detector is used when it can run; otherwise movement. Each is
stored under its own name, so installing the detector later computes a person
track rather than reusing the movement one.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from repcut.export.subjects import (
    TRACK_VERSION,
    Detector,
    DetectorUnavailableError,
    MovementDetector,
    SubjectTrack,
    open_person_detector,
    track,
)
from repcut.gym.weights import DETECT
from repcut.logging import get_logger
from repcut.media.store import absolute, derived_path

logger = get_logger(__name__)

TRACK_KIND = "subject_track"


def track_path(data_dir: Path, sha256: str, detector: str) -> Path:
    return absolute(data_dir, derived_path(sha256, TRACK_KIND, TRACK_VERSION, f"{detector}.json"))


def _detector(data_dir: Path, device_preference: str) -> Detector:
    try:
        return open_person_detector(absolute(data_dir, DETECT.stored_path), device_preference)
    except DetectorUnavailableError as error:
        # Named: no torch, no ultralytics or no weights. Movement is the fallback.
        logger.info("subject_tracking_fallback", reason=str(error))
        return MovementDetector()


def _cached(path: Path) -> SubjectTrack | None:
    if not path.is_file():
        return None
    return SubjectTrack.from_json(path.read_text(encoding="utf-8"))


def ensure_track_sync(
    data_dir: Path, sha256: str, proxy: Path, device_preference: str = "auto"
) -> SubjectTrack:
    detector = _detector(data_dir, device_preference)
    path = track_path(data_dir, sha256, detector.name)
    found = _cached(path)
    if found is not None:
        return found
    result = track(proxy, detector)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".part")
    partial.write_text(result.to_json(), encoding="utf-8")
    partial.replace(path)
    return result


async def ensure_track(
    data_dir: Path, sha256: str, proxy: Path, device_preference: str = "auto"
) -> SubjectTrack:
    """Idempotent: the stored track if there is one, else measured now (off the event loop)."""
    return await asyncio.to_thread(ensure_track_sync, data_dir, sha256, proxy, device_preference)


__all__ = ["TRACK_KIND", "ensure_track", "ensure_track_sync", "track_path"]
