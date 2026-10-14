"""The interpolator: one midpoint frame between two captured frames, on a 4 GB card.

The only thing it can make is the frame halfway between two real ones
(``timestep`` 0.5). There is no parameter for any other instant, so it cannot
be asked for more than 2x (``limits``).

Memory (``.claude/rules/gpu-vram.md``):

- One model resident: ``RifeInterpolator`` is a context manager; leaving it
  deletes the network and empties the CUDA cache. Nothing holds it at module
  level, so captions' or reframing's models are never loaded beside it.
- fp16 on CUDA; batch size 1, always (one pair at a time, whatever the shot's
  length).
- The resolution lever is RIFE's own ``scale``: the flow network runs at a
  fraction of the frame (the "tile" the guide asks to shrink) while the final
  warp still samples the full frames. 1080p starts at 1.0, anything larger at
  0.5.
- ``torch.cuda.OutOfMemoryError`` is caught by name: free, halve the flow
  scale, retry once; a second OOM moves the rest of the shot to the CPU with a
  logged warning. An OOM never reaches the person as a failed export.
- Peak allocated VRAM is measured and reported for every shot.

torch is imported by the caller and passed in (amendment 003: the engine boots
without it), which is also what lets the tests drive the memory steps with a
stand-in.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType, TracebackType
from typing import Any, Literal, Protocol

import numpy as np
import structlog

from repcut.slowmo.weights import load_verified

logger = structlog.get_logger(__name__)

Device = Literal["cuda", "cpu"]

# Short side above which the flow starts at half resolution (4K and up).
FULL_SCALE_MAX_SHORT_SIDE = 1080
FLOW_SCALES = (1.0, 0.5, 0.25)
# The network downsamples by 32 at full scale; padding is to this times 1/scale.
_MODULO = 32
_MIDPOINT = 0.5


class Interpolator(Protocol):
    """Anything that can make the frame halfway between two frames (RGB24, HxWx3)."""

    def midpoint(self, first: np.ndarray, second: np.ndarray) -> np.ndarray: ...


@dataclass(slots=True)
class RunStats:
    """What a shot's interpolation cost and where it ran. Logged and reported."""

    device: str
    flow_scale: float
    pairs: int = 0
    seconds: float = 0.0
    peak_vram_mb: float | None = None
    oom_retries: int = 0
    fell_back_to_cpu: bool = False
    notes: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, object]:
        return {
            "device": self.device,
            "flow_scale": self.flow_scale,
            "pairs": self.pairs,
            "seconds": round(self.seconds, 3),
            "peak_vram_mb": None if self.peak_vram_mb is None else round(self.peak_vram_mb, 1),
            "oom_retries": self.oom_retries,
            "fell_back_to_cpu": self.fell_back_to_cpu,
            "notes": list(self.notes),
        }


def initial_flow_scale(width: int, height: int) -> float:
    return 1.0 if min(width, height) <= FULL_SCALE_MAX_SHORT_SIDE else 0.5


def _padded(size: int, scale: float) -> int:
    step = max(_MODULO, int(_MODULO / scale))
    return math.ceil(size / step) * step


# Any throughout this class: torch is an optional dependency handed in at
# runtime (amendment 003); its types do not exist where it is not installed.
class RifeInterpolator:
    """RIFE 4.6, loaded from the verified weights for the length of a ``with`` block."""

    def __init__(self, torch: ModuleType, weights: Path, *, preference: str = "auto") -> None:
        self._torch = torch
        self._weights = weights
        cuda = preference != "cpu" and bool(torch.cuda.is_available())
        self._device: Device = "cuda" if cuda else "cpu"
        self._net: Any = None
        self._state: dict[str, Any] | None = None
        self._grids: dict[tuple[int, int, float], tuple[Any, Any]] = {}
        self.stats = RunStats(device=self._device, flow_scale=1.0)
        self._scale: float | None = None

    # --- lifecycle -------------------------------------------------------------

    def __enter__(self) -> RifeInterpolator:
        self._state = load_verified(self._weights, self._torch)
        self._build(self._device)
        if self._device == "cuda":
            self._torch.cuda.reset_peak_memory_stats()
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        trace: TracebackType | None,
    ) -> None:
        self.unload()

    def unload(self) -> None:
        if self._device == "cuda" and self._net is not None:
            peak = self._torch.cuda.max_memory_allocated() / (1024 * 1024)
            self.stats.peak_vram_mb = max(self.stats.peak_vram_mb or 0.0, peak)
        self._net = None
        self._state = None
        self._grids.clear()
        if self._torch.cuda.is_available():
            self._torch.cuda.empty_cache()
        logger.info("slowmo_interpolator_unloaded", **self.stats.to_json())

    def _build(self, device: Device) -> None:
        from repcut.slowmo.ifnet import IFNet  # torch is known to be importable here

        if self._net is not None and self._device == "cuda":
            peak = self._torch.cuda.max_memory_allocated() / (1024 * 1024)
            self.stats.peak_vram_mb = max(self.stats.peak_vram_mb or 0.0, peak)
        # The old network goes before the new one is built: two resident copies
        # is the allocation that OOMs on a 4 GB card.
        self._net = None
        self._grids.clear()
        if self._device == "cuda":
            self._torch.cuda.empty_cache()
        scale = self._scale or 1.0
        net = IFNet(scale)
        if self._state is None:
            raise RuntimeError("the interpolator is used outside its with-block")
        net.load_state_dict(self._state, strict=True)
        dtype = self._torch.float16 if device == "cuda" else self._torch.float32
        self._net = net.eval().to(device, dtype)
        self._device = device
        self._dtype = dtype
        self.stats.device = device
        self._grids.clear()

    # --- one pair --------------------------------------------------------------

    def _grid(self, height: int, width: int) -> tuple[Any, Any]:
        key = (height, width, self._scale or 1.0)
        if key not in self._grids:
            torch = self._torch
            flow_div = torch.tensor(
                [(width - 1.0) / 2.0, (height - 1.0) / 2.0], dtype=torch.float, device=self._device
            )
            horizontal = torch.linspace(-1.0, 1.0, width, dtype=torch.float, device=self._device)
            vertical = torch.linspace(-1.0, 1.0, height, dtype=torch.float, device=self._device)
            grid = torch.cat(
                [
                    horizontal.view(1, 1, 1, width).expand(-1, -1, height, -1),
                    vertical.view(1, 1, height, 1).expand(-1, -1, -1, width),
                ],
                1,
            )
            self._grids[key] = (flow_div, grid)
        return self._grids[key]

    # Any: a torch tensor, whose type does not exist where torch is not installed.
    def _tensor(self, frame: np.ndarray, height: int, width: int) -> Any:  # noqa: ANN401
        torch = self._torch
        # A copy: frames arrive as read-only memory maps, which torch will not wrap.
        image = torch.from_numpy(np.array(frame, copy=True)).to(self._device)
        image = image.permute(2, 0, 1).unsqueeze(0).to(self._dtype) / 255.0
        pad = (0, width - frame.shape[1], 0, height - frame.shape[0])
        return torch.nn.functional.pad(image, pad) if any(pad) else image

    def _run(self, first: np.ndarray, second: np.ndarray) -> np.ndarray:
        torch = self._torch
        scale = self._scale or 1.0
        height = _padded(first.shape[0], scale)
        width = _padded(first.shape[1], scale)
        flow_div, grid = self._grid(height, width)
        timestep = torch.full(
            [1, 1, height, width], _MIDPOINT, dtype=self._dtype, device=self._device
        )
        with torch.inference_mode():
            out = self._net(
                self._tensor(first, height, width),
                self._tensor(second, height, width),
                timestep,
                flow_div,
                grid,
            )
        out = out[0, :, : first.shape[0], : first.shape[1]].float().clamp(0.0, 1.0)
        result: np.ndarray = (out * 255.0).round().byte().permute(1, 2, 0).cpu().numpy()
        return result

    def midpoint(self, first: np.ndarray, second: np.ndarray) -> np.ndarray:
        """The frame halfway between ``first`` and ``second``. Same shape, uint8 RGB."""
        if first.shape != second.shape or first.ndim != 3 or first.shape[2] != 3:
            raise ValueError("two RGB frames of the same size are needed")
        if self._net is None:
            raise RuntimeError("the interpolator is used outside its with-block")
        if self._scale is None:
            self._scale = initial_flow_scale(first.shape[1], first.shape[0])
            self.stats.flow_scale = self._scale
            if self._scale != 1.0:
                self._build(self._device)
        started = time.perf_counter()
        try:
            result = self._run(first, second)
        except self._torch.cuda.OutOfMemoryError:
            # Named: the 4 GB card ran out at this resolution. Smaller flow,
            # one retry, then the CPU - never a crashed export.
            result = self._after_oom(first, second)
        self.stats.pairs += 1
        self.stats.seconds += time.perf_counter() - started
        return result

    def _after_oom(self, first: np.ndarray, second: np.ndarray) -> np.ndarray:
        torch = self._torch
        self.stats.oom_retries += 1
        torch.cuda.empty_cache()
        current = self._scale or 1.0
        smaller = [s for s in FLOW_SCALES if s < current]
        if smaller:
            self._scale = smaller[0]
            self.stats.flow_scale = self._scale
            self.stats.notes.append(f"out of GPU memory; flow scale lowered to {self._scale}")
            logger.warning("slowmo_oom_retry", flow_scale=self._scale)
            self._build("cuda")
            try:
                return self._run(first, second)
            except torch.cuda.OutOfMemoryError:
                # Named: still out of memory at the smaller flow. The CPU is
                # slow but has the memory; the shot finishes there.
                self.stats.oom_retries += 1
        torch.cuda.empty_cache()
        self.stats.fell_back_to_cpu = True
        self.stats.notes.append("out of GPU memory twice; this shot finished on the CPU")
        logger.warning("slowmo_oom_cpu_fallback", flow_scale=self._scale)
        self._build("cpu")
        return self._run(first, second)


__all__ = [
    "FLOW_SCALES",
    "FULL_SCALE_MAX_SHORT_SIDE",
    "Interpolator",
    "RifeInterpolator",
    "RunStats",
    "initial_flow_scale",
]
