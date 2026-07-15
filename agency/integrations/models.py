from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

PathPolicyMode = Literal["restricted", "unrestricted"]
ToolPolicyMode = Literal["all", "allowlist", "none"]


@dataclass(frozen=True)
class ResolvedToolPolicy:
    mode: ToolPolicyMode
    names: tuple[str, ...] = ()


@dataclass(frozen=True)
class EffectiveRuntimePolicy:
    timeout: int
    sandbox_mode: PathPolicyMode
    sandbox_roots: tuple[Path, ...]
    tools: ResolvedToolPolicy


@dataclass(frozen=True)
class RuntimeCapabilities:
    path_modes: frozenset[PathPolicyMode] = frozenset()
    tool_modes: frozenset[ToolPolicyMode] = frozenset()


@dataclass(frozen=True)
class ProjectorCapabilities:
    instruction_target: PurePosixPath
    skills_target: PurePosixPath
    discovers_skills: bool
    activates_selected_skill: bool


@dataclass(frozen=True)
class IntegrationRunRequest:
    workspace_dir: Path
    launch_dir: Path
    task_file: Path
    timeout: int
    runtime_policy: EffectiveRuntimePolicy
    skill: str | None
    skill_arguments: tuple[str, ...] = ()