"""The builder's output is asserted as strings before any file is touched.

The centrepiece is ``test_every_recipe_argv_matches_its_params_version``. A
derived artifact is keyed ``(sha256, artifact_kind, params_version)``, and
nothing in the language forces the version up when the recipe changes - so an
edited recipe would keep its key and go on serving bytes the previous recipe
made, invisible until a grade looks wrong several prompts later. The frozen argv
below is what forces the bump: change a recipe and the snapshot mismatches;
bump the version and the lookup misses. Either way the failure names the fix.
"""

import asyncio
import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from repcut.media.artifacts import (
    PARAMS_VERSION,
    PROXY_RECIPE,
    THUMBNAIL_STRIP_RECIPE,
    ArtifactKind,
)
from repcut.media.ffmpeg_builder import (
    PROBE_TIMEOUT_S,
    RENDER_TIMEOUT_S,
    FFmpegEncodeError,
    FFmpegFilterGraphError,
    FFmpegNotInstalledError,
    UnsupportedCodecError,
    build_probe,
    build_proxy,
    build_thumbnail_strip,
    classify_failure,
    redact_paths,
    render,
    run,
    temp_target,
    thumbnail_frame_count,
)

# Fixed inputs, so the snapshots below describe the recipe and nothing else.
# Content-addressed paths, as amendment 004 lays them out - never a user filename.
SHA = "a" * 64
SOURCE = Path(f"media/blobs/aa/{SHA}/source.mp4")
PROXY_OUT = Path(f"media/derived/aa/{SHA}/proxy/1/proxy.mp4")
STRIP_OUT = Path(f"media/derived/aa/{SHA}/thumbnail_strip/1/strip.jpg")
DISPLAY_HEIGHT = 1080
DURATION_S = 10.0


def _argv_for(kind: ArtifactKind) -> list[str]:
    """Build ``kind``'s command from the fixed inputs above."""
    if kind is ArtifactKind.PROXY:
        return build_proxy(SOURCE, PROXY_OUT, display_height=DISPLAY_HEIGHT).argv
    if kind is ArtifactKind.THUMBNAIL_STRIP:
        return build_thumbnail_strip(SOURCE, STRIP_OUT, duration_seconds=DURATION_S).argv
    raise AssertionError(f"no builder wired for {kind.value} - add one before shipping the kind")


# Frozen argv per (kind, params_version). Add an entry when you bump a version;
# keep the superseded one, because artifacts rendered under it are still on disk
# and still referenced.
RECIPE_ARGV: dict[tuple[ArtifactKind, int], list[str]] = {
    (ArtifactKind.PROXY, 1): [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "error",
        "-y",
        "-i",
        SOURCE.as_posix(),
        "-vf",
        "scale=-2:720,fps=30",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        "-colorspace",
        "bt709",
        "-color_primaries",
        "bt709",
        "-color_trc",
        "bt709",
        "-color_range",
        "tv",
        "-fps_mode",
        "cfr",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-movflags",
        "+faststart",
        PROXY_OUT.as_posix(),
    ],
    (ArtifactKind.THUMBNAIL_STRIP, 1): [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "error",
        "-y",
        "-i",
        SOURCE.as_posix(),
        "-vf",
        "fps=1/2,scale=-2:180,tile=5x1",
        "-frames:v",
        "1",
        "-c:v",
        "mjpeg",
        "-q:v",
        "4",
        "-an",
        STRIP_OUT.as_posix(),
    ],
}


@pytest.mark.parametrize("kind", list(ArtifactKind))
def test_every_recipe_argv_matches_its_params_version(kind: ArtifactKind) -> None:
    """A recipe change without a version bump fails here, saying so.

    This is the whole point of the file. Without it, `params_version` is a
    manual integer nothing enforces, and a stale artifact is served silently.
    """
    version = PARAMS_VERSION[kind]
    snapshot = RECIPE_ARGV.get((kind, version))

    assert snapshot is not None, (
        f"PARAMS_VERSION[{kind.value}] is {version} and RECIPE_ARGV has no argv frozen at "
        f"that version. If you bumped the version, add the new argv to RECIPE_ARGV in this "
        f"same commit; keep the old entry, since artifacts rendered under it are still on disk."
    )
    assert _argv_for(kind) == snapshot, (
        f"the {kind.value} recipe changed but PARAMS_VERSION[{kind.value}] is still {version}. "
        f"Bump it in engine/repcut/media/artifacts.py in this same commit and freeze the new "
        f"argv here. An unbumped version keeps the artifact key, so every already-rendered "
        f"{kind.value} goes on being served for bytes the old recipe made."
    )


def test_every_artifact_kind_has_a_version_and_a_builder() -> None:
    """A new kind cannot ship without a version and an argv to freeze."""
    assert set(PARAMS_VERSION) == set(ArtifactKind)
    for kind in ArtifactKind:
        assert _argv_for(kind)


def test_no_snapshot_outlives_its_kind() -> None:
    """Deleting an ArtifactKind must not leave its frozen argv behind."""
    assert {kind for kind, _ in RECIPE_ARGV} <= set(ArtifactKind)


def test_the_probe_asks_for_both_frame_rates_and_the_rotation_tag() -> None:
    """VFR is detected by r_frame_rate != avg_frame_rate; rotation by side data.

    Missing either turns phone footage into silent drift and portrait video into
    a sideways proxy, so the probe is asserted rather than trusted.
    """
    argv = build_probe(SOURCE).argv
    requested = " ".join(argv)

    assert "r_frame_rate" in requested
    assert "avg_frame_rate" in requested
    assert "stream_side_data=rotation" in requested
    assert argv[-2:] == ["-of", "json"]
    # ffprobe writes to stdout; an empty output operand would be a real argument.
    assert "" not in argv


def test_the_probe_carries_the_probe_budget_not_the_render_budget() -> None:
    """A probe that inherits the render budget blocks ingest on a truncated file.

    Bound to the command rather than passed by callers: a budget every caller
    has to remember is a budget most callers will not pass, and the one that
    forgets is the one probing the broken upload.
    """
    assert build_probe(SOURCE).timeout_s == PROBE_TIMEOUT_S
    assert PROBE_TIMEOUT_S < RENDER_TIMEOUT_S


def test_the_proxy_forces_constant_frame_rate_two_ways() -> None:
    command = build_proxy(SOURCE, PROXY_OUT, display_height=DISPLAY_HEIGHT)

    assert f"fps={PROXY_RECIPE.fps}" in command.argv[command.argv.index("-vf") + 1]
    assert command.argv[command.argv.index("-fps_mode") + 1] == "cfr"


def test_the_proxy_sets_colour_explicitly() -> None:
    """Left implicit, the grade shifts between preview and export."""
    argv = build_proxy(SOURCE, PROXY_OUT, display_height=DISPLAY_HEIGHT).argv

    for flag in ("-colorspace", "-color_primaries", "-color_trc"):
        assert argv[argv.index(flag) + 1] == "bt709"
    assert argv[argv.index("-color_range") + 1] == "tv"


def test_the_proxy_takes_its_height_from_the_probe_not_the_container() -> None:
    """Rotation makes the container's dimensions a lie for portrait phone video.

    Width is left to ``-2`` so it follows the decoded, already-rotated frame.
    """
    portrait = build_proxy(SOURCE, PROXY_OUT, display_height=1920)

    assert "scale=-2:720," in portrait.argv[portrait.argv.index("-vf") + 1]


@pytest.mark.parametrize(
    ("display_height", "expected"),
    [(1080, 720), (720, 720), (480, 480), (481, 480), (2160, 720)],
)
def test_a_short_source_is_not_upscaled(display_height: int, expected: int) -> None:
    """The recipe height is a ceiling. Upscaling spends bytes inventing detail.

    481 is the case that matters: an odd height would be rejected by x264 under
    yuv420p, so it rounds down to even rather than through.
    """
    command = build_proxy(SOURCE, PROXY_OUT, display_height=display_height)

    assert f"scale=-2:{expected}," in command.argv[command.argv.index("-vf") + 1]


@pytest.mark.parametrize(
    ("duration", "frames"),
    [(0.5, 1), (2.0, 1), (3.0, 2), (5.0, 3), (10.0, 5), (11.0, 6)],
)
def test_the_strip_holds_one_frame_per_interval(duration: float, frames: int) -> None:
    """ceil, and never zero - a clip shorter than one interval still gets a frame."""
    assert thumbnail_frame_count(duration, THUMBNAIL_STRIP_RECIPE) == frames

    command = build_thumbnail_strip(SOURCE, STRIP_OUT, duration_seconds=duration)
    assert f"tile={frames}x1" in command.argv[command.argv.index("-vf") + 1]


def test_the_dry_run_keeps_the_graph_and_drops_the_container() -> None:
    """The plan is validated on a 2s slice before the timeline is rendered.

    It has to be the *same* graph and the *same* encoder, or it validates
    something else. It must not carry `-movflags`, which the null muxer rejects.
    """
    command = build_proxy(SOURCE, PROXY_OUT, display_height=DISPLAY_HEIGHT)
    dry = command.dry_run()

    assert dry.filter_arguments == command.filter_arguments
    assert dry.encode_arguments == command.encode_arguments
    assert dry.argv[-5:] == ["-t", "2", "-f", "null", "-"]
    assert "-movflags" not in dry.argv
    assert PROXY_OUT.as_posix() not in dry.argv


def test_two_renders_of_one_target_get_different_temp_names() -> None:
    """The store is content-addressed, so two jobs reach the same target.

    A temp name derived only from the target collides exactly then - two
    projects uploading the same clip, or a retry racing a job that is not dead
    yet - and one render would then delete or overwrite a file the other has
    open. On Windows that is a ``PermissionError``, which is not an
    ``FFmpegError`` and escapes ``render``'s handler as a raw traceback.
    """
    first = temp_target(PROXY_OUT)
    second = temp_target(PROXY_OUT)

    assert first != second
    assert first.parent == second.parent == PROXY_OUT.parent


@pytest.mark.parametrize("final", [PROXY_OUT, STRIP_OUT])
def test_the_temp_name_keeps_the_real_suffix_last(final: Path) -> None:
    """FFmpeg picks the muxer from the extension; `.partial` is not one."""
    temp = temp_target(final)

    assert temp.suffix == final.suffix
    assert temp.name.startswith(f".{final.stem}.")
    assert ".partial" in temp.name


def test_argv_is_a_list_of_strings_and_never_a_shell_string() -> None:
    argv = build_proxy(SOURCE, PROXY_OUT, display_height=DISPLAY_HEIGHT).argv

    assert isinstance(argv, list)
    assert all(isinstance(token, str) for token in argv)
    # A quoted or joined command would show up as a token carrying a space.
    assert not any(" " in token for token in argv)


# Verbatim from ffmpeg 8.1, not from memory. The wording moved between major
# versions - "filtergraph" became "filterchain" - and a classifier matching only
# remembered text degrades every filter bug to the generic case without saying so.
UNKNOWN_FILTER_STDERR = (
    "[AVFilterGraph @ 0x1] No such filter: 'nosuchfilter'\n"
    "Error opening output file -.\n"
    "Error opening output files: Filter not found\n"
)
BAD_FILTER_ARGUMENT_STDERR = (
    "[Parsed_scale_0 @ 0x1] [Eval @ 0x2] Undefined constant or missing '(' in 'bogus'\n"
    "[Parsed_scale_0 @ 0x1] Cannot parse expression for height: 'bogus'\n"
    "[AVFilterGraph @ 0x3] Error initializing filters\n"
    "Error opening output file -.\n"
)
UNKNOWN_ENCODER_STDERR = (
    "[vost#0:0 @ 0x1] Unknown encoder 'libnosuchcodec'\n"
    "[vost#0:0 @ 0x1] Error selecting an encoder\n"
    "Error opening output files: Encoder not found\n"
)


@pytest.mark.parametrize(
    ("stderr", "expected"),
    [
        (UNKNOWN_FILTER_STDERR, FFmpegFilterGraphError),
        (BAD_FILTER_ARGUMENT_STDERR, FFmpegFilterGraphError),
        ("Error parsing filterchain 'x' around: ", FFmpegFilterGraphError),
        (UNKNOWN_ENCODER_STDERR, UnsupportedCodecError),
        ("Automatic encoder selection failed", UnsupportedCodecError),
        ("moov atom not found", FFmpegEncodeError),
        ("No space left on device", FFmpegEncodeError),
        ("something nobody has seen before", FFmpegEncodeError),
        ("", FFmpegEncodeError),
    ],
)
def test_stderr_is_classified_into_a_typed_error(stderr: str, expected: type[Exception]) -> None:
    assert type(classify_failure(stderr, 1)) is expected


def test_the_detail_is_the_diagnostic_not_ffmpegs_closing_summary() -> None:
    """FFmpeg's last line is a summary; the line that says what broke is above it."""
    error = classify_failure(UNKNOWN_FILTER_STDERR, 127)

    assert "nosuchfilter" in error.detail
    assert "Error opening output files" not in error.detail


def test_an_error_carries_a_readable_cause_and_never_a_dump() -> None:
    """The UI renders `cause`. A traceback or a stderr dump is never surfaced."""
    stderr = "\n".join(f"line {n} of noise" for n in range(50)) + "\nNo such filter: 'lut3d'"

    error = classify_failure(stderr, 234)

    assert isinstance(error, FFmpegFilterGraphError)
    assert "filter chain" in error.cause
    assert "\n" not in error.detail
    assert len(error.detail) <= 200
    assert len(str(error)) < len(stderr)


def test_an_error_detail_never_carries_a_path() -> None:
    """$DATA_DIR contains the OS username, so stderr is redacted like argv is."""
    error = classify_failure(
        "Error opening output C:/Users/someone/OneDrive/repcut/data/media/x.mp4: No such file",
        1,
    )

    assert "someone" not in error.detail
    assert "OneDrive" not in error.detail
    assert "x.mp4" in error.detail


def test_logged_argv_keeps_the_filename_and_drops_the_directory() -> None:
    command = build_proxy(
        Path("/home/someone/repcut-data/media/blobs/aa/source.mp4"),
        Path("/home/someone/repcut-data/media/derived/aa/proxy.mp4"),
        display_height=DISPLAY_HEIGHT,
    )

    logged = " ".join(command.loggable_argv)

    assert "someone" not in logged
    assert "source.mp4" in logged
    # The filter chain is not a path and must survive redaction intact.
    assert "scale=-2:720,fps=30" in logged


def test_redaction_leaves_filter_expressions_alone() -> None:
    """`fps=1/2` is one slash, not a path. Redacting it would break the recipe."""
    assert redact_paths("fps=1/2,scale=-2:180,tile=5x1") == "fps=1/2,scale=-2:180,tile=5x1"


# --- Against real FFmpeg -----------------------------------------------------
#
# The argv assertions above prove the builder emits what it means to. These
# prove FFmpeg agrees, which is the only way to know a filter chain is valid.
# Fixtures are generated by the conftest factory; nothing is committed.


def _leftover_partials(directory: Path) -> list[Path]:
    """Temp renders still on disk. Synchronous, so the async lint rule is happy.

    The glob is deliberately loose: the temp name carries a random token, and a
    pattern tight enough to encode the token's shape would stop matching the
    moment the shape changed - and would then report a clean directory that is
    not clean.
    """
    return list(directory.glob(".*.partial*"))


async def _probe(path: Path, entries: str) -> dict[str, str]:
    """Read stream properties out of a rendered file, as strings."""
    process = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        f"stream={entries}",
        "-of",
        "json",
        path.as_posix(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await process.communicate()
    stream: dict[str, str] = json.loads(stdout)["streams"][0]
    return stream


async def test_the_probe_command_returns_parseable_json(
    make_clip: Callable[..., Path],
) -> None:
    source = make_clip(seconds=1.0)

    # No explicit budget: the point is that the command carries its own.
    stdout = await run(build_probe(source))

    probed = json.loads(stdout)
    assert probed["streams"][0]["codec_name"] == "h264"
    assert float(probed["format"]["duration"]) > 0


async def test_a_variable_frame_rate_source_renders_a_constant_rate_proxy(
    make_clip: Callable[..., Path], tmp_path: Path
) -> None:
    """The trap the whole normalization exists for.

    The source's frames land at uneven intervals. Everything downstream - beat
    grids, cut timing, interpolation - assumes constant spacing, and drifts
    silently toward the end of a clip when it is wrong.
    """
    source = make_clip("vfr.mp4", seconds=4.0, variable_frame_rate=True, audio=False)
    before = await _probe(source, "r_frame_rate,avg_frame_rate")
    assert before["r_frame_rate"] != before["avg_frame_rate"], (
        "the fixture is not actually VFR - the assertion below would prove nothing"
    )

    proxy = await render(
        build_proxy(source, tmp_path / "proxy.mp4", display_height=360),
    )

    after = await _probe(proxy, "r_frame_rate,avg_frame_rate,height")
    assert after["r_frame_rate"] == after["avg_frame_rate"] == f"{PROXY_RECIPE.fps}/1"
    assert int(after["height"]) == 360


async def test_the_proxy_caps_height_without_upscaling(
    make_clip: Callable[..., Path], tmp_path: Path
) -> None:
    """Measured on the output, never inferred from the input's dimensions."""
    tall = make_clip("tall.mp4", seconds=1.0, width=1280, height=1080, audio=False)

    proxy = await render(build_proxy(tall, tmp_path / "capped.mp4", display_height=1080))

    assert int((await _probe(proxy, "height"))["height"]) == PROXY_RECIPE.height


async def test_the_strip_holds_one_frame_per_interval_on_disk(
    make_clip: Callable[..., Path], tmp_path: Path
) -> None:
    """ceil(duration / 2) frames, tiled into one image, asserted by its width."""
    source = make_clip("strip.mp4", seconds=5.0, width=320, height=180, audio=False)

    strip = await render(
        build_thumbnail_strip(source, tmp_path / "strip.jpg", duration_seconds=5.0)
    )

    tile = await _probe(strip, "width,height")
    frames = thumbnail_frame_count(5.0, THUMBNAIL_STRIP_RECIPE)
    # The source is 16:9, so each cell is 320x180 after scale=-2:180.
    assert int(tile["height"]) == THUMBNAIL_STRIP_RECIPE.height
    assert int(tile["width"]) == frames * 320


async def test_a_finished_render_leaves_no_partial_file(
    make_clip: Callable[..., Path], tmp_path: Path
) -> None:
    """The encode writes to a dotted temp name and is renamed onto the target."""
    source = make_clip(seconds=1.0)
    destination = tmp_path / "out" / "proxy.mp4"

    await render(build_proxy(source, destination, display_height=360))

    assert destination.is_file()
    assert _leftover_partials(destination.parent) == []


async def test_two_concurrent_renders_of_one_target_both_succeed(
    make_clip: Callable[..., Path], tmp_path: Path
) -> None:
    """The collision dedup makes likely: one target, two live renders.

    Both write, both rename onto the same content-addressed path, neither
    touches the other's file. Measured against the pre-fix deterministic name,
    this failed with ``PermissionError: [WinError 32] ... being used by another
    process`` - raised from ``_prepare``'s unlink or ``_finalise``'s replace
    depending on where the two renders interleave. Neither is an
    ``FFmpegError``, so it escaped ``render``'s handler and reached the caller
    as a raw traceback, which `.claude/rules/ffmpeg.md` forbids.
    """
    source = make_clip(seconds=1.0)
    destination = tmp_path / "out" / "proxy.mp4"

    first, second = await asyncio.gather(
        render(build_proxy(source, destination, display_height=360)),
        render(build_proxy(source, destination, display_height=360)),
    )

    assert first == second == destination
    assert destination.stat().st_size > 0
    assert _leftover_partials(destination.parent) == []


async def test_a_broken_graph_fails_before_the_target_is_created(
    make_clip: Callable[..., Path], tmp_path: Path
) -> None:
    """Fail fast: the dry run rejects the plan, so nothing lands at the target.

    A truncated file at a path that looks finished is the failure mode being
    prevented - the next run would read it as a completed artifact.
    """
    source = make_clip(seconds=1.0)
    destination = tmp_path / "never.mp4"
    broken = replace(
        build_proxy(source, destination, display_height=360),
        filter_arguments=("-vf", "definitely_not_a_filter=1"),
    )

    with pytest.raises(FFmpegFilterGraphError):
        await render(broken)

    assert not destination.exists()
    assert _leftover_partials(tmp_path) == []


async def test_a_missing_executable_is_a_named_error(
    make_clip: Callable[..., Path], tmp_path: Path
) -> None:
    """/health reports a missing FFmpeg; a job still needs a cause it can show."""
    command = build_proxy(
        make_clip(seconds=1.0), tmp_path / "x.mp4", display_height=360
    ).writing_to(tmp_path / "x.mp4")

    with pytest.raises(FFmpegNotInstalledError):
        await run(replace(command, executable="repcut-nonexistent-ffmpeg"))
