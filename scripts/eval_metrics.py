#!/usr/bin/env python3
"""The arithmetic behind ``scripts/eval_pipeline.py``: pure, testable, no I/O.

Every product claim the eval gates reduces to one of these functions, so each
can be proven on a known answer (``engine/tests/test_eval_pipeline.py``) apart
from the footage it is later run on. Nothing here reads a file, a model or the
network.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field

import numpy as np

# One word-error-rate for the whole repo: verify-06 and the eval share it.
from speech_fixture import word_error_rate

PASS = "PASS"  # noqa: S105 - a verdict word, not a credential
FAIL = "FAIL"
# An input this machine does not have: a fixture or label Ashwin has not
# written yet, or a model that is not installed. Never a PASS (amendment 017):
# `make eval` exits 3 on it and strict mode turns it into a FAIL.
BOUND = "BOUND"

EXIT_PASS, EXIT_FAIL, EXIT_BOUND = 0, 1, 3


@dataclass(frozen=True)
class GateResult:
    """One product claim, measured. ``metric`` names what was measured."""

    gate: str
    claim: str
    status: str
    metric: str
    value: str
    bound: str
    detail: str = ""
    inputs: dict[str, str] = field(default_factory=dict)

    def line(self) -> str:
        text = f"[{self.status}] {self.gate}: {self.metric} = {self.value} (bound {self.bound})"
        return f"{text} - {self.detail}" if self.detail else text


def exit_code(results: Sequence[GateResult], strict: bool = False) -> int:
    """1 if any gate failed (or any is bound under strict), 3 if any is bound, else 0.

    An empty result list is a failure: a scorecard that measured nothing has
    not shown that anything holds."""
    if not results or any(r.status == FAIL for r in results):
        return EXIT_FAIL
    if any(r.status == BOUND for r in results):
        return EXIT_FAIL if strict else EXIT_BOUND
    return EXIT_PASS


def to_json(results: Sequence[GateResult], meta: dict[str, str]) -> str:
    return json.dumps({"meta": meta, "gates": [asdict(r) for r in results]}, indent=2)


# ------------------------------------------------------------------ beat sync
def frame_differences(frames: np.ndarray) -> np.ndarray:
    """Mean absolute change between consecutive frames; index i is the change
    INTO frame i (index 0 is 0). ``frames`` is (n, h, w) grey, any dtype."""
    if len(frames) < 2:
        return np.zeros(len(frames), dtype=np.float64)
    f = frames.astype(np.float64)
    diffs = np.abs(np.diff(f, axis=0)).mean(axis=(1, 2))
    return np.concatenate([[0.0], diffs])


def locate_cuts(
    diffs: np.ndarray, fps: float, expected: Sequence[float], window: float = 0.15
) -> list[float | None]:
    """Where each expected cut actually happens: the strongest frame change
    within ``window`` seconds of it. ``None`` when that change does not stand
    out (under 2x the window's median), i.e. no visible cut there."""
    found: list[float | None] = []
    for t in expected:
        lo = max(1, int(np.floor((t - window) * fps)))
        hi = min(len(diffs) - 1, int(np.ceil((t + window) * fps)))
        if hi < lo:
            found.append(None)
            continue
        part = diffs[lo : hi + 1]
        i = int(np.argmax(part))
        median = float(np.median(part)) or 1e-9
        found.append((lo + i) / fps if part[i] >= 2.0 * median and part[i] > 0 else None)
    return found


def onset_envelope(samples: np.ndarray, sample_rate: int, hop: int = 256) -> np.ndarray:
    """Positive spectral-energy flux per hop: where sound starts. Mono float."""
    n = len(samples) // hop
    if n < 2:
        return np.zeros(max(n, 0))
    frames = samples[: n * hop].reshape(n, hop).astype(np.float64)
    energy = np.log1p(np.sqrt((frames**2).mean(axis=1)) * 1000.0)
    flux = np.maximum(np.diff(energy, prepend=energy[0]), 0.0)
    return flux


def audio_lag(rendered: np.ndarray, reference: np.ndarray, rate: float, max_lag: float) -> float:
    """Seconds by which ``rendered`` trails ``reference`` (positive = later),
    from the cross-correlation of their onset envelopes (``rate`` = envelope
    frames per second), searched within +/- ``max_lag``."""
    n = min(len(rendered), len(reference))
    a = rendered[:n] - rendered[:n].mean()
    b = reference[:n] - reference[:n].mean()
    k = round(max_lag * rate)
    best, best_lag = -np.inf, 0
    for lag in range(-k, k + 1):
        if lag >= 0:
            score = float(np.dot(a[lag:], b[: n - lag]))
        else:
            score = float(np.dot(a[: n + lag], b[-lag:]))
        if score > best:
            best, best_lag = score, lag
    return best_lag / rate


def beat_errors(cuts: Sequence[float], beats: Sequence[float]) -> list[float]:
    """For each cut, the distance in seconds to its nearest beat."""
    grid = np.asarray(sorted(beats), dtype=np.float64)
    if grid.size == 0:
        return [float("inf") for _ in cuts]
    return [float(np.min(np.abs(grid - c))) for c in cuts]


# ---------------------------------------------------------------- the others
def hue_drift_degrees(before_rgb: Sequence[float], after_rgb: Sequence[float]) -> float:
    """Shortest angle between two colours' hues, in degrees (0..180)."""

    def hue(rgb: Sequence[float]) -> float:
        r, g, b = (float(v) for v in rgb)
        mx, mn = max(r, g, b), min(r, g, b)
        if mx == mn:
            return 0.0
        d = mx - mn
        if mx == r:
            h = ((g - b) / d) % 6
        elif mx == g:
            h = (b - r) / d + 2
        else:
            h = (r - g) / d + 4
        return 60.0 * h

    delta = hue(after_rgb) - hue(before_rgb)
    return abs((delta + 180.0) % 360.0 - 180.0)


def revert_rate(accepted: Sequence[bool]) -> float:
    """Share of artifact-prone slow-motion requests that fell back to normal speed."""
    return 1.0 - (sum(1 for a in accepted if a) / len(accepted)) if accepted else 0.0


def within_one(counted: Sequence[int | None], truth: Sequence[int]) -> list[bool]:
    return [c is not None and abs(c - t) <= 1 for c, t in zip(counted, truth, strict=True)]


def middle_third_fraction(
    subject_x: Sequence[float | None], crop_x: Sequence[float], crop_width: float
) -> float:
    """Share of frames with a detected subject whose centre lies in the crop's
    middle third. Frames without a detection are not counted either way."""
    inside = total = 0
    for sx, cx in zip(subject_x, crop_x, strict=True):
        if sx is None:
            continue
        total += 1
        inside += cx + crop_width / 3 <= sx <= cx + 2 * crop_width / 3
    return inside / total if total else 0.0


__all__ = ["BOUND", "FAIL", "PASS", "GateResult", "word_error_rate"]
