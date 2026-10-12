"""Clip signals and usable segments, on generated footage with known content."""

import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from repcut.edit.segments import SHAKE_LIMIT, boundaries, unusable_windows, usable_segments
from repcut.edit.signals import ClipSignals, measure
from repcut.edit.types import BoundaryOverride
from repcut.edit.vad import SpeechRegion

SHA = "e" * 64


@pytest.fixture(scope="module")
def steady_pan_shaken(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Three seconds each: one frozen frame, a smooth pan, the same pan shaken."""
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg is not on PATH; `make check-env` reports it with a fix")
    destination = tmp_path_factory.mktemp("signals") / "three.mp4"
    graph = (
        "[0:v]split=3[a][b][c];"
        "[a]trim=end_frame=1,loop=loop=89:size=1:start=0,setpts=N/30/TB,crop=640:360:80:45[s];"
        "[b]trim=3:6,setpts=PTS-STARTPTS,crop=640:360:x='20+40*t':y=45[p];"
        # Deterministic jitter, not random(): FFmpeg 6.1 and 8.1 draw different
        # random() sequences, and one quiet half second failed CI on 6.1.
        "[c]trim=6:9,setpts=PTS-STARTPTS,"
        "crop=640:360:x='60+40*mod(n,2)':y='25+40*mod(floor(n/5),2)'[k];"
        "[s][p][k]concat=n=3:v=1:a=0[v]"
    )
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc=size=800x450:rate=30:duration=9",
            "-filter_complex", graph, "-map", "[v]",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
            destination.as_posix(),
        ],
        check=True,
        capture_output=True,
        timeout=120,
    )  # fmt: skip
    return destination


def _signals(clip: Path, speech: tuple[SpeechRegion, ...] | None = None) -> ClipSignals:
    quiet = np.zeros(16000 * 9, dtype=np.float32)
    measured = measure(clip, quiet, 9.0, Path("no-model-here"))
    assert measured.speech is None  # no model: "not measured", never "no speech"
    return ClipSignals(
        measured.version,
        measured.duration_seconds,
        measured.window_seconds,
        measured.audio_db,
        measured.motion,
        measured.shake,
        speech,
    )


def test_a_still_frame_has_no_motion_and_a_shaken_one_has_shake(steady_pan_shaken: Path) -> None:
    signals = _signals(steady_pan_shaken)
    assert max(signals.motion[1:5]) < 0.5
    steady, pan, shaken = signals.shake[1:5], signals.shake[7:11], signals.shake[13:17]
    assert max(steady) < SHAKE_LIMIT / 4
    assert max(pan) < SHAKE_LIMIT / 4, "a smooth pan is not shake"
    assert min(shaken) > SHAKE_LIMIT


def test_dead_space_and_shake_are_cut_out(steady_pan_shaken: Path) -> None:
    segments = usable_segments(SHA, _signals(steady_pan_shaken), [3.0, 6.0])
    covered = [(s.start, s.end) for s in segments]
    assert not any(start < 2.5 and end > 0.5 for start, end in covered), "dead space kept"
    assert all(start < 6.5 for start, _ in covered), "shaken footage was kept"
    assert any(start >= 3.0 and end <= 6.0 for start, end in covered), "the pan was lost"


def test_speech_is_never_dead_space(steady_pan_shaken: Path) -> None:
    talking = _signals(steady_pan_shaken, (SpeechRegion(0.0, 3.0),))
    windows = unusable_windows(talking)
    assert not any(windows[:6])


def test_boundary_overrides_add_and_remove() -> None:
    adds = [BoundaryOverride(SHA, "add", 2.0)]
    assert boundaries([3.0, 6.0], 9.0, adds, SHA) == (2.0, 3.0, 6.0)
    removes = [BoundaryOverride(SHA, "remove", 3.2)]
    assert boundaries([3.0, 6.0], 9.0, removes, SHA) == (6.0,)
    other_clip = [BoundaryOverride("f" * 64, "remove", 3.0)]
    assert boundaries([3.0, 6.0], 9.0, other_clip, SHA) == (3.0, 6.0)


def test_a_removed_boundary_merges_two_segments(steady_pan_shaken: Path) -> None:
    signals = _signals(steady_pan_shaken, (SpeechRegion(0.0, 9.0),))
    split = usable_segments(SHA, signals, [3.0, 6.0])
    merged = usable_segments(SHA, signals, [3.0, 6.0], [BoundaryOverride(SHA, "remove", 3.0)])
    assert any(s.start < 3.0 < s.end for s in merged)
    assert not any(s.start < 3.0 < s.end for s in split)


def test_signals_round_trip_through_json(steady_pan_shaken: Path) -> None:
    signals = _signals(steady_pan_shaken, (SpeechRegion(1.0, 2.0),))
    assert ClipSignals.from_json(signals.to_json()) == signals
