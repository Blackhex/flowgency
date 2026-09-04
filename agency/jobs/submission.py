import logging
from pathlib import Path

import yaml

from agency.blueprints.cache import active_pins
from agency.blueprints import BlueprintLibrary, CompilationCache
from agency.configuration import ConfigStore, ValidationFailed
from agency.configuration.paths import (
    initialize_storage_directories,
    validate_resolved_paths,
)
from agency.configuration.store import ConfigConflictError, ConfigSnapshot
from agency.integrations import REGISTRY
from agency.prompts import PromptStore

from .authority import JobStore
from .launcher import JobLauncher
from .models import JobHandle, JobRecord, JobRequest, JobSpec
from .resolution import resolve_job_request
from .store import read_job, revision_bound_team_operation

log = logging.getLogger("agency.jobs.submission")


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
    prompt_root = snapshot.config.agency.prompt_store or (
        config_dir / "prompts"
    )
    return resolve_job_request(
        request,
        config_store=config_store,
        library=BlueprintLibrary(Path(library_root)),
        cache=CompilationCache(Path(cache_root), _projector_registry()),
        prompt_store=PromptStore(Path(prompt_root)),
        integrations=REGISTRY,
        snapshot=snapshot,
    )


def _submit_resolved(
    spec: JobSpec,
    job_store: JobStore,
    *,
    due_at: str | None = None,
) -> JobHandle:
    """Pin the artifact and put the job in the queue. The drain starts it."""
    spec.validate()
    artifact = spec.blueprint.to_artifact()
    record = JobRecord.from_spec(spec, due_at=due_at)
    from agency.blueprints.cache import pin_artifact, release_pin

    try:
        active_pins(spec.blueprint.cache_root, artifact.ref)
    except Exception:
        pass
    pin_artifact(spec.blueprint.cache_root, artifact.ref, spec.job_id)
    authority = job_store.reference(
        spec.team_key,
        spec.job_id,
        record.authority_digest,
    )
    try:
        authority = job_store.create(record)
    except Exception as error:
        release_pin(spec.blueprint.cache_root, artifact.ref, spec.job_id)
        raise JobSubmissionError(str(error), authority.path) from error
    return JobHandle(spec.job_id, "queued", authority.path, None)


def _settle(handle: JobHandle) -> JobHandle:
    """Report what the drain made of the job it was just handed.

    A record that is ``failed`` without ever having been claimed never
    reached a worker, so the submission failed and the caller must hear so.
    A scheduled caller depends on this to leave its marker unwritten.
    """
    from agency.blueprints.cache import release_pin

    try:
        record = read_job(handle.path)
    except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError):
        return handle
    if record.status == "failed" and record.launched_at is None:
        summary = record.execution_summary or "job could not be launched"
        try:
            release_pin(
                record.spec.blueprint.cache_root,
                record.spec.blueprint.cache_ref,
                record.spec.job_id,
            )
        except Exception:
            log.warning("could not release the pin of %s", record.spec.job_id)
        raise JobSubmissionError(summary, handle.path)
    return JobHandle(
        handle.job_id,
        record.status,
        handle.path,
        record.worker_pid,
    )


def submit_job_request(
    request: JobRequest,
    launcher: JobLauncher | None = None,
) -> JobHandle:
    config_store = ConfigStore(Path(request.config_path))
    last_conflict = None
    for _attempt in range(3):
        try:
            with revision_bound_team_operation(
                config_store,
                team_ids=(request.team_key,),
            ) as locked_snapshot:
                issues = validate_resolved_paths(locked_snapshot.config)
                if issues:
                    raise ValidationFailed(issues)
                initialize_storage_directories(locked_snapshot.config)
                job_store = JobStore(locked_snapshot.config.agency.memory_store)
                handle = _submit_resolved(
                    _resolve_request(request, locked_snapshot),
                    job_store,
                    due_at=request.due_at,
                )
                snapshot_config = locked_snapshot.config
                memory_store = job_store.memory_store
            try:
                from .queue import drain

                drain(snapshot_config, memory_store=memory_store, launcher=launcher)
            except Exception:
                log.warning("drain after submission failed", exc_info=True)
            return _settle(handle)
        except ConfigConflictError as error:
            last_conflict = error
    raise last_conflict or ConfigConflictError(
        "config changed while submitting job"
    )
