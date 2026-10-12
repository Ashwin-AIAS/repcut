"""Ducking: the envelope's numbers, and the reduction measured in rendered audio."""

import asyncio
import shutil
import wave
from pathlib import Path

import numpy as np
import pytest

from repcut.edit import ducking
from repcut.edit.types import Plan, Shot
from repcut.edit.vad import (
    SpeechRegion,
    VadUnavailableError,
    regions_from_probabilities,
    verify_model,
)
from repcut.media.ffmpeg_builder import (
    FFmpegFilterGraphError,
    UnsafeSourceError,
    build_music_stem,
    build_pcm_decode,
    run,
)
from repcut.music.synthetic import generate_track, to_wav_bytes

SHA = "f" * 64


def _plan() -> Plan:
    shots = (
        Shot("a", "s1", SHA, 10.0, 20.0, 0.0, 10.0, 0, 20),
        Shot("b", "s2", SHA, 30.0, 40.0, 10.0, 20.0, 20, 40),
    )
    return Plan(shots, 20.0, 20.0, 5.0, True, "x" * 64, 1)


def test_speech_maps_through_the_plan_onto_the_timeline() -> None:
    speech = {SHA: (SpeechRegion(12.0, 14.0), SpeechRegion(33.0, 36.5), SpeechRegion(25, 27))}
    assert ducking.timeline_speech(_plan(), speech) == [(2.0, 4.0), (13.0, 16.5)]
    assert ducking.timeline_speech(_plan(), {SHA: None}) == []


def test_the_envelope_ducks_under_speech_and_swells_on_action() -> None:
    plan = _plan()
    ramps = ducking.envelope(plan, [(2.0, 4.0)], {"s1": 0.1, "s2": 0.95})
    assert ducking.gain_at(ramps, 3.0) == pytest.approx(ducking.DUCK_DB)
    assert ducking.gain_at(ramps, 7.0) == pytest.approx(ducking.BASE_DB)
    assert ducking.gain_at(ramps, 15.0) == pytest.approx(ducking.BASE_DB + ducking.SWELL_DB)
    # The ramp is gradual: half way through the attack is half the duck.
    halfway = 2.0 - ducking.ATTACK_SECONDS / 2
    assert ducking.gain_at(ramps, halfway) == pytest.approx(
        ducking.BASE_DB + (ducking.DUCK_DB - ducking.BASE_DB) / 2
    )


def test_close_speech_regions_merge_into_one_duck() -> None:
    speech = {SHA: (SpeechRegion(12.0, 13.0), SpeechRegion(13.3, 14.0))}
    assert ducking.timeline_speech(_plan(), speech) == [(2.0, 4.0)]


def _rms_db(path: Path, start: float, end: float) -> float:
    with wave.open(str(path)) as handle:
        rate = handle.getframerate()
        frames = np.frombuffer(handle.readframes(handle.getnframes()), dtype="<i2")
    samples = frames.reshape(-1, 2).astype(float)[int(start * rate) : int(end * rate)]
    return float(20 * np.log10(np.sqrt(np.mean(samples**2))))


def test_rendered_music_is_at_least_8_db_lower_under_speech(tmp_path: Path) -> None:
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg is not on PATH; `make check-env` reports it with a fix")
    track = generate_track(bpm=128, bars=40, intro_bars=2, build_bars=2, drop_bars=30)
    music = tmp_path / "music.wav"
    music.write_bytes(to_wav_bytes(track.samples, track.sample_rate))
    plan = _plan()
    speech_on_timeline = ducking.timeline_speech(plan, {SHA: (SpeechRegion(12.0, 14.0),)})
    ramps = ducking.envelope(plan, speech_on_timeline, {})

    async def render(name: str, expression: str) -> Path:
        out = tmp_path / name
        await run(
            build_music_stem(
                music, out, start_seconds=5.0, duration_seconds=20.0,
                gain_db_expression=expression,
            )
        )  # fmt: skip
        return out

    ducked = asyncio.run(render("ducked.wav", ducking.gain_expression(ramps)))
    plain = asyncio.run(render("plain.wav", "0"))
    under_speech = _rms_db(plain, 2.0, 4.0) - _rms_db(ducked, 2.0, 4.0)
    against_base = _rms_db(ducked, 6.0, 9.0) - _rms_db(ducked, 2.0, 4.0)
    assert under_speech >= 8.0
    assert against_base >= 8.0


@pytest.mark.parametrize("expression", ["pow(2,3)", "0';ls'", "t;amix", "1/0, aeval=1"])
def test_a_gain_expression_outside_the_allow_list_is_refused(expression: str) -> None:
    with pytest.raises(FFmpegFilterGraphError):
        build_music_stem(
            Path("m.wav"), Path("o.wav"), start_seconds=0, duration_seconds=1,
            gain_db_expression=expression,
        )  # fmt: skip


@pytest.mark.parametrize("source", ["concat:a.mp3|b.mp3", "http://example.com/a.mp3", "-y"])
def test_a_track_that_names_a_protocol_is_never_decoded(source: str) -> None:
    with pytest.raises(UnsafeSourceError):
        build_pcm_decode(Path(source), Path("out.f32"), sample_rate=22050)


def test_pcm_decode_is_mono_float_at_a_known_rate() -> None:
    argv = build_pcm_decode(Path("track.wav"), Path("out.f32"), sample_rate=22050).argv
    assert argv[argv.index("-ac") + 1] == "1"
    assert argv[argv.index("-ar") + 1] == "22050"
    assert argv[-3:] == ["-f", "f32le", "out.f32"]
    with pytest.raises(ValueError, match="sample rate"):
        build_pcm_decode(Path("t.wav"), Path("o"), sample_rate=12345)


def test_a_missing_or_altered_model_is_named(tmp_path: Path) -> None:
    with pytest.raises(VadUnavailableError, match="not installed"):
        verify_model(tmp_path / "absent.onnx")
    altered = tmp_path / "altered.onnx"
    altered.write_bytes(b"not the model")
    with pytest.raises(VadUnavailableError, match="pinned"):
        verify_model(altered)


def test_speech_regions_open_high_close_low_and_are_padded() -> None:
    probabilities = np.array([0.1] * 10 + [0.9] * 20 + [0.4] * 3 + [0.1] * 20, dtype=np.float32)
    (region,) = regions_from_probabilities(probabilities, 2.0)
    step = 512 / 16000
    assert region.start == pytest.approx(10 * step - 0.12, abs=1e-3)
    assert region.end == pytest.approx(33 * step + 0.12, abs=1e-3)
    blip = np.array([0.1] * 10 + [0.9] * 3 + [0.1] * 20, dtype=np.float32)
    assert regions_from_probabilities(blip, 2.0) == ()
