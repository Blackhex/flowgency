from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from agency.configuration.models import MemorySelector
from agency.jobs.store import read_job

from .models import (
    MemoryPublicationReceipt,
    ResolvedMemory,
)
from .publication import _cleanup_paths, _mark_complete, _mark_failed
from .store import (
    _canonical_directory,
    _ensure_actual_directory,
    _ensure_child_directory,
    _ensure_infrastructure_directory,
    _is_symlink_or_reparse,
    _memory_lock,
    _read_canonical_files,
    _validate_job_id,
    _validate_memory_hash,
    memory_content_revision,
)


@dataclass(frozen=True)
class RecoveryResult:
    recovered: int = 0


def recover_publications(store_root: Path, job_store: Path) -> RecoveryResult:
    store_root = _ensure_actual_directory(
        Path(store_root),
        label="memory",
        create=True,
    )
    job_store = _ensure_actual_directory(
        Path(job_store),
        label="job",
        create=True,
    )
    journals_root = store_root / ".journals"
    if not journals_root.exists():
        return RecoveryResult()
    journals_root = _ensure_actual_directory(journals_root, label="journal")

    recovered = 0
    for journal_path in sorted(
        journals_root.glob("*/*.yaml"),
        key=lambda path: (path.parent.name, path.name),
    ):
        payload = (
            yaml.safe_load(journal_path.read_text(encoding="utf-8"))
            or {}
        )
        try:
            operation = _operation_from_payload(
                payload,
                journal_path,
                store_root,
                job_store,
            )
        except Exception:
            _quarantine_journal(store_root, journal_path)
            raise
        with _memory_lock(operation.resolved, wait=True):
            recovered += _recover_locked(operation)
    return RecoveryResult(recovered=recovered)


@dataclass(frozen=True)
class _RecoveryOperation:
    kind: str
    operation_id: str
    phase: str
    no_change: bool
    selector: dict[str, object]
    resolved: ResolvedMemory
    memory_hash: str
    old_revision: str
    new_revision: str
    stage_path: Path
    backup_path: Path
    journal_path: Path
    job_path: Path | None


def _operation_from_payload(
    payload: dict[str, Any],
    journal_path: Path,
    store_root: Path,
    job_store: Path,
) -> _RecoveryOperation:
    if _is_symlink_or_reparse(journal_path):
        raise ValueError("journal path is unsafe")
    kind = str(payload.get("kind") or "job")
    if kind not in {"job", "direct-save"}:
        raise ValueError("journal kind is invalid")
    memory_hash = _validate_memory_hash(str(payload["memory_hash"]))
    journal_path = journal_path.resolve(strict=False)
    if journal_path.parent.name != memory_hash:
        raise ValueError("journal memory hash does not match its directory")
    operation_id = _validate_job_id(
        str(payload.get("operation_id") or payload.get("job_id"))
    )
    if kind == "job":
        journal_job_id = _validate_job_id(str(payload["job_id"]))
        if journal_job_id != operation_id:
            raise ValueError(
                "journal job id does not match its operation id"
            )
    if journal_path.name != f"{operation_id}.yaml":
        raise ValueError("journal filename does not match its operation id")
    selector_payload = dict(payload["selector"])
    selector = MemorySelector(**selector_payload)
    for superseded_field in ("job_path", "stage_path", "backup_path"):
        if superseded_field in payload:
            raise ValueError(
                "journal contains unsupported superseded field: "
                f"{superseded_field}"
            )
    stage_name = _safe_directory_name(
        payload.get("stage_directory"),
        field_name="stage_directory",
        expected=operation_id,
    )
    backup_name = _safe_directory_name(
        payload.get("backup_directory", operation_id),
        field_name="backup_directory",
        expected=operation_id,
    )
    job_path = _job_path(job_store, operation_id) if kind == "job" else None
    stage_path = _stage_path(store_root, memory_hash, stage_name)
    backup_path = _backup_path(
        store_root,
        memory_hash,
        backup_name,
        required=payload.get("phase") in {"backed_up", "published"},
    )
    resolved = ResolvedMemory(
        selector=selector,
        canonical_json="",
        memory_hash=memory_hash,
        directory=Path(store_root) / memory_hash,
    )
    return _RecoveryOperation(
        kind=kind,
        operation_id=operation_id,
        phase=str(payload.get("phase") or ""),
        no_change=bool(payload.get("no_change", False)),
        selector=selector_payload,
        resolved=resolved,
        memory_hash=memory_hash,
        old_revision=str(payload["old_revision"]),
        new_revision=str(payload["new_revision"]),
        stage_path=stage_path,
        backup_path=backup_path,
        journal_path=journal_path,
        job_path=job_path,
    )


def _recover_locked(operation: _RecoveryOperation) -> int:
    phase = operation.phase
    current_revision = memory_content_revision(
        _read_canonical_files(_canonical_directory(operation.resolved))
    )
    stage_revision = memory_content_revision(
        _read_canonical_files(operation.stage_path)
    )

    if stage_revision != operation.new_revision:
        _quarantine_journal(
            operation.resolved.directory.parent,
            operation.journal_path,
        )
        return 0

    if (
        phase in {"backed_up", "published"}
        and current_revision == operation.new_revision
    ):
        _finalize_recovered_success(operation)
        return 1

    if (
        phase == "prepared"
        and operation.no_change
        and current_revision
        == operation.old_revision
        == operation.new_revision
    ):
        if operation.kind == "job":
            _finalize_recovered_success(operation)
        else:
            _cleanup_operation(operation)
        return 1

    if current_revision == operation.old_revision:
        if operation.kind == "job":
            _mark_failed(
                operation.job_path,
                summary=(
                    "Recovered incomplete memory publication with old "
                    "canonical revision."
                ),
            )
        _cleanup_operation(operation)
        return 1

    _quarantine_journal(
        operation.resolved.directory.parent,
        operation.journal_path,
    )
    return 0


def _finalize_recovered_success(operation: _RecoveryOperation) -> None:
    if operation.kind == "job":
        receipt = MemoryPublicationReceipt(
            selector=operation.selector,
            memory_hash=operation.memory_hash,
            old_revision=operation.old_revision,
            new_revision=operation.new_revision,
            diff_artifact=None,
            published_at=_published_at(operation.job_path),
            no_change=operation.no_change,
        )
        _mark_complete(operation.job_path, receipt)
    _cleanup_operation(operation)


def _cleanup_operation(operation: _RecoveryOperation) -> None:
    _cleanup_paths(
        journal_path=operation.journal_path,
        backup_path=operation.backup_path,
        stage_directory=operation.stage_path,
    )


def _published_at(job_path: Path | None) -> str:
    if job_path is None:
        return ""
    record = read_job(job_path)
    return record.completed_at or record.started_at or ""


def _safe_directory_name(
    value: object,
    *,
    field_name: str,
    expected: str,
) -> str:
    if not isinstance(value, str):
        raise ValueError(f"journal {field_name} must be a string")
    safe = _validate_job_id(value)
    if safe != expected:
        raise ValueError(f"journal {field_name} does not match job id")
    return safe


def _stage_path(store_root: Path, memory_hash: str, job_id: str) -> Path:
    stage_root = _ensure_infrastructure_directory(
        store_root,
        [".staging", memory_hash],
        label="staging",
    )
    path = stage_root / job_id
    return _ensure_actual_directory(path, label="staging")


def _backup_path(
    store_root: Path,
    memory_hash: str,
    job_id: str,
    *,
    required: bool,
) -> Path:
    backup_root = _ensure_infrastructure_directory(
        store_root,
        [".publication-backups", memory_hash],
        label="backup",
    )
    path = backup_root / job_id
    if required:
        return _ensure_actual_directory(path, label="backup")
    if path.exists():
        return _ensure_actual_directory(path, label="backup")
    return path


def _job_path(job_store: Path, job_id: str) -> Path:
    job_path = job_store / f"{job_id}.yaml"
    if _is_symlink_or_reparse(job_path):
        raise ValueError("job path is unsafe")
    parent = job_path.parent.resolve()
    if parent != job_store.resolve():
        raise ValueError("job path escapes job store")
    if not job_path.is_file():
        raise ValueError("job path must be a direct file in job store")
    return job_path


def _quarantine_journal(store_root: Path, journal_path: Path) -> None:
    quarantine_root = _ensure_infrastructure_directory(
        store_root,
        [".journals", "_quarantine"],
        label="journal",
    )
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    target_dir = _ensure_child_directory(
        quarantine_root,
        timestamp,
        label="journal",
    )
    target = target_dir / journal_path.name
    journal_path.replace(target)
