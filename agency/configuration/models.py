from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .issues import ValidationFailed, ValidationIssue

MemoryScope = Literal["run", "routine", "agent", "group", "channel"]
ToolMode = Literal["all", "allowlist", "none"]
SandboxMode = Literal["restricted", "unrestricted"]
ScheduleKind = Literal["at", "every"]

_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]*$"


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


class RuntimeSandbox(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    mode: SandboxMode = "unrestricted"
    roots: tuple[Path, ...] = ()
    additional_roots: tuple[Path, ...] = ()


class RuntimeTools(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    mode: ToolMode = "all"
    names: tuple[str, ...] = ()


class AgentRuntime(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)
    timeout: int = 1800
    sandbox: RuntimeSandbox = Field(default_factory=RuntimeSandbox)
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


class DispatchRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    at: str | None = None
    every: str | None = None


class GroupDispatch(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)
    enabled: bool = False
    daily_limit: int = 20
    agents: dict[str, list[DispatchRule]] = Field(default_factory=dict)


class WorkspaceConfig(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)
    name: str
    type: str
    config: dict[str, Any] = Field(default_factory=dict)


class GroupRuntime(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)
    timeout: int = 1800
    sandbox: RuntimeSandbox = Field(default_factory=lambda: RuntimeSandbox(mode="unrestricted"))
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
            hint="Use a stable identifier starting with a letter or digit and containing only letters, digits, hyphens, or underscores.",
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


def _validate_runtime(runtime: Any, scope: str) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if not _is_mapping(runtime):
        return issues
    sandbox = runtime.get("sandbox") or {}
    if not _is_mapping(sandbox):
        return issues
    if sandbox.get("mode") == "unrestricted" and (sandbox.get("additional_roots") or sandbox.get("additions")):
        issues.append(
            _build_issue(
                code="sandbox-contradiction",
                scope=scope,
                field="runtime.sandbox.additional_roots",
                message="Unrestricted sandbox cannot add roots.",
                hint="Remove additional roots or switch to restricted mode.",
            )
        )
    tools = runtime.get("tools") or {}
    if not _is_mapping(tools):
        return issues
    if tools.get("mode") == "allowlist":
        names = tools.get("names") or []
        if not _is_list(names):
            return issues
        if not names:
            issues.append(
                _build_issue(
                    code="empty-allowlist",
                    scope=scope,
                    field="runtime.tools.names",
                    message="Allowlist mode requires at least one tool name.",
                    hint="Add one or more tool names to the allowlist.",
                )
            )
        else:
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
    return issues


def _validate_raw_config(raw: dict[str, Any], config_path: Path) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    agency = raw.get("agency") if _is_mapping(raw.get("agency")) else {}
    memory = raw.get("memory") if _is_mapping(raw.get("memory")) else {}
    channels = memory.get("channels") if _is_mapping(memory.get("channels")) else {}
    declared_channels = set(channels)
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
    for group_name, group in groups.items():
        if not _is_mapping(group):
            continue
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
            for routine in routines:
                if not isinstance(routine, dict):
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
            issues.extend(_validate_runtime(runtime, f"groups.{group_name}.agents.{name or '<unknown>'}"))
    return issues

def _validate_agent(agent: dict[str, Any], group_name: str) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    scope = f"groups.{group_name}.agents.{agent.get('name', '<unknown>')}"
    required = ["name", "blueprint", "integration"]
    for key in required:
        if not agent.get(key):
            issues.append(
                _build_issue(
                    code=f"missing-{key}",
                    scope=scope,
                    field=key,
                    message=f"Agent {key} is required.",
                    hint=f"Set agent {key} explicitly.",
                )
            )
    name = agent.get("name")
    if isinstance(name, str):
        issue = _validate_identifier("agent", name, scope)
        if issue:
            issues.append(issue)
    default_memory = agent.get("default_memory")
    if isinstance(default_memory, dict):
        issue = _validate_memory_selector(default_memory, scope, allow_routine=False)
        if issue:
            issues.append(issue)
    routines = agent.get("routines") or []
    seen_routines: set[str] = set()
    for routine in routines:
        routine_name = routine.get("id")
        if isinstance(routine_name, str):
            issue = _validate_identifier("routine", routine_name, scope)
            if issue:
                issues.append(issue)
            if routine_name in seen_routines:
                issues.append(
                    _build_issue(
                        code="duplicate-routine-name",
                        scope=scope,
                        field="routines",
                        message=f"Duplicate routine id: {routine_name}",
                        hint="Give each routine a unique id within the agent.",
                    )
                )
            seen_routines.add(routine_name)
        schedule = routine.get("schedule") or {}
        issue = _validate_rule(schedule, scope)
        if issue:
            issues.append(issue)
        memory = routine.get("memory")
        if isinstance(memory, dict):
            issue = _validate_memory_selector(memory, scope, allow_routine=True)
            if issue:
                issues.append(issue)
    issues.extend(_validate_runtime(agent.get("runtime") or {}, scope))
    return issues


def _sorted_issues(issues: list[ValidationIssue]) -> tuple[ValidationIssue, ...]:
    return tuple(sorted(issues, key=lambda issue: (issue.scope, issue.field, issue.code, issue.message)))


def parse_config_canonical(raw: dict[str, Any], config_path: Path) -> ParsedConfig:
    shape_issues = _collect_shape_issues(raw)
    if shape_issues:
        raise ValidationFailed(_sorted_issues(shape_issues))

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
        resolved_group = dict(group)
        if resolved_group.get("path") is not None:
            resolved_group["path"] = _path_from_config(resolved_group["path"], config_dir)
        group_path = resolved_group.get("path")
        if group_path is not None:
            runtime = dict(resolved_group.get("runtime") or {})
            sandbox = dict(runtime.get("sandbox") or {})
            roots = []
            for root in sandbox.get("roots") or []:
                roots.append(_path_from_config(root, Path(group_path)))
            additional_roots = []
            for root in sandbox.get("additional_roots") or []:
                additional_roots.append(_path_from_config(root, Path(group_path)))
            sandbox["roots"] = tuple(roots)
            sandbox["additional_roots"] = tuple(additional_roots)
            runtime["sandbox"] = sandbox
            resolved_group["runtime"] = runtime
        agents = {}
        for index, agent in enumerate(resolved_group.get("agents") or []):
            if not isinstance(agent, dict):
                raise ValidationFailed(
                    _sorted_issues(
                        [
                            _build_issue(
                                code="invalid-agent-entry",
                                scope=f"groups.{group_name}.agents[{index}]",
                                field=f"agents[{index}]",
                                message="Agent entry must be a mapping.",
                                hint="Define each agent as a mapping with name, blueprint, and integration.",
                            )
                        ]
                    )
                )
            name = agent.get("name")
            if not isinstance(name, str) or not name.strip():
                raise ValidationFailed(
                    _sorted_issues(
                        [
                            _build_issue(
                                code="missing-agent-name",
                                scope=f"groups.{group_name}.agents[{index}]",
                                field=f"agents[{index}].name",
                                message="Agent name is required.",
                                hint="Set agent.name to a non-empty identifier.",
                            )
                        ]
                    )
                )
            agent_entry = dict(agent)
            runtime = dict(agent_entry.get("runtime") or {})
            sandbox = dict(runtime.get("sandbox") or {})
            if group_path is not None:
                group_root = Path(group_path)
                roots = []
                for root in sandbox.get("roots") or []:
                    roots.append(_path_from_config(root, group_root))
                additional_roots = []
                for root in sandbox.get("additional_roots") or []:
                    additional_roots.append(_path_from_config(root, group_root))
                sandbox["roots"] = tuple(roots)
                sandbox["additional_roots"] = tuple(additional_roots)
            runtime["sandbox"] = sandbox
            tools = dict(runtime.get("tools") or {})
            if tools.get("names") is not None:
                tools["names"] = tuple(str(name) for name in tools.get("names") or ())
            runtime["tools"] = tools
            agent_entry["runtime"] = runtime
            if agent_entry.get("routines") is not None:
                routines = []
                for routine in agent_entry.get("routines") or []:
                    routine_entry = dict(routine)
                    if routine_entry.get("memory") is not None:
                        routine_entry["memory"] = dict(routine_entry["memory"])
                    routines.append(routine_entry)
                agent_entry["routines"] = tuple(routines)
            agents[name] = agent_entry
        resolved_group["agents"] = agents
        if resolved_group.get("workspaces") is not None:
            resolved_group["workspaces"] = tuple(resolved_group.get("workspaces") or ())
        resolved_groups[group_name] = resolved_group
    prepared["groups"] = resolved_groups

    try:
        resolved = AgencyConfigcanonical.model_validate(prepared)
    except ValidationError as exc:
        raise ValidationFailed(_sorted_issues(_collect_pydantic_issues(exc))) from exc
    return ParsedConfig(raw=raw, resolved=resolved)


def validate_config_canonical(raw: dict[str, Any], config_path: Path) -> tuple[ValidationIssue, ...]:
    issues: list[ValidationIssue] = []
    if raw.get("schema_version") != 2:
        issues.append(
            _build_issue(
                code="invalid-schema-version",
                scope="schema_version",
                field="schema_version",
                message="Only schema_version 2 is supported.",
                hint="Run the canonical migration utility before loading config.",
            )
        )
        return _sorted_issues(issues)
    shape_issues = _collect_shape_issues(raw)
    if shape_issues:
        issues.extend(shape_issues)
        return _sorted_issues(issues)
    issues.extend(_validate_raw_config(raw, config_path))
    try:
        parsed = parse_config_canonical(raw, config_path)
    except ValidationFailed as exc:
        issues.extend(exc.issues)
        return _sorted_issues(issues)
    except ValidationError as exc:
        issues.extend(_collect_pydantic_issues(exc))
        return _sorted_issues(issues)

    cfg = parsed.resolved
    config_dir = config_path.parent.resolve()
    for field_name in ("agent_library", "compilation_cache", "memory_store"):
        value = getattr(cfg.agency, field_name)
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
    for group_name, group in cfg.groups.items():
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
        agent_names = set()
        for agent_name, agent in group.agents.items():
            if agent_name in agent_names:
                issues.append(
                    _build_issue(
                        code="duplicate-agent-name",
                        scope=f"groups.{group_name}",
                        field="agents",
                        message=f"Duplicate agent name: {agent_name}",
                        hint="Give each agent a unique name within the group.",
                    )
                )
            agent_names.add(agent_name)
            if agent.integration is None or not str(agent.integration).strip():
                issues.append(
                    _build_issue(
                        code="missing-explicit-integration",
                        scope=f"groups.{group_name}.agents.{agent_name}",
                        field="integration",
                        message="Each agent must declare an explicit integration.",
                        hint="Set integration on every agent instance.",
                    )
                )
            issues.extend(_validate_agent(agent.model_dump(mode="python"), group_name))
    return _sorted_issues(issues)
