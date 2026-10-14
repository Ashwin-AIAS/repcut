"""The export pipeline on synthetic clips: geometry, segments, resume, platform check."""

import asyncio
import itertools
import json
import subprocess
import sys
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from repcut.captions import ass
from repcut.captions.styles import style
from repcut.captions.types import CaptionLine, TimedWord
from repcut.export.check import Expected, is_faststart, platform_problems
from repcut.export.presets import config
from repcut.export.reframe import crop_for
from repcut.export.render import ExportInputs, ShotSource, frame_grid, plan_segments, render_export
from repcut.media.ffmpeg_builder import (
    CropBox,
    ExportEncode,
    GradeSpec,
    UnsafeFilterPathError,
    build_export_mux,
    build_export_segment,
)

ENCODE = ExportEncode(180, 320, 30, "ultrafast", 28, "high", "4.2", 2)
GRADE = GradeSpec(0.0, (1.0, 1.0, 1.0), 0.0, None, 0, 0.0)


# --- geometry and config -------------------------------------------------------


def test_presets_come_from_config_and_are_platform_shaped() -> None:
    cfg = config()
    assert {p.id: (p.width, p.height) for p in cfg.presets} == {
        "reel": (1080, 1920),
        "square": (1080, 1080),
        "landscape": (1920, 1080),
    }
    assert cfg.default == "reel" and cfg.fps == 30 and cfg.encode.audio_sample_rate == 48000


@pytest.mark.parametrize(
    ("size", "aspect", "offset", "expected"),
    [
        ((1920, 1080), 9 / 16, 0.0, (606, 1080, 656, 0)),
        ((1920, 1080), 9 / 16, -1.0, (606, 1080, 0, 0)),
        ((1920, 1080), 9 / 16, 1.0, (606, 1080, 1314, 0)),
        ((1080, 1920), 9 / 16, 0.0, (1080, 1920, 0, 0)),
        ((1080, 1920), 16 / 9, 0.5, (1080, 606, 0, 986)),
        ((1080, 1920), 1.0, 0.0, (1080, 1080, 0, 420)),
    ],
)
def test_the_crop_is_centred_nudged_and_never_leaves_the_frame(
    size: tuple[int, int], aspect: float, offset: float, expected: tuple[int, int, int, int]
) -> None:
    crop = crop_for(*size, aspect, offset)
    assert (crop.width, crop.height, crop.x, crop.y) == expected
    assert crop.x % 2 == 0 and crop.y % 2 == 0
    assert crop.x + crop.width <= size[0] and crop.y + crop.height <= size[1]
    assert crop_for(*size, aspect, 7.0) == crop_for(*size, aspect, 1.0)


def _shot(
    source: Path, timeline_in: float, timeline_out: float, source_in: float = 0.2
) -> ShotSource:
    crop = crop_for(640, 360, ENCODE.width / ENCODE.height)
    return ShotSource(
        sha256="c" * 64,
        source=source,
        source_in=source_in,
        timeline_in=timeline_in,
        timeline_out=timeline_out,
        width=640,
        height=360,
        has_audio=True,
        crop=CropBox(crop.width, crop.height, crop.x, crop.y),
        grade=GRADE,
        lut_digest=None,
    )


def test_the_frame_grid_is_contiguous_and_ends_on_the_edit() -> None:
    shots = [_shot(Path("a"), t, t + 0.517) for t in (0.0, 0.517, 1.034)]
    grid = frame_grid(shots, 30, 1.551)
    assert grid == [(0, 16), (16, 31), (31, 47)]
    assert all(a[1] == b[0] for a, b in itertools.pairwise(grid))


def test_the_segment_builder_states_every_platform_property(tmp_path: Path) -> None:
    argv = build_export_segment(
        tmp_path / "src.mp4",
        tmp_path / "seg.mkv",
        start_seconds=1.5,
        frames=45,
        crop=CropBox(202, 360, 218, 0),
        encode=ENCODE,
        grade=GRADE,
        has_audio=False,
        subtitles=tmp_path / "c.ass",
        color_primaries="arib-std-b67",
        color_transfer="arib-std-b67",
    ).argv
    graph = argv[argv.index("-filter_complex") + 1]
    stages = graph.split(",")
    tonemap = next(i for i, s in enumerate(stages) if s.startswith("tonemap="))
    assert tonemap < next(i for i, s in enumerate(stages) if s.startswith("crop="))
    assert graph.index("crop=") < graph.index("scale=180:320") < graph.index("ass=filename=")
    assert "anullsrc=r=48000" in graph and "atrim=end_sample=72000" in graph
    for flag, value in (
        ("-pix_fmt", "yuv420p"),
        ("-colorspace", "bt709"),
        ("-color_range", "tv"),
        ("-frames:v", "45"),
        ("-map_metadata", "-1"),
    ):
        assert argv[argv.index(flag) + 1] == value
    with pytest.raises(UnsafeFilterPathError):
        build_export_segment(
            tmp_path / "src.mp4",
            tmp_path / "seg.mkv",
            start_seconds=0.0,
            frames=1,
            crop=CropBox(2, 2, 0, 0),
            encode=ENCODE,
            grade=GRADE,
            has_audio=True,
            subtitles=tmp_path / "it's.ass",
        )
    mux = build_export_mux(tmp_path / "l.txt", None, tmp_path / "o.mp4", duration_seconds=3.0).argv
    assert "+faststart" in mux and mux[mux.index("-c:a") + 1] == "aac"
    assert mux[mux.index("-ar") + 1] == "48000"


# --- rendering -----------------------------------------------------------------


def _inputs(source: Path, *, captions: tuple[ass.Event, ...] = ()) -> ExportInputs:
    shots = (
        _shot(source, 0.0, 1.0, 0.2),
        _shot(source, 1.0, 1.7, 2.0),
        _shot(source, 1.7, 2.5, 0.5),
    )
    return ExportInputs(
        shots=shots,
        duration_seconds=2.5,
        encode=ENCODE,
        audio_bitrate="128k",
        music=None,
        captions=captions,
        style=style("classic") if captions else None,
    )


def test_a_caption_change_invalidates_only_the_shots_it_touches(tmp_path: Path) -> None:
    words = (TimedWord("w", 1.2, 1.5, "hello", "hello", 0.9),)
    line = CaptionLine("l", 1.2, 1.5, words)
    before = plan_segments(
        _inputs(tmp_path / "a.mp4", captions=tuple(ass.events([line], style("classic"))))
    )
    edited = (replace(words[0], text="hallo"),)
    after = plan_segments(
        _inputs(
            tmp_path / "a.mp4",
            captions=tuple(ass.events([CaptionLine("l", 1.2, 1.5, edited)], style("classic"))),
        )
    )
    changed = [a.key != b.key for a, b in zip(before, after, strict=True)]
    assert changed == [False, True, False]


async def test_an_export_is_platform_safe_and_resumes_from_finished_segments(
    tmp_path: Path, make_clip: Callable[..., Path]
) -> None:
    source = make_clip("long.mp4", seconds=4.0)
    inputs = _inputs(source)
    work = tmp_path / "work"
    first = await render_export(inputs, work, tmp_path / "out.mp4")
    assert first.problems == []
    assert (first.segments_rendered, first.segments_reused) == (3, 0)
    assert set(first.timings_ms) == {"plan", "segments", "music", "mux", "check"}
    assert is_faststart(tmp_path / "out.mp4")
    manifest = json.loads((tmp_path / "out.json").read_text())
    assert manifest["output"] == "out.mp4"  # a name, never a path

    # A crash after the first segment: the others are gone, the first is kept.
    segments = sorted((work / "segments").glob("*.mkv"), key=lambda p: p.stat().st_mtime)
    kept = {p.name: p.stat().st_mtime_ns for p in segments[:1]}
    for path in segments[1:]:
        path.unlink()
    second = await render_export(inputs, work, tmp_path / "out.mp4")
    assert (second.segments_rendered, second.segments_reused) == (2, 1)
    assert {p: (work / "segments" / p).stat().st_mtime_ns for p in kept} == kept
    assert second.problems == []


def test_platform_problems_name_every_shortfall(tmp_path: Path) -> None:
    probe = {
        "streams": [
            {"codec_type": "video", "codec_name": "hevc", "pix_fmt": "yuv422p10le", "width": 1080,
             "height": 1920, "color_space": "bt2020nc", "color_primaries": "bt2020",
             "color_transfer": "arib-std-b67", "color_range": "tv"},
            {"codec_type": "audio", "codec_name": "opus", "sample_rate": "44100"},
        ],
        "format": {"duration": "40.0"},
    }  # fmt: skip
    fake = tmp_path / "fake.mp4"
    fake.write_bytes(b"\x00\x00\x00\x08mdat" + b"\x00\x00\x00\x08moov")
    problems = platform_problems(json.dumps(probe), fake, Expected(1080, 1920, 30.0))
    text = " | ".join(problems)
    for word in ("hevc", "yuv422p10le", "bt2020", "opus", "44100", "40.00", "faststart"):
        assert word in text, word


def test_a_killed_render_leaves_no_file_that_looks_finished(
    tmp_path: Path, make_clip: Callable[..., Path]
) -> None:
    """The temp-then-rename contract the resume rests on, under a real SIGKILL."""
    source = make_clip("k.mp4", seconds=4.0)
    script = tmp_path / "go.py"
    script.write_text(
        "import asyncio, sys\n"
        "from pathlib import Path\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "import test_export as t\n"
        "inputs = t._inputs(Path(sys.argv[2]))\n"
        "asyncio.run(t.render_export(inputs, Path(sys.argv[3]), Path(sys.argv[4])))\n",
        encoding="utf-8",
    )
    work = tmp_path / "w"
    process = subprocess.Popen(
        [
            sys.executable,
            str(script),
            str(Path(__file__).parent),
            str(source),
            str(work),
            str(tmp_path / "o.mp4"),
        ],
    )
    try:
        process.wait(timeout=0.8)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
    for path in (work / "segments").glob("*.mkv") if (work / "segments").exists() else []:
        if not path.name.startswith("."):
            assert path.stat().st_size > 0
    result = asyncio.run(render_export(_inputs(source), work, tmp_path / "o.mp4"))
    assert result.problems == [] and result.segments_total == 3
