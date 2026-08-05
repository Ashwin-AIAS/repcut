"""Derived-artifact kinds and the version of the recipe that produced them.

Amendment 004: derived artifacts are content-addressed like their source, keyed
``(sha256, artifact_kind, params_version)``. ``PARAMS_VERSION`` is the single
place those versions are declared.

**Bump the version in the same commit that changes an artifact's recipe** -
resolution, CRF, filter graph, frame cadence, anything that changes the bytes
produced from an unchanged source. A bump never mutates or deletes an existing
file: it changes the key, so the superseded artifact becomes unreferenced rather
than silently wrong, and the next request regenerates under the new key.
"""

from enum import StrEnum


class ArtifactKind(StrEnum):
    """A derived render that is a pure function of (source bytes, recipe)."""

    PROXY = "proxy"
    THUMBNAIL_STRIP = "thumbnail_strip"


PARAMS_VERSION: dict[ArtifactKind, int] = {
    ArtifactKind.PROXY: 1,
    ArtifactKind.THUMBNAIL_STRIP: 1,
}
