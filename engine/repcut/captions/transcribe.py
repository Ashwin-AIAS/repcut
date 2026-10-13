"""Local speech-to-text with word timings, and the guard against invented captions.

faster-whisper (MIT; CTranslate2, MIT; Whisper weights, MIT) runs on this
machine only (P4): no audio leaves it. The "small" model, multilingual, with
language detection on every segment, because gym talk here switches between
German and English mid-sentence.

The model is never downloaded by the engine. ``scripts/fetch_models.py`` puts it
under ``$DATA_DIR/models/`` once and records each file's sha256; without it the
caption step says so and the export carries on without captions.

**The guard** (Whisper's known failure: it writes plausible text over music):
a segment is dropped when its mean word probability is below 0.5 AND it
overlaps no speech region the voice detector found. Either signal alone keeps
it: a confident line over music is probably a shout the detector missed, and a
mumbled line over detected speech is probably real.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Protocol

import numpy as np

from repcut.captions.types import CaptionSegment, Transcript, Word
from repcut.edit.vad import SpeechRegion
from repcut.logging import get_logger

logger = get_logger(__name__)

MODEL_NAME = "faster-whisper-small"
# Where fetch_models.py gets it: the CTranslate2 conversion of Whisper small
# published by the faster-whisper authors. Files, not a pickled object.
MODEL_REPOSITORY = "Systran/faster-whisper-small"
MODEL_FILES = ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt")
MODEL_STORED_DIR = PurePosixPath("models", MODEL_NAME)
PINS_FILENAME = "PINNED.json"
SAMPLE_RATE = 16000

MIN_MEAN_PROBABILITY = 0.5


class CaptionModelUnavailableError(RuntimeError):
    """The speech-to-text model is not installed here. A fixed sentence for the UI."""


MODEL_MISSING = (
    "the speech-to-text model is not installed; run scripts/fetch_models.py once "
    "(captions are left out until then)"
)


class _RawWord(Protocol):
    start: float
    end: float
    word: str
    probability: float


class _RawSegment(Protocol):
    id: int
    start: float
    end: float
    words: Sequence[_RawWord] | None


class Recogniser(Protocol):
    """The slice of ``faster_whisper.WhisperModel`` used here; a fake in tests."""

    def transcribe(
        self, audio: np.ndarray, **options: object
    ) -> tuple[Iterable[_RawSegment], object]: ...


def model_dir(data_dir: Path) -> Path:
    return data_dir.joinpath(*MODEL_STORED_DIR.parts)


def model_installed(data_dir: Path) -> bool:
    folder = model_dir(data_dir)
    return all((folder / name).is_file() for name in (*MODEL_FILES, PINS_FILENAME))


@dataclass(frozen=True, slots=True)
class LoadedModel:
    recogniser: Recogniser
    device: str


@lru_cache(maxsize=1)
def _load(folder: str) -> LoadedModel:
    from faster_whisper import WhisperModel  # local import: heavy, and optional in tests

    # GPU first (float16 weights, int8 matmuls: ~0.6 GB of the 3050's 4 GB),
    # the CPU when CUDA or its libraries are absent. Which one ran is logged.
    try:
        model = WhisperModel(
            folder, device="cuda", compute_type="int8_float16", local_files_only=True
        )
        return LoadedModel(model, "cuda")
    except (RuntimeError, ValueError, OSError) as error:
        logger.info("speech_to_text_on_cpu", reason=type(error).__name__)
    model = WhisperModel(folder, device="cpu", compute_type="int8", local_files_only=True)
    return LoadedModel(model, "cpu")


def load_model(data_dir: Path) -> LoadedModel:
    if not model_installed(data_dir):
        raise CaptionModelUnavailableError(MODEL_MISSING)
    return _load(str(model_dir(data_dir)))


def overlaps_speech(start: float, end: float, speech: Sequence[SpeechRegion] | None) -> bool:
    if not speech:
        return False
    return any(region.start < end and start < region.end for region in speech)


def keep_segment(segment: CaptionSegment, speech: Sequence[SpeechRegion] | None) -> bool:
    """The guard. Dropped only when BOTH signals say it is not speech."""
    if not segment.words:
        return False
    unsure = segment.mean_probability < MIN_MEAN_PROBABILITY
    return not (unsure and not overlaps_speech(segment.start, segment.end, speech))


def _segments(raw: Iterable[_RawSegment]) -> list[CaptionSegment]:
    out: list[CaptionSegment] = []
    for index, segment in enumerate(raw):
        words = tuple(
            Word(float(w.start), float(w.end), w.word.strip(), float(w.probability))
            for w in (segment.words or ())
            if w.word.strip()
        )
        out.append(
            CaptionSegment(
                index=index, start=float(segment.start), end=float(segment.end), words=words
            )
        )
    return out


def transcribe_samples(
    recogniser: Recogniser,
    samples: np.ndarray,
    *,
    sha256: str,
    speech: Sequence[SpeechRegion] | None,
) -> Transcript:
    """16 kHz mono float32 in, guarded transcript out. Blocking: run in a thread."""
    raw, info = recogniser.transcribe(
        samples,
        language=None,
        multilingual=True,
        word_timestamps=True,
        beam_size=5,
        condition_on_previous_text=False,
        vad_filter=False,
    )
    every = _segments(raw)
    kept = tuple(s for s in every if keep_segment(s, speech))
    return Transcript(
        sha256=sha256,
        model=MODEL_NAME,
        language=getattr(info, "language", None),
        segments=kept,
        dropped=len(every) - len(kept),
        speech_measured=speech is not None,
    )


async def transcribe(
    data_dir: Path, samples: np.ndarray, *, sha256: str, speech: Sequence[SpeechRegion] | None
) -> Transcript:
    loaded = await asyncio.to_thread(load_model, data_dir)
    logger.info("speech_to_text", device=loaded.device)
    return await asyncio.to_thread(
        transcribe_samples, loaded.recogniser, samples, sha256=sha256, speech=speech
    )


def read_pins(data_dir: Path) -> dict[str, str]:
    path = model_dir(data_dir) / PINS_FILENAME
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {str(k): str(v) for k, v in data.get("sha256", {}).items()}


__all__ = [
    "MIN_MEAN_PROBABILITY",
    "MODEL_FILES",
    "MODEL_MISSING",
    "MODEL_NAME",
    "MODEL_REPOSITORY",
    "MODEL_STORED_DIR",
    "PINS_FILENAME",
    "SAMPLE_RATE",
    "CaptionModelUnavailableError",
    "LoadedModel",
    "Recogniser",
    "keep_segment",
    "load_model",
    "model_dir",
    "model_installed",
    "overlaps_speech",
    "read_pins",
    "transcribe",
    "transcribe_samples",
]
