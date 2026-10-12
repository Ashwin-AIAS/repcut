"""The ``grade_preview`` job: render each scene of a clip under its current grade.

A full graded scene is a render, not a request (amendment 012 row 10), so it is
a job with progress over ``/ws/jobs``. Scenes whose current grade already has a
preview are skipped: re-running after one scene's lighting override renders
that scene only.
"""

from __future__ import annotations

from sqlalchemy import select

from repcut.db.models import JobType, MediaFile
from repcut.grading.service import GradingNotReadyError, clip_grade, ensure_previews
from repcut.jobs import JobContext, JobFailedError

GRADE_PREVIEW_JOB_TYPE = JobType.GRADE_PREVIEW.value

_DECIDE_AT = 0.02
_RENDER_FROM = 0.1


class GradePreviewUnavailableError(JobFailedError):
    """The clip or its proxy is gone, so there is nothing to grade."""


async def run_grade_preview(context: JobContext) -> None:
    record = context.record
    if record.project_id is None or record.sha256 is None:
        raise GradePreviewUnavailableError("this grading job is not attached to a clip")
    data_dir = context.settings.data_dir

    await context.report.step("deciding the grade", _DECIDE_AT)
    async with context.session_factory() as session:
        statement = select(MediaFile).where(
            MediaFile.project_id == record.project_id, MediaFile.sha256 == record.sha256
        )
        media_file = (await session.execute(statement)).scalars().first()
        if media_file is None:
            raise GradePreviewUnavailableError("this clip is no longer in the project")
        try:
            grade = await clip_grade(session, data_dir, media_file)
        except GradingNotReadyError as error:
            raise GradePreviewUnavailableError(str(error)) from error

    span = 1.0 - _RENDER_FROM

    async def on_scene(position: int, total: int) -> None:
        start = _RENDER_FROM + span * position / total
        await context.report.step(
            f"grading scene {position + 1} of {total}", start, until=start + span / total
        )

    await ensure_previews(data_dir, grade, on_scene=on_scene, on_progress=context.report.fraction)
    await context.report.step("finished", 1.0)


__all__ = ["GRADE_PREVIEW_JOB_TYPE", "GradePreviewUnavailableError", "run_grade_preview"]
