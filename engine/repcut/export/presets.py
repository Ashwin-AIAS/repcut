"""Export presets and encode settings, read from ``presets.toml``."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

PRESETS_FILE = Path(__file__).with_name("presets.toml")


@dataclass(frozen=True, slots=True)
class Preset:
    id: str
    label: str
    width: int
    height: int

    @property
    def aspect(self) -> float:
        return self.width / self.height


@dataclass(frozen=True, slots=True)
class Encode:
    preset: str
    crf: int
    profile: str
    level: str
    keyframe_seconds: int
    audio_bitrate: str
    audio_sample_rate: int


@dataclass(frozen=True, slots=True)
class ExportConfig:
    default: str
    fps: int
    encode: Encode
    presets: tuple[Preset, ...]

    def preset(self, preset_id: str) -> Preset:
        for preset in self.presets:
            if preset.id == preset_id:
                return preset
        raise KeyError("no export preset with that id exists")


@lru_cache(maxsize=1)
def config() -> ExportConfig:
    data = tomllib.loads(PRESETS_FILE.read_text(encoding="utf-8"))
    presets = tuple(
        Preset(
            id=str(p["id"]), label=str(p["label"]), width=int(p["width"]), height=int(p["height"])
        )
        for p in data["preset"]
    )
    for preset in presets:
        if preset.width % 2 or preset.height % 2:
            raise ValueError("export presets need even dimensions (yuv420p)")
    encode = data["encode"]
    return ExportConfig(
        default=str(data["default"]),
        fps=int(data["fps"]),
        encode=Encode(
            preset=str(encode["preset"]),
            crf=int(encode["crf"]),
            profile=str(encode["profile"]),
            level=str(encode["level"]),
            keyframe_seconds=int(encode["keyframe_seconds"]),
            audio_bitrate=str(encode["audio_bitrate"]),
            audio_sample_rate=int(encode["audio_sample_rate"]),
        ),
        presets=presets,
    )


__all__ = ["Encode", "ExportConfig", "Preset", "config"]
