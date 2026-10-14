"""v1 reframing: a centre crop to the preset's aspect, nudged per scene by hand.

``offset`` runs -1 (crop hard left / top) to +1 (hard right / bottom) across the
picture the crop leaves out; 0 is the centre. The crop never leaves the frame
and never pads: nothing is invented at the edges (P1). Subject-tracked
reframing is a later prompt's.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Crop:
    width: int
    height: int
    x: int
    y: int

    def filter(self) -> str:
        return f"crop={self.width}:{self.height}:{self.x}:{self.y}"


def _even(value: float) -> int:
    return max(2, int(value) // 2 * 2)


def crop_for(width: int, height: int, aspect: float, offset: float = 0.0) -> Crop:
    """The largest ``aspect`` rectangle inside ``width``x``height``, placed by ``offset``."""
    if width <= 0 or height <= 0 or aspect <= 0:
        raise ValueError("crop needs positive dimensions")
    offset = min(1.0, max(-1.0, offset))
    if width / height > aspect:
        crop_w, crop_h = _even(height * aspect), _even(height)
    else:
        crop_w, crop_h = _even(width), _even(width / aspect)
    crop_w, crop_h = min(crop_w, width // 2 * 2), min(crop_h, height // 2 * 2)
    slack_x, slack_y = width - crop_w, height - crop_h
    # Even offsets: a 4:2:0 crop that starts on an odd pixel shifts chroma by half a sample.
    x = min(round(slack_x * (offset + 1) / 4) * 2, slack_x // 2 * 2)
    y = min(round(slack_y * (offset + 1) / 4) * 2, slack_y // 2 * 2)
    return Crop(crop_w, crop_h, x, y)


__all__ = ["Crop", "crop_for"]
