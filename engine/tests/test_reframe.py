"""Subject-tracked reframing: centred, smooth, and honest when the subject is lost."""

from __future__ import annotations

import subprocess
from pathlib import Path

import cv2
import numpy as np
import pytest

from repcut.export.reframe import LOST_SECONDS, MAX_PAN_PER_SECOND, crop_path
from repcut.export.subjects import MovementDetector, SubjectTrack, track
from repcut.media.ffmpeg_builder import CropBox

FPS = 30
W, H = 1280, 720
REEL = 9 / 16


def walking_clip(path: Path, seconds: float = 8.0, *, walking: bool = True) -> Path:
    """A grey room with sensor noise and one bright figure walking left to right."""
    x = f"80+(1120-80)*t/{seconds}" if walking else "600"
    graph = (
        f"color=c=0x3a3a3a:s={W}x{H}:r={FPS}:d={seconds},noise=alls=6:allf=t[bg];"
        f"color=c=white:s=90x320:r={FPS}:d={seconds}[fig];"
        f"[bg][fig]overlay=x='{x}':y=300:shortest=1,format=yuv420p[v]"
    )
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-filter_complex",
            graph,
            "-map",
            "[v]",
            "-c:v",
            "libx264",
            "-crf",
            "18",
            str(path),
        ],
        check=True,
    )
    return path


def _subject_x(frame: np.ndarray) -> float | None:
    luma = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    _, xs = np.nonzero(luma > 200)
    return float(xs.mean()) if len(xs) > 200 else None


def _render_crop(source: Path, box: CropBox, frames: int, out: Path) -> list[np.ndarray]:
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            str(source),
            "-filter_complex",
            f"[0:v]fps={FPS},{box.filter()},scale=540:960[v]",
            "-map",
            "[v]",
            "-frames:v",
            str(frames),
            "-c:v",
            "libx264",
            "-crf",
            "16",
            str(out),
        ],
        check=True,
    )
    capture = cv2.VideoCapture(str(out))
    frames_out = []
    while True:
        ok, frame = capture.read()
        if not ok:
            break
        frames_out.append(frame)
    capture.release()
    return frames_out


def _noisy_walk(seconds: float = 6.0, rate: float = 5.0) -> list[tuple[float, float | None]]:
    rng = np.random.default_rng(7)
    out: list[tuple[float, float | None]] = []
    for i in range(int(seconds * rate)):
        t = i / rate
        cx = 0.15 + 0.7 * t / seconds + float(rng.uniform(-0.02, 0.02))
        out.append((t, None if float(rng.random()) < 0.1 else cx))
    return out


def test_the_crop_follows_without_chasing_jitter() -> None:
    frames = 6 * FPS
    path = crop_path(
        _noisy_walk(), start=0.0, frames=frames, fps=FPS, width=W, height=H, aspect=REEL
    )
    xs = [path.x_at(f) for f in range(frames)]
    steps = np.diff(xs)
    assert np.max(np.abs(steps)) <= MAX_PAN_PER_SECOND * path.width / FPS + 2
    reversals = int(np.sum(np.diff(np.sign(steps[np.abs(steps) > 0.5])) != 0))
    assert reversals <= 2, f"the crop changed direction {reversals} times on a steady walk"
    assert all(x % 2 == 0 for _, x in path.keys) and min(xs) >= 0 and max(xs) <= W - path.width
    # The subject (true centre) stays in the crop's middle third.
    inside = 0
    for f in range(frames):
        true = (0.15 + 0.7 * (f / FPS) / 6.0) * W
        inside += path.x_at(f) + path.width / 3 <= true <= path.x_at(f) + 2 * path.width / 3
    assert inside / frames >= 0.95


def test_a_lost_subject_eases_to_the_centre_and_is_counted() -> None:
    samples: list[tuple[float, float | None]] = [(t / 5, 0.2) for t in range(10)]
    samples += [(2.0 + t / 5, None) for t in range(3 * 5)]
    path = crop_path(samples, start=0.0, frames=5 * FPS, fps=FPS, width=W, height=H, aspect=REEL)
    assert path.lost_seconds > LOST_SECONDS
    centre = (W - path.width) / 2
    assert abs(path.x_at(5 * FPS - 1) - centre) <= 2
    nobody = crop_path(
        [(t / 5, None) for t in range(25)],
        start=0.0,
        frames=FPS,
        fps=FPS,
        width=W,
        height=H,
        aspect=REEL,
    )
    assert nobody.centred and nobody.keys == ((0, (W - nobody.width) // 2 // 2 * 2),)


def test_a_landscape_export_of_landscape_footage_has_nothing_to_track() -> None:
    path = crop_path(
        _noisy_walk(), start=0.0, frames=FPS, fps=FPS, width=W, height=H, aspect=16 / 9
    )
    assert path.centred


def test_the_track_reads_numbers_only_and_round_trips(tmp_path: Path) -> None:
    clip = walking_clip(tmp_path / "walk.mp4", seconds=3.0)
    found = track(clip, MovementDetector())
    assert found.detector == "movement" and len(found.samples) >= 14
    assert SubjectTrack.from_json(found.to_json()) == found
    assert SubjectTrack.from_json('{"detector": "x", "version": 999, "samples": []}') is None
    assert SubjectTrack.from_json("not json") is None


@pytest.mark.slow
def test_walking_subject_stays_in_the_middle_third_of_a_reel(tmp_path: Path) -> None:
    """Criterion: the subject's centre of mass in the middle third for >= 95% of frames."""
    clip = walking_clip(tmp_path / "walk.mp4")
    found = track(clip, MovementDetector())
    frames = 7 * FPS
    path = crop_path(
        [(s.t, s.cx) for s in found.samples],
        start=0.5,
        frames=frames,
        fps=FPS,
        width=W,
        height=H,
        aspect=REEL,
    )
    assert not path.centred
    trimmed = tmp_path / "from-half.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-ss",
            "0.5",
            "-i",
            str(clip),
            "-c:v",
            "libx264",
            "-crf",
            "12",
            str(trimmed),
        ],
        check=True,
    )
    box = CropBox(path.width, path.height, path.keys[0][1], path.y, path.keys)
    out = _render_crop(trimmed, box, frames, tmp_path / "reel.mp4")
    assert len(out) >= frames - 2
    width = out[0].shape[1]
    positions = [_subject_x(f) for f in out]
    seen = [x for x in positions if x is not None]
    inside = sum(width / 3 <= x <= 2 * width / 3 for x in seen)
    assert len(seen) >= 0.95 * len(out)
    assert inside / len(out) >= 0.95, f"{inside}/{len(out)} frames centred"


def test_nobody_moving_falls_back_to_the_centre_crop(tmp_path: Path) -> None:
    still = walking_clip(tmp_path / "still.mp4", seconds=3.0, walking=False)
    found = track(still, MovementDetector())
    path = crop_path(
        [(s.t, s.cx) for s in found.samples],
        start=0.0,
        frames=2 * FPS,
        fps=FPS,
        width=W,
        height=H,
        aspect=REEL,
    )
    assert path.centred or abs(path.x_at(0) - (W - path.width) / 2) <= 2
