"""Worker-side execution flow for durable agent jobs."""

import difflib
import logging
import os
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml

from flowgency.blueprints.cache import release_pin
from flowgency.configuration.models import MemorySelector
from flowgency.permissions.zones import ZONE_INSTRUCTIONS
from flowgency.configuration.store import ConfigStore
from flowgency.fs.locks import LockCancelledError, exclusive_lock
from flowgency.integrations import get_integration
from flowgency.integrations.models import (
    EffectiveRuntimePolicy,
    IntegrationRunRequest,
)
from flowgency.integrations.ticket_tools import build_ticket_tool_launch
from flowgency.memory.models import ResolvedMemory
from flowgency.prompts.projection import project_prompt_snapshots
from flowgency.memory.publication import (
    MemoryPublicationError,
    apply_publication,
    finalize_publication,
    prepare_publication,
)
from flowgency.memory.store import (
    _ensure_memory_locked,
    _memory_lock,
    _stage_memory_locked,
)
from flowgency.memory.launch import copy_launch_memory_to_stage, prepare_launch_memory
from flowgency.tickets.access import TicketAccessRegistry
from flowgency.tickets.broker import TicketBroker
from flowgency.tickets.service import TicketService
from flowgency.tickets.storages.registry import resolve_storage
from flowgency.workflows.library import WorkflowLibrary

from .authority import JobAuthorityError, JobAuthorityRef, JobStore
from .artifacts import JobArtifact, retain_failed_stage
from .changes import capture_base_sha, capture_git_changes
from .launch_view import create_launch_view
from .models import JobRecord
from .processes import ProcessStopEvidence, RuntimeProcessLifecycle
from .store import (
    InvalidJobTransition,
    job_lock_path,
    read_job,
    transition_job,
    write_job,
)
from .tickets import TicketJobCoordinator, _ticket_task_input

logger = logging.getLogger(__name__)

RETIRED_TRIGGERS = frozenset({"decision", "decision_retry"})


def _unenforced_policy_note(result) -> str:
    """A Markdown tail naming what the integration could not enforce.

    Integrations report these in a fixed order, most serious first, so the
    note is reproduced verbatim rather than re-ordered here.
    """
    entries = [str(entry) for entry in getattr(result, "unenforced_rules", ()) or ()]
    if not entries:
        return ""
    listed = "\n".join(f"- {entry}" for entry in entries)
    return f"\n\n**Permission policy not fully enforced**\n\n{listed}"


def _resolved_memory(spec) -> ResolvedMemory:
    selector_payload = dict(spec.memory.selector)
    return ResolvedMemory(
        selector=MemorySelector(
            scope=selector_payload["scope"],
            channel=selector_payload.get("channel"),
        ),
        canonical_json=spec.memory.canonical_json,
        memory_hash=spec.memory.memory_hash,
        directory=Path(spec.memory.path),
    )


def _jobs_dir(job_path: Path) -> Path:
    return Path(job_path).resolve().parent


def _read_authority(
    authority: JobAuthorityRef,
) -> tuple[JobStore, Path, JobRecord]:
    store = JobStore.from_store_root(authority.store_root)
    record = store.read(authority)
    return store, authority.path, record


def _mark_cancelled_if_waiting(job_path: Path) -> JobRecord:
    record = read_job(job_path)
    if record.status == "cancelled":
        return record
    raise InvalidJobTransition(
        f"Expected cancelled job, found {record.status!r}"
    )


def _read_stage_files(stage_dir: Path) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for item in sorted(
        stage_dir.iterdir(), key=lambda path: path.name.casefold()
    ):
        if item.is_file():
            files[item.name] = item.read_bytes()
    return files


def _failed_memory_artifacts(
    job_path: Path,
    stage_dir: Path,
    old_files: dict[str, bytes],
) -> list[dict[str, object]]:
    diff_lines = []
    current_files = _read_stage_files(stage_dir)
    for name in sorted(set(old_files) | set(current_files)):
        old_text = old_files.get(name, b"").decode(
            "utf-8", errors="replace"
        ).splitlines(keepends=True)
        new_text = current_files.get(name, b"").decode(
            "utf-8", errors="replace"
        ).splitlines(keepends=True)
        diff_lines.extend(
            difflib.unified_diff(
                old_text,
                new_text,
                fromfile=f"canonical/{name}",
                tofile=f"stage/{name}",
            )
        )
    artifacts = retain_failed_stage(
        job_store=_jobs_dir(job_path),
        job_id=read_job(job_path).spec.job_id,
        stage_directory=stage_dir,
        diff_bytes="".join(diff_lines).encode("utf-8"),
    )
    return [artifact.to_dict() for artifact in artifacts]


def _retained_failed_artifacts(job_path: Path) -> list[dict[str, object]]:
    job_id = read_job(job_path).spec.job_id
    root = _jobs_dir(job_path) / "artifacts" / job_id
    if not root.exists():
        return []
    artifacts: list[dict[str, object]] = []
    for item in sorted(root.iterdir(), key=lambda path: path.name.casefold()):
        if item.is_file():
            artifacts.append(
                JobArtifact(
                    name=item.name,
                    path=str(item.resolve()),
                    size=item.stat().st_size,
                ).to_dict()
            )
    return artifacts


def _terminalize_failure(
    job_path: Path,
    *,
    summary: str,
    started_at: str | None,
    stdout_path: str | None = None,
    stderr_path: str | None = None,
    exit_code: int | None = None,
    duration_seconds: float | None = None,
    changed_files: list[dict[str, object]] | None = None,
    base_sha: str | None = None,
    memory_publication: dict[str, object] | None = None,
    session_id: str | None = None,
    copilot_home: str | None = None,
) -> JobRecord:
    record = read_job(job_path)
    if record.status == "cancelled":
        return record
    expected = record.status
    if expected not in {"running", "waiting_for_memory"}:
        return record
    return transition_job(
        job_path,
        expected,
        "failed",
        completed_at=datetime.now(timezone.utc).isoformat(),
        started_at=started_at,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        exit_code=exit_code,
        duration_seconds=duration_seconds,
        changed_files=changed_files or [],
        execution_summary=summary,
        base_sha=base_sha,
        memory_publication=memory_publication,
        session_id=session_id,
        copilot_home=copilot_home,
    )


def _merge_failed_terminal_metadata(
    job_path: Path,
    *,
    summary: str | None,
    started_at: str | None,
    stdout_path: str | None = None,
    stderr_path: str | None = None,
    exit_code: int | None = None,
    duration_seconds: float | None = None,
    changed_files: list[dict[str, object]] | None = None,
    base_sha: str | None = None,
    memory_publication: dict[str, object] | None = None,
    session_id: str | None = None,
    copilot_home: str | None = None,
) -> JobRecord:
    current = read_job(job_path)
    if current.status != "failed":
        return current

    merged_memory_publication = dict(current.memory_publication or {})
    if memory_publication:
        merged_memory_publication.update(memory_publication)

    merged_changed_files = (
        current.changed_files
        if current.changed_files
        else (changed_files or [])
    )

    updated = replace(
        current,
        started_at=current.started_at or started_at,
        stdout_path=current.stdout_path or stdout_path,
        stderr_path=current.stderr_path or stderr_path,
        exit_code=current.exit_code if current.exit_code is not None else exit_code,
        duration_seconds=(
            current.duration_seconds
            if current.duration_seconds is not None
            else duration_seconds
        ),
        changed_files=merged_changed_files,
        execution_summary=current.execution_summary or summary,
        base_sha=current.base_sha or base_sha,
        memory_publication=(merged_memory_publication or None),
        session_id=current.session_id or session_id,
        copilot_home=current.copilot_home or copilot_home,
    )
    write_job(job_path, updated)
    return updated


def _merge_result_metadata(job_path: Path, update: dict[str, Any]) -> JobRecord:
    with exclusive_lock(job_lock_path(job_path), wait=True):
        record = read_job(job_path)
        merged = dict(record.result_metadata or {})
        merged.update(update)
        updated = replace(record, result_metadata=merged or None)
        write_job(job_path, updated)
        return updated


def _requires_live_ticket_tools(record: JobRecord, snapshot) -> bool:
    if record.spec.ticket_target is not None:
        return True
    if record.spec.trigger not in {"manual_prompt", "scheduled_prompt"}:
        return False
    team = snapshot.config.teams.get(record.spec.team_key)
    return bool(team and team.workflows)


def _ticket_runtime(record: JobRecord, job_store: JobStore):
    config_store = ConfigStore(Path(record.spec.config_path))
    snapshot = config_store.load()
    if not _requires_live_ticket_tools(record, snapshot):
        return None
    workflow_library = snapshot.config.flowgency.workflow_library
    if workflow_library is None:
        raise ValueError("Current workflow definition is unavailable")
    registry = TicketAccessRegistry(job_store)
    service = TicketService(
        config_store,
        WorkflowLibrary(Path(workflow_library)),
        resolve_storage,
        registry.validate_context,
        clock=lambda: datetime.now(timezone.utc),
    )
    coordinator = TicketJobCoordinator(
        service=service,
        job_store=job_store,
        config_store=config_store,
        submitter=lambda request: None,
    )
    return SimpleNamespace(
        config_store=config_store,
        snapshot=snapshot,
        registry=registry,
        service=service,
        coordinator=coordinator,
    )


def _refresh_ticket_prompt(task_input: str, current_view) -> str:
    marker = "\n## Flowgency reporting protocol"
    _, separator, suffix = task_input.partition(marker)
    refreshed = _ticket_task_input(current_view)
    if not separator:
        return refreshed
    return refreshed + separator + suffix


def _ticket_cleanup_metadata(
    stopped: ProcessStopEvidence,
    result,
    *,
    error: dict[str, str] | None = None,
) -> dict[str, Any]:
    if result.cleared and not result.pending_cleanup:
        status = "cleared"
    elif result.pending_cleanup and not result.cleared:
        status = "pending"
    elif result.cleared:
        status = "partial"
    else:
        status = "idle"
    requires_retry = bool(result.pending_cleanup)
    if error is not None:
        if status == "idle":
            status = "error"
        if status != "cleared":
            requires_retry = True
    payload = {
        "status": status,
        "job_id": stopped.job_id,
        "generation": stopped.generation,
        "confirmed": stopped.confirmed,
        "reason": stopped.reason,
        "cleared": [ref.model_dump(mode="json") for ref in result.cleared],
        "pending_cleanup": [ref.model_dump(mode="json") for ref in result.pending_cleanup],
        "requires_retry": requires_retry,
    }
    if error is not None:
        payload["error"] = dict(error)
    return payload


def _ticket_cleanup_error(phase: str, error: Exception) -> dict[str, str]:
    messages = {
        "broker_close": "Ticket broker shutdown failed",
        "cleanup": "Ticket cleanup failed",
    }
    return {
        "phase": phase,
        "kind": type(error).__name__,
        "message": messages.get(phase, "Ticket cleanup failed"),
    }


def _finalize_ticket_runtime(
    *,
    authority: JobAuthorityRef,
    job_path: Path,
    record: JobRecord,
    ticket_runtime,
    broker,
    ticket_generation: str | None,
    result,
) -> str | None:
    if broker is None or ticket_runtime is None or ticket_generation is None:
        return None
    stop_evidence = (
        getattr(result, "process_stop_evidence", None)
        if result is not None
        else None
    )
    if stop_evidence is None:
        stop_evidence = ProcessStopEvidence(
            job_id=record.spec.job_id,
            generation=ticket_generation,
            confirmed=False,
            reason="unknown",
        )
    cleanup_result = SimpleNamespace(cleared=(), pending_cleanup=())
    metadata_error = None
    broker_error = None
    cleanup_error = None
    try:
        broker.close()
    except Exception as error:
        broker_error = error
    try:
        cleanup_result = ticket_runtime.coordinator.cleanup(authority, stop_evidence)
    except Exception as error:
        cleanup_error = error
    if cleanup_error is not None:
        metadata_error = _ticket_cleanup_error("cleanup", cleanup_error)
    elif broker_error is not None:
        metadata_error = _ticket_cleanup_error("broker_close", broker_error)
    _merge_result_metadata(
        job_path,
        {
            "ticket_cleanup": _ticket_cleanup_metadata(
                stop_evidence,
                cleanup_result,
                error=metadata_error,
            )
        },
    )
    if cleanup_error is not None:
        return metadata_error["message"]
    if broker_error is not None:
        return metadata_error["message"]
    return None


def resolve_job_context(spec):
    runtime_policy = spec.runtime_policy.to_effective_policy()
    integration = get_integration(spec.integration_name)
    if hasattr(integration, "with_config") and spec.integration_config:
        integration = integration.with_config(spec.integration_config)
    return SimpleNamespace(
        team_root=spec.resolved_team_root,
        workspace_root=spec.resolved_workspace_root,
        integration=integration,
        timeout=spec.runtime_policy.timeout,
        runtime_policy=runtime_policy,
        sandbox_root=None,
        launch_dir=None,
    )


def _retired_trigger_summary(trigger: str) -> str:
    return (
        f"Retired pipeline trigger '{trigger}' is no longer supported. "
        "Submit work through a ticket workflow instead."
    )


def execute_job(authority: JobAuthorityRef) -> JobRecord:
    store, job_path, record = _read_authority(authority)
    if record.spec.trigger in RETIRED_TRIGGERS:
        final = transition_job(
            job_path,
            "queued",
            "failed",
            completed_at=datetime.now(timezone.utc).isoformat(),
            execution_summary=_retired_trigger_summary(record.spec.trigger),
        )
        try:
            release_pin(
                record.spec.blueprint.cache_root,
                record.spec.blueprint.cache_ref,
                record.spec.job_id,
            )
        except Exception:
            pass
        return final
    record = transition_job(
        job_path,
        "queued",
        "waiting_for_memory",
        worker_pid=os.getpid(),
    )

    base_sha = None
    started = None
    launch_view = None
    final = record
    try:
        store.read(authority)
        spec = record.spec
        context = resolve_job_context(spec)
        runtime_policy = context.runtime_policy
        integration = context.integration
        artifact = spec.blueprint.to_artifact()
        launch_dir = Path(job_path).with_suffix("") / "launch"
        launch_view = getattr(context, "launch_dir", None)
        resolved_memory = _resolved_memory(spec)

        def cancelled() -> bool:
            return store.read(authority).status == "cancelled"

        try:
            with _memory_lock(
                resolved_memory,
                wait=True,
                cancelled=cancelled,
            ) as memory_lease:
                if cancelled():
                    final = _mark_cancelled_if_waiting(job_path)
                    return final
                snapshot = _ensure_memory_locked(resolved_memory, memory_lease)
                stage = _stage_memory_locked(
                    resolved_memory,
                    job_id=spec.job_id,
                    lease=memory_lease,
                )
                canonical_files = dict(snapshot.files)
                if launch_view is None:
                    launch_view = create_launch_view(artifact, launch_dir)
                launch_memory = prepare_launch_memory(launch_view, memory_files=canonical_files)
                if spec.private_prompts:
                    projector = getattr(integration, "projector", None)
                    if projector is None:
                        raise ValueError(
                            "Integration has no runtime projector for private prompt projection."
                        )
                    project_prompt_snapshots(
                        projector,
                        spec.private_prompts,
                        launch_view / ZONE_INSTRUCTIONS,
                    )
                started = datetime.now(timezone.utc)
                record = transition_job(
                    job_path,
                    "waiting_for_memory",
                    "running",
                    worker_pid=os.getpid(),
                    started_at=started.isoformat(),
                )

                # Tie change capture to the root the job actually ran in.
                git_root = None
                if (
                    getattr(context, "sandbox_root", None)
                    and getattr(context.sandbox_root, "roots", ())
                ):
                    git_root = Path(context.sandbox_root.roots[0])
                elif getattr(context, "workspace_root", None):
                    git_root = Path(context.workspace_root)
                base_sha = capture_base_sha(git_root)
                log_dir = Path(context.team_root) / "logs" / started.strftime("%Y-%m-%d")
                log_dir.mkdir(parents=True, exist_ok=True)
                stem = (
                    f"{record.spec.agent_name}-{record.spec.trigger}-"
                    f"{record.spec.job_id}"
                )
                prompt_path = log_dir / f"{stem}.prompt"
                stdout_path = log_dir / f"{stem}.out"
                stderr_path = log_dir / f"{stem}.err"
                prompt_path.write_text(
                    record.spec.task_input, encoding="utf-8"
                )
                request = IntegrationRunRequest(
                    workspace_root=spec.resolved_workspace_root,
                    launch_dir=launch_view,
                    task_file=prompt_path,
                    timeout=getattr(
                        context,
                        "timeout",
                        spec.runtime_policy.timeout,
                    ),
                    runtime_policy=runtime_policy.with_launch_zones(launch_view),
                    skill=spec.skill,
                    skill_arguments=spec.skill_arguments,
                    enforce_validation=True,
                    memory_working_dir=launch_memory.memory,
                )
                ticket_runtime = None
                broker = None
                ticket_generation = None
                result = None
                runtime_error = None
                ticket_finalization_error = None
                try:
                    ticket_runtime = _ticket_runtime(record, store)
                    if ticket_runtime is not None:
                        broker = TicketBroker(
                            ticket_runtime.service,
                            ticket_runtime.registry,
                            authority=authority,
                        )
                        endpoint = broker.start()
                        ticket_generation = endpoint.grant.context.session_id
                        if record.spec.ticket_target is not None:
                            ticket_view = ticket_runtime.coordinator.preflight(record)
                            prompt_path.write_text(
                                _refresh_ticket_prompt(record.spec.task_input, ticket_view),
                                encoding="utf-8",
                            )
                        request = replace(
                            request,
                            ticket_tools=replace(
                                build_ticket_tool_launch(endpoint),
                                lifecycle=RuntimeProcessLifecycle(
                                    job_id=record.spec.job_id,
                                    generation=ticket_generation,
                                ),
                            ),
                        )
                    try:
                        result = integration.run(request)
                    except Exception as error:
                        runtime_error = error
                finally:
                    ticket_finalization_error = _finalize_ticket_runtime(
                        authority=authority,
                        job_path=job_path,
                        record=record,
                        ticket_runtime=ticket_runtime,
                        broker=broker,
                        ticket_generation=ticket_generation,
                        result=result,
                    )
                if runtime_error is not None:
                    raise runtime_error
                policy_note = _unenforced_policy_note(result)
                stdout_path.write_text(result.stdout, encoding="utf-8")
                persisted_stderr_path = None
                if result.stderr:
                    stderr_path.write_text(result.stderr, encoding="utf-8")
                    persisted_stderr_path = str(stderr_path.resolve())
                # A write the agent tried but did not land is denial evidence a
                # read-only run must not lose; the parsed changes only record what
                # succeeded, so the attempt is recorded against the job separately.
                write_attempts = list(getattr(result, "write_attempts", []) or [])
                if write_attempts:
                    _merge_result_metadata(
                        job_path, {"write_attempts": write_attempts}
                    )
                native_changes = list(getattr(result, "changed_files", []))
                if not native_changes:
                    native_changes = capture_git_changes(git_root, base_sha)
                changes = [
                    {
                        "path": item.path,
                        "status": item.status,
                        "lines_added": item.lines_added,
                        "lines_removed": item.lines_removed,
                    }
                    for item in native_changes
                ]

                if ticket_finalization_error is not None:
                    final = _terminalize_failure(
                        job_path,
                        summary=ticket_finalization_error + policy_note,
                        started_at=started.isoformat(),
                        stdout_path=str(stdout_path.resolve()),
                        stderr_path=persisted_stderr_path,
                        exit_code=result.exit_code,
                        duration_seconds=result.duration_seconds,
                        changed_files=changes,
                        base_sha=base_sha,
                        session_id=result.session_id,
                        copilot_home=result.copilot_home,
                    )
                elif result.exit_code != 0:
                    if result.exit_code == 124:
                        timeout_seconds = getattr(
                            context,
                            "timeout",
                            spec.runtime_policy.timeout,
                        )
                        summary = (
                            "Agent timed out after "
                            f"{timeout_seconds} "
                            "seconds."
                        )
                    else:
                        summary = f"Agent exited with code {result.exit_code}."
                    final = _terminalize_failure(
                        job_path,
                        summary=summary + policy_note,
                        started_at=started.isoformat(),
                        stdout_path=str(stdout_path.resolve()),
                        stderr_path=persisted_stderr_path,
                        exit_code=result.exit_code,
                        duration_seconds=result.duration_seconds,
                        changed_files=changes,
                        base_sha=base_sha,
                        memory_publication={
                            "failed_artifacts": _failed_memory_artifacts(
                                job_path,
                                stage.directory,
                                canonical_files,
                            )
                        },
                        session_id=result.session_id,
                        copilot_home=result.copilot_home,
                    )
                else:
                    try:
                        copy_launch_memory_to_stage(launch_memory, stage.directory)
                        prepared = prepare_publication(
                            stage,
                            job_store=_jobs_dir(job_path),
                            job_path=job_path,
                            lease=memory_lease,
                        )
                        finalize_publication(
                            apply_publication(
                                prepared,
                                retain_failed_stage_artifacts=True,
                                lease=memory_lease,
                            )
                        )
                        summary = (
                            f"Agent completed execution; captured "
                            f"{len(changes)} changed "
                            f"{'file' if len(changes) == 1 else 'files'}."
                            if changes
                            else "Agent completed execution (inferred from exit code)."
                        ) + policy_note
                        final = read_job(job_path)
                        if final.status == "complete":
                            final = replace(
                                final,
                                started_at=started.isoformat(),
                                stdout_path=str(stdout_path.resolve()),
                                stderr_path=persisted_stderr_path,
                                exit_code=result.exit_code,
                                duration_seconds=result.duration_seconds,
                                changed_files=changes,
                                execution_summary=summary,
                                base_sha=base_sha,
                                session_id=result.session_id,
                                copilot_home=result.copilot_home,
                            )
                            write_job(job_path, final)
                    except (MemoryPublicationError, ValueError) as error:
                        current = read_job(job_path)
                        artifacts = _retained_failed_artifacts(job_path)
                        if not artifacts:
                            artifacts = _failed_memory_artifacts(
                                job_path,
                                stage.directory,
                                canonical_files,
                            )
                        if current.status == "failed":
                            final = _merge_failed_terminal_metadata(
                                job_path,
                                summary=f"Memory publication failed: {error}{policy_note}",
                                started_at=started.isoformat(),
                                stdout_path=str(stdout_path.resolve()),
                                stderr_path=persisted_stderr_path,
                                exit_code=result.exit_code,
                                duration_seconds=result.duration_seconds,
                                changed_files=changes,
                                base_sha=base_sha,
                                memory_publication={
                                    "failed_artifacts": artifacts,
                                },
                                session_id=result.session_id,
                                copilot_home=result.copilot_home,
                            )
                        else:
                            final = _terminalize_failure(
                                job_path,
                                summary=(
                                    f"Memory publication failed: {error}"
                                    f"{policy_note}"
                                ),
                                started_at=started.isoformat(),
                                stdout_path=str(stdout_path.resolve()),
                                stderr_path=persisted_stderr_path,
                                exit_code=result.exit_code,
                                duration_seconds=result.duration_seconds,
                                changed_files=changes,
                                base_sha=base_sha,
                                memory_publication={
                                    "failed_artifacts": artifacts,
                                },
                                session_id=result.session_id,
                                copilot_home=result.copilot_home,
                            )
        except LockCancelledError:
            final = _mark_cancelled_if_waiting(job_path)
            return final
    except JobAuthorityError as error:
        final = _terminalize_failure(
            job_path,
            summary=f"Execution authority error: {error}",
            started_at=None if started is None else started.isoformat(),
            base_sha=base_sha,
        )
    except Exception as error:
        final = _terminalize_failure(
            job_path,
            summary=f"Execution error: {error}",
            started_at=None if started is None else started.isoformat(),
            base_sha=base_sha,
        )
    finally:
        try:
            release_pin(
                record.spec.blueprint.cache_root,
                record.spec.blueprint.cache_ref,
                record.spec.job_id,
            )
        except Exception:
            pass
    return final
