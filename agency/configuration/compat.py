from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import tempfile

import yaml


def load_config_path(path: Path) -> dict:
    """Load configuration from an explicit filesystem path."""
    if not path.exists():
        return {
            "agency": {"title": "Agency", "default_group": ""},
            "groups": {},
        }
    with path.open(encoding="utf-8") as config_file:
        return yaml.safe_load(config_file) or {}


def save_config_path(path: Path, config: dict) -> None:
    """Atomically write an Agency config to an explicit path."""
    destination = path.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_path = tempfile.mkstemp(
        dir=destination.parent,
        suffix=".yaml",
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as config_file:
            yaml.dump(
                config,
                config_file,
                default_flow_style=False,
                sort_keys=False,
            )
        os.replace(temporary_path, destination)
    except Exception:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)
        raise


@dataclass(frozen=True)
class SandboxSpec:
    """Least-privilege spec for a confined agent run."""

    roots: tuple[Path, ...] = ()
    allowed_tools: tuple[str, ...] = ()


def _is_absolute_path(path_str: str) -> bool:
    return PurePosixPath(path_str).is_absolute() or PureWindowsPath(
        path_str
    ).is_absolute()


def normalize_agents(
    agents_list: list,
    default_integration: str = "claude-code",
) -> list[dict]:
    """Normalize agent list: bare strings inherit the group integration."""
    normalized = []
    for entry in agents_list:
        if isinstance(entry, str):
            normalized.append(
                {"name": entry, "integration": default_integration}
            )
        elif isinstance(entry, dict):
            agent = dict(entry)
            if "integration" not in agent:
                agent["integration"] = default_integration
            normalized.append(agent)
    return normalized


def agent_names(agents: list[dict]) -> list[str]:
    return [agent["name"] for agent in agents]


def agent_can_write(agents: list[dict], agent_name: str) -> bool:
    for agent in agents:
        if agent.get("name") == agent_name:
            capabilities = agent.get("capabilities")
            return (
                isinstance(capabilities, dict)
                and capabilities.get("write") is True
            )
    return False


def get_agent_dir(g: dict, agent_name: str) -> Path:
    for agent_info in g.get("agents_full", []):
        if agent_info["name"] == agent_name and "path" in agent_info:
            if _is_absolute_path(agent_info["path"]):
                return Path(agent_info["path"])
    return Path(g["path"]) / agent_name


def get_allowed_roots(g: dict) -> list[Path]:
    roots = [Path(g["path"])]
    for agent_info in g.get("agents_full", []):
        if "path" in agent_info:
            roots.append(Path(agent_info["path"]))
    return roots


def get_sandbox_root(g: dict) -> SandboxSpec | None:
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
        str(tool).strip()
        for tool in (g.get("allowed_tools") or [])
        if str(tool).strip()
    )

    if not roots and not tools:
        return None
    return SandboxSpec(roots=tuple(roots), allowed_tools=tools)


def find_agent_in_config(
    agents: list, agent_name: str
) -> tuple[int, dict | str | None]:
    for index, entry in enumerate(agents):
        if isinstance(entry, str) and entry == agent_name:
            return index, entry
        if isinstance(entry, dict) and entry.get("name") == agent_name:
            return index, entry
    return -1, None


def is_shared_agent(agents: list, agent_name: str) -> bool:
    _, entry = find_agent_in_config(agents, agent_name)
    return isinstance(entry, dict) and "path" in entry
