"""Generated music, for fixtures, gates and review artifacts.

Repcut never downloads music (`.claude/rules/frontend-and-licensing.md`): a
track whose licence nobody verified cannot be bundled, and committed audio is
forbidden outright. Everything that needs a track to exist - the test suite,
`verify-05`, the deferred taste review - generates one here instead, with a
known tempo and a known drop, so the analysis can be checked against truth.

The shape is a plain electronic arrangement: a quiet intro (hi-hats only), a
build (kick and bass enter), a drop (everything, louder) and an outro.
"""

from dataclasses import dataclass

import numpy as np

SAMPLE_RATE = 22050


@dataclass(frozen=True, slots=True)
class SyntheticTrack:
    """A generated track and the truth it was generated from."""

    samples: np.ndarray
    sample_rate: int
    bpm: float
    first_beat_seconds: float
    drop_seconds: float
    duration_seconds: float


def _kick(sample_rate: int) -> np.ndarray:
    length = int(0.18 * sample_rate)
    t = np.arange(length) / sample_rate
    # A pitch sweep from 120 Hz to 45 Hz with a fast decay: a kick, not a click.
    frequency = 45.0 + 75.0 * np.exp(-t * 30.0)
    phase = 2.0 * np.pi * np.cumsum(frequency) / sample_rate
    return np.sin(phase) * np.exp(-t * 18.0)


def _hat(sample_rate: int, rng: np.random.Generator) -> np.ndarray:
    length = int(0.04 * sample_rate)
    t = np.arange(length) / sample_rate
    noise = rng.standard_normal(length)
    # A crude high-pass: the difference of white noise keeps the top end.
    return np.diff(noise, prepend=0.0) * np.exp(-t * 90.0) * 0.25


def _place(track: np.ndarray, sound: np.ndarray, at: int, gain: float) -> None:
    end = min(len(track), at + len(sound))
    if at < end:
        track[at:end] += gain * sound[: end - at]


def generate_track(
    *,
    bpm: float = 128.0,
    bars: int = 32,
    intro_bars: int = 8,
    build_bars: int = 8,
    drop_bars: int = 12,
    first_beat_seconds: float = 0.25,
    sample_rate: int = SAMPLE_RATE,
    seed: int = 5,
) -> SyntheticTrack:
    """A four-on-the-floor track with an intro, a build, a drop and an outro.

    ``intro_bars + build_bars + drop_bars`` must leave at least one bar of outro.
    """
    if intro_bars + build_bars + drop_bars >= bars:
        raise ValueError("the sections must leave at least one bar of outro")
    rng = np.random.default_rng(seed)
    beat = 60.0 / bpm
    duration = first_beat_seconds + bars * 4 * beat + 1.0
    track = np.zeros(int(duration * sample_rate))
    kick = _kick(sample_rate)
    drop_start_bar = intro_bars + build_bars
    for index in range(bars * 4):
        bar = index // 4
        at = int((first_beat_seconds + index * beat) * sample_rate)
        off = int((first_beat_seconds + (index + 0.5) * beat) * sample_rate)
        in_drop = drop_start_bar <= bar < drop_start_bar + drop_bars
        in_intro = bar < intro_bars
        _place(track, _hat(sample_rate, rng), off, 0.6 if in_drop else 0.35)
        if in_intro:
            # A quiet kick: enough for a beat tracker, not enough to read as
            # energy.
            _place(track, kick, at, 0.3 if index % 4 == 0 else 0.2)
            continue
        accent = 1.0 if index % 4 == 0 else 0.8
        _place(track, kick, at, (1.0 if in_drop else 0.6) * accent)
        if in_drop:
            # A bass note per beat and a clap on 2 and 4: the drop is dense.
            t = np.arange(int(beat * 0.9 * sample_rate)) / sample_rate
            root = 55.0 if (bar % 2 == 0) else 49.0
            _place(track, np.sin(2 * np.pi * root * t) * np.exp(-t * 3.0), at, 0.5)
            if index % 4 in (1, 3):
                _place(track, _hat(sample_rate, rng) * 3.0, at, 0.5)
    peak = float(np.max(np.abs(track))) or 1.0
    samples = (track / peak * 0.9).astype(np.float32)
    return SyntheticTrack(
        samples=samples,
        sample_rate=sample_rate,
        bpm=bpm,
        first_beat_seconds=first_beat_seconds,
        drop_seconds=first_beat_seconds + drop_start_bar * 4 * beat,
        duration_seconds=len(samples) / sample_rate,
    )


def to_wav_bytes(samples: np.ndarray, sample_rate: int) -> bytes:
    """16-bit mono PCM WAV, written with the standard library only."""
    import io
    import wave

    clipped = np.clip(samples, -1.0, 1.0)
    pcm = (clipped * 32767.0).astype("<i2").tobytes()
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm)
    return buffer.getvalue()


__all__ = ["SAMPLE_RATE", "SyntheticTrack", "generate_track", "to_wav_bytes"]
