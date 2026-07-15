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
from agency.fs.locks import LockCancelledError, exclusive_lock
from agency.integrations import get_integration
from agency.integrations.models import (
    EffectiveRuntimePolicy,
    IntegrationRunRequest,
    ResolvedToolPolicy,
)
from agency.memory.models import ResolvedMemory
from agency.memory.publication import (
    MemoryPublicationError,
    apply_publication,
    finalize_publication,
    prepare_publication,
)
from agency.memory.store import ensure_memory, stage_memory

from .atomic import atomic_write_text
from .artifacts import JobArtifact, retain_failed_stage
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


def _selector_lock_path(resolved: ResolvedMemory) -> Path:
    return (
        resolved.directory.parent
        / ".selectors"
        / f"{resolved.memory_hash}.lock"
    )


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
    )
    write_job(job_path, updated)
    return updated


def _fallback_runtime_policy(context, timeout: int) -> EffectiveRuntimePolicy:
    sandbox = getattr(context, "sandbox_root", None)
    if sandbox and getattr(sandbox, "roots", ()):
        sandbox_mode = "restricted"
        sandbox_roots = tuple(sandbox.roots)
    else:
        sandbox_mode = "unrestricted"
        sandbox_roots = ()
    allowed_tools = (
        tuple(getattr(sandbox, "allowed_tools", ()) or ())
        if sandbox
        else ()
    )
    tool_mode = "allowlist" if allowed_tools else "all"
    return EffectiveRuntimePolicy(
        timeout=timeout,
        sandbox_mode=sandbox_mode,
        sandbox_roots=sandbox_roots,
        tools=ResolvedToolPolicy(tool_mode, allowed_tools),
    )


def resolve_job_context(spec):
    runtime_policy = spec.runtime_policy.to_effective_policy()
    integration = get_integration(spec.integration_name)
    if hasattr(integration, "with_config") and spec.integration_config:
        integration = integration.with_config(spec.integration_config)
    return SimpleNamespace(
        group_path=Path(spec.workspace_dir),
        workspace_dir=Path(spec.workspace_dir),
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


def execute_job(job_path: Path) -> JobRecord:
    record = read_job(job_path)
    record = transition_job(
        job_path,
        "queued",
        "waiting_for_memory",
        worker_pid=os.getpid(),
    )

    prompt_path = Path(job_path).with_suffix(".prompt")
    base_sha = None
    started = None
    launch_view = None
    final = record
    try:
        spec = record.spec
        context = resolve_job_context(spec)
        runtime_policy = getattr(context, "runtime_policy", None)
        if runtime_policy is None:
            runtime_policy = _fallback_runtime_policy(context, context.timeout)
        integration = context.integration
        artifact = spec.blueprint.to_artifact()
        launch_dir = Path(job_path).with_suffix("") / "launch"
        launch_view = getattr(context, "launch_dir", None)
        resolved_memory = _resolved_memory(spec)

        def cancelled() -> bool:
            return read_job(job_path).status == "cancelled"

        selector_lock = _selector_lock_path(resolved_memory)
        try:
            with exclusive_lock(selector_lock, wait=True, cancelled=cancelled):
                if cancelled():
                    final = _mark_cancelled_if_waiting(job_path)
                    return final
                snapshot = ensure_memory(resolved_memory)
                stage = stage_memory(resolved_memory, job_id=spec.job_id)
                canonical_files = dict(snapshot.files)
                if launch_view is None:
                    launch_view = create_launch_view(artifact, launch_dir)
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
                elif runtime_policy.sandbox_roots:
                    git_root = Path(runtime_policy.sandbox_roots[0])
                elif getattr(context, "workspace_dir", None):
                    git_root = Path(context.workspace_dir)
                base_sha = capture_base_sha(git_root)
                log_dir = (
                    Path(context.group_path)
                    / "shared"
                    / "logs"
                    / started.strftime("%Y-%m-%d")
                )
                log_dir.mkdir(parents=True, exist_ok=True)
                stem = (
                    f"{record.spec.agent_name}-{record.spec.trigger}-"
                    f"{record.spec.job_id}"
                )
                stdout_path = log_dir / f"{stem}.out"
                stderr_path = log_dir / f"{stem}.err"
                prompt_path.write_text(
                    record.spec.task_input, encoding="utf-8"
                )
                request = IntegrationRunRequest(
                    workspace_dir=Path(spec.workspace_dir),
                    launch_dir=launch_view,
                    task_file=prompt_path,
                    timeout=getattr(
                        context,
                        "timeout",
                        spec.runtime_policy.timeout,
                    ),
                    runtime_policy=runtime_policy,
                    skill=spec.skill,
                    skill_arguments=spec.skill_arguments,
                    enforce_validation=True,
                    memory_working_dir=stage.directory,
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
                    )
                else:
                    try:
                        prepared = prepare_publication(
                            stage,
                            job_store=_jobs_dir(job_path),
                            job_path=job_path,
                        )
                        finalize_publication(
                            apply_publication(
                                prepared,
                                retain_failed_stage_artifacts=True,
                            )
                        )
                        summary = (
                            f"Agent completed execution; captured "
                            f"{len(changes)} changed "
                            f"{'file' if len(changes) == 1 else 'files'}."
                            if changes
                            else (
                                "Agent completed execution "
                                "(inferred from exit code)."
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
                            )
                            write_job(job_path, final)
                    except MemoryPublicationError as error:
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
                            )
        except LockCancelledError:
            final = _mark_cancelled_if_waiting(job_path)
            return final
    except Exception as error:
        final = _terminalize_failure(
            job_path,
            summary=f"Execution error: {error}",
            started_at=None if started is None else started.isoformat(),
            base_sha=base_sha,
        )
    finally:
        prompt_path.unlink(missing_ok=True)
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
