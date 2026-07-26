from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agency.fs.snapshot import TreeSnapshot
from agency.prompts.assets import PromptDocument


@dataclass(frozen=True)
class BlueprintInspection:
    key: str
    path: Path
    title: str
    skills: tuple[str, ...]
    prompts: tuple[PromptDocument, ...]
    snapshot: TreeSnapshot


__all__ = ["BlueprintInspection"]