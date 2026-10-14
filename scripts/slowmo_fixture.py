#!/usr/bin/env python3
"""Synthetic clips for the slow-motion gate and review: one gentle, one built to break RIFE.

Generated, never downloaded or committed (testing.md: no media in the repo).
Frames are drawn with numpy and encoded by FFmpeg from a raw pipe.

- ``gentle``: a slow pan over a smooth, non-repeating texture with a bar
  rising steadily - the motion interpolation handles well.
- ``barbell``: the guide's worst case, a fast pan (about 70 px per frame at
  1080 px wide) across a repeating pattern with a long, straight bar and
  plates - large displacement, a straight edge and many look-alike matches,
  the combination that makes flow estimation warp a bar. The confidence gate
  must refuse it.

Usage:
    python scripts/slowmo_fixture.py gentle|barbell OUT.mp4 [--size WxH] [--seconds S]
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

FPS = 30
KINDS = ("gentle", "barbell")
# Full-range R'G'B' to bt709 limited-range Y'CbCr, every property stated (amendment 013).
_TO_BT709_TV = (
    "format=gbrp,zscale=rin=pc:pin=bt709:tin=bt709:m=bt709:r=tv:p=bt709:t=bt709,format=yuv420p"
)


def _texture(height: int, width: int, blur: float, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    noise = rng.random((height, width, 3)).astype(np.float32)
    noise = cv2.GaussianBlur(noise, (0, 0), blur)
    noise = (noise - noise.min()) / max(1e-6, float(noise.max() - noise.min()))
    texture: np.ndarray = (40 + 170 * noise).astype(np.uint8)
    return texture


def frames(kind: str, width: int, height: int, count: int) -> list[np.ndarray]:
    """``count`` RGB24 frames of the named clip."""
    scale = width / 540
    if kind == "gentle":
        pan, rise, blur, grid = 4 * scale, 6 * scale, 6 * scale, False
    elif kind == "barbell":
        pan, rise, blur, grid = 70 * scale, 25 * scale, 1.5 * scale, True
    else:
        raise ValueError(f"unknown clip kind: {kind}")
    world = _texture(height, width + int(pan * count) + 16, blur, seed=7)
    bar_h, plate_w, plate_h = max(4, int(22 * scale)), int(46 * scale), int(160 * scale)
    out: list[np.ndarray] = []
    for index in range(count):
        x = int(index * pan)
        frame = world[:, x : x + width].copy()
        if grid:
            period, line = max(8, int(48 * scale)), max(1, int(3 * scale))
            frame[:, (np.arange(width) + x) % period < line] = 200
        y = int(height * 0.3 + index * rise) % max(1, height - plate_h - bar_h)
        y = max(plate_h, y)
        frame[y : y + bar_h, int(10 * scale) : width - int(10 * scale)] = (205, 205, 210)
        for left in (int(50 * scale), width - int(100 * scale)):
            top = y - (plate_h - bar_h) // 2
            frame[top : top + plate_h, left : left + plate_w] = (25, 25, 25)
        out.append(frame)
    return out


def write_clip(kind: str, destination: Path, width: int, height: int, seconds: float) -> Path:
    count = round(seconds * FPS)
    destination.parent.mkdir(parents=True, exist_ok=True)
    argv = [
        "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}", "-r", str(FPS),
        "-i", "pipe:0",
        "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=48000",
        "-t", f"{seconds:.3f}",
        "-vf", _TO_BT709_TV,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "16",
        "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
        "-color_range", "tv", "-c:a", "aac", "-shortest", destination.as_posix(),
    ]  # fmt: skip
    data = b"".join(frame.tobytes() for frame in frames(kind, width, height, count))
    subprocess.run(argv, input=data, check=True, capture_output=True, timeout=600)
    return destination


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("kind", choices=KINDS)
    parser.add_argument("out", type=Path)
    parser.add_argument("--size", default="1080x1920")
    parser.add_argument("--seconds", type=float, default=3.0)
    args = parser.parse_args(argv)
    width, height = (int(v) for v in args.size.split("x"))
    write_clip(args.kind, args.out, width, height, args.seconds)
    print(f"wrote {args.kind} clip, {width}x{height}, {args.seconds} s")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
