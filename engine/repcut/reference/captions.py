"""A reference clip's captions: are there any, where, how big, how they move -> a caption style.

Two sources, local first:

1. **Measured locally** on every scene's sampled frame (Prompt 03's frames,
   already on disk): caption text is a run of rows dense in strong horizontal
   edges between very bright and very dark pixels (white type, dark outline),
   far denser than the picture around it. That gives presence, vertical
   position and the height of the type. It works with no key and no network.
2. **Gemini**, when a key is set: the same sampled frames, one request per
   frame, at most ``MAX_GEMINI_FRAMES`` per reference, through the shared
   rate limiter. It adds what one still cannot measure locally (letter case,
   an animation guess) and overrides the local position and size when it
   answers. Only those frames are sent (P4), as for any scene.

The answer is the closest of Repcut's own caption styles plus the
differences that remain, stated; the style stays a default the person can
change (P2).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel

from repcut.captions.styles import CaptionStyle, styles

CAPTIONS_VERSION = 1
MAX_GEMINI_FRAMES = 4

Position = Literal["top", "center", "lower-third", "bottom"]
SizeClass = Literal["small", "medium", "large"]

# Local detector thresholds (on 0-255 luma). Text is bright on dark or dark on
# bright, with an edge between: |dx| above EDGE on a pixel that is itself
# extreme (above BRIGHT or below DARK).
EDGE = 60
BRIGHT = 200
DARK = 55
ROW_DENSITY = 0.06
ROW_RATIO = 4.0
MIN_TEXT_ROWS = 0.012
# Text height (fraction of frame height) between the size classes.
SIZE_SMALL_BELOW = 0.04
SIZE_LARGE_ABOVE = 0.075
SIZE_OF: dict[SizeClass, float] = {"small": 0.04, "medium": 0.06, "large": 0.085}


@dataclass(frozen=True, slots=True)
class FrameCaption:
    present: bool
    centre: float | None = None
    height: float | None = None


def detect_text_band(frame: NDArray[np.uint8]) -> FrameCaption:
    """Caption-like text in one RGB frame: where its rows are, as fractions of the height."""
    rgb = np.asarray(frame, dtype=np.float64)
    if rgb.ndim != 3 or rgb.shape[0] < 32 or rgb.shape[1] < 32:
        return FrameCaption(False)
    y = rgb @ np.array([0.2126, 0.7152, 0.0722])
    dx = np.abs(np.diff(y, axis=1))
    extreme = (y[:, 1:] > BRIGHT) | (y[:, 1:] < DARK) | (y[:, :-1] > BRIGHT) | (y[:, :-1] < DARK)
    strong = (dx > EDGE) & extreme
    rows = strong.mean(axis=1)
    baseline = float(np.median(rows)) + 1e-3
    text_rows = (rows > ROW_DENSITY) & (rows > ROW_RATIO * baseline)
    # Keep the largest run of near-contiguous text rows (gaps of a few rows close).
    height = len(rows)
    indices = np.flatnonzero(text_rows)
    if len(indices) < max(int(MIN_TEXT_ROWS * height), 2):
        return FrameCaption(False)
    gap = max(int(0.01 * height), 2)
    runs: list[list[int]] = [[int(indices[0])]]
    for index in indices[1:]:
        if index - runs[-1][-1] <= gap:
            runs[-1].append(int(index))
        else:
            runs.append([int(index)])
    best = max(runs, key=len)
    if len(best) < max(int(MIN_TEXT_ROWS * height), 2):
        return FrameCaption(False)
    top, bottom = best[0], best[-1] + 1
    return FrameCaption(True, centre=(top + bottom) / 2 / height, height=(bottom - top) / height)


def position_of(centre: float) -> Position:
    if centre < 0.3:
        return "top"
    if centre < 0.62:
        return "center"
    if centre < 0.82:
        return "lower-third"
    return "bottom"


def size_of(height: float) -> SizeClass:
    if height < SIZE_SMALL_BELOW:
        return "small"
    if height > SIZE_LARGE_ABOVE:
        return "large"
    return "medium"


class GeminiCaptionAnswer(BaseModel):
    """One frame's caption description. Untrusted until validated; every field optional."""

    captions_present: str | None = None
    position: str | None = None
    size: str | None = None
    letter_case: str | None = None
    animation_guess: str | None = None


CAPTION_PROMPT = (
    "You are looking at one still frame sampled from a short social video. "
    "Describe only the burned-in caption text (subtitles or on-screen words "
    "that transcribe speech or narrate), not logos, signs or text that is part "
    "of the scene. If you are not sure about a field, use null.\n"
    "\n"
    "Return a JSON object with exactly these fields:\n"
    '- captions_present: "yes" or "no"\n'
    '- position: one of "top", "center", "lower-third", "bottom"\n'
    '- size: one of "small", "medium", "large" relative to the frame height\n'
    '- letter_case: one of "upper", "mixed"\n'
    '- animation_guess: one of "word-by-word", "karaoke", "full-line", "static"\n'
    "\n"
    "Respond with the JSON object only."
)

CAPTION_RESPONSE_SCHEMA: dict[str, object] = {
    "type": "OBJECT",
    "properties": {
        name: {"type": "STRING", "nullable": True}
        for name in ("captions_present", "position", "size", "letter_case", "animation_guess")
    },
}

_POSITIONS = {"top", "center", "lower-third", "bottom"}
_SIZES = {"small", "medium", "large"}


@dataclass(frozen=True, slots=True)
class CaptionProfile:
    present: bool
    position: Position | None
    size: SizeClass | None
    uppercase: bool | None
    animation: str | None
    frames_seen: int
    frames_with_text: int
    source: Literal["local", "local+gemini"]

    def as_json(self) -> dict[str, object]:
        return {
            "present": self.present,
            "position": self.position,
            "size": self.size,
            "uppercase": self.uppercase,
            "animation": self.animation,
            "frames_seen": self.frames_seen,
            "frames_with_text": self.frames_with_text,
            "source": self.source,
            "version": CAPTIONS_VERSION,
        }

    @classmethod
    def from_json(cls, data: dict[str, object]) -> CaptionProfile:
        return cls(
            present=bool(data["present"]),
            position=data.get("position"),  # type: ignore[arg-type]
            size=data.get("size"),  # type: ignore[arg-type]
            uppercase=data.get("uppercase"),  # type: ignore[arg-type]
            animation=data.get("animation"),  # type: ignore[arg-type]
            frames_seen=int(data["frames_seen"]),  # type: ignore[call-overload]
            frames_with_text=int(data["frames_with_text"]),  # type: ignore[call-overload]
            source=data.get("source", "local"),  # type: ignore[arg-type]
        )


def _vote(values: Sequence[str | None], allowed: set[str]) -> str | None:
    kept = [v.strip().lower() for v in values if v and v.strip().lower() in allowed]
    if not kept:
        return None
    return sorted(Counter(kept).items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


def caption_profile(
    frames: Sequence[NDArray[np.uint8]], answers: Sequence[GeminiCaptionAnswer] = ()
) -> CaptionProfile:
    """Local measurement of every frame, refined by Gemini's answers when there are any."""
    seen = [detect_text_band(frame) for frame in frames]
    hits = [f for f in seen if f.present]
    present = len(hits) * 2 >= max(len(seen), 1) and bool(hits)
    position: str | None = None
    size: str | None = None
    if hits:
        position = position_of(float(np.median([h.centre for h in hits if h.centre is not None])))
        size = size_of(float(np.median([h.height for h in hits if h.height is not None])))
    uppercase: bool | None = None
    animation: str | None = None
    source: Literal["local", "local+gemini"] = "local"
    if answers:
        source = "local+gemini"
        said = _vote([a.captions_present for a in answers], {"yes", "no"})
        if said is not None:
            present = said == "yes"
        position = _vote([a.position for a in answers], _POSITIONS) or position
        size = _vote([a.size for a in answers], _SIZES) or size
        case = _vote([a.letter_case for a in answers], {"upper", "mixed"})
        uppercase = None if case is None else case == "upper"
        animation = _vote(
            [a.animation_guess for a in answers], {"word-by-word", "karaoke", "full-line", "static"}
        )
    return CaptionProfile(
        present=present,
        position=position if present else None,  # type: ignore[arg-type]
        size=size if present else None,  # type: ignore[arg-type]
        uppercase=uppercase if present else None,
        animation=animation if present else None,
        frames_seen=len(seen),
        frames_with_text=len(hits),
        source=source,
    )


@dataclass(frozen=True, slots=True)
class CaptionMatch:
    style_id: str
    # The reference's own captions are off: matching turns Repcut's off too.
    enabled: bool
    # What the chosen style still does differently, one short sentence each.
    differences: tuple[str, ...] = field(default=())

    def as_json(self) -> dict[str, object]:
        return {
            "style_id": self.style_id,
            "enabled": self.enabled,
            "differences": list(self.differences),
        }


_STYLE_POSITION: dict[str, Position] = {
    "center": "center",
    "lower-third": "lower-third",
    "bottom": "bottom",
}


def _distance(style: CaptionStyle, p: CaptionProfile) -> float:
    score = 0.0
    if p.position is not None:
        wanted = "center" if p.position == "top" else p.position
        score += 0.0 if _STYLE_POSITION[style.position] == wanted else 2.0
    if p.size is not None:
        score += abs(style.size - SIZE_OF[p.size]) * 40.0
    if p.uppercase is not None:
        score += 0.0 if style.uppercase == p.uppercase else 1.0
    if p.animation is not None:
        words = p.animation in {"word-by-word", "karaoke"}
        score += 0.0 if (style.mode == "words") == words else 1.0
        if p.animation == "karaoke":
            score += 0.0 if style.karaoke else 0.5
    return score


def closest_style(p: CaptionProfile, choices: Sequence[CaptionStyle] | None = None) -> CaptionMatch:
    options = list(choices or styles())
    best = min(options, key=lambda s: (_distance(s, p), s.id))
    if not p.present:
        return CaptionMatch(
            style_id=best.id, enabled=False, differences=("the reference has no captions",)
        )
    notes: list[str] = []
    if p.position == "top":
        notes.append(
            "the reference's captions sit at the top; Repcut's nearest position is the centre"
        )
    elif p.position is not None and _STYLE_POSITION[best.position] != p.position:
        notes.append(f"the reference's captions sit {p.position}; this style sits {best.position}")
    if p.size is not None and abs(best.size - SIZE_OF[p.size]) > 0.015:
        notes.append(f"the reference's captions are {p.size}; this style's are a different size")
    return CaptionMatch(style_id=best.id, enabled=True, differences=tuple(notes))


__all__ = [
    "CAPTIONS_VERSION",
    "CAPTION_PROMPT",
    "CAPTION_RESPONSE_SCHEMA",
    "MAX_GEMINI_FRAMES",
    "CaptionMatch",
    "CaptionProfile",
    "FrameCaption",
    "GeminiCaptionAnswer",
    "caption_profile",
    "closest_style",
    "detect_text_band",
]
