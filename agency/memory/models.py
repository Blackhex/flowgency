from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from agency.configuration.models import MemorySelector


@dataclass(frozen=True)
class ResolvedMemory:
    selector: MemorySelector
    canonical_json: str
    memory_hash: str
    directory: Path


@dataclass(frozen=True)
class MemorySnapshot:
    resolved: ResolvedMemory
    files: Mapping[str, bytes]
    revision: str


@dataclass(frozen=True)
class MemoryStage:
    resolved: ResolvedMemory
    job_id: str
    directory: Path
    base_revision: str


@dataclass(frozen=True)
class PreparedPublication:
    stage: MemoryStage
    job_store: Path
    job_path: Path
    selector: dict[str, object]
    memory_hash: str
    old_revision: str
    new_revision: str
    old_files: Mapping[str, bytes]
    new_files: Mapping[str, bytes]
    diff_bytes: bytes
    journal_path: Path
    backup_path: Path
    no_change: bool


@dataclass(frozen=True)
class MemoryPublicationReceipt:
    selector: dict[str, object]
    memory_hash: str
    old_revision: str
    new_revision: str
    diff_artifact: object | None
    published_at: str
    no_change: bool


class MemoryConflictError(RuntimeError):
    def __init__(
        self,
        *,
        expected_revision: str,
        current: MemorySnapshot,
        attempted_files: Mapping[str, bytes],
    ) -> None:
        super().__init__("memory changed; reload before saving")
        self.expected_revision = expected_revision
        self.current = current
        self.attempted_files = dict(attempted_files)


class MemoryStoreError(ValueError):
    pass
