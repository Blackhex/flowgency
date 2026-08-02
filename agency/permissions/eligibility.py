"""Whether an agent may be trusted to execute a decision.

Derived from the permission rules rather than stored, so the answer cannot
disagree with the policy the agent actually runs under.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Protocol


class _Rule(Protocol):
    path: Path | None
    tools: tuple[str, ...] | None


def grants_write_on(rules: Iterable[_Rule], workspace: Path | str) -> bool:
    """Return True iff the rule covering `workspace` itself grants write on it.

    The one statement of "this agent may change the project", so the answer the
    runtime acts on and the answer the decision form shows cannot drift. A rule
    on a subdirectory does not count: it grants write somewhere below the
    project, not over the project.
    """
    target = Path(workspace).resolve(strict=False)
    for rule in rules:
        if rule.path is None:
            continue
        if Path(rule.path).resolve(strict=False) != target:
            continue
        return rule.tools is None or "write" in rule.tools
    return False


def may_execute_decisions(config, group_key: str, agent_name: str) -> bool:
    """Return True iff the agent's effective policy grants write on the group workspace root."""
    # Deferred: resolving a policy imports the integrations package, which
    # imports this module to decide the Copilot sandbox's credential grants.
    from agency.configuration.effective import resolve_effective_policy
    from agency.configuration.issues import ValidationFailed

    group = config.groups.get(group_key)
    if group is None or agent_name not in group.agents:
        return False
    try:
        policy = resolve_effective_policy(config, group_key, agent_name)
    except (ValidationFailed, KeyError):
        return False

    return grants_write_on(policy.rules, group.workspace_path)
