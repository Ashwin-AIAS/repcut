"""Derived-artifact kinds, their recipes, and the version of the recipe used.

Amendment 004: derived artifacts are content-addressed like their source, keyed
``(sha256, artifact_kind, params_version)``. ``PARAMS_VERSION`` is the single
place those versions are declared.

**Bump the version in the same commit that changes an artifact's recipe** -
resolution, CRF, filter graph, frame cadence, anything that changes the bytes
produced from an unchanged source. A bump never mutates or deletes an existing
file: it changes the key, so the superseded artifact becomes unreferenced rather
than silently wrong, and the next request regenerates under the new key.

The recipes live here, beside the versions they are versioned by, so that a
change and its bump are one edit in one file rather than two edits in two.
``test_ffmpeg_builder.py`` freezes each recipe's argv against its version and
fails if one moves without the other.

Deliberately **not** in `Settings`: an environment variable that changes the
bytes an artifact is made of, without changing the key those bytes are stored
under, is exactly the staleness the version exists to prevent. A preset the user
can edit is a preset that can desynchronise from `params_version` on a machine
nobody is looking at.
"""

from dataclasses import dataclass
from enum import StrEnum


class ArtifactKind(StrEnum):
    """A derived render that is a pure function of (source bytes, recipe)."""

    PROXY = "proxy"
    THUMBNAIL_STRIP = "thumbnail_strip"


@dataclass(frozen=True, slots=True)
class ProxyRecipe:
    """The preview proxy: 720p, CFR, one audio rate, explicit colour.

    ``height`` is a ceiling, not a target - a source shorter than this is left
    alone rather than upscaled, since upscaling spends bytes inventing detail
    the camera never captured.
    """

    height: int
    fps: int
    crf: int
    preset: str
    audio_bitrate: str
    audio_sample_rate: int
    audio_channels: int


@dataclass(frozen=True, slots=True)
class ThumbnailStripRecipe:
    """One tiled JPEG, one frame every ``seconds_per_frame``."""

    seconds_per_frame: int
    height: int
    quality: int


PROXY_RECIPE = ProxyRecipe(
    height=720,
    fps=30,
    crf=23,
    # veryfast, not slow: this is a scrubbing preview, and it is on the critical
    # path of "upload finished" to "the user can see something". Exports get the
    # slow preset (.claude/rules/ffmpeg.md).
    preset="veryfast",
    audio_bitrate="128k",
    # One project sample rate. Segments concatenated at mixed rates desync.
    audio_sample_rate=48000,
    audio_channels=2,
)

THUMBNAIL_STRIP_RECIPE = ThumbnailStripRecipe(
    seconds_per_frame=2,
    height=180,
    quality=4,
)


PARAMS_VERSION: dict[ArtifactKind, int] = {
    ArtifactKind.PROXY: 1,
    ArtifactKind.THUMBNAIL_STRIP: 1,
}


__all__ = [
    "PARAMS_VERSION",
    "PROXY_RECIPE",
    "THUMBNAIL_STRIP_RECIPE",
    "ArtifactKind",
    "ProxyRecipe",
    "ThumbnailStripRecipe",
]
