from __future__ import annotations

from collections.abc import Mapping
from collections.abc import Callable, Iterator
from contextlib import contextmanager
import hashlib
import os
import re
import shutil
import stat
import tempfile
import unicodedata
from pathlib import Path

from agency.fs.atomic import atomic_write_bytes
from agency.fs.locks import exclusive_lock, try_exclusive_lock

from .models import (
    MemoryConflictError,
    MemorySnapshot,
    MemoryStage,
    MemoryStoreError,
    ResolvedMemory,
)


_REVISION_DOMAIN = b"agency-memory-content:v1\0"
_LOWER_HEX_64_RE = re.compile(r"^[0-9a-f]{64}$")
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}
_LOCK_LEASE_MARKER = object()


class _MemoryLockLease:
    __slots__ = ("resolved", "lock_path", "_marker")

    def __init__(
        self,
        resolved: ResolvedMemory,
        lock_path: Path,
        marker: object,
    ) -> None:
        if marker is not _LOCK_LEASE_MARKER:
            raise MemoryStoreError("memory lock leases are internal")
        self.resolved = resolved
        self.lock_path = lock_path
        self._marker = marker


@contextmanager
def _memory_lock(
    resolved: ResolvedMemory,
    *,
    wait: bool,
    cancelled: Callable[[], bool] | None = None,
) -> Iterator[_MemoryLockLease]:
    _validate_resolved_memory(resolved)
    lock_path = _lock_path(resolved)
    with exclusive_lock(
        lock_path,
        wait=wait,
        cancelled=cancelled,
    ):
        yield _MemoryLockLease(resolved, lock_path, _LOCK_LEASE_MARKER)


def _validate_lock_lease(
    lease: _MemoryLockLease,
    resolved: ResolvedMemory,
) -> None:
    if not isinstance(lease, _MemoryLockLease):
        raise MemoryStoreError("canonical memory lock lease required")
    if lease._marker is not _LOCK_LEASE_MARKER:
        raise MemoryStoreError("invalid canonical memory lock lease")
    if lease.resolved.memory_hash != resolved.memory_hash:
        raise MemoryStoreError("memory lock lease identity mismatch")
    if lease.lock_path != _lock_path(resolved):
        raise MemoryStoreError("memory lock lease path mismatch")


def _normalized_key(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def memory_content_revision(files: Mapping[str, bytes]) -> str:
    digest = hashlib.sha256()
    digest.update(_REVISION_DOMAIN)
    for name in sorted(files):
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(files[name])
        digest.update(b"\0")
    return digest.hexdigest()


def read_memory(
    resolved: ResolvedMemory,
    *,
    wait: bool = True,
) -> MemorySnapshot:
    _validate_resolved_memory(resolved)
    with _memory_lock(resolved, wait=wait) as lease:
        return _read_memory_locked(resolved, lease)


def _read_memory_locked(
    resolved: ResolvedMemory,
    lease: _MemoryLockLease,
) -> MemorySnapshot:
    _validate_lock_lease(lease, resolved)
    files = _read_canonical_files(_canonical_directory(resolved))
    return MemorySnapshot(
        resolved=resolved,
        files=files,
        revision=memory_content_revision(files),
    )


def ensure_memory(resolved: ResolvedMemory) -> MemorySnapshot:
    _validate_resolved_memory(resolved)
    with _memory_lock(resolved, wait=True) as lease:
        return _ensure_memory_locked(resolved, lease)


def _ensure_memory_locked(
    resolved: ResolvedMemory,
    lease: _MemoryLockLease,
) -> MemorySnapshot:
    _validate_lock_lease(lease, resolved)
    directory = _ensure_canonical_directory(resolved)
    if not any(directory.iterdir()):
        atomic_write_bytes(directory / "memory.md", b"")
    files = _read_canonical_files(directory)
    if not files:
        raise ValueError("memory must contain at least one markdown file")
    return MemorySnapshot(
        resolved=resolved,
        files=files,
        revision=memory_content_revision(files),
    )


def stage_memory(resolved: ResolvedMemory, *, job_id: str) -> MemoryStage:
    with _memory_lock(resolved, wait=True) as lease:
        return _stage_memory_locked(resolved, job_id=job_id, lease=lease)


def _stage_memory_locked(
    resolved: ResolvedMemory,
    *,
    job_id: str,
    lease: _MemoryLockLease,
) -> MemoryStage:
    _validate_lock_lease(lease, resolved)
    snapshot = _ensure_memory_locked(resolved, lease)
    safe_job_id = _validate_job_id(job_id)
    stage_parent = _ensure_infrastructure_directory(
        _store_root(resolved),
        [".staging", resolved.memory_hash],
        label="staging",
    )
    stage_root = stage_parent / safe_job_id
    if stage_root.exists():
        _ensure_actual_directory(stage_root, label="staging")
        _safe_rmtree(stage_root, label="staging")
    _ensure_child_directory(stage_parent, safe_job_id, label="staging")
    for name, payload in snapshot.files.items():
        atomic_write_bytes(stage_root / name, payload)
    return MemoryStage(
        resolved=resolved,
        job_id=job_id,
        directory=stage_root,
        base_revision=snapshot.revision,
    )


def try_save_memory(
    resolved: ResolvedMemory,
    expected_revision: str,
    files: Mapping[str, bytes],
) -> MemorySnapshot:
    _validate_resolved_memory(resolved)
    normalized = _normalize_candidate_files(files)
    with _memory_lock(resolved, wait=False) as lease:
        current_files = _read_canonical_files(resolved.directory)
        current = MemorySnapshot(
            resolved=resolved,
            files=current_files,
            revision=memory_content_revision(current_files),
        )
        if current.revision != expected_revision:
            raise MemoryConflictError(
                expected_revision=expected_revision,
                current=current,
                attempted_files=normalized,
            )
        from .publication import _save_direct_locked

        return _save_direct_locked(
            resolved,
            current,
            normalized,
            lease=lease,
        )


class MemoryStore:
    def __init__(self, root: Path):
        self.root = Path(root).expanduser().resolve()

    def _lock_path(self, resolved: ResolvedMemory) -> Path:
        _validate_resolved_memory(resolved, expected_root=self.root)
        return self.root / ".locks" / f"{resolved.memory_hash}.lock"

    def read(
        self,
        resolved: ResolvedMemory,
        *,
        wait: bool = True,
    ) -> MemorySnapshot:
        _validate_resolved_memory(resolved, expected_root=self.root)
        return read_memory(resolved, wait=wait)

    def ensure(self, resolved: ResolvedMemory) -> MemorySnapshot:
        _validate_resolved_memory(resolved, expected_root=self.root)
        return ensure_memory(resolved)

    def stage(self, resolved: ResolvedMemory, job_id: str) -> MemoryStage:
        _validate_resolved_memory(resolved, expected_root=self.root)
        return stage_memory(resolved, job_id=job_id)

    def try_save(
        self,
        resolved: ResolvedMemory,
        expected_revision: str,
        files: Mapping[str, bytes],
    ) -> MemorySnapshot:
        _validate_resolved_memory(resolved, expected_root=self.root)
        return try_save_memory(resolved, expected_revision, files)


def _store_root(resolved: ResolvedMemory) -> Path:
    return resolved.directory.parent


def _lock_path(resolved: ResolvedMemory) -> Path:
    root = _ensure_infrastructure_directory(
        _store_root(resolved),
        [".locks"],
        label="locks",
    )
    lock_path = root / f"{resolved.memory_hash}.lock"
    _ensure_safe_leaf(lock_path, label="locks")
    return lock_path


def _validate_resolved_memory(
    resolved: ResolvedMemory,
    *,
    expected_root: Path | None = None,
) -> None:
    _validate_memory_hash(resolved.memory_hash)
    root = (
        expected_root.resolve()
        if expected_root is not None
        else _store_root(resolved)
    )
    directory = resolved.directory
    if directory.parent != root:
        raise MemoryStoreError(
            "memory directory must stay under the configured memory root"
        )
    if directory.name != resolved.memory_hash:
        raise MemoryStoreError(
            "memory directory name must match the resolved hash"
        )


def _validate_memory_hash(memory_hash: str) -> str:
    if not isinstance(memory_hash, str):
        raise TypeError("memory hash must be a string")
    if not _LOWER_HEX_64_RE.fullmatch(memory_hash):
        raise ValueError("memory hash must be exactly 64 lowercase hex characters")
    return memory_hash


def _read_canonical_files(directory: Path) -> dict[str, bytes]:
    directory = Path(directory)
    if not directory.exists():
        return {}
    _ensure_actual_directory(directory, label="memory")

    files: dict[str, bytes] = {}
    seen_casefold: dict[str, str] = {}
    for entry in sorted(
        directory.iterdir(),
        key=lambda item: item.name.casefold(),
    ):
        if _is_symlink_or_reparse(entry):
            raise ValueError(
                f"memory contains symlink or reparse point: {entry.name}"
            )
        if not entry.is_file():
            raise ValueError(
                f"memory contains nested or non-file entry: {entry.name}"
            )
        _validate_filename(entry.name, seen_casefold)
        if entry.suffix != ".md":
            raise ValueError(
                f"memory contains non-markdown file: {entry.name}"
            )
        files[entry.name] = entry.read_bytes()
    return files


def _normalize_candidate_files(files: Mapping[str, bytes]) -> dict[str, bytes]:
    normalized = dict(files)
    if not normalized:
        raise ValueError("memory must contain at least one markdown file")
    seen_casefold: dict[str, str] = {}
    for name, payload in normalized.items():
        _validate_filename(name, seen_casefold)
        if Path(name).suffix != ".md":
            raise ValueError(f"memory contains non-markdown file: {name}")
        if not isinstance(payload, bytes):
            raise TypeError(f"memory payload for {name} must be bytes")
    return normalized


def _validate_filename(name: str, seen_casefold: dict[str, str]) -> None:
    candidate = Path(name)
    if candidate.name != name:
        raise ValueError(f"memory filename must be direct: {name}")
    if name in {"", ".", ".."}:
        raise ValueError("memory filename must not be empty")
    if name.endswith((" ", ".")):
        raise ValueError(f"memory filename has trailing ambiguity: {name}")
    if name.startswith("."):
        raise ValueError(
            f"memory filename must not be hidden infrastructure: {name}"
        )
    if "/" in name or "\\" in name:
        raise ValueError(f"memory filename must be direct: {name}")
    if candidate.suffix != ".md":
        raise ValueError(f"memory contains non-markdown file: {name}")
    stem = candidate.stem
    if stem.upper() in _WINDOWS_RESERVED_NAMES:
        raise ValueError(f"memory filename uses reserved name: {name}")
    folded = _normalized_key(name)
    previous = seen_casefold.get(folded)
    if previous is not None and previous != name:
        raise ValueError(
            "memory filenames must not case-fold collide: "
            f"{previous}, {name}"
        )
    seen_casefold[folded] = name


def _replace_canonical_files(
    directory: Path,
    files: Mapping[str, bytes],
) -> None:
    directory = _ensure_actual_directory(directory, label="memory")
    parent = directory.parent
    staging_parent = _ensure_infrastructure_directory(
        parent,
        [".backups"],
        label="backup",
    )
    temp_directory = _create_verified_tempdir(
        staging_parent,
        prefix=f"{directory.name}.",
        label="backup",
    )
    backup_directory = _create_verified_tempdir(
        staging_parent,
        prefix=f"{directory.name}.",
        label="backup",
    )
    moved_new_files: list[Path] = []
    try:
        for name, payload in files.items():
            atomic_write_bytes(temp_directory / name, payload)
        if directory.exists():
            for entry in directory.iterdir():
                _evacuate_path(entry, backup_directory / entry.name)
        for entry in temp_directory.iterdir():
            target = directory / entry.name
            moved_new_files.append(target)
            _install_path(entry, target)
        _safe_rmtree(backup_directory, label="backup")
    except Exception as exc:
        rollback_error = _rollback_canonical_replace(
            directory,
            backup_directory,
            moved_new_files,
        )
        if rollback_error is not None:
            raise RuntimeError(
                "memory replacement failed and recovery failed: "
                f"{exc}; rollback error: {rollback_error}; "
                f"backup preserved at {backup_directory}"
            ) from exc
        raise RuntimeError(
            f"memory replacement failed and rolled back: {exc}"
        ) from exc
    finally:
        if temp_directory.exists():
            _safe_rmtree(temp_directory, label="backup", ignore_missing=True)


def _rollback_canonical_replace(
    directory: Path,
    backup_directory: Path,
    moved_new_files: list[Path],
) -> Exception | None:
    try:
        for path in moved_new_files:
            try:
                if path.exists():
                    path.unlink()
            except FileNotFoundError:
                continue
        if backup_directory.exists():
            for entry in backup_directory.iterdir():
                _restore_path(entry, directory / entry.name)
            _safe_rmtree(backup_directory, label="backup")
        return None
    except Exception as rollback_error:
        return rollback_error


def _move_path(source: Path, target: Path) -> Path:
    moved = shutil.move(str(source), target)
    return Path(moved)


def _evacuate_path(source: Path, target: Path) -> Path:
    return _move_path(source, target)


def _install_path(source: Path, target: Path) -> Path:
    return _move_path(source, target)


def _restore_path(source: Path, target: Path) -> Path:
    return _move_path(source, target)


def _validate_job_id(job_id: str) -> str:
    if not isinstance(job_id, str):
        raise TypeError("job id must be a string")
    if job_id in {"", ".", ".."}:
        raise ValueError("job id must be one safe filename segment")
    if job_id.endswith((" ", ".")):
        raise ValueError("job id must not have trailing dot or space")
    if "/" in job_id or "\\" in job_id:
        raise ValueError("job id must be one safe filename segment")
    path = Path(job_id)
    if path.name != job_id:
        raise ValueError("job id must be one safe filename segment")
    if path.is_absolute() or path.anchor:
        raise ValueError("job id must be one safe filename segment")
    normalized = unicodedata.normalize("NFKC", job_id)
    if normalized != job_id:
        raise ValueError(
            "job id must not be ambiguous under Unicode normalization"
        )
    if _normalized_key(job_id) != job_id:
        raise ValueError(
            "job id must not be ambiguous under case normalization"
        )
    if path.stem.upper() in _WINDOWS_RESERVED_NAMES:
        raise ValueError("job id uses a reserved Windows basename")
    return job_id


def _canonical_directory(resolved: ResolvedMemory) -> Path:
    return _ensure_infrastructure_directory(
        _store_root(resolved),
        [resolved.memory_hash],
        label="memory",
    )


def _ensure_canonical_directory(resolved: ResolvedMemory) -> Path:
    return _canonical_directory(resolved)


def _ensure_infrastructure_directory(
    root: Path,
    components: list[str],
    *,
    label: str,
) -> Path:
    current = _ensure_actual_directory(Path(root), label=label, create=True)
    for component in components:
        current = _ensure_child_directory(current, component, label=label)
    return current


def _ensure_child_directory(parent: Path, name: str, *, label: str) -> Path:
    child = parent / name
    try:
        child.lstat()
    except FileNotFoundError:
        child.mkdir()
    return _ensure_actual_directory(child, label=label)


def _ensure_actual_directory(
    path: Path,
    *,
    label: str,
    create: bool = False,
) -> Path:
    if create:
        path.mkdir(parents=True, exist_ok=True)
    try:
        stat_result = path.lstat()
    except FileNotFoundError as exc:
        raise MemoryStoreError(
            f"missing {label} directory: {path}"
        ) from exc
    if _stat_is_symlink_or_reparse(stat_result):
        raise MemoryStoreError(f"unsafe {label} directory: {path}")
    if not stat.S_ISDIR(stat_result.st_mode):
        raise MemoryStoreError(f"{label} path is not a directory: {path}")
    return path


def _ensure_safe_leaf(path: Path, *, label: str) -> None:
    try:
        stat_result = path.lstat()
    except FileNotFoundError:
        return
    if _stat_is_symlink_or_reparse(stat_result):
        raise MemoryStoreError(f"unsafe {label} path: {path}")
    if stat.S_ISDIR(stat_result.st_mode):
        raise MemoryStoreError(f"unsafe {label} path: {path}")


def _create_verified_tempdir(parent: Path, *, prefix: str, label: str) -> Path:
    created = Path(tempfile.mkdtemp(prefix=prefix, dir=parent))
    return _ensure_actual_directory(created, label=label)


def _safe_rmtree(
    path: Path,
    *,
    label: str,
    ignore_missing: bool = False,
) -> None:
    try:
        _ensure_actual_directory(path, label=label)
    except MemoryStoreError:
        if ignore_missing and not path.exists():
            return
        raise
    except FileNotFoundError:
        if ignore_missing:
            return
        raise
    with os.scandir(path) as entries:
        for entry in entries:
            child = Path(entry.path)
            if entry.is_symlink():
                raise MemoryStoreError(f"unsafe {label} entry: {child}")
            if _direntry_is_reparse(entry):
                raise MemoryStoreError(f"unsafe {label} entry: {child}")
            if entry.is_dir(follow_symlinks=False):
                _safe_rmtree(child, label=label)
            else:
                child.unlink()
    path.rmdir()


def _direntry_is_reparse(entry: os.DirEntry[str]) -> bool:
    try:
        stat_result = entry.stat(follow_symlinks=False)
    except FileNotFoundError:
        return False
    return _stat_is_symlink_or_reparse(stat_result)


def _stat_is_symlink_or_reparse(stat_result: os.stat_result) -> bool:
    file_attributes = getattr(stat_result, "st_file_attributes", 0) or 0
    return bool(
        stat.S_ISLNK(stat_result.st_mode)
        or (
            file_attributes
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        )
    )


def _is_symlink_or_reparse(path: Path) -> bool:
    try:
        stat_result = path.lstat()
    except FileNotFoundError:
        return False
    return _stat_is_symlink_or_reparse(stat_result)
