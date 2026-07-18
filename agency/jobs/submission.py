from pathlib import Path

from agency.blueprints.cache import active_pins
from agency.blueprints import BlueprintLibrary, CompilationCache
from agency.configuration import ConfigStore, ValidationFailed
from agency.configuration.paths import initialize_control_directories, validate_resolved_paths
from agency.configuration.store import ConfigConflictError, ConfigSnapshot
from agency.integrations import REGISTRY

from .authority import JobStore
from .launcher import JobLauncher, default_launcher
from .models import JobHandle, JobRecord, JobRequest, JobSpec
from .resolution import resolve_job_request
from .store import revision_bound_group_operation


class JobSubmissionError(RuntimeError):
    def __init__(self, message: str, job_path: Path):
        super().__init__(message)
        self.job_path = job_path


def _projector_registry() -> dict[str, object]:
    return {
        name: integration.projector
        for name, integration in REGISTRY.items()
        if integration.projector is not None
    }


def _resolve_request(
    request: JobRequest,
    snapshot: ConfigSnapshot,
) -> JobSpec:
    config_store = ConfigStore(Path(request.config_path))
    config_dir = snapshot.path.resolve().parent
    library_root = snapshot.config.agency.agent_library or (
        config_dir / "agent-library"
    )
    cache_root = snapshot.config.agency.compilation_cache or (
        config_dir / "compiled-agents"
    )
    return resolve_job_request(
        request,
        config_store=config_store,
        library=BlueprintLibrary(Path(library_root)),
        cache=CompilationCache(Path(cache_root), _projector_registry()),
        integrations=REGISTRY,
        snapshot=snapshot,
    )


def _submit_resolved(
    spec: JobSpec,
    job_store: JobStore,
    launcher: JobLauncher | None = None,
) -> JobHandle:
    spec.validate()
    artifact = spec.blueprint.to_artifact()
    record = JobRecord.from_spec(spec)
    from agency.blueprints.cache import pin_artifact, release_pin

    try:
        active_pins(spec.blueprint.cache_root, artifact.ref)
    except Exception:
        pass
    pin_artifact(spec.blueprint.cache_root, artifact.ref, spec.job_id)
    selected_launcher = launcher or default_launcher()
    authority = job_store.reference(
        spec.group_key,
        spec.job_id,
        record.authority_digest,
    )
    try:
        authority = job_store.create(record)
        result = selected_launcher.launch(authority)
    except Exception as error:
        release_pin(spec.blueprint.cache_root, artifact.ref, spec.job_id)
        failed = JobRecord(
            spec=record.spec,
            authority_digest=record.authority_digest,
            status="failed",
            worker_pid=record.worker_pid,
            started_at=record.started_at,
            completed_at=record.completed_at,
            stdout_path=record.stdout_path,
            stderr_path=record.stderr_path,
            exit_code=record.exit_code,
            duration_seconds=record.duration_seconds,
            changed_files=record.changed_files,
            execution_summary=f"Launch error: {error}",
            base_sha=record.base_sha,
            memory_publication=record.memory_publication,
        )
        job_store.write(authority, failed)
        raise JobSubmissionError(str(error), authority.path) from error
    return JobHandle(spec.job_id, "queued", authority.path, result.worker_pid)


def submit_job_request(
    request: JobRequest,
    launcher: JobLauncher | None = None,
) -> JobHandle:
    config_store = ConfigStore(Path(request.config_path))
    last_conflict = None
    for _attempt in range(3):
        try:
            with revision_bound_group_operation(
                config_store,
                group_ids=(request.group_key,),
            ) as locked_snapshot:
                initialize_control_directories(locked_snapshot.config)
                issues = validate_resolved_paths(locked_snapshot.config)
                if issues:
                    raise ValidationFailed(issues)
                job_store = JobStore(locked_snapshot.config.agency.memory_store)
                return _submit_resolved(
                    _resolve_request(request, locked_snapshot),
                    job_store,
                    launcher,
                )
        except ConfigConflictError as error:
            last_conflict = error
    raise last_conflict or ConfigConflictError(
        "config changed while submitting job"
    )
