"""Worker-side execution flow for durable agent jobs."""

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import yaml

from agency.integrations import get_integration
from agency.integrations.models import EffectiveRuntimePolicy, IntegrationRunRequest, ResolvedToolPolicy

from .atomic import atomic_write_text
from .changes import capture_base_sha, capture_git_changes
from .launch_view import create_launch_view
from .models import JobRecord
from .store import read_job, transition_job

logger = logging.getLogger(__name__)


def _fallback_runtime_policy(context, timeout: int) -> EffectiveRuntimePolicy:
    sandbox = getattr(context, "sandbox_root", None)
    if sandbox and getattr(sandbox, "roots", ()):
        sandbox_mode = "restricted"
        sandbox_roots = tuple(sandbox.roots)
    else:
        sandbox_mode = "unrestricted"
        sandbox_roots = ()
    allowed_tools = tuple(getattr(sandbox, "allowed_tools", ()) or ()) if sandbox else ()
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
    started = datetime.now(timezone.utc)
    record = transition_job(
        job_path,
        "queued",
        "running",
        worker_pid=os.getpid(),
        started_at=started.isoformat(),
    )

    # Decision projection is best-effort metadata sync and must not block
    # durable job terminalization when decision files are missing/corrupt.
    try:
        project_decision(record)
    except Exception as error:
        logger.warning(
            "Failed to project running status for job %s to its decision: %s",
            record.spec.job_id,
            error,
        )

    prompt_path = Path(job_path).with_suffix(".prompt")
    base_sha = None
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
        if launch_view is None:
            launch_view = create_launch_view(artifact, launch_dir)
        # The capture must be tied to the one root the job actually ran in: prefer
        # the sandbox root the integration executes against, then the persisted
        # runtime policy root, then the durable workspace root.
        git_root = None
        if getattr(context, "sandbox_root", None) and getattr(context.sandbox_root, "roots", ()):
            git_root = Path(context.sandbox_root.roots[0])
        elif runtime_policy.sandbox_roots:
            git_root = Path(runtime_policy.sandbox_roots[0])
        elif getattr(context, "workspace_dir", None):
            git_root = Path(context.workspace_dir)
        # Record HEAD before the run so committed work is visible afterwards. A
        # fleet whose agents must commit every atomic change leaves a clean tree,
        # so working-tree-only would miss the rule and capture only the exception.
        base_sha = capture_base_sha(git_root)
        log_dir = Path(context.group_path) / "shared" / "logs" / started.strftime("%Y-%m-%d")
        log_dir.mkdir(parents=True, exist_ok=True)
        stem = f"{record.spec.agent_name}-{record.spec.trigger}-{record.spec.job_id}"
        stdout_path = log_dir / f"{stem}.out"
        stderr_path = log_dir / f"{stem}.err"
        prompt_path.write_text(record.spec.task_input, encoding="utf-8")
        request = IntegrationRunRequest(
            workspace_dir=Path(spec.workspace_dir),
            launch_dir=launch_view,
            task_file=prompt_path,
            timeout=getattr(context, "timeout", spec.runtime_policy.timeout),
            runtime_policy=runtime_policy,
            skill=spec.skill,
            skill_arguments=spec.skill_arguments,
            enforce_validation=True,
        )
        result = integration.run(request)
        stdout_path.write_text(result.stdout, encoding="utf-8")
        persisted_stderr_path = None
        if result.stderr:
            stderr_path.write_text(result.stderr, encoding="utf-8")
            persisted_stderr_path = str(stderr_path.resolve())
        native_changes = list(getattr(result, "changed_files", []))
        # Native per-file edits (currently only Copilot) win when present. For
        # every other integration, fall back to a git diff of the sandbox root —
        # unioning the working tree with the committed range base_sha..HEAD — so
        # outcome visibility is integration-agnostic, not Copilot-only.
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
        status = "complete" if result.exit_code == 0 else "failed"
        if status == "complete":
            if changes:
                noun = "file" if len(changes) == 1 else "files"
                summary = (
                    f"Agent completed execution; captured "
                    f"{len(changes)} changed {noun}."
                )
            else:
                summary = "Agent completed execution (inferred from exit code)."
        elif result.exit_code == 124:
            summary = f"Agent timed out after {getattr(context, 'timeout', spec.runtime_policy.timeout)} seconds."
        else:
            summary = f"Agent exited with code {result.exit_code}."
        final = transition_job(
            job_path,
            "running",
            status,
            completed_at=datetime.now(timezone.utc).isoformat(),
            stdout_path=str(stdout_path.resolve()),
            stderr_path=persisted_stderr_path,
            exit_code=result.exit_code,
            duration_seconds=result.duration_seconds,
            changed_files=changes,
            execution_summary=summary,
            base_sha=base_sha,
        )
    except Exception as error:
        final = transition_job(
            job_path,
            "running",
            "failed",
            completed_at=datetime.now(timezone.utc).isoformat(),
            execution_summary=f"Execution error: {error}",
            base_sha=base_sha,
        )
    finally:
        prompt_path.unlink(missing_ok=True)

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
