"""Every FFmpeg and ffprobe invocation Repcut makes is built here.

Amendment 004 §2 fixes this path. The rule it implements
(`.claude/rules/ffmpeg.md`) is short and absolute: arguments are a ``list[str]``,
never a concatenated string, and never ``shell=True``. A command is therefore a
structured value - ``FFmpegCommand`` - and its ``argv`` is derived from the
parts rather than assembled by hand. Filter chains are composed programmatically
and asserted as strings in tests before any file is touched.

Three traps from the rule are handled here rather than left to callers:

- **VFR.** Phone footage is variable frame rate; beat sync, cut timing and
  interpolation all drift on it, invisibly, toward the end of a clip. The proxy
  is forced to CFR by the ``fps`` filter *and* ``-fps_mode cfr``.
- **Rotation.** Portrait video is usually landscape pixels plus a rotation tag.
  The builder never takes width and height from the container; it takes the
  *display* height the probe reported and lets ``scale=-2:h`` derive width from
  the decoded, already-rotated frame.
- **Colour.** ``bt709`` primaries, transfer, matrix and range are set explicitly
  on every encode. Left implicit, a grade shifts between preview and export and
  the difference is nearly invisible until someone compares them side by side.

Nothing here logs an absolute path. On this machine ``$DATA_DIR`` contains the
OS username (`.claude/rules/secrets.md`), and the same redaction is applied to
the one stderr line an error carries.
"""

import asyncio
import math
import os
import re
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, replace
from pathlib import Path
from uuid import uuid4

from repcut.analysis.params import FRAME_PARAMS_VERSION, FRAME_RECIPE, FrameRecipe
from repcut.logging import get_logger
from repcut.media.artifacts import (
    PARAMS_VERSION,
    PROXY_RECIPE,
    THUMBNAIL_STRIP_RECIPE,
    ArtifactKind,
    ProxyRecipe,
    ThumbnailStripRecipe,
)
from repcut.redaction import redact_paths

logger = get_logger(__name__)

# Called with a 0.0-1.0 fraction as FFmpeg reports it. Synchronous on purpose:
# it runs inside the stdout drain loop, and awaiting there would let a slow
# subscriber stall the pipe the loop exists to keep empty.
ProgressCallback = Callable[[float], None]

# The budget a render carries when the clip's duration is not known. A fixed
# render timeout is wrong in kind, not only in value - clip length is not fixed,
# so any constant is simultaneously too short for a long session and too long
# for a wedge. Callers that know the duration use `render_timeout_for`.
RENDER_TIMEOUT_S = 900.0

# Wall-clock budget per second of source. Measured on this laptop rather than
# guessed (docs/reports/prompt-02.md): the proxy recipe over 60s of synthetic
# H.264 runs at 0.13 s/s at 720p, 0.21 s/s at 1080p and 0.16 s/s at 2160p. The
# constant is ~30x the worst of those, which is the headroom real footage needs
# over the measurement: HEVC decodes several times slower than H.264, a 60fps
# source costs the fps filter more, and a thermally throttled laptop sharing the
# CPU is slower again. A timeout is a ceiling on "still working", not an
# estimate of it - too low turns a slow encode into a failure the user did not
# earn, which is the failure mode being avoided here.
RENDER_SECONDS_PER_SOURCE_SECOND = 6.0

# Short clips are dominated by process start, encoder init and muxer flush, none
# of which scale with duration.
RENDER_TIMEOUT_FLOOR_S = 120.0

# A probe reads container metadata and nothing else: it answers in under a
# second or the file cannot be read at all. Giving it the render budget means a
# truncated upload blocks an ingest job for fifteen minutes before failing.
# Bound to the command by `build_probe` rather than left to callers - a budget
# every caller has to remember to pass is a budget most callers will not pass.
PROBE_TIMEOUT_S = 60.0

# The plan is dry-run on this much of the clip before the real render. Long
# enough to build the graph, open the encoder and emit frames; short enough to
# be free.
DRY_RUN_SECONDS = 2

# A single frame extraction seeks to one keyframe and decodes forward to one
# target frame; it does not scale with the clip's duration the way a full
# render does, so it gets its own small fixed budget rather than
# `render_timeout_for` - the same reasoning as `PROBE_TIMEOUT_S`, sized larger
# to cover decoding forward across a long GOP on a throttled machine.
FRAME_EXTRACTION_TIMEOUT_S = 60.0

# The audio-energy probe reads one scene's worth of PCM through `astats` and
# writes to the null muxer; like the frame extraction above, its cost is set by
# the scene's own (short) duration, not the whole clip's.
AUDIO_ENERGY_PROBE_TIMEOUT_S = 60.0

# Never surface a raw stderr dump to the UI. One line, capped, redacted.
_MAX_DETAIL_CHARS = 200

# -y: the temp target may survive a crashed render, and a prompt would hang a
# background job forever. -nostdin for the same reason. -loglevel error keeps
# stderr to the lines classify_failure actually reads.
_GLOBAL_RENDER_ARGUMENTS = ("-hide_banner", "-nostdin", "-loglevel", "error", "-y")


class UnsafeSourceError(ValueError):
    """A source path FFmpeg would have interpreted as something other than a file."""


def _checked_source(source: Path) -> str:
    """The POSIX form of ``source``, refusing anything FFmpeg would not treat as a file.

    ``create_subprocess_exec`` means no shell, so nothing in a filename can be
    interpreted *by a shell*. FFmpeg, however, does its own parsing of the value
    after ``-i``, and two forms are not files at all:

    - **Protocols.** ``http://``, ``rtmp://``, ``concat:``, ``subfile:`` and
      friends make FFmpeg open a network or composite input. A source that
      reached the builder as a string from a database row, a resumed upload, or
      a future "import from URL" feature would silently become a request the
      engine makes on the attacker's behalf - server-side request forgery with
      the user's home network as the reachable surface, and a P4 violation the
      moment it succeeds.
    - **Leading dash.** ``-y`` as a filename is read as an *option*. Harmless
      here, confusing forever.

    Both are impossible from the content-addressed store today. This is the
    check that keeps them impossible when the store stops being the only caller.
    """
    text = source.as_posix()
    if text.startswith("-"):
        raise UnsafeSourceError("a source path may not begin with '-'")
    # A drive letter is `C:/…`; a protocol is `scheme://` or `scheme:` with a
    # multi-character scheme. Requiring 2+ characters keeps Windows paths valid.
    if re.match(r"\A[A-Za-z][A-Za-z0-9+.-]+:", text):
        raise UnsafeSourceError("a source path may not name a protocol")
    return text


class FFmpegError(RuntimeError):
    """An FFmpeg failure with a cause a person can read.

    ``cause`` is what the UI renders. ``detail`` is one redacted stderr line kept
    for the log - never a dump, and never a path.
    """

    def __init__(self, cause: str, detail: str = "") -> None:
        super().__init__(cause)
        self.cause = cause
        self.detail = detail


class FFmpegEncodeError(FFmpegError):
    """FFmpeg started and failed - encoder, muxer, disk, or a truncated source."""


class FFmpegFilterGraphError(FFmpegError):
    """The filter chain was rejected. A bug in the builder, not in the footage."""


class UnsupportedCodecError(FFmpegError):
    """The local FFmpeg build cannot read or write a codec this file needs."""


class FFmpegUnavailableError(FFmpegError):
    """FFmpeg could not be *run at all*. The machine's fault, never the clip's.

    The distinction this base draws is the one that was missing, and it decides
    what happens to a 2GB upload. ``_probe_or_reject`` used to catch
    ``FFmpegError`` flat and answer "this file is not a video" - so an engine
    that could not start a subprocess told the user their footage was broken and
    aborted the session, which on a completed transfer means re-sending every
    byte. An environment failure must leave the upload exactly where it was, and
    that is only expressible if the two kinds of failure are different types.

    Subclasses are conditions of the host: no executable, no permission to run
    it, an event loop that cannot spawn it. Nothing here says anything about the
    bytes being processed.
    """


class FFmpegNotInstalledError(FFmpegUnavailableError):
    """The executable is not on PATH. /health reports this before it matters."""


class FFmpegLoopError(FFmpegUnavailableError):
    """The running event loop cannot spawn subprocesses.

    Windows-specific and, until it was measured, invisible: ``asyncio``'s
    ``SelectorEventLoop`` inherits ``BaseEventLoop._make_subprocess_transport``,
    which raises ``NotImplementedError``. Every ``create_subprocess_exec`` in
    this module is therefore dead under that loop, and uvicorn selects it for
    the whole server whenever ``--reload`` is passed. See ``repcut.loop`` for
    the fix and for why the engine now chooses its own loop.

    Kept as a named error rather than left to escape: the loop is chosen once
    per process, so if this is raised once it will be raised for every clip, and
    an operator needs the cause rather than nine frames of traceback.
    """


class FFmpegTimeoutError(FFmpegError):
    """The process outlived its budget and was killed."""


# Matched case-insensitively against stderr, first hit wins. Ordered most
# specific first: a filter-graph failure often also mentions a codec.
_STDERR_CLASSES: tuple[tuple[tuple[str, ...], type[FFmpegError], str], ...] = (
    (
        (
            # Phrasings verified against ffmpeg 8.1 rather than remembered: the
            # wording moved between major versions ("filtergraph" became
            # "filterchain"), and a classifier matching only the old text
            # silently degrades every filter bug to the generic case.
            "no such filter",
            "filter not found",
            "error parsing filterchain",
            "error parsing filtergraph",
            "error parsing a filter description",
            "error initializing filter",
            "error initializing filters",
            "error reinitializing filters",
            "error initializing complex filters",
            "invalid filtergraph",
            "unable to parse graph",
        ),
        FFmpegFilterGraphError,
        "the video filter chain was rejected - this is a Repcut bug, not a problem with your clip",
    ),
    (
        (
            "unknown encoder",
            "unknown decoder",
            "encoder not found",
            "decoder not found",
            "unsupported codec",
            "automatic encoder selection failed",
            "error selecting an encoder",
        ),
        UnsupportedCodecError,
        "this clip uses a video or audio format the installed FFmpeg build cannot handle",
    ),
    (
        (
            "invalid data found when processing input",
            "moov atom not found",
            "could not find codec parameters",
        ),
        FFmpegEncodeError,
        "this file could not be read as video - it may be incomplete or corrupted",
    ),
    (
        ("no space left on device",),
        FFmpegEncodeError,
        "the drive holding the media store ran out of space",
    ),
)

_GENERIC_ENCODE_CAUSE = "FFmpeg could not finish processing this clip"


def render_timeout_for(duration_seconds: float | None) -> float:
    """The budget for rendering a clip of this length.

    ``None`` or a nonsense duration falls back to the fixed budget: a caller
    that does not know the length gets the old behaviour rather than a budget
    computed from a number nobody measured.
    """
    if duration_seconds is None or duration_seconds <= 0:
        return RENDER_TIMEOUT_S
    return max(RENDER_TIMEOUT_FLOOR_S, duration_seconds * RENDER_SECONDS_PER_SOURCE_SECOND)


def _detail(line: str) -> str:
    """One stderr line, redacted and capped. Never a dump, never a path."""
    return redact_paths(line.strip())[:_MAX_DETAIL_CHARS]


def classify_failure(stderr: str, returncode: int) -> FFmpegError:
    """Turn an FFmpeg exit into a typed error with a human-readable cause.

    ``detail`` is the line that identified the class, not the last line
    FFmpeg printed. FFmpeg ends with a summary - "Error opening output files:
    Invalid argument" - and the diagnostic that says what was actually wrong is
    several lines above it. Keeping the summary would throw away the only line
    worth reading.
    """
    lines = [line for line in stderr.splitlines() if line.strip()]
    for needles, error_type, cause in _STDERR_CLASSES:
        for line in lines:
            folded = line.casefold()
            if any(needle in folded for needle in needles):
                return error_type(cause, _detail(line))
    fallback = _detail(lines[0]) if lines else ""
    return FFmpegEncodeError(f"{_GENERIC_ENCODE_CAUSE} (exit {returncode})", fallback)


@dataclass(frozen=True, slots=True)
class FFmpegCommand:
    """One invocation, kept in parts so ``argv`` is derived and never concatenated.

    The split between ``encode_arguments`` and ``container_arguments`` is not
    cosmetic: ``dry_run`` keeps the first and drops the second, because muxer
    options like ``-movflags`` are rejected by the null muxer the dry run writes
    to.
    """

    executable: str
    global_arguments: tuple[str, ...]
    source: str
    filter_arguments: tuple[str, ...]
    encode_arguments: tuple[str, ...]
    container_arguments: tuple[str, ...]
    output: str
    kind: ArtifactKind | None = None
    params_version: int | None = None
    # How long this invocation may run. A property of the invocation, not of the
    # caller: a probe and a full encode differ by an order of magnitude, and the
    # builder is the only place that knows which one it just built.
    timeout_s: float = RENDER_TIMEOUT_S

    @property
    def argv(self) -> list[str]:
        """The full argument vector, as a list. Never a string, never a shell.

        An empty ``output`` contributes no token: ffprobe writes to stdout and
        takes no output operand, and an empty string in argv is a real argument
        that it would try to open.
        """
        tail = [self.output] if self.output else []
        return [
            self.executable,
            *self.global_arguments,
            "-i",
            self.source,
            *self.filter_arguments,
            *self.encode_arguments,
            *self.container_arguments,
            *tail,
        ]

    @property
    def loggable_argv(self) -> list[str]:
        """``argv`` with paths reduced to their final component."""
        return [redact_paths(token) for token in self.argv]

    def writing_to(self, output: Path) -> "FFmpegCommand":
        """The same command, aimed at a different file."""
        return replace(self, output=output.as_posix())

    def dry_run(self, seconds: int = DRY_RUN_SECONDS) -> "FFmpegCommand":
        """The same plan over the first ``seconds``, discarding the output.

        `.claude/rules/ffmpeg.md`: two-pass the *plan* before rendering the
        timeline, and fail fast. This builds the identical graph and encoder and
        throws the frames away, so a graph the builder got wrong costs two
        seconds rather than the whole clip.
        """
        return replace(
            self,
            container_arguments=("-t", str(seconds), "-f", "null"),
            output="-",
            # Two seconds of video cannot legitimately need the whole clip's
            # budget, and a dry run that wedges would otherwise hold a job slot
            # for as long as the render it was meant to make cheap.
            timeout_s=min(self.timeout_s, RENDER_TIMEOUT_FLOOR_S),
        )

    def reporting_progress(self) -> "FFmpegCommand":
        """The same command, asked to print machine-readable progress.

        ``-progress pipe:1`` writes ``key=value`` lines to stdout, which a render
        does not otherwise use. ``-nostats`` suppresses the human progress line
        on stderr, so stderr stays only the diagnostics ``classify_failure``
        reads. Applied by ``run`` when a caller wants progress, never by a
        builder - the frozen recipe argv describes the bytes produced, and a
        progress flag does not change them.
        """
        return replace(
            self,
            global_arguments=(*self.global_arguments, "-progress", "pipe:1", "-nostats"),
        )


def build_probe(source: Path, *, executable: str = "ffprobe") -> FFmpegCommand:
    """The one probe every ingest runs, as JSON on stdout.

    ``r_frame_rate`` and ``avg_frame_rate`` are both requested on purpose: they
    differ exactly when the source is variable frame rate, which is the default
    for phone footage and the root cause of drift that only shows up at the end
    of a clip. ``stream_side_data`` carries the rotation tag that makes a
    portrait video's stored width and height a lie.

    Every stream, not just ``v:0``: the audio sample rate has to be recorded at
    ingest, because concatenating segments muxed at different rates desyncs and
    by export it is too late to notice. One probe answering both questions beats
    two that can disagree about the same file. ``codec_type`` is what lets the
    parser tell the streams apart.

    ``color_primaries``/``color_transfer`` ride alongside the ``color_space``
    this already requested: free on an already-scheduled probe, and what
    ``metadata.parse_color_properties`` reads to decide whether a frame
    extraction needs to tone-map (amendment 008 resolution 3).
    """
    return FFmpegCommand(
        executable=executable,
        global_arguments=("-v", "error"),
        source=_checked_source(source),
        filter_arguments=(),
        encode_arguments=(
            "-show_entries",
            "stream=codec_type,codec_name,width,height,r_frame_rate,avg_frame_rate,"
            "nb_frames,pix_fmt,color_space,color_primaries,color_transfer,duration,"
            "sample_rate,channels",
            "-show_entries",
            "stream_side_data=rotation",
            "-show_entries",
            "format=duration,bit_rate,format_name",
            "-show_entries",
            "stream_tags=rotate",
        ),
        container_arguments=("-of", "json"),
        output="",
        timeout_s=PROBE_TIMEOUT_S,
    )


def _proxy_height(display_height: int, recipe: ProxyRecipe) -> int:
    """The proxy's height: the recipe's ceiling, never above the source's.

    Rounded down to an even number because ``yuv420p`` subsamples chroma by two
    and x264 rejects an odd dimension.
    """
    return min(recipe.height, display_height) // 2 * 2


def build_proxy(
    source: Path,
    destination: Path,
    *,
    display_height: int,
    duration_seconds: float | None = None,
    recipe: ProxyRecipe = PROXY_RECIPE,
    executable: str = "ffmpeg",
) -> FFmpegCommand:
    """The 720p CFR preview proxy.

    ``display_height`` comes from the probe, after rotation has been applied -
    never from the container's raw dimensions, which are landscape for most
    portrait phone video. Width is left to ``scale=-2``, so it follows the
    decoded frame and stays even.

    ``duration_seconds`` sets the timeout, not the recipe: it changes how long
    the command may run and nothing about the bytes, so it is absent from the
    frozen argv and does not touch ``params_version``.
    """
    height = _proxy_height(display_height, recipe)
    return FFmpegCommand(
        executable=executable,
        global_arguments=_GLOBAL_RENDER_ARGUMENTS,
        source=_checked_source(source),
        # scale before fps: resampling the frame rate is cheaper once the frames
        # are smaller, and the two are independent.
        filter_arguments=("-vf", f"scale=-2:{height},fps={recipe.fps}"),
        encode_arguments=(
            "-c:v",
            "libx264",
            "-preset",
            recipe.preset,
            "-crf",
            str(recipe.crf),
            "-pix_fmt",
            "yuv420p",
            # Explicit on every encode, or the grade shifts between preview and
            # export (.claude/rules/ffmpeg.md).
            "-colorspace",
            "bt709",
            "-color_primaries",
            "bt709",
            "-color_trc",
            "bt709",
            "-color_range",
            "tv",
            # Belt and braces with the fps filter above: the filter fixes the
            # cadence, this stops the muxer reintroducing variable timestamps.
            # `-fps_mode` rather than the deprecated `-vsync`.
            "-fps_mode",
            "cfr",
            "-c:a",
            "aac",
            "-b:a",
            recipe.audio_bitrate,
            "-ar",
            str(recipe.audio_sample_rate),
            "-ac",
            str(recipe.audio_channels),
        ),
        # Lets the browser player seek before the whole file has loaded.
        container_arguments=("-movflags", "+faststart"),
        output=destination.as_posix(),
        kind=ArtifactKind.PROXY,
        params_version=PARAMS_VERSION[ArtifactKind.PROXY],
        timeout_s=render_timeout_for(duration_seconds),
    )


def thumbnail_frame_count(duration_seconds: float, recipe: ThumbnailStripRecipe) -> int:
    """How many frames a strip holds: one per interval, and never zero.

    ``ceil`` because a 5-second clip at one frame per 2s has frames at 0, 2 and
    4 - the trailing partial interval still has a frame in it.
    """
    return max(1, math.ceil(duration_seconds / recipe.seconds_per_frame))


def build_thumbnail_strip(
    source: Path,
    destination: Path,
    *,
    duration_seconds: float,
    recipe: ThumbnailStripRecipe = THUMBNAIL_STRIP_RECIPE,
    executable: str = "ffmpeg",
) -> FFmpegCommand:
    """One tiled JPEG holding the whole clip, a frame every ``seconds_per_frame``.

    A single image rather than N files: the timeline scrubber loads one request,
    and one row in ``derived_artifacts`` describes it.
    """
    frames = thumbnail_frame_count(duration_seconds, recipe)
    return FFmpegCommand(
        executable=executable,
        global_arguments=_GLOBAL_RENDER_ARGUMENTS,
        source=_checked_source(source),
        filter_arguments=(
            "-vf",
            f"fps=1/{recipe.seconds_per_frame},scale=-2:{recipe.height},tile={frames}x1",
        ),
        encode_arguments=(
            "-frames:v",
            "1",
            # Named rather than inferred from the extension: the dry run writes
            # to the null muxer, which has no extension to infer from.
            "-c:v",
            "mjpeg",
            "-q:v",
            str(recipe.quality),
            "-an",
        ),
        container_arguments=(),
        output=destination.as_posix(),
        kind=ArtifactKind.THUMBNAIL_STRIP,
        params_version=PARAMS_VERSION[ArtifactKind.THUMBNAIL_STRIP],
        timeout_s=render_timeout_for(duration_seconds),
    )


# --- Sampled-frame extraction (amendment 008) --------------------------------
#
# The only two colour transfer characteristics real phone footage uses for HDR
# capture, spelled the way both ffprobe and zscale's `t=`/`tin=` name them:
# HLG (`arib-std-b67`) and PQ (`smpte2084`). `bt2020` primaries is included as a
# second, independent signal - real HLG/PQ footage carries both, but a source
# that only manages to tag one of the two still gets tone-mapped rather than
# sent to Gemini washed out.
_HDR_TRANSFERS = frozenset({"arib-std-b67", "smpte2084"})
_HDR_PRIMARIES = frozenset({"bt2020"})


def source_is_hdr(color_primaries: str | None, color_transfer: str | None) -> bool:
    """Whether a frame extracted from this source needs tone-mapping before anyone sees it.

    Deliberately conservative in one direction only: a *positive* match on
    either signal is enough to trigger the real conversion chain, because a
    false negative here is a washed-out frame attributed to the camera (bad,
    but the source really was HDR and nothing was invented or lost) while a
    false positive spends a real pixel transform - `zscale`+`tonemap` - on
    footage that did not need it. Untagged, unknown, or plain `bt709` source is
    the overwhelmingly common case and must not pay that cost or that risk of
    altering already-correct footage (`.claude/rules/ffmpeg.md`: "do not apply
    a tone-map filter unconditionally").
    """
    transfer = (color_transfer or "").strip().casefold()
    primaries = (color_primaries or "").strip().casefold()
    return transfer in _HDR_TRANSFERS or primaries in _HDR_PRIMARIES


def _hdr_tonemap_filter(
    color_primaries: str | None, color_transfer: str | None, tone_map_target: str
) -> str:
    """``zscale``+``tonemap``: linearize, tone-map to SDR range, land on ``tone_map_target``.

    Five stages, verified end to end against a real HLG/BT.2020-tagged fixture
    before landing (session report has the measurements):

    1. ``zscale=...:t=linear:npl=100`` - decode-side primaries/transfer are
       passed through explicitly (``tin=``/``pin=``) when known, rather than
       trusted to the decoder's own frame-side-data a second time, since this
       is the same read `source_is_hdr` already made its decision from. Range
       and matrix are left to ``zscale``'s own input auto-detection
       (``rin=``/``min=`` default to ``input``) - measured to agree with an
       explicit hint to within floating rounding (<1/255 per channel), so
       nothing is gained by guessing a matrix from the primaries alone.
       ``npl=100`` is the nominal peak luminance SDR target zscale assumes.
    2. ``format=gbrpf32le`` - float RGB, what ``tonemap`` operates in.
    3. ``zscale=p={target}`` - primaries to the output gamut before tone-mapping,
       so the operator compresses luminance in the gamut it will be displayed in.
    4. ``tonemap=tonemap=hable:desat=0`` - the Hable filmic operator with
       desaturation disabled, the commonly recommended default: `desat`
       trades saturated highlights for perceived brightness accuracy, which
       is the wrong trade for a frame a still-image classifier is about to
       read colour and lighting off.
    5. ``zscale=t=...:m=...:p=...:r=pc,format=yuv420p`` - full transfer/matrix/
       primaries conversion to the target, **full-range** output. Measured
       against `mjpeg`'s own encoder: `-color_range tv` as an *output flag* is
       rejected outright ("Non full-range YUV is non-standard"), and JPEG/JFIF
       has no limited-range convention to begin with, so `r=pc` is not a
       stylistic choice - `r=tv` here would encode limited-range sample values
       into a format that reads every sample as full-range, a quiet double-dip
       into the very range mismatch `.claude/rules/ffmpeg.md` warns about.
    """
    to_linear = ["t=linear", "npl=100"]
    if color_primaries is not None:
        to_linear.insert(0, f"pin={color_primaries}")
    if color_transfer is not None:
        to_linear.insert(0, f"tin={color_transfer}")
    return (
        f"zscale={':'.join(to_linear)},"
        "format=gbrpf32le,"
        f"zscale=p={tone_map_target},"
        "tonemap=tonemap=hable:desat=0,"
        f"zscale=t={tone_map_target}:m={tone_map_target}:p={tone_map_target}:r=pc,"
        "format=yuv420p"
    )


def build_frame_extraction(
    source: Path,
    destination: Path,
    *,
    timestamp_seconds: float,
    color_primaries: str | None = None,
    color_transfer: str | None = None,
    recipe: FrameRecipe = FRAME_RECIPE,
    executable: str = "ffmpeg",
) -> FFmpegCommand:
    """One JPEG frame, read from the SOURCE - never the proxy (amendment 008 resolution 3).

    ``color_primaries``/``color_transfer`` are the caller's own probe result
    (``metadata.parse_color_properties``), not re-probed here: a builder stays
    a pure function of its arguments, the same reason `build_proxy` takes
    ``display_height`` pre-computed rather than probing the source itself.
    Passing them decides, via `source_is_hdr`, whether the filter graph is the
    real `zscale`+`tonemap` conversion chain or nothing at all - never a
    tone-map applied unconditionally.

    No scaling filter, ever: the output's dimensions must equal the source's
    own display dimensions exactly, which is the entire reason extraction reads
    the source instead of the already-406-pixels-narrow proxy (amendment 008 /
    `docs/future-prompts/prompt-03-frame-source.md`).

    ``-map_metadata -1`` strips everything the container carries - timed
    metadata tracks, ambient-viewing-environment side data, GPS - never
    ``-map_metadata 0``, which would carry all of it onto a frame P4 requires
    to leave the machine with nothing but the picture.

    ``-ss`` is placed **before** ``-i`` (input-side seeking) for speed. Measured
    frame-identical against a full decode plus `select=eq(n,N)` ground truth on
    this build of FFmpeg (8.x): input-side seeking finds the nearest keyframe
    and then decodes forward to the exact requested time by default
    (`-accurate_seek` is FFmpeg's own default), so nothing is traded for the
    speed here - see the session report for the measurement.
    """
    if timestamp_seconds < 0:
        raise ValueError("timestamp_seconds must not be negative")

    filter_arguments: tuple[str, ...] = ()
    if source_is_hdr(color_primaries, color_transfer):
        filter_arguments = (
            "-vf",
            _hdr_tonemap_filter(color_primaries, color_transfer, recipe.tone_map_target),
        )

    return FFmpegCommand(
        executable=executable,
        global_arguments=(*_GLOBAL_RENDER_ARGUMENTS, "-ss", f"{timestamp_seconds:.3f}"),
        source=_checked_source(source),
        filter_arguments=filter_arguments,
        encode_arguments=(
            "-frames:v",
            "1",
            "-map_metadata",
            "-1",
            # Named rather than inferred from the extension: a candidate is
            # rendered to a temp name whose suffix `render` controls, and the
            # dry run (if a caller ever runs one) writes to the null muxer,
            # which has no extension to infer from either.
            "-c:v",
            "mjpeg",
            "-q:v",
            str(recipe.quality),
            "-an",
        ),
        container_arguments=(),
        output=destination.as_posix(),
        params_version=FRAME_PARAMS_VERSION,
        timeout_s=FRAME_EXTRACTION_TIMEOUT_S,
    )


# --- Scene audio energy (motion.py) -------------------------------------------

# `RMS level dB:` appears once per channel and once more for the `Overall`
# summary, which is always printed last - so the last match is always the
# summary regardless of channel count. `-inf` is `astats`' own spelling of true
# digital silence, and `float("-inf")` parses it without a special case.
_RMS_LEVEL_PATTERN = re.compile(r"RMS level dB:\s*(-?inf|-?[\d.]+)")


def parse_overall_rms_db(stderr: str) -> float | None:
    """The clip's overall RMS level in dB, or None if `astats` printed nothing.

    No match is not an error: a scene with no audio stream at all produces
    exactly this - `-af astats` finds nothing to filter and FFmpeg exits 0
    having written silence, not a diagnostic. The caller (`motion.py`) reads
    that as zero audio energy rather than a failure.
    """
    matches = _RMS_LEVEL_PATTERN.findall(stderr)
    return float(matches[-1]) if matches else None


def build_audio_energy_probe(
    source: Path,
    *,
    start_seconds: float,
    end_seconds: float,
    executable: str = "ffmpeg",
) -> FFmpegCommand:
    """RMS level of one time range's audio, read from `astats`' own stderr summary.

    Deliberately not an artifact-producing command: output is the null muxer,
    and the only thing the caller reads is stderr, parsed by
    `parse_overall_rms_db`. ``-ss``/``-to`` are both input-side options here, so
    ``-to`` is an absolute position on the input's own timeline (matching
    `start_seconds`/`end_seconds`, which are already absolute against the
    proxy) rather than a duration relative to ``-ss``.

    ``-loglevel info`` rather than this module's usual ``error``: `astats`
    writes its summary through `av_log` at *info* severity, so the quieter
    level every render uses would silently discard the one line this command
    exists to produce.
    """
    if end_seconds <= start_seconds:
        raise ValueError("end_seconds must be after start_seconds")
    return FFmpegCommand(
        executable=executable,
        global_arguments=(
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "info",
            "-y",
            "-ss",
            f"{start_seconds:.3f}",
            "-to",
            f"{end_seconds:.3f}",
        ),
        source=_checked_source(source),
        filter_arguments=("-af", "astats=metadata=0:reset=0"),
        encode_arguments=(),
        container_arguments=("-f", "null"),
        output="-",
        timeout_s=AUDIO_ENERGY_PROBE_TIMEOUT_S,
    )


def _progress_fraction(line: str, total_seconds: float) -> float | None:
    """Read one ``-progress`` line into a completed fraction, or None.

    FFmpeg emits a block of ``key=value`` lines per update; ``out_time_us`` is
    the only one worth reading. It reports ``N/A`` before the first frame is
    muxed, which is not a number and not an error.
    """
    key, _, value = line.strip().partition("=")
    if key != "out_time_us":
        return None
    try:
        microseconds = float(value)
    except ValueError:
        # Named: FFmpeg's "N/A", printed until the first frame lands.
        return None
    return min(1.0, max(0.0, microseconds / 1_000_000 / total_seconds))


async def _read_progress(
    stream: asyncio.StreamReader, total_seconds: float, on_progress: ProgressCallback
) -> bytes:
    """Drain stdout, reporting progress. Returns the raw bytes read.

    Draining matters even when nothing subscribes to the output: a full pipe
    buffer blocks FFmpeg, and a blocked FFmpeg looks exactly like a wedge until
    the timeout fires.
    """
    collected = bytearray()
    async for raw in stream:
        collected += raw
        fraction = _progress_fraction(raw.decode("utf-8", errors="replace"), total_seconds)
        if fraction is not None:
            on_progress(fraction)
    return bytes(collected)


async def _collect_output(
    process: asyncio.subprocess.Process,
    on_progress: ProgressCallback | None,
    total_seconds: float | None,
) -> tuple[bytes, bytes]:
    """Drain stdout and stderr concurrently and wait for exit.

    Concurrently is the only safe order: reading one pipe to completion while
    the other fills blocks FFmpeg on a write it can never finish, and a blocked
    FFmpeg is indistinguishable from a wedged one until the timeout fires.
    """
    if (
        on_progress is None
        or total_seconds is None
        or process.stdout is None
        or process.stderr is None
    ):
        return await process.communicate()

    stdout, stderr, _ = await asyncio.gather(
        _read_progress(process.stdout, total_seconds, on_progress),
        process.stderr.read(),
        process.wait(),
    )
    return stdout, stderr


async def _stop(process: asyncio.subprocess.Process) -> None:
    """Kill a child and reap it, tolerating one that has already exited.

    ``Process.kill`` raises ``ProcessLookupError`` when the child died between
    the decision to stop it and the call - a real race, because a timeout and a
    natural exit can land in the same millisecond. Reaping it either way is what
    keeps a zombie out of the process table.
    """
    with suppress(ProcessLookupError):
        process.kill()
    await process.wait()


async def run(
    command: FFmpegCommand,
    *,
    timeout_s: float | None = None,
    on_progress: ProgressCallback | None = None,
    total_seconds: float | None = None,
) -> str:
    """Execute ``command`` and return stdout, raising a typed error on failure.

    ``create_subprocess_exec`` takes the argv list directly - there is no shell
    anywhere on this path, so nothing in a filename can be interpreted.

    The budget comes from the command unless the caller overrides it, so a probe
    gets ``PROBE_TIMEOUT_S`` without anyone having to remember to ask for it.

    ``on_progress`` needs ``total_seconds`` to turn FFmpeg's elapsed output time
    into a fraction; without a duration to divide by there is no percentage to
    report, so the command runs unreported rather than reporting a made-up one.
    """
    budget = command.timeout_s if timeout_s is None else timeout_s
    reporting = on_progress is not None and total_seconds is not None and total_seconds > 0
    if reporting:
        command = command.reporting_progress()
    logger.debug("ffmpeg_invocation", argv=command.loggable_argv)

    try:
        process = await asyncio.create_subprocess_exec(
            *command.argv,
            # Inheriting the engine's stdin hands the child whatever descriptor
            # the engine was started with. FFmpeg reads stdin for interactive
            # keys, so an inherited pipe that never reaches EOF is a descriptor
            # it can sit on, and an inherited terminal is one it steals input
            # from - on POSIX a backgrounded read there takes SIGTTIN and stops
            # the job outright. `-nostdin` covers the render commands, but it is
            # an ffmpeg flag and ffprobe has no equivalent, so the probe path
            # has no such cover. DEVNULL closes both at the spawn, which is the
            # one place that cannot be forgotten per-recipe.
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as error:
        # Named: the executable is not on PATH. /health already reports this, so
        # the job needs a cause it can show, not a traceback.
        raise FFmpegNotInstalledError(
            f"{command.executable} is not installed or not on PATH"
        ) from error
    except PermissionError as error:
        # Named: the binary exists but this user may not execute it.
        raise FFmpegNotInstalledError(
            f"{command.executable} is present but not executable by this user"
        ) from error
    except NotImplementedError as error:
        # Named: the running event loop has no subprocess transport. Raised from
        # BaseEventLoop._make_subprocess_transport, which is what a Windows
        # SelectorEventLoop inherits. Nothing about the command is wrong and
        # retrying it will not help - the loop is fixed for the life of the
        # process - so this has to arrive as a cause, not as a 500.
        logger.error(
            "event_loop_cannot_spawn_subprocesses",
            loop=type(asyncio.get_running_loop()).__name__,
            fix="start the engine with `make dev` (python -m repcut), which selects a usable loop",
        )
        raise FFmpegLoopError(
            "the engine cannot start video tools in its current configuration - "
            "restart it with `make dev`"
        ) from error

    try:
        stdout, stderr = await asyncio.wait_for(
            _collect_output(process, on_progress if reporting else None, total_seconds),
            timeout=budget,
        )
    except TimeoutError:
        # Named: a wedged encode. Kill it, or it holds the file handle and the
        # job slot until the engine restarts.
        await _stop(process)
        logger.warning("ffmpeg_timeout", timeout_s=budget, executable=command.executable)
        raise FFmpegTimeoutError(
            f"processing took longer than {int(budget)}s and was stopped"
        ) from None
    except asyncio.CancelledError:
        # Named: the job was cancelled - a user pressed Cancel, or the engine is
        # shutting down. Cancelling the *task* does not stop the child: without
        # this the encode runs to completion, orphaned, still holding a core and
        # the output handle, and "cancelled" in the UI would be a lie.
        await _stop(process)
        logger.info("ffmpeg_cancelled", executable=command.executable)
        raise

    if process.returncode != 0:
        failure = classify_failure(
            stderr.decode("utf-8", errors="replace"), process.returncode or -1
        )
        logger.warning(
            "ffmpeg_failed",
            returncode=process.returncode,
            error_type=type(failure).__name__,
            detail=failure.detail,
        )
        raise failure

    return stdout.decode("utf-8", errors="replace")


def _finalise(temp: Path, final: Path) -> None:
    """Move a finished render onto its final name. Blocking; call in a thread."""
    os.replace(temp, final)


def temp_target(final: Path) -> Path:
    """A temp path beside ``final``, unique per render, real suffix last.

    Two properties, both load-bearing:

    - **The suffix stays last.** FFmpeg picks the muxer from the output
      extension, and a name ending ``.partial`` is an extension it does not
      know - the render fails with "Error opening output files: Invalid
      argument", which reads like a permissions problem and is not one.
    - **The token is random.** Derived purely from the target, this name would
      collide exactly when the target does, and the store is content-addressed:
      two projects uploading the same clip, or a retry racing a job that is not
      dead yet, both resolve to the same ``(sha256, kind, params_version)``.
      Sharing the temp name means one render deleting or overwriting a file the
      other has open - on Windows a ``PermissionError``, which is not an
      ``FFmpegError`` and would reach the UI as a traceback. Dedup makes that
      collision more likely, not less.
    """
    return final.with_name(f".{final.stem}.{uuid4().hex[:8]}.partial{final.suffix}")


def _prepare(temp: Path) -> None:
    """Make the destination directory. Blocking; call in a thread.

    Nothing is unlinked here. ``temp_target`` never returns a name that already
    exists, and unlinking a *sibling* partial would delete the file a concurrent
    render is writing. Partials left behind by a killed process are orphans, and
    orphan collection for the content-addressed store is Prompt 12's per
    amendment 004.
    """
    temp.parent.mkdir(parents=True, exist_ok=True)


def _written_size(temp: Path) -> int:
    """Size of a finished render, or -1 when FFmpeg exited 0 without a file."""
    try:
        return temp.stat().st_size
    except OSError:
        # Named: the output is absent. FFmpeg exiting 0 with no file is rare but
        # real - a filter that emitted no frames, a muxer that wrote nothing.
        return -1


async def render(
    command: FFmpegCommand,
    *,
    timeout_s: float | None = None,
    dry_run_first: bool = True,
    on_progress: ProgressCallback | None = None,
    total_seconds: float | None = None,
) -> Path:
    """Validate the plan, render to a temp file, then move it into place.

    Three properties the rule requires, all structural rather than best-effort:

    - **Fail fast.** The filter graph runs over a two-second slice first, so a
      graph this module built wrong costs two seconds instead of a whole clip.
      Set ``dry_run_first=False`` only when the caller has already validated it.
    - **Resumable.** The encode writes to a unique temp name beside the target
      and is renamed onto it only after FFmpeg exits 0. A killed render leaves a
      partial file with a name nothing reads, never a truncated file at a path
      that looks finished. ``os.replace`` is atomic within a filesystem, and the
      temp file is in the destination directory to guarantee that.
    - **Exit 0 is not proof.** The output is checked for existence and for a
      non-zero size before it is moved into place. Without that check a
      zero-byte proxy is promoted to the content-addressed store under a key
      that says it is finished, and the failure surfaces later as an unplayable
      file nothing will regenerate - the artifact key is occupied.
    """
    final = Path(command.output)
    temp = temp_target(final)

    await asyncio.to_thread(_prepare, temp)

    if dry_run_first:
        await run(command.dry_run(), timeout_s=timeout_s)

    try:
        await run(
            command.writing_to(temp),
            timeout_s=timeout_s,
            on_progress=on_progress,
            total_seconds=total_seconds,
        )
        size_bytes = await asyncio.to_thread(_written_size, temp)
        if size_bytes <= 0:
            logger.warning("ffmpeg_empty_output", size_bytes=size_bytes)
            raise FFmpegEncodeError("FFmpeg reported success but produced no output for this clip")
    except FFmpegError:
        # The partial is worthless and its name is unguessable; leaving it would
        # accumulate garbage in the media store on every failure.
        await asyncio.to_thread(temp.unlink, True)
        raise

    await asyncio.to_thread(_finalise, temp, final)
    logger.debug(
        "artifact_rendered",
        kind=command.kind.value if command.kind else None,
        params_version=command.params_version,
        size_bytes=size_bytes,
    )
    return final


__all__ = [
    "AUDIO_ENERGY_PROBE_TIMEOUT_S",
    "DRY_RUN_SECONDS",
    "FRAME_EXTRACTION_TIMEOUT_S",
    "PROBE_TIMEOUT_S",
    "RENDER_SECONDS_PER_SOURCE_SECOND",
    "RENDER_TIMEOUT_FLOOR_S",
    "RENDER_TIMEOUT_S",
    "FFmpegCommand",
    "FFmpegEncodeError",
    "FFmpegError",
    "FFmpegFilterGraphError",
    "FFmpegLoopError",
    "FFmpegNotInstalledError",
    "FFmpegTimeoutError",
    "FFmpegUnavailableError",
    "ProgressCallback",
    "UnsafeSourceError",
    "UnsupportedCodecError",
    "build_audio_energy_probe",
    "build_frame_extraction",
    "build_probe",
    "build_proxy",
    "build_thumbnail_strip",
    "classify_failure",
    "parse_overall_rms_db",
    "redact_paths",
    "render",
    "render_timeout_for",
    "run",
    "source_is_hdr",
    "temp_target",
    "thumbnail_frame_count",
]
