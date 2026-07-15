from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .issues import ValidationFailed, ValidationIssue

MemoryScope = Literal["run", "routine", "agent", "group", "channel"]
ToolMode = Literal["all", "allowlist", "none"]
SandboxMode = Literal["restricted", "unrestricted"]
ScheduleKind = Literal["at", "every"]

_IDENTIFIER_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"


class AgencyDispatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    interval: int = 15


class AgencySettings(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)
    title: str = "Agency"
    default_group: str = ""
    ai_backend: str = "claude-code"
    dispatch: AgencyDispatch = Field(default_factory=AgencyDispatch)
    agent_library: Path | None = None
    compilation_cache: Path | None = None
    memory_store: Path | None = None


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


class GroupRuntimeSandbox(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    mode: SandboxMode = "unrestricted"
    roots: tuple[Path, ...] = ()


class AgentRuntimeSandbox(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)
    mode: SandboxMode = "unrestricted"
    additional_roots: tuple[Path, ...] = ()


class RuntimeTools(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)
    mode: ToolMode = "all"
    names: tuple[str, ...] = ()


class AgentRuntime(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)
    timeout: int = 1800
    sandbox: AgentRuntimeSandbox = Field(default_factory=AgentRuntimeSandbox)
    tools: RuntimeTools = Field(default_factory=RuntimeTools)


class AgentIdentity(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)
    display_name: str = ""
    title: str = ""
    emoji: str = ""


class AgentCapabilities(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)
    write: bool = False


class ScheduleRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    at: str | None = None
    every: str | None = None


class Routine(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)
    id: str
    skill: str
    schedule: ScheduleRule
    memory: MemorySelector | None = None


class AgentInstance(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)
    name: str
    blueprint: str
    integration: str
    integration_config: dict[str, Any] = Field(default_factory=dict)
    identity: AgentIdentity = Field(default_factory=AgentIdentity)
    capabilities: AgentCapabilities = Field(default_factory=AgentCapabilities)
    runtime: AgentRuntime = Field(default_factory=AgentRuntime)
    default_memory: MemorySelector | None = None
    routines: tuple[Routine, ...] = ()


class GroupDispatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    enabled: bool = False
    daily_limit: int = 20


class WorkspaceConfig(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)
    name: str
    type: str
    config: dict[str, Any] = Field(default_factory=dict)


class GroupRuntime(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)
    timeout: int = 1800
    sandbox: GroupRuntimeSandbox = Field(default_factory=lambda: GroupRuntimeSandbox(mode="unrestricted"))
    tools: RuntimeTools = Field(default_factory=RuntimeTools)


class GroupConfig(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)
    name: str
    path: Path
    default_integration: str
    runtime: GroupRuntime = Field(default_factory=GroupRuntime)
    dispatch: GroupDispatch = Field(default_factory=GroupDispatch)
    agents: dict[str, AgentInstance] = Field(default_factory=dict)
    workspaces: tuple[WorkspaceConfig, ...] = ()


class AgencyConfigcanonical(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)
    schema_version: Literal[2]
    agency: AgencySettings
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    groups: dict[str, GroupConfig]


class ParsedConfig(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)
    raw: dict[str, Any]
    resolved: AgencyConfigcanonical

    @property
    def schema_version(self) -> Literal[2]:
        return self.resolved.schema_version

    @property
    def agency(self) -> AgencySettings:
        return self.resolved.agency

    @property
    def memory(self) -> MemoryConfig:
        return self.resolved.memory

    @property
    def groups(self) -> dict[str, GroupConfig]:
        return self.resolved.groups


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

    groups = raw.get("groups")
    groups_map = _mapping_or_none(groups)
    if groups_map is None:
        issues.append(_shape_issue("groups", "mapping"))
        return issues

    for group_name, group in groups_map.items():
        group_field = f"groups.{group_name}"
        group_map = _mapping_or_none(group)
        if group_map is None:
            issues.append(_shape_issue(group_field, "mapping"))
            continue

        runtime = group_map.get("runtime")
        runtime_map = _mapping_or_none(runtime)
        if runtime is not None and runtime_map is None:
            issues.append(_shape_issue(f"{group_field}.runtime", "mapping"))
        if runtime_map is not None:
            sandbox = runtime_map.get("sandbox")
            sandbox_map = _mapping_or_none(sandbox)
            if sandbox is not None and sandbox_map is None:
                issues.append(_shape_issue(f"{group_field}.runtime.sandbox", "mapping"))
            elif sandbox_map is not None:
                roots = sandbox_map.get("roots")
                if roots is not None and not _is_list(roots):
                    issues.append(_shape_issue(f"{group_field}.runtime.sandbox.roots", "list"))
                additional_roots = sandbox_map.get("additional_roots")
                if additional_roots is not None and not _is_list(additional_roots):
                    issues.append(_shape_issue(f"{group_field}.runtime.sandbox.additional_roots", "list"))

            tools = runtime_map.get("tools")
            tools_map = _mapping_or_none(tools)
            if tools is not None and tools_map is None:
                issues.append(_shape_issue(f"{group_field}.runtime.tools", "mapping"))
            elif tools_map is not None:
                names = tools_map.get("names")
                if names is not None and not _is_list(names):
                    issues.append(_shape_issue(f"{group_field}.runtime.tools.names", "list"))

        dispatch = group_map.get("dispatch")
        if dispatch is not None and not _is_mapping(dispatch):
            issues.append(_shape_issue(f"{group_field}.dispatch", "mapping"))
        elif _is_mapping(dispatch) and "agents" in dispatch:
            issues.append(
                _build_issue(
                    code="superseded-group-dispatch-agents",
                    scope=f"groups.{group_name}.dispatch",
                    field=f"{group_field}.dispatch.agents",
                    message="Group dispatch schedules are superseded v1 data and are not supported in canonical.",
                    hint="Move schedules into each agent's routines using the standalone migration utility.",
                )
            )

        workspaces = group_map.get("workspaces")
        if workspaces is not None and not _is_list(workspaces):
            issues.append(_shape_issue(f"{group_field}.workspaces", "list"))

        agents = group_map.get("agents")
        agents_list = None
        if agents is not None:
            if not _is_list(agents):
                issues.append(_shape_issue(f"{group_field}.agents", "list"))
            else:
                agents_list = agents
        if agents_list is None:
            continue

        for index, agent in enumerate(agents_list):
            agent_field = f"{group_field}.agents[{index}]"
            agent_map = _mapping_or_none(agent)
            if agent_map is None:
                continue

            identity = agent_map.get("identity")
            if identity is not None and not _is_mapping(identity):
                issues.append(_shape_issue(f"{agent_field}.identity", "mapping"))

            capabilities = agent_map.get("capabilities")
            if capabilities is not None and not _is_mapping(capabilities):
                issues.append(_shape_issue(f"{agent_field}.capabilities", "mapping"))

            runtime = agent_map.get("runtime")
            runtime_map = _mapping_or_none(runtime)
            if runtime is not None and runtime_map is None:
                issues.append(_shape_issue(f"{agent_field}.runtime", "mapping"))
            if runtime_map is not None:
                sandbox = runtime_map.get("sandbox")
                sandbox_map = _mapping_or_none(sandbox)
                if sandbox is not None and sandbox_map is None:
                    issues.append(_shape_issue(f"{agent_field}.runtime.sandbox", "mapping"))
                elif sandbox_map is not None:
                    roots = sandbox_map.get("roots")
                    if roots is not None and not _is_list(roots):
                        issues.append(_shape_issue(f"{agent_field}.runtime.sandbox.roots", "list"))
                    additional_roots = sandbox_map.get("additional_roots")
                    if additional_roots is not None and not _is_list(additional_roots):
                        issues.append(_shape_issue(f"{agent_field}.runtime.sandbox.additional_roots", "list"))

                tools = runtime_map.get("tools")
                tools_map = _mapping_or_none(tools)
                if tools is not None and tools_map is None:
                    issues.append(_shape_issue(f"{agent_field}.runtime.tools", "mapping"))
                elif tools_map is not None:
                    names = tools_map.get("names")
                    if names is not None and not _is_list(names):
                        issues.append(_shape_issue(f"{agent_field}.runtime.tools.names", "list"))

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
            hint="Choose run, agent, group, or channel for an agent default memory selector.",
        )
    if selected_scope == "channel" and not selector.get("channel"):
        return _build_issue(
            code="missing-memory-channel",
            scope=scope,
            field=f"{field_prefix}.channel",
            message="Channel memory selectors require a channel.",
            hint="Set channel to a declared memory channel key.",
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


def _validate_runtime(runtime: Any, scope: str) -> list[ValidationIssue]:
    return _validate_agent_runtime(runtime, scope)


def _validate_group_runtime(runtime: Any, scope: str) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if not _is_mapping(runtime):
        return issues
    sandbox = runtime.get("sandbox") or {}
    if not _is_mapping(sandbox):
        return issues
    if sandbox.get("mode") == "unrestricted" and sandbox.get("roots"):
        issues.append(
            _build_issue(
                code="sandbox-contradiction",
                scope=scope,
                field="runtime.sandbox.roots",
                message="Unrestricted sandbox cannot add roots.",
                hint="Remove roots or switch to restricted mode.",
            )
        )
    issues.extend(_validate_runtime_tools(runtime, scope))
    return issues


def _validate_agent_runtime(runtime: Any, scope: str) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if not _is_mapping(runtime):
        return issues
    sandbox = runtime.get("sandbox") or {}
    if not _is_mapping(sandbox):
        return issues
    if sandbox.get("roots"):
        issues.append(
            _build_issue(
                code="invalid-config",
                scope=scope,
                field=f"{scope}.runtime.sandbox.roots",
                message="superseded sandbox roots are not supported for agent runtime.",
                hint="Remove runtime.sandbox.roots and use runtime.sandbox.additional_roots instead.",
            )
        )
    if sandbox.get("additional_roots") and sandbox.get("mode") == "unrestricted":
        issues.append(
            _build_issue(
                code="sandbox-contradiction",
                scope=scope,
                field="runtime.sandbox.additional_roots",
                message="Unrestricted sandbox cannot add roots.",
                hint="Remove additional roots or switch to restricted mode.",
            )
        )
    issues.extend(_validate_runtime_tools(runtime, scope))
    return issues


def _validate_runtime_tools(runtime: Any, scope: str) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if not _is_mapping(runtime):
        return issues
    tools = runtime.get("tools") or {}
    if not _is_mapping(tools):
        return issues
    if tools.get("mode") == "allowlist":
        names = tools.get("names") or []
        if not _is_list(names):
            return issues
        trimmed_names = []
        for index, name in enumerate(names):
            if not isinstance(name, str) or not name.strip():
                issues.append(
                    _build_issue(
                        code="invalid-allowlist-name",
                        scope=scope,
                        field=f"runtime.tools.names[{index}]",
                        message="Allowlist names must be non-empty trimmed strings.",
                        hint="Remove blank entries and keep each allowlist name as a trimmed string.",
                    )
                )
                continue
            trimmed_names.append(name.strip())
        if not trimmed_names:
            issues.append(
                _build_issue(
                    code="empty-allowlist",
                    scope=scope,
                    field="runtime.tools.names",
                    message="Allowlist mode requires at least one tool name.",
                    hint="Add one or more tool names to the allowlist.",
                )
            )
    return issues


def _validate_default_group(default_group: Any, groups: Mapping[str, Any]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if default_group is None or default_group == "":
        return issues
    if not isinstance(default_group, str):
        issues.append(
            _build_issue(
                code="invalid-group-name",
                scope="agency",
                field="agency.default_group",
                message=f"Invalid group identifier: {default_group}",
                hint="Use a lowercase stable slug containing only letters, digits, and single hyphen separators.",
            )
        )
        return issues
    identifier_issue = _validate_identifier("group", default_group, "agency")
    if identifier_issue is not None:
        issues.append(
            ValidationIssue(
                code=identifier_issue.code,
                scope=identifier_issue.scope,
                field="agency.default_group",
                message=identifier_issue.message,
                corrective_hint=identifier_issue.corrective_hint,
            )
        )
        return issues
    if default_group not in groups:
        issues.append(
            _build_issue(
                code="missing-default-group",
                scope="agency",
                field="agency.default_group",
                message=f"Default group is not declared: {default_group}",
                hint="Set agency.default_group to a declared group key or leave it blank when omission is intended.",
            )
        )
    return issues
    return None


def _validate_raw_config(raw: dict[str, Any], config_path: Path) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    agency = raw.get("agency") if _is_mapping(raw.get("agency")) else {}
    memory = raw.get("memory") if _is_mapping(raw.get("memory")) else {}
    channels = memory.get("channels") if _is_mapping(memory.get("channels")) else {}
    declared_channels = set(channels)
    for channel_name in channels:
        identifier_issue = _validate_identifier("channel", channel_name, f"memory.channels.{channel_name}")
        if identifier_issue:
            issues.append(identifier_issue)
    for field_name in ("agent_library", "compilation_cache", "memory_store"):
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
    groups = raw.get("groups") if _is_mapping(raw.get("groups")) else {}
    issues.extend(_validate_default_group(agency.get("default_group", ""), groups))
    for group_name, group in groups.items():
        identifier_issue = _validate_identifier("group", group_name, f"groups.{group_name}")
        if identifier_issue:
            issues.append(identifier_issue)
        if not _is_mapping(group):
            continue
        if not str(group.get("default_integration", "")).strip():
            issues.append(
                _build_issue(
                    code="missing-default-integration",
                    scope=f"groups.{group_name}",
                    field="default_integration",
                    message="Group default integration is required.",
                    hint="Set group.default_integration to a non-empty integration name.",
                )
            )
        if not str(group.get("path", "")).strip():
            issues.append(
                _build_issue(
                    code="missing-group-path",
                    scope=f"groups.{group_name}",
                    field="path",
                    message="Group path is required.",
                    hint="Set group.path relative to config.yaml.",
                )
            )
        runtime = group.get("runtime") or {}
        issues.extend(_validate_group_runtime(runtime, f"groups.{group_name}"))
        agents = group.get("agents") if _is_list(group.get("agents")) else []
        seen_agents: set[str] = set()
        for index, agent in enumerate(agents):
            if not isinstance(agent, dict):
                issues.append(
                    _build_issue(
                        code="invalid-agent-entry",
                        scope=f"groups.{group_name}.agents[{index}]",
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
                        scope=f"groups.{group_name}.agents[{index}]",
                        field=f"agents[{index}].name",
                        message="Agent name is required.",
                        hint="Set agent.name to a non-empty identifier.",
                    )
                )
                continue
            identifier_issue = _validate_identifier(
                "agent", name, f"groups.{group_name}.agents.{name or '<unknown>'}"
            )
            if identifier_issue:
                issues.append(identifier_issue)
            if name in seen_agents:
                issues.append(
                    _build_issue(
                        code="duplicate-agent-name",
                        scope=f"groups.{group_name}",
                        field="agents",
                        message=f"Duplicate agent name: {name}",
                        hint="Give each agent a unique name within the group.",
                    )
                )
            seen_agents.add(name)
            blueprint_issue = _validate_blueprint(agent, f"groups.{group_name}.agents.{name or '<unknown>'}")
            if blueprint_issue:
                issues.append(blueprint_issue)
            if not str(agent.get("integration", "")).strip():
                issues.append(
                    _build_issue(
                        code="missing-explicit-integration",
                        scope=f"groups.{group_name}.agents.{name or '<unknown>'}",
                        field="integration",
                        message="Each agent must declare an explicit integration.",
                        hint="Set integration on every agent instance.",
                    )
                )
            default_memory = agent.get("default_memory") or {}
            if default_memory:
                issue = _validate_memory_selector(
                    default_memory,
                    f"groups.{group_name}.agents.{name or '<unknown>'}",
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
                        _routine_entry_issue(f"groups.{group_name}.agents[{index}].routines[{routine_index}]")
                    )
                    continue
                routine_id = routine.get("id")
                if isinstance(routine_id, str) and routine_id in seen_routines:
                    issues.append(
                        _build_issue(
                            code="duplicate-routine-name",
                            scope=f"groups.{group_name}.agents.{name or '<unknown>'}",
                            field="routines",
                            message=f"Duplicate routine id: {routine_id}",
                            hint="Give each routine a unique id within the agent.",
                        )
                    )
                if isinstance(routine_id, str):
                    identifier_issue = _validate_identifier(
                        "routine", routine_id, f"groups.{group_name}.agents.{name or '<unknown>'}"
                    )
                    if identifier_issue:
                        issues.append(identifier_issue)
                    seen_routines.add(routine_id)
                schedule = routine.get("schedule") or {}
                issue = _validate_rule(schedule, f"groups.{group_name}.agents.{name or '<unknown>'}")
                if issue:
                    issues.append(issue)
                memory = routine.get("memory")
                if memory is not None:
                    issue = _validate_memory_selector(
                        memory,
                        f"groups.{group_name}.agents.{name or '<unknown>'}",
                        allow_routine=True,
                        field_prefix="memory",
                        declared_channels=declared_channels,
                    )
                    if issue:
                        issues.append(issue)
            runtime = agent.get("runtime") or {}
            issues.extend(_validate_agent_runtime(runtime, f"groups.{group_name}.agents.{name or '<unknown>'}"))

        dispatch = group.get("dispatch") if _is_mapping(group.get("dispatch")) else {}
        if _is_mapping(dispatch):
            for key in dispatch:
                if key not in {"enabled", "daily_limit"} and key != "agents":
                    issues.append(
                        _build_issue(
                            code="invalid-config",
                            scope=f"groups.{group_name}.dispatch",
                            field=f"groups.{group_name}.dispatch.{key}",
                            message=f"Unknown group dispatch field: {key}",
                            hint="Remove the unsupported field or migrate it to a supported location.",
                        )
                    )
    return issues

def _sorted_issues(issues: list[ValidationIssue]) -> tuple[ValidationIssue, ...]:
    return tuple(sorted(issues, key=lambda issue: (issue.scope, issue.field, issue.code, issue.message)))


def _collect_schema_issues(raw: dict[str, Any]) -> list[ValidationIssue]:
    if raw.get("schema_version") == 2:
        return []
    return [
        _build_issue(
            code="invalid-schema-version",
            scope="schema_version",
            field="schema_version",
            message="Only schema_version 2 is supported.",
            hint="Run the canonical migration utility before loading config.",
        )
    ]


def _prepare_runtime(runtime: Any, base_path: Path | None) -> dict[str, Any]:
    runtime_entry = dict(runtime) if _is_mapping(runtime) else {}
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
    tools = dict(runtime_entry.get("tools") or {})
    if tools.get("names") is not None:
        tools["names"] = tuple(str(name) for name in tools.get("names") or ())
    runtime_entry["tools"] = tools
    return runtime_entry


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
    prepared["agency"] = agency

    groups = dict(prepared.get("groups") or {})
    resolved_groups: dict[str, Any] = {}
    for group_name, group in groups.items():
        if not _is_mapping(group):
            continue
        resolved_group = dict(group)
        if resolved_group.get("path") is not None:
            resolved_group["path"] = _path_from_config(resolved_group["path"], config_dir)
        group_path = resolved_group.get("path")
        group_root = Path(group_path) if group_path is not None else None
        resolved_group["runtime"] = _prepare_runtime(resolved_group.get("runtime") or {}, group_root)
        agents = {}
        for agent in resolved_group.get("agents") or []:
            if not isinstance(agent, dict):
                continue
            name = agent.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            agent_entry = dict(agent)
            agent_entry["runtime"] = _prepare_runtime(agent_entry.get("runtime") or {}, group_root)
            if agent_entry.get("routines") is not None:
                routines = []
                for routine in agent_entry.get("routines") or []:
                    if not _is_mapping(routine):
                        continue
                    routine_entry = dict(routine)
                    if _is_mapping(routine_entry.get("memory")):
                        routine_entry["memory"] = dict(routine_entry["memory"])
                    routines.append(routine_entry)
                agent_entry["routines"] = tuple(routines)
            agents[name] = agent_entry
        resolved_group["agents"] = agents
        if resolved_group.get("workspaces") is not None:
            resolved_group["workspaces"] = tuple(resolved_group.get("workspaces") or ())
        resolved_groups[group_name] = resolved_group
    prepared["groups"] = resolved_groups
    return prepared


def _collect_post_parse_issues(parsed: ParsedConfig) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for field_name in ("agent_library", "compilation_cache", "memory_store"):
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
    for group_name, group in parsed.groups.items():
        if not group.path.is_absolute():
            issues.append(
                _build_issue(
                    code="missing-group-path",
                    scope=f"groups.{group_name}",
                    field="path",
                    message="Group path is required.",
                    hint="Set group.path relative to config.yaml.",
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
        resolved = AgencyConfigcanonical.model_validate(prepared)
    except ValidationError as exc:
        issues.extend(_collect_pydantic_issues(exc))
        return _PipelineResult(parsed=None, issues=_sorted_issues(issues))

    parsed = ParsedConfig(raw=raw, resolved=resolved)
    issues.extend(_collect_post_parse_issues(parsed))
    sorted_issues = _sorted_issues(issues)
    return _PipelineResult(parsed=parsed if not sorted_issues else None, issues=sorted_issues)


def parse_config_canonical(raw: dict[str, Any], config_path: Path) -> ParsedConfig:
    result = _build_pipeline_result(raw, config_path)
    if result.issues:
        raise ValidationFailed(result.issues)
    assert result.parsed is not None
    return result.parsed


def validate_config_canonical(raw: dict[str, Any], config_path: Path) -> tuple[ValidationIssue, ...]:
    return _build_pipeline_result(raw, config_path).issues
