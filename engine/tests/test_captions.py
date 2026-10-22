"""Captions without a model: the guard, the timeline mapping, styles, and the ASS sink.

The recogniser is a fake with Whisper's shape (``transcribe`` -> segments with
words), so every rule here is tested on every machine. Accuracy (WER) needs the
real model and is a gate criterion (``scripts/verify_06_checks.py``).
"""

import shutil
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from repcut.captions import ass
from repcut.captions.styles import CaptionStyle, CaptionStyleError, load_style_file, style, styles
from repcut.captions.timeline import clean_text, line_id, timeline_lines, visible, word_id
from repcut.captions.transcribe import keep_segment, transcribe_samples
from repcut.captions.types import (
    CaptionEdits,
    CaptionLine,
    CaptionSegment,
    TimedWord,
    Transcript,
    Word,
)
from repcut.edit.types import Plan, Shot
from repcut.edit.vad import SpeechRegion

SHA = "ab" * 32


@dataclass
class _W:
    start: float
    end: float
    word: str
    probability: float


@dataclass
class _S:
    id: int
    start: float
    end: float
    words: list[_W]


@dataclass
class _Info:
    language: str


class FakeRecogniser:
    def __init__(self, segments: list[_S]) -> None:
        self.segments = segments
        self.options: dict[str, object] = {}

    def transcribe(self, audio: np.ndarray, **options: object) -> tuple[Iterable[Any], object]:
        self.options = options
        return self.segments, _Info("de")


def _segment(start: float, probability: float, words: int = 3, index: int = 0) -> CaptionSegment:
    return CaptionSegment(
        index=index,
        start=start,
        end=start + words * 0.4,
        words=tuple(
            Word(start + i * 0.4, start + i * 0.4 + 0.3, f"w{i}", probability) for i in range(words)
        ),
    )


# --- the guard -----------------------------------------------------------------


def test_the_guard_drops_only_unsure_text_away_from_speech() -> None:
    speech = (SpeechRegion(10.0, 12.0),)
    assert not keep_segment(_segment(0.0, 0.3), speech)  # unsure, over music: invented
    assert keep_segment(_segment(10.5, 0.3), speech)  # unsure, but someone is talking
    assert keep_segment(_segment(0.0, 0.9), speech)  # confident shout the detector missed
    assert keep_segment(_segment(10.5, 0.9), speech)
    # No speech model: overlap cannot be shown, so only confidence keeps a line.
    assert not keep_segment(_segment(10.5, 0.3), None)
    assert keep_segment(_segment(10.5, 0.6), None)
    assert not keep_segment(CaptionSegment(0, 0.0, 1.0, ()), speech)


def test_transcription_is_multilingual_word_timed_and_guarded() -> None:
    fake = FakeRecogniser(
        [
            _S(0, 0.0, 1.0, [_W(0.0, 0.4, " Thank", 0.2), _W(0.4, 0.9, " you.", 0.3)]),
            _S(1, 5.0, 6.0, [_W(5.0, 5.4, " Noch", 0.95), _W(5.4, 5.9, " eine!", 0.9)]),
        ]
    )
    transcript = transcribe_samples(
        fake, np.zeros(16000, dtype=np.float32), sha256=SHA, speech=(SpeechRegion(4.8, 6.2),)
    )
    assert fake.options["word_timestamps"] is True
    assert fake.options["multilingual"] is True and fake.options["language"] is None
    assert [w.text for s in transcript.segments for w in s.words] == ["Noch", "eine!"]
    assert transcript.dropped == 1 and transcript.language == "de"
    assert Transcript.from_json(transcript.to_json()) == transcript


# --- the timeline --------------------------------------------------------------


def _plan(*shots: Shot) -> Plan:
    end = shots[-1].timeline_out
    return Plan(shots, end, end, 0.0, True, None, 1)


def _shot(source_in: float, source_out: float, timeline_in: float) -> Shot:
    return Shot(
        id=f"{int(source_in * 10):016x}",
        segment_id="s",
        sha256=SHA,
        source_in=source_in,
        source_out=source_out,
        timeline_in=timeline_in,
        timeline_out=timeline_in + source_out - source_in,
        beat_in=None,
        beat_out=None,
    )


def _transcript() -> dict[str, Transcript]:
    words = (Word(4.0, 4.3, "eins", 0.9), Word(4.9, 5.3, "zwei", 0.9), Word(5.5, 5.8, "drei", 0.9))
    return {SHA: Transcript(SHA, "fake", "de", (CaptionSegment(7, 4.0, 5.8, words),))}


def test_words_follow_their_shot_onto_the_timeline_by_midpoint() -> None:
    # The cut at source 5.0 splits "zwei" (4.9-5.3): midpoint 5.1 is in the second shot.
    plan = _plan(_shot(3.0, 5.0, 0.0), _shot(5.0, 7.0, 2.0))
    lines = timeline_lines(plan, _transcript())
    assert [[w.text for w in line.words] for line in lines] == [["eins"], ["zwei", "drei"]]
    assert lines[0].words[0].start == pytest.approx(1.0)
    assert lines[1].words[0].start == pytest.approx(2.0)  # clamped to the shot's first frame
    assert lines[1].words[1].start == pytest.approx(2.5)
    assert {line.id for line in lines} == {line_id(SHA, 7)}
    assert lines[1].words[0].id == word_id(SHA, 7, 1)


def test_edits_and_hidden_lines_survive_a_replan() -> None:
    edits = CaptionEdits(words={word_id(SHA, 7, 2): "DREI!"}, hidden=frozenset())
    before = timeline_lines(_plan(_shot(3.0, 7.0, 0.0)), _transcript(), edits)
    after = timeline_lines(_plan(_shot(4.5, 7.0, 10.0)), _transcript(), edits)
    assert before[0].words[-1].text == after[0].words[-1].text == "DREI!"
    assert after[0].words[-1].edited
    hidden = CaptionEdits(hidden=frozenset({line_id(SHA, 7)}))
    lines = timeline_lines(_plan(_shot(3.0, 7.0, 0.0)), _transcript(), hidden)
    assert lines[0].hidden and visible(lines) == []


def test_an_emptied_word_is_not_burned_in_and_text_is_cleaned() -> None:
    edits = CaptionEdits(words={word_id(SHA, 7, 0): ""})
    shown = visible(timeline_lines(_plan(_shot(3.0, 7.0, 0.0)), _transcript(), edits))
    assert [w.text for w in shown[0].words] == ["zwei", "drei"]
    assert clean_text("  a\nb\x00c  ") == "a b c"
    assert len(clean_text("x" * 500)) == 80


# --- styles --------------------------------------------------------------------


def test_the_three_styles_are_data_and_valid() -> None:
    assert [s.id for s in styles()] == ["punch", "classic", "minimal"]
    punch = style("punch")
    assert punch.karaoke and punch.mode == "words" and punch.beat_snap_ms > 0
    assert style("classic").position == "bottom"
    assert style("minimal").position == "lower-third"


def test_a_style_outside_its_bounds_is_refused_by_name(tmp_path: Path) -> None:
    text = (Path(__file__).parents[1] / "repcut/captions/styles/punch.toml").read_text()
    bad = tmp_path / "punch.toml"
    bad.write_text(text.replace("size = 0.055", "size = 0.5"), encoding="utf-8")
    with pytest.raises(CaptionStyleError, match="punch"):
        load_style_file(bad)
    renamed = tmp_path / "other.toml"
    renamed.write_text(text, encoding="utf-8")
    with pytest.raises(CaptionStyleError, match="names itself"):
        load_style_file(renamed)


# --- ASS -----------------------------------------------------------------------

HOSTILE = ["{\\c&H0000FF&}", '"quoted"', "back\\slash\\N", "<script>alert(1)</script>", "a;b,c[d]"]


def test_escaping_closes_every_markup_door() -> None:
    for text in HOSTILE:
        escaped = ass.escape_text(text)
        # No unescaped brace, and every backslash is followed by an escape we chose.
        for index, char in enumerate(escaped):
            if char in "{}":
                assert escaped[index - 1] == "\\", (text, escaped)
            if char == "\\":
                assert escaped[index + 1] in "{}" + ass.WORD_JOINER, (text, escaped)
    assert "\n" not in ass.escape_text("a\nb")


def _line(words: list[tuple[float, float, str]]) -> CaptionLine:
    timed = tuple(TimedWord(f"w{i}", a, b, t, t, 0.9) for i, (a, b, t) in enumerate(words))
    return CaptionLine("l", timed[0].start, timed[-1].end, timed)


def test_punch_pops_words_on_the_beat_with_karaoke_timing() -> None:
    punch = style("punch")
    line = _line([(1.03, 1.3, "one"), (1.4, 1.7, "more"), (1.8, 2.0, "rep"), (2.5, 2.8, "go")])
    events = ass.events([line], punch, beats=[1.0, 2.45])
    assert [round(e.start, 2) for e in events] == [1.0, 2.45]  # snapped within 80 ms
    assert "\\kf" in events[0].text and "ONE" in events[0].text and "\\fscx118" in events[0].text
    assert events[0].end <= events[1].start
    classic = ass.events([line], style("classic"), beats=[1.0])
    assert [round(e.start, 2) for e in classic] == [1.03] and "\\kf" not in classic[0].text


def test_a_document_is_shifted_into_a_segment(tmp_path: Path) -> None:
    events = [ass.Event(10.0, 11.0, "x"), ass.Event(12.5, 13.5, "y")]
    window = ass.events_between(events, 12.0, 13.0)
    text = ass.document(
        window, style("classic"), width=1080, height=1920, offset=12.0, duration=1.0
    )
    assert "PlayResY: 1920" in text
    assert "Dialogue: 0,0:00:00.50,0:00:01.00,Repcut,,0,0,0,,y" in text
    assert "x" not in text.split("[Events]")[1].replace("Text", "")


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not on PATH")
def test_hostile_caption_text_renders_literally_through_ffmpeg(tmp_path: Path) -> None:
    """The DOM is React's job; this is the subtitle sink, measured on pixels."""
    plain = CaptionStyle.model_validate(
        {
            **style("classic").model_dump(),
            "position": "center",
            "size": 0.12,
            "outline": 0.0,
            "shadow": 0.0,
        }
    )
    red = []
    for index, text in enumerate([*HOSTILE, "X"]):
        event = ass.Event(0.0, 1.0, ass.escape_text(text))
        doc = tmp_path / f"c{index}.ass"
        doc.write_text(ass.document([event], plain, width=320, height=180), encoding="utf-8")
        result = subprocess.run(
            [
                "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=black:s=320x180:d=0.2",
                "-vf", f"ass=filename='{doc.as_posix()}'", "-frames:v", "1",
                "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
            ],
            capture_output=True, check=True, timeout=60,
        )  # fmt: skip
        pixels = np.frombuffer(result.stdout, dtype=np.uint8).reshape(-1, 3).astype(int)
        red.append(int(((pixels[:, 0] > 150) & (pixels[:, 1] < 80)).sum()))
        assert int((pixels.min(axis=1) > 150).sum()) > 20, text  # something was drawn
    # Unescaped, the first string would draw nothing but a red override; escaped, no red at all.
    assert red == [0] * len(red)
