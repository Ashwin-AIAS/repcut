"""The new Prompt 03 fixtures actually produce what their docstrings claim.

Two additions to ``conftest.py``: ``make_clip(hdr=True)`` and
``make_motion_loudness_clip``. Neither backs an engine feature yet - Prompt 03's
analysis package does not exist - so there is nothing else to test them
against. This file is that test: it measures the fixtures the way
``verify_03_checks.py`` will, so a fixture that stops producing what its
docstring promises fails here, in seconds, rather than as a confusing failure
three layers into a gate criterion that assumed it.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path


def _ffprobe(path: Path, *args: str) -> dict[str, object]:
    completed = subprocess.run(
        ["ffprobe", "-v", "error", *args, "-of", "json", path.as_posix()],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    document: dict[str, object] = json.loads(completed.stdout)
    return document


def test_hdr_clip_carries_bt2020_hlg_tags(make_clip: Callable[..., Path]) -> None:
    """``hdr=True`` stamps primaries/transfer/matrix, and nothing else moves."""
    clip = make_clip("hdr.mp4", seconds=1.0, width=640, height=360, hdr=True)

    streams = _ffprobe(
        clip,
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=codec_name,color_primaries,color_transfer,color_space",
    )["streams"][0]  # type: ignore[index]

    assert streams["color_primaries"] == "bt2020"
    assert streams["color_transfer"] == "arib-std-b67"
    assert streams["color_space"] == "bt2020nc"
    # Re-tagging is a re-encode (measured in the fixture's own docstring: a
    # stream copy alone does not reliably survive across FFmpeg versions), so
    # the codec is still H.264 - this fixture is deliberately not real HEVC/HDR
    # footage, only a colour tag on a synthetic clip.
    assert streams["codec_name"] == "h264"

    audio = _ffprobe(clip, "-select_streams", "a:0", "-show_entries", "stream=codec_type")
    assert audio["streams"], "the audio track did not survive the HDR re-encode"


def test_plain_clip_has_no_hdr_tags(make_clip: Callable[..., Path]) -> None:
    """The control case: ``hdr`` defaults off, and ffprobe reports it unknown."""
    clip = make_clip("plain.mp4", seconds=1.0)

    streams = _ffprobe(
        clip,
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=color_primaries,color_transfer,color_space",
    )["streams"][0]  # type: ignore[index]

    # ffprobe's JSON writer omits a field entirely when it is unset rather than
    # printing the literal "unknown" it uses for the human-readable formats -
    # measured against this repo's FFmpeg (8.1). Either shape means the same
    # thing: no colour tag was written.
    assert streams.get("color_primaries", "unknown") == "unknown"
    assert streams.get("color_transfer", "unknown") == "unknown"


def test_motion_loudness_clip_has_two_unequal_segments(
    make_motion_loudness_clip: Callable[..., Path],
) -> None:
    """Segment A is flat and quiet; segment B moves and is louder - measurably."""
    clip = make_motion_loudness_clip("motion.mp4", segment_seconds=1.0, fps=30)

    duration = float(_ffprobe(clip, "-show_entries", "format=duration")["format"]["duration"])  # type: ignore[index]
    assert duration == 2.0

    def rms_db(start: float, end: float) -> float:
        completed = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-nostdin",
                "-y",
                "-i",
                clip.as_posix(),
                "-ss",
                str(start),
                "-to",
                str(end),
                "-af",
                "astats=metadata=1",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
        lines = [line for line in completed.stderr.splitlines() if "RMS level dB" in line]
        assert lines, "astats produced no RMS level line"
        return float(lines[-1].rsplit(":", 1)[-1].strip())

    quiet_rms = rms_db(0.0, 1.0)
    loud_rms = rms_db(1.0, 2.0)
    # Measured (see the fixture's docstring) at roughly -38.6dB / -25.0dB, a
    # ~13.6dB step. 6dB is a wide margin under that, so this holds even if the
    # exact numbers move a little with a different FFmpeg build.
    assert loud_rms - quiet_rms > 6.0, (
        f"loud segment ({loud_rms:.1f}dB) is not clearly louder than the quiet "
        f"one ({quiet_rms:.1f}dB)"
    )

    # `movie`'s own ``seek_point`` was measured to be silently ignored on a
    # concat-filter output on this FFmpeg build - every seek returned frame 0
    # regardless of the value. Reading every frame's luma once and slicing by
    # index in Python sidesteps the seek entirely, and is exactly how a scene
    # detector would read it anyway: by frame index against a known fps, not by
    # asking the container to seek.
    #
    # The `movie` filter's own mini-syntax splits on `:`, which an absolute
    # Windows path contains (the drive letter) - so the filename is passed
    # relative, with `cwd` set to its directory, rather than escaped.
    completed = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"movie={clip.name},signalstats",
            "-show_entries",
            "frame_tags=lavfi.signalstats.YAVG",
            "-of",
            "csv=p=0",
        ],
        cwd=clip.parent,
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    # `csv=p=0` still emits a trailing comma (ffprobe leaves the frame index
    # column empty rather than dropping it), so each line is "16," not "16".
    luma = [float(line.rstrip(",")) for line in completed.stdout.splitlines() if line.strip()]
    frames_per_segment = 30  # segment_seconds=1.0 * fps=30
    assert len(luma) == frames_per_segment * 2, f"expected 60 frames, got {len(luma)}"
    quiet_luma, loud_luma = luma[:frames_per_segment], luma[frames_per_segment:]
    # Segment A is a static colour: every frame reads the same mean luma.
    assert max(quiet_luma) - min(quiet_luma) < 0.5
    # Segment B moves every frame: the mean luma is not constant. Measured at
    # ~0.34 swing (125.07-125.42) - the threshold sits comfortably under that
    # rather than at the round number the first draft of this test guessed.
    assert max(loud_luma) - min(loud_luma) > 0.2
    # And the two segments do not sit at the same brightness either - the cut
    # itself is a strong, unambiguous scene boundary.
    assert min(loud_luma) - max(quiet_luma) > 50.0
