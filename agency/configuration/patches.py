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
    theme: str
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
class GroupSettingsStatePatch:
    name: str
    path: str
    default_integration: str
    runtime_timeout: int
    sandbox_mode: Literal["restricted", "unrestricted"]
    sandbox_roots: tuple[str, ...] = ()
    tool_mode: ToolMode = "all"
    tool_names: tuple[str, ...] = ()
    dispatch_enabled: bool = False
    dispatch_daily_limit: int = 20
    workspaces: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class GroupCreateStatePatch:
    name: str
    path: str
    default_integration: str
    runtime_timeout: int
    sandbox_mode: Literal["restricted", "unrestricted"]
    sandbox_roots: tuple[str, ...] = ()
    tool_mode: ToolMode = "all"
    tool_names: tuple[str, ...] = ()
    dispatch_enabled: bool = False
    dispatch_daily_limit: int = 20
    workspaces: tuple[dict[str, Any], ...] = ()


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


def _merge_mapping(target: dict[str, Any], updates: dict[str, Any]) -> None:
    target.update(updates)


def _clear_known_keys(mapping: dict[str, Any], keys: tuple[str, ...]) -> None:
    for key in keys:
        mapping.pop(key, None)


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
        agency["theme"] = patch.theme
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


def create_group_state(
    store: ConfigStore,
    expected_revision: str,
    group_id: str,
    patch: GroupCreateStatePatch,
) -> ConfigSnapshot:
    def apply(raw: dict[str, Any]) -> None:
        groups = _groups(raw)
        if group_id in groups:
            raise ValueError(f"Group already exists: {group_id}")
        groups[group_id] = {
            "name": patch.name,
            "path": patch.path,
            "default_integration": patch.default_integration,
            "runtime": {
                "timeout": patch.runtime_timeout,
                "sandbox": {
                    "mode": patch.sandbox_mode,
                    "roots": list(patch.sandbox_roots),
                },
                "tools": {
                    "mode": patch.tool_mode,
                    "names": list(patch.tool_names),
                },
            },
            "dispatch": {
                "enabled": patch.dispatch_enabled,
                "daily_limit": patch.dispatch_daily_limit,
            },
            "workspaces": deepcopy(list(patch.workspaces)),
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


def patch_group_settings_state(
    store: ConfigStore,
    expected_revision: str,
    group_id: str,
    patch: GroupSettingsStatePatch,
) -> ConfigSnapshot:
    def apply(raw: dict[str, Any]) -> None:
        group = _group(raw, group_id)
        group["name"] = patch.name
        group["path"] = patch.path
        group["default_integration"] = patch.default_integration

        runtime = group.setdefault("runtime", {})
        if not isinstance(runtime, dict):
            raise TypeError(f"groups.{group_id}.runtime must be a mapping")
        runtime["timeout"] = patch.runtime_timeout

        sandbox = runtime.setdefault("sandbox", {})
        if not isinstance(sandbox, dict):
            raise TypeError(
                f"groups.{group_id}.runtime.sandbox must be a mapping"
            )
        sandbox["mode"] = patch.sandbox_mode
        sandbox["roots"] = list(patch.sandbox_roots)

        tools = runtime.setdefault("tools", {})
        if not isinstance(tools, dict):
            raise TypeError(
                f"groups.{group_id}.runtime.tools must be a mapping"
            )
        tools["mode"] = patch.tool_mode
        tools["names"] = list(patch.tool_names)

        dispatch = group.setdefault("dispatch", {})
        if not isinstance(dispatch, dict):
            raise TypeError(f"groups.{group_id}.dispatch must be a mapping")
        dispatch["enabled"] = patch.dispatch_enabled
        dispatch["daily_limit"] = patch.dispatch_daily_limit

        group["workspaces"] = deepcopy(list(patch.workspaces))

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
        identity = agent.setdefault("identity", {})
        if not isinstance(identity, dict):
            raise TypeError(f"groups.{group_id}.agents.{agent_id}.identity must be a mapping")
        _merge_mapping(
            identity,
            {
                "display_name": patch.display_name,
                "title": patch.title,
                "emoji": patch.emoji,
            },
        )
        capabilities = agent.setdefault("capabilities", {})
        if not isinstance(capabilities, dict):
            raise TypeError(f"groups.{group_id}.agents.{agent_id}.capabilities must be a mapping")
        _merge_mapping(capabilities, {"write": patch.can_write})

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
        runtime = agent.setdefault("runtime", {})
        if not isinstance(runtime, dict):
            raise TypeError(f"groups.{group_id}.agents.{agent_id}.runtime must be a mapping")
        if patch.timeout is None:
            _clear_known_keys(runtime, ("timeout",))
        else:
            runtime["timeout"] = patch.timeout

        sandbox = runtime.setdefault("sandbox", {})
        if not isinstance(sandbox, dict):
            raise TypeError(f"groups.{group_id}.agents.{agent_id}.runtime.sandbox must be a mapping")
        _merge_mapping(sandbox, {"additional_roots": list(patch.additional_roots)})

        tools = runtime.setdefault("tools", {})
        if not isinstance(tools, dict):
            raise TypeError(f"groups.{group_id}.agents.{agent_id}.runtime.tools must be a mapping")
        if patch.tools is None:
            _clear_known_keys(tools, ("mode", "names"))
        else:
            _merge_mapping(tools, _tool_mapping(patch.tools))
            if patch.tools.mode != "allowlist":
                tools.pop("names", None)

        if patch.additional_roots:
            sandbox["additional_roots"] = list(patch.additional_roots)
        else:
            _clear_known_keys(sandbox, ("additional_roots",))

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


def dismiss_tip(
    store: ConfigStore,
    expected_revision: str,
    tip_id: str,
) -> ConfigSnapshot:
    def apply(raw: dict[str, Any]) -> None:
        agency = raw.setdefault("agency", {})
        dismissed = agency.get("tips_dismissed")
        if not isinstance(dismissed, list):
            dismissed = []
        if tip_id not in dismissed:
            dismissed.append(tip_id)
        agency["tips_dismissed"] = dismissed

    return store.patch(expected_revision, apply)


def hide_all_tips(
    store: ConfigStore,
    expected_revision: str,
) -> ConfigSnapshot:
    def apply(raw: dict[str, Any]) -> None:
        agency = raw.setdefault("agency", {})
        agency["show_tips"] = False

    return store.patch(expected_revision, apply)


def delete_group(
    store: ConfigStore,
    expected_revision: str,
    group_id: str,
) -> ConfigSnapshot:
    def apply(raw: dict[str, Any]) -> None:
        groups = _groups(raw)
        if group_id not in groups:
            raise KeyError(group_id)
        del groups[group_id]

        agency = raw.setdefault("agency", {})
        if agency.get("default_group") == group_id:
            agency["default_group"] = next(iter(groups), "")

    return store.patch(expected_revision, apply)
