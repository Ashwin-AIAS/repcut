"""Analysis recipes and the version of each recipe used.

Same discipline as ``media.artifacts.PARAMS_VERSION``, applied to the two
recipes Prompt 03 introduces: scene detection (``scenes.detector_params_version``)
and frame sampling (``scenes.sampled_frame_path``, amendment 008 resolution 2).
Both are pure functions of *(source bytes, recipe)*, so a change to either
recipe gets a version bump in the same commit - resolution, threshold, filter
graph, candidate count, anything that changes what analysis produces from an
unchanged source.

A bump never mutates or deletes what the previous version produced: for
detection it means new ``Scene`` rows land under a new
``detector_params_version`` rather than overwriting the old boundaries; for
sampling it means the next frame is written under a new
``sampled_frame/<params_version>/`` directory (``media/store.py``'s existing
content-addressed layout - no change needed there, since ``artifact_kind`` is
already an unvalidated-by-CHECK slug and ``sampled_frame`` is just another
value for it, even though no ``derived_artifacts`` row is ever written for one;
see amendment 008 resolution 2 for why the frame path lives on ``Scene``
instead).

Two separate version constants, not one shared table like artifacts.py's
``PARAMS_VERSION`` dict: detection and sampling are independent recipes that
version independently of each other, and there are only two of them, not an
open set keyed by kind.
"""

from dataclasses import dataclass
from datetime import timedelta


@dataclass(frozen=True, slots=True)
class SceneDetectorRecipe:
    """PySceneDetect ``ContentDetector``: content-aware cut detection.

    ``threshold`` and ``minimum_scene_length`` are ``ContentDetector``'s own
    defaults, carried over as a starting point rather than re-derived - see
    ``docs/reports/`` for the session that tunes them against real footage.
    ``minimum_scene_length`` is a duration, not a frame count: the detector may
    run against the CFR proxy (amendment 008 resolution 6), and a frame count
    is silently wrong the moment the input file changes while the duration is
    not.
    """

    threshold: float
    minimum_scene_length: timedelta


@dataclass(frozen=True, slots=True)
class FrameRecipe:
    """One representative frame per scene: sharpest of N candidates.

    ``tone_map_target`` names the colour space the extracted frame is encoded
    in, regardless of the source's own (amendment 008 resolution 3: the source
    may be HDR - HEVC Main 10, BT.2020, HLG - and extraction owns tone-mapping
    down to something Gemini and a browser both render correctly).
    ``candidate_count`` is how many frames are sampled evenly across the
    scene's span, avoiding the exact boundaries, before picking the sharpest by
    Laplacian variance - the guide's own number, three. ``quality`` is
    `mjpeg`'s own ``-q:v`` scale (2-31, lower is higher quality), set well
    above ``media/artifacts.py``'s thumbnail strip because this frame is what
    Gemini's vision model actually sees, not a scrubber preview.
    """

    tone_map_target: str
    candidate_count: int
    quality: int


SCENE_DETECTOR_RECIPE = SceneDetectorRecipe(
    threshold=27.0,
    minimum_scene_length=timedelta(seconds=0.5),
)

FRAME_RECIPE = FrameRecipe(
    tone_map_target="bt709",
    candidate_count=3,
    quality=2,
)


SCENE_PARAMS_VERSION = 1
FRAME_PARAMS_VERSION = 1


__all__ = [
    "FRAME_PARAMS_VERSION",
    "FRAME_RECIPE",
    "SCENE_DETECTOR_RECIPE",
    "SCENE_PARAMS_VERSION",
    "FrameRecipe",
    "SceneDetectorRecipe",
]
