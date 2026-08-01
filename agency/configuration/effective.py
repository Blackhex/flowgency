from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

from agency.configuration.issues import ValidationFailed, ValidationIssue
from agency.configuration.models import AgencyConfig, AgentInstance, GroupConfig, PermissionMode
from agency.integrations import BaseIntegration
from agency.integrations.models import EffectiveRuntimePolicy, ResolvedPermissionRule


def _build_issue(code: str, scope: str, field: str, message: str, hint: str) -> ValidationIssue:
    return ValidationIssue(code=code, scope=scope, field=field, message=message, corrective_hint=hint)


def _platform_path_key(path: Path) -> str:
    resolved = str(path.resolve(strict=False))
    if os.name == "nt":
        return os.path.normcase(resolved)
    return resolved


def _get_group(config: AgencyConfig, group_id: str) -> GroupConfig:
    try:
        return config.groups[group_id]
    except KeyError as exc:
        raise KeyError(f"Unknown group: {group_id}") from exc


def _get_agent(group: GroupConfig, agent_id: str) -> AgentInstance:
    try:
        return group.agents[agent_id]
    except KeyError as exc:
        raise KeyError(f"Unknown agent: {agent_id}") from exc


def _resolve_timeout(group: GroupConfig, agent: AgentInstance, timeout_override: int | None) -> int:
    if timeout_override is not None:
        return timeout_override
    if "timeout" in agent.runtime.model_fields_set:
        return agent.runtime.timeout
    return group.runtime.timeout


def _resolve_mode(group: GroupConfig, agent: AgentInstance) -> PermissionMode:
    if "permissions" in agent.runtime.model_fields_set and (
        "mode" in agent.runtime.permissions.model_fields_set
    ):
        return agent.runtime.permissions.mode
    return group.runtime.permissions.mode


def _merge_rules(
    group: GroupConfig,
    agent: AgentInstance,
) -> tuple[ResolvedPermissionRule, ...]:
    """Instance rules are additive; identical paths union their tools."""
    merged: list[ResolvedPermissionRule] = []
    index: dict[str | None, int] = {}

    for source in (group.runtime.permissions.rules, agent.runtime.permissions.rules):
        for rule in source:
            key = None if rule.path is None else _platform_path_key(Path(rule.path))
            resolved = ResolvedPermissionRule(
                path=None if rule.path is None else Path(rule.path).resolve(strict=False),
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
    group_id: str,
    agent_id: str,
    *,
    timeout_override: int | None = None,
    integration: BaseIntegration | None = None,
) -> EffectiveRuntimePolicy:
    group = _get_group(config, group_id)
    agent = _get_agent(group, agent_id)
    return EffectiveRuntimePolicy(
        timeout=_resolve_timeout(group, agent, timeout_override),
        mode=_resolve_mode(group, agent),
        rules=_merge_rules(group, agent),
    )
