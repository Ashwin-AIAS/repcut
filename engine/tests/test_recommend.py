"""Track recommendation: tempo and energy against the session, with a reason."""

from dataclasses import replace

from repcut.edit.types import Pace
from repcut.music.analysis import Section, TrackAnalysis
from repcut.music.recommend import (
    LibraryTrack,
    SessionProfile,
    effective_bpm,
    recommend,
    session_profile,
    target_bpm,
)


def _track(name: str, bpm: float, energy: float, drop: bool = True) -> LibraryTrack:
    analysis = TrackAnalysis(
        version=1,
        duration_seconds=120.0,
        bpm=bpm,
        beats=tuple(i * 60 / bpm for i in range(200)),
        downbeat_offset=0,
        bar_energy=(energy,) * 40,
        energy_curve=(energy,) * 10,
        sections=(Section("body", 0, 40),),
        tempo_variation=0.01,
        drop_bar=8 if drop else None,
    )
    return LibraryTrack(name * 64 if len(name) == 1 else name, name, analysis)


TRACKS = [_track("a", 140, 0.9), _track("b", 92, 0.35), _track("c", 120, 0.6, drop=False)]


def test_an_intense_session_gets_the_fast_loud_track_first() -> None:
    intense = SessionProfile(energy=0.9, high_share=0.8)
    ranked = recommend(TRACKS, intense, Pace(energy=0.9))
    assert ranked[0].display_name == "a"
    assert "140 BPM" in ranked[0].reason and "drop" in ranked[0].reason


def test_a_heavy_slow_session_gets_the_slow_track_first() -> None:
    heavy = SessionProfile(energy=0.2, low_share=0.7)
    ranked = recommend(TRACKS, heavy, Pace(energy=0.3))
    assert ranked[0].display_name == "b"
    assert "heavy-set" in ranked[0].reason


def test_half_time_counts_as_the_same_tempo() -> None:
    assert effective_bpm(70.0, 140.0) == 140.0
    assert effective_bpm(280.0, 140.0) == 140.0


def test_at_most_five_and_deterministic() -> None:
    many = [
        replace(_track("a", 100 + i, 0.5), sha256=f"{i:064x}", display_name=f"t{i}")
        for i in range(9)
    ]
    profile = SessionProfile(energy=0.5)
    first = recommend(many, profile, Pace())
    assert len(first) == 5
    assert first == recommend(list(reversed(many)), profile, Pace())


def test_the_session_profile_weights_scenes_by_duration() -> None:
    profile = session_profile([(9.0, 90.0, "high"), (1.0, 10.0, "low"), (5.0, None, None)])
    assert abs(profile.energy - 0.82) < 1e-6
    assert profile.high_share == 0.5 and profile.low_share == 0.5
    assert session_profile([]).energy == 0.5
    assert target_bpm(SessionProfile(1.0, 1.0), Pace(energy=1.0)) == 150.0
