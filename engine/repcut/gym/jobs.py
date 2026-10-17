"""The rep-counting job: the pose model over the edit's hero segments, once."""

from repcut.db.models import JobType
from repcut.edit.service import EditNotReadyError
from repcut.gym import service
from repcut.jobs import JobContext, JobFailedError

GYM_REPS_JOB_TYPE = JobType.GYM_REPS.value


class RepCountUnavailableError(JobFailedError):
    """Nothing to count reps on."""


async def run_gym_reps(context: JobContext) -> None:
    record = context.record
    if record.project_id is None:
        raise RepCountUnavailableError("this job is not attached to a project")
    await context.report.step("counting reps on your best moments", 0.05)
    async with context.session_factory() as session:
        try:
            await service.count(
                session,
                context.settings.data_dir,
                record.project_id,
                device_preference=context.settings.torch_device,
            )
        except EditNotReadyError as error:
            raise RepCountUnavailableError(str(error)) from error
    await context.report.step("finished", 1.0)


__all__ = ["GYM_REPS_JOB_TYPE", "RepCountUnavailableError", "run_gym_reps"]
