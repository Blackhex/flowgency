from __future__ import annotations

import os
from pathlib import Path

from agency.configuration.group_paths import resolve_group_paths
from agency.configuration.issues import ValidationFailed, ValidationIssue
from agency.configuration.models import AgencyConfig, AgentInstance, GroupConfig
from agency.integrations import BaseIntegration, get_integration
from agency.integrations.models import EffectiveRuntimePolicy, ResolvedToolPolicy


def _build_issue(code: str, scope: str, field: str, message: str, hint: str) -> ValidationIssue:
    return ValidationIssue(code=code, scope=scope, field=field, message=message, corrective_hint=hint)


def _platform_path_key(path: Path) -> str:
    resolved = str(path.resolve(strict=False))
    if os.name == "nt":
        return os.path.normcase(resolved)
    return resolved


def _merge_roots(*root_sets: tuple[Path, ...]) -> tuple[Path, ...]:
    ordered: list[Path] = []
    seen: set[str] = set()
    for roots in root_sets:
        for root in roots:
            canonical = root.resolve(strict=False)
            key = _platform_path_key(canonical)
            if key in seen:
                continue
            seen.add(key)
            ordered.append(canonical)
    return tuple(ordered)


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


def _resolve_tools(group: GroupConfig, agent: AgentInstance) -> ResolvedToolPolicy:
    if "tools" in agent.runtime.model_fields_set:
        tools = agent.runtime.tools
    else:
        tools = group.runtime.tools
    return ResolvedToolPolicy(mode=tools.mode, names=tuple(tools.names))


def _resolve_sandbox(
    group: GroupConfig,
    agent: AgentInstance,
    *,
    group_id: str,
    agent_id: str,
) -> tuple[str, tuple[Path, ...]]:
    agent_sandbox = agent.runtime.sandbox
    group_sandbox = group.runtime.sandbox
    agent_overrides_mode = "mode" in agent_sandbox.model_fields_set
    mode = agent_sandbox.mode if agent_overrides_mode else group_sandbox.mode
    additional_roots = tuple(agent_sandbox.additional_roots)

    if mode == "unrestricted":
        if additional_roots:
            issue = _build_issue(
                code="sandbox-contradiction",
                scope=f"groups.{group_id}.agents.{agent_id}",
                field="runtime.sandbox.additional_roots",
                message="Unrestricted sandbox cannot add roots.",
                hint="Remove additional roots or switch to restricted mode.",
            )
            raise ValidationFailed((issue,))
        return mode, ()

    paths = resolve_group_paths(group)
    return mode, _merge_roots(
        (paths.workspace_root, paths.group_root),
        tuple(group_sandbox.roots),
        additional_roots,
    )


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
    sandbox_mode, sandbox_roots = _resolve_sandbox(
        group,
        agent,
        group_id=group_id,
        agent_id=agent_id,
    )
    policy = EffectiveRuntimePolicy(
        timeout=_resolve_timeout(group, agent, timeout_override),
        sandbox_mode=sandbox_mode,
        sandbox_roots=sandbox_roots,
        tools=_resolve_tools(group, agent),
    )

    if integration is None:
        try:
            integration = get_integration(agent.integration)
        except KeyError as exc:
            issue = _build_issue(
                code="unknown-integration",
                scope=f"groups.{group_id}.agents.{agent_id}",
                field="integration",
                message=f"Integration '{agent.integration}' is not registered.",
                hint="Choose an installed integration or register it before running this agent.",
            )
            raise ValidationFailed((issue,)) from exc

    issues = integration.validate_runtime_policy(policy)
    if issues:
        raise ValidationFailed(issues)
    return policy
