"""Turn one ffprobe JSON document into the properties ``media_blobs`` stores.

Three traps from `.claude/rules/ffmpeg.md` are decided here, because this is the
only place that sees the raw probe:

- **Rotation.** Portrait phone video is landscape pixels plus a rotation tag.
  ``width``/``height`` from ffprobe are the *coded* dimensions; the display
  dimensions are those swapped when the rotation is a quarter turn. Everything
  downstream - the proxy's ``scale=-2:h``, the library thumbnail, the player's
  aspect ratio - reads the display pair.
- **Audio rate.** Recorded at ingest, because segments muxed at different rates
  desync on concat and by export it is too late.
- **VFR.** See ``detect_variable_frame_rate``. This is the one that returns
  ``None``, and the reason is worth reading before touching it.
"""

from dataclasses import dataclass
from typing import Any

from repcut.logging import get_logger

logger = get_logger(__name__)

# Containers where ffprobe derives avg_frame_rate from the packets' own
# timestamps, so `r_frame_rate != avg_frame_rate` is a usable VFR signal.
#
# Measured, not assumed (docs/reports/prompt-02.md): one 52-frame clip with
# deliberately uneven timestamps, muxed both ways. MP4 reports r=30/1 and
# avg=1560/121; Matroska reports 30/1 for both. Same frames, same timestamps,
# opposite answers - Matroska's demuxer hands back the nominal rate, so the
# heuristic there is a false *negative*, not an answer.
_VFR_ANSWERABLE_FORMATS = frozenset({"mov", "mp4", "m4a", "3gp", "3g2", "mj2"})

# A quarter turn swaps the display dimensions; a half turn does not.
_QUARTER_TURNS = (90, 270)

_VIDEO = "video"
_AUDIO = "audio"


class ProbeParseError(ValueError):
    """ffprobe returned something this parser cannot read as a video file.

    Carries a cause the UI can render. A file that is not video at all lands
    here, which is how a `.txt` renamed to `.mp4` is rejected.
    """


@dataclass(frozen=True, slots=True)
class MediaProperties:
    """Everything ``media_blobs`` records about a byte sequence.

    ``is_variable_frame_rate`` is deliberately three-valued - see
    ``detect_variable_frame_rate``.
    """

    container_format: str
    duration_seconds: float
    display_width: int
    display_height: int
    rotation_degrees: int
    fps_source: float
    is_variable_frame_rate: bool | None
    video_codec: str
    audio_codec: str | None
    audio_sample_rate: int | None


def parse_rational(value: object) -> float | None:
    """Parse ffprobe's ``"30000/1001"`` rate notation into a float.

    ``"0/0"`` is ffprobe for "no answer" and must not become ``0.0``: a zero
    frame rate would flow into the proxy recipe and the beat grid as a number.
    """
    if not isinstance(value, str) or "/" not in value:
        return None
    numerator, _, denominator = value.partition("/")
    try:
        top, bottom = float(numerator), float(denominator)
    except ValueError:
        # Named: ffprobe printed something that is not a rational. Treated as
        # "unknown" rather than crashing an ingest job over one field.
        return None
    if bottom == 0 or top <= 0:
        return None
    return top / bottom


def _container_can_answer_vfr(format_name: str) -> bool:
    """Whether the r/avg heuristic means anything for this container."""
    tokens = {token.strip().casefold() for token in format_name.split(",")}
    return bool(tokens & _VFR_ANSWERABLE_FORMATS)


def detect_variable_frame_rate(
    format_name: str, r_frame_rate: object, avg_frame_rate: object
) -> bool | None:
    """Three-valued: True measured VFR, False measured CFR, **None unknown**.

    ``None`` is not a nicety. ``r_frame_rate != avg_frame_rate`` is a property of
    the *container*, not of the file: Matroska reports both as the nominal rate
    for frames MP4 correctly reports as uneven. Returning ``False`` there would
    say "measured CFR" about a clip nobody measured, and Prompt 05's beat sync
    reads this column to decide whether it can trust the frame cadence. A false
    ``False`` reintroduces exactly the silent end-of-clip drift the column exists
    to prevent; a ``None`` makes the sync path treat the cadence as untrusted and
    fall back to timestamps.

    So: unanswerable container, or a rate ffprobe would not state, means unknown.
    """
    if not _container_can_answer_vfr(format_name):
        logger.debug("vfr_unanswerable_container", format_name=format_name)
        return None

    nominal = parse_rational(r_frame_rate)
    average = parse_rational(avg_frame_rate)
    if nominal is None or average is None:
        return None

    return nominal != average


def _first_stream(streams: list[dict[str, Any]], codec_type: str) -> dict[str, Any] | None:
    """The first stream of a kind, or None. Order is ffprobe's, which is the file's."""
    return next((stream for stream in streams if stream.get("codec_type") == codec_type), None)


def _display_dimensions(width: int, height: int, rotation: int) -> tuple[int, int]:
    """Swap the coded dimensions when the rotation is a quarter turn.

    A half turn is the case that catches an over-eager version of this: 180
    rotates the picture without changing its shape, so swapping on any non-zero
    rotation would report a landscape clip as portrait.
    """
    if rotation in _QUARTER_TURNS:
        return height, width
    return width, height


def _normalise_rotation(degrees: float) -> int:
    """Canonical 0, 90, 180 or 270, whatever sign and multiple ffprobe reported.

    Measured: ``-display_rotation 180`` produces ``rotation: -180`` in the side
    data. -180 and 180 are the same rotation, and a consumer comparing against a
    literal would get one of them wrong. Modulo 360 preserves the meaning -
    FFmpeg's angle is counter-clockwise, so -90 and 270 are the same instruction
    - while giving every reader one spelling to test against.
    """
    return int(degrees) % 360


def _rotation_degrees(stream: dict[str, Any]) -> int:
    """Read the rotation tag from either place ffprobe puts it.

    Modern FFmpeg exposes a display matrix as ``side_data_list[].rotation``;
    older files carry a ``tags.rotate`` string. Both appear in the wild - a
    phone from 2016 and a phone from last year write different ones - so both
    are read, side data first.
    """
    for side_data in stream.get("side_data_list") or []:
        rotation = side_data.get("rotation")
        if isinstance(rotation, int | float):
            return _normalise_rotation(rotation)
    tag = (stream.get("tags") or {}).get("rotate")
    if isinstance(tag, str):
        try:
            return _normalise_rotation(float(tag))
        except ValueError:
            # Named: a rotate tag that is not a number. Treat as unrotated
            # rather than failing an otherwise readable file.
            logger.warning("rotation_tag_unparseable")
    return 0


def _duration_seconds(document: dict[str, Any], video: dict[str, Any]) -> float:
    """Container duration, falling back to the video stream's own.

    Matroska frequently omits ``format.duration`` for a stream-copied file, and
    a zero duration would make the thumbnail strip one frame wide and the
    render budget the floor.
    """
    candidates: tuple[object, ...] = (
        document.get("format", {}).get("duration"),
        video.get("duration"),
    )
    for candidate in candidates:
        if not isinstance(candidate, str | int | float):
            continue
        try:
            duration = float(candidate)
        except ValueError:
            # Named: ffprobe printed "N/A". Try the next source.
            continue
        if duration > 0:
            return duration
    raise ProbeParseError("this file reports no duration, so it cannot be read as a clip")


def parse_probe(document: dict[str, Any]) -> MediaProperties:
    """Read one ffprobe JSON document, or say why it is not a video.

    Raises ``ProbeParseError`` with a readable cause. A caller turns that into a
    named API error; nothing here produces a traceback for the UI.
    """
    streams = document.get("streams")
    if not isinstance(streams, list):
        raise ProbeParseError("this file could not be read as media")

    video = _first_stream(streams, _VIDEO)
    if video is None:
        raise ProbeParseError("this file contains no video track")

    width, height = video.get("width"), video.get("height")
    if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
        raise ProbeParseError("this file's video track reports no usable dimensions")

    format_name = str(document.get("format", {}).get("format_name", ""))
    rotation = _rotation_degrees(video)
    display_width, display_height = _display_dimensions(width, height, rotation)

    # avg over r: r_frame_rate is the smallest cadence that fits every frame,
    # which for VFR footage is a rate no part of the clip actually runs at.
    fps_source = parse_rational(video.get("avg_frame_rate")) or parse_rational(
        video.get("r_frame_rate")
    )
    if fps_source is None:
        raise ProbeParseError("this file's video track reports no frame rate")

    audio = _first_stream(streams, _AUDIO)
    sample_rate = None
    if audio is not None:
        try:
            sample_rate = int(audio["sample_rate"])
        except (KeyError, TypeError, ValueError):
            # Named: an audio stream ffprobe could not give a rate for. The
            # clip is still usable; the rate is recorded as unknown.
            logger.warning("audio_sample_rate_unreadable")

    return MediaProperties(
        container_format=format_name,
        duration_seconds=_duration_seconds(document, video),
        display_width=display_width,
        display_height=display_height,
        rotation_degrees=rotation,
        fps_source=fps_source,
        is_variable_frame_rate=detect_variable_frame_rate(
            format_name, video.get("r_frame_rate"), video.get("avg_frame_rate")
        ),
        video_codec=str(video.get("codec_name", "")),
        audio_codec=str(audio.get("codec_name", "")) if audio is not None else None,
        audio_sample_rate=sample_rate,
    )


__all__ = [
    "MediaProperties",
    "ProbeParseError",
    "detect_variable_frame_rate",
    "parse_probe",
    "parse_rational",
]
