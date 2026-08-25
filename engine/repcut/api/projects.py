"""Projects and their media library.

The library joins ``media_files`` to ``media_blobs`` explicitly. No ORM
relationship is declared anywhere in this project (`db/models.py`): under asyncio
a lazy load raises at attribute access rather than at query time, which turns a
forgotten ``selectinload`` into a runtime error far from its cause.
"""

from fastapi import APIRouter, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repcut.api.deps import JobQueueDep, SessionDep
from repcut.api.errors import MediaFileNotFoundError, ProjectNotFoundError
from repcut.api.schemas import (
    JobResponse,
    MediaFileResponse,
    ProjectCreate,
    ProjectResponse,
)
from repcut.db.models import DerivedArtifact, Job, MediaBlob, MediaFile, Project
from repcut.logging import get_logger
from repcut.media.artifacts import PARAMS_VERSION, ArtifactKind
from repcut.media.ingest import INGEST_JOB_TYPE

logger = get_logger(__name__)

router = APIRouter(tags=["projects"])


@router.post(
    "/projects",
    response_model=ProjectResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a project",
)
async def create_project(body: ProjectCreate, session: SessionDep) -> ProjectResponse:
    """A new gym session to edit."""
    project = Project(name=body.name)
    session.add(project)
    await session.commit()
    logger.info("project_created", project_id=project.id)
    return ProjectResponse(
        id=project.id,
        name=project.name,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


@router.get("/projects", response_model=list[ProjectResponse], summary="List projects")
async def list_projects(session: SessionDep) -> list[ProjectResponse]:
    """Newest first - the dashboard's order."""
    statement = select(Project).order_by(Project.created_at.desc())
    projects = (await session.execute(statement)).scalars().all()
    return [
        ProjectResponse(
            id=project.id,
            name=project.name,
            created_at=project.created_at,
            updated_at=project.updated_at,
        )
        for project in projects
    ]


@router.get("/projects/{project_id}", response_model=ProjectResponse, summary="One project")
async def get_project(project_id: str, session: SessionDep) -> ProjectResponse:
    project = await session.get(Project, project_id)
    if project is None:
        raise ProjectNotFoundError("that project does not exist")
    return ProjectResponse(
        id=project.id,
        name=project.name,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


@router.get(
    "/projects/{project_id}/media",
    response_model=list[MediaFileResponse],
    summary="The project's media library",
)
async def list_media(project_id: str, session: SessionDep) -> list[MediaFileResponse]:
    """Every clip in the project, with the properties probed from its bytes.

    ``is_variable_frame_rate`` passes through as three-valued. The UI must render
    null as "unknown", never as "constant" - see
    ``media.metadata.detect_variable_frame_rate`` for what collapsing them costs.
    """
    project = await session.get(Project, project_id)
    if project is None:
        raise ProjectNotFoundError("that project does not exist")

    statement = (
        select(MediaFile, MediaBlob)
        .join(MediaBlob, MediaBlob.sha256 == MediaFile.sha256)
        .where(MediaFile.project_id == project_id)
        .order_by(MediaFile.position, MediaFile.added_at)
    )
    rows = (await session.execute(statement)).all()
    if not rows:
        return []

    artifacts = await _artifact_kinds_by_blob(session, [blob.sha256 for _, blob in rows])
    return [
        MediaFileResponse(
            id=reference.id,
            project_id=reference.project_id,
            sha256=reference.sha256,
            display_name=reference.display_name,
            position=reference.position,
            added_at=reference.added_at,
            size_bytes=blob.size_bytes,
            container_format=blob.container_format,
            duration_seconds=blob.duration_seconds,
            display_width=blob.display_width,
            display_height=blob.display_height,
            rotation_degrees=blob.rotation_degrees,
            fps_source=blob.fps_source,
            fps_normalized=blob.fps_normalized,
            is_variable_frame_rate=blob.is_variable_frame_rate,
            video_codec=blob.video_codec,
            audio_codec=blob.audio_codec,
            audio_sample_rate=blob.audio_sample_rate,
            has_proxy=ArtifactKind.PROXY.value in artifacts.get(blob.sha256, frozenset()),
            has_thumbnail_strip=(
                ArtifactKind.THUMBNAIL_STRIP.value in artifacts.get(blob.sha256, frozenset())
            ),
        )
        for reference, blob in rows
    ]


@router.post(
    "/media/{media_file_id}/reingest",
    response_model=JobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Re-derive a clip's artifacts",
)
async def reingest(media_file_id: str, session: SessionDep, queue: JobQueueDep) -> JobResponse:
    """Queue the ingest job again for the blob behind this reference.

    Safe to call at any time: ingest is a pure function of (source bytes,
    recipe), so a repeat run reuses whatever is already on disk and re-renders
    only what is missing. It exists so a failed ingest has a retry that is not
    "delete the clip and upload it again".
    """
    reference = await session.get(MediaFile, media_file_id)
    if reference is None:
        raise MediaFileNotFoundError("that clip is not in this library")

    job_id = await queue.enqueue(
        INGEST_JOB_TYPE, project_id=reference.project_id, sha256=reference.sha256
    )
    job = await session.get(Job, job_id)
    if job is None:  # pragma: no cover - the row was committed by enqueue
        raise MediaFileNotFoundError("the ingest job could not be created")
    return JobResponse(
        id=job.id,
        job_type=job.job_type,
        status=job.status,
        progress=job.progress,
        step=job.step,
        error=job.error,
        project_id=job.project_id,
        sha256=job.sha256,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


async def _artifact_kinds_by_blob(
    session: AsyncSession, digests: list[str]
) -> dict[str, frozenset[str]]:
    """Which artifact kinds exist, at the *current* version, per blob.

    Version-scoped on purpose: an artifact rendered under a superseded
    ``params_version`` is still on disk and still a row, but it is not what the
    player should be handed, so it does not count as present. The pair is
    filtered in Python rather than as a SQL row-value comparison, which SQLite
    supports unevenly and which would read as clever rather than obvious.
    """
    statement = select(
        DerivedArtifact.sha256, DerivedArtifact.artifact_kind, DerivedArtifact.params_version
    ).where(DerivedArtifact.sha256.in_(digests))

    current = {kind.value: PARAMS_VERSION[kind] for kind in ArtifactKind}
    found: dict[str, set[str]] = {}
    for digest, kind, version in (await session.execute(statement)).all():
        if current.get(kind) == version:
            found.setdefault(digest, set()).add(kind)
    return {digest: frozenset(kinds) for digest, kinds in found.items()}


__all__ = ["router"]
