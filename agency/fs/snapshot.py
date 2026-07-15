from __future__ import annotations

import hashlib
import os
import re
import stat
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from agency.configuration.issues import ValidationFailed, ValidationIssue


_PATH_PREFIX = b"agency-blueprint-source:v1\0"
_IDENTIFIER_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_WINDOWS_RESERVED = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}


@dataclass(frozen=True)
class SnapshotFile:
    path: PurePosixPath
    content: bytes


@dataclass(frozen=True)
class TreeSnapshot:
    files: tuple[SnapshotFile, ...]
    digest: str

    def file(self, relative_path: str) -> SnapshotFile:
        target = PurePosixPath(relative_path)
        for item in self.files:
            if item.path == target:
                return item
        raise KeyError(relative_path)


@dataclass(frozen=True)
class _InventoryEntry:
    path: PurePosixPath
    size: int
    mtime_ns: int
    file_id: tuple[int, int]


@dataclass(frozen=True)
class _ScanResult:
    files: tuple[SnapshotFile, ...]
    inventory: tuple[_InventoryEntry, ...]


class AssetValidationError(ValidationFailed):
    pass


def _issue(field: str, message: str, hint: str, *, code: str = "invalid-blueprint-asset") -> ValidationIssue:
    return ValidationIssue(
        code=code,
        scope="blueprint",
        field=field,
        message=message,
        corrective_hint=hint,
    )


def _raise(field: str, message: str, hint: str, *, code: str = "invalid-blueprint-asset") -> None:
    raise AssetValidationError((_issue(field, message, hint, code=code),))


def _is_reparse_point(file_stat: os.stat_result) -> bool:
    attributes = getattr(file_stat, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(attributes & reparse_flag)


def _validate_name(name: str, field: str) -> None:
    if name in {"", ".", ".."}:
        _raise(field, f"Invalid path segment: {name!r}.", "Remove empty or relative path segments.")
    if name.endswith(" ") or name.endswith("."):
        _raise(field, f"Path segment is not stable on Windows: {name!r}.", "Remove trailing spaces or dots from file and directory names.")
    stem = name.rstrip(" .").split(".", 1)[0].casefold()
    if stem in _WINDOWS_RESERVED:
        _raise(field, f"Windows reserved path segment is not allowed: {name!r}.", "Rename the file or directory to a non-reserved name.")


def _relative_path(parts: tuple[str, ...]) -> PurePosixPath:
    for index, part in enumerate(parts):
        _validate_name(part, f"path[{index}]")
    relative = PurePosixPath(*parts)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        _raise("path", f"Blueprint path escapes are not allowed: {relative.as_posix()!r}.", "Keep every captured file inside the blueprint root.")
    return relative


def _casefold_key(path: PurePosixPath) -> str:
    return path.as_posix().casefold()


def _file_id(file_stat: os.stat_result) -> tuple[int, int]:
    return (int(getattr(file_stat, "st_dev", 0)), int(getattr(file_stat, "st_ino", 0)))


def _root_stat(root: Path) -> os.stat_result:
    try:
        root_stat = root.stat(follow_symlinks=False)
    except FileNotFoundError as exc:
        _raise("root", f"Blueprint root does not exist: {root}.", "Create the blueprint directory before capturing it.", code="missing-blueprint-root")
        raise AssertionError("unreachable") from exc
    if not stat.S_ISDIR(root_stat.st_mode):
        _raise("root", f"Blueprint root is not a directory: {root}.", "Point capture_tree() at a real directory.", code="invalid-blueprint-root")
    if _is_reparse_point(root_stat):
        _raise("root", f"Blueprint root cannot be a symlink or reparse point: {root}.", "Use a real directory for the blueprint source.")
    return root_stat


def _scan_tree(root: Path) -> _ScanResult:
    _root_stat(root)
    files: list[SnapshotFile] = []
    inventory: list[_InventoryEntry] = []
    seen_exact: set[PurePosixPath] = set()
    seen_casefold: set[str] = set()

    def visit(directory: Path, parts: tuple[str, ...]) -> None:
        try:
            entries = sorted(os.scandir(directory), key=lambda entry: entry.name.casefold())
        except FileNotFoundError:
            _raise("root", f"Blueprint source changed while scanning: {root}.", "Retry after the source tree stops changing.", code="unstable-blueprint-source")
        for entry in entries:
            relative = _relative_path(parts + (entry.name,))
            if relative in seen_exact or _casefold_key(relative) in seen_casefold:
                _raise(
                    "path",
                    f"Blueprint contains duplicate normalized or case-folded paths: {relative.as_posix()}.",
                    "Rename colliding files so every relative path is unique on all supported platforms.",
                    code="duplicate-blueprint-path",
                )
            try:
                entry_stat = entry.stat(follow_symlinks=False)
            except FileNotFoundError:
                _raise("root", f"Blueprint source changed while scanning: {relative.as_posix()}.", "Retry after the source tree stops changing.", code="unstable-blueprint-source")
            if _is_reparse_point(entry_stat):
                _raise(
                    relative.as_posix(),
                    f"Symlinks, junctions, and reparse points are not allowed: {relative.as_posix()}.",
                    "Replace linked entries with regular files or directories.",
                )
            seen_exact.add(relative)
            seen_casefold.add(_casefold_key(relative))
            if stat.S_ISDIR(entry_stat.st_mode):
                visit(Path(entry.path), parts + (entry.name,))
                continue
            if not stat.S_ISREG(entry_stat.st_mode):
                _raise(
                    relative.as_posix(),
                    f"Only regular files are allowed in blueprints: {relative.as_posix()}.",
                    "Remove device, pipe, or other special filesystem entries from the blueprint source.",
                )
            content = Path(entry.path).read_bytes()
            files.append(SnapshotFile(path=relative, content=content))
            inventory.append(
                _InventoryEntry(
                    path=relative,
                    size=entry_stat.st_size,
                    mtime_ns=entry_stat.st_mtime_ns,
                    file_id=_file_id(entry_stat),
                )
            )

    visit(root, ())
    inventory.sort(key=lambda item: item.path.as_posix())
    files.sort(key=lambda item: item.path.as_posix())
    return _ScanResult(files=tuple(files), inventory=tuple(inventory))


def compute_source_digest(files: Sequence[SnapshotFile]) -> str:
    digest = hashlib.sha256(_PATH_PREFIX)
    for item in sorted(files, key=lambda value: value.path.as_posix()):
        encoded_path = item.path.as_posix().encode("utf-8")
        digest.update(len(encoded_path).to_bytes(8, "big", signed=False))
        digest.update(encoded_path)
        digest.update(len(item.content).to_bytes(8, "big", signed=False))
        digest.update(item.content)
    return digest.hexdigest()


def capture_tree(root: Path, attempts: int = 3) -> TreeSnapshot:
    source_root = Path(root)
    if attempts < 1:
        raise ValueError("attempts must be at least 1")
    last_change: AssetValidationError | None = None
    for _ in range(attempts):
        first = _scan_tree(source_root)
        second = _scan_tree(source_root)
        if first.inventory == second.inventory:
            return TreeSnapshot(files=second.files, digest=compute_source_digest(second.files))
        last_change = AssetValidationError(
            (
                _issue(
                    "root",
                    f"Blueprint source changed while scanning: {source_root}.",
                    "Retry after the source tree stops changing.",
                    code="unstable-blueprint-source",
                ),
            )
        )
    assert last_change is not None
    raise last_change


__all__ = [
    "AssetValidationError",
    "SnapshotFile",
    "TreeSnapshot",
    "capture_tree",
    "compute_source_digest",
]