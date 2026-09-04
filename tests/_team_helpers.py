from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TestTeamPaths:
    key: str
    workspace_root: Path
    state_root: Path


def team_paths(tmp_path: Path, key: str) -> TestTeamPaths:
    return TestTeamPaths(
        key=key,
        workspace_root=tmp_path / "workspaces" / key,
        state_root=tmp_path / "teams" / key,
    )


def create_team_environment(
    tmp_path: Path,
    key: str,
    *,
    workspace_entries: tuple[str, ...] = (),
    team_dirs: tuple[str, ...] = (),
    create_workspace: bool = True,
    create_state: bool = False,
) -> TestTeamPaths:
    paths = team_paths(tmp_path, key)
    if create_workspace:
        paths.workspace_root.mkdir(parents=True, exist_ok=True)
    if create_state:
        paths.state_root.mkdir(parents=True, exist_ok=True)
    for relative in workspace_entries:
        (paths.workspace_root / relative).mkdir(parents=True, exist_ok=True)
    for relative in team_dirs:
        (paths.state_root / relative).mkdir(parents=True, exist_ok=True)
    return paths


def apply_team_paths(team: dict, paths: TestTeamPaths) -> dict:
    team["workspace_path"] = str(paths.workspace_root)
    team["path"] = str(paths.state_root)
    return team
