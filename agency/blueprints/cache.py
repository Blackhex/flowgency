from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from agency.blueprints.models import BlueprintInspection
from agency.blueprints.projectors import RuntimeProjector
from agency.fs.atomic import atomic_write_text
from agency.fs.locks import exclusive_lock
from agency.fs.snapshot import TreeSnapshot


@dataclass(frozen=True)
class CacheRef:
    integration: str
    projector_version: str
    source_digest: str


@dataclass(frozen=True)
class CompiledArtifact:
    ref: CacheRef
    entry_path: Path
    runtime_path: Path
    manifest_path: Path

    @property
    def manifest(self) -> dict[str, Any]:
        return json.loads(self.manifest_path.read_text(encoding="utf-8"))


def _cache_key(ref: CacheRef) -> str:
    return f"{ref.integration}--{ref.projector_version}--{ref.source_digest}"


def _entry_path(root: Path, ref: CacheRef) -> Path:
    return root / ref.integration / ref.projector_version / ref.source_digest


def _artifact_from_entry(root: Path, ref: CacheRef) -> CompiledArtifact:
    entry = _entry_path(root, ref)
    return CompiledArtifact(
        ref=ref,
        entry_path=entry,
        runtime_path=entry / "runtime",
        manifest_path=entry / "manifest.json",
    )


def _lock_path(root: Path, ref: CacheRef) -> Path:
    return root / "_locks" / f"{_cache_key(ref)}.lock"


def _pins_dir(root: Path, ref: CacheRef) -> Path:
    return root / "_pins" / _cache_key(ref)


def _quarantine_root(root: Path) -> Path:
    return root / "_quarantine"


_WINDOWS_RESERVED_FILENAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_mode(path: Path) -> int:
    return stat.S_IMODE(path.stat(follow_symlinks=False).st_mode)


def _is_reparse_point(file_stat: os.stat_result) -> bool:
    attributes = getattr(file_stat, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(attributes & reparse_flag)


def _validate_job_id(job_id: str) -> str:
    if not isinstance(job_id, str) or not job_id:
        raise ValueError("job_id must be a non-empty safe filename segment")
    if job_id in {".", ".."}:
        raise ValueError("job_id must be a non-empty safe filename segment")
    if job_id[-1] in {".", " "}:
        raise ValueError("job_id must be a non-empty safe filename segment")
    candidate = Path(job_id)
    if candidate.name != job_id or candidate.anchor:
        raise ValueError("job_id must be a non-empty safe filename segment")
    if any(sep and sep in job_id for sep in (os.sep, os.altsep)):
        raise ValueError("job_id must be a non-empty safe filename segment")
    stem = job_id.split(".", 1)[0].rstrip(" .").casefold()
    if stem in _WINDOWS_RESERVED_FILENAMES:
        raise ValueError("job_id must be a non-empty safe filename segment")
    return job_id


def _validate_runtime_inventory(runtime_path: Path) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    if not runtime_path.exists() or not runtime_path.is_dir():
        raise ValueError(f"Runtime directory is missing: {runtime_path}")
    for directory, _, _ in os.walk(runtime_path):
        current = Path(directory)
        current_stat = current.stat(follow_symlinks=False)
        if _is_reparse_point(current_stat):
            raise ValueError(
                f"Runtime directory contains a reparse point: {current}"
            )
        for entry in sorted(
            os.scandir(current),
            key=lambda item: item.name.casefold(),
        ):
            entry_path = Path(entry.path)
            entry_stat = entry.stat(follow_symlinks=False)
            relative = PurePosixPath(
                *entry_path.relative_to(runtime_path).parts
            )
            if _is_reparse_point(entry_stat):
                raise ValueError(
                    "Runtime projection contains a symlink or "
                    f"reparse point: {relative.as_posix()}"
                )
            if stat.S_ISDIR(entry_stat.st_mode):
                continue
            if not stat.S_ISREG(entry_stat.st_mode):
                raise ValueError(
                    "Runtime projection contains a non-regular "
                    f"file: {relative.as_posix()}"
                )
            files.append(
                {
                    "path": relative.as_posix(),
                    "size": entry_stat.st_size,
                    "sha256": _file_sha256(entry_path),
                    "mode": _file_mode(entry_path),
                }
            )
    files.sort(key=lambda item: item["path"])
    return files


def _manifest_payload(ref: CacheRef, runtime_path: Path) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "ref": asdict(ref),
        "runtime_files": _validate_runtime_inventory(runtime_path),
    }


def _manifest_text(payload: Mapping[str, Any]) -> str:
    return (
        json.dumps(
            payload,
            sort_keys=True,
            indent=2,
            separators=(",", ": "),
        )
        + "\n"
    )


def validate_artifact(
    entry_path: Path,
    ref: CacheRef,
    source: TreeSnapshot,
    projector: RuntimeProjector,
) -> CompiledArtifact:
    artifact = CompiledArtifact(
        ref=ref,
        entry_path=Path(entry_path),
        runtime_path=Path(entry_path) / "runtime",
        manifest_path=Path(entry_path) / "manifest.json",
    )
    if not artifact.entry_path.exists() or not artifact.entry_path.is_dir():
        raise ValueError(
            f"Compiled artifact is missing: {artifact.entry_path}"
        )
    if not artifact.manifest_path.is_file():
        raise ValueError(
            "Compiled artifact manifest is missing: "
            f"{artifact.manifest_path}"
        )
    issues = projector.validate_output(source, artifact.runtime_path)
    if issues:
        raise ValueError(
            "Compiled artifact projection does not match source bytes"
        )
    payload = json.loads(artifact.manifest_path.read_text(encoding="utf-8"))
    expected = {
        "schema_version": 1,
        "ref": asdict(ref),
        "runtime_files": _validate_runtime_inventory(artifact.runtime_path),
    }
    if payload != expected:
        raise ValueError(
            "Compiled artifact manifest does not match runtime content"
        )
    expected_text = _manifest_text(expected)
    if artifact.manifest_path.read_text(encoding="utf-8") != expected_text:
        raise ValueError("Compiled artifact manifest is not deterministic")
    return artifact


def _publish_directory(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(source, destination)
    except PermissionError:
        if destination.exists():
            raise
        shutil.copytree(source, destination)
        shutil.rmtree(source, ignore_errors=True)


def _quarantine_artifact(root: Path, ref: CacheRef, entry_path: Path) -> None:
    quarantine_root = _quarantine_root(root)
    quarantine_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    target = quarantine_root / f"{timestamp}-{_cache_key(ref)}"
    os.replace(entry_path, target)


def ensure_compiled(
    root: Path,
    ref: CacheRef,
    source: TreeSnapshot,
    projector: RuntimeProjector,
) -> CompiledArtifact:
    root = Path(root).resolve()
    artifact = _artifact_from_entry(root, ref)
    try:
        return validate_artifact(artifact.entry_path, ref, source, projector)
    except ValueError:
        pass

    with exclusive_lock(_lock_path(root, ref), wait=True):
        try:
            return validate_artifact(
                artifact.entry_path,
                ref,
                source,
                projector,
            )
        except ValueError:
            if artifact.entry_path.exists():
                _quarantine_artifact(root, ref, artifact.entry_path)

        artifact.entry_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_parent = artifact.entry_path.parent
        tmp_entry = Path(
            tempfile.mkdtemp(
                prefix=f".{artifact.entry_path.name}.tmp-",
                dir=tmp_parent,
            )
        )
        try:
            runtime_path = tmp_entry / "runtime"
            runtime_path.mkdir(parents=True, exist_ok=True)
            projector.project(source, runtime_path)
            issues = projector.validate_output(source, runtime_path)
            if issues:
                raise ValueError("Projected runtime failed validation")
            manifest = _manifest_payload(ref, runtime_path)
            atomic_write_text(
                tmp_entry / "manifest.json",
                _manifest_text(manifest),
            )
            validate_artifact(tmp_entry, ref, source, projector)
            _publish_directory(tmp_entry, artifact.entry_path)
        except Exception:
            shutil.rmtree(tmp_entry, ignore_errors=True)
            raise

    return validate_artifact(artifact.entry_path, ref, source, projector)


def pin_artifact(root: Path, ref: CacheRef, job_id: str) -> Path:
    safe_job_id = _validate_job_id(job_id)
    pin_path = _pins_dir(Path(root).resolve(), ref) / safe_job_id
    atomic_write_text(pin_path, "")
    return pin_path


def release_pin(root: Path, ref: CacheRef, job_id: str) -> None:
    safe_job_id = _validate_job_id(job_id)
    pin_path = _pins_dir(Path(root).resolve(), ref) / safe_job_id
    try:
        pin_path.unlink()
    except FileNotFoundError:
        return
    try:
        pin_path.parent.rmdir()
    except OSError:
        pass


def active_pins(root: Path, ref: CacheRef) -> tuple[str, ...]:
    pins_dir = _pins_dir(Path(root).resolve(), ref)
    if not pins_dir.exists():
        return ()
    active: list[str] = []
    for item in pins_dir.iterdir():
        try:
            item_stat = item.stat(follow_symlinks=False)
        except FileNotFoundError:
            continue
        if _is_reparse_point(item_stat):
            continue
        if not stat.S_ISREG(item_stat.st_mode):
            continue
        try:
            active.append(_validate_job_id(item.name))
        except ValueError:
            continue
    return tuple(sorted(active))


class CompilationCache:
    def __init__(self, root: Path, projectors: Mapping[str, RuntimeProjector]):
        self.root = Path(root).resolve()
        self.projectors = dict(projectors)

    def ensure_compiled(
        self,
        integration: str,
        inspection: BlueprintInspection,
    ) -> CompiledArtifact:
        projector = self.projectors[integration]
        ref = CacheRef(
            integration,
            projector.version,
            inspection.snapshot.digest,
        )
        return ensure_compiled(self.root, ref, inspection.snapshot, projector)

    def pin(self, artifact: CompiledArtifact, job_id: str) -> Path:
        return pin_artifact(self.root, artifact.ref, job_id)

    def release(self, artifact: CompiledArtifact, job_id: str) -> None:
        release_pin(self.root, artifact.ref, job_id)


__all__ = [
    "CacheRef",
    "CompiledArtifact",
    "CompilationCache",
    "active_pins",
    "ensure_compiled",
    "pin_artifact",
    "release_pin",
    "validate_artifact",
]
