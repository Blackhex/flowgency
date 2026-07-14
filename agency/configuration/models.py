from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .issues import ValidationIssue

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


def _validate_rule(rule: dict[str, Any], scope: str) -> ValidationIssue | None:
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


def _validate_memory_selector(selector: dict[str, Any], scope: str, allow_routine: bool) -> ValidationIssue | None:
    selected_scope = selector.get("scope")
    if selected_scope == "routine" and not allow_routine:
        return _build_issue(
            code="invalid-memory-scope",
            scope=scope,
            field="default_memory.scope",
            message="Agent default memory cannot use routine scope.",
            hint="Choose run, agent, group, or channel for an agent default memory selector.",
        )
    if selected_scope == "channel" and not selector.get("channel"):
        return _build_issue(
            code="missing-memory-channel",
            scope=scope,
            field="default_memory.channel",
            message="Channel memory selectors require a channel.",
            hint="Set channel to a declared memory channel key.",
        )
    return None


def _validate_runtime(runtime: dict[str, Any], scope: str) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    sandbox = runtime.get("sandbox") or {}
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
    if tools.get("mode") == "allowlist" and not tools.get("names"):
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


def _validate_raw_config(raw: dict[str, Any], config_path: Path) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    agency = raw.get("agency") or {}
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
    groups = raw.get("groups") or {}
    for group_name, group in groups.items():
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
        agents = group.get("agents") or []
        seen_agents: set[str] = set()
        for agent in agents:
            if not isinstance(agent, dict):
                continue
            name = agent.get("name")
            if isinstance(name, str):
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
            if isinstance(default_memory, dict):
                issue = _validate_memory_selector(default_memory, f"groups.{group_name}.agents.{name or '<unknown>'}", allow_routine=False)
                if issue:
                    issues.append(issue)
            routines = agent.get("routines") or []
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
                if isinstance(memory, dict):
                    issue = _validate_memory_selector(memory, f"groups.{group_name}.agents.{name or '<unknown>'}", allow_routine=True)
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
        for agent in resolved_group.get("agents") or []:
            agent_entry = dict(agent)
            runtime = dict(agent_entry.get("runtime") or {})
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
            agents[agent_entry["name"]] = agent_entry
        resolved_group["agents"] = agents
        if resolved_group.get("workspaces") is not None:
            resolved_group["workspaces"] = tuple(resolved_group.get("workspaces") or ())
        resolved_groups[group_name] = resolved_group
    prepared["groups"] = resolved_groups

    resolved = AgencyConfigcanonical.model_validate(prepared)
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
    issues.extend(_validate_raw_config(raw, config_path))
    try:
        parsed = parse_config_canonical(raw, config_path)
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
