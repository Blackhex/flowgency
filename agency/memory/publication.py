from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import difflib
from pathlib import Path

import yaml

from agency.fs.atomic import atomic_write_bytes, atomic_write_text
from agency.fs.locks import exclusive_lock
from agency.jobs.artifacts import JobArtifact, retain_failed_stage
from agency.jobs.store import InvalidJobTransition, read_job, transition_job

from .models import MemoryPublicationReceipt, MemoryStage, PreparedPublication
from .store import (
    _canonical_directory,
    _ensure_child_directory,
    _ensure_infrastructure_directory,
    _is_symlink_or_reparse,
    _lock_path,
    _read_canonical_files,
    _replace_canonical_files,
    _safe_rmtree,
    _store_root,
    _validate_job_id,
    _validate_memory_hash,
    _validate_resolved_memory,
    memory_content_revision,
)


class MemoryPublicationError(RuntimeError):
    pass


class MemoryPublicationConflictError(MemoryPublicationError):
    def __init__(
        self,
        *,
        reason: str,
        expected_revision: str,
        current_revision: str,
    ) -> None:
        message = (
            f"stale-stage publication conflict: {reason} "
            f"(expected {expected_revision}, found {current_revision})"
        )
        super().__init__(message)
        self.reason = reason
        self.expected_revision = expected_revision
        self.current_revision = current_revision


class PublicationCrash(RuntimeError):
    pass


@dataclass(frozen=True)
class AppliedPublication:
    prepared: PreparedPublication
    published_at: str
    diff_artifact: JobArtifact | None


def prepare_publication(
    stage: MemoryStage,
    *,
    job_store: Path,
    job_path: Path | None = None,
) -> PreparedPublication:
    try:
        _validate_resolved_memory(stage.resolved)
        job_store = _validate_job_store(job_store)
        canonical_job_path = _job_path(job_store, stage.job_id)
        if job_path is not None and Path(job_path).resolve() != canonical_job_path.resolve():
            raise MemoryPublicationError(
                "job path must match the canonical file in job store"
            )
        old_files = _read_canonical_files(_canonical_directory(stage.resolved))
        old_revision = memory_content_revision(old_files)
        if old_revision != stage.base_revision:
            raise MemoryPublicationConflictError(
                reason="base revision changed before prepare",
                expected_revision=stage.base_revision,
                current_revision=old_revision,
            )
        new_files = _read_canonical_files(stage.directory)
        if not new_files:
            raise MemoryPublicationError(
                "memory stage must contain at least one markdown file"
            )
        store_root = _store_root(stage.resolved)
        return PreparedPublication(
            stage=stage,
            job_store=job_store,
            job_path=canonical_job_path,
            selector=stage.resolved.selector.model_dump(exclude_none=True),
            memory_hash=stage.resolved.memory_hash,
            old_revision=old_revision,
            new_revision=memory_content_revision(new_files),
            old_files=old_files,
            new_files=new_files,
            diff_bytes=_build_diff(old_files, new_files),
            journal_path=_journal_path(
                store_root,
                stage.resolved.memory_hash,
                stage.job_id,
            ),
            backup_path=_backup_path(
                store_root,
                stage.resolved.memory_hash,
                stage.job_id,
            ),
            no_change=old_files == new_files,
        )
    except MemoryPublicationError:
        raise
    except Exception as error:
        raise MemoryPublicationError(str(error)) from error


def apply_publication(
    prepared: PreparedPublication,
    *,
    crash_at: str | None = None,
    fail_after_publish: bool = False,
    retain_failed_stage_artifacts: bool = False,
) -> AppliedPublication:
    try:
        with exclusive_lock(_lock_path(prepared.stage.resolved), wait=True):
            current_files = _read_canonical_files(
                _canonical_directory(prepared.stage.resolved)
            )
            current_revision = memory_content_revision(current_files)
            if current_revision != prepared.stage.base_revision:
                raise MemoryPublicationConflictError(
                    reason="base revision changed before apply",
                    expected_revision=prepared.stage.base_revision,
                    current_revision=current_revision,
                )

            _write_journal(prepared, phase="prepared")
            if crash_at == "prepared":
                raise PublicationCrash("simulated crash at prepared")

            if prepared.no_change:
                _write_journal(prepared, phase="published")
                if crash_at == "published":
                    raise PublicationCrash("simulated crash at published")
                return AppliedPublication(
                    prepared=prepared,
                    published_at=_now_iso(),
                    diff_artifact=None,
                )

            _write_backup(prepared.backup_path, prepared.old_files)
            _write_journal(prepared, phase="backed_up")
            if crash_at == "backed_up":
                raise PublicationCrash("simulated crash at backed_up")

            _replace_canonical_files(
                _canonical_directory(prepared.stage.resolved),
                prepared.new_files,
            )
            _write_journal(prepared, phase="published")
            if crash_at == "published":
                raise PublicationCrash("simulated crash at published")
            if fail_after_publish:
                raise OSError("simulated publication failure")

            return AppliedPublication(
                prepared=prepared,
                published_at=_now_iso(),
                diff_artifact=None,
            )
    except MemoryPublicationConflictError:
        raise
    except PublicationCrash:
        raise
    except Exception as error:
        _rollback_failed_publication(
            prepared,
            error,
            retain_failed_stage_artifacts=retain_failed_stage_artifacts,
        )
        raise MemoryPublicationError(str(error)) from error


def finalize_publication(
    applied: AppliedPublication,
) -> MemoryPublicationReceipt:
    receipt = MemoryPublicationReceipt(
        selector=applied.prepared.selector,
        memory_hash=applied.prepared.memory_hash,
        old_revision=applied.prepared.old_revision,
        new_revision=applied.prepared.new_revision,
        diff_artifact=applied.diff_artifact,
        published_at=applied.published_at,
        no_change=applied.prepared.no_change,
    )
    _mark_complete(applied.prepared.job_path, receipt)
    _cleanup(prepared=applied.prepared)
    return receipt


def _rollback_failed_publication(
    prepared: PreparedPublication,
    error: Exception,
    *,
    retain_failed_stage_artifacts: bool,
) -> None:
    with exclusive_lock(_lock_path(prepared.stage.resolved), wait=True):
        _replace_canonical_files(
            _canonical_directory(prepared.stage.resolved),
            prepared.old_files,
        )
    if retain_failed_stage_artifacts:
        retain_failed_stage(
            job_store=prepared.job_store,
            job_id=prepared.stage.job_id,
            stage_directory=prepared.stage.directory,
            diff_bytes=prepared.diff_bytes,
        )
    _mark_failed(prepared.job_path, summary=f"Memory publication failed: {error}")
    _cleanup(prepared=prepared)


def _validate_job_store(job_store: Path) -> Path:
    candidate = Path(job_store)
    if _is_symlink_or_reparse(candidate):
        raise MemoryPublicationError("job store is unsafe")
    if candidate.name != "jobs" or candidate.parent.name != "shared":
        raise MemoryPublicationError(
            "job store must be the canonical shared/jobs directory"
        )
    resolved = candidate.resolve()
    if resolved.name != "jobs" or resolved.parent.name != "shared":
        raise MemoryPublicationError(
            "job store must be the canonical shared/jobs directory"
        )
    if not resolved.is_dir():
        raise MemoryPublicationError("job store must be an existing directory")
    return resolved


def _job_path(job_store: Path, expected_job_id: str) -> Path:
    safe_job_id = _validate_job_id(expected_job_id)
    candidate = Path(job_store) / f"{safe_job_id}.yaml"
    if _is_symlink_or_reparse(candidate):
        raise MemoryPublicationError("job path is unsafe")
    if candidate.parent.resolve() != Path(job_store).resolve():
        raise MemoryPublicationError("job path escapes job store")
    if not candidate.is_file():
        raise MemoryPublicationError("job path must be a direct file in job store")
    return candidate


def _journal_path(store_root: Path, memory_hash: str, job_id: str) -> Path:
    _validate_memory_hash(memory_hash)
    safe_job_id = _validate_job_id(job_id)
    journal_root = _ensure_infrastructure_directory(
        store_root,
        [".journals", memory_hash],
        label="journal",
    )
    return journal_root / f"{safe_job_id}.yaml"


def _backup_path(store_root: Path, memory_hash: str, job_id: str) -> Path:
    _validate_memory_hash(memory_hash)
    safe_job_id = _validate_job_id(job_id)
    backup_root = _ensure_infrastructure_directory(
        store_root,
        [".publication-backups", memory_hash],
        label="backup",
    )
    return backup_root / safe_job_id


def _write_journal(prepared: PreparedPublication, *, phase: str) -> None:
    payload = {
        "job_id": prepared.stage.job_id,
        "selector": prepared.selector,
        "memory_hash": prepared.memory_hash,
        "old_revision": prepared.old_revision,
        "new_revision": prepared.new_revision,
        "stage_directory": prepared.stage.directory.name,
        "backup_directory": prepared.backup_path.name,
        "phase": phase,
        "no_change": prepared.no_change,
    }
    atomic_write_text(
        prepared.journal_path,
        yaml.safe_dump(payload, sort_keys=False),
    )


def _write_backup(path: Path, files: Mapping[str, bytes]) -> None:
    if path.exists():
        _safe_rmtree(path, label="backup")
    path = _ensure_child_directory(path.parent, path.name, label="backup")
    for name, payload in files.items():
        atomic_write_bytes(path / name, payload)


def _mark_complete(
    job_path: Path,
    receipt: MemoryPublicationReceipt,
) -> None:
    try:
        transition_job(
            job_path,
            "running",
            "complete",
            completed_at=_now_iso(),
            memory_publication=_receipt_to_dict(receipt),
        )
    except InvalidJobTransition:
        record = read_job(job_path)
        if record.status != "complete":
            raise


def _mark_failed(job_path: Path, *, summary: str) -> None:
    try:
        transition_job(
            job_path,
            "running",
            "failed",
            completed_at=_now_iso(),
            execution_summary=summary,
        )
    except InvalidJobTransition:
        record = read_job(job_path)
        if record.status != "failed":
            raise


def _cleanup(*, prepared: PreparedPublication) -> None:
    if prepared.journal_path.exists():
        prepared.journal_path.unlink()
    if prepared.backup_path.exists():
        _safe_rmtree(prepared.backup_path, label="backup")


def _receipt_to_dict(receipt: MemoryPublicationReceipt) -> dict[str, object]:
    return {
        "selector": dict(receipt.selector),
        "memory_hash": receipt.memory_hash,
        "old_revision": receipt.old_revision,
        "new_revision": receipt.new_revision,
        "diff_artifact": (
            None
            if receipt.diff_artifact is None
            else receipt.diff_artifact.to_dict()
        ),
        "published_at": receipt.published_at,
        "no_change": receipt.no_change,
    }


def _build_diff(
    old_files: Mapping[str, bytes],
    new_files: Mapping[str, bytes],
) -> bytes:
    lines: list[str] = []
    for name in sorted(set(old_files) | set(new_files)):
        old_text = old_files.get(name, b"").decode(
            "utf-8",
            errors="surrogateescape",
        ).splitlines(keepends=True)
        new_text = new_files.get(name, b"").decode(
            "utf-8",
            errors="surrogateescape",
        ).splitlines(keepends=True)
        lines.extend(
            difflib.unified_diff(
                old_text,
                new_text,
                fromfile=f"old/{name}",
                tofile=f"new/{name}",
            )
        )
    return "".join(lines).encode("utf-8")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
