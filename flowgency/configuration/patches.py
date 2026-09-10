from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import os
from typing import Any, Literal

from .store import ConfigSnapshot, ConfigStore


_UNSET = object()


@dataclass(frozen=True)
class FlowgencySettingsPatch:
    title: str
    default_team: str
    ai_backend: str
    theme: str
    dispatch_interval: int
    agent_library: str
    compilation_cache: str
    memory_store: str
    prompt_store: str
    workflow_library: str | None = None


@dataclass(frozen=True)
class TeamSettingsPatch:
    name: str
    workspace_path: str
    path: str
    default_integration: str


@dataclass(frozen=True)
class TeamDispatchPatch:
    enabled: bool


@dataclass(frozen=True)
class TeamSettingsStatePatch:
    name: str
    workspace_path: str
    path: str
    default_integration: str
    runtime_timeout: int
    permission_mode: Literal["restricted", "unrestricted"] | None = None
    permission_rules: tuple[dict[str, Any], ...] | None = None
    dispatch_enabled: bool = False
    workspaces: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class TeamCreateStatePatch:
    name: str
    workspace_path: str
    path: str
    default_integration: str
    runtime_timeout: int
    permission_mode: Literal["restricted", "unrestricted"] = "unrestricted"
    permission_rules: tuple[dict[str, Any], ...] = ()
    dispatch_enabled: bool = False
    workspaces: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class AgentProfilePatch:
    display_name: str
    title: str
    emoji: str


@dataclass(frozen=True)
class AgentRuntimePatch:
    timeout: int | None
    integration_config: dict[str, Any] | object = _UNSET


def _teams(raw: dict[str, Any]) -> dict[str, Any]:
    teams = raw.setdefault("teams", {})
    if not isinstance(teams, dict):
        raise TypeError("teams must be a mapping")
    return teams


def _team(raw: dict[str, Any], team_id: str) -> dict[str, Any]:
    teams = _teams(raw)
    team = teams[team_id]
    if not isinstance(team, dict):
        raise TypeError(f"teams.{team_id} must be a mapping")
    return team


def _agents(team: dict[str, Any]) -> list[dict[str, Any]]:
    agents = team.setdefault("agents", [])
    if not isinstance(agents, list):
        raise TypeError("agents must be a list")
    return agents


def _agent(team: dict[str, Any], agent_id: str) -> dict[str, Any]:
    for entry in _agents(team):
        if isinstance(entry, dict) and entry.get("name") == agent_id:
            return entry
    raise KeyError(agent_id)


def _merge_mapping(target: dict[str, Any], updates: dict[str, Any]) -> None:
    target.update(updates)


def _normalized_library(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return os.path.normcase(os.path.normpath(text))


def _apply_workflow_library(
    raw: dict[str, Any], flowgency: dict[str, Any], workflow_library: str
) -> None:
    """Set the shared workflow library, fencing affected instances on real change.

    An explicit change advances ``context_generation`` for every configured
    workflow instance so a stale queued target cannot survive the move; an
    equivalent path spelling is treated as a no-op and preserves the fence.
    """
    previous = _normalized_library(flowgency.get("workflow_library"))
    flowgency["workflow_library"] = workflow_library
    if previous == _normalized_library(workflow_library):
        return
    teams = raw.get("teams")
    if not isinstance(teams, dict):
        return
    for team in teams.values():
        if not isinstance(team, dict):
            continue
        workflows = team.get("workflows")
        if not isinstance(workflows, dict):
            continue
        for workflow in workflows.values():
            if isinstance(workflow, dict):
                workflow["context_generation"] = (
                    int(workflow.get("context_generation", 0)) + 1
                )


def _clear_known_keys(mapping: dict[str, Any], keys: tuple[str, ...]) -> None:
    for key in keys:
        mapping.pop(key, None)


def patch_flowgency_settings(
    store: ConfigStore,
    expected_revision: str,
    patch: FlowgencySettingsPatch,
) -> ConfigSnapshot:
    def apply(raw: dict[str, Any]) -> None:
        flowgency = raw.setdefault("flowgency", {})
        flowgency["title"] = patch.title
        flowgency["default_team"] = patch.default_team
        flowgency["ai_backend"] = patch.ai_backend
        flowgency["theme"] = patch.theme
        flowgency["agent_library"] = patch.agent_library
        flowgency["compilation_cache"] = patch.compilation_cache
        flowgency["memory_store"] = patch.memory_store
        flowgency["prompt_store"] = patch.prompt_store
        dispatch = flowgency.setdefault("dispatch", {})
        dispatch["interval"] = patch.dispatch_interval
        if patch.workflow_library is not None:
            _apply_workflow_library(raw, flowgency, patch.workflow_library)

    return store.patch(expected_revision, apply)


def create_team(
    store: ConfigStore,
    expected_revision: str,
    team_id: str,
    patch: TeamSettingsPatch,
) -> ConfigSnapshot:
    def apply(raw: dict[str, Any]) -> None:
        teams = _teams(raw)
        if team_id in teams:
            raise ValueError(f"Team already exists: {team_id}")
        teams[team_id] = {
            "name": patch.name,
            "workspace_path": patch.workspace_path,
            "path": patch.path,
            "default_integration": patch.default_integration,
            "agents": [],
        }

    return store.patch(expected_revision, apply)


def create_team_state(
    store: ConfigStore,
    expected_revision: str,
    team_id: str,
    patch: TeamCreateStatePatch,
) -> ConfigSnapshot:
    def apply(raw: dict[str, Any]) -> None:
        teams = _teams(raw)
        if team_id in teams:
            raise ValueError(f"Team already exists: {team_id}")
        teams[team_id] = {
            "name": patch.name,
            "workspace_path": patch.workspace_path,
            "path": patch.path,
            "default_integration": patch.default_integration,
            "permissions": {
                "mode": patch.permission_mode,
                "rules": list(deepcopy(patch.permission_rules)),
            },
            "runtime": {
                "timeout": patch.runtime_timeout,
            },
            "dispatch": {
                "enabled": patch.dispatch_enabled,
            },
            "workspaces": deepcopy(list(patch.workspaces)),
            "agents": [],
        }

    return store.patch(expected_revision, apply)


def patch_team_settings(
    store: ConfigStore,
    expected_revision: str,
    team_id: str,
    patch: TeamSettingsPatch,
) -> ConfigSnapshot:
    def apply(raw: dict[str, Any]) -> None:
        team = _team(raw, team_id)
        team["name"] = patch.name
        team["workspace_path"] = patch.workspace_path
        team["path"] = patch.path
        team["default_integration"] = patch.default_integration

    return store.patch(expected_revision, apply)


def patch_team_dispatch(
    store: ConfigStore,
    expected_revision: str,
    team_id: str,
    patch: TeamDispatchPatch,
) -> ConfigSnapshot:
    def apply(raw: dict[str, Any]) -> None:
        team = _team(raw, team_id)
        team["dispatch"] = {
            "enabled": patch.enabled,
        }

    return store.patch(expected_revision, apply)


def patch_team_settings_state(
    store: ConfigStore,
    expected_revision: str,
    team_id: str,
    patch: TeamSettingsStatePatch,
) -> ConfigSnapshot:
    def apply(raw: dict[str, Any]) -> None:
        team = _team(raw, team_id)
        team["name"] = patch.name
        team["workspace_path"] = patch.workspace_path
        team["path"] = patch.path
        team["default_integration"] = patch.default_integration

        runtime = team.setdefault("runtime", {})
        if not isinstance(runtime, dict):
            raise TypeError(f"teams.{team_id}.runtime must be a mapping")
        runtime["timeout"] = patch.runtime_timeout

        permissions = team.setdefault("permissions", {})
        if not isinstance(permissions, dict):
            raise TypeError(
                f"teams.{team_id}.permissions must be a mapping"
            )
        if patch.permission_mode is not None:
            permissions["mode"] = patch.permission_mode
        if patch.permission_rules is not None:
            permissions["rules"] = list(deepcopy(patch.permission_rules))

        dispatch = team.setdefault("dispatch", {})
        if not isinstance(dispatch, dict):
            raise TypeError(f"teams.{team_id}.dispatch must be a mapping")
        dispatch["enabled"] = patch.dispatch_enabled

        team["workspaces"] = deepcopy(list(patch.workspaces))

    return store.patch(expected_revision, apply)


def create_agent_instance(
    store: ConfigStore,
    expected_revision: str,
    team_id: str,
    agent: dict[str, Any],
) -> ConfigSnapshot:
    def apply(raw: dict[str, Any]) -> None:
        team = _team(raw, team_id)
        agents = _agents(team)
        agent_name = agent.get("name")
        for entry in agents:
            if isinstance(entry, dict) and entry.get("name") == agent_name:
                raise ValueError(f"Agent already exists: {agent_name}")
        agents.append(deepcopy(agent))

    return store.patch(expected_revision, apply)


def patch_agent_profile(
    store: ConfigStore,
    expected_revision: str,
    team_id: str,
    agent_id: str,
    patch: AgentProfilePatch,
) -> ConfigSnapshot:
    def apply(raw: dict[str, Any]) -> None:
        agent = _agent(_team(raw, team_id), agent_id)
        identity = agent.setdefault("identity", {})
        if not isinstance(identity, dict):
            raise TypeError(f"teams.{team_id}.agents.{agent_id}.identity must be a mapping")
        _merge_mapping(
            identity,
            {
                "display_name": patch.display_name,
                "title": patch.title,
                "emoji": patch.emoji,
            },
        )

    return store.patch(expected_revision, apply)


def patch_agent_runtime(
    store: ConfigStore,
    expected_revision: str,
    team_id: str,
    agent_id: str,
    patch: AgentRuntimePatch,
) -> ConfigSnapshot:
    def apply(raw: dict[str, Any]) -> None:
        agent = _agent(_team(raw, team_id), agent_id)
        runtime = agent.setdefault("runtime", {})
        if not isinstance(runtime, dict):
            raise TypeError(f"teams.{team_id}.agents.{agent_id}.runtime must be a mapping")
        if patch.timeout is None:
            _clear_known_keys(runtime, ("timeout",))
        else:
            runtime["timeout"] = patch.timeout
        if patch.integration_config is not _UNSET:
            integration_config = agent.get("integration_config")
            if integration_config is None:
                integration_config = {}
            if not isinstance(integration_config, dict):
                raise TypeError(
                    f"teams.{team_id}.agents.{agent_id}.integration_config must be a mapping"
                )
            merged = dict(integration_config)
            merged.update(deepcopy(patch.integration_config))
            agent["integration_config"] = merged

    return store.patch(expected_revision, apply)


def replace_agent_routines(
    store: ConfigStore,
    expected_revision: str,
    team_id: str,
    agent_id: str,
    routines: list[dict[str, Any]],
) -> ConfigSnapshot:
    def apply(raw: dict[str, Any]) -> None:
        agent = _agent(_team(raw, team_id), agent_id)
        agent["routines"] = deepcopy(routines)

    return store.patch(expected_revision, apply)


def register_agent_prompt(
    store: ConfigStore,
    expected_revision: str,
    team_id: str,
    agent_id: str,
    prompt_name: str,
) -> ConfigSnapshot:
    def apply(raw: dict[str, Any]) -> None:
        agent = _agent(_team(raw, team_id), agent_id)
        prompts = agent.setdefault("prompts", [])
        if not isinstance(prompts, list):
            raise TypeError(
                f"teams.{team_id}.agents.{agent_id}.prompts must be a list"
            )
        if prompt_name in prompts:
            raise ValueError(f"Prompt already registered: {prompt_name}")
        prompts.append(prompt_name)

    return store.patch(expected_revision, apply)


def unregister_agent_prompt(
    store: ConfigStore,
    expected_revision: str,
    team_id: str,
    agent_id: str,
    prompt_name: str,
) -> ConfigSnapshot:
    def apply(raw: dict[str, Any]) -> None:
        agent = _agent(_team(raw, team_id), agent_id)
        prompts = agent.setdefault("prompts", [])
        if not isinstance(prompts, list):
            raise TypeError(
                f"teams.{team_id}.agents.{agent_id}.prompts must be a list"
            )
        try:
            prompts.remove(prompt_name)
        except ValueError as exc:
            raise KeyError(prompt_name) from exc

    return store.patch(expected_revision, apply)


def remove_agent_instance(
    store: ConfigStore,
    expected_revision: str,
    team_id: str,
    agent_id: str,
) -> ConfigSnapshot:
    def apply(raw: dict[str, Any]) -> None:
        team = _team(raw, team_id)
        agents = _agents(team)
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
        flowgency = raw.setdefault("flowgency", {})
        dismissed = flowgency.get("tips_dismissed")
        if not isinstance(dismissed, list):
            dismissed = []
        if tip_id not in dismissed:
            dismissed.append(tip_id)
        flowgency["tips_dismissed"] = dismissed

    return store.patch(expected_revision, apply)


def hide_all_tips(
    store: ConfigStore,
    expected_revision: str,
) -> ConfigSnapshot:
    def apply(raw: dict[str, Any]) -> None:
        flowgency = raw.setdefault("flowgency", {})
        flowgency["show_tips"] = False

    return store.patch(expected_revision, apply)


def delete_team(
    store: ConfigStore,
    expected_revision: str,
    team_id: str,
) -> ConfigSnapshot:
    def apply(raw: dict[str, Any]) -> None:
        teams = _teams(raw)
        if team_id not in teams:
            raise KeyError(team_id)
        del teams[team_id]

        flowgency = raw.setdefault("flowgency", {})
        if flowgency.get("default_team") == team_id:
            flowgency["default_team"] = next(iter(teams), "")

    return store.patch(expected_revision, apply)
