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
import subprocess
import sys
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from repcut.analysis.params import FRAME_PARAMS_VERSION
from repcut.media.artifacts import (
    PARAMS_VERSION,
    PROXY_RECIPE,
    THUMBNAIL_STRIP_RECIPE,
    ArtifactKind,
)
from repcut.media.ffmpeg_builder import (
    AUDIO_ENERGY_PROBE_TIMEOUT_S,
    FRAME_EXTRACTION_TIMEOUT_S,
    PROBE_TIMEOUT_S,
    RENDER_SECONDS_PER_SOURCE_SECOND,
    RENDER_TIMEOUT_FLOOR_S,
    RENDER_TIMEOUT_S,
    FFmpegEncodeError,
    FFmpegFilterGraphError,
    FFmpegLoopError,
    FFmpegNotInstalledError,
    FFmpegUnavailableError,
    UnsupportedCodecError,
    build_audio_energy_probe,
    build_frame_extraction,
    build_probe,
    build_proxy,
    build_thumbnail_strip,
    classify_failure,
    parse_overall_rms_db,
    redact_paths,
    render,
    render_timeout_for,
    run,
    source_is_hdr,
    temp_target,
    thumbnail_frame_count,
)

# Fixed inputs, so the snapshots below describe the recipe and nothing else.
# Content-addressed paths, as amendment 004 lays them out - never a user filename.
SHA = "a" * 64
SOURCE = Path(f"media/blobs/aa/{SHA}/source.mp4")
PROXY_OUT = Path(f"media/derived/aa/{SHA}/proxy/1/proxy.mp4")
STRIP_OUT = Path(f"media/derived/aa/{SHA}/thumbnail_strip/1/strip.jpg")
FRAME_OUT = Path(f"media/derived/aa/{SHA}/sampled_frame/1/scene_0.jpg")
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


@pytest.mark.parametrize(
    ("duration", "expected"),
    [
        (None, RENDER_TIMEOUT_S),
        (0.0, RENDER_TIMEOUT_S),
        (-1.0, RENDER_TIMEOUT_S),
        (1.0, RENDER_TIMEOUT_FLOOR_S),
        (10.0, RENDER_TIMEOUT_FLOOR_S),
        (60.0, 60.0 * RENDER_SECONDS_PER_SOURCE_SECOND),
        (1200.0, 1200.0 * RENDER_SECONDS_PER_SOURCE_SECOND),
    ],
)
def test_the_render_budget_scales_with_the_clip(duration: float | None, expected: float) -> None:
    """A fixed render timeout is wrong in kind: clip length is not fixed.

    A 20-minute session proxied on this laptop can outlast any constant, and the
    user then sees a timeout on a job that was working. Below the floor, process
    start and encoder init dominate and duration stops predicting anything.
    """
    assert render_timeout_for(duration) == pytest.approx(expected)


def test_a_long_clip_gets_a_longer_budget_than_the_old_constant() -> None:
    """The regression the scaling exists to prevent, stated as a number.

    Twenty minutes of footage under the old fixed 900s budget had 900s to encode.
    Measured at 0.21 s/s it needs ~250s, so 900s looks generous - until the
    source is 4K HEVC on a throttled laptop, which is exactly the session
    someone films.
    """
    twenty_minutes = 20 * 60.0

    assert render_timeout_for(twenty_minutes) > RENDER_TIMEOUT_S


def test_the_builders_bind_the_budget_to_the_duration_they_were_given() -> None:
    """The scaling is only real if the builders actually apply it."""
    long_clip = build_proxy(SOURCE, PROXY_OUT, display_height=DISPLAY_HEIGHT, duration_seconds=600)
    short_clip = build_proxy(SOURCE, PROXY_OUT, display_height=DISPLAY_HEIGHT, duration_seconds=1)
    strip = build_thumbnail_strip(SOURCE, STRIP_OUT, duration_seconds=600)

    assert long_clip.timeout_s == render_timeout_for(600)
    assert short_clip.timeout_s == RENDER_TIMEOUT_FLOOR_S
    assert strip.timeout_s == render_timeout_for(600)
    # The duration changes the budget and nothing else - the frozen argv above
    # would fail if it had leaked into the recipe.
    assert long_clip.argv == short_clip.argv


def test_the_dry_run_cannot_hold_the_whole_clips_budget() -> None:
    """Two seconds of video must not be able to occupy a job slot for hours."""
    command = build_proxy(SOURCE, PROXY_OUT, display_height=DISPLAY_HEIGHT, duration_seconds=3600)

    assert command.dry_run().timeout_s == RENDER_TIMEOUT_FLOOR_S
    assert command.dry_run().timeout_s < command.timeout_s


def test_progress_reporting_is_not_part_of_the_recipe() -> None:
    """`-progress` changes what FFmpeg prints, never what it encodes.

    It is applied by the runner rather than a builder for exactly that reason:
    in the frozen argv it would look like a recipe change and force a
    params_version bump that produces identical bytes.
    """
    command = build_proxy(SOURCE, PROXY_OUT, display_height=DISPLAY_HEIGHT)
    reporting = command.reporting_progress()

    assert "-progress" not in command.argv
    assert reporting.argv[reporting.argv.index("-progress") + 1] == "pipe:1"
    assert "-nostats" in reporting.argv
    assert reporting.encode_arguments == command.encode_arguments
    assert reporting.filter_arguments == command.filter_arguments


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


# --- Sampled-frame extraction (amendment 008) ---------------------------------
#
# `build_frame_extraction`'s argv branches on whether the source is HDR, so it
# gets two frozen snapshots at one params_version rather than the single-branch
# snapshot every other recipe in `RECIPE_ARGV` has - both must move together
# with `FRAME_PARAMS_VERSION`, the same discipline as `test_every_recipe_argv_
# matches_its_params_version` above, just not expressed through that dict since
# frame extraction is not an `ArtifactKind` (amendment 008 resolution 2: it is
# never a `derived_artifacts` row).

FRAME_EXTRACTION_ARGV: dict[int, dict[str, list[str]]] = {
    1: {
        "sdr": [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            "1.500",
            "-i",
            SOURCE.as_posix(),
            "-frames:v",
            "1",
            "-map_metadata",
            "-1",
            "-c:v",
            "mjpeg",
            "-q:v",
            "2",
            "-an",
            FRAME_OUT.as_posix(),
        ],
        "hdr": [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            "1.500",
            "-i",
            SOURCE.as_posix(),
            "-vf",
            "zscale=tin=arib-std-b67:pin=bt2020:t=linear:npl=100,format=gbrpf32le,"
            "zscale=p=bt709,tonemap=tonemap=hable:desat=0,"
            "zscale=t=bt709:m=bt709:p=bt709:r=pc,format=yuv420p",
            "-frames:v",
            "1",
            "-map_metadata",
            "-1",
            "-c:v",
            "mjpeg",
            "-q:v",
            "2",
            "-an",
            FRAME_OUT.as_posix(),
        ],
    }
}


def test_frame_extraction_argv_matches_its_params_version() -> None:
    """A changed extraction recipe without a version bump fails here, saying so.

    Mirrors `test_every_recipe_argv_matches_its_params_version`'s own reasoning:
    `Scene.sampled_frame_path` is content-addressed under
    `sampled_frame/<params_version>/`, so an unbumped version keeps pointing at
    a directory holding bytes from the *previous* recipe.
    """
    snapshot = FRAME_EXTRACTION_ARGV.get(FRAME_PARAMS_VERSION)
    assert snapshot is not None, (
        f"FRAME_PARAMS_VERSION is {FRAME_PARAMS_VERSION} and FRAME_EXTRACTION_ARGV has "
        "no argv frozen at that version. If you bumped the version, add the new argv here "
        "in this same commit; keep the old entry, since frames rendered under it are still "
        "on disk."
    )
    sdr = build_frame_extraction(SOURCE, FRAME_OUT, timestamp_seconds=1.5)
    hdr = build_frame_extraction(
        SOURCE,
        FRAME_OUT,
        timestamp_seconds=1.5,
        color_primaries="bt2020",
        color_transfer="arib-std-b67",
    )
    assert sdr.argv == snapshot["sdr"], (
        "the SDR frame-extraction recipe changed but FRAME_PARAMS_VERSION is still "
        f"{FRAME_PARAMS_VERSION} - bump it in engine/repcut/analysis/params.py and freeze "
        "the new argv here, in the same commit."
    )
    assert hdr.argv == snapshot["hdr"], (
        "the HDR frame-extraction recipe changed but FRAME_PARAMS_VERSION is still "
        f"{FRAME_PARAMS_VERSION} - bump it in engine/repcut/analysis/params.py and freeze "
        "the new argv here, in the same commit."
    )


def test_frame_extraction_reads_the_source_never_scales_it() -> None:
    """The whole point of amendment 008: no `scale=` filter, ever.

    A resize here would make the dimension-equality gate criterion pass for
    the wrong reason - matching the proxy's own capped height rather than the
    source's real one.
    """
    sdr = build_frame_extraction(SOURCE, FRAME_OUT, timestamp_seconds=1.0)
    hdr = build_frame_extraction(
        SOURCE,
        FRAME_OUT,
        timestamp_seconds=1.0,
        color_primaries="bt2020",
        color_transfer="smpte2084",
    )

    # "scale=-2" (or any other dimension) is the actual resize filter every
    # other recipe in this file uses (`build_proxy`, `build_thumbnail_strip`);
    # `zscale=...` is the HDR chain's colour-conversion filter, and matching
    # bare "scale" would false-positive on its name.
    assert "scale=-2" not in " ".join(sdr.argv)
    assert "scale=-2" not in " ".join(hdr.argv)
    assert "-vf" not in sdr.argv


def test_frame_extraction_strips_metadata_with_negative_one_not_zero() -> None:
    """`-map_metadata 0` carries timed-metadata tracks and GPS side data onto the frame."""
    argv = build_frame_extraction(SOURCE, FRAME_OUT, timestamp_seconds=1.0).argv

    assert argv[argv.index("-map_metadata") + 1] == "-1"


def test_frame_extraction_seeks_input_side_before_the_dash_i() -> None:
    """`-ss` before `-i`, for speed - measured frame-accurate on this FFmpeg build."""
    argv = build_frame_extraction(SOURCE, FRAME_OUT, timestamp_seconds=2.5).argv

    assert argv.index("-ss") < argv.index("-i")
    assert argv[argv.index("-ss") + 1] == "2.500"


def test_frame_extraction_rejects_a_negative_timestamp() -> None:
    with pytest.raises(ValueError, match="timestamp_seconds"):
        build_frame_extraction(SOURCE, FRAME_OUT, timestamp_seconds=-0.1)


def test_frame_extraction_has_its_own_bounded_timeout_not_the_render_budget() -> None:
    """A single frame does not scale with clip duration the way a full render does."""
    command = build_frame_extraction(SOURCE, FRAME_OUT, timestamp_seconds=1.0)

    assert command.timeout_s == FRAME_EXTRACTION_TIMEOUT_S
    assert FRAME_EXTRACTION_TIMEOUT_S < RENDER_TIMEOUT_S


@pytest.mark.parametrize(
    ("color_primaries", "color_transfer", "expected"),
    [
        ("bt2020", "arib-std-b67", True),  # HLG
        ("bt2020", "smpte2084", True),  # PQ
        ("bt2020", None, True),  # wide gamut alone is still a positive signal
        (None, "arib-std-b67", True),  # transfer alone is still a positive signal
        ("BT2020", "ARIB-STD-B67", True),  # case-insensitive
        ("bt709", "bt709", False),
        (None, None, False),
        ("unknown", "unknown", False),
    ],
)
def test_source_is_hdr_reads_both_signals(
    color_primaries: str | None, color_transfer: str | None, expected: bool
) -> None:
    assert source_is_hdr(color_primaries, color_transfer) is expected


def test_only_an_hdr_source_pays_for_the_tonemap_filter() -> None:
    """`.claude/rules/ffmpeg.md`: never a tone-map applied unconditionally."""
    sdr = build_frame_extraction(SOURCE, FRAME_OUT, timestamp_seconds=1.0)
    hdr = build_frame_extraction(
        SOURCE,
        FRAME_OUT,
        timestamp_seconds=1.0,
        color_primaries="bt2020",
        color_transfer="smpte2084",
    )

    assert "-vf" not in sdr.argv
    assert "-vf" in hdr.argv
    assert "tonemap" in hdr.argv[hdr.argv.index("-vf") + 1]


# --- Scene audio energy (motion.py's own FFmpeg command) ---------------------


def test_audio_energy_probe_uses_input_side_ss_and_to() -> None:
    """`-to` after `-ss`, both before `-i`: an absolute input-timeline window."""
    argv = build_audio_energy_probe(SOURCE, start_seconds=1.0, end_seconds=3.5).argv

    assert argv.index("-ss") < argv.index("-i")
    assert argv.index("-to") < argv.index("-i")
    assert argv[argv.index("-ss") + 1] == "1.000"
    assert argv[argv.index("-to") + 1] == "3.500"


def test_audio_energy_probe_uses_info_loglevel_so_astats_actually_prints() -> None:
    """`astats` writes its summary at `av_log` info level - `error` would discard it."""
    argv = build_audio_energy_probe(SOURCE, start_seconds=0.0, end_seconds=1.0).argv

    assert argv[argv.index("-loglevel") + 1] == "info"


def test_audio_energy_probe_rejects_a_non_positive_span() -> None:
    with pytest.raises(ValueError, match="end_seconds"):
        build_audio_energy_probe(SOURCE, start_seconds=2.0, end_seconds=2.0)
    with pytest.raises(ValueError, match="end_seconds"):
        build_audio_energy_probe(SOURCE, start_seconds=2.0, end_seconds=1.0)


def test_audio_energy_probe_has_its_own_bounded_timeout() -> None:
    command = build_audio_energy_probe(SOURCE, start_seconds=0.0, end_seconds=2.0)

    assert command.timeout_s == AUDIO_ENERGY_PROBE_TIMEOUT_S
    assert AUDIO_ENERGY_PROBE_TIMEOUT_S < RENDER_TIMEOUT_S


@pytest.mark.parametrize(
    ("stderr", "expected"),
    [
        ("[Parsed_astats_0 @ 0x1] RMS level dB: -21.131171\n", -21.131171),
        (
            "[Parsed_astats_0 @ 0x1] Channel: 1\n"
            "[Parsed_astats_0 @ 0x1] RMS level dB: -18.000000\n"
            "[Parsed_astats_0 @ 0x1] Overall\n"
            "[Parsed_astats_0 @ 0x1] RMS level dB: -20.500000\n",
            -20.5,
        ),  # per-channel then Overall - the LAST match is the summary
        ("[Parsed_astats_0 @ 0x1] RMS level dB: -inf\n", float("-inf")),
        ("no audio stream, nothing to filter\n", None),
        ("", None),
    ],
)
def test_parse_overall_rms_db(stderr: str, expected: float | None) -> None:
    assert parse_overall_rms_db(stderr) == expected


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


async def test_an_empty_output_is_a_named_error_not_a_finished_artifact(
    make_clip: Callable[..., Path], tmp_path: Path
) -> None:
    """Exit 0 is not proof that a file was produced.

    Without the check, a zero-byte proxy is promoted into the content-addressed
    store under a key that says it is finished - so nothing regenerates it, the
    player is handed an unplayable file, and the artifact key is occupied by an
    absence. Simulated here by having FFmpeg exit 0 while writing to the null
    muxer, which is what "succeeded and produced nothing" looks like.
    """
    source = make_clip(seconds=1.0)
    destination = tmp_path / "empty.mp4"
    silent_success = replace(
        build_proxy(source, destination, display_height=360),
        container_arguments=("-f", "null"),
    )

    with pytest.raises(FFmpegEncodeError, match="produced no output"):
        await render(silent_success, dry_run_first=False)

    assert not destination.exists()
    assert _leftover_partials(tmp_path) == []


async def test_a_render_reports_monotonic_progress_from_ffmpeg(
    make_clip: Callable[..., Path], tmp_path: Path
) -> None:
    """The percentage in the UI comes from FFmpeg's own output position.

    Asserted against real FFmpeg rather than a fake stream: `-progress` writes
    `out_time_us` on stdout, and the whole point is that the parser reads what
    this version actually prints.
    """
    source = make_clip(seconds=3.0, audio=False)
    reported: list[float] = []

    await render(
        build_proxy(source, tmp_path / "proxy.mp4", display_height=360, duration_seconds=3.0),
        on_progress=reported.append,
        total_seconds=3.0,
    )

    assert reported, "no progress was reported at all"
    assert reported == sorted(reported)
    assert reported[0] >= 0.0 and reported[-1] <= 1.0
    assert reported[-1] > 0.5, f"progress stalled at {reported[-1]:.2f}"


async def test_progress_reporting_does_not_disturb_the_output(
    make_clip: Callable[..., Path], tmp_path: Path
) -> None:
    """`-progress` and `-nostats` must not change a single encoded byte.

    If they did, the frozen recipe argv would be describing something other than
    what gets rendered, and `params_version` would be keyed to a fiction.
    """
    source = make_clip(seconds=1.0, audio=False)

    quiet = await render(build_proxy(source, tmp_path / "quiet.mp4", display_height=360))
    loud = await render(
        build_proxy(source, tmp_path / "loud.mp4", display_height=360),
        on_progress=lambda _: None,
        total_seconds=1.0,
    )

    assert quiet.read_bytes() == loud.read_bytes()


async def test_a_missing_executable_is_a_named_error(
    make_clip: Callable[..., Path], tmp_path: Path
) -> None:
    """/health reports a missing FFmpeg; a job still needs a cause it can show."""
    command = build_proxy(
        make_clip(seconds=1.0), tmp_path / "x.mp4", display_height=360
    ).writing_to(tmp_path / "x.mp4")

    with pytest.raises(FFmpegNotInstalledError):
        await run(replace(command, executable="repcut-nonexistent-ffmpeg"))


async def test_a_loop_without_subprocesses_is_a_named_error(
    make_clip: Callable[..., Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The failure that shipped: ``NotImplementedError`` escaping ``run``.

    ``run`` caught ``FileNotFoundError`` and ``PermissionError`` only, so a
    Windows selector loop - which is what uvicorn builds under ``--reload`` -
    raised straight through the API as an unhandled 500. It is a named error
    now, and specifically an ``FFmpegUnavailableError``: that base is what tells
    ``finalize`` this says nothing about the clip, so the upload survives it.
    """

    async def _no_transport(*_: object, **__: object) -> None:
        raise NotImplementedError

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _no_transport)
    command = build_proxy(make_clip(seconds=1.0), tmp_path / "x.mp4", display_height=360)

    with pytest.raises(FFmpegLoopError) as raised:
        await run(command)

    assert isinstance(raised.value, FFmpegUnavailableError)
    assert "Users" not in raised.value.cause
    assert "Traceback" not in raised.value.cause


async def test_a_cancelled_render_kills_the_ffmpeg_process(
    make_clip: Callable[..., Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cancelling the task must stop the child, not merely stop waiting for it.

    ``asyncio.wait_for`` raises ``CancelledError`` into the awaiting task and
    leaves the subprocess untouched, so without an explicit kill a cancelled job
    reports ``cancelled`` while its encode runs on to completion - holding a
    core, the output handle and the job's temp file. That makes the engine's own
    shutdown path leak an encode too, which is what ``JobQueue._work`` says it
    prevents.
    """
    spawned: list[asyncio.subprocess.Process] = []
    running = asyncio.Event()
    real = asyncio.create_subprocess_exec

    async def _remember(*argv: str, **kwargs: object) -> asyncio.subprocess.Process:
        process = await real(*argv, **kwargs)  # type: ignore[arg-type]
        spawned.append(process)
        running.set()
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _remember)

    # Long enough that it cannot finish inside the window this test cancels in.
    source = make_clip("long.mp4", seconds=20.0, fps=30, width=1280, height=720)
    command = build_proxy(source, tmp_path / "out.mp4", display_height=720).writing_to(
        tmp_path / "out.mp4"
    )

    task = asyncio.create_task(run(command))
    await asyncio.wait_for(running.wait(), timeout=30.0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert spawned[0].returncode is not None, "ffmpeg outlived the job that started it"


# --- The child's stdin ---------------------------------------------------------
#
# A spawned FFmpeg inherits the engine's stdin unless told otherwise, and it
# reads stdin for interactive keys. The render recipes pass `-nostdin`, but that
# is an ffmpeg flag with no ffprobe equivalent, so the probe - the one command
# that runs on every single ingest - had no cover at all.

_STDIN_BUDGET_S = 60.0

# Runs in a child process whose stdin is a pipe the parent never closes, so the
# only thing that can stop a read of it is the engine declining to inherit it.
_PROBE_IN_CHILD = """
import sys
from pathlib import Path

from repcut.loop import event_loop
from repcut.media.ffmpeg_builder import build_probe, run


async def main() -> None:
    await run(build_probe(Path(sys.argv[1])))


loop = event_loop()
try:
    loop.run_until_complete(main())
finally:
    loop.close()
"""


async def test_the_spawn_never_hands_ffmpeg_the_engines_stdin(
    make_clip: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The kwarg itself, because it is the thing that can be deleted.

    Asserted separately from the end-to-end test below because this one fails
    deterministically on every platform the moment the argument goes missing,
    whereas whether an inherited descriptor actually blocks depends on the OS
    and on what the engine happened to be started from.
    """
    captured: dict[str, object] = {}
    real = asyncio.create_subprocess_exec

    async def _capture(*argv: str, **kwargs: object) -> asyncio.subprocess.Process:
        captured.update(kwargs)
        return await real(*argv, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _capture)

    await run(build_probe(make_clip(seconds=1.0)))

    # .get, not ["stdin"]: a deleted argument is the failure under test, and a
    # KeyError would report it as a broken test rather than a broken spawn.
    assert captured.get("stdin") == asyncio.subprocess.DEVNULL, (
        "the child inherited the engine's stdin"
    )


def test_a_probe_returns_under_a_parent_stdin_that_never_reaches_eof(
    make_clip: Callable[..., Path], tmp_path: Path
) -> None:
    """The behaviour the kwarg buys, exercised through a real spawn.

    The engine is started by a parent that gives it a pipe - `dev_stack` and the
    gate's own `boot` both use `subprocess.Popen` - so this is the shape the
    descriptor really arrives in, not a contrived one. The pipe here is never
    written to and never closed, so it never reaches EOF and a child that reads
    it has nothing to return.

    Synchronous on purpose: the point is a separate OS process with a stdin of
    this test's choosing, which is not something the running event loop can be
    asked for.
    """
    source = make_clip(seconds=1.0)
    log = tmp_path / "child.log"

    with log.open("wb") as sink:
        child = subprocess.Popen(
            [sys.executable, "-c", _PROBE_IN_CHILD, source.as_posix()],
            stdin=subprocess.PIPE,
            stdout=sink,
            stderr=subprocess.STDOUT,
        )
        try:
            returncode = child.wait(timeout=_STDIN_BUDGET_S)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
            pytest.fail(
                f"the probe did not return within {_STDIN_BUDGET_S}s - "
                "the child is sitting on the stdin it inherited"
            )
        finally:
            # After the wait: closing it first would supply the EOF the test is
            # withholding, and the premise with it.
            if child.stdin is not None:
                child.stdin.close()

    assert returncode == 0, log.read_text(encoding="utf-8", errors="replace")


# --- Frame extraction against real FFmpeg -------------------------------------


async def test_extracted_frame_matches_the_sources_own_dimensions(
    make_clip: Callable[..., Path], tmp_path: Path
) -> None:
    """Gate criterion 2: the sampled frame's size is the source's, not a proxy's.

    A 720p-capped proxy of this same clip would report a *different* height -
    the whole regression amendment 008 exists to prevent.
    """
    source = make_clip("wide.mp4", seconds=2.0, width=960, height=540, audio=False)

    frame = await render(
        build_frame_extraction(source, tmp_path / "frame.jpg", timestamp_seconds=1.0),
        dry_run_first=False,
    )

    dimensions = await _probe(frame, "width,height")
    assert int(dimensions["width"]) == 960
    assert int(dimensions["height"]) == 540


async def test_extracted_frame_carries_no_container_metadata(
    make_clip: Callable[..., Path], tmp_path: Path
) -> None:
    """Gate criterion 10: `-map_metadata -1`, verified on the rendered file.

    A throwaway tagged clip is built inline here rather than via the shared
    `make_clip` factory, which does not itself add arbitrary tags - this is
    the "build one in your own test file" path the brief names.
    """
    source = make_clip("plain.mp4", seconds=1.0, audio=False)
    tagged = tmp_path / "tagged.mp4"
    tag_process = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "error",
        "-y",
        "-i",
        source.as_posix(),
        "-c",
        "copy",
        "-metadata",
        "title=do-not-leak-this-title",
        tagged.as_posix(),
    )
    await tag_process.wait()
    assert tag_process.returncode == 0

    frame = await render(
        build_frame_extraction(tagged, tmp_path / "stripped.jpg", timestamp_seconds=0.2),
        dry_run_first=False,
    )

    probe = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v",
        "error",
        "-show_format",
        "-of",
        "json",
        frame.as_posix(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await probe.communicate()
    document = json.loads(stdout)
    assert "do-not-leak-this-title" not in json.dumps(document)


async def test_an_hdr_source_tonemaps_to_a_visibly_different_frame_than_no_filter(
    make_clip: Callable[..., Path], tmp_path: Path
) -> None:
    """Gate criterion 11: the HDR branch is a real pixel transform, not a label.

    Extracts the same timestamp from the same HDR-tagged fixture with and
    without the colour hints that trigger `source_is_hdr`, and asserts the two
    results actually differ - proving the filter graph does something, not
    only that it is present in the argv.
    """
    source = make_clip("hdr.mp4", seconds=2.0, width=640, height=360, audio=False, hdr=True)

    tonemapped = await render(
        build_frame_extraction(
            source,
            tmp_path / "tonemapped.jpg",
            timestamp_seconds=1.0,
            color_primaries="bt2020",
            color_transfer="arib-std-b67",
        ),
        dry_run_first=False,
    )
    naive = await render(
        build_frame_extraction(source, tmp_path / "naive.jpg", timestamp_seconds=1.0),
        dry_run_first=False,
    )

    tonemapped_bytes = await asyncio.to_thread(tonemapped.read_bytes)
    naive_bytes = await asyncio.to_thread(naive.read_bytes)
    assert tonemapped_bytes != naive_bytes


async def test_frame_extraction_falls_back_when_the_tonemap_filter_is_unavailable(
    make_clip: Callable[..., Path], tmp_path: Path
) -> None:
    """The documented fallback: an HDR-shaped filter graph FFmpeg rejects still fails typed.

    `zscale`/`tonemap` unavailability is simulated the same way an unrelated
    broken graph is elsewhere in this file - a filter FFmpeg cannot find -
    rather than by uninstalling `libzimg`, which is not this suite's to do.
    The caller that owns the actual fallback retry is `sampler.pick_frame`
    (`test_sampler.py`); this proves the failure it retries on is the typed
    error it expects, not a generic one.
    """
    source = make_clip("hdr2.mp4", seconds=1.0, audio=False)
    broken = replace(
        build_frame_extraction(
            source,
            tmp_path / "never.jpg",
            timestamp_seconds=0.5,
            color_primaries="bt2020",
            color_transfer="arib-std-b67",
        ),
        filter_arguments=("-vf", "definitely_not_a_filter=1"),
    )

    with pytest.raises(FFmpegFilterGraphError):
        await render(broken, dry_run_first=False)


# --- Audio energy probe against real FFmpeg -----------------------------------


async def test_audio_energy_probe_reports_a_louder_window_as_less_negative_db(
    tmp_path: Path,
) -> None:
    """Sanity check for `motion.py`'s own synchronous use of this same argv."""
    quiet = tmp_path / "quiet.mp4"
    loud = tmp_path / "loud.mp4"
    for destination, volume in ((quiet, 0.02), (loud, 0.9)):
        process = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:sample_rate=44100:duration=1,volume={volume}",
            "-c:a",
            "aac",
            destination.as_posix(),
        )
        await process.wait()
        assert process.returncode == 0

    async def _rms(path: Path) -> float | None:
        command = build_audio_energy_probe(path, start_seconds=0.0, end_seconds=1.0)
        process = await asyncio.create_subprocess_exec(
            *command.argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()
        assert process.returncode == 0
        return parse_overall_rms_db(stderr.decode("utf-8", errors="replace"))

    quiet_db = await _rms(quiet)
    loud_db = await _rms(loud)
    assert quiet_db is not None
    assert loud_db is not None
    assert loud_db > quiet_db
