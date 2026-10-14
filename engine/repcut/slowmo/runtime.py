"""Opening the interpolator where it may not exist: no torch, no weights, no GPU.

Every missing piece is a reason a shot plays at normal speed, never a failed
export (P1's fallback covers "could not interpolate at all" as much as
"interpolated badly"). The reason is a fixed sentence the person reads.
"""

import importlib
from dataclasses import dataclass
from pathlib import Path

from repcut.slowmo.rife import RifeInterpolator
from repcut.slowmo.weights import WeightsUnavailableError, verify

NO_TORCH = (
    "slow motion needs PyTorch, which is not installed (make setup-gpu); "
    "slow-motion shots play at normal speed"
)


class SlowmoUnavailableError(RuntimeError):
    """Interpolation cannot run on this machine right now. A fixed sentence."""


@dataclass(frozen=True, slots=True)
class InterpolationSetup:
    """Where the weights are and which device the person prefers (``Settings.torch_device``)."""

    weights: Path
    device_preference: str = "auto"


def open_interpolator(setup: InterpolationSetup) -> RifeInterpolator:
    """An interpolator ready for a ``with`` block. Raises ``SlowmoUnavailableError``.

    The weights are checked before torch is imported: importing torch costs
    about a gigabyte of memory for the life of the process, which is not worth
    paying to learn that the weights are missing.
    """
    try:
        verify(setup.weights)
    except WeightsUnavailableError as error:
        raise SlowmoUnavailableError(str(error)) from error
    try:
        torch = importlib.import_module("torch")
    except ImportError as error:
        # Named: torch is optional (amendment 003); without it there is no
        # interpolation, and the shot falls back to normal speed.
        raise SlowmoUnavailableError(NO_TORCH) from error
    return RifeInterpolator(torch, setup.weights, preference=setup.device_preference)


__all__ = ["NO_TORCH", "InterpolationSetup", "SlowmoUnavailableError", "open_interpolator"]
