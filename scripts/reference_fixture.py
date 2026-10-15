#!/usr/bin/env python3
"""Generated clips for the reference gate: no footage, nothing downloaded.

- ``natural``: smooth, slowly drifting colour fields across the whole luma
  range, with muted chroma in every hue - what an ungraded clip's colour
  distribution looks like, for the look round trip.
- ``cuts``: hard cuts every ``shot_seconds`` between unrelated fields, for the
  pacing criterion (a fast-cut and a slow, cinematic reference).
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

import numpy as np
from slowmo_fixture import _TO_BT709_TV

FPS = 30


def _field(height: int, width: int, seed: int) -> np.ndarray:
    """A smooth RGB field in [0, 1]: luma spread over the range, muted colour."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:height, 0:width] / max(height, width)
    luma = np.zeros((height, width))
    for _ in range(6):
        fx, fy, phase = rng.uniform(0.5, 3.0), rng.uniform(0.5, 3.0), rng.uniform(0, 6.3)
        luma += np.sin(2 * np.pi * (fx * xx + fy * yy) + phase)
    luma = (luma - luma.min()) / (np.ptp(luma) + 1e-9)
    luma = 0.03 + 0.94 * luma
    hue = 2 * np.pi * (0.5 * xx + 0.3 * yy + rng.uniform(0, 1))
    chroma = 0.06 * (4 * luma * (1 - luma) + 0.2)
    a = np.array([1.0, -0.4, -0.6])
    a = a - a @ np.array([0.2126, 0.7152, 0.0722])
    b = np.array([-0.2, 0.55, -1.0])
    b = b - b @ np.array([0.2126, 0.7152, 0.0722])
    rgb = (
        luma[..., None]
        + (chroma * np.cos(hue))[..., None] * a
        + (chroma * np.sin(hue))[..., None] * b
    )
    return np.clip(rgb, 0.0, 1.0)


def natural_frames(width: int, height: int, seconds: float, seed: int = 1) -> list[np.ndarray]:
    base = _field(height, width, seed)
    count = round(seconds * FPS)
    out = []
    for i in range(count):
        shift = int(i * 2) % width
        out.append((np.roll(base, shift, axis=1) * 255).round().astype(np.uint8))
    return out


def cut_frames(
    width: int, height: int, seconds: float, shot_seconds: float, seed: int = 3
) -> list[np.ndarray]:
    count = round(seconds * FPS)
    per = max(round(shot_seconds * FPS), 1)
    fields: dict[int, np.ndarray] = {}
    out = []
    for i in range(count):
        shot = i // per
        if shot not in fields:
            fields[shot] = _field(height, width, seed * 1000 + shot)
        shift = (i % per) * 2
        out.append((np.roll(fields[shot], shift, axis=1) * 255).round().astype(np.uint8))
    return out


def write(frames: list[np.ndarray], destination: Path) -> Path:
    height, width = frames[0].shape[:2]
    seconds = len(frames) / FPS
    destination.parent.mkdir(parents=True, exist_ok=True)
    argv = [
        "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}", "-r", str(FPS),
        "-i", "pipe:0",
        "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=48000",
        "-t", f"{seconds:.3f}",
        "-vf", _TO_BT709_TV,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "14",
        "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
        "-color_range", "tv", "-c:a", "aac", "-shortest", destination.as_posix(),
    ]  # fmt: skip
    data = b"".join(frame.tobytes() for frame in frames)
    subprocess.run(argv, input=data, check=True, capture_output=True, timeout=600)
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("kind", choices=("natural", "cuts"))
    parser.add_argument("out", type=Path)
    parser.add_argument("--size", default="360x640")
    parser.add_argument("--seconds", type=float, default=8.0)
    parser.add_argument("--shot-seconds", type=float, default=2.0)
    args = parser.parse_args()
    width, height = (int(v) for v in args.size.split("x"))
    frames = (
        natural_frames(width, height, args.seconds)
        if args.kind == "natural"
        else cut_frames(width, height, args.seconds, args.shot_seconds)
    )
    write(frames, args.out)
    print(f"wrote a {args.kind} clip, {width}x{height}, {args.seconds} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
