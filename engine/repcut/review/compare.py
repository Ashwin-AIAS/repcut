"""Before/after: the original proxy and a scene's graded preview, frame for frame.

Both sides are files that already exist - the clip's CFR proxy and the scene's
graded preview, rendered from that proxy (``grading.service.ensure_previews``)
- so scrubbing is instant: no render waits on a drag (the guide's constraint).

**Frame accuracy.** The preview is cut from the proxy with an input seek to
the scene's start, written with three decimals. FFmpeg keeps the first proxy
frame whose time is at or after that point, so the preview's frame ``i`` is
the proxy's frame ``first_frame + i``. Both players are pointed at the middle
of a frame (``FRAME_CENTRE``), never its edge, so rounding in a browser's seek
cannot land either side on a neighbour. ``side_seconds`` is the one mapping
the UI and the gate both use (``ui/lib/compare.ts`` mirrors it).
"""

import math
from dataclasses import dataclass
from typing import Final, Literal

from repcut.media.artifacts import PROXY_RECIPE

Side = Literal["before", "after"]

# Seek to the middle of a frame: half a frame of slack on either side.
FRAME_CENTRE: Final = 0.5
_EPSILON: Final = 1e-6


def seek_point(start_seconds: float) -> float:
    """The scene start as the preview's render command wrote it (three decimals)."""
    return float(f"{start_seconds:.3f}")


def first_frame(start_seconds: float, fps: int = PROXY_RECIPE.fps) -> int:
    """The proxy frame a preview starting at ``start_seconds`` opens with."""
    return math.ceil(seek_point(start_seconds) * fps - _EPSILON)


@dataclass(frozen=True, slots=True)
class Comparison:
    """How one scene's two sides line up. Every time is in that side's own file."""

    fps: int
    start_seconds: float
    end_seconds: float
    offset_frames: int

    @property
    def before_offset_seconds(self) -> float:
        return self.offset_frames / self.fps

    @property
    def frame_count(self) -> int:
        """Frames in the preview: proxy frames from the seek point for the written duration."""
        written = float(f"{self.end_seconds - self.start_seconds:.3f}")
        last = math.ceil((seek_point(self.start_seconds) + written) * self.fps - _EPSILON)
        return max(1, last - self.offset_frames)

    def side_seconds(self, side: Side, frame: int) -> float:
        """Where ``side``'s player seeks to show preview frame ``frame``."""
        if not 0 <= frame < self.frame_count:
            raise ValueError("that frame is outside this scene")
        base = 0 if side == "after" else self.offset_frames
        return round((base + frame + FRAME_CENTRE) / self.fps, 6)


def comparison(start_seconds: float, end_seconds: float, fps: int = PROXY_RECIPE.fps) -> Comparison:
    if not 0 <= start_seconds < end_seconds:
        raise ValueError("a scene needs 0 <= start < end")
    return Comparison(
        fps=fps,
        start_seconds=start_seconds,
        end_seconds=end_seconds,
        offset_frames=first_frame(start_seconds, fps),
    )


__all__ = ["FRAME_CENTRE", "Comparison", "Side", "comparison", "first_frame", "seek_point"]
