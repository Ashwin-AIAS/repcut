"""Transcripts (clip seconds) onto the edit's timeline, with the person's edits applied.

A word belongs to a shot when its midpoint lies inside the shot's source range:
a word cut in half by an edit point is shown once, in the shot holding most of
it, and never as half a word in each. Word and line ids are derived from the
clip and the recogniser's segment index, never from the timeline, so an edit
made before a re-plan still finds its word afterwards (P2).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from repcut.captions.types import CaptionEdits, CaptionLine, TimedWord, Transcript
from repcut.edit.types import Plan

ID_PREFIX = 16


def line_id(sha256: str, segment_index: int) -> str:
    return f"{sha256[:ID_PREFIX]}-{segment_index}"


def word_id(sha256: str, segment_index: int, word_index: int) -> str:
    return f"{line_id(sha256, segment_index)}-{word_index}"


def clean_text(text: str) -> str:
    """An edited word as stored: one line, no control characters, trimmed, bounded."""
    flat = "".join(ch if ch.isprintable() else " " for ch in text)
    return " ".join(flat.split())[:80]


def timeline_lines(
    plan: Plan, transcripts: Mapping[str, Transcript], edits: CaptionEdits | None = None
) -> list[CaptionLine]:
    edits = edits or CaptionEdits()
    lines: list[CaptionLine] = []
    for shot in plan.shots:
        transcript = transcripts.get(shot.sha256)
        # A slow-motion shot is silent (export/render.py), so it has no words
        # to caption: a caption without its sound would be a claim about audio
        # the viewer is not hearing.
        if transcript is None or shot.slow:
            continue
        offset = shot.timeline_in - shot.source_in
        for segment in transcript.segments:
            if segment.end <= shot.source_in or segment.start >= shot.source_out:
                continue
            words: list[TimedWord] = []
            for index, word in enumerate(segment.words):
                middle = (word.start + word.end) / 2
                if not shot.source_in <= middle < shot.source_out:
                    continue
                wid = word_id(shot.sha256, segment.index, index)
                text = edits.words.get(wid, word.text)
                start = max(word.start, shot.source_in) + offset
                end = min(word.end, shot.source_out) + offset
                words.append(
                    TimedWord(
                        id=wid,
                        start=round(start, 3),
                        end=round(max(end, start + 0.04), 3),
                        text=text,
                        original=word.text,
                        probability=word.probability,
                    )
                )
            if not words:
                continue
            lid = line_id(shot.sha256, segment.index)
            lines.append(
                CaptionLine(
                    id=lid,
                    start=words[0].start,
                    end=words[-1].end,
                    words=tuple(words),
                    hidden=lid in edits.hidden,
                )
            )
    lines.sort(key=lambda line: line.start)
    return lines


def visible(lines: Sequence[CaptionLine]) -> list[CaptionLine]:
    """What gets burned in: hidden lines out, words emptied by an edit out."""
    out: list[CaptionLine] = []
    for line in lines:
        if line.hidden:
            continue
        words = tuple(w for w in line.words if w.text)
        if words:
            out.append(
                CaptionLine(id=line.id, start=words[0].start, end=words[-1].end, words=words)
            )
    return out


__all__ = ["clean_text", "line_id", "timeline_lines", "visible", "word_id"]
