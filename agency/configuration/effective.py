from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

from agency.configuration.issues import ValidationFailed, ValidationIssue
from agency.configuration.models import AgencyConfig, AgentInstance, TeamConfig, PermissionMode
from agency.integrations import BaseIntegration, get_integration
from agency.integrations.models import EffectiveRuntimePolicy, ResolvedPermissionRule


def _build_issue(code: str, scope: str, field: str, message: str, corrective_hint: str) -> ValidationIssue:
    return ValidationIssue(code=code, scope=scope, field=field, message=message, corrective_hint=corrective_hint)


def _platform_path_key(path: Path) -> str:
    resolved = str(path.resolve(strict=False))
    if os.name == "nt":
        return os.path.normcase(resolved)
    return resolved


def _get_team(config: AgencyConfig, team_id: str) -> TeamConfig:
    try:
        return config.teams[team_id]
    except KeyError as exc:
        raise KeyError(f"Unknown team: {team_id}") from exc


def _get_agent(team: TeamConfig, agent_id: str) -> AgentInstance:
    try:
        return team.agents[agent_id]
    except KeyError as exc:
        raise KeyError(f"Unknown agent: {agent_id}") from exc


def _resolve_timeout(team: TeamConfig, agent: AgentInstance, timeout_override: int | None) -> int:
    if timeout_override is not None:
        return timeout_override
    if "timeout" in agent.runtime.model_fields_set:
        return agent.runtime.timeout
    return team.runtime.timeout


def _resolve_mode(team: TeamConfig, agent: AgentInstance) -> PermissionMode:
    if "permissions" in agent.runtime.model_fields_set and (
        "mode" in agent.runtime.permissions.model_fields_set
    ):
        return agent.runtime.permissions.mode
    return team.runtime.permissions.mode


def _resolve_rule_path(rule_path: Path, workspace: Path) -> Path:
    """Resolve a rule path against the team workspace when relative."""
    if rule_path.is_absolute():
        return rule_path.resolve(strict=False)
    return (workspace / rule_path).resolve(strict=False)


def _merge_rules(
    team: TeamConfig,
    agent: AgentInstance,
) -> tuple[ResolvedPermissionRule, ...]:
    """Instance rules are additive; identical paths union their tools."""
    merged: list[ResolvedPermissionRule] = []
    index: dict[str | None, int] = {}
    workspace = team.workspace_path

    for source in (team.runtime.permissions.rules, agent.runtime.permissions.rules):
        for rule in source:
            resolved_path = None if rule.path is None else _resolve_rule_path(Path(rule.path), workspace)
            key = None if resolved_path is None else _platform_path_key(resolved_path)
            resolved = ResolvedPermissionRule(
                path=resolved_path,
                tools=None if rule.tools is None else tuple(rule.tools),
            )
            if key in index:
                existing = merged[index[key]]
                if existing.tools is None or resolved.tools is None:
                    union = None
                else:
                    union = tuple(dict.fromkeys((*existing.tools, *resolved.tools)))
                merged[index[key]] = replace(existing, tools=union)
                continue
            index[key] = len(merged)
            merged.append(resolved)

    return tuple(merged)


def resolve_effective_policy(
    config: AgencyConfig,
    team_id: str,
    agent_id: str,
    *,
    timeout_override: int | None = None,
    integration: BaseIntegration | None = None,
) -> EffectiveRuntimePolicy:
    team = _get_team(config, team_id)
    agent = _get_agent(team, agent_id)
    policy = EffectiveRuntimePolicy(
        timeout=_resolve_timeout(team, agent, timeout_override),
        mode=_resolve_mode(team, agent),
        rules=_merge_rules(team, agent),
    )

    if integration is None:
        try:
            integration = get_integration(agent.integration)
        except KeyError as exc:
            issue = _build_issue(
                code="unknown-integration",
                scope=f"teams.{team_id}.agents.{agent_id}",
                field="integration",
                message=f"Integration '{agent.integration}' is not registered.",
                corrective_hint="Choose an installed integration or register it before running this agent.",
            )
            raise ValidationFailed((issue,)) from exc

    issues = integration.validate_runtime_policy(policy)
    if issues:
        raise ValidationFailed(issues)
    return policy
