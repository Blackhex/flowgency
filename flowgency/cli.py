"""Command-line interface for Flowgency's canonical control plane."""

from __future__ import annotations

import argparse
from argparse import Namespace
from dataclasses import dataclass
from datetime import datetime
import importlib
import json
import os
from pathlib import Path
import sys
from typing import Any

import yaml

from flowgency.cli_output import ExitCode, render_error
from flowgency.blueprints import BlueprintLibrary
from flowgency.configuration import (
    ConfigSnapshot,
    ConfigStore,
    ValidationFailed,
    ValidationIssue,
    config_revision,
    parse_config,
    resolve_team_paths,
)
from flowgency.configuration.effective import resolve_effective_policy
from flowgency.configuration.models import MemorySelector
from flowgency.dispatch.install import get_timer_status, install_timer, uninstall_timer
from flowgency.fs.locks import LockCancelledError, ResourceBusyError
from flowgency.integrations import REGISTRY
from flowgency.jobs import JobRequest, submit_job_request
from flowgency.jobs.authority import JobStore
from flowgency.jobs.store import read_job
from flowgency.memory import MemoryConflictError, MemoryStore, resolve_memory_selector
from flowgency.prompts import PromptStore
from flowgency.tickets.cli import register_ticket_commands
from flowgency.tickets.models import UserTicketContext
from flowgency.tickets.views import build_board_view
from flowgency.web.dependencies import FlowgencyServices, build_services


def _supports_color() -> bool:
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


COLORS_ENABLED = _supports_color()


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if COLORS_ENABLED else text


def green(text: str) -> str:
    return _c("32", text)


def yellow(text: str) -> str:
    return _c("33", text)


def red(text: str) -> str:
    return _c("31", text)


def cyan(text: str) -> str:
    return _c("36", text)


def bold(text: str) -> str:
    return _c("1", text)


def dim(text: str) -> str:
    return _c("2", text)


@dataclass
class CliFailure(Exception):
    exit_code: ExitCode
    code: str
    message: str
    issues: tuple[ValidationIssue, ...] = ()

    def __str__(self) -> str:
        return self.message


def _issue(code: str, scope: str, field: str, message: str, hint: str) -> ValidationIssue:
    return ValidationIssue(code, scope, field, message, hint)


def _validation_failure(
    code: str,
    message: str,
    *,
    scope: str = "cli",
    field: str = "command",
    hint: str = "Correct the command and try again.",
) -> CliFailure:
    return CliFailure(
        ExitCode.VALIDATION,
        code,
        message,
        (_issue(code, scope, field, message, hint),),
    )


def _config_path(args: Namespace) -> Path:
    selected = getattr(args, "config", None) or os.environ.get("FLOWGENCY_CONFIG") or (Path.cwd() / "config.yaml")
    return Path(selected).expanduser().resolve()


def _services(args: Namespace) -> FlowgencyServices:
    services = build_services(_config_path(args))
    if services.startup_error is None:
        return services
    error = services.startup_error
    if isinstance(error, ValidationFailed):
        raise CliFailure(ExitCode.VALIDATION, "invalid-config", "Configuration is invalid", tuple(error.issues))
    issue = _issue(
        "invalid-config",
        "configuration",
        str(services.config_path),
        str(error),
        "Provide a readable canonical config and valid configured asset roots.",
    )
    raise CliFailure(ExitCode.VALIDATION, "invalid-config", "Configuration is invalid", (issue,))


def _snapshot(args: Namespace):
    try:
        return _snapshot_read_only(_config_path(args))
    except ValidationFailed as error:
        raise CliFailure(ExitCode.VALIDATION, "invalid-config", "Configuration is invalid", tuple(error.issues)) from error


def _snapshot_read_only(path: Path) -> ConfigSnapshot:
    payload = path.read_bytes()
    loaded = yaml.safe_load(payload.decode("utf-8"))
    raw = loaded if isinstance(loaded, dict) else {}
    parsed = parse_config(raw, path)
    return ConfigSnapshot(
        path=path,
        revision=config_revision(payload),
        raw=raw,
        config=parsed.resolved,
    )


def _team_id(args: Namespace, snapshot) -> str:
    team_id = getattr(args, "team", None) or snapshot.config.flowgency.default_team
    if not team_id:
        raise _validation_failure(
            "missing-team",
            "No team was selected and no default team is configured.",
            field="team",
            hint="Pass --team or configure flowgency.default_team.",
        )
    if team_id not in snapshot.config.teams:
        raise _validation_failure(
            "unknown-team",
            f"Unknown team: {team_id}",
            field="team",
            hint="Choose a team defined in config.yaml.",
        )
    return team_id


def _team(args: Namespace):
    snapshot = _snapshot(args)
    team_id = _team_id(args, snapshot)
    return snapshot, team_id, snapshot.config.teams[team_id]


def _instance(snapshot, team_id: str, agent_id: str):
    try:
        return snapshot.config.teams[team_id].agents[agent_id]
    except KeyError as error:
        raise _validation_failure(
            "unknown-agent",
            f"Unknown agent: {agent_id}",
            scope=f"teams.{team_id}",
            field="agent",
            hint="Choose an agent instance owned by this team.",
        ) from error


def _relative_time(value: str | datetime | None) -> str:
    if not value:
        return "never"
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except (TypeError, ValueError):
            return value
    now = datetime.now(value.tzinfo) if value.tzinfo else datetime.now()
    minutes = max(0, int((now - value).total_seconds() / 60))
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    return f"{hours}h ago" if hours < 24 else f"{hours // 24}d ago"


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    try:
        metadata = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return {}, text
    return (metadata if isinstance(metadata, dict) else {}), parts[2].strip()


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    return _parse_frontmatter(text)


def _job_records(snapshot, team_id: str):
    job_store = JobStore(snapshot.config.flowgency.memory_store)
    records = []
    for path in job_store.paths(team_id):
        try:
            records.append((path, read_job(path)))
        except Exception:
            records.append((path, None))
    records.sort(
        key=lambda item: (
            item[1].spec.job_id if item[1] is not None else item[0].name,
            item[0].as_posix(),
        )
    )
    records.sort(
        key=lambda item: item[1].started_at or item[1].spec.created_at or "" if item[1] is not None else "",
        reverse=True,
    )
    return records


def _active_job(snapshot, team_id: str, agent_name: str):
    records = JobStore(snapshot.config.flowgency.memory_store).active(team_id, agent_name)
    return max(records, key=lambda record: (record.spec.created_at, record.spec.job_id), default=None)


def _memory_label(selector: MemorySelector | None, channels) -> str:
    selected = selector or MemorySelector(scope="run")
    if selected.scope == "channel":
        channel = channels.get(selected.channel or "")
        name = channel.display_name if channel is not None else selected.channel or "Channel"
        return f"Channel: {name}"
    return f"{selected.scope.title()} memory"


def _policy_payload(policy) -> dict[str, Any]:
    return {
        "timeout": policy.timeout,
        "mode": policy.mode,
        "rules": [
            {
                "path": str(rule.path).replace("\\", "/") if rule.path else None,
                "tools": list(rule.tools) if rule.tools is not None else None,
            }
            for rule in policy.rules
        ],
    }


def _cache_status(services: FlowgencyServices, instance, inspection) -> str:
    integration = services.integrations.get(instance.integration)
    projector = integration.projector if integration is not None else None
    if projector is None or services.compilation_cache is None:
        return "unavailable"
    entry = services.compilation_cache.root / instance.integration / projector.version / inspection.snapshot.digest
    return "compiled" if (entry / "manifest.json").is_file() else "missing"


def _read_only_runtime(snapshot) -> tuple[BlueprintLibrary, dict[str, Any]]:
    return BlueprintLibrary(Path(snapshot.config.flowgency.agent_library)), dict(REGISTRY)


def _cache_status_read_only(snapshot, integrations: dict[str, Any], instance, inspection) -> str:
    integration = integrations.get(instance.integration)
    projector = integration.projector if integration is not None else None
    if projector is None:
        return "unavailable"
    entry = Path(snapshot.config.flowgency.compilation_cache) / instance.integration / projector.version / inspection.snapshot.digest
    return "compiled" if (entry / "manifest.json").is_file() else "missing"


def _agent_payload_read_only(snapshot, team_id: str, instance) -> dict[str, Any]:
    team = snapshot.config.teams[team_id]
    current = _active_job(snapshot, team_id, instance.name)
    library, integrations = _read_only_runtime(snapshot)
    inspection = library.inspect(instance.blueprint)
    policy = resolve_effective_policy(snapshot.config, team_id, instance.name)
    return {
        "name": instance.name,
        "display_name": instance.identity.display_name or instance.name,
        "blueprint": instance.blueprint,
        "integration": instance.integration,
        "health": "active" if current is not None else "idle",
        "job": (
            {"id": current.spec.job_id, "status": current.status, "routine": current.spec.routine_id}
            if current is not None
            else None
        ),
        "routine": [routine.id for routine in instance.routines],
        "memory": _memory_label(instance.default_memory, snapshot.config.memory.channels),
        "cache": _cache_status_read_only(snapshot, integrations, instance, inspection),
        "effective_policy": _policy_payload(policy),
    }


def _agent_payload(services: FlowgencyServices, snapshot, team_id: str, instance) -> dict[str, Any]:
    team = snapshot.config.teams[team_id]
    current = _active_job(snapshot, team_id, instance.name)
    inspection = services.blueprint_library.inspect(instance.blueprint)
    policy = resolve_effective_policy(snapshot.config, team_id, instance.name)
    return {
        "name": instance.name,
        "display_name": instance.identity.display_name or instance.name,
        "blueprint": instance.blueprint,
        "integration": instance.integration,
        "health": "active" if current is not None else "idle",
        "job": (
            {"id": current.spec.job_id, "status": current.status, "routine": current.spec.routine_id}
            if current is not None
            else None
        ),
        "routine": [routine.id for routine in instance.routines],
        "memory": _memory_label(instance.default_memory, snapshot.config.memory.channels),
        "cache": _cache_status(services, instance, inspection),
        "effective_policy": _policy_payload(policy),
    }


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def run_server(**options) -> None:
    importlib.import_module("flowgency.app").run_server(**options)


def cmd_validate(args: Namespace) -> int:
    services = _services(args)
    issues: list[ValidationIssue] = list(services.prompt_issues)
    seen: set[tuple[str, str, str]] = {(i.code, i.field, i.message) for i in issues}

    if services.blueprint_library is not None:
        try:
            services.blueprint_library.list()
        except ValidationFailed as exc:
            for issue in exc.issues:
                key = (issue.code, issue.field, issue.message)
                if key not in seen:
                    seen.add(key)
                    issues.append(issue)

    combined = tuple(issues)
    if combined:
        raise CliFailure(ExitCode.VALIDATION, "validation-failed", "Validation failed", combined)
    if getattr(args, "json", False):
        print(json.dumps({"issues": []}, sort_keys=True))
    else:
        print("No validation issues found.")
    return int(ExitCode.SUCCESS)


def cmd_serve(args: Namespace) -> int:
    os.environ["FLOWGENCY_CONFIG"] = str(_config_path(args))
    run_server(host=args.host, port=args.port, reload=args.reload)
    return 0


def cmd_status(args: Namespace) -> int:
    services = _services(args)
    snapshot = services.config_store.load()
    job_store = JobStore(snapshot.config.flowgency.memory_store)
    result = {}
    for team_id, team in snapshot.config.teams.items():
        workflows = []
        if services.tickets is not None:
            actor = UserTicketContext(team_id=team_id)
            for binding in services.tickets.list_workflows(actor):
                board = build_board_view(
                    services.tickets,
                    actor,
                    binding.workflow_id,
                    ticket_jobs=services.ticket_jobs,
                )
                workflows.append(board)
        result[team_id] = {
            "name": team.name,
            "workflows": len(workflows),
            "tickets": sum(board.ticket_count for board in workflows),
            "working": sum(board.working_count for board in workflows),
            "unassigned": sum(
                1
                for board in workflows
                for column in board.columns
                for ticket in column.tickets
                if ticket.assignee is None
            ),
            "agents": len(team.agents),
            "active": sum(bool(job_store.active(team_id, name)) for name in team.agents),
        }
    if args.json:
        _print_json(result)
    else:
        print(f"\n{bold(snapshot.config.flowgency.title)} - Fleet Status\n")
        for team_id, item in result.items():
            print(f"  {bold(item['name'])} ({team_id})")
            print(
                f"    {item['agents']} agents - {item['active']} active - {item['workflows']} workflows - "
                f"{item['tickets']} tickets - {item['working']} working - {item['unassigned']} unassigned"
            )
        print()
    return 0


def cmd_agents(args: Namespace) -> int:
    snapshot = _snapshot(args)
    team_id = _team_id(args, snapshot)
    team = snapshot.config.teams[team_id]
    payload = [_agent_payload_read_only(snapshot, team_id, instance) for instance in team.agents.values()]
    if args.json:
        _print_json(payload)
    else:
        print(f"\n{bold('Agents')} - {team.name}\n")
        for item in payload:
            job = item["job"]["status"] if item["job"] else "idle"
            print(
                f"  {item['display_name']} ({item['name']})  {dim(item['integration'])}  "
                f"{dim(item['blueprint'])}  {job}  {item['memory']}"
            )
        print()
    return 0


def cmd_agent_show(args: Namespace) -> int:
    snapshot = _snapshot(args)
    team_id = _team_id(args, snapshot)
    instance = _instance(snapshot, team_id, args.agent)
    payload = _agent_payload_read_only(snapshot, team_id, instance)
    payload.update(
        title=instance.identity.title,
        emoji=instance.identity.emoji,
        routines=[
            {
                "id": routine.id,
                "prompt": {
                    "scope": routine.prompt.scope,
                    "name": routine.prompt.name,
                },
                "arguments": list(routine.arguments),
                "memory": _memory_label(routine.memory, snapshot.config.memory.channels),
            }
            for routine in instance.routines
        ],
    )
    if args.json:
        _print_json(payload)
    else:
        print(f"\n{bold(payload['display_name'])} ({payload['name']})")
        print(f"  Blueprint: {payload['blueprint']}")
        print(f"  Integration: {payload['integration']}")
        print(f"  Memory: {payload['memory']}")
        print(f"  Cache: {payload['cache']}")
        print("  Routines: " + (", ".join(payload["routine"]) or "none"))
        print()
    return 0


def _memory_override(args: Namespace, snapshot) -> MemorySelector | None:
    scope = getattr(args, "memory_scope", None)
    channel = getattr(args, "memory_channel", None)
    if scope is None:
        if channel:
            raise _validation_failure("invalid-memory-selector", "--memory-channel requires --memory-scope channel.", field="memory-channel")
        return None
    if scope == "channel":
        if not channel:
            raise _validation_failure("invalid-memory-selector", "Channel memory requires --memory-channel.", field="memory-channel")
        if channel not in snapshot.config.memory.channels:
            raise _validation_failure("unknown-memory-channel", f"Unknown memory channel: {channel}", field="memory-channel")
        return MemorySelector(scope=scope, channel=channel)
    if channel:
        raise _validation_failure("invalid-memory-selector", "--memory-channel is only valid for channel memory.", field="memory-channel")
    return MemorySelector(scope=scope)


def cmd_agent_run(args: Namespace) -> int:
    config_path = _config_path(args)
    snapshot = _snapshot(args)
    team_id = _team_id(args, snapshot)
    instance = _instance(snapshot, team_id, args.agent)
    routine = next((item for item in instance.routines if item.id == args.routine), None)
    if routine is None:
        raise _validation_failure(
            "unknown-routine",
            f"Unknown routine '{args.routine}' for agent '{args.agent}'.",
            scope=f"teams.{team_id}.agents.{args.agent}",
            field="routine",
            hint="Choose an existing stable routine ID.",
        )
    if not routine.enabled:
        raise _validation_failure(
            "routine-disabled",
            f"Routine '{routine.id}' is disabled; enable it before running.",
            scope=f"teams.{team_id}.agents.{args.agent}",
            field="routine",
            hint="Enable the routine in Agent Detail before submitting it.",
        )
    request = JobRequest(
        config_path=config_path,
        team_key=team_id,
        agent_name=instance.name,
        trigger="manual_prompt",
        task_input="",
        routine_id=routine.id,
        memory_override=_memory_override(args, snapshot),
    )
    handle = submit_job_request(request)
    payload = {"job_id": handle.job_id, "status": handle.status, "agent": instance.name, "routine": routine.id}
    _print_json(payload) if args.json else print(f"Queued {handle.job_id}: {instance.name} / {routine.id}")
    return 0


def cmd_inbox(args: Namespace) -> int:
    cli_services = _services(args)
    snapshot = cli_services.config_store.load()
    team_id = _team_id(args, snapshot)
    actor = UserTicketContext(team_id=team_id)
    boards = []
    if cli_services.tickets is not None:
        for binding in cli_services.tickets.list_workflows(actor):
            boards.append(
                build_board_view(
                    cli_services.tickets,
                    actor,
                    binding.workflow_id,
                    ticket_jobs=cli_services.ticket_jobs,
                )
            )
    unassigned = [
        {"workflow": board.binding.workflow_id, "id": ticket.ref.ticket_id, "title": ticket.title}
        for board in boards
        for column in board.columns
        for ticket in column.tickets
        if ticket.assignee is None
    ]
    active = [
        {
            "workflow": board.binding.workflow_id,
            "id": ticket.ref.ticket_id,
            "title": ticket.title,
            "assignee": ticket.assignee,
        }
        for board in boards
        for column in board.columns
        for ticket in column.tickets
        if ticket.active_run_job_id is not None
    ]
    failed_jobs = [
        {"job_id": record.spec.job_id, "agent": record.spec.agent_name, "summary": record.execution_summary}
        for _, record in _job_records(snapshot, team_id)
        if record is not None and record.status == "failed"
    ]
    payload = {
        "team": team_id,
        "unassigned_tickets": unassigned,
        "active_tickets": active,
        "workflow_issues": [
            {"workflow": board.binding.workflow_id, "issues": [issue.model_dump(mode="json") for issue in board.issues]}
            for board in boards
            if board.issues
        ],
        "failed_jobs": failed_jobs,
    }
    if args.json:
        _print_json(payload)
    else:
        print(f"\n{bold(snapshot.config.flowgency.title)} - {snapshot.config.teams[team_id].name}\n")
        print(f"  Unassigned tickets: {len(unassigned)}")
        print(f"  Active tickets: {len(active)}")
        print(f"  Workflow issues: {sum(len(board.issues) for board in boards)}")
        print(f"  Failed jobs: {len(failed_jobs)}\n")
    return 0


def cmd_jobs(args: Namespace) -> int:
    snapshot, team_id, team = _team(args)
    records = _job_records(snapshot, team_id)
    if args.status:
        records = [(path, record) for path, record in records if record and record.status == args.status]
    if args.agent:
        records = [(path, record) for path, record in records if record and record.spec.agent_name == args.agent]
    payload = [
        {
            "job_id": record.spec.job_id,
            "agent": record.spec.agent_name,
            "trigger": record.spec.trigger,
            "status": record.status,
            "changed_files": len(record.changed_files or []),
            "exit_code": record.exit_code,
            "started_at": record.started_at,
            "completed_at": record.completed_at,
            "log": record.stdout_path,
        }
        for _, record in records
        if record is not None
    ]
    if args.json:
        _print_json(payload)
    else:
        print(f"\n{bold('Jobs')} - {team.name} ({len(records)} total)\n")
        for item in payload:
            print(
                f"  {item['status'].ljust(18)} {item['agent'][:16].ljust(16)} "
                f"{item['changed_files']} file(s)  {dim(_relative_time(item['started_at']))}"
            )
            print(f"    {dim(item['job_id'])}")
        if not records:
            print("  No jobs recorded yet.")
        print()
    return 0


def cmd_logs(args: Namespace) -> int:
    try:
        return _cmd_logs_inner(args)
    except BaseException as error:
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise
        return _render_failure(error, json_output=False)


def _cmd_logs_inner(args: Namespace) -> int:
    snapshot, team_id, team = _team(args)
    records = _job_records(snapshot, team_id)
    if not args.job_id:
        rows = [record for _, record in records if record is not None and record.stdout_path]
        print(f"\n{bold('Execution logs')} - {team.name}\n")
        for record in rows[:20]:
            print(f"  {record.status.ljust(18)} {record.spec.job_id}  {record.spec.agent_name}")
        if not rows:
            print("  No execution logs yet.")
        print()
        return 0
    match = next((record for _, record in records if record and record.spec.job_id.startswith(args.job_id)), None)
    if match is None:
        raise CliFailure(ExitCode.OPERATIONAL_FAILURE, "job-not-found", f"No job matching '{args.job_id}'.")
    log_path = match.stderr_path if args.stderr else match.stdout_path
    if not log_path or not Path(log_path).is_file():
        stream = "stderr" if args.stderr else "stdout"
        raise CliFailure(ExitCode.OPERATIONAL_FAILURE, "log-not-found", f"No {stream} log for job {match.spec.job_id}.")
    lines = Path(log_path).read_text(encoding="utf-8", errors="replace").splitlines()
    shown = lines[-args.lines:] if args.lines > 0 else lines
    print(f"\n{bold(match.spec.job_id)} - {log_path} ({match.status})\n")
    for line in shown:
        print(f"  {line}")
    print()
    return 0


def _resolve_memory(args: Namespace, services: FlowgencyServices, snapshot):
    team_id = _team_id(args, snapshot)
    _instance(snapshot, team_id, args.agent)
    selector = MemorySelector(scope=args.scope, channel=args.channel)
    if selector.scope == "routine" and not args.routine:
        raise _validation_failure("invalid-memory-selector", "Routine memory requires --routine.", field="routine")
    return resolve_memory_selector(
        selector,
        job_id=f"cli-preview-{team_id}-{args.agent}",
        team_key=team_id,
        agent_name=args.agent,
        routine_id=args.routine,
        channels=snapshot.config.memory.channels,
        store_root=services.memory_store.root,
    )


def cmd_memory_show(args: Namespace) -> int:
    snapshot = _snapshot(args)
    store = MemoryStore(Path(snapshot.config.flowgency.memory_store))
    job_store = JobStore(Path(snapshot.config.flowgency.memory_store))
    services = FlowgencyServices(
        config_path=snapshot.path,
        config_store=ConfigStore(snapshot.path),
        blueprint_library=None,
        compilation_cache=None,
        memory_store=store,
        prompt_store=PromptStore(Path(snapshot.config.flowgency.prompt_store)),
        job_store=job_store,
        instances=None,
        integrations=REGISTRY,
        startup_error=None,
    )
    resolved = _resolve_memory(args, services, snapshot)
    memory = store.read(resolved)
    files = {name: payload.decode("utf-8") for name, payload in sorted(memory.files.items())}
    payload = {
        "scope": _memory_label(resolved.selector, snapshot.config.memory.channels),
        "revision": memory.revision,
        "files": files,
    }
    if args.json:
        _print_json(payload)
    else:
        print(f"Scope: {payload['scope']}")
        print(f"Revision: {memory.revision}")
        for name, content in files.items():
            print(f"\n--- {name} ---\n{content}", end="" if content.endswith("\n") else "\n")
    return 0


def cmd_memory_save(args: Namespace) -> int:
    snapshot = _snapshot(args)
    store = MemoryStore(Path(snapshot.config.flowgency.memory_store))
    job_store = JobStore(Path(snapshot.config.flowgency.memory_store))
    services = FlowgencyServices(
        config_path=snapshot.path,
        config_store=ConfigStore(snapshot.path),
        blueprint_library=None,
        compilation_cache=None,
        memory_store=store,
        prompt_store=PromptStore(Path(snapshot.config.flowgency.prompt_store)),
        job_store=job_store,
        instances=None,
        integrations=REGISTRY,
        startup_error=None,
    )
    resolved = _resolve_memory(args, services, snapshot)
    payload = sys.stdin.buffer.read() if hasattr(sys.stdin, "buffer") else sys.stdin.read().encode("utf-8")
    saved = store.try_update(
        resolved,
        args.revision,
        lambda current: {**current.files, args.file: payload},
    )
    result = {"revision": saved.revision, "file": args.file}
    _print_json(result) if args.json else print(f"Saved {args.file}; revision {saved.revision}")
    return 0


def _dispatch_interval(config: Any) -> int:
    if hasattr(config, "flowgency"):
        return config.flowgency.dispatch.interval
    return int(config.get("flowgency", {}).get("dispatch", {}).get("interval", 15))


def _dispatch_status_exit_code(status: dict[str, Any]) -> int:
    if status.get("error"):
        return int(ExitCode.OPERATIONAL_FAILURE)
    if status.get("state") == "misconfigured":
        return int(ExitCode.VALIDATION)
    if not status.get("installed") or status.get("state") == "inactive":
        return int(ExitCode.OPERATIONAL_FAILURE)
    return 0


def _print_dispatch_status(status: dict[str, Any]) -> None:
    if status.get("error"):
        print(f"Dispatcher inspection failed: {status['error']}", file=sys.stderr)
    elif not status.get("installed"):
        print("Dispatcher absent")
    elif status.get("state") == "misconfigured":
        print("Dispatcher misconfigured: " + ", ".join(status.get("mismatches", ())))
    elif status.get("state") == "inactive":
        print("Dispatcher inactive")
    else:
        print(f"Dispatcher active: heartbeat every {status['expected_interval']} minutes")



def cmd_dispatch(args: Namespace) -> int:
    try:
        return _cmd_dispatch_inner(args)
    except BaseException as error:
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise
        return _render_failure(error, json_output=False)


def _cmd_dispatch_inner(args: Namespace) -> int:
    store = ConfigStore(_config_path(args))
    try:
        snapshot = store.load()
    except ValidationFailed as error:
        raise CliFailure(ExitCode.VALIDATION, "invalid-config", "Configuration is invalid", tuple(error.issues)) from error
    interval = args.interval if args.interval is not None else _dispatch_interval(snapshot.config)
    if args.dispatch_command == "install":
        if args.interval is not None:
            snapshot = store.patch(
                snapshot.revision,
                lambda raw: raw.setdefault("flowgency", {}).setdefault("dispatch", {}).update({"interval": interval}),
            )
        error = install_timer(str(snapshot.path), interval, replace=args.replace)
        if error:
            raise CliFailure(ExitCode.OPERATIONAL_FAILURE, "dispatcher-install-failed", str(error))
        status = get_timer_status(str(snapshot.path), interval)
        _print_dispatch_status(status)
        return _dispatch_status_exit_code(status)
    if args.dispatch_command == "status":
        status = get_timer_status(str(snapshot.path), interval)
        _print_dispatch_status(status)
        return _dispatch_status_exit_code(status)
    error = uninstall_timer(str(snapshot.path), force=args.force)
    if error:
        raise CliFailure(ExitCode.OPERATIONAL_FAILURE, "dispatcher-uninstall-failed", str(error))
    print("Dispatcher removed")
    return 0


def _add_config(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", default=argparse.SUPPRESS, help="Path to canonical config.yaml")


def _add_team_json(parser: argparse.ArgumentParser) -> None:
    _add_config(parser)
    parser.add_argument("--team", "-t")
    parser.add_argument("--json", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="flowgency", description="Flowgency - AI Agent Management")
    parser.add_argument("--config", help="Path to canonical config.yaml")
    subparsers = parser.add_subparsers(dest="command")

    serve = subparsers.add_parser("serve", help="Start the web dashboard")
    _add_config(serve)
    serve.add_argument("--port", type=int, default=8500)
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--reload", action="store_true")
    serve.set_defaults(handler=cmd_serve)

    status = subparsers.add_parser("status", help="Fleet overview across all teams")
    _add_config(status)
    status.add_argument("--json", action="store_true")
    status.set_defaults(handler=cmd_status)

    validate = subparsers.add_parser("validate", help="Validate the config and configured assets")
    _add_config(validate)
    validate.add_argument("--json", action="store_true")
    validate.set_defaults(handler=cmd_validate)

    register_ticket_commands(subparsers)

    for name, help_text, handler in (
        ("inbox", "What needs attention", cmd_inbox),
        ("agents", "List agent instances", cmd_agents),
    ):
        command = subparsers.add_parser(name, help=help_text)
        _add_team_json(command)
        command.set_defaults(handler=handler)

    agent = subparsers.add_parser("agent", help="Inspect or run one agent")
    _add_config(agent)
    agent_subparsers = agent.add_subparsers(dest="agent_command", required=True)
    show = agent_subparsers.add_parser("show", help="Show one agent")
    _add_team_json(show)
    show.add_argument("agent")
    show.set_defaults(handler=cmd_agent_show)
    run_agent = agent_subparsers.add_parser("run", help="Run an existing routine")
    _add_team_json(run_agent)
    run_agent.add_argument("agent")
    run_agent.add_argument("routine")
    run_agent.add_argument("--memory-scope", choices=("run", "routine", "agent", "team", "channel"))
    run_agent.add_argument("--memory-channel")
    run_agent.set_defaults(handler=cmd_agent_run)

    memory = subparsers.add_parser("memory", help="Show or save semantic memory")
    _add_config(memory)
    memory_subparsers = memory.add_subparsers(dest="memory_command", required=True)
    for name, handler in (("show", cmd_memory_show), ("save", cmd_memory_save)):
        command = memory_subparsers.add_parser(name)
        _add_team_json(command)
        command.add_argument("agent")
        command.add_argument("--scope", choices=("run", "routine", "agent", "team", "channel"), default="agent")
        command.add_argument("--channel")
        command.add_argument("--routine")
        command.set_defaults(handler=handler)
        if name == "save":
            command.add_argument("--revision", required=True)
            command.add_argument("--file", default="memory.md")

    dispatch = subparsers.add_parser("dispatch", help="Manage the global dispatcher")
    _add_config(dispatch)
    dispatch_subparsers = dispatch.add_subparsers(dest="dispatch_command", required=True)
    install = dispatch_subparsers.add_parser("install")
    _add_config(install)
    install.add_argument("--interval", type=int, choices=range(5, 121))
    install.add_argument("--replace", action="store_true")
    install.set_defaults(handler=cmd_dispatch, force=False, json=False)
    dispatch_status = dispatch_subparsers.add_parser("status")
    _add_config(dispatch_status)
    dispatch_status.set_defaults(handler=cmd_dispatch, interval=None, replace=False, force=False, json=False)
    uninstall = dispatch_subparsers.add_parser("uninstall")
    _add_config(uninstall)
    uninstall.add_argument("--force", action="store_true")
    uninstall.set_defaults(handler=cmd_dispatch, interval=None, replace=False, json=False)

    jobs = subparsers.add_parser("jobs", help="List durable agent jobs")
    _add_team_json(jobs)
    jobs.add_argument("--status", "-s")
    jobs.add_argument("--agent", "-a")
    jobs.set_defaults(handler=cmd_jobs)

    logs = subparsers.add_parser("logs", help="Tail or list execution logs")
    _add_config(logs)
    logs.add_argument("job_id", nargs="?")
    logs.add_argument("--team", "-t")
    logs.add_argument("--lines", "-n", type=int, default=40)
    logs.add_argument("--stderr", action="store_true")
    logs.set_defaults(handler=cmd_logs, json=False)
    return parser


def _render_failure(error: BaseException, *, json_output: bool) -> int:
    if isinstance(error, CliFailure):
        render_error(code=error.code, message=error.message, issues=error.issues, json_output=json_output)
        return int(error.exit_code)
    if isinstance(error, ValidationFailed):
        render_error(code="validation-failed", message="Validation failed", issues=tuple(error.issues), json_output=json_output)
        return int(ExitCode.VALIDATION)
    if isinstance(error, (ResourceBusyError, LockCancelledError)):
        render_error(code="resource-busy", message=str(error), json_output=json_output)
        return int(ExitCode.RESOURCE_BUSY)
    if isinstance(error, MemoryConflictError):
        render_error(code="memory-conflict", message="Memory changed; reload before saving.", json_output=json_output)
        return int(ExitCode.OPERATIONAL_FAILURE)
    if isinstance(error, (KeyError, ValueError)):
        render_error(code="validation-failed", message=str(error), json_output=json_output)
        return int(ExitCode.VALIDATION)
    render_error(code="operational-failure", message=str(error), json_output=json_output)
    return int(ExitCode.OPERATIONAL_FAILURE)


def run(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as error:
        return int(error.code or ExitCode.SUCCESS)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    try:
        return int(args.handler(args))
    except BaseException as error:
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise
        return _render_failure(error, json_output=bool(getattr(args, "json", False)))


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
