"""Command-line interface for Agency's strict canonical control plane."""

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

from agency.cli_output import ExitCode, render_error
from agency.blueprints import BlueprintLibrary
from agency.configuration import ConfigSnapshot, ConfigStore, ValidationFailed, ValidationIssue, config_revision, parse_config_canonical
from agency.configuration.effective import resolve_effective_policy
from agency.configuration.models import MemorySelector
from agency.dispatch.install import get_timer_status, install_timer, uninstall_timer
from agency.fs.locks import LockCancelledError, ResourceBusyError
from agency.integrations import REGISTRY
from agency.jobs import JobRequest, JobSubmissionError, submit_job_request
from agency.jobs.authority import JobStore
from agency.jobs.atomic import atomic_write_text
from agency.jobs.prompts import build_decision_prompt, build_routine_task_input
from agency.jobs.store import read_job
from agency.memory import MemoryConflictError, MemoryStore, resolve_memory_selector
from agency.proposals import (
    SKIP_EXECUTION_SUMMARY,
    question_option_labels,
    should_execute_decision,
    validate_answers,
    validate_proposal_schema,
)
from agency.web.dependencies import AgencyServices, build_services


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
    selected = getattr(args, "config", None) or os.environ.get("AGENCY_CONFIG") or (Path.cwd() / "config.yaml")
    return Path(selected).expanduser().resolve()


def _services(args: Namespace) -> AgencyServices:
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
        "Provide a readable strict canonical config and valid configured asset roots.",
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
    parsed = parse_config_canonical(raw, path)
    return ConfigSnapshot(
        path=path,
        revision=config_revision(payload),
        raw=raw,
        config=parsed.resolved,
    )


def _group_id(args: Namespace, snapshot) -> str:
    group_id = getattr(args, "group", None) or snapshot.config.agency.default_group
    if not group_id:
        raise _validation_failure(
            "missing-group",
            "No group was selected and no default group is configured.",
            field="group",
            hint="Pass --group or configure agency.default_group.",
        )
    if group_id not in snapshot.config.groups:
        raise _validation_failure(
            "unknown-group",
            f"Unknown group: {group_id}",
            field="group",
            hint="Choose a group defined in config.yaml.",
        )
    return group_id


def _group(args: Namespace):
    snapshot = _snapshot(args)
    group_id = _group_id(args, snapshot)
    return snapshot, group_id, snapshot.config.groups[group_id]


def _resolve_group(args: Namespace) -> dict[str, Any]:
    snapshot, group_id, group = _group(args)
    return {
        "key": group_id,
        "name": group.name,
        "path": group.path,
        "shared": group.path / "shared",
        "agents": list(group.agents),
        "_agents_normalized": [
            {
                "name": instance.name,
                "integration": instance.integration,
                "integration_config": dict(instance.integration_config),
                "capabilities": {"write": instance.capabilities.write},
            }
            for instance in group.agents.values()
        ],
        "_snapshot": snapshot,
        "_group_config": group,
    }


def _instance(snapshot, group_id: str, agent_id: str):
    try:
        return snapshot.config.groups[group_id].agents[agent_id]
    except KeyError as error:
        raise _validation_failure(
            "unknown-agent",
            f"Unknown agent: {agent_id}",
            scope=f"groups.{group_id}",
            field="agent",
            hint="Choose an agent instance owned by this group.",
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


def _extract_title(body: str, fallback: str) -> str:
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        parts = stripped.split("**")
        if len(parts) >= 3 and parts[1].strip():
            return parts[1].strip().rstrip(".,;:!?")
    return fallback.replace("-", " ")


def _markdown_items(group_path: Path, kind: str) -> list[dict[str, Any]]:
    directory = group_path / "shared" / kind
    if not directory.is_dir():
        return []
    items = []
    for path in sorted(directory.glob("*.md"), reverse=True):
        metadata, body = _parse_frontmatter(path.read_text(encoding="utf-8"))
        items.append({**metadata, "_slug": path.stem, "_title": _extract_title(body, path.stem), "_path": path})
    return items


def _job_records(snapshot, group_id: str):
    job_store = JobStore(snapshot.config.agency.memory_store)
    records = []
    for path in job_store.paths(group_id):
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


def _active_job(snapshot, group_id: str, agent_name: str):
    records = JobStore(snapshot.config.agency.memory_store).active(group_id, agent_name)
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
        "sandbox": {
            "mode": policy.sandbox_mode,
            "roots": [str(path).replace("\\", "/") for path in policy.sandbox_roots],
        },
        "tools": {"mode": policy.tools.mode, "names": list(policy.tools.names)},
    }


def _cache_status(services: AgencyServices, instance, inspection) -> str:
    integration = services.integrations.get(instance.integration)
    projector = integration.projector if integration is not None else None
    if projector is None or services.compilation_cache is None:
        return "unavailable"
    entry = services.compilation_cache.root / instance.integration / projector.version / inspection.snapshot.digest
    return "compiled" if (entry / "manifest.json").is_file() else "missing"


def _read_only_runtime(snapshot) -> tuple[BlueprintLibrary, dict[str, Any]]:
    return BlueprintLibrary(Path(snapshot.config.agency.agent_library)), dict(REGISTRY)


def _cache_status_read_only(snapshot, integrations: dict[str, Any], instance, inspection) -> str:
    integration = integrations.get(instance.integration)
    projector = integration.projector if integration is not None else None
    if projector is None:
        return "unavailable"
    entry = Path(snapshot.config.agency.compilation_cache) / instance.integration / projector.version / inspection.snapshot.digest
    return "compiled" if (entry / "manifest.json").is_file() else "missing"


def _agent_payload_read_only(snapshot, group_id: str, instance) -> dict[str, Any]:
    group = snapshot.config.groups[group_id]
    current = _active_job(snapshot, group_id, instance.name)
    library, integrations = _read_only_runtime(snapshot)
    inspection = library.inspect(instance.blueprint)
    policy = resolve_effective_policy(snapshot.config, group_id, instance.name)
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


def _agent_payload(services: AgencyServices, snapshot, group_id: str, instance) -> dict[str, Any]:
    group = snapshot.config.groups[group_id]
    current = _active_job(snapshot, group_id, instance.name)
    inspection = services.blueprint_library.inspect(instance.blueprint)
    policy = resolve_effective_policy(snapshot.config, group_id, instance.name)
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
    importlib.import_module("agency.app").run_server(**options)


def cmd_serve(args: Namespace) -> int:
    os.environ["AGENCY_CONFIG"] = str(_config_path(args))
    run_server(host=args.host, port=args.port, reload=args.reload)
    return 0


def cmd_status(args: Namespace) -> int:
    snapshot = _snapshot(args)
    job_store = JobStore(snapshot.config.agency.memory_store)
    result = {}
    for group_id, group in snapshot.config.groups.items():
        observations = _markdown_items(group.path, "observations")
        proposals = _markdown_items(group.path, "proposals")
        decisions = _markdown_items(group.path, "decisions")
        result[group_id] = {
            "name": group.name,
            "observations": len(observations),
            "proposals": len(proposals),
            "decisions": len(decisions),
            "agents": len(group.agents),
            "active": sum(bool(job_store.active(group_id, name)) for name in group.agents),
        }
    if args.json:
        _print_json(result)
    else:
        print(f"\n{bold(snapshot.config.agency.title)} - Fleet Status\n")
        for group_id, item in result.items():
            print(f"  {bold(item['name'])} ({group_id})")
            print(
                f"    {item['agents']} agents - {item['active']} active - {item['observations']} observations - "
                f"{item['proposals']} proposals - {item['decisions']} decisions"
            )
        print()
    return 0


def cmd_agents(args: Namespace) -> int:
    snapshot = _snapshot(args)
    group_id = _group_id(args, snapshot)
    group = snapshot.config.groups[group_id]
    payload = [_agent_payload_read_only(snapshot, group_id, instance) for instance in group.agents.values()]
    if args.json:
        _print_json(payload)
    else:
        print(f"\n{bold('Agents')} - {group.name}\n")
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
    group_id = _group_id(args, snapshot)
    instance = _instance(snapshot, group_id, args.agent)
    payload = _agent_payload_read_only(snapshot, group_id, instance)
    payload.update(
        title=instance.identity.title,
        emoji=instance.identity.emoji,
        routines=[
            {
                "id": routine.id,
                "skill": routine.skill,
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
    group_id = _group_id(args, snapshot)
    instance = _instance(snapshot, group_id, args.agent)
    routine = next((item for item in instance.routines if item.id == args.routine), None)
    if routine is None:
        raise _validation_failure(
            "unknown-routine",
            f"Unknown routine '{args.routine}' for agent '{args.agent}'.",
            scope=f"groups.{group_id}.agents.{args.agent}",
            field="routine",
            hint="Choose an existing stable routine ID.",
        )
    if not routine.enabled:
        raise _validation_failure(
            "routine-disabled",
            f"Routine '{routine.id}' is disabled; enable it before running.",
            scope=f"groups.{group_id}.agents.{args.agent}",
            field="routine",
            hint="Enable the routine in Agent Detail before submitting it.",
        )
    request = JobRequest(
        config_path=config_path,
        group_key=group_id,
        agent_name=instance.name,
        trigger="manual_prompt",
        task_input=build_routine_task_input(routine.id, routine.arguments),
        routine_id=routine.id,
        memory_override=_memory_override(args, snapshot),
    )
    handle = submit_job_request(request)
    payload = {"job_id": handle.job_id, "status": handle.status, "agent": instance.name, "routine": routine.id}
    _print_json(payload) if args.json else print(f"Queued {handle.job_id}: {instance.name} / {routine.id}")
    return 0


def _list_command(args: Namespace, kind: str) -> int:
    _, _, group = _group(args)
    items = _markdown_items(group.path, kind)
    if getattr(args, "status", None):
        items = [item for item in items if item.get("status") == args.status]
    agent_key = "origin_agent" if kind == "proposals" else "agent"
    if getattr(args, "agent", None):
        items = [item for item in items if item.get(agent_key) == args.agent]
    payload = [
        {
            "slug": item["_slug"],
            "title": item.get("_title", item["_slug"]),
            "agent": item.get(agent_key, ""),
            "status": item.get("status", ""),
            "date": str(item.get("date", "")),
        }
        for item in items
    ]
    if args.json:
        _print_json(payload)
    else:
        print(f"\n{bold(kind.title())} - {group.name} ({len(payload)} total)\n")
        for item in payload:
            print(f"  {item['agent'][:16].rjust(16)}  {item['title'][:60]}  {dim(item['status'])}")
        print()
    return 0


def cmd_observations(args: Namespace) -> int:
    return _list_command(args, "observations")


def cmd_proposals(args: Namespace) -> int:
    return _list_command(args, "proposals")


def cmd_decisions(args: Namespace) -> int:
    _, _, group = _group(args)
    items = _markdown_items(group.path, "decisions")
    payload = [
        {
            "slug": item["_slug"],
            "title": item.get("_title", item["_slug"]),
            "answers": item.get("answers", {}),
            "date": str(item.get("date", "")),
        }
        for item in items
    ]
    if args.json:
        _print_json(payload)
    else:
        print(f"\n{bold('Decisions')} - {group.name} ({len(payload)} total)\n")
        for item in payload:
            print(f"  decided  {item['title'][:60]}  {dim(item['date'])}")
        print()
    return 0


def cmd_inbox(args: Namespace) -> int:
    snapshot, group_id, group = _group(args)
    observations = _markdown_items(group.path, "observations")
    proposals = _markdown_items(group.path, "proposals")
    decisions = _markdown_items(group.path, "decisions")
    actionable = [item for item in proposals if item.get("status") in {"proposed", "investigating"}]
    floated = [item for item in observations if item.get("float") and item.get("status") == "open"]
    open_items = [item for item in observations if item.get("status") == "open"]
    payload = {
        "group": group_id,
        "actionable_proposals": [
            {"slug": item["_slug"], "title": item["_title"], "status": item.get("status", ""), "agent": item.get("origin_agent", "")}
            for item in actionable
        ],
        "floated_observations": [
            {"slug": item["_slug"], "title": item["_title"], "agent": item.get("agent", "")}
            for item in floated
        ],
        "open_observations": len(open_items),
        "total_decisions": len(decisions),
    }
    if args.json:
        _print_json(payload)
    else:
        print(f"\n{bold(snapshot.config.agency.title)} - {group.name}\n")
        print(f"  Needs decision: {len(actionable)}")
        print(f"  Floated signals: {len(floated)}")
        print(f"  Open observations: {len(open_items)}\n")
    return 0


def cmd_jobs(args: Namespace) -> int:
    snapshot, group_id, group = _group(args)
    records = _job_records(snapshot, group_id)
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
        print(f"\n{bold('Jobs')} - {group.name} ({len(records)} total)\n")
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
    snapshot, group_id, group = _group(args)
    records = _job_records(snapshot, group_id)
    if not args.job_id:
        rows = [record for _, record in records if record is not None and record.stdout_path]
        print(f"\n{bold('Execution logs')} - {group.name}\n")
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


def _resolve_memory(args: Namespace, services: AgencyServices, snapshot):
    group_id = _group_id(args, snapshot)
    _instance(snapshot, group_id, args.agent)
    selector = MemorySelector(scope=args.scope, channel=args.channel)
    if selector.scope == "routine" and not args.routine:
        raise _validation_failure("invalid-memory-selector", "Routine memory requires --routine.", field="routine")
    return resolve_memory_selector(
        selector,
        job_id=f"cli-preview-{group_id}-{args.agent}",
        group_key=group_id,
        agent_name=args.agent,
        routine_id=args.routine,
        channels=snapshot.config.memory.channels,
        store_root=services.memory_store.root,
    )


def cmd_memory_show(args: Namespace) -> int:
    snapshot = _snapshot(args)
    store = MemoryStore(Path(snapshot.config.agency.memory_store))
    job_store = JobStore(Path(snapshot.config.agency.memory_store))
    services = AgencyServices(
        config_path=snapshot.path,
        config_store=ConfigStore(snapshot.path),
        blueprint_library=None,
        compilation_cache=None,
        memory_store=store,
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
    store = MemoryStore(Path(snapshot.config.agency.memory_store))
    job_store = JobStore(Path(snapshot.config.agency.memory_store))
    services = AgencyServices(
        config_path=snapshot.path,
        config_store=ConfigStore(snapshot.path),
        blueprint_library=None,
        compilation_cache=None,
        memory_store=store,
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
    if hasattr(config, "agency"):
        return config.agency.dispatch.interval
    return int(config.get("agency", {}).get("dispatch", {}).get("interval", 15))


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
                lambda raw: raw.setdefault("agency", {}).setdefault("dispatch", {}).update({"interval": interval}),
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


def _write_frontmatter(path: Path, metadata: dict[str, Any], body: str = "") -> None:
    frontmatter = yaml.safe_dump(metadata, sort_keys=False).strip()
    suffix = f"\n{body}" if body else "\n"
    atomic_write_text(path, f"---\n{frontmatter}\n---{suffix}")


def _update_frontmatter_field(path: Path, field: str, value: Any) -> None:
    metadata, body = _parse_frontmatter(path.read_text(encoding="utf-8"))
    metadata[field] = value
    _write_frontmatter(path, metadata, body)


def cmd_decide(args: Namespace) -> int:
    try:
        return _cmd_decide_inner(args)
    except EOFError as error:
        return _render_failure(
            CliFailure(ExitCode.OPERATIONAL_FAILURE, "input-closed", "Input closed unexpectedly."),
            json_output=False,
        )
    except BaseException as error:
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise
        return _render_failure(error, json_output=False)


def _cmd_decide_inner(args: Namespace) -> int:
    config_path = _config_path(args)
    runtime_group = _resolve_group(args)
    snapshot = runtime_group.get("_snapshot")
    group_id = runtime_group["key"]
    proposal_path = runtime_group["shared"] / "proposals" / f"{args.slug}.md"
    decision_path = runtime_group["shared"] / "decisions" / f"{args.slug}.md"
    if not proposal_path.is_file():
        raise CliFailure(ExitCode.OPERATIONAL_FAILURE, "proposal-not-found", f"Proposal '{args.slug}' not found.")
    metadata, body = _parse_frontmatter(proposal_path.read_text(encoding="utf-8"))
    schema_errors = validate_proposal_schema(metadata)
    if schema_errors:
        issues = tuple(
            _issue("invalid-proposal", f"proposals.{args.slug}", "questions", message, "Correct the proposal frontmatter.")
            for message in schema_errors
        )
        raise CliFailure(ExitCode.VALIDATION, "invalid-proposal", "Proposal is invalid", issues)
    eligible = [
        item["name"]
        for item in runtime_group.get("_agents_normalized", ())
        if bool(item.get("capabilities", {}).get("write"))
    ]
    declared = metadata["execution_agent"].strip()
    if declared not in eligible:
        raise _validation_failure(
            "invalid-execution-agent",
            f"execution_agent '{declared}' is not available or not writable.",
            field="execution_agent",
        )
    print(f"\n{bold(args.slug)}\n\n  Executor:")
    for index, name in enumerate(eligible, 1):
        print(f"    [{index}] {name}{' (default)' if name == declared else ''}")
    execution_agent = None
    while execution_agent is None:
        raw = input("  > ").strip()
        if not raw:
            execution_agent = declared
        elif raw.isdigit() and 1 <= int(raw) <= len(eligible):
            execution_agent = eligible[int(raw) - 1]
        else:
            print(f"     Enter a number 1-{len(eligible)} or press Enter for default.")
    answers: dict[str, Any] = {}
    questions = metadata["questions"]
    for index, question in enumerate(questions, 1):
        print(f"\n  {index}. {question['prompt']}")
        question_id = question["id"]
        question_type = question["type"]
        required = question.get("required", True) is not False
        if question_type == "boolean":
            while True:
                choice = input("     [a]pprove / [d]ecline > ").strip().lower()
                if choice in {"a", "approve"}:
                    answers[question_id] = "approved"
                    break
                if choice in {"d", "decline"}:
                    answers[question_id] = "declined"
                    break
                print("     Enter a/approve or d/decline.")
        elif question_type == "choice":
            labels = question_option_labels(question)
            for option, label in enumerate(labels, 1):
                print(f"     [{option}] {label}")
            if question.get("multi"):
                while True:
                    raw = input("     > ").strip()
                    indices = [int(value.strip()) for value in raw.split(",") if value.strip().isdigit()]
                    selected = list(dict.fromkeys(labels[value - 1] for value in indices if 1 <= value <= len(labels)))
                    if not raw or selected:
                        answers[question_id] = selected
                        break
                    print(f"     No valid selections. Enter numbers 1-{len(labels)} or leave blank to skip.")
            else:
                while True:
                    raw = input("     > ").strip()
                    if raw.isdigit() and 1 <= int(raw) <= len(labels):
                        answers[question_id] = labels[int(raw) - 1]
                        break
                    print(f"     Enter a number 1-{len(labels)}.")
        else:
            while True:
                answer = input("     > ").strip()
                if answer or not required:
                    answers[question_id] = answer
                    break
    note = input("\n  Decision note (optional): ").strip()
    answer_errors = validate_answers(questions, answers)
    if answer_errors:
        issues = tuple(
            _issue("invalid-answer", f"proposals.{args.slug}", "answers", message, "Answer every required question.")
            for message in answer_errors
        )
        raise CliFailure(ExitCode.VALIDATION, "invalid-answers", "Decision answers are invalid", issues)
    decision = {
        "proposal": f"{args.slug}.md",
        "decided_by": "cli",
        "date": datetime.now().strftime("%Y-%m-%d"),
        "answers": answers,
        "decision_note": note,
        "execution_agent": execution_agent,
        "execution_job_history": [],
    }
    decision_path.parent.mkdir(parents=True, exist_ok=True)
    if should_execute_decision(questions, answers, note):
        request = JobRequest(
            config_path=config_path,
            group_key=group_id,
            agent_name=execution_agent,
            trigger="decision",
            task_input=build_decision_prompt(body, answers, note),
            trigger_context={"decision_path": str(decision_path.resolve()), "proposal_path": str(proposal_path.resolve())},
        )
        decision.update(execution_status="pending", execution_job_id=request.job_id)
        _write_frontmatter(decision_path, decision)
        try:
            submit_job_request(request)
        except JobSubmissionError:
            decision_path.unlink(missing_ok=True)
            raise
    else:
        decision.update(execution_status="skipped", execution_summary=SKIP_EXECUTION_SUMMARY)
        _write_frontmatter(decision_path, decision)
    _update_frontmatter_field(proposal_path, "status", "decided")
    print(f"Decision saved: shared/decisions/{args.slug}.md")
    return 0


def _add_config(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", default=argparse.SUPPRESS, help="Path to strict canonical config.yaml")


def _add_group_json(parser: argparse.ArgumentParser) -> None:
    _add_config(parser)
    parser.add_argument("--group", "-g")
    parser.add_argument("--json", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="christag-agency", description="Agency - AI Agent Management")
    parser.add_argument("--config", help="Path to strict canonical config.yaml")
    subparsers = parser.add_subparsers(dest="command")

    serve = subparsers.add_parser("serve", help="Start the web dashboard")
    _add_config(serve)
    serve.add_argument("--port", type=int, default=8500)
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--reload", action="store_true")
    serve.set_defaults(handler=cmd_serve)

    status = subparsers.add_parser("status", help="Fleet overview across all groups")
    _add_config(status)
    status.add_argument("--json", action="store_true")
    status.set_defaults(handler=cmd_status)

    for name, help_text, handler in (
        ("inbox", "What needs attention", cmd_inbox),
        ("agents", "List agent instances", cmd_agents),
        ("decisions", "List decisions", cmd_decisions),
    ):
        command = subparsers.add_parser(name, help=help_text)
        _add_group_json(command)
        command.set_defaults(handler=handler)

    observations = subparsers.add_parser("observations", help="List observations")
    _add_group_json(observations)
    observations.add_argument("--status", "-s")
    observations.add_argument("--agent", "-a")
    observations.set_defaults(handler=cmd_observations)

    proposals = subparsers.add_parser("proposals", help="List proposals")
    _add_group_json(proposals)
    proposals.add_argument("--status", "-s")
    proposals.set_defaults(handler=cmd_proposals)

    decide = subparsers.add_parser("decide", help="Answer a proposal's questions")
    _add_config(decide)
    decide.add_argument("slug")
    decide.add_argument("--group", "-g")
    decide.set_defaults(handler=cmd_decide, json=False)

    agent = subparsers.add_parser("agent", help="Inspect or run one agent")
    _add_config(agent)
    agent_subparsers = agent.add_subparsers(dest="agent_command", required=True)
    show = agent_subparsers.add_parser("show", help="Show one agent")
    _add_group_json(show)
    show.add_argument("agent")
    show.set_defaults(handler=cmd_agent_show)
    run_agent = agent_subparsers.add_parser("run", help="Run an existing routine")
    _add_group_json(run_agent)
    run_agent.add_argument("agent")
    run_agent.add_argument("routine")
    run_agent.add_argument("--memory-scope", choices=("run", "routine", "agent", "group", "channel"))
    run_agent.add_argument("--memory-channel")
    run_agent.set_defaults(handler=cmd_agent_run)

    memory = subparsers.add_parser("memory", help="Show or save semantic memory")
    _add_config(memory)
    memory_subparsers = memory.add_subparsers(dest="memory_command", required=True)
    for name, handler in (("show", cmd_memory_show), ("save", cmd_memory_save)):
        command = memory_subparsers.add_parser(name)
        _add_group_json(command)
        command.add_argument("agent")
        command.add_argument("--scope", choices=("run", "routine", "agent", "group", "channel"), default="agent")
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
    _add_group_json(jobs)
    jobs.add_argument("--status", "-s")
    jobs.add_argument("--agent", "-a")
    jobs.set_defaults(handler=cmd_jobs)

    logs = subparsers.add_parser("logs", help="Tail or list execution logs")
    _add_config(logs)
    logs.add_argument("job_id", nargs="?")
    logs.add_argument("--group", "-g")
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
