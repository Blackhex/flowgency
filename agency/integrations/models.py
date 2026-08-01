from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Literal

from agency.projector_capabilities import ProjectorCapabilities

PermissionMode = Literal["restricted", "unrestricted"]

# Deprecated aliases kept until Tasks 3-7 clean up their call sites.
PathPolicyMode = PermissionMode
ToolPolicyMode = Literal["all", "allowlist", "none"]


@dataclass(frozen=True)
class ResolvedToolPolicy:
    """Deprecated; kept for backward-compat imports until Tasks 3-7."""
    mode: str
    names: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResolvedPermissionRule:
    path: Path | None
    tools: tuple[str, ...] | None


@dataclass(frozen=True)
class EffectiveRuntimePolicy:
    timeout: int
    mode: PermissionMode = "unrestricted"
    rules: tuple[ResolvedPermissionRule, ...] = ()
    # Deprecated fields kept for backward-compat until Tasks 3-7 update call sites.
    sandbox_mode: str = "unrestricted"
    sandbox_roots: tuple[Path, ...] = ()
    tools: object = None
    writable_roots: tuple[Path, ...] = ()
    writes_narrowed: bool = False

    @property
    def narrows_writes(self) -> bool:
        """Deprecated; whether this policy grants read but not write access."""
        return self.writes_narrowed

    def tools_for(self, path: Path) -> tuple[str, ...] | None:
        """Tools permitted on `path`. None means every tool."""
        target = Path(path)
        best: ResolvedPermissionRule | None = None
        for candidate in self.rules:
            if candidate.path is None:
                continue
            if not _covers(candidate.path, target):
                continue
            if best is None or len(candidate.path.parts) > len(best.path.parts):
                best = candidate
        if best is not None:
            return best.tools
        return None if self.mode == "unrestricted" else ()

    @property
    def scoped_tools(self) -> frozenset[str]:
        """Tools whose grant differs between path-bearing rules.

        Names granted by tools=None rules outside the explicit vocabulary cannot
        be enumerated here; treat any non-empty result as indicating that
        additional unbounded differences may also exist.
        """
        granted: list[frozenset[str] | None] = [
            None if rule.tools is None else frozenset(rule.tools)
            for rule in self.rules
            if rule.path is not None
        ]
        if len(granted) < 2:
            return frozenset()
        has_none = any(entry is None for entry in granted)
        explicit = [entry for entry in granted if entry is not None]
        explicit_union = frozenset().union(*explicit) if explicit else frozenset()
        # Names that differ among the explicit rules.
        differs = frozenset(
            name for name in explicit_union if any(name not in entry for entry in explicit)
        )
        # When an unbounded (None) rule coexists with any explicit tuple, every
        # named tool is potentially scoped differently across paths.
        if has_none and explicit:
            return differs | explicit_union
        return differs

    def with_launch_zones(self, launch_dir: Path) -> "EffectiveRuntimePolicy":
        from agency.permissions.zones import launch_zone_rules

        authored = tuple(
            rule
            for rule in self.rules
            if rule.path is None or not _under_launch(rule.path, launch_dir)
        )
        return replace(self, rules=authored + launch_zone_rules(launch_dir))


def _covers(rule_path: Path, target: Path) -> bool:
    try:
        target.resolve(strict=False).relative_to(rule_path.resolve(strict=False))
        return True
    except ValueError:
        return False


def _under_launch(rule_path: Path, launch_dir: Path) -> bool:
    return _covers(Path(launch_dir), Path(rule_path))


@dataclass(frozen=True)
class RuntimeCapabilities:
    # New fields for the permission model.
    permission_modes: frozenset[PermissionMode] = frozenset()
    path_scopable_tools: frozenset[str] = frozenset()
    # Deprecated fields kept for backward-compat until Tasks 3-7 update integrations.
    path_modes: frozenset[str] = frozenset()
    tool_modes: frozenset[str] = frozenset()
    enforces_write_boundary: bool = False


@dataclass(frozen=True)
class IntegrationRunRequest:
    workspace_root: Path
    launch_dir: Path
    task_file: Path
    timeout: int
    runtime_policy: EffectiveRuntimePolicy
    skill: str | None = None
    skill_arguments: tuple[str, ...] = ()
    enforce_validation: bool = True
    memory_working_dir: Path | None = None


@dataclass(frozen=True)
class InteractiveSetupRequest:
    project_dir: Path
    config_path: Path
    prompt: str


@dataclass(frozen=True)
class InteractiveSetupResult:
    fallback_command: str
