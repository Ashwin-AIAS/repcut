"""Slow motion in the export: interpolated when it holds up, the fallback when not (P1).

The interpolator is a stand-in (the real one needs torch and its weights), so
these check the export's handling of each verdict, not RIFE's quality.
"""

import json
import subprocess
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from test_export import ENCODE, _shot

from repcut.export.render import ExportInputs, ShotSource, plan_segments, render_export
from repcut.slowmo import confidence
from repcut.slowmo.rife import Interpolator
from repcut.slowmo.runtime import InterpolationSetup
from repcut.slowmo.weights import NOT_INSTALLED


class Blend:
    def midpoint(self, first: np.ndarray, second: np.ndarray) -> np.ndarray:
        result: np.ndarray = ((first.astype(np.uint16) + second) // 2).astype(np.uint8)
        return result


class Garbage:
    def midpoint(self, first: np.ndarray, second: np.ndarray) -> np.ndarray:
        result: np.ndarray = np.ascontiguousarray(first[::-1])
        return result


def _factory(interpolator: Interpolator) -> Callable[[], Iterator[Interpolator]]:
    @contextmanager
    def opened() -> Iterator[Interpolator]:
        yield interpolator

    return opened  # type: ignore[return-value]


def _still_clip(path: Path) -> Path:
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "color=c=0x507090:s=640x360:r=30",
            "-f", "lavfi", "-i", "sine=frequency=330:sample_rate=44100",
            "-t", "4", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30",
            "-c:a", "aac", "-shortest", path.as_posix(),
        ],
        check=True,
        capture_output=True,
        timeout=120,
    )  # fmt: skip
    return path


def _inputs(source: Path, *, setup: InterpolationSetup | None = None) -> ExportInputs:
    slow: ShotSource = replace(_shot(source, 1.0, 2.0, 1.2), speed=0.5, fallback_in=0.95)
    return ExportInputs(
        shots=(_shot(source, 0.0, 1.0, 0.1), slow, _shot(source, 2.0, 2.6, 3.0)),
        duration_seconds=2.6,
        encode=ENCODE,
        audio_bitrate="128k",
        music=None,
        captions=(),
        style=None,
        interpolation=setup,
    )


def _frames(path: Path) -> int:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
         "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", path.as_posix()],
        check=True, capture_output=True, text=True, timeout=60,
    )  # fmt: skip
    return int(out.stdout.strip())


def test_a_slow_segment_is_keyed_by_its_speed_window_and_the_pinned_model(
    tmp_path: Path,
) -> None:
    inputs = _inputs(tmp_path / "a.mp4")
    slow = plan_segments(inputs)[1]
    assert slow.slow and slow.start_seconds == pytest.approx(1.2)
    assert slow.fallback_seconds == pytest.approx(0.95)
    normal = plan_segments(
        replace(
            inputs,
            shots=(
                inputs.shots[0],
                replace(inputs.shots[1], speed=1.0, fallback_in=None),
                inputs.shots[2],
            ),
        )
    )[1]
    assert normal.key != slow.key and not normal.slow


async def test_an_accepted_slow_shot_is_interpolated_and_the_export_stays_platform_safe(
    tmp_path: Path,
) -> None:
    source = _still_clip(tmp_path / "still.mp4")
    result = await render_export(
        _inputs(source),
        tmp_path / "work",
        tmp_path / "out.mp4",
        interpolator_factory=_factory(Blend()),
    )
    assert result.problems == []
    assert len(result.slowmo) == 1
    decision = result.slowmo[0]
    assert decision["accepted"] is True and decision["played"] == "slow motion"
    assert decision["reason"] == confidence.ACCEPTED
    assert _frames(tmp_path / "out.mp4") == round(2.6 * ENCODE.fps)


async def test_a_refused_slow_shot_plays_its_fallback_and_says_why(
    tmp_path: Path, make_clip: Callable[..., Path]
) -> None:
    source = make_clip("moving.mp4", seconds=4.0)
    result = await render_export(
        _inputs(source),
        tmp_path / "work",
        tmp_path / "out.mp4",
        interpolator_factory=_factory(Garbage()),
    )
    assert result.problems == []
    decision = result.slowmo[0]
    assert decision["accepted"] is False and decision["played"] == "normal speed"
    assert decision["reason"] in (confidence.WARP_FAILED, confidence.POP_FAILED)
    assert float(str(decision["confidence"])) < confidence.THRESHOLD
    manifest = json.loads((tmp_path / "out.json").read_text())
    assert manifest["slowmo"][0]["reason"] == decision["reason"]
    assert _frames(tmp_path / "out.mp4") == round(2.6 * ENCODE.fps)
    # Nothing interpolated is left behind.
    assert not list((tmp_path / "work" / "segments").glob("*.rgb"))


async def test_without_the_model_the_shot_plays_normally_and_the_resume_keeps_the_verdict(
    tmp_path: Path, make_clip: Callable[..., Path]
) -> None:
    source = make_clip("moving.mp4", seconds=4.0)
    setup = InterpolationSetup(weights=tmp_path / "models" / "absent.pkl")
    inputs = _inputs(source, setup=setup)
    first = await render_export(inputs, tmp_path / "work", tmp_path / "out.mp4")
    assert first.problems == []
    assert first.slowmo[0]["accepted"] is False
    reason = str(first.slowmo[0]["reason"])
    assert reason in (NOT_INSTALLED,) or "PyTorch" in reason
    second = await render_export(inputs, tmp_path / "work", tmp_path / "out.mp4")
    assert second.segments_reused == 3
    assert second.slowmo[0]["reused"] is True and second.slowmo[0]["reason"] == reason
