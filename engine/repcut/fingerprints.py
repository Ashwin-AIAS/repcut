"""Recipe fingerprints, pinned to the versions that key what each recipe produces.

``test_ffmpeg_builder.py`` freezes each recipe's argv against its own version.
That catches "recipe changed, version did not". It cannot catch the coupling
between two versions, which is where amendment 012 found the next bug:

- **Scenes read the proxy** (amendment 008 #6), but a ``Scene`` row is keyed by
  ``SCENE_PARAMS_VERSION`` alone. A proxy change moves the detector's input; if
  the scene version stays put, boundaries detected on the old proxy read back
  as current. So the scene fingerprint *contains* the proxy fingerprint.
- **Gemini reads the sampled frame**, but its cache is keyed by
  ``GEMINI_PROMPT_VERSION`` and carries no frame version (open issue 12). A
  frame change without a prompt bump reads back tags describing the old frame.
  So each prompt version is pinned to the frame fingerprint it was asked about.

A fingerprint is a short digest of the argv a recipe builds from fixed inputs:
every colour branch (untagged SDR, an SDR source whose matrix and range need
converting, HDR with and without a matrix tag) and landscape and portrait, so
every branch of a builder is covered.
Changing a recipe changes its digest; the pins below then name every version
that has to move with it. ``check_pins`` returns what is wrong rather than
raising, so the gate can print a negative control beside the real result.

Changing the tone-map operator, for example, moves all four: the proxy and
frame argvs both contain it, the scene fingerprint contains the proxy's, and
the Gemini pin contains the frame's.
"""

import hashlib
from collections.abc import Iterable, Mapping
from pathlib import Path

from repcut.analysis.params import (
    FRAME_PARAMS_VERSION,
    FRAME_RECIPE,
    SCENE_DETECTOR_RECIPE,
    SCENE_PARAMS_VERSION,
    FrameRecipe,
    SceneDetectorRecipe,
)
from repcut.analysis.pipeline import GEMINI_PROMPT_VERSION
from repcut.media.artifacts import PARAMS_VERSION, PROXY_RECIPE, ArtifactKind, ProxyRecipe
from repcut.media.ffmpeg_builder import build_frame_extraction, build_proxy

# Fixed, content-addressed-looking inputs, so a digest describes the recipe and
# nothing else. Never a real path.
_SOURCE = Path("fingerprint/source.mp4")
_OUTPUT = Path("fingerprint/output")
# (primaries, transfer, matrix, range) per branch, as ffprobe reports them. The
# SDR BT.601 full-range case is the one proxy v2 left to FFmpeg's CLI
# (amendment 013).
_Tags = tuple[str | None, str | None, str | None, str | None]
_COLOURS: tuple[_Tags, ...] = (
    (None, None, None, None),
    (None, None, "smpte170m", "pc"),
    ("bt2020", "arib-std-b67", None, None),
    ("bt2020", "arib-std-b67", "bt2020nc", "tv"),
)
# Landscape and portrait display sizes, both above the short-side cap.
_GEOMETRIES = ((1920, 1080), (2160, 3840))


def _digest(parts: Iterable[str]) -> str:
    joined = "\x00".join(parts).encode("utf-8")
    return hashlib.sha256(joined).hexdigest()[:16]


def proxy_fingerprint(recipe: ProxyRecipe = PROXY_RECIPE) -> str:
    """The proxy recipe's argv across every colour branch and landscape/portrait."""
    parts: list[str] = []
    for width, height in _GEOMETRIES:
        for primaries, transfer, matrix, colour_range in _COLOURS:
            command = build_proxy(
                _SOURCE,
                _OUTPUT,
                display_width=width,
                display_height=height,
                color_primaries=primaries,
                color_transfer=transfer,
                color_space=matrix,
                color_range=colour_range,
                recipe=recipe,
            )
            parts.extend(command.argv)
    return _digest(parts)


def frame_fingerprint(recipe: FrameRecipe = FRAME_RECIPE) -> str:
    """The frame-extraction recipe's argv across every colour branch."""
    parts: list[str] = []
    for primaries, transfer, matrix, colour_range in _COLOURS:
        command = build_frame_extraction(
            _SOURCE,
            _OUTPUT,
            timestamp_seconds=1.5,
            color_primaries=primaries,
            color_transfer=transfer,
            color_space=matrix,
            color_range=colour_range,
            recipe=recipe,
        )
        parts.extend(command.argv)
    parts.extend((str(recipe.candidate_count), str(recipe.quality)))
    return _digest(parts)


def scene_fingerprint(
    detector: SceneDetectorRecipe = SCENE_DETECTOR_RECIPE,
    proxy: ProxyRecipe = PROXY_RECIPE,
) -> str:
    """The detector's own settings plus the proxy it reads."""
    return _digest(
        (
            repr(detector.threshold),
            repr(detector.minimum_scene_length.total_seconds()),
            proxy_fingerprint(proxy),
        )
    )


# Pins: version -> the fingerprint of the recipe that version was produced by.
# Add an entry when you bump a version; keep superseded ones, since what they
# produced is still on disk. Versions that predate this module (proxy 1, scene
# 1) were built by code that no longer exists and cannot be recomputed, so they
# have no pin - their argv is frozen in test_ffmpeg_builder.py instead. Pins
# from proxy 3 / scene 3 / frame 3 on digest four colour branches (_COLOURS);
# earlier ones digested two, so they are history, not recomputable.
PROXY_PINS: Mapping[int, str] = {2: "5a922dbd74f35730", 3: "b19c8b76c8b8cd20"}
SCENE_PINS: Mapping[int, str] = {2: "5a119a68d72d0f7c", 3: "8c40afa71d65399b"}
FRAME_PINS: Mapping[int, str] = {
    1: "f5eabde0c69ba634",
    2: "709513bc4a7d5d20",
    3: "a43f8ef7ecc6e440",
}
# Which frame each Gemini prompt version was asked about.
GEMINI_FRAME_PINS: Mapping[int, str] = {
    2: "f5eabde0c69ba634",
    3: "709513bc4a7d5d20",
    4: "a43f8ef7ecc6e440",
}


def _check(name: str, version: int, pins: Mapping[int, str], actual: str, bump: str) -> list[str]:
    pinned = pins.get(version)
    if pinned is None:
        return [f"{name} {version} has no pinned fingerprint - add {actual!r} for it"]
    if pinned != actual:
        return [f"{name} is still {version} but its input changed ({pinned} -> {actual}); {bump}"]
    return []


def check_pins(
    *,
    proxy: ProxyRecipe = PROXY_RECIPE,
    frame: FrameRecipe = FRAME_RECIPE,
    detector: SceneDetectorRecipe = SCENE_DETECTOR_RECIPE,
    proxy_version: int = PARAMS_VERSION[ArtifactKind.PROXY],
    scene_version: int = SCENE_PARAMS_VERSION,
    frame_version: int = FRAME_PARAMS_VERSION,
    gemini_version: int = GEMINI_PROMPT_VERSION,
    proxy_pins: Mapping[int, str] = PROXY_PINS,
    scene_pins: Mapping[int, str] = SCENE_PINS,
    frame_pins: Mapping[int, str] = FRAME_PINS,
    gemini_pins: Mapping[int, str] = GEMINI_FRAME_PINS,
) -> list[str]:
    """Every version whose input moved without it. Empty means consistent.

    Every argument defaults to what is in force; a negative control passes a
    changed recipe with the versions left alone and expects a non-empty answer.
    """
    frame_now = frame_fingerprint(frame)
    return [
        *_check(
            "PARAMS_VERSION[proxy]",
            proxy_version,
            proxy_pins,
            proxy_fingerprint(proxy),
            "bump it in media/artifacts.py",
        ),
        *_check(
            "SCENE_PARAMS_VERSION",
            scene_version,
            scene_pins,
            scene_fingerprint(detector, proxy),
            "bump it in analysis/params.py - detection reads the proxy",
        ),
        *_check(
            "FRAME_PARAMS_VERSION",
            frame_version,
            frame_pins,
            frame_now,
            "bump it in analysis/params.py",
        ),
        *_check(
            "GEMINI_PROMPT_VERSION",
            gemini_version,
            gemini_pins,
            frame_now,
            "bump it in analysis/pipeline.py - cached tags describe the old frame",
        ),
    ]


__all__ = [
    "FRAME_PINS",
    "GEMINI_FRAME_PINS",
    "PROXY_PINS",
    "SCENE_PINS",
    "check_pins",
    "frame_fingerprint",
    "proxy_fingerprint",
    "scene_fingerprint",
]
