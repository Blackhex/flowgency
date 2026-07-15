from __future__ import annotations

from collections.abc import Mapping
import hashlib
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
    ResolvedMemory,
)


_REVISION_DOMAIN = b"agency-memory-content:v1\0"
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


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


def read_memory(resolved: ResolvedMemory) -> MemorySnapshot:
    _validate_resolved_memory(resolved)
    lock_path = _lock_path(resolved)
    with exclusive_lock(lock_path, wait=True):
        files = _read_canonical_files(resolved.directory)
        return MemorySnapshot(
            resolved=resolved,
            files=files,
            revision=memory_content_revision(files),
        )


def ensure_memory(resolved: ResolvedMemory) -> MemorySnapshot:
    _validate_resolved_memory(resolved)
    lock_path = _lock_path(resolved)
    with exclusive_lock(lock_path, wait=True):
        resolved.directory.mkdir(parents=True, exist_ok=True)
        if not resolved.directory.exists():
            raise RuntimeError(
                f"missing memory directory: {resolved.directory}"
            )
        if not any(resolved.directory.iterdir()):
            atomic_write_bytes(resolved.directory / "memory.md", b"")
        files = _read_canonical_files(resolved.directory)
        if not files:
            raise ValueError("memory must contain at least one markdown file")
    return MemorySnapshot(
        resolved=resolved,
        files=files,
        revision=memory_content_revision(files),
    )


def stage_memory(resolved: ResolvedMemory, *, job_id: str) -> MemoryStage:
    snapshot = ensure_memory(resolved)
    root = _store_root(resolved)
    stage_parent = root / ".staging" / resolved.memory_hash
    safe_job_id = _validate_job_id(job_id)
    stage_root = (stage_parent / safe_job_id).resolve(strict=False)
    if stage_root.parent != stage_parent.resolve():
        raise ValueError("job id must stay within the staging directory")
    if stage_root.exists():
        shutil.rmtree(stage_root)
    stage_root.mkdir(parents=True, exist_ok=True)
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
    lock_path = _lock_path(resolved)
    with try_exclusive_lock(lock_path):
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
        _replace_canonical_files(resolved.directory, normalized)
    return MemorySnapshot(
        resolved=resolved,
        files=normalized,
        revision=memory_content_revision(normalized),
    )


class MemoryStore:
    def __init__(self, root: Path):
        self.root = Path(root).expanduser().resolve()

    def _lock_path(self, resolved: ResolvedMemory) -> Path:
        _validate_resolved_memory(resolved, expected_root=self.root)
        return self.root / ".locks" / f"{resolved.memory_hash}.lock"

    def read(self, resolved: ResolvedMemory) -> MemorySnapshot:
        _validate_resolved_memory(resolved, expected_root=self.root)
        return read_memory(resolved)

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
    return resolved.directory.parent.resolve()


def _lock_path(resolved: ResolvedMemory) -> Path:
    root = _store_root(resolved)
    return root / ".locks" / f"{resolved.memory_hash}.lock"


def _validate_resolved_memory(
    resolved: ResolvedMemory,
    *,
    expected_root: Path | None = None,
) -> None:
    if len(resolved.memory_hash) != 64:
        raise ValueError("memory hash must be 64 hex characters")
    int(resolved.memory_hash, 16)
    root = (
        expected_root.resolve()
        if expected_root is not None
        else _store_root(resolved)
    )
    directory = resolved.directory.resolve(strict=False)
    if directory.parent != root:
        raise ValueError(
            "memory directory must stay under the configured memory root"
        )
    if directory.name != resolved.memory_hash:
        raise ValueError("memory directory name must match the resolved hash")


def _read_canonical_files(directory: Path) -> dict[str, bytes]:
    directory = Path(directory)
    if not directory.exists():
        return {}
    if not directory.is_dir():
        raise ValueError(f"memory path is not a directory: {directory}")

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
    directory.mkdir(parents=True, exist_ok=True)
    parent = directory.parent
    staging_parent = parent / ".backups"
    staging_parent.mkdir(parents=True, exist_ok=True)
    temp_directory = Path(
        tempfile.mkdtemp(prefix=f"{directory.name}.", dir=staging_parent)
    )
    backup_directory = Path(
        tempfile.mkdtemp(prefix=f"{directory.name}.", dir=staging_parent)
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
        shutil.rmtree(backup_directory)
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
        shutil.rmtree(temp_directory, ignore_errors=True)


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
            shutil.rmtree(backup_directory)
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
        raise ValueError("job id must not be ambiguous under Unicode normalization")
    if _normalized_key(job_id) != job_id:
        raise ValueError("job id must not be ambiguous under case normalization")
    if path.stem.upper() in _WINDOWS_RESERVED_NAMES:
        raise ValueError("job id uses a reserved Windows basename")
    return job_id


def _is_symlink_or_reparse(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        stat_result = path.lstat()
    except FileNotFoundError:
        return False
    file_attributes = getattr(stat_result, "st_file_attributes", 0)
    return bool(
        file_attributes
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )
