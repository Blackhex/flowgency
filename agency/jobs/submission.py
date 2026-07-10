from dataclasses import replace
from pathlib import Path

from .context import JobValidationError, resolve_job_context
from .launcher import JobLauncher, default_launcher
from .models import JobHandle, JobRecord, JobSpec
from .store import job_path, write_job


class JobSubmissionError(RuntimeError):
    def __init__(self, message: str, job_path: Path):
        super().__init__(message)
        self.job_path = job_path


def submit_job(spec: JobSpec, launcher: JobLauncher | None = None) -> JobHandle:
    context = resolve_job_context(spec)
    path = job_path(context.group_path, spec.job_id)
    record = JobRecord.from_spec(spec)
    write_job(path, record)
    selected_launcher = launcher or default_launcher()
    try:
        result = selected_launcher.launch(path)
    except Exception as error:
        failed = replace(
            record,
            status="failed",
            execution_summary=f"Launch error: {error}",
        )
        write_job(path, failed)
        raise JobSubmissionError(str(error), path) from error
    return JobHandle(spec.job_id, "queued", path, result.worker_pid)
