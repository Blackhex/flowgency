from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Literal

from .store import ConfigSnapshot, ConfigStore

ToolMode = Literal["all", "allowlist", "none"]


@dataclass(frozen=True)
class ToolPolicy:
    mode: ToolMode
    names: tuple[str, ...] = ()


@dataclass(frozen=True)
class AgencySettingsPatch:
    title: str
    default_group: str
    ai_backend: str
    dispatch_interval: int
    agent_library: str
    compilation_cache: str
    memory_store: str


@dataclass(frozen=True)
class GroupSettingsPatch:
    name: str
    path: str
    default_integration: str


@dataclass(frozen=True)
class GroupDispatchPatch:
    enabled: bool
    daily_limit: int


@dataclass(frozen=True)
class AgentProfilePatch:
    display_name: str
    title: str
    emoji: str
    can_write: bool


@dataclass(frozen=True)
class AgentRuntimePatch:
    timeout: int | None
    additional_roots: tuple[str, ...]
    tools: ToolPolicy | None


def _groups(raw: dict[str, Any]) -> dict[str, Any]:
    groups = raw.setdefault("groups", {})
    if not isinstance(groups, dict):
        raise TypeError("groups must be a mapping")
    return groups


def _group(raw: dict[str, Any], group_id: str) -> dict[str, Any]:
    groups = _groups(raw)
    group = groups[group_id]
    if not isinstance(group, dict):
        raise TypeError(f"groups.{group_id} must be a mapping")
    return group


def _agents(group: dict[str, Any]) -> list[dict[str, Any]]:
    agents = group.setdefault("agents", [])
    if not isinstance(agents, list):
        raise TypeError("agents must be a list")
    return agents


def _agent(group: dict[str, Any], agent_id: str) -> dict[str, Any]:
    for entry in _agents(group):
        if isinstance(entry, dict) and entry.get("name") == agent_id:
            return entry
    raise KeyError(agent_id)


def _tool_mapping(policy: ToolPolicy | None) -> dict[str, Any]:
    if policy is None:
        return {"mode": "all"}
    mapping: dict[str, Any] = {"mode": policy.mode}
    if policy.mode == "allowlist":
        mapping["names"] = list(policy.names)
    return mapping


def patch_agency_settings(
    store: ConfigStore,
    expected_revision: str,
    patch: AgencySettingsPatch,
) -> ConfigSnapshot:
    def apply(raw: dict[str, Any]) -> None:
        agency = raw.setdefault("agency", {})
        agency["title"] = patch.title
        agency["default_group"] = patch.default_group
        agency["ai_backend"] = patch.ai_backend
        agency["agent_library"] = patch.agent_library
        agency["compilation_cache"] = patch.compilation_cache
        agency["memory_store"] = patch.memory_store
        dispatch = agency.setdefault("dispatch", {})
        dispatch["interval"] = patch.dispatch_interval

    return store.patch(expected_revision, apply)


def create_group(
    store: ConfigStore,
    expected_revision: str,
    group_id: str,
    patch: GroupSettingsPatch,
) -> ConfigSnapshot:
    def apply(raw: dict[str, Any]) -> None:
        groups = _groups(raw)
        if group_id in groups:
            raise ValueError(f"Group already exists: {group_id}")
        groups[group_id] = {
            "name": patch.name,
            "path": patch.path,
            "default_integration": patch.default_integration,
            "agents": [],
        }

    return store.patch(expected_revision, apply)


def patch_group_settings(
    store: ConfigStore,
    expected_revision: str,
    group_id: str,
    patch: GroupSettingsPatch,
) -> ConfigSnapshot:
    def apply(raw: dict[str, Any]) -> None:
        group = _group(raw, group_id)
        group["name"] = patch.name
        group["path"] = patch.path
        group["default_integration"] = patch.default_integration

    return store.patch(expected_revision, apply)


def patch_group_dispatch(
    store: ConfigStore,
    expected_revision: str,
    group_id: str,
    patch: GroupDispatchPatch,
) -> ConfigSnapshot:
    def apply(raw: dict[str, Any]) -> None:
        group = _group(raw, group_id)
        group["dispatch"] = {
            "enabled": patch.enabled,
            "daily_limit": patch.daily_limit,
        }

    return store.patch(expected_revision, apply)


def create_agent_instance(
    store: ConfigStore,
    expected_revision: str,
    group_id: str,
    agent: dict[str, Any],
) -> ConfigSnapshot:
    def apply(raw: dict[str, Any]) -> None:
        group = _group(raw, group_id)
        agents = _agents(group)
        agent_name = agent.get("name")
        for entry in agents:
            if isinstance(entry, dict) and entry.get("name") == agent_name:
                raise ValueError(f"Agent already exists: {agent_name}")
        agents.append(deepcopy(agent))

    return store.patch(expected_revision, apply)


def patch_agent_profile(
    store: ConfigStore,
    expected_revision: str,
    group_id: str,
    agent_id: str,
    patch: AgentProfilePatch,
) -> ConfigSnapshot:
    def apply(raw: dict[str, Any]) -> None:
        agent = _agent(_group(raw, group_id), agent_id)
        agent["identity"] = {
            "display_name": patch.display_name,
            "title": patch.title,
            "emoji": patch.emoji,
        }
        agent["capabilities"] = {"write": patch.can_write}

    return store.patch(expected_revision, apply)


def patch_agent_runtime(
    store: ConfigStore,
    expected_revision: str,
    group_id: str,
    agent_id: str,
    patch: AgentRuntimePatch,
) -> ConfigSnapshot:
    def apply(raw: dict[str, Any]) -> None:
        agent = _agent(_group(raw, group_id), agent_id)
        runtime: dict[str, Any] = {
            "sandbox": {
                "additional_roots": list(patch.additional_roots),
            },
            "tools": _tool_mapping(patch.tools),
        }
        if patch.timeout is not None:
            runtime["timeout"] = patch.timeout
        agent["runtime"] = runtime

    return store.patch(expected_revision, apply)


def replace_agent_routines(
    store: ConfigStore,
    expected_revision: str,
    group_id: str,
    agent_id: str,
    routines: list[dict[str, Any]],
) -> ConfigSnapshot:
    def apply(raw: dict[str, Any]) -> None:
        agent = _agent(_group(raw, group_id), agent_id)
        agent["routines"] = deepcopy(routines)

    return store.patch(expected_revision, apply)


def remove_agent_instance(
    store: ConfigStore,
    expected_revision: str,
    group_id: str,
    agent_id: str,
) -> ConfigSnapshot:
    def apply(raw: dict[str, Any]) -> None:
        group = _group(raw, group_id)
        agents = _agents(group)
        for index, entry in enumerate(agents):
            if isinstance(entry, dict) and entry.get("name") == agent_id:
                del agents[index]
                return
        raise KeyError(agent_id)

    return store.patch(expected_revision, apply)


def patch_memory_channels(
    store: ConfigStore,
    expected_revision: str,
    channels: dict[str, dict[str, Any]],
) -> ConfigSnapshot:
    def apply(raw: dict[str, Any]) -> None:
        memory = raw.setdefault("memory", {})
        memory["channels"] = deepcopy(channels)

    return store.patch(expected_revision, apply)
