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
class NormalisationRecipe:
    """Source to the bt709 SDR working space every preview, frame and grade starts from.

    One object, held by both ``ProxyRecipe`` and ``analysis.params.FrameRecipe``
    (amendment 012 row 4): the proxy a person judges and the frame Gemini reads
    must come from the same conversion, or a grade tuned on one is wrong on the
    other. Export (Prompt 06) inherits the same contract.

    ``operator`` is FFmpeg's CPU ``tonemap`` curve and is a *look* decision, made
    once by a person against an HDR-off twin (STOP A). ``nominal_peak`` is the
    ``npl`` zscale linearises against. SDR sources never reach either field:
    ``ffmpeg_builder.normalise_to_sdr`` returns no filter for them.
    """

    target: str
    operator: str
    nominal_peak: int
    desaturation: int


@dataclass(frozen=True, slots=True)
class ProxyRecipe:
    """The preview proxy: short side capped, CFR, one audio rate, bt709 SDR.

    ``short_side`` is a ceiling on the *shorter* display dimension, not a
    height: a height cap made portrait phone video 406x720, spending the budget
    on the axis a portrait frame has to spare (amendment 012 row 2). A source
    whose short side is already under the cap is left alone rather than
    upscaled, since upscaling spends bytes inventing detail the camera never
    captured.
    """

    short_side: int
    fps: int
    crf: int
    preset: str
    audio_bitrate: str
    audio_sample_rate: int
    audio_channels: int
    normalisation: NormalisationRecipe


@dataclass(frozen=True, slots=True)
class ThumbnailStripRecipe:
    """One tiled JPEG, one frame every ``seconds_per_frame``."""

    seconds_per_frame: int
    height: int
    quality: int


NORMALISATION = NormalisationRecipe(
    target="bt709",
    # Prompt 03's operator, carried over until a person picks one at STOP A.
    operator="hable",
    nominal_peak=100,
    # desat trades saturated highlights for perceived brightness - the wrong
    # trade for a frame colour and lighting are about to be judged from.
    desaturation=0,
)

PROXY_RECIPE = ProxyRecipe(
    short_side=720,
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
    normalisation=NORMALISATION,
)

THUMBNAIL_STRIP_RECIPE = ThumbnailStripRecipe(
    seconds_per_frame=2,
    height=180,
    quality=4,
)


PARAMS_VERSION: dict[ArtifactKind, int] = {
    # 2: HDR normalised to bt709 SDR, short side capped (amendment 012).
    ArtifactKind.PROXY: 2,
    ArtifactKind.THUMBNAIL_STRIP: 1,
}


__all__ = [
    "NORMALISATION",
    "PARAMS_VERSION",
    "PROXY_RECIPE",
    "THUMBNAIL_STRIP_RECIPE",
    "ArtifactKind",
    "NormalisationRecipe",
    "ProxyRecipe",
    "ThumbnailStripRecipe",
]
