"""Translate a schema_version 4 document into version 5.

Works on plain mappings, never on the config models: a document the current
models reject must still be migratable.

The migration produces a valid, working version-5 configuration using a
straightforward mapping. It does not attempt to reproduce the version-4
effective policy. Constructs that cannot be represented faithfully under the
version-5 model without widening access are dropped rather than approximated.
The returned list names every such construct so the operator knows what to
review.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def _tools_for(tools: dict[str, Any]) -> list[str] | None:
    """None means omit the tools key, which in version 5 means every tool."""
    mode = tools.get("mode", "all")
    if mode == "all":
        return None
    if mode == "none":
        return []
    return list(tools.get("names", ()))


def _rule(path: str | None, tools: list[str] | None) -> dict[str, Any]:
    rule: dict[str, Any] = {}
    if path is not None:
        rule["path"] = path
    if tools is not None:
        rule["tools"] = tools
    return rule


def _group_permissions(runtime: dict[str, Any], roots: list[str]) -> dict[str, Any]:
    sandbox = runtime.get("sandbox", {}) or {}
    tools = _tools_for(runtime.get("tools", {}) or {})
    mode = sandbox.get("mode", "unrestricted")
    if mode == "unrestricted":
        return {"mode": "unrestricted", "rules": [_rule(None, tools)]}
    return {"mode": "restricted", "rules": [_rule(root, tools) for root in roots]}


def migrate_v4_to_v5(raw: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Migrate a version-4 configuration document to version 5.

    Returns ``(migrated_document, dropped)`` where ``dropped`` is a list of
    human-readable messages naming every v4 construct that was dropped because
    it cannot be represented in version 5 without widening access beyond what
    the version-4 configuration granted. Surface these to the operator.
    """
    version = raw.get("schema_version")
    if version == 5:
        raise ValueError("configuration is already at schema_version 5")
    if version != 4:
        raise ValueError(f"cannot migrate schema_version {version!r}")

    result = deepcopy(raw)
    result["schema_version"] = 5
    dropped: list[str] = []

    for group_id, group in (result.get("groups") or {}).items():
        runtime = group.setdefault("runtime", {})
        sandbox = runtime.get("sandbox", {}) or {}
        group_roots = [str(root) for root in (sandbox.get("roots") or ())]
        group_tools = _tools_for(runtime.get("tools", {}) or {})

        runtime["permissions"] = _group_permissions(runtime, group_roots)
        runtime.pop("sandbox", None)
        runtime.pop("tools", None)

        for agent in group.get("agents") or ():
            agent_name = agent.get("name", "<unnamed>")
            agent_runtime = agent.setdefault("runtime", {})
            agent_sandbox = agent_runtime.get("sandbox", {}) or {}
            extra = [str(root) for root in (agent_sandbox.get("additional_roots") or ())]

            if "tools" in agent_runtime:
                agent_tools = _tools_for(agent_runtime["tools"])
                # In v4, runtime.tools was a complete override for every path,
                # including the group's sandbox roots. Under v5's additive model,
                # instance rules cannot narrow the group's grant on those roots:
                # same-path union keeps the group's (possibly broader) tool set.
                # Keeping agent_tools only on additional_roots would silently
                # widen access on the group's roots beyond what v4 allowed.
                dropped.append(
                    f"group '{group_id}', agent '{agent_name}': runtime.tools"
                    f" — in v4 this was a complete override for all sandbox"
                    f" paths. Under v5's additive model it cannot restrict the"
                    f" group's tool grant on the group's roots without widening;"
                    f" dropped. Set permissions.rules manually to express the"
                    f" intended access."
                )
            else:
                agent_tools = group_tools

            rules = [_rule(root, agent_tools) for root in extra]

            capabilities = agent.pop("capabilities", {}) or {}
            if capabilities.get("write"):
                # In v4, capabilities.write granted executor eligibility only;
                # it did not extend the sandbox beyond the configured roots. The
                # v5 equivalent requires a write rule on workspace_path itself,
                # which may be a strict ancestor of the v4 sandbox roots, so
                # adding it would widen access. Dropped and reported.
                dropped.append(
                    f"group '{group_id}', agent '{agent_name}':"
                    f" capabilities.write — v5 executor eligibility requires a"
                    f" write rule on workspace_path, which may exceed the v4"
                    f" sandbox roots. Dropped to avoid widening. Add a write"
                    f" rule on the intended path to restore executor eligibility."
                )
            elif "write" in capabilities:
                # In v4, capabilities.write: false denied the agent workspace
                # writes. The v5 additive model cannot narrow the group's grant
                # from an instance, so the agent now inherits whatever the group
                # allows. Dropped and reported rather than silently widened.
                dropped.append(
                    f"group '{group_id}', agent '{agent_name}':"
                    f" capabilities.write: false — v5 instance rules are additive"
                    f" and cannot narrow the group's grant, so this agent now"
                    f" inherits the group's tools. Dropped. Narrow the group's"
                    f" rules, or move this agent to a group that grants less."
                )

            agent_runtime.pop("sandbox", None)
            agent_runtime.pop("tools", None)
            if rules:
                agent_runtime["permissions"] = {"rules": rules}
            elif not agent_runtime:
                agent.pop("runtime", None)

    return result, dropped
