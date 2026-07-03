"""Shared config utilities for Agency."""

from pathlib import Path, PurePosixPath, PureWindowsPath


def _is_absolute_path(path_str: str) -> bool:
    """Check if a path string is absolute on any platform (POSIX or Windows).

    Config files authored on Linux use POSIX-style absolute paths (/shared/...),
    which are not recognized as absolute by WindowsPath. Accept either form so
    shared-agent paths resolve consistently across operating systems.
    """
    return PurePosixPath(path_str).is_absolute() or PureWindowsPath(path_str).is_absolute()


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


def get_agent_dir(g: dict, agent_name: str) -> Path:
    """Resolve an agent's directory. Checks for path override in config, falls back to group_path/name.

    External paths must be absolute. Relative paths in config are ignored (fall through to default).
    """
    for agent_info in g.get("agents_full", []):
        if agent_info["name"] == agent_name and "path" in agent_info:
            if _is_absolute_path(agent_info["path"]):
                return Path(agent_info["path"])
    return Path(g["path"]) / agent_name


def get_allowed_roots(g: dict) -> list[Path]:
    """Return allowed filesystem roots for a group: group path + any external agent paths."""
    roots = [Path(g["path"])]
    for agent_info in g.get("agents_full", []):
        if "path" in agent_info:
            roots.append(Path(agent_info["path"]))
    return roots


def get_sandbox_root(g: dict) -> Path | None:
    """Resolve a group's optional sandbox_root to an absolute Path.

    Absolute paths are used as-is. Relative paths are resolved against the
    group path. Returns None if unset/blank or if no group path is available
    to resolve a relative value.
    """
    raw = g.get("sandbox_root")
    if not raw or not str(raw).strip():
        return None
    p = Path(str(raw).strip())
    if _is_absolute_path(str(raw).strip()):
        return p
    base = g.get("path")
    if not base:
        return None
    return (Path(base) / p).resolve()


def find_agent_in_config(agents: list, agent_name: str) -> tuple[int, dict | str | None]:
    """Find an agent in a raw (non-normalized) config list. Returns (index, entry) or (-1, None)."""
    for i, entry in enumerate(agents):
        if isinstance(entry, str) and entry == agent_name:
            return i, entry
        elif isinstance(entry, dict) and entry.get("name") == agent_name:
            return i, entry
    return -1, None


def is_shared_agent(agents: list, agent_name: str) -> bool:
    """Check if an agent has an external path override (is shared)."""
    _, entry = find_agent_in_config(agents, agent_name)
    return isinstance(entry, dict) and "path" in entry
