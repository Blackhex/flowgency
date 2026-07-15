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
