"""Speech regions, from silero-vad's published ONNX model, on the CPU.

Local and free (P4, P5): the audio never leaves the machine, and the model is
MIT-licensed. The ``silero-vad`` pip package is not used because it requires
torch, which amendment 003 defers to Prompt 07; the same model file runs through
onnxruntime instead, with the package's own chunking (512 samples at 16 kHz,
plus 64 samples of context) reproduced here.

The model is not bundled (no binaries in the repo). ``scripts/fetch_models.py``
downloads the pinned release once and checks its sha256; the engine itself
never downloads anything. Without the model, speech detection reports itself
unavailable and ducking is skipped with a message, rather than guessing.
"""

import hashlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import numpy as np

SAMPLE_RATE = 16000
CHUNK = 512
CONTEXT = 64

MODEL_RELEASE = "v5.1.2"
MODEL_URL = "https://github.com/snakers4/silero-vad/raw/v5.1.2/src/silero_vad/data/silero_vad.onnx"
MODEL_SHA256 = "2623a2953f6ff3d2c1e61740c6cdb7168133479b267dfef114a4a3cc5bdd788f"
MODEL_STORED_PATH = PurePosixPath("models", "silero_vad_v5.1.2.onnx")

# The package's defaults: a region opens at 0.5 and closes below 0.35.
START_THRESHOLD = 0.5
END_THRESHOLD = 0.35
MIN_SPEECH_SECONDS = 0.25
MIN_SILENCE_SECONDS = 0.3
# Kept around speech so a duck never clips a breath (beat-and-audio skill).
SPEECH_PAD_SECONDS = 0.12


class VadUnavailableError(RuntimeError):
    """The model is missing or does not match its pinned digest. A fixed sentence."""


@dataclass(frozen=True, slots=True)
class SpeechRegion:
    start: float
    end: float


def verify_model(path: Path) -> None:
    if not path.is_file():
        raise VadUnavailableError(
            "the speech detection model is not installed; run scripts/fetch_models.py"
        )
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != MODEL_SHA256:
        raise VadUnavailableError("the speech detection model does not match its pinned version")


def speech_probabilities(samples: np.ndarray, model_path: Path) -> np.ndarray:
    """One speech probability per 32 ms chunk of 16 kHz mono audio."""
    verify_model(model_path)
    import onnxruntime

    session = onnxruntime.InferenceSession(
        model_path.as_posix(), providers=["CPUExecutionProvider"]
    )
    audio = np.asarray(samples, dtype=np.float32)
    state = np.zeros((2, 1, 128), dtype=np.float32)
    context = np.zeros(CONTEXT, dtype=np.float32)
    rate = np.array(SAMPLE_RATE, dtype=np.int64)
    count = int(np.ceil(len(audio) / CHUNK))
    padded = np.zeros(count * CHUNK, dtype=np.float32)
    padded[: len(audio)] = audio
    probabilities = np.zeros(count, dtype=np.float32)
    for index in range(count):
        chunk = padded[index * CHUNK : (index + 1) * CHUNK]
        window = np.concatenate([context, chunk])[np.newaxis, :]
        output, state = session.run(None, {"input": window, "state": state, "sr": rate})
        probabilities[index] = float(np.asarray(output).reshape(-1)[0])
        context = chunk[-CONTEXT:]
    return probabilities


def regions_from_probabilities(
    probabilities: np.ndarray, duration_seconds: float
) -> tuple[SpeechRegion, ...]:
    """Hysteresis over chunk probabilities, then minimum lengths and padding."""
    step = CHUNK / SAMPLE_RATE
    raw: list[list[float]] = []
    open_at: float | None = None
    for index, probability in enumerate(probabilities):
        time = index * step
        if open_at is None and probability >= START_THRESHOLD:
            open_at = time
        elif open_at is not None and probability < END_THRESHOLD:
            raw.append([open_at, time])
            open_at = None
    if open_at is not None:
        raw.append([open_at, len(probabilities) * step])

    merged: list[list[float]] = []
    for start, end in raw:
        if merged and start - merged[-1][1] < MIN_SILENCE_SECONDS:
            merged[-1][1] = end
        else:
            merged.append([start, end])
    return tuple(
        SpeechRegion(
            round(max(0.0, start - SPEECH_PAD_SECONDS), 3),
            round(min(duration_seconds, end + SPEECH_PAD_SECONDS), 3),
        )
        for start, end in merged
        if end - start >= MIN_SPEECH_SECONDS
    )


def detect_speech(samples: np.ndarray, model_path: Path) -> tuple[SpeechRegion, ...]:
    """Speech regions in 16 kHz mono audio, in seconds from its start."""
    probabilities = speech_probabilities(samples, model_path)
    return regions_from_probabilities(probabilities, len(samples) / SAMPLE_RATE)


__all__ = [
    "MODEL_SHA256",
    "MODEL_STORED_PATH",
    "MODEL_URL",
    "SAMPLE_RATE",
    "SpeechRegion",
    "VadUnavailableError",
    "detect_speech",
    "regions_from_probabilities",
    "speech_probabilities",
    "verify_model",
]
