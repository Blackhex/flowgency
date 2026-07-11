"""Worker-side execution flow for durable agent jobs."""

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .atomic import atomic_write_text
from .changes import capture_git_changes
from .context import resolve_job_context
from .models import JobRecord
from .store import read_job, transition_job

logger = logging.getLogger(__name__)


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
    try:
        context = resolve_job_context(record.spec)
        log_dir = context.group_path / "shared" / "logs" / started.strftime("%Y-%m-%d")
        log_dir.mkdir(parents=True, exist_ok=True)
        stem = f"{record.spec.agent_name}-{record.spec.trigger}-{record.spec.job_id}"
        stdout_path = log_dir / f"{stem}.out"
        stderr_path = log_dir / f"{stem}.err"
        prompt_path.write_text(record.spec.prompt_content, encoding="utf-8")
        result = context.integration.run(
            context.agent_dir,
            prompt_path,
            context.timeout,
            sandbox_root=context.sandbox_root,
        )
        stdout_path.write_text(result.stdout, encoding="utf-8")
        stderr_path.write_text(result.stderr, encoding="utf-8")
        native_changes = list(getattr(result, "changed_files", []))
        # Native per-file edits (currently only Copilot) win when present. For
        # every other integration, fall back to a git-status diff of the sandbox
        # root so outcome visibility is integration-agnostic, not Copilot-only.
        if not native_changes:
            git_root = None
            if context.sandbox_root and context.sandbox_root.roots:
                git_root = Path(context.sandbox_root.roots[0])
            elif context.agent_dir:
                git_root = Path(context.agent_dir)
            native_changes = capture_git_changes(git_root)
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
        summary = (
            "Agent completed execution (inferred from exit code)."
            if status == "complete"
            else f"Agent exited with code {result.exit_code}."
        )
        final = transition_job(
            job_path,
            "running",
            status,
            completed_at=datetime.now(timezone.utc).isoformat(),
            stdout_path=str(stdout_path.resolve()),
            stderr_path=str(stderr_path.resolve()),
            exit_code=result.exit_code,
            duration_seconds=result.duration_seconds,
            changed_files=changes,
            execution_summary=summary,
        )
    except Exception as error:
        final = transition_job(
            job_path,
            "running",
            "failed",
            completed_at=datetime.now(timezone.utc).isoformat(),
            execution_summary=f"Execution error: {error}",
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
