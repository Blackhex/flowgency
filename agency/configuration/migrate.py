"""Translate a schema_version 4 document into version 5.

Works on plain mappings, never on the config models: a document the current
models reject must still be migratable.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def _tools_for(tools: dict[str, Any]) -> list[str] | None:
    """None means "omit the key", which in version 5 means every tool."""
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


def _permissions(runtime: dict[str, Any], roots: list[str]) -> dict[str, Any]:
    sandbox = runtime.get("sandbox", {}) or {}
    tools = _tools_for(runtime.get("tools", {}) or {})
    mode = sandbox.get("mode", "unrestricted")
    if mode == "unrestricted":
        return {"mode": "unrestricted", "rules": [_rule(None, tools)]}
    return {"mode": "restricted", "rules": [_rule(root, tools) for root in roots]}


def migrate_v4_to_v5(raw: dict[str, Any]) -> dict[str, Any]:
    version = raw.get("schema_version")
    if version == 5:
        raise ValueError("configuration is already at schema_version 5")
    if version != 4:
        raise ValueError(f"cannot migrate schema_version {version!r}")

    result = deepcopy(raw)
    result["schema_version"] = 5

    for group in (result.get("groups") or {}).values():
        runtime = group.setdefault("runtime", {})
        sandbox = runtime.get("sandbox", {}) or {}
        group_roots = [str(root) for root in (sandbox.get("roots") or ())]
        # Read group_tools BEFORE popping "tools" from runtime below.
        group_tools = _tools_for(runtime.get("tools", {}) or {})
        workspace = str(group.get("workspace_path", ""))

        runtime["permissions"] = _permissions(runtime, group_roots)
        runtime.pop("sandbox", None)
        runtime.pop("tools", None)

        for agent in group.get("agents") or ():
            agent_runtime = agent.setdefault("runtime", {})
            agent_sandbox = agent_runtime.get("sandbox", {}) or {}
            extra = [str(root) for root in (agent_sandbox.get("additional_roots") or ())]
            agent_tools = (
                _tools_for(agent_runtime["tools"])
                if "tools" in agent_runtime
                else group_tools
            )

            rules = [_rule(root, agent_tools) for root in extra]

            capabilities = agent.pop("capabilities", {}) or {}
            if capabilities.get("write") and workspace:
                writable = list(agent_tools) if agent_tools is not None else None
                if writable is not None and "write" not in writable:
                    writable.append("write")
                rules.append(_rule(workspace, writable))

            agent_runtime.pop("sandbox", None)
            agent_runtime.pop("tools", None)
            if rules:
                agent_runtime["permissions"] = {"rules": rules}
            elif not agent_runtime:
                agent.pop("runtime", None)

    return result
