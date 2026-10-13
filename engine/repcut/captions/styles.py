"""Caption styles are data: one TOML file each in ``styles/``, validated here.

Like a grading theme (amendment 012), adding a style is a new file and zero
engine changes. Every size is a fraction of the output's height, so one style
reads the same on a 9:16 reel and a 16:9 export.
"""

from __future__ import annotations

import re
import tomllib
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

STYLES_DIR = Path(__file__).with_name("styles")
DEFAULT_STYLE = "punch"
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
_HEX = r"^#[0-9a-fA-F]{6}$"


class CaptionStyleError(ValueError):
    """A style file that cannot be read or validated, named."""


class CaptionStyle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=_SLUG.pattern)
    name: str = Field(min_length=1, max_length=40)
    description: str = Field(max_length=200)
    font: str = Field(min_length=1, max_length=60, pattern=r"^[A-Za-z0-9 ]+$")
    bold: bool = False
    uppercase: bool = False
    size: float = Field(ge=0.02, le=0.12)
    colour: str = Field(pattern=_HEX)
    highlight: str = Field(pattern=_HEX)
    outline_colour: str = Field(pattern=_HEX)
    outline: float = Field(ge=0.0, le=0.2)
    shadow: float = Field(ge=0.0, le=0.2)
    position: Literal["center", "bottom", "lower-third"]
    mode: Literal["words", "line"]
    max_words: int = Field(ge=1, le=12)
    karaoke: bool = False
    pop_scale: int = Field(ge=100, le=140, default=100)
    beat_snap_ms: int = Field(ge=0, le=120, default=0)


def load_style_file(path: Path) -> CaptionStyle:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        style = CaptionStyle.model_validate(data)
    except (OSError, tomllib.TOMLDecodeError, ValidationError) as error:
        raise CaptionStyleError(f"caption style {path.stem!r} is not valid") from error
    if style.id != path.stem:
        raise CaptionStyleError(f"caption style {path.stem!r} names itself {style.id!r}")
    return style


@lru_cache(maxsize=1)
def _all(folder: str) -> tuple[CaptionStyle, ...]:
    order = {"punch": 0, "classic": 1, "minimal": 2}
    styles = [load_style_file(p) for p in sorted(Path(folder).glob("*.toml"))]
    return tuple(sorted(styles, key=lambda s: (order.get(s.id, 99), s.id)))


def styles(folder: Path = STYLES_DIR) -> tuple[CaptionStyle, ...]:
    return _all(str(folder))


def style(style_id: str, folder: Path = STYLES_DIR) -> CaptionStyle:
    for candidate in styles(folder):
        if candidate.id == style_id:
            return candidate
    raise CaptionStyleError("no caption style with that id exists")


__all__ = [
    "DEFAULT_STYLE",
    "STYLES_DIR",
    "CaptionStyle",
    "CaptionStyleError",
    "load_style_file",
    "style",
    "styles",
]
