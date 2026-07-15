from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

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
    _read_canonical_files,
    memory_content_revision,
)


@dataclass(frozen=True)
class RecoveryResult:
    recovered: int = 0


def recover_publications(store_root: Path, job_store: Path) -> RecoveryResult:
    journals_root = Path(store_root) / ".journals"
    if not journals_root.exists():
        return RecoveryResult()

    recovered = 0
    for journal_path in journals_root.glob("*/*.yaml"):
        payload = (
            yaml.safe_load(journal_path.read_text(encoding="utf-8")) or {}
        )
        prepared = _prepared_from_payload(payload, journal_path, store_root)
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
    payload: dict[str, object],
    journal_path: Path,
    store_root: Path,
) -> PreparedPublication:
    selector_payload = dict(payload["selector"])
    selector = MemorySelector(**selector_payload)
    memory_hash = str(payload["memory_hash"])
    stage_path = Path(str(payload["stage_path"]))
    backup_path = Path(str(payload["backup_path"]))
    job_path = Path(str(payload["job_path"]))
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
            job_id=job_path.stem,
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
