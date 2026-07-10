"""Shared config utilities for Agency."""

from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

import yaml


def load_config_path(path: Path) -> dict:
    """Load configuration from an explicit filesystem path."""
    if not path.exists():
        return {"agency": {"title": "Agency", "default_group": ""}, "groups": {}}
    with path.open(encoding="utf-8") as config_file:
        return yaml.safe_load(config_file) or {}


@dataclass(frozen=True)
class SandboxSpec:
    """Least-privilege spec for a confined agent run.

    ``roots`` is the list of allowed filesystem roots (empty => --allow-all-paths).
    ``allowed_tools`` is the list of granted tool names (empty => --allow-all-tools).
    """

    roots: tuple[Path, ...] = ()
    allowed_tools: tuple[str, ...] = ()


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


def get_sandbox_root(g: dict) -> SandboxSpec | None:
    """Resolve a group's optional sandbox_root / allowed_tools into a SandboxSpec.

    ``sandbox_root`` accepts a single string or a list of strings. Absolute
    entries are used as-is; relative entries are resolved against the group path
    (and dropped if no group path is available). ``allowed_tools`` is an optional
    list of tool names. Returns None only when both roots and tools are empty,
    preserving the historical "fully unrestricted" None-equivalence.
    """
    raw = g.get("sandbox_root")
    if isinstance(raw, list):
        entries = raw
    elif raw is None:
        entries = []
    else:
        entries = [raw]

    base = g.get("path")
    roots: list[Path] = []
    for entry in entries:
        text = str(entry).strip()
        if not text:
            continue
        if _is_absolute_path(text):
            roots.append(Path(text))
        elif base:
            roots.append((Path(base) / Path(text)).resolve())

    tools = tuple(
        str(t).strip() for t in (g.get("allowed_tools") or []) if str(t).strip()
    )

    if not roots and not tools:
        return None
    return SandboxSpec(roots=tuple(roots), allowed_tools=tools)


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
