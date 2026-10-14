"""One slow-motion shot: captured frames in, a verdict and (if accepted) 2x frames out.

The export calls this for every shot the plan marked half speed. The order is
chosen so a shot that will be refused costs as little as possible:

1. **Warp check first** (``confidence.warp_scores``): a dozen leave-one-out
   interpolations against frames the camera really recorded. If the shot
   already fails here, nothing else is interpolated - the fast barbell pan is
   refused for the price of twelve frames, not the whole shot.
2. **Interpolate the shot**: one midpoint per pair of captured frames, written
   straight to a temporary file in display order (captured, invented,
   captured, ...), so a 1080p shot never sits in memory whole.
3. **Continuity check** on the frames actually produced, then the verdict.
   Accepted: the file is moved into place. Refused: it is deleted, and the
   export renders the shot at normal speed instead (``export/render.py``).

Frames are RGB24, ``height x width x 3``, read from and written to raw files.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from repcut.slowmo import confidence
from repcut.slowmo.confidence import Verdict
from repcut.slowmo.rife import Interpolator


@dataclass(frozen=True, slots=True)
class FrameShape:
    width: int
    height: int

    @property
    def bytes_per_frame(self) -> int:
        return self.width * self.height * 3


def read_frames(path: Path, shape: FrameShape) -> np.ndarray:
    """A raw RGB24 file as a read-only (frames, height, width, 3) memory map."""
    count = path.stat().st_size // shape.bytes_per_frame
    if count == 0:
        return np.zeros((0, shape.height, shape.width, 3), dtype=np.uint8)
    frames: np.ndarray = np.memmap(
        path, dtype=np.uint8, mode="r", shape=(count, shape.height, shape.width, 3)
    )
    return frames


def output_frames_needed(frames_out: int) -> int:
    """Captured frames a half-speed shot of ``frames_out`` frames is made from."""
    return frames_out // 2 + 1


ProgressFn = Callable[[float], None]


def interpolate_shot(
    captured: np.ndarray,
    interpolator: Interpolator,
    destination: Path,
    *,
    frames_out: int,
    on_progress: ProgressFn | None = None,
) -> Verdict:
    """Judge and, if accepted, write the half-speed shot to ``destination``.

    ``captured`` must hold at least ``output_frames_needed(frames_out)`` frames;
    fewer is refused with ``TOO_SHORT`` (the shot plays at normal speed).
    Exactly ``frames_out`` frames are written on acceptance.
    """
    report = on_progress or (lambda _fraction: None)
    needed = output_frames_needed(frames_out)
    if frames_out < 2 or len(captured) < max(3, needed):
        return confidence.judge([], [])
    captured = captured[:needed]

    warp = confidence.warp_scores(captured, interpolator)
    report(0.2)
    # Continuity is not measured yet; 1.0 lets the warp score decide alone.
    early = confidence.judge(warp, [1.0])
    if not early.accepted:
        return early

    partial = destination.with_name(destination.name + ".partial")
    destination.parent.mkdir(parents=True, exist_ok=True)
    continuity: list[float] = []
    written = 0
    pairs = len(captured) - 1
    try:
        with partial.open("wb") as handle:
            for index in range(pairs):
                first, second = captured[index], captured[index + 1]
                if written < frames_out:
                    handle.write(np.ascontiguousarray(first).tobytes())
                    written += 1
                middle = interpolator.midpoint(first, second)
                continuity.extend(confidence.continuity_scores([first, second], [middle]))
                if written < frames_out:
                    handle.write(middle.tobytes())
                    written += 1
                report(0.2 + 0.8 * (index + 1) / pairs)
            if written < frames_out:
                handle.write(np.ascontiguousarray(captured[-1]).tobytes())
                written += 1
        verdict = confidence.judge(warp, continuity)
        if verdict.accepted and written == frames_out:
            partial.replace(destination)
        return verdict
    finally:
        partial.unlink(missing_ok=True)


__all__ = ["FrameShape", "interpolate_shot", "output_frames_needed", "read_frames"]
