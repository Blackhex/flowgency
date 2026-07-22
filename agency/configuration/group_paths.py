from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .models import GroupConfig


@dataclass(frozen=True)
class ResolvedGroupPaths:
    workspace_root: Path
    group_root: Path
    observations: Path
    proposals: Path
    decisions: Path
    locks: Path
    logs: Path

    @property
    def record_directories(self) -> tuple[Path, ...]:
        return (
            self.observations,
            self.proposals,
            self.decisions,
            self.locks,
            self.logs,
        )


def resolve_group_paths(group: GroupConfig) -> ResolvedGroupPaths:
    workspace_root = Path(group.workspace_path).resolve(strict=False)
    group_root = Path(group.path).resolve(strict=False)
    return ResolvedGroupPaths(
        workspace_root=workspace_root,
        group_root=group_root,
        observations=group_root / "observations",
        proposals=group_root / "proposals",
        decisions=group_root / "decisions",
        locks=group_root / "locks",
        logs=group_root / "logs",
    )
