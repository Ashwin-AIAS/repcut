"""Slow motion: the 2x ceiling, the weights check, the confidence gate and its memory steps.

None of these need torch or the weights: the interpolator is replaced by small
stand-ins whose output is known, so the verdicts are about the scoring, not
about RIFE. RIFE itself is exercised by ``verify-07`` (criteria 2 and 3).
"""

from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, cast

import numpy as np
import pytest

from repcut.config import Settings
from repcut.edit.types import Shot
from repcut.slowmo import confidence, limits, weights
from repcut.slowmo.rife import FLOW_SCALES, RifeInterpolator, initial_flow_scale
from repcut.slowmo.runtime import InterpolationSetup, SlowmoUnavailableError, open_interpolator
from repcut.slowmo.shot import FrameShape, interpolate_shot, output_frames_needed, read_frames

H, W = 96, 160


def _texture(seed: int = 3, width: int = W + 400) -> np.ndarray:
    rng = np.random.default_rng(seed)
    noise = rng.random((H, width, 3)).astype(np.float32)
    import cv2

    noise = cv2.GaussianBlur(noise, (0, 0), 3)
    noise = (noise - noise.min()) / (noise.max() - noise.min())
    return (30 + 200 * noise).astype(np.uint8)


def _pan(step: float, frames: int, seed: int = 3) -> list[np.ndarray]:
    """A camera pan over a smooth texture, ``step`` pixels per frame (sub-pixel exact)."""
    world = _texture(seed).astype(np.float32)
    out = []
    for index in range(frames):
        x = index * step
        left = int(np.floor(x))
        frac = x - left
        a = world[:, left : left + W]
        b = world[:, left + 1 : left + 1 + W]
        out.append(((1 - frac) * a + frac * b).round().astype(np.uint8))
    return out


class TruthfulPan:
    """Knows the pan exactly: its midpoint IS the real half-step frame."""

    def __init__(self, step: float, frames: list[np.ndarray]) -> None:
        self.step = step
        self.world = _texture().astype(np.float32)
        self.frames = frames

    def midpoint(self, first: np.ndarray, second: np.ndarray) -> np.ndarray:
        def position(frame: np.ndarray) -> int:
            return next(i for i, f in enumerate(self.frames) if np.array_equal(f, frame))

        x = (position(first) + position(second)) / 2 * self.step
        left = int(np.floor(x))
        frac = x - left
        a = self.world[:, left : left + W]
        b = self.world[:, left + 1 : left + 1 + W]
        result: np.ndarray = ((1 - frac) * a + frac * b).round().astype(np.uint8)
        return result


class Blend:
    """A cross-fade: right for still pictures, a double image for fast motion."""

    def midpoint(self, first: np.ndarray, second: np.ndarray) -> np.ndarray:
        result: np.ndarray = ((first.astype(np.uint16) + second) // 2).astype(np.uint8)
        return result


class Garbage:
    """Shows something that never happened: the picture upside down."""

    def midpoint(self, first: np.ndarray, second: np.ndarray) -> np.ndarray:
        result: np.ndarray = np.ascontiguousarray(first[::-1])
        return result


# --- the 2x ceiling (P1) ----------------------------------------------------------


@pytest.mark.parametrize("speed", [0.25, 0.3333, 0.49, 0.75, 1.5, 2.0, 0.0, -0.5])
def test_no_speed_beyond_2x_or_between_exists(speed: float) -> None:
    with pytest.raises(limits.SlowdownLimitError):
        limits.checked_speed(speed)


def test_the_ceiling_is_two_and_half_speed_is_the_slowest() -> None:
    assert limits.MAX_SLOWDOWN == 2
    assert limits.SLOWEST_SPEED == 0.5
    assert limits.checked_speed(0.5) == 0.5
    assert limits.checked_speed(1.0) == 1.0


def test_a_stored_plan_cannot_ask_for_more_than_2x() -> None:
    raw = Shot("a" * 16, "s", "f" * 64, 0.0, 0.5, 0.0, 2.0, None, None).to_json()
    raw.update(speed=0.25, fallback_in=0.0)
    with pytest.raises(limits.SlowdownLimitError):
        Shot.from_json(raw)
    raw.update(speed=0.5, fallback_in=None)
    with pytest.raises(ValueError, match="normal-speed window"):
        Shot.from_json(raw)


def test_no_setting_reaches_the_slowdown() -> None:
    names = " ".join(Settings.model_fields).casefold()
    assert "slow" not in names and "interpol" not in names and "speed" not in names


# --- the weights (security, guide section 7) --------------------------------------


class _ExplodingTorch(ModuleType):
    def __init__(self) -> None:
        super().__init__("torch")
        self.loaded: list[Path] = []

    def load(self, path: Path, **kwargs: object) -> dict[str, Any]:
        self.loaded.append(path)
        assert kwargs.get("weights_only") is True
        return {"module.block0.x": 1, "other": 2}


def test_missing_weights_are_named_and_never_loaded(tmp_path: Path) -> None:
    torch = _ExplodingTorch()
    with pytest.raises(weights.WeightsUnavailableError, match="not installed"):
        weights.load_verified(tmp_path / "absent.pkl", torch)
    assert torch.loaded == []


def test_tampered_weights_are_refused_before_torch_load(tmp_path: Path) -> None:
    fake = tmp_path / "flownet.pkl"
    fake.write_bytes(b"\0" * weights.RIFE_SIZE_BYTES)
    torch = _ExplodingTorch()
    with pytest.raises(weights.WeightsUnavailableError, match="pinned version"):
        weights.load_verified(fake, torch)
    assert torch.loaded == [], "torch.load ran on an unverified file"


def test_verified_weights_load_with_weights_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = tmp_path / "flownet.pkl"
    real.write_bytes(b"pinned")
    monkeypatch.setattr(weights, "RIFE_SIZE_BYTES", len(b"pinned"))
    monkeypatch.setattr(weights, "RIFE_SHA256", weights.sha256_of(real))
    torch = _ExplodingTorch()
    state = weights.load_verified(real, torch)
    assert torch.loaded == [real]
    assert state == {"block0.x": 1}


def test_without_torch_or_weights_slow_motion_is_unavailable_not_an_error(
    tmp_path: Path,
) -> None:
    with pytest.raises(SlowmoUnavailableError):
        open_interpolator(InterpolationSetup(weights=tmp_path / "absent.pkl"))


# --- confidence --------------------------------------------------------------------


def test_ssim_of_a_picture_with_itself_is_one() -> None:
    frame = confidence.luma(_pan(0, 1)[0])
    assert confidence.ssim_map(frame, frame).mean() == pytest.approx(1.0, abs=1e-6)


def test_a_true_midpoint_is_accepted() -> None:
    frames = _pan(6.0, 14)
    interpolator = TruthfulPan(6.0, frames)
    warp = confidence.warp_scores(frames, interpolator)
    middles = [interpolator.midpoint(frames[i], frames[i + 1]) for i in range(len(frames) - 1)]
    verdict = confidence.judge(warp, confidence.continuity_scores(frames, middles))
    assert verdict.accepted, verdict
    assert verdict.confidence >= confidence.THRESHOLD
    assert verdict.reason == confidence.ACCEPTED


def test_a_double_image_on_fast_motion_is_refused_with_its_reason() -> None:
    frames = _pan(24.0, 14)
    verdict = confidence.judge(confidence.warp_scores(frames, Blend()), [1.0])
    assert not verdict.accepted
    assert verdict.reason == confidence.WARP_FAILED


def test_a_still_picture_is_accepted_whatever_the_interpolator() -> None:
    frames = _pan(0.0, 8)
    warp = confidence.warp_scores(frames, Garbage())
    assert warp and all(score == 1.0 for score in warp)


def test_a_frame_that_jumps_against_its_neighbours_is_refused() -> None:
    frames = _pan(3.0, 6)
    middles = [Garbage().midpoint(frames[i], frames[i + 1]) for i in range(len(frames) - 1)]
    verdict = confidence.judge([1.0], confidence.continuity_scores(frames, middles))
    assert not verdict.accepted
    assert verdict.reason == confidence.POP_FAILED


def test_nothing_to_judge_is_a_refusal() -> None:
    verdict = confidence.judge([], [])
    assert not verdict.accepted and verdict.reason == confidence.TOO_SHORT


# --- one shot, end to end -----------------------------------------------------------


def _write(frames: list[np.ndarray], path: Path) -> np.ndarray:
    np.stack(frames).tofile(path)
    return read_frames(path, FrameShape(W, H))


@pytest.mark.parametrize("frames_out", [10, 11])
def test_an_accepted_shot_writes_exactly_its_frames_in_display_order(
    tmp_path: Path, frames_out: int
) -> None:
    frames = _pan(4.0, output_frames_needed(frames_out))
    captured = _write(frames, tmp_path / "captured.rgb")
    out = tmp_path / "slow.rgb"
    verdict = interpolate_shot(captured, TruthfulPan(4.0, frames), out, frames_out=frames_out)
    assert verdict.accepted
    written = read_frames(out, FrameShape(W, H))
    assert len(written) == frames_out
    assert np.array_equal(written[0], frames[0])
    assert np.array_equal(written[2], frames[1]), "captured frames sit at even positions"
    assert not (tmp_path / "slow.rgb.partial").exists()


def test_a_refused_shot_writes_nothing(tmp_path: Path) -> None:
    frames = _pan(24.0, output_frames_needed(20))
    captured = _write(frames, tmp_path / "captured.rgb")
    out = tmp_path / "slow.rgb"
    verdict = interpolate_shot(captured, Blend(), out, frames_out=20)
    assert not verdict.accepted
    assert not out.exists()
    assert not (tmp_path / "slow.rgb.partial").exists()


def test_too_few_captured_frames_is_a_refusal(tmp_path: Path) -> None:
    frames = _pan(1.0, 3)
    captured = _write(frames, tmp_path / "captured.rgb")
    verdict = interpolate_shot(captured, Blend(), tmp_path / "s.rgb", frames_out=30)
    assert not verdict.accepted and verdict.reason == confidence.TOO_SHORT


# --- the interpolator's memory steps, with torch stood in -----------------------------


class _OOM(Exception):
    pass


def _fake_torch(cuda: bool) -> SimpleNamespace:
    calls: list[str] = []
    cuda_ns = SimpleNamespace(
        is_available=lambda: cuda,
        OutOfMemoryError=_OOM,
        empty_cache=lambda: calls.append("empty_cache"),
        reset_peak_memory_stats=lambda: None,
        max_memory_allocated=lambda: 512 * 1024 * 1024,
    )
    return SimpleNamespace(cuda=cuda_ns, calls=calls)


def _interpolator(
    cuda: bool, failures: int, monkeypatch: pytest.MonkeyPatch
) -> tuple[RifeInterpolator, list[tuple[str, float]]]:
    torch = _fake_torch(cuda)
    instance = RifeInterpolator(cast("ModuleType", torch), Path("unused"))
    builds: list[tuple[str, float]] = []
    remaining = {"oom": failures}

    def build(device: str) -> None:
        instance._net = object()
        instance._device = device  # type: ignore[assignment]
        instance.stats.device = device
        builds.append((device, instance._scale or 1.0))

    def run(first: np.ndarray, second: np.ndarray) -> np.ndarray:
        if instance._device == "cuda" and remaining["oom"] > 0:
            remaining["oom"] -= 1
            raise _OOM
        return first

    monkeypatch.setattr(instance, "_build", build)
    monkeypatch.setattr(instance, "_run", run)
    instance._net = object()
    instance._state = {}
    return instance, builds


def test_one_oom_halves_the_flow_and_retries_on_the_gpu(monkeypatch: pytest.MonkeyPatch) -> None:
    instance, builds = _interpolator(cuda=True, failures=1, monkeypatch=monkeypatch)
    frame = np.zeros((H, W, 3), np.uint8)
    instance.midpoint(frame, frame)
    assert builds == [("cuda", FLOW_SCALES[1])]
    assert instance.stats.oom_retries == 1 and not instance.stats.fell_back_to_cpu


def test_two_ooms_finish_the_shot_on_the_cpu(monkeypatch: pytest.MonkeyPatch) -> None:
    instance, builds = _interpolator(cuda=True, failures=2, monkeypatch=monkeypatch)
    frame = np.zeros((H, W, 3), np.uint8)
    result = instance.midpoint(frame, frame)
    assert result.shape == frame.shape
    assert builds[-1][0] == "cpu"
    assert instance.stats.fell_back_to_cpu and instance.stats.oom_retries == 2
    instance.midpoint(frame, frame)  # the rest of the shot stays on the CPU
    assert builds[-1][0] == "cpu" and len(builds) == 2


def test_4k_starts_at_half_flow_and_1080p_at_full() -> None:
    assert initial_flow_scale(1080, 1920) == 1.0
    assert initial_flow_scale(1920, 1080) == 1.0
    assert initial_flow_scale(3840, 2160) == 0.5


def test_only_the_midpoint_exists() -> None:
    assert "timestep" not in RifeInterpolator.midpoint.__code__.co_varnames
