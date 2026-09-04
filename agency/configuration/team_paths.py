from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .models import TeamConfig


@dataclass(frozen=True)
class ResolvedTeamPaths:
    workspace_root: Path
    team_root: Path
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


def resolve_team_paths(team: TeamConfig) -> ResolvedTeamPaths:
    workspace_root = Path(team.workspace_path).resolve(strict=False)
    team_root = Path(team.path).resolve(strict=False)
    return ResolvedTeamPaths(
        workspace_root=workspace_root,
        team_root=team_root,
        observations=team_root / "observations",
        proposals=team_root / "proposals",
        decisions=team_root / "decisions",
        locks=team_root / "locks",
        logs=team_root / "logs",
    )
