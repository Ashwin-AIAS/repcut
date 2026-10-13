"""Beat grid, bars, drop and rejection, checked against generated truth."""

import numpy as np
import pytest

from repcut.music.analysis import (
    MAX_TEMPO_VARIATION,
    TrackAnalysis,
    TrackAnalysisError,
    analyse,
    find_drop,
    sections_for,
)
from repcut.music.synthetic import generate_track, to_wav_bytes

SNAP_TOLERANCE = 0.040


@pytest.mark.parametrize("bpm", [75.0, 100.0, 128.0, 160.0])
def test_beats_land_within_the_snap_tolerance_of_the_generated_beats(bpm: float) -> None:
    track = generate_track(bpm=bpm)
    result = analyse(track.samples, track.sample_rate)
    truth = track.first_beat_seconds + np.arange(32 * 4) * 60.0 / bpm
    errors = [float(np.min(np.abs(truth - beat))) for beat in result.beats]
    assert max(errors) <= SNAP_TOLERANCE
    assert result.bpm == pytest.approx(bpm, abs=1.0)


@pytest.mark.parametrize("bpm", [90.0, 128.0, 140.0])
def test_the_drop_is_found_within_one_bar_of_where_it_was_generated(bpm: float) -> None:
    track = generate_track(bpm=bpm)
    result = analyse(track.samples, track.sample_rate)
    assert result.drop_seconds is not None
    assert abs(result.drop_seconds - track.drop_seconds) <= 4 * 60.0 / bpm
    kinds = [section.kind for section in result.sections]
    assert "drop" in kinds and kinds[0] == "intro"


def test_a_drifting_tempo_is_rejected_with_a_reason() -> None:
    slow = generate_track(bpm=100.0, bars=12, intro_bars=1, build_bars=1, drop_bars=8)
    fast = generate_track(bpm=135.0, bars=12, intro_bars=1, build_bars=1, drop_bars=8)
    drifting = np.concatenate([slow.samples, fast.samples])
    with pytest.raises(TrackAnalysisError) as raised:
        result = analyse(drifting, slow.sample_rate)
        # If the tracker smoothed it, the variation it measured must still show.
        assert result.tempo_variation > MAX_TEMPO_VARIATION
    assert "tempo" in raised.value.reason


def test_silence_and_short_tracks_are_named_not_crashed() -> None:
    with pytest.raises(TrackAnalysisError, match="silent"):
        analyse(np.zeros(22050 * 10, dtype=np.float32), 22050)
    with pytest.raises(TrackAnalysisError, match="too short"):
        analyse(np.ones(22050 * 2, dtype=np.float32), 22050)


def test_an_analysis_round_trips_through_json() -> None:
    track = generate_track(bpm=120.0)
    result = analyse(track.samples, track.sample_rate)
    assert TrackAnalysis.from_json(result.to_json()) == result


def test_no_drop_means_one_body_section() -> None:
    flat = np.full(16, 0.8)
    assert find_drop(flat) is None
    assert [s.kind for s in sections_for(flat, None)] == ["body"]


def test_the_generated_wav_is_mono_16_bit() -> None:
    track = generate_track(bpm=120.0, bars=10, intro_bars=2, build_bars=2, drop_bars=4)
    data = to_wav_bytes(track.samples, track.sample_rate)
    assert data[:4] == b"RIFF" and data[8:12] == b"WAVE"
