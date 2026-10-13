"""What makes an exported file postable, checked on the file (never on the argv).

``platform_problems`` returns every way the file falls short, as sentences; an
empty list is a pass. The same check runs at the end of every export and in the
gate: H.264 yuv420p video at the preset's size, bt709 tags, AAC-LC at 48 kHz,
the ``moov`` atom before ``mdat`` (faststart: the player can start before the
whole file has arrived), and the duration within 5 % of the edit's.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DURATION_TOLERANCE = 0.05


def top_level_atoms(path: Path, limit: int = 64) -> list[str]:
    """The MP4's top-level box types, in file order. Reads headers only."""
    names: list[str] = []
    size_total = path.stat().st_size
    with path.open("rb") as handle:
        offset = 0
        while offset < size_total and len(names) < limit:
            handle.seek(offset)
            header = handle.read(8)
            if len(header) < 8:
                break
            size, kind = struct.unpack(">I4s", header)
            if size == 1:
                size = struct.unpack(">Q", handle.read(8))[0]
            elif size == 0:
                size = size_total - offset
            names.append(kind.decode("latin-1"))
            if size < 8:
                break
            offset += size
    return names


def is_faststart(path: Path) -> bool:
    atoms = top_level_atoms(path)
    return "moov" in atoms and "mdat" in atoms and atoms.index("moov") < atoms.index("mdat")


@dataclass(frozen=True, slots=True)
class Expected:
    width: int
    height: int
    duration_seconds: float
    sample_rate: int = 48000


def platform_problems(probe_json: str, path: Path, expected: Expected) -> list[str]:
    problems: list[str] = []
    data: dict[str, Any] = json.loads(probe_json)
    streams = data.get("streams", [])
    video = [s for s in streams if s.get("codec_type") == "video"]
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    if len(video) != 1:
        problems.append(f"expected one video stream, found {len(video)}")
    else:
        v = video[0]
        if v.get("codec_name") != "h264":
            problems.append(f"video codec is {v.get('codec_name')}, not h264")
        if v.get("pix_fmt") != "yuv420p":
            problems.append(f"pixel format is {v.get('pix_fmt')}, not yuv420p")
        if (v.get("width"), v.get("height")) != (expected.width, expected.height):
            size = f"{v.get('width')}x{v.get('height')}"
            problems.append(f"size is {size}, not {expected.width}x{expected.height}")
        for key in ("color_space", "color_primaries", "color_transfer"):
            if v.get(key) != "bt709":
                problems.append(f"{key} is {v.get(key)}, not bt709")
        if v.get("color_range") != "tv":
            problems.append(f"colour range is {v.get('color_range')}, not tv")
    if len(audio) != 1:
        problems.append(f"expected one audio stream, found {len(audio)}")
    else:
        a = audio[0]
        if a.get("codec_name") != "aac":
            problems.append(f"audio codec is {a.get('codec_name')}, not aac")
        if int(a.get("sample_rate", 0)) != expected.sample_rate:
            problems.append(
                f"audio sample rate is {a.get('sample_rate')}, not {expected.sample_rate}"
            )
    duration = float(data.get("format", {}).get("duration", 0.0) or 0.0)
    target = expected.duration_seconds
    if target > 0 and abs(duration - target) > DURATION_TOLERANCE * target:
        problems.append(f"duration is {duration:.2f} s, not within 5 % of {target:.2f} s")
    if not is_faststart(path):
        problems.append("the index (moov) is not at the start of the file (faststart)")
    return problems


__all__ = ["DURATION_TOLERANCE", "Expected", "is_faststart", "platform_problems", "top_level_atoms"]
