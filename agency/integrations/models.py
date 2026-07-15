from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
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