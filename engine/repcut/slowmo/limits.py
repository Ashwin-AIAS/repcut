"""The slow-motion ceiling. P1 in code: never more than 2x, from any input.

A speed here is playback speed: 1.0 is real time, 0.5 is half speed, which
needs one invented frame between every two captured ones. Anything slower would
need more invented frames than real ones - the picture would be mostly the
network's guess, not the camera's record - so it is refused, not clamped:
there is no setting, request field or stored plan that can ask for it and get
a quieter version instead.

The ceiling is a module constant on purpose. Not ``Settings``, not a TOML
value, not a request parameter: configuration is exactly the thing the guide
says must not be able to exceed it.
"""

from typing import Final

# Interpolated frames per captured frame, at most one: 2x.
MAX_SLOWDOWN: Final = 2
SLOWEST_SPEED: Final = 1.0 / MAX_SLOWDOWN
SPEEDS: Final = frozenset({1.0, SLOWEST_SPEED})


class SlowdownLimitError(ValueError):
    """A speed outside what P1 allows. A fixed sentence."""


def checked_speed(speed: float) -> float:
    """``speed`` if it is real time or half speed; anything else is refused.

    Exactly two values, not a range: the interpolator only ever makes the
    midpoint between two captured frames, so 0.5 is the only slow speed it can
    produce without inventing more than it captured.
    """
    if speed not in SPEEDS:
        raise SlowdownLimitError("slow motion is half speed at most (2x); no other speed exists")
    return speed


__all__ = ["MAX_SLOWDOWN", "SLOWEST_SPEED", "SPEEDS", "SlowdownLimitError", "checked_speed"]
