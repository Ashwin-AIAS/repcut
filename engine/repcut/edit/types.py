"""Value objects shared by the edit package. Frozen: a plan is data, not state.

Everything here is JSON-round-trippable, because a plan and the decisions that
produced it are stored (``edit_plans``) and sent to the UI as they are.
"""

import hashlib
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import Literal

from repcut.music.analysis import BEATS_PER_BAR, TrackAnalysis

MusicMode = Literal["suggested", "chosen", "off"]
BoundaryAction = Literal["add", "remove"]


@dataclass(frozen=True, slots=True)
class Pace:
    """How a theme cuts: its energy and how many beats a shot lasts.

    ``calm_beats`` applies where the music is quiet (intro, outro, low-energy
    bars); ``peak_beats`` in the drop and loud bars. Never under one beat and
    never under ``min_shot_seconds``: below ~0.6 s a shot reads as a glitch.
    """

    energy: float = 0.5
    calm_beats: int = 8
    peak_beats: int = 4
    min_shot_seconds: float = 0.6

    def __post_init__(self) -> None:
        if not 0.0 <= self.energy <= 1.0:
            raise ValueError("pace energy is a fraction")
        if self.calm_beats < 1 or self.peak_beats < 1:
            raise ValueError("a shot lasts at least one beat")
        if self.min_shot_seconds <= 0:
            raise ValueError("min_shot_seconds must be positive")


@dataclass(frozen=True, slots=True)
class Segment:
    """A usable stretch of one clip, in the clip's own (source) seconds."""

    id: str
    sha256: str
    start: float
    end: float
    quality: float
    energy: float
    scene_index: int = 0

    @property
    def duration(self) -> float:
        return self.end - self.start

    @staticmethod
    def make_id(sha256: str, start: float, end: float) -> str:
        return f"{sha256[:16]}-{round(start * 1000)}-{round(end * 1000)}"


@dataclass(frozen=True, slots=True)
class BeatGrid:
    """The beats a plan cuts on. ``snapped`` is false for a pacing grid (no music)."""

    beats: tuple[float, ...]
    downbeat_offset: int
    bar_energy: tuple[float, ...]
    drop_bar: int | None
    snapped: bool
    track_sha256: str | None = None
    bpm: float = 0.0

    @classmethod
    def from_analysis(cls, analysis: TrackAnalysis, track_sha256: str) -> "BeatGrid":
        return cls(
            beats=analysis.beats,
            downbeat_offset=analysis.downbeat_offset,
            bar_energy=analysis.bar_energy,
            drop_bar=analysis.drop_bar,
            snapped=True,
            track_sha256=track_sha256,
            bpm=analysis.bpm,
        )

    def bar_of(self, beat_index: int) -> int:
        return max(0, (beat_index - self.downbeat_offset) // BEATS_PER_BAR)

    def is_downbeat(self, beat_index: int) -> bool:
        return beat_index >= self.downbeat_offset and (
            (beat_index - self.downbeat_offset) % BEATS_PER_BAR == 0
        )

    def energy_at(self, beat_index: int) -> float:
        if not self.bar_energy:
            return 0.5
        return self.bar_energy[min(self.bar_of(beat_index), len(self.bar_energy) - 1)]

    @property
    def drop_beat(self) -> int | None:
        if self.drop_bar is None:
            return None
        return self.downbeat_offset + self.drop_bar * BEATS_PER_BAR


@dataclass(frozen=True, slots=True)
class Shot:
    """One shot of the edit: a source range placed at a timeline range.

    ``beat_in``/``beat_out`` are the musical positions (indices into the grid)
    the shot's edges were snapped to, or None for an edge that is a locked
    shot's: musical positions, not timestamps, are what make a new track a
    re-snap rather than a rewrite (beat-and-audio skill).
    """

    id: str
    segment_id: str
    sha256: str
    source_in: float
    source_out: float
    timeline_in: float
    timeline_out: float
    beat_in: int | None
    beat_out: int | None
    locked: bool = False

    @property
    def duration(self) -> float:
        return self.timeline_out - self.timeline_in

    @staticmethod
    def make_id(segment_id: str, source_in: float) -> str:
        digest = hashlib.sha256(f"{segment_id}@{source_in:.3f}".encode()).hexdigest()
        return digest[:16]

    def to_json(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_json(cls, data: Mapping[str, object]) -> "Shot":
        def number(key: str) -> float:
            return float(str(data[key]))

        def optional_int(key: str) -> int | None:
            value = data.get(key)
            return None if value is None else int(str(value))

        return cls(
            id=str(data["id"]),
            segment_id=str(data["segment_id"]),
            sha256=str(data["sha256"]),
            source_in=number("source_in"),
            source_out=number("source_out"),
            timeline_in=number("timeline_in"),
            timeline_out=number("timeline_out"),
            beat_in=optional_int("beat_in"),
            beat_out=optional_int("beat_out"),
            locked=bool(data.get("locked", False)),
        )


@dataclass(frozen=True, slots=True)
class BoundaryOverride:
    """A person's correction to a detected scene boundary (amendment 012 row 11)."""

    sha256: str
    action: BoundaryAction
    seconds: float


@dataclass(frozen=True, slots=True)
class Decisions:
    """Everything a person has decided about one edit. The planner's other input.

    Nothing here is a suggestion: a field at its default means "follow the AI".
    Re-sync reads these and never discards one (P2).
    """

    target_seconds: float = 30.0
    seed: int = 1
    music: MusicMode = "suggested"
    track_sha256: str | None = None
    locked: tuple[Shot, ...] = ()
    removed_segments: frozenset[str] = field(default_factory=frozenset)
    forced_segments: frozenset[str] = field(default_factory=frozenset)
    boundaries: tuple[BoundaryOverride, ...] = ()

    def to_json(self) -> dict[str, object]:
        return {
            "target_seconds": self.target_seconds,
            "seed": self.seed,
            "music": self.music,
            "track_sha256": self.track_sha256,
            "locked": [shot.to_json() for shot in self.locked],
            "removed_segments": sorted(self.removed_segments),
            "forced_segments": sorted(self.forced_segments),
            "boundaries": [asdict(boundary) for boundary in self.boundaries],
        }

    @classmethod
    def from_json(cls, data: Mapping[str, object]) -> "Decisions":
        def strings(key: str) -> frozenset[str]:
            raw = data.get(key, [])
            return frozenset(str(item) for item in raw) if isinstance(raw, list) else frozenset()

        locked_raw = data.get("locked", [])
        boundaries_raw = data.get("boundaries", [])
        music = str(data.get("music", "suggested"))
        if music not in ("suggested", "chosen", "off"):
            raise ValueError("unknown music mode")
        track = data.get("track_sha256")
        return cls(
            target_seconds=float(str(data.get("target_seconds", 30.0))),
            seed=int(str(data.get("seed", 1))),
            music=music,  # type: ignore[arg-type]  # narrowed by the check above
            track_sha256=None if track is None else str(track),
            locked=tuple(Shot.from_json(item) for item in locked_raw if isinstance(item, Mapping))
            if isinstance(locked_raw, list)
            else (),
            removed_segments=strings("removed_segments"),
            forced_segments=strings("forced_segments"),
            boundaries=tuple(
                BoundaryOverride(
                    sha256=str(item["sha256"]),
                    action="add" if item.get("action") == "add" else "remove",
                    seconds=float(str(item["seconds"])),
                )
                for item in boundaries_raw
                if isinstance(item, Mapping)
            )
            if isinstance(boundaries_raw, list)
            else (),
        )


@dataclass(frozen=True, slots=True)
class Plan:
    """The edit: shots in timeline order, and where the music starts."""

    shots: tuple[Shot, ...]
    duration_seconds: float
    target_seconds: float
    music_start_seconds: float
    snapped: bool
    track_sha256: str | None
    seed: int
    warnings: tuple[str, ...] = ()

    @property
    def cuts(self) -> tuple[float, ...]:
        """Every cut, in timeline seconds: each shot boundary after the first."""
        return tuple(shot.timeline_in for shot in self.shots[1:])

    def to_json(self) -> dict[str, object]:
        return {
            "shots": [shot.to_json() for shot in self.shots],
            "duration_seconds": self.duration_seconds,
            "target_seconds": self.target_seconds,
            "music_start_seconds": self.music_start_seconds,
            "snapped": self.snapped,
            "track_sha256": self.track_sha256,
            "seed": self.seed,
            "warnings": list(self.warnings),
        }


__all__ = [
    "BeatGrid",
    "BoundaryAction",
    "BoundaryOverride",
    "Decisions",
    "MusicMode",
    "Pace",
    "Plan",
    "Segment",
    "Shot",
]
