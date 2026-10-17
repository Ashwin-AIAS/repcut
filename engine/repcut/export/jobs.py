"""The export job: render the project's edit with its current settings, resumably.

A re-run after a crash, a cancel or an engine restart (the queue requeues
interrupted jobs) reuses every finished segment and renders only the rest.
"""

from repcut.db.models import JobType
from repcut.export import service
from repcut.export.render import ExportResult
from repcut.grading.service import GradingNotReadyError
from repcut.jobs import JobContext, JobFailedError

EXPORT_JOB_TYPE = JobType.EXPORT.value
EXPORT_ALL_JOB_TYPE = JobType.EXPORT_ALL.value


class ExportUnavailableError(JobFailedError):
    """Nothing to export, or the exported file failed its platform check."""


async def run_export(context: JobContext) -> None:
    record = context.record
    if record.project_id is None:
        raise ExportUnavailableError("this export job is not attached to a project")
    await context.report.step("gathering the edit, grades and captions", 0.01)

    def progress(step: str, fraction: float) -> None:
        context.report.fraction(fraction)

    async with context.session_factory() as session:
        await context.report.step("rendering shots", 0.02, until=1.0)
        try:
            result: ExportResult = await service.export(
                session,
                context.settings.data_dir,
                record.project_id,
                on_progress=progress,
                device_preference=context.settings.torch_device,
            )
        except (service.ExportNotReadyError, GradingNotReadyError) as error:
            raise ExportUnavailableError(str(error)) from error
    if result.problems:
        raise ExportUnavailableError(
            "the exported file is not safe to post: " + "; ".join(result.problems)
        )
    await context.report.step("finished", 1.0)


async def run_export_all(context: JobContext) -> None:
    """Every platform preset from the one edit: reel, square, landscape, in config order.

    Each is the ordinary export (its own crop, the subject tracked for that
    aspect) and each must pass the platform check; the first that fails stops
    the job with its cause. Finished shots are reused on a re-run, per preset.
    """
    record = context.record
    if record.project_id is None:
        raise ExportUnavailableError("this export job is not attached to a project")
    presets = service.config().presets
    for index, preset in enumerate(presets):
        base = index / len(presets)
        await context.report.step(f"rendering {preset.label}", base + 0.01)

        def progress(step: str, fraction: float, base: float = base) -> None:
            context.report.fraction(min(1.0, base + fraction / len(presets)))

        async with context.session_factory() as session:
            try:
                result = await service.export(
                    session,
                    context.settings.data_dir,
                    record.project_id,
                    preset=preset,
                    on_progress=progress,
                    device_preference=context.settings.torch_device,
                )
            except (service.ExportNotReadyError, GradingNotReadyError) as error:
                raise ExportUnavailableError(str(error)) from error
        if result.problems:
            raise ExportUnavailableError(
                f"the {preset.label} file is not safe to post: " + "; ".join(result.problems)
            )
    await context.report.step("finished", 1.0)


__all__ = [
    "EXPORT_ALL_JOB_TYPE",
    "EXPORT_JOB_TYPE",
    "ExportUnavailableError",
    "run_export",
    "run_export_all",
]
