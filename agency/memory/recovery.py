from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import re
from pathlib import Path
from typing import Any

import yaml

from agency.configuration.models import MemorySelector
from agency.jobs.store import read_job
from agency.memory.selectors import resolve_memory_selector

from .models import (
    MemoryPublicationReceipt,
    ResolvedMemory,
)
from .publication import _cleanup_paths, _mark_complete, _mark_failed
from .store import (
    _canonical_directory,
    _ensure_actual_directory,
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
    blocked_job_ids: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()


def recover_publications(
    store_root: Path,
    job_stores: Mapping[str, Path],
) -> RecoveryResult:
    store_root = _ensure_actual_directory(
        Path(store_root),
        label="memory",
        create=True,
    )
    allowed_job_stores = _validate_job_stores(job_stores)
    journals_root = store_root / ".journals"
    if not journals_root.exists():
        return RecoveryResult()
    journals_root = _ensure_actual_directory(journals_root, label="journal")

    recovered = 0
    blocked_job_ids: set[str] = set()
    errors: list[str] = []
    for journal_path in sorted(
        journals_root.glob("*/*.yaml"),
        key=lambda path: (path.parent.name, path.name),
    ):
        try:
            identity = _read_identity(journal_path, store_root)
            resolved = ResolvedMemory(
                selector=MemorySelector(scope="run"),
                canonical_json="",
                memory_hash=identity.memory_hash,
                directory=store_root / identity.memory_hash,
            )
            with _memory_lock(resolved, wait=True):
                try:
                    payload = _read_payload(journal_path)
                except FileNotFoundError:
                    continue
                operation = _operation_from_payload(
                    payload,
                    journal_path,
                    store_root,
                    allowed_job_stores,
                )
                recovered += _recover_locked(operation)
        except Exception as error:
            blocked_job_ids.update(
                _barrier_job_ids(journal_path, allowed_job_stores)
            )
            errors.append(f"{journal_path}: {error}")
            continue
    return RecoveryResult(
        recovered=recovered,
        blocked_job_ids=tuple(sorted(blocked_job_ids)),
        errors=tuple(errors),
    )


@dataclass(frozen=True)
class _JournalIdentity:
    memory_hash: str
    operation_id: str


def _read_identity(journal_path: Path, store_root: Path) -> _JournalIdentity:
    if _is_symlink_or_reparse(journal_path):
        raise ValueError("journal path is unsafe")
    canonical = journal_path.resolve(strict=False)
    if canonical.parent.parent != (store_root / ".journals").resolve():
        raise ValueError("journal path escapes journal store")
    memory_hash = _validate_memory_hash(canonical.parent.name)
    operation_id = _operation_id_from_path(canonical)
    if operation_id is None:
        raise ValueError("journal filename operation id is invalid")
    return _JournalIdentity(memory_hash, operation_id)


def _read_payload(journal_path: Path) -> dict[str, Any]:
    loaded = yaml.safe_load(journal_path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError("journal must decode to a mapping")
    return loaded


def _operation_id_from_path(journal_path: Path) -> str | None:
    if journal_path.suffix != ".yaml":
        return None
    try:
        return _validate_job_id(journal_path.stem)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class _JobStoreOwner:
    group_id: str
    path: Path
    group_root: Path | None


def _validate_job_stores(
    job_stores: Mapping[str, Path],
) -> tuple[_JobStoreOwner, ...]:
    if not isinstance(job_stores, Mapping):
        raise TypeError("job stores must map configured group ids to paths")
    validated: list[_JobStoreOwner] = []
    seen: set[Path] = set()
    for group_id, value in job_stores.items():
        if not isinstance(group_id, str) or not group_id:
            raise ValueError("job store owner must be a configured group id")
        configured_group_root: Path | None = None
        if isinstance(value, Mapping):
            candidate = Path(value["job_store"]).expanduser()
            configured_group_root = Path(value["group_root"]).expanduser().resolve(strict=False)
        else:
            candidate = Path(value).expanduser()
        if candidate.parent.name != ".jobs":
            raise ValueError(
                "job store must be a direct .jobs/<group> directory"
            )
        components = (candidate.parent, candidate)
        for component in components:
            if component.exists() and _is_symlink_or_reparse(component):
                raise ValueError("job store is unsafe")
        canonical = candidate.resolve(strict=False)
        if canonical.parent.name != ".jobs":
            raise ValueError("job store must resolve to .jobs/<group>")
        if canonical in seen:
            raise ValueError("job stores must be canonical and distinct")
        if canonical.exists():
            _ensure_actual_directory(canonical, label="job")
        seen.add(canonical)
        validated.append(
            _JobStoreOwner(
                group_id=group_id,
                path=canonical,
                group_root=configured_group_root,
            )
        )
    return tuple(
        sorted(validated, key=lambda owner: (str(owner.path), owner.group_id))
    )


def _matching_job_paths(
    job_stores: tuple[_JobStoreOwner, ...],
    operation_id: str,
) -> tuple[tuple[_JobStoreOwner, Path], ...]:
    matches: list[tuple[_JobStoreOwner, Path]] = []
    for owner in job_stores:
        candidate = owner.path / f"{operation_id}.yaml"
        if _is_symlink_or_reparse(candidate):
            raise ValueError("job path is unsafe")
        if candidate.is_file():
            matches.append((owner, candidate))
    return tuple(matches)


def _barrier_job_ids(
    journal_path: Path,
    job_stores: tuple[_JobStoreOwner, ...],
) -> tuple[str, ...]:
    candidates = {_operation_id_from_path(journal_path)}
    try:
        payload = _read_payload(journal_path)
    except Exception:
        payload = {}
    for field_name in ("operation_id", "job_id"):
        try:
            candidates.add(_validate_job_id(payload.get(field_name)))
        except (TypeError, ValueError):
            pass
    blocked = set()
    for operation_id in candidates - {None}:
        try:
            if _matching_job_paths(job_stores, operation_id):
                blocked.add(operation_id)
        except ValueError:
            blocked.add(operation_id)
    return tuple(sorted(blocked))


@dataclass(frozen=True)
class _RecoveryOperation:
    kind: str
    operation_id: str
    phase: str
    no_change: bool
    selector: dict[str, object]
    canonical_json: str
    resolved: ResolvedMemory
    memory_hash: str
    old_revision: str
    new_revision: str
    stage_path: Path
    backup_path: Path
    journal_path: Path
    job_path: Path | None
    owner_group_id: str | None
    owner_group_root: Path | None


def _operation_from_payload(
    payload: dict[str, Any],
    journal_path: Path,
    store_root: Path,
    job_stores: tuple[_JobStoreOwner, ...],
) -> _RecoveryOperation:
    kind = payload.get("kind")
    if "kind" not in payload:
        raise ValueError("journal kind is required")
    required = {
        "kind",
        "operation_id",
        "selector",
        "canonical_json",
        "memory_hash",
        "old_revision",
        "new_revision",
        "stage_directory",
        "backup_directory",
        "phase",
        "no_change",
    }
    if kind == "job":
        required.add("job_id")
    if set(payload) != required:
        raise ValueError("journal keys do not match the closed schema")
    if kind not in {"job", "direct-save"}:
        raise ValueError("journal kind is invalid")
    memory_hash = _validate_memory_hash(str(payload["memory_hash"]))
    journal_path = journal_path.resolve(strict=False)
    if journal_path.parent.name != memory_hash:
        raise ValueError("journal memory hash does not match its directory")
    operation_id = _validate_job_id(str(payload["operation_id"]))
    if kind == "job":
        journal_job_id = _validate_job_id(str(payload["job_id"]))
        if journal_job_id != operation_id:
            raise ValueError(
                "journal job id does not match its operation id"
            )
    if journal_path.name != f"{operation_id}.yaml":
        raise ValueError("journal filename does not match its operation id")
    if not isinstance(payload["selector"], Mapping):
        raise ValueError("journal selector must be a mapping")
    selector_payload = dict(payload["selector"])
    selector = MemorySelector(**selector_payload)
    canonical_json = payload["canonical_json"]
    if not isinstance(canonical_json, str) or not canonical_json:
        raise ValueError("journal canonical_json must be a non-empty string")
    phase = payload["phase"]
    if phase not in {"prepared", "backed_up", "published"}:
        raise ValueError("journal phase is invalid")
    no_change = payload["no_change"]
    if type(no_change) is not bool:
        raise ValueError("journal no_change must be a literal boolean")
    for field_name in ("old_revision", "new_revision"):
        if not isinstance(payload[field_name], str) or not re.fullmatch(
            r"[0-9a-f]{64}", payload[field_name]
        ):
            raise ValueError(f"journal {field_name} revision is invalid")
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
    job_path = None
    owner_group_id = None
    owner_group_root = None
    if kind == "job":
        matches = _matching_job_paths(job_stores, operation_id)
        if len(matches) != 1:
            raise ValueError(
                "job journal must resolve to exactly one allowed job record"
            )
        owner, job_path = matches[0]
        owner_group_id = owner.group_id
        owner_group_root = owner.group_root
    stage_path = _stage_path(store_root, memory_hash, stage_name)
    backup_path = _backup_path(
        store_root,
        memory_hash,
        backup_name,
        required=(
            not no_change and phase in {"backed_up", "published"}
        ),
    )
    resolved = ResolvedMemory(
        selector=selector,
        canonical_json=canonical_json,
        memory_hash=memory_hash,
        directory=Path(store_root) / memory_hash,
    )
    operation = _RecoveryOperation(
        kind=kind,
        operation_id=operation_id,
        phase=phase,
        no_change=no_change,
        selector=selector_payload,
        canonical_json=canonical_json,
        resolved=resolved,
        memory_hash=memory_hash,
        old_revision=str(payload["old_revision"]),
        new_revision=str(payload["new_revision"]),
        stage_path=stage_path,
        backup_path=backup_path,
        journal_path=journal_path,
        job_path=job_path,
        owner_group_id=owner_group_id,
        owner_group_root=owner_group_root,
    )
    if kind == "job":
        _validate_job_ownership(operation)
    else:
        _validate_direct_identity(operation)
    return operation


def _validate_job_ownership(
    operation: _RecoveryOperation,
) -> None:
    record = read_job(operation.job_path)
    spec = record.spec
    if spec.job_id != operation.operation_id:
        raise ValueError("job spec id does not own journal operation")
    if spec.group_key != operation.owner_group_id:
        raise ValueError("job does not belong to its configured group owner")
    if operation.owner_group_root is None:
        raise ValueError("configured group root is required for job recovery")
    trusted_group_root = operation.owner_group_root.resolve()
    if Path(spec.group_root).resolve() != trusted_group_root:
        raise ValueError("job spec group root does not match configured group")
    if (
        Path(spec.memory.path).resolve().parent
        != operation.resolved.directory.parent
    ):
        raise ValueError("job spec memory path is outside global store")
    selector = MemorySelector(**spec.memory.selector)
    channels = (
        {selector.channel: object()}
        if selector.scope == "channel" and selector.channel is not None
        else {}
    )
    recomputed = resolve_memory_selector(
        selector,
        job_id=spec.job_id,
        group_key=spec.group_key,
        agent_name=spec.agent_name,
        routine_id=spec.routine_id,
        channels=channels,
        store_root=operation.resolved.directory.parent,
    )
    journal_selector = operation.resolved.selector.model_dump(
        exclude_none=True
    )
    spec_selector = selector.model_dump(exclude_none=True)
    if journal_selector != spec_selector:
        raise ValueError("journal selector does not match job spec")
    if spec.memory.canonical_json != recomputed.canonical_json:
        raise ValueError("job spec canonical JSON is invalid")
    if operation.canonical_json != recomputed.canonical_json:
        raise ValueError("journal canonical JSON does not match job spec")
    if spec.memory.memory_hash != recomputed.memory_hash:
        raise ValueError("job spec memory hash is invalid")
    if operation.memory_hash != recomputed.memory_hash:
        raise ValueError("journal memory hash does not match job spec")
    if Path(spec.memory.path).resolve() != recomputed.directory.resolve():
        raise ValueError("job spec memory path does not match selector")


def _validate_direct_identity(operation: _RecoveryOperation) -> None:
    from .selectors import resolved_memory_from_canonical

    recomputed = resolved_memory_from_canonical(
        operation.resolved.selector,
        operation.canonical_json,
        store_root=operation.resolved.directory.parent,
    )
    if recomputed.memory_hash != operation.memory_hash:
        raise ValueError("direct-save canonical identity hash mismatch")


def _recover_locked(operation: _RecoveryOperation) -> int:
    phase = operation.phase
    current_revision = memory_content_revision(
        _read_canonical_files(_canonical_directory(operation.resolved))
    )
    stage_revision = memory_content_revision(
        _read_canonical_files(operation.stage_path)
    )

    if stage_revision != operation.new_revision:
        raise ValueError("stage revision does not match journal new revision")

    backup_revision = None
    if operation.backup_path.exists():
        backup_revision = memory_content_revision(
            _read_canonical_files(operation.backup_path)
        )
    if (
        backup_revision is not None
        and backup_revision != operation.old_revision
    ):
        raise ValueError("backup revision does not match journal old revision")
    if (
        not operation.no_change
        and operation.phase in {"backed_up", "published"}
        and backup_revision is None
    ):
        raise ValueError("journal phase requires an old-revision backup")
    if operation.no_change and backup_revision is not None:
        raise ValueError("no_change journal must not have a backup")
    if (
        operation.no_change
        and operation.old_revision != operation.new_revision
    ):
        raise ValueError("no_change journal revisions differ")
    if (
        not operation.no_change
        and operation.old_revision == operation.new_revision
    ):
        raise ValueError("changed journal revisions must differ")

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

    raise ValueError(
        "canonical revision is unknown; manual intervention required"
    )


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
    try:
        safe = _validate_job_id(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"journal {field_name} is unsafe") from exc
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
