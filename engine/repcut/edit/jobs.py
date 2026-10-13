"""The edit's background jobs: scan and analyse the music library; measure a clip.

Both are slow enough to need progress (decoding and beat tracking a track,
reading every frame of a proxy), and both are idempotent: a re-run analyses
only pending tracks and re-measures only a clip whose signals are missing.
"""

from sqlalchemy import select

from repcut.db.models import JobType, MediaFile, MusicTrack, MusicTrackStatus
from repcut.edit import service
from repcut.grading.service import GradingNotReadyError
from repcut.jobs import JobContext, JobFailedError
from repcut.music.library import analyse_track, scan

MUSIC_SCAN_JOB_TYPE = JobType.MUSIC_SCAN.value
EDIT_SIGNALS_JOB_TYPE = JobType.EDIT_SIGNALS.value


class EditSignalsUnavailableError(JobFailedError):
    """The clip or its preview is gone, so there is nothing to measure."""


async def run_music_scan(context: JobContext) -> None:
    data_dir = context.settings.data_dir
    await context.report.step("looking for tracks", 0.02)
    async with context.session_factory() as session:
        files = await scan(session, data_dir)
        pending = [
            row
            for row in (
                await session.execute(
                    select(MusicTrack).where(
                        MusicTrack.status == MusicTrackStatus.PENDING,
                        MusicTrack.present.is_(True),
                    )
                )
            ).scalars()
            if row.sha256 in files
        ]
        for position, row in enumerate(pending):
            start = 0.1 + 0.9 * position / len(pending)
            await context.report.step(
                f"analysing track {position + 1} of {len(pending)}",
                start,
                until=start + 0.9 / len(pending),
            )
            await analyse_track(session, data_dir, row, files[row.sha256])
    await context.report.step("finished", 1.0)


async def run_edit_signals(context: JobContext) -> None:
    record = context.record
    if record.project_id is None or record.sha256 is None:
        raise EditSignalsUnavailableError("this measuring job is not attached to a clip")
    await context.report.step("measuring motion, shake, loudness and speech", 0.05, until=0.95)
    async with context.session_factory() as session:
        statement = select(MediaFile).where(
            MediaFile.project_id == record.project_id, MediaFile.sha256 == record.sha256
        )
        media_file = (await session.execute(statement)).scalars().first()
        if media_file is None:
            raise EditSignalsUnavailableError("this clip is no longer in the project")
        try:
            await service.measure_clip(session, context.settings.data_dir, media_file)
        except GradingNotReadyError as error:
            raise EditSignalsUnavailableError(str(error)) from error
    await context.report.step("finished", 1.0)


__all__ = [
    "EDIT_SIGNALS_JOB_TYPE",
    "MUSIC_SCAN_JOB_TYPE",
    "EditSignalsUnavailableError",
    "run_edit_signals",
    "run_music_scan",
]
