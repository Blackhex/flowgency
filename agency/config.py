"""Shared config utilities for Agency."""


def normalize_agents(agents_list: list, default_integration: str = "claude-code") -> list[dict]:
    """Normalize agent list: bare strings become dicts with inherited integration."""
    normalized = []
    for entry in agents_list:
        if isinstance(entry, str):
            normalized.append({"name": entry, "integration": default_integration})
        elif isinstance(entry, dict):
            agent = dict(entry)  # shallow copy
            if "integration" not in agent:
                agent["integration"] = default_integration
            normalized.append(agent)
    return normalized


def agent_names(agents: list[dict]) -> list[str]:
    """Extract agent names from a normalized agents list."""
    return [a["name"] for a in agents]
