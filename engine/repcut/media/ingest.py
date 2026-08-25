"""The ingest job: probe a blob, then derive its thumbnail strip and proxy.

Runs once per *blob*, not once per upload. Everything it produces is a pure
function of (source bytes, recipe), so a second project adding the same clip
finds the work already done and re-encodes nothing - which is what makes
amendment 004's content-addressed store worth its cost.

Idempotent in both directions. Re-running after a crash overwrites the probe
columns with the same values and skips any artifact whose row and file both
exist; a recipe change bumps ``params_version``, which changes the key, so the
new render lands beside the old one rather than on top of it.
"""

import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repcut.db.models import DerivedArtifact, MediaBlob
from repcut.jobs import JobContext, JobFailedError
from repcut.logging import get_logger
from repcut.media.artifacts import PARAMS_VERSION, PROXY_RECIPE, ArtifactKind
from repcut.media.ffmpeg_builder import (
    build_probe,
    build_proxy,
    build_thumbnail_strip,
    render,
    run,
)
from repcut.media.metadata import MediaProperties, ProbeParseError, parse_probe
from repcut.media.store import absolute, derived_path

logger = get_logger(__name__)

INGEST_JOB_TYPE = "ingest"

PROXY_FILENAME = "proxy.mp4"
THUMBNAIL_STRIP_FILENAME = "strip.jpg"

# Where each step sits on the overall bar. The proxy owns the widest span
# because it is the only step whose duration scales with the clip, and the only
# one FFmpeg reports sub-progress for.
_PROBE_AT = 0.05
_STRIP_AT = 0.20
_PROXY_AT = 0.40
_PROXY_UNTIL = 0.98


class BlobMissingError(JobFailedError, FileNotFoundError):
    """The row exists but its bytes do not. A store inconsistency, not a bad clip.

    Both bases on purpose. ``FileNotFoundError`` is what it *is*, and callers
    outside the job worker should be able to catch it as one; ``JobFailedError``
    is what makes the worker keep this message instead of flattening it into the
    generic "the media store could not be read or written".
    """


async def _load_blob(session: AsyncSession, sha256: str) -> MediaBlob:
    """Fetch the blob row, or say which invariant broke."""
    blob = await session.get(MediaBlob, sha256)
    if blob is None:
        raise BlobMissingError("this clip is no longer in the media library")
    return blob


async def probe_media(source: Path) -> MediaProperties:
    """Run the one probe and parse it, or raise ``ProbeParseError``.

    The only place a file is decided to be video at all: a `.txt` renamed to
    `.mp4` reaches here and leaves as a named error, never a 500.
    """
    stdout = await run(build_probe(source))
    try:
        document = json.loads(stdout)
    except json.JSONDecodeError as error:
        # Named: ffprobe exited 0 and printed something that is not JSON. Seen
        # when a build writes a warning to stdout; treated as unreadable media
        # rather than crashing the job.
        raise ProbeParseError("this file could not be read as media") from error
    if not isinstance(document, dict):
        raise ProbeParseError("this file could not be read as media")
    return parse_probe(document)


def _apply_properties(blob: MediaBlob, properties: MediaProperties) -> None:
    """Write the probed properties onto the blob row.

    ``fps_normalized`` is the proxy recipe's rate, because the proxy is the
    normalized rendition and the two must not be able to disagree.

    ``is_variable_frame_rate`` is copied through **including its None**. Writing
    False for an unanswerable container would record "measured CFR" about a clip
    nobody measured - see ``metadata.detect_variable_frame_rate``.
    """
    blob.container_format = properties.container_format
    blob.duration_seconds = properties.duration_seconds
    blob.display_width = properties.display_width
    blob.display_height = properties.display_height
    blob.rotation_degrees = properties.rotation_degrees
    blob.fps_source = properties.fps_source
    blob.fps_normalized = float(PROXY_RECIPE.fps)
    blob.is_variable_frame_rate = properties.is_variable_frame_rate
    blob.video_codec = properties.video_codec
    blob.audio_codec = properties.audio_codec
    blob.audio_sample_rate = properties.audio_sample_rate


async def _existing_artifact(
    session: AsyncSession, sha256: str, kind: ArtifactKind, data_dir: Path
) -> DerivedArtifact | None:
    """The artifact row for this key, only if its file is still on disk.

    Both halves are checked. A row whose file was deleted would otherwise make
    the ingest skip a render and hand the UI a path to nothing.
    """
    statement = select(DerivedArtifact).where(
        DerivedArtifact.sha256 == sha256,
        DerivedArtifact.artifact_kind == kind.value,
        DerivedArtifact.params_version == PARAMS_VERSION[kind],
    )
    artifact = (await session.execute(statement)).scalars().first()
    if artifact is None:
        return None
    if not absolute(data_dir, artifact.stored_path).is_file():
        logger.warning("derived_artifact_file_missing", artifact_kind=kind.value)
        await session.delete(artifact)
        await session.commit()
        return None
    return artifact


async def _record_artifact(
    session: AsyncSession, sha256: str, kind: ArtifactKind, stored: str, size_bytes: int
) -> None:
    """Insert or refresh the row for one derived artifact key."""
    existing = (
        (
            await session.execute(
                select(DerivedArtifact).where(
                    DerivedArtifact.sha256 == sha256,
                    DerivedArtifact.artifact_kind == kind.value,
                    DerivedArtifact.params_version == PARAMS_VERSION[kind],
                )
            )
        )
        .scalars()
        .first()
    )
    if existing is None:
        session.add(
            DerivedArtifact(
                sha256=sha256,
                artifact_kind=kind.value,
                params_version=PARAMS_VERSION[kind],
                stored_path=stored,
                size_bytes=size_bytes,
            )
        )
    else:
        existing.stored_path = stored
        existing.size_bytes = size_bytes
    await session.commit()


async def run_ingest(context: JobContext) -> None:
    """Probe the blob, then derive its strip and proxy. The ``ingest`` handler."""
    sha256 = context.record.sha256
    if sha256 is None:
        raise BlobMissingError("this ingest job is not attached to a clip")

    data_dir = context.settings.data_dir

    await context.report.step("reading clip metadata", _PROBE_AT)
    async with context.session_factory() as session:
        blob = await _load_blob(session, sha256)
        source = absolute(data_dir, blob.stored_path)
        if not source.is_file():
            raise BlobMissingError("this clip's file is missing from the media library")

        properties = await probe_media(source)
        _apply_properties(blob, properties)
        await session.commit()

    logger.info(
        "clip_probed",
        # No path, no filename: this line goes to a log the user may share.
        video_codec=properties.video_codec,
        display_height=properties.display_height,
        rotation_degrees=properties.rotation_degrees,
        is_variable_frame_rate=properties.is_variable_frame_rate,
    )

    await context.report.step("building the timeline thumbnails", _STRIP_AT)
    await _derive_thumbnail_strip(context, sha256, source, properties, data_dir)

    await context.report.step("encoding the preview proxy", _PROXY_AT, until=_PROXY_UNTIL)
    await _derive_proxy(context, sha256, source, properties, data_dir)

    await context.report.step("finished", 1.0)


async def _derive_thumbnail_strip(
    context: JobContext,
    sha256: str,
    source: Path,
    properties: MediaProperties,
    data_dir: Path,
) -> None:
    """Render the tiled strip, unless this exact key is already on disk."""
    kind = ArtifactKind.THUMBNAIL_STRIP
    async with context.session_factory() as session:
        if await _existing_artifact(session, sha256, kind, data_dir) is not None:
            logger.info("derived_artifact_reused", artifact_kind=kind.value)
            return

    stored = derived_path(sha256, kind.value, PARAMS_VERSION[kind], THUMBNAIL_STRIP_FILENAME)
    destination = absolute(data_dir, stored)
    await render(
        build_thumbnail_strip(source, destination, duration_seconds=properties.duration_seconds),
        # The strip's graph is validated by the proxy's dry run being the same
        # decoder path, and a 2s dry run of a `tile` filter renders the whole
        # tile anyway - so it costs the strip twice and proves nothing extra.
        dry_run_first=False,
    )
    async with context.session_factory() as session:
        await _record_artifact(session, sha256, kind, str(stored), destination.stat().st_size)


async def _derive_proxy(
    context: JobContext,
    sha256: str,
    source: Path,
    properties: MediaProperties,
    data_dir: Path,
) -> None:
    """Render the CFR preview proxy, unless this exact key is already on disk."""
    kind = ArtifactKind.PROXY
    async with context.session_factory() as session:
        if await _existing_artifact(session, sha256, kind, data_dir) is not None:
            logger.info("derived_artifact_reused", artifact_kind=kind.value)
            return

    stored = derived_path(sha256, kind.value, PARAMS_VERSION[kind], PROXY_FILENAME)
    destination = absolute(data_dir, stored)
    await render(
        build_proxy(
            source,
            destination,
            display_height=properties.display_height,
            duration_seconds=properties.duration_seconds,
        ),
        on_progress=context.report.fraction,
        total_seconds=properties.duration_seconds,
    )
    async with context.session_factory() as session:
        await _record_artifact(session, sha256, kind, str(stored), destination.stat().st_size)


__all__ = [
    "INGEST_JOB_TYPE",
    "PROXY_FILENAME",
    "THUMBNAIL_STRIP_FILENAME",
    "BlobMissingError",
    "probe_media",
    "run_ingest",
]
