from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agency.config import (
    SandboxSpec,
    get_agent_dir,
    get_sandbox_root,
    load_config_path,
    normalize_agents,
)
from agency.integrations import BaseIntegration, detect_integration, get_integration

from .models import JobSpec


class JobValidationError(ValueError):
    pass


@dataclass
class ResolvedJobContext:
    config: dict[str, Any]
    group: dict[str, Any]
    group_path: Path
    agent_config: dict[str, Any]
    agent_dir: Path
    integration: BaseIntegration
    timeout: int
    sandbox_root: SandboxSpec | None


def resolve_job_context(spec: JobSpec) -> ResolvedJobContext:
    spec.validate()
    config = load_config_path(Path(spec.config_path))
    raw_group = config.get("groups", {}).get(spec.group_key)
    if raw_group is None:
        raise JobValidationError(f"Unknown group: {spec.group_key}")
    group_path = Path(raw_group["path"])
    agents = normalize_agents(
        raw_group.get("agents", []),
        raw_group.get("default_integration", "claude-code"),
    )
    agent_config = next(
        (agent for agent in agents if agent["name"] == spec.agent_name), None
    )
    if agent_config is None:
        raise JobValidationError(f"Unknown agent: {spec.agent_name}")
    group = {**raw_group, "path": group_path, "agents_full": agents}
    agent_dir = get_agent_dir(group, spec.agent_name)
    if not agent_dir.is_dir():
        raise JobValidationError(f"Agent directory not found: {agent_dir}")
    integration = detect_integration(agent_dir) or get_integration(
        agent_config.get(
            "integration", raw_group.get("default_integration", "claude-code")
        )
    )
    if not integration.supports_execution:
        raise JobValidationError(
            f"Integration '{integration.name}' does not support execution"
        )
    if hasattr(integration, "with_config") and agent_config.get("integration_config"):
        integration = integration.with_config(agent_config["integration_config"])
    dispatch = raw_group.get("dispatch", {})
    configured = dispatch.get("timeout", 1800)
    agent_dispatch = dispatch.get("agents", {}).get(spec.agent_name, {})
    if isinstance(agent_dispatch, dict):
        configured = agent_dispatch.get("timeout", configured)
    timeout = spec.timeout_override if spec.timeout_override is not None else configured
    return ResolvedJobContext(
        config=config,
        group=group,
        group_path=group_path,
        agent_config=agent_config,
        agent_dir=agent_dir,
        integration=integration,
        timeout=timeout,
        sandbox_root=get_sandbox_root(raw_group),
    )