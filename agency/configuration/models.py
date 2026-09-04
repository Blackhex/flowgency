from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .issues import ValidationFailed, ValidationIssue

MemoryScope = Literal["run", "routine", "agent", "team", "channel"]
PermissionMode = Literal["restricted", "unrestricted"]
ScheduleKind = Literal["at", "every"]
PromptScope = Literal["blueprint", "instance"]

_IDENTIFIER_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
CONFIG_SCHEMA_VERSION = 6
_ROOT_KEYS = {"schema_version", "agency", "memory", "teams"}


class AgencyDispatch(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)
    interval: int = 15


class AgencyJobs(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)
    pool: int = Field(default=4, ge=1)


class AgencySettings(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)
    title: str = "Agency"
    default_team: str = ""
    ai_backend: str = "claude-code"
    dispatch: AgencyDispatch = Field(default_factory=AgencyDispatch)
    jobs: AgencyJobs = Field(default_factory=AgencyJobs)
    agent_library: Path | None = None
    compilation_cache: Path | None = None
    memory_store: Path | None = None
    prompt_store: Path | None = None


class MemoryChannel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    display_name: str


class MemoryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    channels: dict[str, MemoryChannel] = Field(default_factory=dict)


class MemorySelector(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    scope: MemoryScope
    channel: str | None = None


class PermissionRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    path: Path | None = None
    # None means every tool the integration offers; () means none.
    tools: tuple[str, ...] | None = None


class RuntimePermissions(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    mode: PermissionMode = "unrestricted"
    rules: tuple[PermissionRule, ...] = ()


class AgentRuntime(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)
    timeout: int = 1800
    permissions: RuntimePermissions = Field(default_factory=RuntimePermissions)


class AgentIdentity(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)
    display_name: str = ""
    title: str = ""
    emoji: str = ""


class ScheduleRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    at: str | None = None
    every: str | None = None
    catch_up: str | None = None


class PromptSelector(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    scope: PromptScope
    name: str


class Routine(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)
    id: str
    prompt: PromptSelector
    arguments: tuple[str, ...] = ()
    schedule: ScheduleRule
    memory: MemorySelector | None = None
    enabled: bool = True


class AgentInstance(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)
    name: str
    blueprint: str
    integration: str
    integration_config: dict[str, Any] = Field(default_factory=dict)
    identity: AgentIdentity = Field(default_factory=AgentIdentity)
    runtime: AgentRuntime = Field(default_factory=AgentRuntime)
    default_memory: MemorySelector | None = None
    prompts: tuple[str, ...] = ()
    routines: tuple[Routine, ...] = ()


class TeamDispatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    enabled: bool = False


class WorkspaceConfig(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)
    name: str
    type: str
    config: dict[str, Any] = Field(default_factory=dict)


class TeamRuntime(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)
    timeout: int = 1800
    permissions: RuntimePermissions = Field(default_factory=RuntimePermissions)


class TeamConfig(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)
    name: str
    workspace_path: Path
    path: Path
    default_integration: str
    runtime: TeamRuntime = Field(default_factory=TeamRuntime)
    dispatch: TeamDispatch = Field(default_factory=TeamDispatch)
    agents: dict[str, AgentInstance] = Field(default_factory=dict)
    workspaces: tuple[WorkspaceConfig, ...] = ()


class AgencyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[6]
    agency: AgencySettings
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    teams: dict[str, TeamConfig]


class ParsedConfig(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)
    raw: dict[str, Any]
    resolved: AgencyConfig

    @property
    def agency(self) -> AgencySettings:
        return self.resolved.agency

    @property
    def memory(self) -> MemoryConfig:
        return self.resolved.memory

    @property
    def teams(self) -> dict[str, TeamConfig]:
        return self.resolved.teams


@dataclass(frozen=True)
class _PipelineResult:
    parsed: ParsedConfig | None
    issues: tuple[ValidationIssue, ...]


def _path_from_config(value: Any, config_dir: Path) -> Path:
    path = Path(str(value))
    if path.is_absolute():
        return path.resolve()
    return (config_dir / path).resolve()


def _build_issue(code: str, scope: str, field: str, message: str, hint: str) -> ValidationIssue:
    return ValidationIssue(code=code, scope=scope, field=field, message=message, corrective_hint=hint)


def _shape_scope(field: str) -> str:
    if "[" in field and "." not in field:
        return field.split("[", 1)[0]
    if "." in field:
        return field.rsplit(".", 1)[0]
    return field


def _shape_issue(field: str, expected: str) -> ValidationIssue:
    return _build_issue(
        code="invalid-field-shape",
        scope=_shape_scope(field),
        field=field,
        message=f"{field} must be a {expected}.",
        hint=f"Set {field} to a {expected} value.",
    )


def _routine_entry_issue(field: str) -> ValidationIssue:
    return _build_issue(
        code="invalid-routine-entry",
        scope=field,
        field=field,
        message="Routine entry must be a mapping.",
        hint="Define each routine as a mapping with id, skill, and schedule.",
    )


def _validate_routine_arguments(arguments: Any, scope: str, field: str) -> list[ValidationIssue]:
    if not _is_list(arguments):
        return [_shape_issue(field, "list")]
    issues: list[ValidationIssue] = []
    for index, value in enumerate(arguments):
        if not isinstance(value, str):
            issues.append(_shape_issue(f"{field}[{index}]", "string"))
            continue
        if value == "":
            issues.append(
                _build_issue(
                    code="invalid-routine-argument",
                    scope=scope,
                    field=f"{field}[{index}]",
                    message="Routine arguments must be non-empty strings.",
                    hint="Remove empty arguments and keep each CLI argument as an explicit string.",
                )
            )
    return issues


def _is_mapping(value: Any) -> bool:
    return isinstance(value, Mapping)


def _is_list(value: Any) -> bool:
    return isinstance(value, list)


def _mapping_or_none(value: Any) -> Mapping[str, Any] | None:
    if _is_mapping(value):
        return value
    return None


def _collect_shape_issues(raw: dict[str, Any]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    agency = raw.get("agency")
    if agency is not None and not _is_mapping(agency):
        issues.append(_shape_issue("agency", "mapping"))

    memory = raw.get("memory")
    memory_map = _mapping_or_none(memory)
    if memory is not None and memory_map is None:
        issues.append(_shape_issue("memory", "mapping"))
    channels = None
    if memory_map is not None:
        channels = memory_map.get("channels")
        channels_map = _mapping_or_none(channels)
        if channels is not None and channels_map is None:
            issues.append(_shape_issue("memory.channels", "mapping"))
        elif channels_map is not None:
            for channel_name, channel in channels_map.items():
                if not _is_mapping(channel):
                    issues.append(_shape_issue(f"memory.channels.{channel_name}", "mapping"))

    teams = raw.get("teams")
    teams_map = _mapping_or_none(teams)
    if teams_map is None:
        issues.append(_shape_issue("teams", "mapping"))
        return issues

    for team_name, team in teams_map.items():
        team_field = f"teams.{team_name}"
        team_map = _mapping_or_none(team)
        if team_map is None:
            issues.append(_shape_issue(team_field, "mapping"))
            continue

        runtime = team_map.get("runtime")
        runtime_map = _mapping_or_none(runtime)
        if runtime is not None and runtime_map is None:
            issues.append(_shape_issue(f"{team_field}.runtime", "mapping"))

        dispatch = team_map.get("dispatch")
        if dispatch is not None and not _is_mapping(dispatch):
            issues.append(_shape_issue(f"{team_field}.dispatch", "mapping"))
        elif _is_mapping(dispatch) and "agents" in dispatch:
            issues.append(
                _build_issue(
                    code="team-dispatch-agents-not-supported",
                    scope=f"teams.{team_name}.dispatch",
                    field=f"{team_field}.dispatch.agents",
                    message="Team dispatch schedules belong on agent routines and are not supported in the current config.",
                    hint="Move schedules into each agent's routines on the configured instances.",
                )
            )

        workspaces = team_map.get("workspaces")
        if workspaces is not None and not _is_list(workspaces):
            issues.append(_shape_issue(f"{team_field}.workspaces", "list"))

        agents = team_map.get("agents")
        agents_list = None
        if agents is not None:
            if not _is_list(agents):
                issues.append(_shape_issue(f"{team_field}.agents", "list"))
            else:
                agents_list = agents
        if agents_list is None:
            continue

        for index, agent in enumerate(agents_list):
            agent_field = f"{team_field}.agents[{index}]"
            agent_map = _mapping_or_none(agent)
            if agent_map is None:
                continue

            identity = agent_map.get("identity")
            if identity is not None and not _is_mapping(identity):
                issues.append(_shape_issue(f"{agent_field}.identity", "mapping"))

            runtime = agent_map.get("runtime")
            runtime_map = _mapping_or_none(runtime)
            if runtime is not None and runtime_map is None:
                issues.append(_shape_issue(f"{agent_field}.runtime", "mapping"))

            default_memory = agent_map.get("default_memory")
            if default_memory is not None and not _is_mapping(default_memory):
                issues.append(_shape_issue(f"{agent_field}.default_memory", "mapping"))

            routines = agent_map.get("routines")
            routines_list = None
            if routines is not None:
                if not _is_list(routines):
                    issues.append(_shape_issue(f"{agent_field}.routines", "list"))
                else:
                    routines_list = routines
            if routines_list is None:
                continue

            for routine_index, routine in enumerate(routines_list):
                routine_field = f"{agent_field}.routines[{routine_index}]"
                routine_map = _mapping_or_none(routine)
                if routine_map is None:
                    issues.append(_routine_entry_issue(routine_field))
                    continue
                schedule = routine_map.get("schedule")
                if schedule is not None and not _is_mapping(schedule):
                    issues.append(
                        _build_issue(
                            code="invalid-dispatch-rule",
                            scope=routine_field,
                            field=f"{routine_field}.schedule",
                            message="Dispatch rule must be a mapping with exactly one of at or every.",
                            hint="Set schedule to a mapping containing either at or every.",
                        )
                    )
                memory = routine_map.get("memory")
                if memory is not None and not _is_mapping(memory):
                    issues.append(_shape_issue(f"{routine_field}.memory", "mapping"))
                arguments = routine_map.get("arguments")
                if arguments is not None and not _is_list(arguments):
                    issues.append(_shape_issue(f"{routine_field}.arguments", "list"))

    return issues


def _collect_pydantic_issues(error: ValidationError) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for entry in error.errors():
        location = ".".join(str(part) for part in entry.get("loc", ())) or "config"
        message = entry.get("msg", "Invalid configuration")
        issues.append(
            _build_issue(
                code="invalid-config",
                scope=location,
                field=location,
                message=message,
                hint="Fix the reported field shape or value.",
            )
        )
    return issues


def _validate_identifier(kind: str, value: str, scope: str) -> ValidationIssue | None:
    import re

    if not re.match(_IDENTIFIER_PATTERN, value):
        return _build_issue(
            code=f"invalid-{kind}-name",
            scope=scope,
            field=kind,
            message=f"Invalid {kind} identifier: {value}",
            hint="Use a lowercase stable slug containing only letters, digits, and single hyphen separators.",
        )
    return None


def _validate_rule(rule: Any, scope: str) -> ValidationIssue | None:
    if not isinstance(rule, dict):
        return _build_issue(
            code="invalid-dispatch-rule",
            scope=scope,
            field="schedule",
            message="Dispatch rule must be a mapping with exactly one of at or every.",
            hint="Set schedule to a mapping containing either at or every.",
        )
    has_at = bool(rule.get("at"))
    has_every = bool(rule.get("every"))
    if has_at == has_every:
        return _build_issue(
            code="invalid-dispatch-rule",
            scope=scope,
            field="schedule",
            message="Dispatch rule must define exactly one of at or every.",
            hint="Set either at or every, but not both and not neither.",
        )

    from agency.dispatch.schedule import parse_catch_up

    catch_up = rule.get("catch_up")
    if catch_up is not None and parse_catch_up(str(catch_up)) is None:
        return _build_issue(
            code="invalid-dispatch-rule",
            scope=scope,
            field="schedule.catch_up",
            message=f"Invalid catch_up value: {catch_up}",
            hint="Use none, today, always, or a duration such as 30m, 8h, or 7d.",
        )
    return None


def _validate_memory_selector(
    selector: Any,
    scope: str,
    allow_routine: bool,
    field_prefix: str = "default_memory",
    declared_channels: set[str] | None = None,
) -> ValidationIssue | None:
    if not _is_mapping(selector):
        return _shape_issue(field_prefix, "mapping")
    selected_scope = selector.get("scope")
    if selected_scope == "routine" and not allow_routine:
        return _build_issue(
            code="invalid-memory-scope",
            scope=scope,
            field=f"{field_prefix}.scope",
            message="Agent default memory cannot use routine scope.",
            hint="Choose run, agent, team, or channel for an agent default memory selector.",
        )
    if selected_scope == "channel" and not selector.get("channel"):
        return _build_issue(
            code="missing-memory-channel",
            scope=scope,
            field=f"{field_prefix}.channel",
            message="Channel memory selectors require a channel.",
            hint="Set channel to a declared memory channel key.",
        )
    if selected_scope != "channel" and selector.get("channel") is not None:
        return _build_issue(
            code="invalid-memory-selector-shape",
            scope=scope,
            field=f"{field_prefix}.channel",
            message="Only channel memory selectors may define a channel.",
            hint="Remove channel unless scope is channel.",
        )
    if selected_scope == "channel" and declared_channels is not None:
        channel = selector.get("channel")
        if channel and channel not in declared_channels:
            return _build_issue(
                code="missing-memory-channel",
                scope=scope,
                field=f"{field_prefix}.channel",
                message=f"Unknown memory channel: {channel}",
                hint="Declare the channel under memory.channels or point to an existing key.",
            )
    return None


def _validate_blueprint(agent: Any, scope: str) -> ValidationIssue | None:
    if not _is_mapping(agent):
        return None
    blueprint = agent.get("blueprint")
    if not isinstance(blueprint, str) or not blueprint.strip():
        return _build_issue(
            code="missing-blueprint",
            scope=scope,
            field="blueprint",
            message="Blueprint is required.",
            hint="Set blueprint to a non-empty identifier for the agent instance.",
        )
    return _validate_identifier("blueprint", blueprint, scope)


_SUPERSEDED_V4_RUNTIME_KEYS = ("sandbox", "tools")


def _reject_superseded_keys(
    mapping: dict[str, Any],
    scope: str,
    prefix: str,
    keys: tuple[str, ...] = _SUPERSEDED_V4_RUNTIME_KEYS,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for key in keys:
        if key in mapping:
            issues.append(
                _build_issue(
                    code="superseded-config-key",
                    scope=scope,
                    field=f"{prefix}.{key}",
                    message=(
                        f"'{key}' is a schema_version 4 key that is not "
                        f"recognised in version 6."
                    ),
                    hint=(
                        "Remove the key and use runtime.permissions instead. "
                        "Run `christag-agency config migrate` to convert "
                        "a version-4 configuration."
                    ),
                )
            )
    return issues


def _validate_team_runtime(runtime: Any, scope: str) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if not _is_mapping(runtime):
        return issues
    issues.extend(_reject_superseded_keys(runtime, scope, f"{scope}.runtime"))
    return issues


def _validate_agent_runtime(runtime: Any, scope: str) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if not _is_mapping(runtime):
        return issues
    issues.extend(_reject_superseded_keys(runtime, scope, f"{scope}.runtime"))
    return issues


def _validate_default_team(default_team: Any, teams: Mapping[str, Any]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if default_team is None or default_team == "":
        return issues
    if not isinstance(default_team, str):
        issues.append(
            _build_issue(
                code="invalid-team-name",
                scope="agency",
                field="agency.default_team",
                message=f"Invalid team identifier: {default_team}",
                hint="Use a lowercase stable slug containing only letters, digits, and single hyphen separators.",
            )
        )
        return issues
    identifier_issue = _validate_identifier("team", default_team, "agency")
    if identifier_issue is not None:
        issues.append(
            ValidationIssue(
                code=identifier_issue.code,
                scope=identifier_issue.scope,
                field="agency.default_team",
                message=identifier_issue.message,
                corrective_hint=identifier_issue.corrective_hint,
            )
        )
        return issues
    if default_team not in teams:
        issues.append(
            _build_issue(
                code="missing-default-team",
                scope="agency",
                field="agency.default_team",
                message=f"Default team is not declared: {default_team}",
                hint="Set agency.default_team to a declared team key or leave it blank when omission is intended.",
            )
        )
    return issues
    return None


def _validate_raw_config(raw: dict[str, Any], config_path: Path) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if raw.get("schema_version") != CONFIG_SCHEMA_VERSION:
        issues.append(
            _build_issue(
                code="unsupported-schema-version",
                scope="config",
                field="schema_version",
                message="schema_version must be 6.",
                hint=(
                    "Rewrite the configuration to schema_version 6 with "
                    "teams instead of groups."
                ),
            )
        )
    agency = raw.get("agency") if _is_mapping(raw.get("agency")) else {}
    memory = raw.get("memory") if _is_mapping(raw.get("memory")) else {}
    channels = memory.get("channels") if _is_mapping(memory.get("channels")) else {}
    declared_channels = set(channels)
    for channel_name in channels:
        identifier_issue = _validate_identifier("channel", channel_name, f"memory.channels.{channel_name}")
        if identifier_issue:
            issues.append(identifier_issue)
    for field_name in ("agent_library", "compilation_cache", "memory_store", "prompt_store"):
        if not str(agency.get(field_name, "")).strip():
            issues.append(
                _build_issue(
                    code=f"missing-{field_name}",
                    scope="agency",
                    field=field_name,
                    message=f"{field_name} is required.",
                    hint=f"Set agency.{field_name} relative to config.yaml.",
                )
            )
    teams = raw.get("teams") if _is_mapping(raw.get("teams")) else {}
    issues.extend(_validate_default_team(agency.get("default_team", ""), teams))
    for team_name, team in teams.items():
        identifier_issue = _validate_identifier("team", team_name, f"teams.{team_name}")
        if identifier_issue:
            issues.append(identifier_issue)
        if not _is_mapping(team):
            continue
        if not str(team.get("default_integration", "")).strip():
            issues.append(
                _build_issue(
                    code="missing-default-integration",
                    scope=f"teams.{team_name}",
                    field="default_integration",
                    message="Team default integration is required.",
                    hint="Set team.default_integration to a non-empty integration name.",
                )
            )
        for field_name in ("workspace_path", "path"):
            if not str(team.get(field_name, "")).strip():
                issues.append(
                    _build_issue(
                        code=f"missing-team-{field_name.replace('_', '-')}",
                        scope=f"teams.{team_name}",
                        field=f"teams.{team_name}.{field_name}",
                        message=f"Team {field_name} is required.",
                        hint=f"Set teams.{team_name}.{field_name} relative to config.yaml.",
                    )
                )
        runtime = team.get("runtime") or {}
        issues.extend(_validate_team_runtime(runtime, f"teams.{team_name}"))
        agents = team.get("agents") if _is_list(team.get("agents")) else []
        seen_agents: set[str] = set()
        for index, agent in enumerate(agents):
            if not isinstance(agent, dict):
                issues.append(
                    _build_issue(
                        code="invalid-agent-entry",
                        scope=f"teams.{team_name}.agents[{index}]",
                        field=f"agents[{index}]",
                        message="Agent entry must be a mapping.",
                        hint="Define each agent as a mapping with name, blueprint, and integration.",
                    )
                )
                continue
            name = agent.get("name")
            if not isinstance(name, str) or not name.strip():
                issues.append(
                    _build_issue(
                        code="missing-agent-name",
                        scope=f"teams.{team_name}.agents[{index}]",
                        field=f"agents[{index}].name",
                        message="Agent name is required.",
                        hint="Set agent.name to a non-empty identifier.",
                    )
                )
                continue
            identifier_issue = _validate_identifier(
                "agent", name, f"teams.{team_name}.agents.{name or '<unknown>'}"
            )
            if identifier_issue:
                issues.append(identifier_issue)
            if name in seen_agents:
                issues.append(
                    _build_issue(
                        code="duplicate-agent-name",
                        scope=f"teams.{team_name}",
                        field="agents",
                        message=f"Duplicate agent name: {name}",
                        hint="Give each agent a unique name within the team.",
                    )
                )
            seen_agents.add(name)
            blueprint_issue = _validate_blueprint(agent, f"teams.{team_name}.agents.{name or '<unknown>'}")
            if blueprint_issue:
                issues.append(blueprint_issue)
            if not str(agent.get("integration", "")).strip():
                issues.append(
                    _build_issue(
                        code="missing-explicit-integration",
                        scope=f"teams.{team_name}.agents.{name or '<unknown>'}",
                        field="integration",
                        message="Each agent must declare an explicit integration.",
                        hint="Set integration on every agent instance.",
                    )
                )
            default_memory = agent.get("default_memory") or {}
            if default_memory:
                issue = _validate_memory_selector(
                    default_memory,
                    f"teams.{team_name}.agents.{name or '<unknown>'}",
                    allow_routine=False,
                    field_prefix="default_memory",
                    declared_channels=declared_channels,
                )
                if issue:
                    issues.append(issue)
            routines = agent.get("routines") if _is_list(agent.get("routines")) else []
            seen_routines: set[str] = set()
            for routine_index, routine in enumerate(routines):
                if not isinstance(routine, dict):
                    issues.append(
                        _routine_entry_issue(f"teams.{team_name}.agents[{index}].routines[{routine_index}]")
                    )
                    continue
                routine_id = routine.get("id")
                if isinstance(routine_id, str) and routine_id in seen_routines:
                    issues.append(
                        _build_issue(
                            code="duplicate-routine-name",
                            scope=f"teams.{team_name}.agents.{name or '<unknown>'}",
                            field="routines",
                            message=f"Duplicate routine id: {routine_id}",
                            hint="Give each routine a unique id within the agent.",
                        )
                    )
                if isinstance(routine_id, str):
                    identifier_issue = _validate_identifier(
                        "routine", routine_id, f"teams.{team_name}.agents.{name or '<unknown>'}"
                    )
                    if identifier_issue:
                        issues.append(identifier_issue)
                    seen_routines.add(routine_id)
                schedule = routine.get("schedule") or {}
                issue = _validate_rule(schedule, f"teams.{team_name}.agents.{name or '<unknown>'}")
                if issue:
                    issues.append(issue)
                memory = routine.get("memory")
                if memory is not None:
                    issue = _validate_memory_selector(
                        memory,
                        f"teams.{team_name}.agents.{name or '<unknown>'}",
                        allow_routine=True,
                        field_prefix="memory",
                        declared_channels=declared_channels,
                    )
                    if issue:
                        issues.append(issue)
                arguments = routine.get("arguments")
                if arguments is not None:
                    issues.extend(
                        _validate_routine_arguments(
                            arguments,
                            f"teams.{team_name}.agents.{name or '<unknown>'}",
                            f"teams.{team_name}.agents[{index}].routines[{routine_index}].arguments",
                        )
                    )
            runtime = agent.get("runtime") or {}
            issues.extend(_validate_agent_runtime(runtime, f"teams.{team_name}.agents.{name or '<unknown>'}"))
            if "capabilities" in agent:
                issues.extend(
                    _reject_superseded_keys(
                        agent, f"teams.{team_name}.agents.{name or '<unknown>'}",
                        f"teams.{team_name}.agents.{name or '<unknown>'}",
                        keys=("capabilities",),
                    )
                )
        dispatch = team.get("dispatch") if _is_mapping(team.get("dispatch")) else {}
        if _is_mapping(dispatch):
            for key in dispatch:
                if key not in {"enabled"} and key != "agents":
                    issues.append(
                        _build_issue(
                            code="invalid-config",
                            scope=f"teams.{team_name}.dispatch",
                            field=f"teams.{team_name}.dispatch.{key}",
                            message=f"Unknown team dispatch field: {key}",
                            hint="Remove the unsupported field or migrate it to a supported location.",
                        )
                    )
    return issues

def _sorted_issues(issues: list[ValidationIssue]) -> tuple[ValidationIssue, ...]:
    return tuple(sorted(issues, key=lambda issue: (issue.scope, issue.field, issue.code, issue.message)))


def _collect_schema_issues(raw: dict[str, Any]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for key in raw:
        if key not in _ROOT_KEYS:
            issues.append(
                _build_issue(
                    code="invalid-config",
                    scope="config",
                    field=key,
                    message="Extra inputs are not permitted.",
                    hint="Remove the unsupported top-level key.",
                )
            )
    return issues


def _prepare_runtime(runtime: Any, base_path: Path | None) -> dict[str, Any]:
    runtime_entry = dict(runtime) if _is_mapping(runtime) else {}
    if "sandbox" in runtime_entry:
        sandbox = dict(runtime_entry.get("sandbox") or {})
        if base_path is not None:
            if "roots" in sandbox:
                roots = []
                for root in sandbox.get("roots") or []:
                    roots.append(_path_from_config(root, base_path))
                sandbox["roots"] = tuple(roots)
            if "additional_roots" in sandbox:
                additional_roots = []
                for root in sandbox.get("additional_roots") or []:
                    additional_roots.append(_path_from_config(root, base_path))
                sandbox["additional_roots"] = tuple(additional_roots)
        runtime_entry["sandbox"] = sandbox
    if "tools" in runtime_entry:
        tools = dict(runtime_entry.get("tools") or {})
        if tools.get("names") is not None:
            tools["names"] = tuple(str(name) for name in tools.get("names") or ())
        runtime_entry["tools"] = tools
    return runtime_entry


def _resolve_permission_paths(runtime_entry: dict[str, Any], workspace_path: Path) -> None:
    """Resolve relative rule paths in permissions against workspace_path."""
    permissions = runtime_entry.get("permissions")
    if not _is_mapping(permissions):
        return
    permissions = dict(permissions)
    rules = permissions.get("rules")
    if rules is None:
        return
    resolved_rules = []
    for rule in rules:
        rule = dict(rule) if _is_mapping(rule) else {}
        if rule.get("path") is not None:
            rule["path"] = _path_from_config(rule["path"], workspace_path)
        resolved_rules.append(rule)
    permissions["rules"] = resolved_rules
    runtime_entry["permissions"] = permissions


def _prepare_for_model(raw: dict[str, Any], config_path: Path) -> dict[str, Any]:
    config_dir = config_path.parent.resolve()
    prepared = dict(raw)
    agency = dict(prepared.get("agency") or {})
    if agency.get("agent_library") is not None:
        agency["agent_library"] = _path_from_config(agency["agent_library"], config_dir)
    if agency.get("compilation_cache") is not None:
        agency["compilation_cache"] = _path_from_config(agency["compilation_cache"], config_dir)
    if agency.get("memory_store") is not None:
        agency["memory_store"] = _path_from_config(agency["memory_store"], config_dir)
    if agency.get("prompt_store") is not None:
        agency["prompt_store"] = _path_from_config(agency["prompt_store"], config_dir)
    prepared["agency"] = agency

    teams = dict(prepared.get("teams") or {})
    resolved_teams: dict[str, Any] = {}
    for team_name, team in teams.items():
        if not _is_mapping(team):
            continue
        resolved_team = dict(team)
        if resolved_team.get("workspace_path") is not None:
            resolved_team["workspace_path"] = _path_from_config(
                resolved_team["workspace_path"], config_dir
            )
        if resolved_team.get("path") is not None:
            resolved_team["path"] = _path_from_config(resolved_team["path"], config_dir)
        workspace_path = resolved_team.get("workspace_path")
        workspace_root = (
            Path(workspace_path) if workspace_path is not None else None
        )
        resolved_team["runtime"] = _prepare_runtime(
            resolved_team.get("runtime") or {}, workspace_root
        )
        _resolve_permission_paths(resolved_team["runtime"], Path(workspace_path) if workspace_path else config_dir)
        agents = {}
        for agent in resolved_team.get("agents") or []:
            if not isinstance(agent, dict):
                continue
            name = agent.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            agent_entry = dict(agent)
            agent_entry["runtime"] = _prepare_runtime(
                agent_entry.get("runtime") or {}, workspace_root
            )
            _resolve_permission_paths(agent_entry["runtime"], Path(workspace_path) if workspace_path else config_dir)
            if agent_entry.get("prompts") is not None:
                agent_entry["prompts"] = tuple(agent_entry.get("prompts") or ())
            if agent_entry.get("routines") is not None:
                routines = []
                for routine in agent_entry.get("routines") or []:
                    if not _is_mapping(routine):
                        continue
                    routine_entry = dict(routine)
                    if _is_mapping(routine_entry.get("memory")):
                        routine_entry["memory"] = dict(routine_entry["memory"])
                    if routine_entry.get("arguments") is not None:
                        routine_entry["arguments"] = tuple(routine_entry.get("arguments") or ())
                    routines.append(routine_entry)
                agent_entry["routines"] = tuple(routines)
            agents[name] = agent_entry
        resolved_team["agents"] = agents
        if resolved_team.get("workspaces") is not None:
            resolved_team["workspaces"] = tuple(resolved_team.get("workspaces") or ())
        resolved_teams[team_name] = resolved_team
    prepared["teams"] = resolved_teams
    return prepared


def _collect_post_parse_issues(parsed: ParsedConfig) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for field_name in ("agent_library", "compilation_cache", "memory_store", "prompt_store"):
        value = getattr(parsed.agency, field_name)
        if value is None:
            issues.append(
                _build_issue(
                    code=f"missing-{field_name}",
                    scope="agency",
                    field=field_name,
                    message=f"{field_name} is required.",
                    hint=f"Set agency.{field_name} relative to config.yaml.",
                )
            )
        elif not Path(value).is_absolute():
            issues.append(
                _build_issue(
                    code=f"invalid-{field_name}",
                    scope="agency",
                    field=field_name,
                    message=f"{field_name} must resolve to an absolute path.",
                    hint="Use a path relative to the config directory or an absolute path.",
                )
            )
    for team_name, team in parsed.teams.items():
        for field_name in ("workspace_path", "path"):
            if not getattr(team, field_name).is_absolute():
                issues.append(
                    _build_issue(
                        code=f"missing-team-{field_name.replace('_', '-')}",
                        scope=f"teams.{team_name}",
                        field=f"teams.{team_name}.{field_name}",
                        message=f"Team {field_name} is required.",
                        hint=f"Set teams.{team_name}.{field_name} relative to config.yaml.",
                    )
                )
    return issues


def _build_pipeline_result(raw: dict[str, Any], config_path: Path) -> _PipelineResult:
    issues: list[ValidationIssue] = []
    schema_issues = _collect_schema_issues(raw)
    shape_issues = _collect_shape_issues(raw)
    raw_issues = _validate_raw_config(raw, config_path)
    issues.extend(schema_issues)
    issues.extend(shape_issues)
    issues.extend(raw_issues)

    if schema_issues or shape_issues or raw_issues:
        return _PipelineResult(parsed=None, issues=_sorted_issues(issues))

    prepared = _prepare_for_model(raw, config_path)
    try:
        resolved = AgencyConfig.model_validate(prepared)
    except ValidationError as exc:
        issues.extend(_collect_pydantic_issues(exc))
        return _PipelineResult(parsed=None, issues=_sorted_issues(issues))

    parsed = ParsedConfig(raw=raw, resolved=resolved)
    issues.extend(_collect_post_parse_issues(parsed))
    sorted_issues = _sorted_issues(issues)
    return _PipelineResult(parsed=parsed if not sorted_issues else None, issues=sorted_issues)


def parse_config(raw: dict[str, Any], config_path: Path) -> ParsedConfig:
    result = _build_pipeline_result(raw, config_path)
    if result.issues:
        raise ValidationFailed(result.issues)
    assert result.parsed is not None
    return result.parsed


def validate_config(raw: dict[str, Any], config_path: Path) -> tuple[ValidationIssue, ...]:
    return _build_pipeline_result(raw, config_path).issues
