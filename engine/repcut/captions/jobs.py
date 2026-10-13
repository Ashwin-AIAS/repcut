"""The caption job: transcribe one clip, locally, with progress."""

from sqlalchemy import select

from repcut.captions import service
from repcut.captions.transcribe import CaptionModelUnavailableError
from repcut.db.models import JobType, MediaFile
from repcut.grading.service import GradingNotReadyError
from repcut.jobs import JobContext, JobFailedError

TRANSCRIBE_JOB_TYPE = JobType.TRANSCRIBE.value


class TranscribeUnavailableError(JobFailedError):
    """The clip, its preview or the model is missing. A fixed sentence."""


async def run_transcribe(context: JobContext) -> None:
    record = context.record
    if record.project_id is None or record.sha256 is None:
        raise TranscribeUnavailableError("this caption job is not attached to a clip")
    await context.report.step("listening for speech and writing it down", 0.05, until=0.95)
    async with context.session_factory() as session:
        statement = select(MediaFile).where(
            MediaFile.project_id == record.project_id, MediaFile.sha256 == record.sha256
        )
        media_file = (await session.execute(statement)).scalars().first()
        if media_file is None:
            raise TranscribeUnavailableError("this clip is no longer in the project")
        try:
            await service.transcribe_clip(session, context.settings.data_dir, media_file)
        except (GradingNotReadyError, CaptionModelUnavailableError) as error:
            raise TranscribeUnavailableError(str(error)) from error
    await context.report.step("finished", 1.0)


__all__ = ["TRANSCRIBE_JOB_TYPE", "TranscribeUnavailableError", "run_transcribe"]
