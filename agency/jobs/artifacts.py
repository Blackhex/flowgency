from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import stat
import unicodedata

from agency.fs.atomic import atomic_write_bytes


_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


@dataclass(frozen=True)
class JobArtifact:
    name: str
    path: str
    size: int

    def to_dict(self) -> dict[str, object]:
        return {"name": self.name, "path": self.path, "size": self.size}


def retain_failed_stage(
    *,
    job_store: Path,
    job_id: str,
    stage_directory: Path,
    diff_bytes: bytes | None,
) -> list[JobArtifact]:
    job_store = _validate_job_store(job_store)
    safe_job_id = _validate_job_id(job_id)
    artifacts_root = _ensure_child_directory(
        job_store,
        "artifacts",
        label="artifacts",
    )
    target = artifacts_root / safe_job_id
    if target.exists():
        _safe_rmtree(target, label="artifacts")
    target = _ensure_child_directory(
        artifacts_root,
        safe_job_id,
        label="artifacts",
    )

    stage_root = _ensure_actual_directory(
        Path(stage_directory),
        label="artifacts",
    )
    artifacts: list[JobArtifact] = []
    for name, payload in _read_stage_files(stage_root).items():
        artifact_path = target / name
        atomic_write_bytes(artifact_path, payload)
        artifacts.append(
            JobArtifact(
                name=name,
                path=str(artifact_path.resolve()),
                size=len(payload),
            )
        )
    if diff_bytes is not None:
        diff_path = target / "memory.diff"
        atomic_write_bytes(diff_path, diff_bytes)
        artifacts.append(
            JobArtifact(
                name="memory.diff",
                path=str(diff_path.resolve()),
                size=len(diff_bytes),
            )
        )
    return artifacts


def _validate_job_store(job_store: Path) -> Path:
    candidate = Path(job_store)
    if _is_symlink_or_reparse(candidate):
        raise ValueError("job store is unsafe")
    if candidate.parent.name != ".jobs":
        raise ValueError("job store must be a canonical .jobs/<group> directory")
    resolved = candidate.resolve()
    if resolved.parent.name != ".jobs":
        raise ValueError("job store must be a canonical .jobs/<group> directory")
    if not resolved.is_dir():
        raise ValueError("job store must be an existing directory")
    return resolved


def _read_stage_files(directory: Path) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for entry in sorted(
        Path(directory).iterdir(),
        key=lambda item: item.name.casefold(),
    ):
        if _is_symlink_or_reparse(entry):
            raise ValueError(
                f"artifacts contain symlink or reparse point: {entry.name}"
            )
        if not entry.is_file():
            raise ValueError(
                f"artifacts contain nested or non-file entry: {entry.name}"
            )
        _validate_stage_filename(entry.name)
        if entry.suffix != ".md":
            raise ValueError(
                f"artifacts contain non-markdown file: {entry.name}"
            )
        files[entry.name] = entry.read_bytes()
    return files


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
        raise ValueError(f"missing {label} directory: {path}") from exc
    if _stat_is_symlink_or_reparse(stat_result):
        raise ValueError(f"unsafe {label} directory: {path}")
    if not stat.S_ISDIR(stat_result.st_mode):
        raise ValueError(f"{label} path is not a directory: {path}")
    return path


def _ensure_child_directory(parent: Path, name: str, *, label: str) -> Path:
    child = parent / name
    try:
        child.lstat()
    except FileNotFoundError:
        child.mkdir()
    return _ensure_actual_directory(child, label=label)


def _safe_rmtree(path: Path, *, label: str) -> None:
    _ensure_actual_directory(path, label=label)
    with os.scandir(path) as entries:
        for entry in entries:
            child = Path(entry.path)
            if entry.is_symlink():
                raise ValueError(f"unsafe {label} entry: {child}")
            if _direntry_is_reparse(entry):
                raise ValueError(f"unsafe {label} entry: {child}")
            if entry.is_dir(follow_symlinks=False):
                _safe_rmtree(child, label=label)
            else:
                child.unlink()
    path.rmdir()


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
    if job_id.casefold() != job_id:
        raise ValueError(
            "job id must not be ambiguous under case normalization"
        )
    if path.stem.upper() in _WINDOWS_RESERVED_NAMES:
        raise ValueError("job id uses a reserved Windows basename")
    return job_id


def _validate_stage_filename(name: str) -> None:
    candidate = Path(name)
    if candidate.name != name or name in {"", ".", ".."}:
        raise ValueError(f"artifacts filename must be direct: {name}")
    if name.startswith("."):
        raise ValueError(
            f"artifacts filename must not be hidden infrastructure: {name}"
        )
    if name.endswith((" ", ".")):
        raise ValueError(f"artifacts filename has trailing ambiguity: {name}")
    if "/" in name or "\\" in name:
        raise ValueError(f"artifacts filename must be direct: {name}")
    if candidate.stem.upper() in _WINDOWS_RESERVED_NAMES:
        raise ValueError(f"artifacts filename uses reserved name: {name}")


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
        or (file_attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
    )


def _is_symlink_or_reparse(path: Path) -> bool:
    try:
        stat_result = path.lstat()
    except FileNotFoundError:
        return False
    return _stat_is_symlink_or_reparse(stat_result)
