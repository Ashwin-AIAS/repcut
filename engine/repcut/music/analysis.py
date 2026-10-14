"""One track's beat grid, bars, energy and sections, from decoded PCM.

Pure with respect to I/O: samples in, ``TrackAnalysis`` out. Decoding happens
in ``library.py`` through ``ffmpeg_builder`` (the validated-source path), so a
track's file is never opened by librosa and no path reaches this module.

The beat grid is the **returned beat times**, never ``tempo * n``: a track
whose tempo wanders accumulates error under a single tempo, and syncing
silently to a drifting grid is the worst outcome (beat-and-audio skill). A
grid whose inter-beat intervals vary too much is rejected with a reason
instead.

Downbeats matter more than beats. Bars are four beats; which of the first four
beats starts a bar is chosen by low-frequency onset strength, which is where a
kick lives.
"""

from dataclasses import asdict, dataclass, field
from typing import Literal

import numpy as np

ANALYSIS_VERSION = 1
ANALYSIS_SAMPLE_RATE = 22050
BEATS_PER_BAR = 4

# Coefficient of variation of the inter-beat intervals above which a grid is
# rejected. 6 % at 128 BPM is ~28 ms of jitter per beat, most of the 40 ms snap
# tolerance spent before a cut is even placed.
MAX_TEMPO_VARIATION = 0.06

# The shortest track worth analysing: two bars at 60 BPM.
MIN_DURATION_SECONDS = 8.0

ENERGY_WINDOW_SECONDS = 0.5

# Onset hop: 256 samples at 22.05 kHz is 11.6 ms per frame. The default 512
# spends 23 ms of the 40 ms snap tolerance on quantisation alone.
ONSET_HOP = 256

# A drop is a rise of at least this much in normalised bar energy, from the four
# bars before to the four bars after, landing at or above DROP_FLOOR.
DROP_MIN_RISE = 0.15
DROP_FLOOR = 0.6

SectionKind = Literal["intro", "build", "drop", "outro", "body"]


class TrackAnalysisError(ValueError):
    """A track that cannot become a beat grid. ``reason`` is a fixed sentence."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class Section:
    """A run of bars, ``start_bar`` inclusive, ``end_bar`` exclusive."""

    kind: SectionKind
    start_bar: int
    end_bar: int


@dataclass(frozen=True, slots=True)
class TrackAnalysis:
    """Everything the planner needs from a track, computed once per version."""

    version: int
    duration_seconds: float
    bpm: float
    beats: tuple[float, ...]
    # Index into ``beats`` of the first downbeat; bar k starts at
    # ``beats[downbeat_offset + 4k]``.
    downbeat_offset: int
    bar_energy: tuple[float, ...]
    energy_curve: tuple[float, ...]
    sections: tuple[Section, ...]
    tempo_variation: float
    drop_bar: int | None = None
    extra: dict[str, float] = field(default_factory=dict)

    @property
    def bar_starts(self) -> tuple[float, ...]:
        return self.beats[self.downbeat_offset :: BEATS_PER_BAR]

    @property
    def drop_seconds(self) -> float | None:
        if self.drop_bar is None:
            return None
        return self.bar_starts[self.drop_bar]

    def to_json(self) -> dict[str, object]:
        data = asdict(self)
        for key in ("beats", "bar_energy", "energy_curve"):
            data[key] = list(data[key])
        data["sections"] = [asdict(section) for section in self.sections]
        return data

    @classmethod
    def from_json(cls, data: dict[str, object]) -> "TrackAnalysis":
        sections_raw = data.get("sections", [])
        if not isinstance(sections_raw, list):
            raise TrackAnalysisError("stored analysis has no section list")
        sections = tuple(Section(**raw) for raw in sections_raw)

        def floats(key: str) -> tuple[float, ...]:
            raw = data.get(key, [])
            if not isinstance(raw, list):
                raise TrackAnalysisError("stored analysis is malformed")
            return tuple(float(value) for value in raw)

        drop = data.get("drop_bar")
        return cls(
            version=int(str(data["version"])),
            duration_seconds=float(str(data["duration_seconds"])),
            bpm=float(str(data["bpm"])),
            beats=floats("beats"),
            downbeat_offset=int(str(data["downbeat_offset"])),
            bar_energy=floats("bar_energy"),
            energy_curve=floats("energy_curve"),
            sections=sections,
            tempo_variation=float(str(data["tempo_variation"])),
            drop_bar=None if drop is None else int(str(drop)),
        )


def _normalise(values: np.ndarray) -> np.ndarray:
    peak = float(values.max()) if values.size else 0.0
    if peak <= 0:
        return np.zeros(values.shape, dtype=float)
    return np.asarray(values / peak, dtype=float)


def _downbeat_offset(low_onset: np.ndarray, beat_frames: np.ndarray) -> int:
    """The beat phase (0-3) whose beats carry the most low-frequency onset."""
    scores = []
    for phase in range(BEATS_PER_BAR):
        frames = beat_frames[phase::BEATS_PER_BAR]
        frames = frames[frames < len(low_onset)]
        scores.append(float(low_onset[frames].mean()) if frames.size else 0.0)
    return int(np.argmax(scores))


def _on_beat_phase(low_onset: np.ndarray, beat_frames: np.ndarray) -> np.ndarray:
    """The grid, or the grid shifted half a beat, whichever sits on the kicks.

    A tracker that locked onto the off-beat reports the right tempo and the
    wrong phase. The half-shifted grid is tested against the low-band onsets
    and kept only when it carries clearly more of them.
    """
    if beat_frames.size < 2:
        return beat_frames
    half = np.round(np.diff(beat_frames) / 2).astype(int)
    shifted = beat_frames[:-1] + half
    shifted = shifted[shifted < len(low_onset)]
    on = beat_frames[beat_frames < len(low_onset)]
    if shifted.size == 0 or on.size == 0:
        return beat_frames
    if float(low_onset[shifted].mean()) > 1.25 * float(low_onset[on].mean()):
        return np.asarray(shifted, dtype=int)
    return beat_frames


def _bar_energy(
    rms: np.ndarray, rms_times: np.ndarray, bar_starts: np.ndarray, end: float
) -> np.ndarray:
    edges = np.append(bar_starts, end)
    energy = np.zeros(len(bar_starts))
    for index in range(len(bar_starts)):
        mask = (rms_times >= edges[index]) & (rms_times < edges[index + 1])
        energy[index] = float(rms[mask].mean()) if mask.any() else 0.0
    return _normalise(energy)


def _energy_curve(rms: np.ndarray, rms_times: np.ndarray, duration: float) -> np.ndarray:
    count = max(1, int(np.ceil(duration / ENERGY_WINDOW_SECONDS)))
    curve = np.zeros(count)
    for index in range(count):
        start = index * ENERGY_WINDOW_SECONDS
        mask = (rms_times >= start) & (rms_times < start + ENERGY_WINDOW_SECONDS)
        curve[index] = float(rms[mask].mean()) if mask.any() else 0.0
    return _normalise(curve)


def find_drop(bar_energy: np.ndarray) -> int | None:
    """The bar where energy rises most, if the rise is large enough to be a drop."""
    best_bar: int | None = None
    best_rise = DROP_MIN_RISE
    for bar in range(2, len(bar_energy) - 1):
        before = bar_energy[max(0, bar - 4) : bar].mean()
        after = bar_energy[bar : bar + 4].mean()
        rise = after - before
        if after >= DROP_FLOOR and rise > best_rise + 1e-9:
            best_rise = float(rise)
            best_bar = bar
    return best_bar


def sections_for(bar_energy: np.ndarray, drop_bar: int | None) -> tuple[Section, ...]:
    """Intro / build / drop / outro around the drop, or one body without one."""
    bars = len(bar_energy)
    if bars == 0:
        return ()
    if drop_bar is None:
        return (Section("body", 0, bars),)
    level = bar_energy[drop_bar : drop_bar + 4].mean()
    drop_end = drop_bar + 1
    while drop_end < bars and bar_energy[drop_end] >= 0.75 * level:
        drop_end += 1
    # The intro ends where energy first reaches half the drop's level.
    intro_end = 0
    while intro_end < drop_bar and bar_energy[intro_end] < 0.5 * level:
        intro_end += 1
    sections: list[Section] = []
    if intro_end > 0:
        sections.append(Section("intro", 0, intro_end))
    if drop_bar > intro_end:
        sections.append(Section("build", intro_end, drop_bar))
    sections.append(Section("drop", drop_bar, drop_end))
    if drop_end < bars:
        sections.append(Section("outro", drop_end, bars))
    return tuple(sections)


def analyse(samples: np.ndarray, sample_rate: int = ANALYSIS_SAMPLE_RATE) -> TrackAnalysis:
    """Beat grid, bars, energy and sections of one mono track.

    Raises ``TrackAnalysisError`` for a track that is too short, has no steady
    beat, or whose tempo varies beyond ``MAX_TEMPO_VARIATION``.
    """
    # librosa is imported here, not at module import: it pulls in numba, which
    # costs seconds at engine start for a feature most requests never touch.
    import librosa

    y = np.asarray(samples, dtype=np.float32)
    duration = len(y) / sample_rate
    if duration < MIN_DURATION_SECONDS:
        raise TrackAnalysisError("this track is too short to find a beat in")
    if not np.any(np.abs(y) > 1e-4):
        raise TrackAnalysisError("this track is silent")

    # The beat is tracked on low-frequency onsets, where a kick lives. A
    # full-band envelope lets off-beat hi-hats win, and a grid half a beat out
    # of phase puts every cut exactly between two beats.
    low_onset = librosa.onset.onset_strength(
        y=y, sr=sample_rate, hop_length=ONSET_HOP, fmax=200.0, n_mels=32
    )
    full_onset = librosa.onset.onset_strength(y=y, sr=sample_rate, hop_length=ONSET_HOP)
    onset = low_onset if float(low_onset.max()) > 0 else full_onset
    _tempo, beat_frames = librosa.beat.beat_track(
        onset_envelope=onset, sr=sample_rate, hop_length=ONSET_HOP
    )
    beat_frames = np.asarray(beat_frames, dtype=int)
    if beat_frames.size < 2 * BEATS_PER_BAR:
        raise TrackAnalysisError("no steady beat was found in this track")
    beat_frames = _on_beat_phase(low_onset, beat_frames)
    beats = librosa.frames_to_time(beat_frames, sr=sample_rate, hop_length=ONSET_HOP)

    intervals = np.diff(beats)
    variation = float(intervals.std() / intervals.mean())
    if variation > MAX_TEMPO_VARIATION:
        raise TrackAnalysisError(
            "this track's tempo drifts too much to cut to; pick a track with a steady beat"
        )
    # The slope of beat time against beat index: every beat votes, so one
    # quantised interval cannot move the tempo the way it moves a median.
    slope = float(np.polyfit(np.arange(len(beats)), beats, 1)[0])
    bpm = round(60.0 / slope, 2)

    offset = _downbeat_offset(low_onset, beat_frames)

    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=512)[0]
    rms_times = librosa.frames_to_time(np.arange(len(rms)), sr=sample_rate, hop_length=512)
    bar_starts = beats[offset::BEATS_PER_BAR]
    bar_energy = _bar_energy(rms, rms_times, bar_starts, duration)
    drop_bar = find_drop(bar_energy)

    return TrackAnalysis(
        version=ANALYSIS_VERSION,
        duration_seconds=round(duration, 4),
        bpm=bpm,
        beats=tuple(round(float(t), 4) for t in beats),
        downbeat_offset=offset,
        bar_energy=tuple(round(float(e), 4) for e in bar_energy),
        energy_curve=tuple(round(float(e), 4) for e in _energy_curve(rms, rms_times, duration)),
        sections=sections_for(bar_energy, drop_bar),
        tempo_variation=round(variation, 5),
        drop_bar=drop_bar,
    )


__all__ = [
    "ANALYSIS_SAMPLE_RATE",
    "ANALYSIS_VERSION",
    "BEATS_PER_BAR",
    "MAX_TEMPO_VARIATION",
    "Section",
    "SectionKind",
    "TrackAnalysis",
    "TrackAnalysisError",
    "analyse",
    "find_drop",
    "sections_for",
]
