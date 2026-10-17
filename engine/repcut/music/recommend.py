"""Rank the library against the session: top five tracks, each with a reason.

Pure. The session is summarised as a ``SessionProfile``: duration-weighted
energy of its scenes (``Scene.energy_score``, 0-100), the share of scenes the
analysis tagged high or low energy, and the theme's pace. A track is scored on:

- **tempo**: its BPM (or double or half - a 70 BPM track is a 140 BPM track to a
  cut) against a target tempo from the session's energy and the theme's pace;
- **energy**: its mean bar energy against the session's;
- **shape**: a drop is worth something, because the planner lands it on the
  session's best footage; a steady grid is required (unstable tracks never
  reach here, ``analysis`` rejects them).

The reason is one plain sentence a person can disagree with, which is the point
of showing it (P2): "High energy, 140 BPM matches your heavy-set pacing."
"""

from collections.abc import Sequence
from dataclasses import dataclass

from repcut.edit.types import Pace
from repcut.music.analysis import TrackAnalysis

TOP_N = 5
BPM_LOW = 80.0
BPM_HIGH = 150.0
_DROP_BONUS = 0.15


@dataclass(frozen=True, slots=True)
class SessionProfile:
    energy: float
    high_share: float = 0.0
    low_share: float = 0.0

    def __post_init__(self) -> None:
        if not 0.0 <= self.energy <= 1.0:
            raise ValueError("session energy is a fraction")


@dataclass(frozen=True, slots=True)
class LibraryTrack:
    sha256: str
    display_name: str
    analysis: TrackAnalysis


@dataclass(frozen=True, slots=True)
class Recommendation:
    sha256: str
    display_name: str
    score: float
    bpm: float
    reason: str


def session_profile(
    scenes: Sequence[tuple[float, float | None, str | None]],
) -> SessionProfile:
    """From (duration, energy_score 0-100 or None, energy tag or None) per scene."""
    weighted = [(max(d, 1e-3), e) for d, e, _ in scenes if e is not None]
    total = sum(d for d, _ in weighted)
    energy = sum(d * e for d, e in weighted) / total / 100.0 if total else 0.5
    tags = [tag for _, _, tag in scenes if tag is not None]
    high = sum(tag == "high" for tag in tags) / len(tags) if tags else 0.0
    low = sum(tag == "low" for tag in tags) / len(tags) if tags else 0.0
    return SessionProfile(min(1.0, max(0.0, energy)), high, low)


def desired_energy(profile: SessionProfile, pace: Pace) -> float:
    tagged = 0.5 + 0.5 * (profile.high_share - profile.low_share)
    return min(1.0, max(0.0, 0.4 * profile.energy + 0.3 * tagged + 0.3 * pace.energy))


def target_bpm(profile: SessionProfile, pace: Pace) -> float:
    return BPM_LOW + (BPM_HIGH - BPM_LOW) * desired_energy(profile, pace)


def effective_bpm(bpm: float, target: float) -> float:
    """The track's tempo, doubled or halved if that sits closer to the target."""
    return min((bpm / 2, bpm, bpm * 2), key=lambda value: abs(value - target))


def _words(value: float) -> str:
    return "high" if value >= 0.66 else "low" if value < 0.33 else "medium"


def _pacing(profile: SessionProfile, pace: Pace) -> str:
    wanted = desired_energy(profile, pace)
    if wanted >= 0.66:
        return "fast, high-intensity"
    if wanted < 0.33:
        return "slow, heavy-set"
    return "steady"


def recommend(
    tracks: Sequence[LibraryTrack],
    profile: SessionProfile,
    pace: Pace,
    limit: int = TOP_N,
    energy_shift: float = 0.0,
) -> list[Recommendation]:
    """The best ``limit`` tracks, best first. Ties by name, then digest: deterministic.

    ``energy_shift`` is the style profile's music prior (Prompt 10): it moves
    the energy the session asks for, bounded, never the tempo fit. 0.0 is the
    behaviour from before profiles existed, exactly.
    """
    target = target_bpm(profile, pace)
    wanted = min(1.0, max(0.0, desired_energy(profile, pace) + energy_shift))
    ranked = []
    for track in tracks:
        analysis = track.analysis
        bpm = effective_bpm(analysis.bpm, target)
        tempo_fit = max(0.0, 1.0 - abs(bpm - target) / 40.0)
        bars = analysis.bar_energy or (0.5,)
        energy = sum(bars) / len(bars)
        energy_fit = 1.0 - abs(energy - wanted)
        has_drop = analysis.drop_bar is not None
        score = round(0.5 * tempo_fit + 0.35 * energy_fit + (_DROP_BONUS if has_drop else 0.0), 4)
        reason = (
            f"{_words(energy).capitalize()} energy, {analysis.bpm:.0f} BPM "
            f"{'matches' if tempo_fit >= 0.6 else 'is a stretch for'} your "
            f"{_pacing(profile, pace)} pacing"
            + ("; it builds to a drop for your best footage." if has_drop else ".")
        )
        ranked.append(Recommendation(track.sha256, track.display_name, score, analysis.bpm, reason))
    ranked.sort(key=lambda r: (-r.score, r.display_name, r.sha256))
    return ranked[:limit]


__all__ = [
    "LibraryTrack",
    "Recommendation",
    "SessionProfile",
    "desired_energy",
    "effective_bpm",
    "recommend",
    "session_profile",
    "target_bpm",
]
