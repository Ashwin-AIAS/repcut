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
from dataclasses import dataclass, replace
from pathlib import Path

from repcut.logging import get_logger
from repcut.media.artifacts import (
    PARAMS_VERSION,
    PROXY_RECIPE,
    THUMBNAIL_STRIP_RECIPE,
    ArtifactKind,
    ProxyRecipe,
    ThumbnailStripRecipe,
)

logger = get_logger(__name__)

# Long enough for a slow x264 pass over a multi-minute clip on this laptop,
# short enough that a wedged process is not mistaken for progress.
RENDER_TIMEOUT_S = 900.0
PROBE_TIMEOUT_S = 60.0

# The plan is dry-run on this much of the clip before the real render. Long
# enough to build the graph, open the encoder and emit frames; short enough to
# be free.
DRY_RUN_SECONDS = 2

# Never surface a raw stderr dump to the UI. One line, capped, redacted.
_MAX_DETAIL_CHARS = 200

# -y: the temp target may survive a crashed render, and a prompt would hang a
# background job forever. -nostdin for the same reason. -loglevel error keeps
# stderr to the lines classify_failure actually reads.
_GLOBAL_RENDER_ARGUMENTS = ("-hide_banner", "-nostdin", "-loglevel", "error", "-y")

# Two or more path segments, optionally drive-qualified. Deliberately requires
# two so that filter arguments like `fps=1/2` are left alone.
_PATH_LIKE = re.compile(r"(?:[A-Za-z]:)?(?:[\\/][^\\/\s'\"]+){2,}")


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


class FFmpegNotInstalledError(FFmpegError):
    """The executable is not on PATH. /health reports this before it matters."""


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


def redact_paths(text: str) -> str:
    """Replace path-like runs with their final component only.

    Stored filenames are derived from the content hash or the session id, never
    from the user's filename (amendment 004), so the last segment is safe to
    keep and is the only part worth reading in a log.
    """
    return _PATH_LIKE.sub(
        lambda match: f".../{match.group(0).rsplit('/', 1)[-1]}", text.replace("\\", "/")
    )


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
        )


def build_probe(source: Path, *, executable: str = "ffprobe") -> FFmpegCommand:
    """The one probe every ingest runs, as JSON on stdout.

    ``r_frame_rate`` and ``avg_frame_rate`` are both requested on purpose: they
    differ exactly when the source is variable frame rate, which is the default
    for phone footage and the root cause of drift that only shows up at the end
    of a clip. ``stream_side_data`` carries the rotation tag that makes a
    portrait video's stored width and height a lie.
    """
    return FFmpegCommand(
        executable=executable,
        global_arguments=("-v", "error", "-select_streams", "v:0"),
        source=source.as_posix(),
        filter_arguments=(),
        encode_arguments=(
            "-show_entries",
            "stream=codec_name,width,height,r_frame_rate,avg_frame_rate,nb_frames,"
            "pix_fmt,color_space,duration",
            "-show_entries",
            "stream_side_data=rotation",
            "-show_entries",
            "format=duration,bit_rate,format_name",
            "-show_entries",
            "stream_tags=rotate",
        ),
        container_arguments=("-of", "json"),
        output="",
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
    recipe: ProxyRecipe = PROXY_RECIPE,
    executable: str = "ffmpeg",
) -> FFmpegCommand:
    """The 720p CFR preview proxy.

    ``display_height`` comes from the probe, after rotation has been applied -
    never from the container's raw dimensions, which are landscape for most
    portrait phone video. Width is left to ``scale=-2``, so it follows the
    decoded frame and stays even.
    """
    height = _proxy_height(display_height, recipe)
    return FFmpegCommand(
        executable=executable,
        global_arguments=_GLOBAL_RENDER_ARGUMENTS,
        source=source.as_posix(),
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
        source=source.as_posix(),
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
    )


async def run(command: FFmpegCommand, *, timeout_s: float = RENDER_TIMEOUT_S) -> str:
    """Execute ``command`` and return stdout, raising a typed error on failure.

    ``create_subprocess_exec`` takes the argv list directly - there is no shell
    anywhere on this path, so nothing in a filename can be interpreted.
    """
    logger.debug("ffmpeg_invocation", argv=command.loggable_argv)

    try:
        process = await asyncio.create_subprocess_exec(
            *command.argv,
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

    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_s)
    except TimeoutError:
        # Named: a wedged encode. Kill it, or it holds the file handle and the
        # job slot until the engine restarts.
        process.kill()
        await process.wait()
        logger.warning("ffmpeg_timeout", timeout_s=timeout_s, executable=command.executable)
        raise FFmpegTimeoutError(
            f"processing took longer than {int(timeout_s)}s and was stopped"
        ) from None

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


def _prepare(temp: Path) -> None:
    """Make the destination directory and clear any leftover partial render."""
    temp.parent.mkdir(parents=True, exist_ok=True)
    temp.unlink(missing_ok=True)


async def render(
    command: FFmpegCommand,
    *,
    timeout_s: float = RENDER_TIMEOUT_S,
    dry_run_first: bool = True,
) -> Path:
    """Validate the plan, render to a temp file, then move it into place.

    Two properties the rule requires, both structural rather than best-effort:

    - **Fail fast.** The filter graph runs over a two-second slice first, so a
      graph this module built wrong costs two seconds instead of a whole clip.
      Set ``dry_run_first=False`` only when the caller has already validated it.
    - **Resumable.** The encode writes to a dotted temp name beside the target
      and is renamed onto it only after FFmpeg exits 0. A killed render leaves a
      partial file with a name nothing reads, never a truncated file at a path
      that looks finished. ``os.replace`` is atomic within a filesystem, and the
      temp file is in the destination directory to guarantee that.
    """
    final = Path(command.output)
    # The suffix has to stay last. FFmpeg picks the muxer from the output
    # extension, and a name ending `.partial` is an extension it does not know -
    # the render fails with "Error opening output files: Invalid argument",
    # which reads like a permissions problem and is not one.
    temp = final.with_name(f".{final.stem}.partial{final.suffix}")

    await asyncio.to_thread(_prepare, temp)

    if dry_run_first:
        await run(command.dry_run(), timeout_s=timeout_s)

    try:
        await run(command.writing_to(temp), timeout_s=timeout_s)
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
    )
    return final


__all__ = [
    "DRY_RUN_SECONDS",
    "PROBE_TIMEOUT_S",
    "RENDER_TIMEOUT_S",
    "FFmpegCommand",
    "FFmpegEncodeError",
    "FFmpegError",
    "FFmpegFilterGraphError",
    "FFmpegNotInstalledError",
    "FFmpegTimeoutError",
    "UnsupportedCodecError",
    "build_probe",
    "build_proxy",
    "build_thumbnail_strip",
    "classify_failure",
    "redact_paths",
    "render",
    "run",
    "thumbnail_frame_count",
]
