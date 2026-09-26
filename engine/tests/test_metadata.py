"""Parsing one ffprobe document, including the answer that is "unknown".

The three-valued VFR result is the reason this file exists as unit tests as well
as integration ones: the integration test proves Matroska behaves as measured
today, and these pin the *rule* so a refactor cannot quietly turn unknown into
false without a test saying so.
"""

from typing import Any

import pytest

from repcut.media.metadata import (
    MediaProperties,
    ProbeParseError,
    detect_variable_frame_rate,
    parse_color_properties,
    parse_probe,
    parse_rational,
)

MP4 = "mov,mp4,m4a,3gp,3g2,mj2"
MATROSKA = "matroska,webm"


def _document(
    *,
    format_name: str = MP4,
    r_frame_rate: str = "30/1",
    avg_frame_rate: str = "30/1",
    width: int = 1920,
    height: int = 1080,
    duration: str = "12.5",
    side_data: list[dict[str, Any]] | None = None,
    tags: dict[str, str] | None = None,
    audio: bool = True,
) -> dict[str, Any]:
    """A probe document shaped exactly as ffprobe emits one."""
    video: dict[str, Any] = {
        "codec_type": "video",
        "codec_name": "h264",
        "width": width,
        "height": height,
        "r_frame_rate": r_frame_rate,
        "avg_frame_rate": avg_frame_rate,
    }
    if side_data is not None:
        video["side_data_list"] = side_data
    if tags is not None:
        video["tags"] = tags

    streams: list[dict[str, Any]] = [video]
    if audio:
        streams.append(
            {
                "codec_type": "audio",
                "codec_name": "aac",
                "sample_rate": "44100",
                "channels": 2,
            }
        )
    return {"streams": streams, "format": {"format_name": format_name, "duration": duration}}


def _parse(**kwargs: Any) -> MediaProperties:  # noqa: ANN401
    """Build a document and parse it in one step.

    ``Any`` is the honest type here: the kwargs are forwarded verbatim to
    ``_document`` above, which types them properly, and restating its signature
    would mean two places to edit every time a probe field is added.
    """
    return parse_probe(_document(**kwargs))


# --- the three-valued answer -------------------------------------------------


def test_a_container_that_answers_reports_variable() -> None:
    assert detect_variable_frame_rate(MP4, "30/1", "1560/121") is True


def test_a_container_that_answers_reports_constant() -> None:
    assert detect_variable_frame_rate(MP4, "30/1", "30/1") is False


def test_a_container_that_cannot_answer_reports_unknown() -> None:
    """Matroska hands back the nominal rate for both, whatever the timestamps.

    So its equality is not evidence of CFR, and reporting False would be
    recording a measurement nobody took. Prompt 05's beat sync reads this.
    """
    assert detect_variable_frame_rate(MATROSKA, "30/1", "30/1") is None
    assert detect_variable_frame_rate(MATROSKA, "13/1", "13/1") is None


@pytest.mark.parametrize("format_name", ["avi", "mpegts", "flv", "asf", "", "webm"])
def test_every_unlisted_container_reports_unknown(format_name: str) -> None:
    """The allow-list is the whole mechanism: unknown by default, not constant.

    A container added to FFmpeg tomorrow is unknown until someone measures it,
    which is the direction that fails safe.
    """
    assert detect_variable_frame_rate(format_name, "30/1", "30/1") is None


@pytest.mark.parametrize(("nominal", "average"), [("0/0", "30/1"), ("30/1", "0/0"), ("", "30/1")])
def test_a_rate_ffprobe_would_not_state_reports_unknown(nominal: str, average: str) -> None:
    """`0/0` is ffprobe for "no answer" and must not be read as a number."""
    assert detect_variable_frame_rate(MP4, nominal, average) is None


def test_the_parsed_document_carries_the_unknown_through() -> None:
    """The value has to survive from the heuristic to the dataclass unchanged."""
    assert _parse(format_name=MATROSKA).is_variable_frame_rate is None
    assert _parse(format_name=MP4, avg_frame_rate="13/1").is_variable_frame_rate is True


# --- rationals ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [("30/1", 30.0), ("30000/1001", 29.97002997002997), ("60/2", 30.0)],
)
def test_a_rational_parses(value: str, expected: float) -> None:
    assert parse_rational(value) == pytest.approx(expected)


@pytest.mark.parametrize("value", ["0/0", "0/1", "N/A", "", "30", None, 30])
def test_a_non_rate_is_none_not_zero(value: object) -> None:
    """Zero is a number the proxy recipe and the beat grid would both accept."""
    assert parse_rational(value) is None


# --- rotation ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("rotation", "expected"),
    [
        (90, (1080, 1920)),
        (-90, (1080, 1920)),
        (270, (1080, 1920)),
        (180, (1920, 1080)),
        (-180, (1920, 1080)),
        (0, (1920, 1080)),
    ],
)
def test_a_quarter_turn_swaps_the_display_dimensions(
    rotation: int, expected: tuple[int, int]
) -> None:
    """Half turns must not swap. Measured: ffprobe reports -180 for a 180 tag."""
    parsed = _parse(side_data=[{"rotation": rotation}])

    assert (parsed.display_width, parsed.display_height) == expected


@pytest.mark.parametrize(("raw", "canonical"), [(-90, 270), (-180, 180), (270, 270), (360, 0)])
def test_rotation_is_stored_canonically(raw: int, canonical: int) -> None:
    """One spelling per rotation, so a consumer can compare against a literal."""
    assert _parse(side_data=[{"rotation": raw}]).rotation_degrees == canonical


def test_the_legacy_rotate_tag_is_read_too() -> None:
    """Older phones write ``tags.rotate`` instead of a display matrix."""
    assert _parse(tags={"rotate": "90"}).rotation_degrees == 90


def test_an_unparseable_rotate_tag_is_treated_as_unrotated() -> None:
    """A malformed tag must not fail an otherwise readable clip."""
    assert _parse(tags={"rotate": "sideways"}).rotation_degrees == 0


# --- rejection ---------------------------------------------------------------


def test_a_document_with_no_video_track_is_rejected() -> None:
    document = _document()
    document["streams"] = [
        stream for stream in document["streams"] if stream["codec_type"] != "video"
    ]

    with pytest.raises(ProbeParseError, match="no video track"):
        parse_probe(document)


def test_a_document_with_no_streams_is_rejected() -> None:
    with pytest.raises(ProbeParseError):
        parse_probe({"format": {"format_name": MP4}})


def test_a_video_track_with_no_dimensions_is_rejected() -> None:
    with pytest.raises(ProbeParseError, match="dimensions"):
        _parse(width=0, height=0)


def test_a_clip_with_no_duration_is_rejected() -> None:
    """A zero duration would make the strip one cell and the budget the floor."""
    with pytest.raises(ProbeParseError, match="duration"):
        _parse(duration="N/A")


def test_the_stream_duration_covers_a_container_that_omits_one() -> None:
    """Matroska frequently omits ``format.duration`` on a stream copy."""
    document = _document(duration="N/A")
    document["streams"][0]["duration"] = "8.0"

    assert parse_probe(document).duration_seconds == pytest.approx(8.0)


# --- audio -------------------------------------------------------------------


def test_the_source_audio_rate_is_recorded() -> None:
    """Recorded at ingest: mixed rates desync on concat, and export is too late."""
    parsed = _parse()

    assert parsed.audio_codec == "aac"
    assert parsed.audio_sample_rate == 44100


def test_a_silent_clip_has_no_audio_columns() -> None:
    parsed = _parse(audio=False)

    assert parsed.audio_codec is None
    assert parsed.audio_sample_rate is None


def test_an_audio_track_with_no_readable_rate_is_not_a_failure() -> None:
    """The clip is still usable; the rate is recorded as unknown."""
    document = _document()
    document["streams"][1]["sample_rate"] = "N/A"

    parsed = parse_probe(document)

    assert parsed.audio_codec == "aac"
    assert parsed.audio_sample_rate is None


def test_the_frame_rate_comes_from_the_average_not_the_nominal() -> None:
    """For VFR footage, `r_frame_rate` is a rate no part of the clip runs at."""
    assert _parse(r_frame_rate="30/1", avg_frame_rate="13/1").fps_source == pytest.approx(13.0)


# --- colour properties (amendment 008 / build_frame_extraction's HDR branch) --


def test_color_properties_are_read_off_the_video_stream() -> None:
    document = _document()
    document["streams"][0]["color_primaries"] = "bt2020"
    document["streams"][0]["color_transfer"] = "arib-std-b67"

    properties = parse_color_properties(document)

    assert properties.color_primaries == "bt2020"
    assert properties.color_transfer == "arib-std-b67"


def test_color_properties_are_none_when_ffprobe_prints_nothing() -> None:
    """Most SDR footage carries no explicit colour tag at all - absence, not HDR."""
    properties = parse_color_properties(_document())

    assert properties.color_primaries is None
    assert properties.color_transfer is None


def test_color_properties_treat_a_missing_video_stream_as_no_signal() -> None:
    """Never raises: a document this broken fails at `parse_probe`, not here."""
    properties = parse_color_properties({"streams": []})

    assert properties.color_primaries is None
    assert properties.color_transfer is None


def test_color_properties_ignore_a_non_string_tag() -> None:
    document = _document()
    document["streams"][0]["color_primaries"] = None

    assert parse_color_properties(document).color_primaries is None
