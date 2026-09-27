"""The six tables of amendment 004, and the rules the database itself enforces.

The interesting assertions here are the ones a docstring cannot make: that a
blob really is shared by two projects, that it really cannot be deleted while
referenced, and that the derived-artifact key really is unique.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.ext.asyncio import AsyncSession

from repcut.analysis.params import SCENE_PARAMS_VERSION
from repcut.db import (
    Base,
    DerivedArtifact,
    GeminiSceneCache,
    Job,
    JobStatus,
    JobType,
    MediaBlob,
    MediaFile,
    Project,
    Scene,
    UploadSession,
    UploadStatus,
)
from repcut.media.artifacts import PARAMS_VERSION, ArtifactKind

EXPECTED_TABLES = {
    "projects",
    "media_blobs",
    "media_files",
    "derived_artifacts",
    "upload_sessions",
    "jobs",
    "scenes",
    "gemini_scene_cache",
}

# A digest-shaped literal. Not a credential and not derived from any file.
BLOB_SHA = "a" * 64


def _blob(sha256: str = BLOB_SHA) -> MediaBlob:
    return MediaBlob(sha256=sha256, size_bytes=1024, stored_path=f"media/blobs/aa/{sha256}/source")


def _scene(
    sha256: str = BLOB_SHA,
    sequence_index: int = 0,
    detector_params_version: int = SCENE_PARAMS_VERSION,
    **overrides: object,
) -> Scene:
    scene = Scene(
        sha256=sha256,
        detector_params_version=detector_params_version,
        sequence_index=sequence_index,
        start_seconds=float(sequence_index) * 2.0,
        end_seconds=float(sequence_index) * 2.0 + 2.0,
        start_frame_source=sequence_index * 60,
        end_frame_source=sequence_index * 60 + 60,
    )
    for field, value in overrides.items():
        setattr(scene, field, value)
    return scene


async def _project(session: AsyncSession, name: str) -> Project:
    project = Project(name=name)
    session.add(project)
    await session.flush()
    return project


def _upload(project_id: str, sha256: str | None = BLOB_SHA, **overrides: object) -> UploadSession:
    session = UploadSession(
        project_id=project_id,
        display_name="clip.mp4",
        declared_size_bytes=4096,
        chunk_size_bytes=1024,
        declared_sha256=sha256,
        part_path="uploads/session.part",
    )
    for field, value in overrides.items():
        setattr(session, field, value)
    return session


def test_all_tables_are_registered() -> None:
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


async def test_a_persisted_timestamp_comes_back_timezone_aware(db_session: AsyncSession) -> None:
    """SQLite drops the offset on write. UTCDateTime has to put it back on read.

    Without this the value is aware going in and naive coming out, and the
    asymmetry surfaces nowhere near here - it surfaces as a TypeError at the
    first ``stored < utcnow()``, which Prompt 03's Gemini cache expiry is.
    """
    project = await _project(db_session, "leg day")
    await db_session.commit()
    # expire_on_commit is False, so the in-memory object still holds the value
    # Python wrote. Only a real load exercises the read path.
    db_session.expunge_all()

    loaded = await db_session.get(Project, project.id)

    assert loaded is not None
    assert loaded.created_at.tzinfo is not None
    assert loaded.created_at.utcoffset() == timedelta(0)
    # The comparison the asymmetry used to break, made at the call site.
    assert loaded.created_at <= datetime.now(UTC)


async def test_a_naive_timestamp_is_refused(db_session: AsyncSession) -> None:
    """Rejected rather than assumed to be UTC - a wrong hour compares fine."""
    job = Job(job_type=JobType.INGEST, started_at=datetime(2026, 1, 1, 12, 0))
    db_session.add(job)

    with pytest.raises(StatementError, match="naive datetime"):
        await db_session.commit()


async def test_a_second_in_progress_upload_of_the_same_clip_is_refused(
    db_session: AsyncSession,
) -> None:
    """The resume-lookup path, in its enforcing form.

    A browser tab refreshed mid-upload has lost the session id. Without this
    index it starts a second transfer and abandons the first ``.part`` with
    nothing referencing it; with it, the collision is the signal to look the
    existing session up and resume.
    """
    project = await _project(db_session, "leg day")
    db_session.add(_upload(project.id))
    await db_session.commit()

    db_session.add(_upload(project.id, part_path="uploads/second.part"))

    with pytest.raises(IntegrityError):
        await db_session.commit()


async def test_a_finished_upload_does_not_block_re_uploading_the_clip(
    db_session: AsyncSession,
) -> None:
    """The index is partial for this reason: only one transfer may be in flight."""
    project = await _project(db_session, "leg day")
    db_session.add(_upload(project.id, status=UploadStatus.COMPLETED))
    db_session.add(_upload(project.id, status=UploadStatus.ABORTED))
    await db_session.commit()

    db_session.add(_upload(project.id, part_path="uploads/third.part"))
    await db_session.commit()

    in_flight = await db_session.scalar(
        text("SELECT COUNT(*) FROM upload_sessions WHERE status = 'in_progress'")
    )

    assert in_flight == 1


async def test_uploads_without_a_declared_hash_do_not_collide(db_session: AsyncSession) -> None:
    """Nothing identifies them, so nothing may claim they are the same transfer."""
    project = await _project(db_session, "leg day")
    db_session.add(_upload(project.id, sha256=None))
    db_session.add(_upload(project.id, sha256=None, part_path="uploads/second.part"))

    await db_session.commit()

    rows = await db_session.scalar(text("SELECT COUNT(*) FROM upload_sessions"))

    assert rows == 2


async def test_two_projects_may_upload_the_same_clip_at_once(db_session: AsyncSession) -> None:
    """The index is scoped per project - concurrent uploads are not each other's."""
    first = await _project(db_session, "leg day")
    second = await _project(db_session, "push day")
    db_session.add(_upload(first.id))
    db_session.add(_upload(second.id, part_path="uploads/second.part"))

    await db_session.commit()

    rows = await db_session.scalar(text("SELECT COUNT(*) FROM upload_sessions"))

    assert rows == 2


async def test_a_scene_must_have_positive_duration(db_session: AsyncSession) -> None:
    """The check the DB enforces, not just a convention callers might skip."""
    db_session.add(_blob())
    await db_session.flush()
    db_session.add(_scene(start_seconds=5.0, end_seconds=5.0))

    with pytest.raises(IntegrityError):
        await db_session.commit()


async def test_a_scene_sequence_index_cannot_be_negative(db_session: AsyncSession) -> None:
    db_session.add(_blob())
    await db_session.flush()
    db_session.add(_scene(sequence_index=-1))

    with pytest.raises(IntegrityError):
        await db_session.commit()


async def test_a_scene_detector_params_version_must_be_positive(db_session: AsyncSession) -> None:
    db_session.add(_blob())
    await db_session.flush()
    db_session.add(_scene(detector_params_version=0))

    with pytest.raises(IntegrityError):
        await db_session.commit()


async def test_one_scene_per_source_detector_version_and_sequence(
    db_session: AsyncSession,
) -> None:
    """The key resolution 4 of amendment 008 relies on: a re-detect is a new row."""
    db_session.add(_blob())
    await db_session.flush()
    db_session.add(_scene(sequence_index=0))
    await db_session.commit()

    db_session.add(_scene(sequence_index=0))

    with pytest.raises(IntegrityError):
        await db_session.commit()


async def test_a_clip_re_added_to_a_second_project_reuses_its_scenes(
    db_session: AsyncSession,
) -> None:
    """Scenes are keyed on the blob, not a project's reference to it."""
    first = await _project(db_session, "leg day")
    second = await _project(db_session, "push day")
    db_session.add(_blob())
    await db_session.flush()
    scene = _scene(sequence_index=0)
    db_session.add(scene)
    await db_session.commit()

    db_session.add(MediaFile(project_id=first.id, sha256=BLOB_SHA, display_name="clip.mp4"))
    db_session.add(MediaFile(project_id=second.id, sha256=BLOB_SHA, display_name="clip.mp4"))
    await db_session.commit()

    scenes_for_blob = await db_session.scalar(
        text("SELECT COUNT(*) FROM scenes WHERE sha256 = :sha"), {"sha": BLOB_SHA}
    )

    assert scenes_for_blob == 1


async def test_scenes_follow_their_source_when_it_is_deleted(db_session: AsyncSession) -> None:
    db_session.add(_blob())
    await db_session.flush()
    db_session.add(_scene(sequence_index=0))
    await db_session.commit()

    await db_session.execute(text("DELETE FROM media_blobs WHERE sha256 = :sha"), {"sha": BLOB_SHA})
    await db_session.commit()

    remaining = await db_session.scalar(text("SELECT COUNT(*) FROM scenes"))

    assert remaining == 0


async def test_sampled_frame_path_is_null_until_the_sampler_runs(
    db_session: AsyncSession,
) -> None:
    db_session.add(_blob())
    await db_session.flush()
    scene = _scene(sequence_index=0)
    db_session.add(scene)
    await db_session.commit()

    stored = await db_session.scalar(
        text("SELECT sampled_frame_path FROM scenes WHERE id = :id"), {"id": scene.id}
    )

    assert stored is None


async def _scene_row(db_session: AsyncSession) -> Scene:
    db_session.add(_blob())
    await db_session.flush()
    scene = _scene(sequence_index=0)
    db_session.add(scene)
    await db_session.commit()
    return scene


async def test_gemini_cache_is_unique_per_scene_and_prompt_version(
    db_session: AsyncSession,
) -> None:
    scene = await _scene_row(db_session)
    db_session.add(GeminiSceneCache(scene_id=scene.id, gemini_prompt_version=1))
    await db_session.commit()

    db_session.add(GeminiSceneCache(scene_id=scene.id, gemini_prompt_version=1))

    with pytest.raises(IntegrityError):
        await db_session.commit()


async def test_a_prompt_version_bump_is_a_new_cache_row_not_an_overwrite(
    db_session: AsyncSession,
) -> None:
    scene = await _scene_row(db_session)
    db_session.add(GeminiSceneCache(scene_id=scene.id, gemini_prompt_version=1))
    db_session.add(GeminiSceneCache(scene_id=scene.id, gemini_prompt_version=2))
    await db_session.commit()

    rows = await db_session.scalar(text("SELECT COUNT(*) FROM gemini_scene_cache"))

    assert rows == 2


async def test_gemini_cache_prompt_version_must_be_positive(db_session: AsyncSession) -> None:
    scene = await _scene_row(db_session)
    db_session.add(GeminiSceneCache(scene_id=scene.id, gemini_prompt_version=0))

    with pytest.raises(IntegrityError):
        await db_session.commit()


async def test_gemini_cache_follows_its_scene_when_it_is_deleted(
    db_session: AsyncSession,
) -> None:
    """Deleting a scene (a re-detect at a new params_version) drops its cache too."""
    scene = await _scene_row(db_session)
    db_session.add(GeminiSceneCache(scene_id=scene.id, gemini_prompt_version=1))
    await db_session.commit()

    await db_session.execute(text("DELETE FROM scenes WHERE id = :id"), {"id": scene.id})
    await db_session.commit()

    remaining = await db_session.scalar(text("SELECT COUNT(*) FROM gemini_scene_cache"))

    assert remaining == 0


async def test_gemini_cache_stores_the_response_body_for_inspection(
    db_session: AsyncSession,
) -> None:
    """gemini-usage.md: cache entries survive restarts and are inspectable."""
    scene = await _scene_row(db_session)
    db_session.add(
        GeminiSceneCache(
            scene_id=scene.id,
            gemini_prompt_version=1,
            content_type="exercise",
            exercise_guess="barbell squat",
            environment="home gym",
            lighting_quality="good",
            lighting_temperature="warm",
            lighting_direction="front",
            energy_level="high",
            aesthetic_notes="clean framing, no clutter",
            raw_response_json='{"content_type": "exercise"}',
        )
    )
    await db_session.commit()

    stored = await db_session.scalar(
        text("SELECT energy_level FROM gemini_scene_cache WHERE scene_id = :id"),
        {"id": scene.id},
    )

    assert stored == "high"
