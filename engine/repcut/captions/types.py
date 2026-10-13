"""Caption values: words and lines in a clip's own seconds, and on the edit's timeline.

Plain frozen data, JSON round-trippable, so a transcript is cached once per clip
and every later step (the guard, the timeline mapping, the subtitle writer) is a
pure function a test can call without a model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

TRANSCRIPT_VERSION = 1


@dataclass(frozen=True, slots=True)
class Word:
    start: float
    end: float
    text: str
    probability: float

    def to_json(self) -> list[Any]:
        return [round(self.start, 3), round(self.end, 3), self.text, round(self.probability, 4)]

    @classmethod
    def from_json(cls, item: list[Any]) -> Word:
        return cls(float(item[0]), float(item[1]), str(item[2]), float(item[3]))


@dataclass(frozen=True, slots=True)
class CaptionSegment:
    """One stretch of speech as the recogniser returned it, in clip seconds."""

    index: int
    start: float
    end: float
    words: tuple[Word, ...]

    @property
    def mean_probability(self) -> float:
        if not self.words:
            return 0.0
        return sum(w.probability for w in self.words) / len(self.words)

    def to_json(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "words": [w.to_json() for w in self.words],
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> CaptionSegment:
        return cls(
            index=int(data["index"]),
            start=float(data["start"]),
            end=float(data["end"]),
            words=tuple(Word.from_json(w) for w in data["words"]),
        )


@dataclass(frozen=True, slots=True)
class Transcript:
    """A clip's kept segments, plus how many the guard dropped and why it could."""

    sha256: str
    model: str
    language: str | None
    segments: tuple[CaptionSegment, ...]
    dropped: int = 0
    speech_measured: bool = True
    version: int = TRANSCRIPT_VERSION

    def to_json(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "sha256": self.sha256,
            "model": self.model,
            "language": self.language,
            "dropped": self.dropped,
            "speech_measured": self.speech_measured,
            "segments": [s.to_json() for s in self.segments],
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Transcript:
        return cls(
            sha256=str(data["sha256"]),
            model=str(data["model"]),
            language=data.get("language"),
            segments=tuple(CaptionSegment.from_json(s) for s in data["segments"]),
            dropped=int(data.get("dropped", 0)),
            speech_measured=bool(data.get("speech_measured", True)),
            version=int(data.get("version", 0)),
        )


@dataclass(frozen=True, slots=True)
class TimedWord:
    """A word on the edit's timeline. ``id`` is stable across re-plans of the edit."""

    id: str
    start: float
    end: float
    text: str
    original: str
    probability: float

    @property
    def edited(self) -> bool:
        return self.text != self.original


@dataclass(frozen=True, slots=True)
class CaptionLine:
    """One caption segment's words inside one shot, on the timeline."""

    id: str
    start: float
    end: float
    words: tuple[TimedWord, ...]
    hidden: bool = False

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)


@dataclass(frozen=True, slots=True)
class CaptionEdits:
    """What the person changed: word text by word id, hidden lines by line id."""

    words: dict[str, str] = field(default_factory=dict)
    hidden: frozenset[str] = field(default_factory=frozenset)
