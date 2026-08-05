"""The six tables of amendment 004, and the rules the database itself enforces.

The interesting assertions here are the ones a docstring cannot make: that a
blob really is shared by two projects, that it really cannot be deleted while
referenced, and that the derived-artifact key really is unique.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from repcut.db import Base, DerivedArtifact, Job, JobStatus, JobType, MediaBlob, MediaFile, Project
from repcut.media.artifacts import PARAMS_VERSION, ArtifactKind

EXPECTED_TABLES = {
    "projects",
    "media_blobs",
    "media_files",
    "derived_artifacts",
    "upload_sessions",
    "jobs",
}

# A digest-shaped literal. Not a credential and not derived from any file.
BLOB_SHA = "a" * 64


def _blob(sha256: str = BLOB_SHA) -> MediaBlob:
    return MediaBlob(sha256=sha256, size_bytes=1024, stored_path=f"media/blobs/aa/{sha256}/source")


async def _project(session: AsyncSession, name: str) -> Project:
    project = Project(name=name)
    session.add(project)
    await session.flush()
    return project


def test_all_six_tables_are_registered() -> None:
    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_byte_properties_live_on_the_blob_not_the_reference() -> None:
    """The split of amendment 004, asserted rather than described.

    Two references to one clip cannot disagree about its frame rate if only the
    blob carries the frame rate.
    """
    blob_columns = set(Base.metadata.tables["media_blobs"].columns.keys())
    reference_columns = set(Base.metadata.tables["media_files"].columns.keys())

    byte_properties = {
        "duration_seconds",
        "display_width",
        "display_height",
        "rotation_degrees",
        "fps_source",
        "fps_normalized",
        "video_codec",
        "audio_sample_rate",
    }

    assert byte_properties <= blob_columns
    assert byte_properties & reference_columns == set()


def test_both_frame_rates_are_recorded() -> None:
    """VFR footage drifts silently when only the nominal rate is stored."""
    columns = Base.metadata.tables["media_blobs"].columns

    assert columns["fps_source"].nullable
    assert columns["fps_normalized"].nullable
    assert "is_variable_frame_rate" in columns


async def test_one_blob_serves_two_projects(db_session: AsyncSession) -> None:
    """The measurable form of "a duplicate links rather than re-stores"."""
    first = await _project(db_session, "leg day")
    second = await _project(db_session, "push day")
    db_session.add(_blob())
    await db_session.flush()

    db_session.add(MediaFile(project_id=first.id, sha256=BLOB_SHA, display_name="clip.mp4"))
    db_session.add(MediaFile(project_id=second.id, sha256=BLOB_SHA, display_name="clip.mp4"))
    await db_session.commit()

    count = await db_session.scalar(
        text("SELECT COUNT(*) FROM media_files WHERE sha256 = :sha"), {"sha": BLOB_SHA}
    )
    blobs = await db_session.scalar(text("SELECT COUNT(*) FROM media_blobs"))

    assert (count, blobs) == (2, 1)


async def test_the_same_clip_cannot_be_added_to_one_project_twice(db_session: AsyncSession) -> None:
    project = await _project(db_session, "leg day")
    db_session.add(_blob())
    await db_session.flush()
    db_session.add(MediaFile(project_id=project.id, sha256=BLOB_SHA, display_name="clip.mp4"))
    await db_session.commit()

    db_session.add(MediaFile(project_id=project.id, sha256=BLOB_SHA, display_name="clip-copy.mp4"))

    with pytest.raises(IntegrityError):
        await db_session.commit()


async def test_a_referenced_blob_cannot_be_deleted(db_session: AsyncSession) -> None:
    """RESTRICT is the refcount. Nothing may delete bytes another project uses."""
    project = await _project(db_session, "leg day")
    db_session.add(_blob())
    await db_session.flush()
    db_session.add(MediaFile(project_id=project.id, sha256=BLOB_SHA, display_name="clip.mp4"))
    await db_session.commit()

    with pytest.raises(IntegrityError):
        await db_session.execute(
            text("DELETE FROM media_blobs WHERE sha256 = :sha"), {"sha": BLOB_SHA}
        )
        await db_session.commit()


async def test_deleting_a_project_leaves_the_blob_orphaned(db_session: AsyncSession) -> None:
    """The state the deferred GC exists to collect: unreferenced, not deleted.

    Prompt 02 ships no delete endpoint, so this is only reachable through raw
    SQL today. It is asserted now so the cascade rules are known-good on the day
    a delete surface lands.
    """
    project = await _project(db_session, "leg day")
    db_session.add(_blob())
    await db_session.flush()
    db_session.add(MediaFile(project_id=project.id, sha256=BLOB_SHA, display_name="clip.mp4"))
    await db_session.commit()

    await db_session.execute(text("DELETE FROM projects WHERE id = :id"), {"id": project.id})
    await db_session.commit()

    references = await db_session.scalar(text("SELECT COUNT(*) FROM media_files"))
    blobs = await db_session.scalar(text("SELECT COUNT(*) FROM media_blobs"))

    assert (references, blobs) == (0, 1)


async def test_one_artifact_per_source_kind_and_params_version(db_session: AsyncSession) -> None:
    db_session.add(_blob())
    await db_session.flush()
    db_session.add(
        DerivedArtifact(
            sha256=BLOB_SHA,
            artifact_kind=ArtifactKind.PROXY,
            params_version=PARAMS_VERSION[ArtifactKind.PROXY],
            stored_path="media/derived/aa/proxy/1/proxy.mp4",
        )
    )
    await db_session.commit()

    db_session.add(
        DerivedArtifact(
            sha256=BLOB_SHA,
            artifact_kind=ArtifactKind.PROXY,
            params_version=PARAMS_VERSION[ArtifactKind.PROXY],
            stored_path="media/derived/aa/proxy/1/proxy-again.mp4",
        )
    )

    with pytest.raises(IntegrityError):
        await db_session.commit()


async def test_a_recipe_bump_is_a_new_row_not_an_overwrite(db_session: AsyncSession) -> None:
    """Bumping params_version must not mutate or replace the existing artifact."""
    db_session.add(_blob())
    await db_session.flush()
    for version in (1, 2):
        db_session.add(
            DerivedArtifact(
                sha256=BLOB_SHA,
                artifact_kind=ArtifactKind.PROXY,
                params_version=version,
                stored_path=f"media/derived/aa/proxy/{version}/proxy.mp4",
            )
        )
    await db_session.commit()

    rows = await db_session.scalar(text("SELECT COUNT(*) FROM derived_artifacts"))

    assert rows == 2


async def test_artifacts_follow_their_source_when_it_is_deleted(db_session: AsyncSession) -> None:
    db_session.add(_blob())
    await db_session.flush()
    db_session.add(
        DerivedArtifact(
            sha256=BLOB_SHA,
            artifact_kind=ArtifactKind.THUMBNAIL_STRIP,
            params_version=1,
            stored_path="media/derived/aa/thumbnail_strip/1/strip.jpg",
        )
    )
    await db_session.commit()

    await db_session.execute(text("DELETE FROM media_blobs WHERE sha256 = :sha"), {"sha": BLOB_SHA})
    await db_session.commit()

    remaining = await db_session.scalar(text("SELECT COUNT(*) FROM derived_artifacts"))

    assert remaining == 0


async def test_job_status_is_constrained_by_the_database(db_session: AsyncSession) -> None:
    """A closed set, so a typo is rejected rather than rendered in the UI."""
    with pytest.raises(IntegrityError):
        await db_session.execute(
            text(
                "INSERT INTO jobs (id, job_type, status, progress, created_at, updated_at) "
                "VALUES ('j1', 'ingest', 'nearly-done', 0.5, '2026-01-01', '2026-01-01')"
            )
        )
        await db_session.commit()


async def test_progress_cannot_leave_the_unit_interval(db_session: AsyncSession) -> None:
    job = Job(job_type=JobType.INGEST, status=JobStatus.RUNNING, progress=1.4, step="probing")
    db_session.add(job)

    with pytest.raises(IntegrityError):
        await db_session.commit()


async def test_a_job_records_its_step_for_the_progress_stream(db_session: AsyncSession) -> None:
    job = Job(job_type=JobType.INGEST, status=JobStatus.RUNNING, progress=0.25, step="probing")
    db_session.add(job)
    await db_session.commit()

    stored = await db_session.scalar(text("SELECT step FROM jobs WHERE id = :id"), {"id": job.id})
    status = await db_session.scalar(text("SELECT status FROM jobs WHERE id = :id"), {"id": job.id})

    assert stored == "probing"
    # Stored as the value the API speaks, not the Python member name.
    assert status == "running"
