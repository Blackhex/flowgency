from pathlib import Path

from agency.blueprints.cache import active_pins
from agency.blueprints import BlueprintLibrary, CompilationCache
from agency.configuration import ConfigStore
from agency.integrations import REGISTRY

from .launcher import JobLauncher, default_launcher
from .models import JobHandle, JobRecord, JobRequest, JobSpec
from .resolution import resolve_job_request
from .store import job_path, write_job


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


def _resolve_request(request: JobRequest) -> JobSpec:
    config_store = ConfigStore(Path(request.config_path))
    snapshot = config_store.load()
    config_dir = snapshot.path.resolve().parent
    library_root = snapshot.config.agency.agent_library or (config_dir / "agent-library")
    cache_root = snapshot.config.agency.compilation_cache or (config_dir / "compiled-agents")
    return resolve_job_request(
        request,
        config_store=config_store,
        library=BlueprintLibrary(Path(library_root)),
        cache=CompilationCache(Path(cache_root), _projector_registry()),
        integrations=REGISTRY,
    )


def _resolve_superseded_spec(spec: JobSpec) -> JobSpec:
    if spec.config_revision != "compat-unresolved":
        return spec
    resolved = _resolve_request(
        JobRequest(
            config_path=Path(spec.config_path),
            group_key=spec.group_key,
            agent_name=spec.agent_name,
            trigger=spec.trigger,
            task_input=spec.task_input,
            job_id=spec.job_id,
            routine_id=spec.routine_id,
            timeout_override=spec.timeout_override,
            trigger_context=spec.trigger_context,
            superseded_prompt_source=spec.prompt_source,
        )
    )
    return JobSpec(
        schema_version=resolved.schema_version,
        job_id=resolved.job_id,
        config_path=resolved.config_path,
        config_revision=resolved.config_revision,
        group_key=resolved.group_key,
        group_path=resolved.group_path,
        agent_name=resolved.agent_name,
        workspace_dir=resolved.workspace_dir,
        agent_dir=resolved.agent_dir,
        trigger=resolved.trigger,
        integration_name=resolved.integration_name,
        integration_config=resolved.integration_config,
        blueprint=resolved.blueprint,
        routine_id=resolved.routine_id,
        skill=resolved.skill,
        skill_arguments=resolved.skill_arguments,
        task_input=resolved.task_input,
        runtime_policy=resolved.runtime_policy,
        memory=resolved.memory,
        trigger_context=resolved.trigger_context,
        prompt_source=resolved.prompt_source,
        timeout_override=resolved.timeout_override,
        created_at=spec.created_at,
    )


def submit_job(spec: JobSpec, launcher: JobLauncher | None = None) -> JobHandle:
    spec = _resolve_superseded_spec(spec)
    spec.validate()
    group_path = Path(spec.workspace_dir)
    artifact = spec.blueprint.to_artifact()
    path = job_path(group_path, spec.job_id)
    record = JobRecord.from_spec(spec)
    from agency.blueprints.cache import pin_artifact, release_pin

    try:
        active_pins(spec.blueprint.cache_root, artifact.ref)
    except Exception:
        pass
    pin_artifact(spec.blueprint.cache_root, artifact.ref, spec.job_id)
    selected_launcher = launcher or default_launcher()
    try:
        write_job(path, record)
        result = selected_launcher.launch(path)
    except Exception as error:
        release_pin(spec.blueprint.cache_root, artifact.ref, spec.job_id)
        failed = JobRecord(
            spec=record.spec,
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
        write_job(path, failed)
        raise JobSubmissionError(str(error), path) from error
    return JobHandle(spec.job_id, "queued", path, result.worker_pid)


def submit_job_request(
    request: JobRequest,
    launcher: JobLauncher | None = None,
) -> JobHandle:
    return submit_job(_resolve_request(request), launcher)
