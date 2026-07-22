from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TestGroupPaths:
    key: str
    workspace_root: Path
    state_root: Path

    @property
    def shared_root(self) -> Path:
        return self.state_root / "shared"


def group_paths(tmp_path: Path, key: str) -> TestGroupPaths:
    return TestGroupPaths(
        key=key,
        workspace_root=tmp_path / "workspaces" / key,
        state_root=tmp_path / "groups" / key,
    )


def create_group_environment(
    tmp_path: Path,
    key: str,
    *,
    workspace_dirs: tuple[str, ...] = (),
    shared_dirs: tuple[str, ...] = (),
    create_workspace: bool = True,
    create_state: bool = False,
) -> TestGroupPaths:
    paths = group_paths(tmp_path, key)
    if create_workspace:
        paths.workspace_root.mkdir(parents=True, exist_ok=True)
    if create_state:
        paths.state_root.mkdir(parents=True, exist_ok=True)
    for relative in workspace_dirs:
        (paths.workspace_root / relative).mkdir(parents=True, exist_ok=True)
    for relative in shared_dirs:
        (paths.shared_root / relative).mkdir(parents=True, exist_ok=True)
    return paths


def apply_group_paths(group: dict, paths: TestGroupPaths) -> dict:
    group["workspace_path"] = str(paths.workspace_root)
    group["path"] = str(paths.state_root)
    return group
