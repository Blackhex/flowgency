from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from flowgency.configuration.effective import resolve_effective_policy
from flowgency.configuration.models import FlowgencyConfig, PermissionRule
from flowgency.permissions.eligibility import grants_write_on


Source = Literal["team", "agent"]


@dataclass(frozen=True)
class EffectiveGrant:
    name: str
    sources: tuple[Source, ...]


@dataclass(frozen=True)
class EffectiveScope:
    path: str | None
    grants: tuple[EffectiveGrant, ...]
    all_tools_sources: tuple[Source, ...]


@dataclass(frozen=True)
class PermissionSummary:
    mode: str
    mode_source: Source
    scopes: tuple[EffectiveScope, ...]
    workspace_write: bool
    team_settings_href: str


def present_permissions(
    config: FlowgencyConfig, team_id: str, agent_id: str
) -> PermissionSummary:
    policy = resolve_effective_policy(config, team_id, agent_id)
    team = config.teams[team_id]
    agent = team.agents[agent_id]
    mode_source: Source = (
        "agent" if "mode" in agent.permissions.model_fields_set else "team"
    )
    scopes: list[EffectiveScope] = []

    for rule in policy.rules:
        rule_sources = _sources_for_exact_path(team.workspace_path, team.permissions.rules, agent.permissions.rules, rule.path)
        if rule.tools is None:
            scopes.append(
                EffectiveScope(
                    path=_display_path(rule.path),
                    grants=(),
                    all_tools_sources=tuple(
                        source
                        for source in ("team", "agent")
                        if source in rule_sources and rule_sources[source] is None
                    ),
                )
            )
            continue

        grants = tuple(
            EffectiveGrant(
                name=tool,
                sources=tuple(
                    source
                    for source in ("team", "agent")
                    if source in rule_sources
                    and (
                        rule_sources[source] is None
                        or tool in rule_sources[source]
                    )
                ),
            )
            for tool in rule.tools
        )
        scopes.append(
            EffectiveScope(
                path=_display_path(rule.path),
                grants=grants,
                all_tools_sources=(),
            )
        )

    return PermissionSummary(
        mode=policy.mode,
        mode_source=mode_source,
        scopes=tuple(scopes),
        workspace_write=grants_write_on(policy.rules, team.workspace_path),
        team_settings_href=f"/admin/teams/{team_id}/edit",
    )


def _sources_for_exact_path(
    workspace: Path,
    team_rules: tuple[PermissionRule, ...],
    agent_rules: tuple[PermissionRule, ...],
    rule_path: Path | None,
) -> dict[Source, tuple[str, ...] | None]:
    expected = _path_key(rule_path, workspace)
    sources: dict[Source, tuple[str, ...] | None] = {}
    for source_name, rules in (("team", team_rules), ("agent", agent_rules)):
        exact: tuple[str, ...] | None = ()
        matched = False
        for rule in rules:
            if _path_key(rule.path, workspace) != expected:
                continue
            matched = True
            if exact is None or rule.tools is None:
                exact = None
                continue
            exact = tuple(dict.fromkeys((*exact, *rule.tools)))
        if matched:
            sources[source_name] = exact
    return sources


def _path_key(path: Path | None, workspace: Path) -> str | None:
    if path is None:
        return None
    resolved = _resolve_rule_path(path, workspace)
    raw = str(resolved.resolve(strict=False))
    if os.name == "nt":
        return os.path.normcase(raw)
    return raw


def _resolve_rule_path(path: Path, workspace: Path) -> Path:
    if path.is_absolute():
        return path.resolve(strict=False)
    return (workspace / path).resolve(strict=False)


def _display_path(path: Path | None) -> str | None:
    if path is None:
        return None
    return path.resolve(strict=False).as_posix()