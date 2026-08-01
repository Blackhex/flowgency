"""Whether an agent may be trusted to execute a decision.

Derived from the permission rules rather than stored, so the answer cannot
disagree with the policy the agent actually runs under.
"""

from __future__ import annotations

from pathlib import Path

from agency.configuration.effective import resolve_effective_policy
from agency.configuration.issues import ValidationFailed


def may_execute_decisions(config, group_key: str, agent_name: str) -> bool:
    """Return True iff the agent's effective policy grants write on the group workspace root."""
    group = config.groups.get(group_key)
    if group is None or agent_name not in group.agents:
        return False
    try:
        policy = resolve_effective_policy(config, group_key, agent_name)
    except (ValidationFailed, KeyError):
        return False

    workspace = Path(group.workspace_path).resolve(strict=False)
    for rule in policy.rules:
        if rule.path is None:
            continue
        if Path(rule.path).resolve(strict=False) != workspace:
            continue
        return rule.tools is None or "write" in rule.tools
    return False
