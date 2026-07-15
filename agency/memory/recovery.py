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
    MemoryStage,
    PreparedPublication,
    ResolvedMemory,
)
from .publication import _cleanup, _mark_complete, _mark_failed
from .store import (
    _canonical_directory,
    _ensure_actual_directory,
    _ensure_child_directory,
    _ensure_infrastructure_directory,
    _is_symlink_or_reparse,
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
    for journal_path in journals_root.glob("*/*.yaml"):
        payload = (
            yaml.safe_load(journal_path.read_text(encoding="utf-8"))
            or {}
        )
        try:
            prepared = _prepared_from_payload(
                payload,
                journal_path,
                store_root,
                job_store,
            )
        except Exception:
            _quarantine_journal(store_root, journal_path)
            raise
        current_revision = memory_content_revision(
            _read_canonical_files(
                _canonical_directory(prepared.stage.resolved)
            )
        )
        if current_revision == prepared.new_revision:
            receipt = MemoryPublicationReceipt(
                selector=prepared.selector,
                memory_hash=prepared.memory_hash,
                old_revision=prepared.old_revision,
                new_revision=prepared.new_revision,
                diff_artifact=None,
                published_at=_published_at(prepared.job_path),
                no_change=prepared.no_change,
            )
            _mark_complete(prepared.job_path, receipt)
            _cleanup(prepared=prepared)
            recovered += 1
        elif current_revision == prepared.old_revision:
            _mark_failed(
                prepared.job_path,
                summary=(
                    "Recovered incomplete memory publication with old "
                    "canonical revision."
                ),
            )
            _cleanup(prepared=prepared)
            recovered += 1
    return RecoveryResult(recovered=recovered)


def _prepared_from_payload(
    payload: dict[str, Any],
    journal_path: Path,
    store_root: Path,
    job_store: Path,
) -> PreparedPublication:
    if _is_symlink_or_reparse(journal_path):
        raise ValueError("journal path is unsafe")
    memory_hash = _validate_memory_hash(str(payload["memory_hash"]))
    journal_path = journal_path.resolve(strict=False)
    if journal_path.parent.name != memory_hash:
        raise ValueError("journal memory hash does not match its directory")
    job_id = _validate_job_id(str(payload["job_id"]))
    if journal_path.name != f"{job_id}.yaml":
        raise ValueError("journal filename does not match its job id")
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
        expected=job_id,
    )
    backup_name = _safe_directory_name(
        payload.get("backup_directory", job_id),
        field_name="backup_directory",
        expected=job_id,
    )
    job_path = _job_path(job_store, job_id)
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
    old_files = (
        _read_canonical_files(backup_path)
        if backup_path.exists()
        else _read_canonical_files(resolved.directory)
    )
    new_files = _read_canonical_files(stage_path)
    return PreparedPublication(
        stage=MemoryStage(
            resolved=resolved,
            job_id=job_id,
            directory=stage_path,
            base_revision=str(payload["old_revision"]),
        ),
        job_path=job_path,
        selector=selector_payload,
        memory_hash=memory_hash,
        old_revision=str(payload["old_revision"]),
        new_revision=str(payload["new_revision"]),
        old_files=old_files,
        new_files=new_files,
        diff_bytes=b"",
        journal_path=journal_path,
        backup_path=backup_path,
        no_change=bool(payload.get("no_change", False)),
    )


def _published_at(job_path: Path) -> str:
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
