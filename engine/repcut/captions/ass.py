"""Caption lines to an ASS subtitle document, burned in by FFmpeg's ``ass`` filter.

**Escaping (security.md: every sink).** Caption text is what a person typed or
what a recogniser heard, so it is untrusted at two sinks here:

- **The filter graph.** Text never enters it: it is written to a file whose path
  goes through ``ffmpeg_builder.filter_path``, which refuses quote, bracket,
  semicolon and comma. Nothing a caption says can end the ``ass=`` option.
- **The subtitle markup.** libass reads ``{...}`` as an override block and
  ``\\N``, ``\\n``, ``\\h`` as line breaks and spaces. ``{`` and ``}`` are written
  as ``\\{`` and ``\\}``, which libass draws literally; a backslash is followed by
  U+2060 WORD JOINER (zero width), so it can never begin an escape. Measured
  against libass in FFmpeg 8.1: ``{\\c&H0000FF&}X`` escaped draws white text
  with its braces, not a red X. Line breaks and control characters become
  spaces.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from repcut.captions.styles import CaptionStyle
from repcut.captions.types import CaptionLine, TimedWord

WORD_JOINER = "⁠"
HOLD_SECONDS = 0.15
MIN_EVENT_SECONDS = 0.12


def escape_text(text: str) -> str:
    """Literal text for an ASS Dialogue line. See the module docstring."""
    flat = "".join(ch if ch.isprintable() else " " for ch in text)
    return flat.replace("\\", "\\" + WORD_JOINER).replace("{", "\\{").replace("}", "\\}")


def _colour(hex_rgb: str, alpha: int = 0) -> str:
    r, g, b = hex_rgb[1:3], hex_rgb[3:5], hex_rgb[5:7]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def _time(seconds: float) -> str:
    centis = max(0, round(seconds * 100))
    h, rem = divmod(centis, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


_ALIGN = {"center": 5, "bottom": 2, "lower-third": 1}
_MARGIN_V = {"center": 0.0, "bottom": 0.08, "lower-third": 0.22}


@dataclass(frozen=True, slots=True)
class Event:
    start: float
    end: float
    text: str


def _snap(start: float, beats: Sequence[float], window: float, low: float, high: float) -> float:
    if window <= 0 or not beats:
        return start
    nearest = min(beats, key=lambda b: abs(b - start))
    if abs(nearest - start) <= window and low <= nearest < high:
        return nearest
    return start


def _chunks(words: Sequence[TimedWord], size: int) -> list[tuple[TimedWord, ...]]:
    return [tuple(words[i : i + size]) for i in range(0, len(words), size)]


def events(
    lines: Sequence[CaptionLine], style: CaptionStyle, beats: Sequence[float] = ()
) -> list[Event]:
    """Timed, escaped event texts. Never overlapping within a line."""
    out: list[Event] = []
    for line in lines:
        chunks = _chunks(line.words, style.max_words)
        for position, chunk in enumerate(chunks):
            following = chunks[position + 1][0].start if position + 1 < len(chunks) else None
            start = chunk[0].start
            if style.mode == "words":
                start = _snap(
                    start, beats, style.beat_snap_ms / 1000, line.start - 0.1, chunk[-1].end
                )
            end = chunk[-1].end + HOLD_SECONDS
            if following is not None:
                end = min(end, following)
            end = max(end, start + MIN_EVENT_SECONDS)
            out.append(Event(start, end, _text(chunk, style, start)))
    return out


def _word(word: TimedWord, style: CaptionStyle) -> str:
    return escape_text(word.text.upper() if style.uppercase else word.text)


def _text(chunk: Sequence[TimedWord], style: CaptionStyle, start: float) -> str:
    parts: list[str] = []
    if style.pop_scale > 100:
        p = style.pop_scale
        parts.append(f"{{\\fscx{p}\\fscy{p}\\t(0,90,\\fscx100\\fscy100)}}")
    if not style.karaoke:
        parts.append(" ".join(_word(w, style) for w in chunk))
        return "".join(parts)
    cursor = start
    pieces: list[str] = []
    for index, word in enumerate(chunk):
        until = chunk[index + 1].start if index + 1 < len(chunk) else word.end
        lead = max(0, round((word.start - cursor) * 100)) if index else 0
        span = max(1, round((until - max(word.start, cursor)) * 100))
        pieces.append(f"{{\\kf{lead + span}}}{_word(word, style)}")
        cursor = until
    parts.append(" ".join(pieces))
    return "".join(parts)


def document(
    event_list: Sequence[Event],
    style: CaptionStyle,
    *,
    width: int,
    height: int,
    offset: float = 0.0,
    duration: float | None = None,
) -> str:
    """A complete ASS file. ``offset`` shifts events (a segment starting there is 0)."""
    size = round(style.size * height)
    outline = round(style.outline * size, 1)
    shadow = round(style.shadow * size, 1)
    margin_v = round(_MARGIN_V[style.position] * height)
    margin_h = round(0.06 * width)
    # Karaoke fills from SecondaryColour to PrimaryColour: the highlight is the
    # sung colour, the plain colour waits.
    primary = _colour(style.highlight if style.karaoke else style.colour)
    secondary = _colour(style.colour)
    header = "\n".join(
        (
            "[Script Info]",
            "ScriptType: v4.00+",
            f"PlayResX: {width}",
            f"PlayResY: {height}",
            "WrapStyle: 0",
            "ScaledBorderAndShadow: yes",
            "",
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
            "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
            "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
            f"Style: Repcut,{style.font},{size},{primary},{secondary},"
            f"{_colour(style.outline_colour)},{_colour(style.outline_colour, 0x80)},"
            f"{-1 if style.bold else 0},0,0,0,100,100,0,0,1,{outline},{shadow},"
            f"{_ALIGN[style.position]},{margin_h},{margin_h},{margin_v},1",
            "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        )
    )
    rows: list[str] = []
    for event in event_list:
        start = event.start - offset
        end = event.end - offset
        if duration is not None:
            end = min(end, duration)
        start = max(start, 0.0)
        if end - start < 0.02:
            continue
        rows.append(f"Dialogue: 0,{_time(start)},{_time(end)},Repcut,,0,0,0,,{event.text}")
    return header + "\n" + "\n".join(rows) + "\n"


def events_between(event_list: Sequence[Event], start: float, end: float) -> list[Event]:
    return [e for e in event_list if e.start < end and e.end > start]


__all__ = [
    "WORD_JOINER",
    "Event",
    "document",
    "escape_text",
    "events",
    "events_between",
]
