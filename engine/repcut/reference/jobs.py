"""The ``reference`` job: measure a reference clip's look, rhythm and captions, with progress."""

from __future__ import annotations

from sqlalchemy import select

from repcut.db.models import JobType, MediaFile, MediaRole
from repcut.grading.service import GradingNotReadyError
from repcut.jobs import JobContext, JobFailedError
from repcut.reference import service

REFERENCE_JOB_TYPE = JobType.REFERENCE.value


class ReferenceUnavailableError(JobFailedError):
    """The reference, or what ingest and analysis should have left for it, is missing."""


async def run_reference(context: JobContext) -> None:
    record = context.record
    if record.project_id is None or record.sha256 is None:
        raise ReferenceUnavailableError("this reference job is not attached to a clip")
    await context.report.step(
        "measuring the reference's look, rhythm and captions", 0.05, until=0.95
    )
    async with context.session_factory() as session:
        statement = select(MediaFile).where(
            MediaFile.project_id == record.project_id,
            MediaFile.sha256 == record.sha256,
            MediaFile.role == MediaRole.REFERENCE.value,
        )
        media_file = (await session.execute(statement)).scalars().first()
        if media_file is None:
            raise ReferenceUnavailableError("this reference is no longer in the project")
        try:
            await service.analyse(session, context.settings, media_file, refresh=True)
        except GradingNotReadyError as error:
            raise ReferenceUnavailableError(str(error)) from error
    await context.report.step("finished", 1.0)


__all__ = ["REFERENCE_JOB_TYPE", "ReferenceUnavailableError", "run_reference"]
