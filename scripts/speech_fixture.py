#!/usr/bin/env python3
"""Known speech for the caption gate: sentences we wrote, spoken by a local voice.

``verify-06`` measures the recogniser's word error rate against text it knows,
so the audio must say exactly that text. It is synthesised at gate time by
Piper (GPL-3.0, a dev-only dependency, never shipped with the engine) from two
voices trained on public-domain (LJSpeech) and CC0 (Thorsten) recordings,
fetched by ``fetch_models.py --gate``. Nothing is committed: the audio exists
only in a temporary folder for the length of the check.

English and German, because gym talk here is both, and one clip that switches
between them is what ``multilingual=True`` is for.
"""

from __future__ import annotations

import io
import re
import unicodedata
import wave
from pathlib import Path

import numpy as np

SENTENCES = {
    "en": (
        "Keep your back straight and push through your heels on every rep. "
        "We rest after this set and then we go again."
    ),
    "de": (
        "Halte den Rücken gerade und drück dich aus den Fersen hoch. "
        "Nach diesem Satz machen wir eine kurze Pause."
    ),
}
VOICE_FOLDERS = {"en": "voice-en", "de": "voice-de"}
SAMPLE_RATE = 16000


class FixtureUnavailableError(RuntimeError):
    """Piper or a voice is missing here; the message says how to install it."""


def voice_path(data_dir: Path, language: str) -> Path:
    return data_dir / "models" / VOICE_FOLDERS[language] / "voice.onnx"


def synthesize(data_dir: Path, language: str) -> np.ndarray:
    """16 kHz mono float32 of ``SENTENCES[language]``."""
    try:
        from piper import PiperVoice
    except ImportError as error:
        raise FixtureUnavailableError(
            'piper-tts is not installed: pip install -e "engine[dev]"'
        ) from error
    model = voice_path(data_dir, language)
    if not model.is_file():
        raise FixtureUnavailableError(
            f"the {language} test voice is not installed: python scripts/fetch_models.py --gate"
        )
    voice = PiperVoice.load(str(model))
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        voice.synthesize_wav(SENTENCES[language], handle)
    buffer.seek(0)
    with wave.open(buffer, "rb") as handle:
        rate = handle.getframerate()
        frames = np.frombuffer(handle.readframes(handle.getnframes()), dtype="<i2")
    samples = frames.astype(np.float32) / 32768.0
    if rate != SAMPLE_RATE:
        import librosa

        samples = librosa.resample(samples, orig_sr=rate, target_sr=SAMPLE_RATE)
    return samples.astype(np.float32)


def normalise(text: str) -> list[str]:
    """Lowercase words, punctuation removed, umlauts kept, ß spelled ss."""
    text = unicodedata.normalize("NFC", text).casefold().replace("ß", "ss")
    return re.findall(r"[\w']+", text)


def word_error_rate(reference: str, hypothesis: str) -> float:
    ref, hyp = normalise(reference), normalise(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    previous = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, start=1):
        current = [i]
        for j, h in enumerate(hyp, start=1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (r != h)))
        previous = current
    return previous[-1] / len(ref)


__all__ = [
    "SAMPLE_RATE",
    "SENTENCES",
    "FixtureUnavailableError",
    "normalise",
    "synthesize",
    "voice_path",
    "word_error_rate",
]
