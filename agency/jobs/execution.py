"""Worker-side execution flow for durable agent jobs."""

import difflib
import logging
import os
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import yaml

from agency.blueprints.cache import release_pin
from agency.configuration.models import MemorySelector
from agency.permissions.zones import ZONE_INSTRUCTIONS
from agency.configuration.store import load_config_snapshot
from agency.fs.locks import LockCancelledError, exclusive_lock
from agency.integrations import get_integration
from agency.integrations.models import (
    EffectiveRuntimePolicy,
    IntegrationRunRequest,
)
from agency.memory.models import ResolvedMemory
from agency.prompts.projection import project_prompt_snapshots
from agency.memory.publication import (
    MemoryPublicationError,
    apply_publication,
    finalize_publication,
    prepare_publication,
)
from agency.memory.store import (
    _ensure_memory_locked,
    _memory_lock,
    _stage_memory_locked,
)
from agency.records.ingest import ingest_records
from agency.records.outbox import copy_outbox_memory_to_stage, create_outbox
from agency.records.validation import validate_outbox, writable_agent_names

from .atomic import atomic_write_text
from .authority import JobAuthorityError, JobAuthorityRef, JobStore
from .artifacts import JobArtifact, retain_failed_stage, retain_rejected_records
from .changes import capture_base_sha, capture_git_changes
from .launch_view import create_launch_view
from .models import JobRecord
from .store import (
    InvalidJobTransition,
    read_job,
    transition_job,
    write_job,
)

logger = logging.getLogger(__name__)

# The rejection summary quotes agent-controlled filenames back to the operator
# and is persisted and rendered, so it is bounded.
MAX_SUMMARY_REASONS = 2000
MAX_SUMMARY_SOURCE_NAME = 120


def _writable_agents(spec) -> frozenset[str]:
    """Instances a proposal may name as its executor.

    Resolved at submission and carried on the spec, so a configuration edit
    between submission and execution cannot fail a run the agent got right.
    Specs persisted before the field existed fall back to the live read.
    """
    if spec.writable_agents is not None:
        return frozenset(spec.writable_agents)
    snapshot = load_config_snapshot(Path(spec.config_path))
    return writable_agent_names(snapshot.config, spec.group_key)


def _rejection_summary(rejected, ingested_count: int) -> str:
    joined = "; ".join(
        f"{item.kind} {item.source_name[:MAX_SUMMARY_SOURCE_NAME]}: {item.reason}"
        for item in rejected
    )
    if len(joined) > MAX_SUMMARY_REASONS:
        joined = f"{joined[:MAX_SUMMARY_REASONS]} … (truncated)"
    filed = (
        f" Filed {ingested_count} valid "
        f"{'record' if ingested_count == 1 else 'records'}."
        if ingested_count
        else ""
    )
    return f"Rejected agent records: {joined}{filed}"


def _retained_outbox_artifacts(
    job_path: Path,
    job_id: str,
    outbox,
) -> list[dict[str, object]]:
    """Retain what survives of a rejected outbox, never at the cost of the reasons."""
    try:
        artifacts = retain_rejected_records(
            job_store=_jobs_dir(job_path),
            job_id=job_id,
            sources={
                "observations": outbox.observations,
                "proposals": outbox.proposals,
            },
        )
    except Exception as error:
        logger.warning(
            "Failed to retain rejected records for job %s: %s", job_id, error
        )
        return []
    return [artifact.to_dict() for artifact in artifacts]


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


def resolve_job_context(spec):
    runtime_policy = spec.runtime_policy.to_effective_policy()
    integration = get_integration(spec.integration_name)
    if hasattr(integration, "with_config") and spec.integration_config:
        integration = integration.with_config(spec.integration_config)
    return SimpleNamespace(
        group_root=spec.resolved_group_root,
        workspace_root=spec.resolved_workspace_root,
        integration=integration,
        timeout=spec.runtime_policy.timeout,
        runtime_policy=runtime_policy,
        sandbox_root=None,
        launch_dir=None,
    )


def _read_frontmatter(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) != 3:
        return {}, text
    frontmatter = parts[1].strip()
    body = parts[2].lstrip("\r\n")
    try:
        metadata = yaml.safe_load(frontmatter) or {}
    except yaml.YAMLError:
        metadata = {}
    return metadata, body


def _write_frontmatter_atomic(path: Path, metadata: dict, body: str) -> None:
    frontmatter = yaml.safe_dump(metadata, sort_keys=False).strip()
    payload = f"---\n{frontmatter}\n---\n\n{body}"
    atomic_write_text(path, payload)


def project_decision(record: JobRecord) -> None:
    context = record.spec.decision_context
    if not context:
        return
    decision_path = Path(context["decision_path"])
    metadata, body = _read_frontmatter(decision_path)
    if metadata.get("execution_job_id") != record.spec.job_id:
        return
    metadata.update(
        {
            "execution_status": record.status,
            "execution_agent": record.spec.agent_name,
            "executed_by": record.spec.agent_name,
            "execution_log": record.stdout_path,
            "changed_files": record.changed_files,
            "execution_summary": record.execution_summary,
        }
    )
    _write_frontmatter_atomic(decision_path, metadata, body)


def execute_job(authority: JobAuthorityRef) -> JobRecord:
    store, job_path, record = _read_authority(authority)
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
                outbox = create_outbox(launch_view, memory_files=canonical_files)
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

                try:
                    project_decision(record)
                except Exception as error:
                    logger.warning(
                        "Failed to project running status for job %s to its "
                        "decision: %s",
                        record.spec.job_id,
                        error,
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
                log_dir = Path(context.group_root) / "logs" / started.strftime("%Y-%m-%d")
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
                    memory_working_dir=outbox.memory,
                )
                result = integration.run(request)
                stdout_path.write_text(result.stdout, encoding="utf-8")
                persisted_stderr_path = None
                if result.stderr:
                    stderr_path.write_text(result.stderr, encoding="utf-8")
                    persisted_stderr_path = str(stderr_path.resolve())
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

                if result.exit_code != 0:
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
                        summary=summary,
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
                        copilot_home=getattr(result, "copilot_home", None),
                    )
                else:
                    try:
                        writable_agents = _writable_agents(spec)
                    except Exception as cfg_error:
                        cfg_path = spec.config_path
                        # Must fall through to tail — project_decision must run.
                        final = _terminalize_failure(
                            job_path,
                            summary=(
                                f"Config load failed for '{cfg_path}': "
                                f"{cfg_error}"
                            ),
                            started_at=started.isoformat(),
                            stdout_path=str(stdout_path.resolve()),
                            stderr_path=persisted_stderr_path,
                            exit_code=result.exit_code,
                            duration_seconds=result.duration_seconds,
                            changed_files=changes,
                            base_sha=base_sha,
                            memory_publication={
                                "failed_artifacts": _retained_outbox_artifacts(
                                    job_path, spec.job_id, outbox
                                )
                            },
                            session_id=result.session_id,
                            copilot_home=getattr(result, "copilot_home", None),
                        )
                    else:
                        validation = validate_outbox(
                            outbox,
                            writable_agents=writable_agents,
                        )
                        # Valid records land even when the same run produced
                        # invalid ones, and always before memory publishes.
                        ingested = ingest_records(
                            validation,
                            observations_dir=Path(context.group_root) / "observations",
                            proposals_dir=Path(context.group_root) / "proposals",
                            agent_name=spec.agent_name,
                            now=started,
                            job_id=spec.job_id,
                        )
                        if validation.rejected:
                            # Must fall through to tail — project_decision must run.
                            final = _terminalize_failure(
                                job_path,
                                summary=_rejection_summary(
                                    validation.rejected, len(ingested)
                                ),
                                started_at=started.isoformat(),
                                stdout_path=str(stdout_path.resolve()),
                                stderr_path=persisted_stderr_path,
                                exit_code=result.exit_code,
                                duration_seconds=result.duration_seconds,
                                changed_files=changes,
                                base_sha=base_sha,
                                memory_publication={
                                    "failed_artifacts": _retained_outbox_artifacts(
                                        job_path, spec.job_id, outbox
                                    )
                                },
                                session_id=result.session_id,
                                copilot_home=getattr(result, "copilot_home", None),
                            )
                        else:
                            try:
                                copy_outbox_memory_to_stage(outbox, stage.directory)
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
                                record_note = (
                                    f" Filed {len(ingested)} "
                                    f"{'record' if len(ingested) == 1 else 'records'}."
                                    if ingested
                                    else ""
                                )
                                summary = (
                                    f"Agent completed execution; captured "
                                    f"{len(changes)} changed "
                                    f"{'file' if len(changes) == 1 else 'files'}."
                                    f"{record_note}"
                                    if changes
                                    else (
                                        "Agent completed execution "
                                        f"(inferred from exit code).{record_note}"
                                    )
                                )
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
                                        copilot_home=getattr(result, "copilot_home", None),
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
                                        summary=f"Memory publication failed: {error}",
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
                                        copilot_home=getattr(result, "copilot_home", None),
                                    )
                                else:
                                    final = _terminalize_failure(
                                        job_path,
                                        summary=(
                                            f"Memory publication failed: {error}"
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
                                        copilot_home=getattr(result, "copilot_home", None),
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

    # Keep terminalization authoritative even if projection fails.
    try:
        project_decision(final)
    except Exception as error:
        logger.warning(
            "Failed to project final status for job %s to its decision: %s",
            final.spec.job_id,
            error,
        )

    return final
