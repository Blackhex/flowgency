from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import dataclasses
import hashlib
import inspect
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import stat
import subprocess
import sys
import time
from urllib.request import urlopen

import yaml
from fastapi import HTTPException, Request, Response

from flowgency.configuration.models import MemorySelector
from flowgency.configuration.store import ConfigStore
from flowgency.fs.atomic import atomic_write_bytes
from flowgency.fs.locks import exclusive_lock
from flowgency.integrations import REGISTRY
from flowgency.jobs.models import JobHandle
from flowgency.jobs.authority import JobStore
from flowgency.jobs.models import BlueprintRef, JobRecord, JobSpec, MemoryBinding, RuntimePolicySnapshot
from flowgency.jobs.store import transition_job, write_job
from flowgency.memory import MemoryStore, resolve_memory_selector
from flowgency.prompts import PromptStore
from flowgency.tickets.models import ActiveTicketRun, StorageBinding, TicketEvent, TicketOperation, TicketRecord, TicketRef
from flowgency.tickets.storages.local import LocalTicketStorage
from flowgency.workflows.models import ArtifactRef


ROOT = Path(__file__).resolve().parents[2]
RUNTIME_PARENT = Path(__file__).resolve().parent / ".runtime"
RUNTIME_ROOT = RUNTIME_PARENT / "current"
FIXTURE_CONFIG = Path(__file__).resolve().parent / "fixtures" / "config.yaml"
FIXED_NOW = "2026-07-16T12:00:00+00:00"
UI_RESET_PATH = "/__ui/reset"
ACTIVITY_LOGS_FIXTURE = "agent-activity-logs"
GIT_EVIDENCE_FIXTURE = "git-evidence"
SUPPORTED_UI_FIXTURES = frozenset({"default", ACTIVITY_LOGS_FIXTURE, GIT_EVIDENCE_FIXTURE})
GIT_EVIDENCE_TICKET_ID = "fixture-git-evidence"
GIT_EVIDENCE_REF = "refs/heads/main"
GIT_EVIDENCE_INDEX = "fixture-index"


def _ui_sitecustomize(runtime: Path) -> Path:
    support = runtime / "test-support"
    support.mkdir(parents=True, exist_ok=True)
    _write(
        support / "sitecustomize.py",
        "from tests.ui.server import _install_ui_test_runtime\n"
        "\n"
        "_install_ui_test_runtime()\n",
    )
    return support


def _ui_memory_binding(
    memory_root: Path, *, team_key: str, agent_name: str, job_id: str
) -> MemoryBinding:
    """Build an agent-scope memory binding the way a real job persists one.

    ``selector`` stores only the ``MemorySelector`` dump (scope/channel); the
    team/agent identity lives in the hash criteria, not the selector, so the
    strict service schema accepts what the Jobs view later revalidates.
    """
    resolved = resolve_memory_selector(
        MemorySelector(scope="agent"),
        job_id=job_id,
        team_key=team_key,
        agent_name=agent_name,
        routine_id=None,
        channels={},
        store_root=memory_root,
    )
    memory_path = memory_root / "ui-ticket-memory" / team_key / agent_name
    memory_path.mkdir(parents=True, exist_ok=True)
    return MemoryBinding(
        selector=resolved.selector.model_dump(mode="python"),
        canonical_json=resolved.canonical_json,
        memory_hash=resolved.memory_hash,
        path=str(memory_path.resolve()),
    )


def _write_runtime_config(config_path: Path, config: dict) -> None:
    """Write the runtime config the way ``ConfigStore`` does: atomically and
    under the shared config lock so a concurrent board/jobs request never reads
    a truncated ``config.yaml`` mid-reset."""
    payload = yaml.safe_dump(config, sort_keys=False).encode("utf-8")
    lock_path = config_path.with_suffix(f"{config_path.suffix}.lock")
    with exclusive_lock(lock_path, wait=True):
        atomic_write_bytes(config_path, payload)


def _ui_submit_job_request(request, launcher=None) -> JobHandle:
    del launcher
    config_store = ConfigStore(Path(request.config_path))
    snapshot = config_store.load()
    team = snapshot.config.teams[request.team_key]
    agent = team.agents[request.agent_name]
    memory_root = Path(snapshot.config.flowgency.memory_store)
    job_store = JobStore(memory_root)
    memory_binding = _ui_memory_binding(
        memory_root,
        team_key=request.team_key,
        agent_name=request.agent_name,
        job_id=request.job_id,
    )
    cache_path = Path(snapshot.config.flowgency.compilation_cache) / "ticket-test" / "ui" / request.agent_name
    cache_path.mkdir(parents=True, exist_ok=True)
    timeout = getattr(team.runtime, "timeout", 1800)
    mode = getattr(team.permissions, "mode", "unrestricted")
    spec = JobSpec(
        schema_version=6,
        job_id=request.job_id,
        config_path=str(config_store.path.resolve()),
        config_revision=snapshot.revision,
        team_key=request.team_key,
        workspace_root=str(team.workspace_path.resolve()),
        team_root=str(team.path.resolve()),
        agent_name=request.agent_name,
        trigger="ticket",
        integration_name=agent.integration,
        integration_config={},
        blueprint=BlueprintRef(
            key=agent.blueprint,
            source_digest="0" * 64,
            integration=agent.integration,
            projector_version="ui-ticket",
            cache_path=str(cache_path.resolve()),
            instance_digest="1" * 64,
        ),
        routine_id=None,
        skill=None,
        skill_arguments=(),
        task_input=request.task_input,
        runtime_policy=RuntimePolicySnapshot(timeout=timeout, mode=mode),
        memory=memory_binding,
        trigger_context=request.trigger_context,
        prompt_source={
            "type": "ticket",
            "scope": "ticket",
            "name": "ticket-run",
            "source_path": "ticket.prompt.md",
            "source_digest": "0" * 64,
        },
        timeout_override=request.timeout_override,
        created_at=FIXED_NOW,
        private_prompts=(),
        ticket_target=request.ticket_target,
    )
    authority = job_store.create(JobRecord.from_spec(spec, due_at=request.due_at))
    return JobHandle(spec.job_id, "queued", authority.path, None)


def _install_ui_test_runtime() -> None:
    import flowgency.jobs.submission as submission_module
    import flowgency.web.dependencies as web_dependencies
    from flowgency.app import app
    from tests._ticket_helpers import TicketRuntimeIntegration

    class UITicketRuntimeIntegration(TicketRuntimeIntegration):
        name = "ticket-test"
        display_name = "UI Ticket Test Runtime"

    REGISTRY["ticket-test"] = UITicketRuntimeIntegration()
    submission_module.submit_job_request = _ui_submit_job_request
    web_dependencies.submit_job_request = _ui_submit_job_request

    if getattr(app.state, "ui_reset_route_installed", False):
        return

    @app.post(UI_RESET_PATH, include_in_schema=False)
    async def reset_ui_runtime(request: Request) -> Response:
        runtime_root = Path(os.environ["FLOWGENCY_UI_RUNTIME"])
        fixture = "default"
        content_type = request.headers.get("content-type", "")
        if content_type.startswith("application/json"):
            try:
                payload = await request.json()
            except json.JSONDecodeError as exc:
                raise HTTPException(status_code=400, detail="Reset payload must be valid JSON") from exc
            if payload is not None and not isinstance(payload, dict):
                raise HTTPException(status_code=400, detail="Reset payload must be an object")
            if isinstance(payload, dict):
                raw_fixture = payload.get("fixture")
                if raw_fixture is not None and not isinstance(raw_fixture, str):
                    raise HTTPException(status_code=400, detail="fixture must be a string")
                fixture = str(raw_fixture or "default").strip() or "default"
        if fixture not in SUPPORTED_UI_FIXTURES:
            raise HTTPException(status_code=400, detail="Unsupported fixture")
        _reset_runtime_state(runtime_root, fixture=fixture)
        return Response(status_code=204)

    app.state.ui_reset_route_installed = True


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _clear_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    for child in path.iterdir():
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)


def _configured_workflow_roots(config: dict) -> tuple[Path, ...]:
    roots: list[Path] = []
    for team in (config.get("teams") or {}).values():
        for workflow in ((team or {}).get("workflows") or {}).values():
            root = ((workflow or {}).get("integration_config") or {}).get("root")
            if root:
                roots.append(Path(root))
    return tuple(roots)


def _clear_directory_keeping(path: Path, keep: frozenset[Path]) -> None:
    path.mkdir(parents=True, exist_ok=True)
    for child in path.iterdir():
        if not child.is_dir():
            child.unlink(missing_ok=True)
        elif child in keep:
            _clear_directory_keeping(child, keep)
        else:
            shutil.rmtree(child, ignore_errors=True)


def _clear_workflow_roots(runtime: Path, config: dict) -> None:
    """Clear seeded tickets while every configured workflow root stays present.

    A board request that races a reset revalidates its configured root, so that
    directory must never be absent, not even between two removals.
    """
    tickets = runtime / "tickets"
    roots = tuple(
        root
        for root in _configured_workflow_roots(config)
        if root == tickets or tickets in root.parents
    )
    for root in roots:
        root.mkdir(parents=True, exist_ok=True)
    keep = frozenset(
        ancestor
        for root in roots
        for ancestor in (root, *root.parents)
        if ancestor == tickets or tickets in ancestor.parents
    )
    _clear_directory_keeping(tickets, keep)



def _set_mtime(path: Path, value: str) -> None:
    timestamp = datetime.fromisoformat(value).astimezone(timezone.utc).timestamp()
    os.utime(path, (timestamp, timestamp))


def _write_log(path: Path, content: str, *, mtime: str) -> None:
    _write(path, content)
    _set_mtime(path, mtime)


def _replace_runtime(value: object, runtime: Path) -> object:
    if isinstance(value, str):
        return value.replace("__RUNTIME__", runtime.as_posix())
    if isinstance(value, list):
        return [_replace_runtime(item, runtime) for item in value]
    if isinstance(value, dict):
        return {key: _replace_runtime(item, runtime) for key, item in value.items()}
    return value


def _prompt_bytes(name: str, description: str, body: str, *, argument_hint: str | None = None) -> bytes:
    metadata = [f"name: {name}", f"description: {description}"]
    if argument_hint is not None:
        metadata.append(f"argument-hint: {argument_hint}")
    return ("---\n" + "\n".join(metadata) + f"\n---\n\n{body.rstrip()}\n").encode("utf-8")


def _seed_blueprint(
    library: Path,
    key: str,
    title: str,
    skill: str,
    *,
    prompts: tuple[tuple[str, str, str, str | None], ...],
) -> None:
    _write(library / key / "AGENTS.md", f"# {title}\n\nDeterministic release-gate instructions.\n")
    _write(
        library / key / ".agents" / "skills" / skill / "SKILL.md",
        f"---\nname: {skill}\ndescription: Release gate skill\n---\n\nRun the deterministic workflow.\n",
    )
    _write(library / key / ".agents" / "skills" / skill / "checklist.md", "- Verify content\n")
    prompt_root = library / key / ".agents" / "prompts"
    for name, description, body, argument_hint in prompts:
        prompt_root.mkdir(parents=True, exist_ok=True)
        (prompt_root / f"{name}.prompt.md").write_bytes(
            _prompt_bytes(name, description, body, argument_hint=argument_hint)
        )


def _seed_workflow_blueprint(library: Path, key: str, definition: dict) -> None:
    directory = library / key
    directory.mkdir(parents=True, exist_ok=True)
    _write(
        directory / "workflow.yaml",
        yaml.safe_dump(definition, sort_keys=False, allow_unicode=True),
    )


def _ticket_operation(label: str) -> TicketOperation:
    return TicketOperation(operation_id=f"seed-{label}", request_digest=f"seed-{label}")


def _ticket_record(
    binding,
    *,
    ticket_id: str,
    number: int,
    title: str,
    description: str,
    state_id: str,
    assignee: str | None = None,
    active_run: ActiveTicketRun | None = None,
    field_values: dict | None = None,
    events: tuple[TicketEvent, ...] | None = None,
) -> TicketRecord:
    ticket_events = list(events or (TicketEvent(kind="opened", actor="local-user", summary="Ticket created"),))
    if assignee is not None and not any(event.kind == "assigned" for event in ticket_events):
        ticket_events.append(
            TicketEvent(
                kind="assigned",
                actor="local-user",
                summary=f"Assigned to {assignee}",
            )
        )
    return TicketRecord(
        id=ticket_id,
        number=number,
        title=title,
        description=description,
        state_id=state_id,
        assignee=assignee,
        active_run=active_run,
        field_values=dict(field_values or {}),
        field_provenance={},
        revision=1,
        events=tuple(ticket_events),
        receipts=(),
        created_at=datetime.fromisoformat(FIXED_NOW),
        updated_at=datetime.fromisoformat(FIXED_NOW),
        ref=TicketRef.from_binding(binding, ticket_id),
    )


def _delivery_definition() -> dict:
    return {
        "schema_version": 1,
        "id": "delivery",
        "name": "Delivery",
        "description": "Deliver verified work.",
        "initial_state": "backlog",
        "states": [
            {"id": "backlog", "name": "Backlog", "color": "#9ca3af"},
            {"id": "in-progress", "name": "In progress", "color": "#8cb8ff"},
            {"id": "review", "name": "Review", "color": "#ebc77c"},
            {"id": "done", "name": "Done", "color": "#7ad7bf"},
        ],
        "fields": [
            {"id": "acceptance-criteria", "label": "Acceptance criteria", "type": "text"},
            {"id": "review-verdict", "label": "Review verdict", "type": "text"},
            {"id": "test-report", "label": "Test report", "type": "artifact"},
        ],
        "transitions": [
            {
                "id": "complete-review",
                "name": "Complete review",
                "from_state": "review",
                "to_state": "done",
                "inputs": [{"field_id": "acceptance-criteria", "required": True}],
                "outputs": [
                    {"field_id": "review-verdict", "required": True},
                    {"field_id": "test-report", "required": False},
                ],
                "preconditions": [],
                "criteria": [
                    {"id": "implementation-satisfies", "description": "The implementation satisfies the acceptance criteria."}
                ],
            }
        ],
    }


def _research_definition() -> dict:
    return {
        "schema_version": 1,
        "id": "research-workflow",
        "name": "Research",
        "description": "Investigate workflow questions.",
        "initial_state": "question",
        "states": [
            {"id": "question", "name": "Questions", "color": "#9ca3af"},
            {"id": "investigating", "name": "Investigating", "color": "#8cb8ff"},
            {"id": "findings", "name": "Findings", "color": "#ebc77c"},
            {"id": "complete", "name": "Complete", "color": "#7ad7bf"},
        ],
        "fields": [
            {"id": "research-notes", "label": "Research notes", "type": "text"},
        ],
        "transitions": [],
    }


def _seed_ticket_workflows(runtime: Path, config: dict) -> None:
    workflow_library = runtime / "workflow-library"
    _seed_workflow_blueprint(workflow_library, "delivery", _delivery_definition())
    _seed_workflow_blueprint(workflow_library, "research-workflow", _research_definition())

    delivery_root = runtime / "tickets" / "delivery"
    research_root = runtime / "tickets" / "research"
    delivery_root.mkdir(parents=True, exist_ok=True)
    research_root.mkdir(parents=True, exist_ok=True)
    delivery_provider = LocalTicketStorage(delivery_root, clock=lambda: datetime.fromisoformat(FIXED_NOW))
    research_provider = LocalTicketStorage(research_root, clock=lambda: datetime.fromisoformat(FIXED_NOW))
    delivery_binding = StorageBinding(
        integration="local",
        config={"root": str(delivery_root)},
        team_id="newsletter",
        workflow_id="delivery",
    )
    research_binding = StorageBinding(
        integration="local",
        config={"root": str(research_root)},
        team_id="newsletter",
        workflow_id="research-workflow",
    )
    (delivery_root / "newsletter" / "delivery" / "tickets").mkdir(parents=True, exist_ok=True)
    (research_root / "newsletter" / "research-workflow" / "tickets").mkdir(parents=True, exist_ok=True)
    _write(delivery_root / "newsletter" / "delivery" / "tickets" / ".sequence", "100")
    _write(research_root / "newsletter" / "research-workflow" / "tickets" / ".sequence", "200")

    active_run = ActiveTicketRun(
        job_id="fixture-active-job",
        session_id="fixture-active-session",
        started_at=datetime.fromisoformat(FIXED_NOW),
    )

    delivery_rows = (
        _ticket_record(delivery_binding, ticket_id="fixture-backlog-1", number=101, title="Define reusable workflow blueprints", description="Represent states, transition contracts, and qualitative criteria in reusable workflow sources.", state_id="backlog"),
        _ticket_record(delivery_binding, ticket_id="fixture-backlog-2", number=102, title="Document ticket storage provider capabilities", description="Describe the persistence guarantees every ticket storage integration must provide.", state_id="backlog", assignee="researcher"),
        _ticket_record(delivery_binding, ticket_id="fixture-active-1", number=103, title="Implement atomic ticket assignment", description="Coordinate assignment and active work so two agents cannot both begin work on one ticket.", state_id="in-progress", assignee="builder", active_run=active_run),
        _ticket_record(delivery_binding, ticket_id="fixture-review", number=104, title="Validate stale transition handling", description="Reject transitions when the ticket revision or workflow definition has changed. Return enough context for the agent to refresh and reevaluate.", state_id="review", assignee="reviewer", field_values={"acceptance-criteria": "A stale ticket revision or workflow digest cannot change state. Repeating an accepted operation returns its original result without a second transition.", "review-verdict": "Passed", "test-report": ArtifactRef(kind="id", value="transition-tests")}),
        _ticket_record(delivery_binding, ticket_id="fixture-review-2", number=105, title="Review the local storage contract", description="Check atomicity, history preservation, and storage error handling against the provider contract.", state_id="review"),
        _ticket_record(delivery_binding, ticket_id="fixture-done-1", number=106, title="Preserve canonical configuration authority", description="Keep workflow instance registration and integration settings in canonical configuration.", state_id="done", assignee="builder"),
        _ticket_record(delivery_binding, ticket_id="fixture-done-2", number=107, title="Separate ticket state from job lifecycle", description="Job completion, failure, and cancellation must not silently move tickets between workflow states.", state_id="done", assignee="reviewer"),
        _ticket_record(delivery_binding, ticket_id="fixture-active-2", number=108, title="Verify recovery after interrupted agent runs", description="Retain assignment after a failed run. Clear active work only when the owning run is confirmed stopped.", state_id="in-progress", assignee="reviewer", active_run=active_run),
    )
    research_rows = (
        _ticket_record(research_binding, ticket_id="fixture-question", number=201, title="Compare future GitHub storage mappings", description="Investigate how tickets, custom states, and evidence can map to GitHub without weakening workflow guarantees.", state_id="question"),
        _ticket_record(research_binding, ticket_id="fixture-investigating", number=202, title="Examine Azure DevOps revision checks", description="Evaluate concurrent-update handling in Azure DevOps work items.", state_id="investigating", assignee="researcher"),
        _ticket_record(research_binding, ticket_id="fixture-findings", number=203, title="Document provider-independent ticket identity", description="Record how a stable ticket ID differs from a provider-specific external reference.", state_id="findings", assignee="researcher"),
        _ticket_record(research_binding, ticket_id="fixture-complete", number=204, title="Inventory supported live agent tool channels", description="Identify which runtimes can expose the live ticket interface without granting shell access.", state_id="complete"),
    )
    for row in delivery_rows:
        delivery_provider.create(row, _ticket_operation(row.id))
    for row in research_rows:
        research_provider.create(row, _ticket_operation(row.id))


def _seed_pipeline(team: Path) -> None:
    for directory in ("logs/2026-07-16", "observations", "proposals", "decisions", "locks"):
        (team / directory).mkdir(parents=True, exist_ok=True)
    _write(
        team / "observations" / "audience-signal.md",
        "---\nagent: advisor\nstatus: open\ndate: 2026-07-16T09:00:00+00:00\nfloat: true\n---\n\n# Audience signal\n\nReaders want shorter releases.\n",
    )
    _write(
        team / "proposals" / "weekly-brief.md",
        "---\norigin_agent: advisor\nstatus: proposed\ndate: 2026-07-16T10:00:00+00:00\nquestions:\n  - Approve the weekly brief?\n---\n\n# Weekly brief\n\nPublish a concise weekly brief.\n",
    )
    _write(
        team / "decisions" / "approve-brief.md",
        "---\ndecided_by: editor\ndate: 2026-07-16T11:00:00+00:00\nanswers:\n  approve: approved\n---\n\n# Approve brief\n",
    )


def _seed_team_scaffold(team: Path) -> None:
    for directory in ("logs/2026-07-16", "observations", "proposals", "decisions", "locks"):
        (team / directory).mkdir(parents=True, exist_ok=True)


def _job_spec(runtime: Path, config_path: Path, job_id: str) -> JobSpec:
    team = runtime / "teams" / "newsletter"
    workspace = runtime / "workspaces" / "newsletter"
    return JobSpec(
        schema_version=5,
        job_id=job_id,
        config_path=str(config_path.resolve()),
        config_revision="ui-gate-revision",
        team_key="newsletter",
        team_root=str(team.resolve()),
        agent_name="advisor",
        workspace_root=str(workspace.resolve()),
        trigger="scheduled_prompt",
        integration_name="copilot",
        integration_config={"model": "gpt-5.4"},
        blueprint=BlueprintRef(
            key="advisor",
            source_digest="1" * 64,
            integration="copilot",
            projector_version="v1",
            cache_path=str((runtime / "compiled-agents" / "copilot" / "v1" / ("1" * 64)).resolve()),
        ),
        routine_id="daily-review",
        skill=None,
        skill_arguments=(),
        task_input="# Daily review\n",
        runtime_policy=RuntimePolicySnapshot(
            timeout=1200,
            mode="restricted",
        ),
        memory=MemoryBinding(
            selector={"scope": "channel", "channel": "brand-strategy"},
            canonical_json='{"channel":"brand-strategy","scope":"channel"}',
            memory_hash="2" * 64,
            path=str((runtime / "memory-store" / "channel-brand-strategy").resolve()),
        ),
        trigger_context={"source": "ui-gate"},
        prompt_source={
            "type": "blueprint_prompt",
            "scope": "blueprint",
            "name": "daily-review",
            "source_path": ".agents/prompts/daily-review.prompt.md",
            "source_digest": "1" * 64,
            "title": "Daily review",
        },
        timeout_override=None,
        created_at="2026-07-16T12:00:00+00:00",
        private_prompts=(),
    )


def _seed_jobs(runtime: Path, config_path: Path) -> None:
    authority = JobStore(runtime / "memory-store")
    authority.team_root("newsletter").mkdir(parents=True, exist_ok=True)
    waiting_path = authority.path("newsletter", "job-waiting")
    write_job(waiting_path, JobRecord.from_spec(_job_spec(runtime, config_path, "job-waiting")))
    transition_job(waiting_path, "queued", "waiting_for_memory")

    failed_path = authority.path("newsletter", "job-failed")
    failed = JobRecord.from_spec(_job_spec(runtime, config_path, "job-failed"))
    failed.status = "failed"
    failed.changed_files = [{"path": "docs/newsletter.md", "status": "modified", "lines_added": 4, "lines_removed": 1}]
    failed.execution_summary = "Memory publication failed after the draft was retained."
    artifact = authority.artifact_root("newsletter", "job-failed") / "memory.md"
    _write(artifact, "# Retained draft memory\n")
    failed.memory_publication = {
        "failed_artifacts": [{"name": "memory.md", "path": str(artifact.resolve()), "size": artifact.stat().st_size}]
    }
    failed.stdout_path = str((runtime / "teams" / "newsletter" / "logs" / "2026-07-16" / "advisor-job-failed.out").resolve())
    failed.stderr_path = str((runtime / "teams" / "newsletter" / "logs" / "2026-07-16" / "advisor-job-failed.err").resolve())
    _write(Path(failed.stdout_path), "deterministic stdout\n")
    _write(Path(failed.stderr_path), "deterministic stderr\n")
    _set_mtime(Path(failed.stdout_path), "2026-07-16T11:30:00+00:00")
    _set_mtime(Path(failed.stderr_path), "2026-07-16T11:30:00+00:00")
    write_job(failed_path, failed)

    active_path = authority.path("newsletter", "fixture-active-job")
    # The running job belongs to reviewer. Setting it on the record alone left
    # spec.agent_name as advisor, so the dashboard fleet read it as advisor's
    # newest active job and hid advisor's waiting-for-memory link.
    active_spec = dataclasses.replace(
        _job_spec(runtime, config_path, "fixture-active-job"),
        agent_name="reviewer",
    )
    active = JobRecord.from_spec(active_spec)
    active.status = "running"
    active.worker_pid = 4242
    active.started_at = FIXED_NOW
    active.launched_at = FIXED_NOW
    active.session_id = "fixture-active-session"
    write_job(active_path, active)


def _seed_activity_jobs(runtime: Path, config_path: Path) -> None:
    authority = JobStore(runtime / "memory-store")
    authority.team_root("newsletter").mkdir(parents=True, exist_ok=True)
    logs_day = runtime / "teams" / "newsletter" / "logs" / "2026-09-11"
    logs_day.mkdir(parents=True, exist_ok=True)

    triage_output = logs_day / "advisor-scheduled_prompt-d03107cc5f8c4d12be2fc74fb462958b.out"
    triage_error = logs_day / "advisor-scheduled_prompt-d03107cc5f8c4d12be2fc74fb462958b.err"
    _write_log(
        triage_output,
        "Daily control-plane triage\n\nInspected the configured workflow and claimed ticket #12.\n\nFinding: canonical path resolution can hide reparse ancestors from the subsequent validation step.\n\nEvidence\n  configuration.models._path_from_config\n  configuration.paths.validate_resolved_paths\n\nRecorded the findings, proposed a bounded repair, and published the implementation handoff to shared memory.\n",
        mtime="2026-09-11T23:13:42+00:00",
    )
    _write_log(
        triage_error,
        "Changes    +0 -0\nAI Credits 1\nTokens     56.2k input / 842 output\n\nSession completed.\n",
        mtime="2026-09-11T23:13:41+00:00",
    )

    encoded_output = logs_day / "advisor-demo & łog.out"
    empty_output = logs_day / "advisor-empty.out"
    long_output = logs_day / (
        "advisor-"
        "ultralongunbrokenlogbasenamefornarrowlayoutverification"
        "1234567890abcdefghijklmnopqrstuvwxyz"
        "-artifact.out"
    )
    omitted_error = logs_day / "advisor-zero.err"
    unrelated_output = logs_day / "reviewer-unrelated.out"
    _write_log(encoded_output, "special encoded filename\n", mtime="2026-09-11T23:08:00+00:00")
    _write_log(empty_output, "", mtime="2026-09-11T23:07:00+00:00")
    _write_log(
        long_output,
        "UNBROKENCONTENT_" + "X" * 900 + "\nwrapped tail\n",
        mtime="2026-09-11T23:06:00+00:00",
    )
    _write_log(omitted_error, "", mtime="2026-09-11T23:05:00+00:00")
    _write_log(unrelated_output, "ignore me\n", mtime="2026-09-11T23:04:00+00:00")

    running = JobRecord.from_spec(
        dataclasses.replace(
            _job_spec(runtime, config_path, "advisor-running-job"),
            created_at="2026-09-11T23:38:00+00:00",
        )
    )
    running.status = "running"
    running.started_at = "2026-09-11T23:38:00+00:00"
    running.launched_at = "2026-09-11T23:38:00+00:00"
    running.session_id = "advisor-running-session"
    running.execution_summary = "Reviewing current configuration and runtime changes."
    write_job(authority.path("newsletter", "advisor-running-job"), running)

    complete = JobRecord.from_spec(
        dataclasses.replace(
            _job_spec(runtime, config_path, "advisor-triage-job"),
            created_at="2026-09-11T23:09:38+00:00",
        )
    )
    complete.status = "complete"
    complete.started_at = "2026-09-11T23:09:30+00:00"
    complete.completed_at = "2026-09-11T23:13:42+00:00"
    complete.duration_seconds = 252
    complete.execution_summary = "Recorded the path-validation finding and handed off implementation."
    complete.stdout_path = str(triage_output.resolve())
    complete.stderr_path = str(triage_error.resolve())
    write_job(authority.path("newsletter", "advisor-triage-job"), complete)

    failed = JobRecord.from_spec(
        dataclasses.replace(
            _job_spec(runtime, config_path, "advisor-failed-job"),
            created_at="2026-09-10T18:25:59+00:00",
        )
    )
    failed.status = "failed"
    failed.started_at = "2026-09-10T18:25:59+00:00"
    failed.completed_at = "2026-09-10T18:26:00+00:00"
    failed.duration_seconds = 1
    failed.execution_summary = "Ticket server could not start. The agent was not invoked."
    failed.stdout_path = None
    failed.stderr_path = None
    write_job(authority.path("newsletter", "advisor-failed-job"), failed)


def _seed_activity_ticket_history(runtime: Path) -> None:
    delivery_root = runtime / "tickets" / "delivery"
    delivery_provider = LocalTicketStorage(delivery_root, clock=lambda: datetime.fromisoformat(FIXED_NOW))
    binding = StorageBinding(
        integration="local",
        config={"root": str(delivery_root)},
        team_id="newsletter",
        workflow_id="delivery",
    )
    ticket = _ticket_record(
        binding,
        ticket_id="fixture-advisor-history",
        number=109,
        title=(
            "Reject canonical paths crossing reparse ancestors "
            "with_long_unbroken_identifier_1234567890abcdefghijklmnopqrstuvwxyz"
        ),
        description="Agent activity fixture ticket.",
        state_id="in-progress",
        assignee="advisor",
        active_run=ActiveTicketRun(
            job_id="advisor-running-job",
            session_id="advisor-running-session",
            started_at=datetime.fromisoformat("2026-09-11T23:38:00+00:00"),
        ),
        events=(
            TicketEvent(
                id="fixture-opened",
                kind="opened",
                actor="local-user",
                summary="Ticket created",
                at=datetime.fromisoformat("2026-09-11T23:09:38+00:00"),
                data={"job_id": "advisor-triage-job"},
            ),
            TicketEvent(
                id="fixture-started",
                kind="started-work",
                actor="advisor",
                summary="Started work",
                at=datetime.fromisoformat("2026-09-11T23:10:00+00:00"),
                data={"job_id": "advisor-triage-job"},
            ),
            TicketEvent(
                id="fixture-transitioned",
                kind="transitioned",
                actor="advisor",
                summary="Start accepted",
                at=datetime.fromisoformat("2026-09-11T23:10:04+00:00"),
                data={
                    "job_id": "advisor-triage-job",
                    "source_state_name": "Backlog",
                    "destination_state_name": "In progress",
                },
            ),
            TicketEvent(
                id="fixture-long-report",
                kind="reported",
                actor="advisor",
                summary=(
                    "Triage identified and documented the violated invariant. Canonical path resolution runs before the reparse-ancestor check, so symlink and junction ancestors can become invisible to the validator. The proposed repair preserves lexical ancestry until safety validation is complete.\n\n"
                    "Evidence: configuration.models._path_from_config resolves lexical paths before configuration.paths.validate_resolved_paths. The existing-directory validator also resolves before checking for reparse points.\n\n"
                    "Record the original path before resolution, reject unsafe ancestors, and retain the current canonical overlap checks. Cover directory symlinks, Windows junctions, missing safe descendants, and ordinary relative paths with focused regressions.\n\n"
                    "No implementation was made in this run. Keep the ticket in progress for the implementation handoff."
                ),
                at=datetime.fromisoformat("2026-09-11T23:12:58+00:00"),
                data={"job_id": "advisor-triage-job"},
            ),
            TicketEvent(
                id="fixture-short-overflow-report",
                kind="reported",
                actor="advisor",
                summary=(
                    "Lexical ancestry validation keeps junction visibility across "
                    "nested workspace recovery boundaries while preserving ordinary "
                    "relative path checks."
                ),
                at=datetime.fromisoformat("2026-09-11T23:12:20+00:00"),
                data={"job_id": "advisor-triage-job"},
            ),
            TicketEvent(
                id="fixture-ended",
                kind="ended-work",
                actor="advisor",
                summary="Ended active work",
                at=datetime.fromisoformat("2026-09-11T23:13:29+00:00"),
                data={"job_id": "advisor-triage-job"},
            ),
            TicketEvent(
                id="fixture-historic-report",
                kind="reported",
                actor="advisor",
                summary="Historical note without provenance remains visible.",
                at=datetime.fromisoformat("2026-09-11T23:08:30+00:00"),
                data={},
            ),
        ),
    )
    delivery_provider.create(ticket, _ticket_operation(ticket.id))


def _apply_activity_logs_fixture(runtime: Path, config_path: Path) -> None:
    memory_root = runtime / "memory-store"
    _clear_directory(memory_root / ".jobs" / "newsletter")
    _clear_directory(runtime / "teams" / "newsletter" / "logs")

    ticket_root = runtime / "tickets" / "delivery" / "newsletter" / "delivery" / "tickets"
    if ticket_root.exists():
        for child in ticket_root.iterdir():
            if child.name == ".sequence":
                continue
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)
        _write(ticket_root / ".sequence", "108")

    _seed_activity_jobs(runtime, config_path)
    _seed_activity_ticket_history(runtime)


# -- opt-in Git evidence fixture ------------------------------------------
#
# Everything below builds real evidence: a real repository under the test
# runtime workspace, a real running job, the real capture service, and real
# accepted transitions. No AI runtime is launched and no remote is contacted.

_GIT_FIXTURE_IDENTITY = {
    "GIT_AUTHOR_NAME": "Fixture Author",
    "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
    "GIT_AUTHOR_DATE": "2026-05-04T09:00:00+00:00",
    "GIT_COMMITTER_NAME": "Fixture Author",
    "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
    "GIT_COMMITTER_DATE": "2026-05-04T09:00:00+00:00",
}

_LONG_DIRECTORY = "reference/unusually-long-reference-directory-for-narrow-layout-verification"
_APPENDIX_BEFORE = f"docs/{_LONG_DIRECTORY}/handbook-appendix-with-an-unusually-long-unbroken-filename.md"
_APPENDIX_AFTER = f"docs/{_LONG_DIRECTORY}/renamed-handbook-appendix-with-an-unusually-long-unbroken-filename.md"
_BULK_REPORT = "docs/generated/bulk-release-report.txt"
_ADDED_BULK_REPORT = "docs/generated/added-bulk-appendix.txt"

_APPENDIX_BODY = b"""# Handbook appendix

The appendix keeps the long-form checklist that the handbook summarizes.

- Record the reviewed range.
- Record the reviewing agent.
- Record the accepted result.
"""

_HANDBOOK_BEFORE = b"""# Delivery handbook

Every release follows the same three steps.

1. Prepare the draft.
2. Review the retained evidence.
3. Publish the result.

Superseded guidance is removed here and stays in history.
"""

_HANDBOOK_AFTER = b"""# Delivery handbook

Every release follows the same three steps.

1. Prepare the draft from committed work only.
2. Review the retained evidence.
3. Publish the result and record the reviewed range.

Superseded guidance is removed here and stays in history.
"""

# Source text that looks hostile on purpose. The viewer must show it as text.
_INERT_SOURCE = b'''"""Sample module retained only as diff content."""

MARKUP = "<script>window.__evidenceXssFired = true</script>"
ATTRIBUTE = '"><img src=x onerror="window.__evidenceXssFired = true">'


def render() -> str:
    return MARKUP + ATTRIBUTE
'''

_HOSTILE_VERDICT = '<b>bold</b> & "quoted" <script>window.__verdictXssFired = true</script>'


def _git_environment(root: Path) -> dict[str, str]:
    """Deterministic Git environment: no inherited GIT_*, no user config."""
    env = {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}
    absent = str(Path(root).parent / "absent-gitconfig")
    env.update(_GIT_FIXTURE_IDENTITY)
    env.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": absent,
            "GIT_CONFIG_SYSTEM": absent,
            "GIT_ATTR_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_OPTIONAL_LOCKS": "0",
        }
    )
    return env


def _git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_git_environment(root),
    )
    return result.stdout.decode("utf-8", errors="replace").strip()


def _git_write(root: Path, relative: str, content: bytes) -> None:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)


def _build_git_evidence_repository(workspace: Path) -> dict[str, str]:
    """Build (once) the fixture repository and name each range boundary.

    The boundaries are kept as ordinary local branches so a reset can read them
    back without remembering process state, and so the capture's local-ref
    policy has a real ref to verify against.
    """
    workspace.mkdir(parents=True, exist_ok=True)
    if not (workspace / ".git").exists():
        _git(workspace, "init", "--template=", "--initial-branch=main")
        _git(workspace, "config", "user.name", "Fixture Author")
        _git(workspace, "config", "user.email", "fixture@example.invalid")
        _git(workspace, "config", "core.autocrlf", "false")
        _git(workspace, "config", "core.safecrlf", "false")
        _git(workspace, "config", "commit.gpgsign", "false")
        _git(workspace, "config", "core.hooksPath", str(workspace.parent / "absent-hooks"))

        _git_write(workspace, "docs/handbook.md", _HANDBOOK_BEFORE)
        _git_write(workspace, _APPENDIX_BEFORE, _APPENDIX_BODY)
        _git_write(
            workspace,
            "tools/retired_notes.txt",
            b"Retired note one\nRetired note two\nRetired note three\n",
        )
        # Present from the start so the oversized range carries a modification
        # as well as the added file below; the two omission notices differ.
        _git_write(workspace, _BULK_REPORT, b"generated release row 0000\n")
        _git(workspace, "add", "--all")
        _git(workspace, "commit", "-m", "test: seed delivery handbook")
        _git(workspace, "branch", "fixture-base")

        _git_write(workspace, "docs/handbook.md", _HANDBOOK_AFTER)
        (workspace / _APPENDIX_BEFORE).unlink()
        _git_write(workspace, _APPENDIX_AFTER, _APPENDIX_BODY)
        (workspace / "tools" / "retired_notes.txt").unlink()
        _git_write(workspace, "assets/release-marker.bin", bytes(range(16)) * 8)
        _git_write(workspace, "src/inert_sample.py", _INERT_SOURCE)
        _git(workspace, "add", "--all")
        _git(workspace, "commit", "-m", "test: publish reviewed delivery changes")
        _git(workspace, "branch", "fixture-rich")

        _git_write(
            workspace,
            _BULK_REPORT,
            b"".join(b"generated release row %04d\n" % index for index in range(2600)),
        )
        _git_write(
            workspace,
            _ADDED_BULK_REPORT,
            b"".join(b"appendix row %04d\n" % index for index in range(2400)),
        )
        _git(workspace, "add", "--all")
        _git(workspace, "commit", "-m", "test: record the bulk release report")
        _git(workspace, "branch", "fixture-bulk")

        _git_write(workspace, "scratch/temporary-note.txt", b"temporary\n")
        _git(workspace, "add", "--all")
        _git(workspace, "commit", "-m", "test: stage a temporary note")
        (workspace / "scratch" / "temporary-note.txt").unlink()
        _git(workspace, "add", "--all")
        _git(workspace, "commit", "-m", "test: remove the temporary note")
    return {
        "base": _git(workspace, "rev-parse", "fixture-base"),
        "rich": _git(workspace, "rev-parse", "fixture-rich"),
        "bulk": _git(workspace, "rev-parse", "fixture-bulk"),
        "tip": _git(workspace, "rev-parse", "main"),
    }


def _git_evidence_delivery_definition() -> dict:
    """The delivery workflow with a Git-format output and the transitions
    that accept one, added without changing the default fixture's contract."""
    definition = _delivery_definition()
    definition["fields"].append(
        {
            "id": "committed-changes",
            "label": "Committed changes",
            "type": "artifact",
            "artifact_format": "git-change",
        }
    )
    definition["transitions"].extend(
        [
            {
                "id": "submit-draft",
                "name": "Submit draft",
                "from_state": "backlog",
                "to_state": "in-progress",
                "inputs": [],
                "outputs": [{"field_id": "committed-changes", "required": True}],
                "preconditions": [],
                "criteria": [],
            },
            {
                "id": "submit-bulk",
                "name": "Submit bulk update",
                "from_state": "in-progress",
                "to_state": "review",
                "inputs": [],
                "outputs": [{"field_id": "committed-changes", "required": True}],
                "preconditions": [],
                "criteria": [],
            },
            {
                "id": "request-changes",
                "name": "Request changes",
                "from_state": "review",
                "to_state": "in-progress",
                "inputs": [],
                "outputs": [
                    {"field_id": "review-verdict", "required": True},
                    {"field_id": "committed-changes", "required": True},
                ],
                "preconditions": [],
                "criteria": [],
            },
            {
                "id": "deliver",
                "name": "Deliver",
                "from_state": "in-progress",
                "to_state": "done",
                "inputs": [],
                "outputs": [{"field_id": "committed-changes", "required": True}],
                "preconditions": [],
                "criteria": [],
            },
        ]
    )
    return definition


def _git_evidence_job(
    runtime: Path, config_path: Path, job_id: str, agent_name: str, target
):
    """Create the running, fixture-owned ticket job a real run would own."""
    from flowgency.configuration.effective import resolve_effective_policy

    store = JobStore(runtime / "memory-store")
    store.team_root("newsletter").mkdir(parents=True, exist_ok=True)
    snapshot = ConfigStore(config_path).load()
    integration = snapshot.config.teams["newsletter"].agents[agent_name].integration
    cache_path = runtime / "compiled-agents" / "ticket-test" / "ui" / agent_name
    cache_path.mkdir(parents=True, exist_ok=True)
    spec = dataclasses.replace(
        _job_spec(runtime, config_path, job_id),
        schema_version=6,
        agent_name=agent_name,
        routine_id=None,
        trigger="ticket",
        integration_name=integration,
        integration_config={},
        blueprint=BlueprintRef(
            key=agent_name,
            source_digest="0" * 64,
            integration=integration,
            projector_version="ui-ticket",
            cache_path=str(cache_path.resolve()),
            instance_digest="1" * 64,
        ),
        ticket_target=target,
        prompt_source={
            "type": "ticket",
            "scope": "ticket",
            "name": "ticket-run",
            "source_path": "ticket.prompt.md",
            "source_digest": "0" * 64,
        },
        runtime_policy=RuntimePolicySnapshot.from_effective_policy(
            resolve_effective_policy(snapshot.config, "newsletter", agent_name)
        ),
    )
    record = JobRecord.from_spec(spec)
    authority = store.create(record)
    record.status = "running"
    record.worker_pid = os.getpid()
    record.started_at = FIXED_NOW
    record.launched_at = FIXED_NOW
    record.session_id = f"{job_id}-session"
    write_job(authority.path, record)
    return authority, record


def _settle_git_evidence_job(authority, record, *, changed_files=None) -> None:
    """Close the fixture job the way a finished run leaves it."""
    record.status = "complete"
    record.completed_at = FIXED_NOW
    record.duration_seconds = 12
    if changed_files is not None:
        record.changed_files = changed_files
    write_job(authority.path, record)


def _evidence_entry(provider, ref, captured, job_id: str) -> dict:
    """Describe one retained artifact from its own stored bytes.

    The expected download digest is taken from the retained manifest on disk
    and never from the download route, so the browser assertion compares the
    route against known bytes rather than against itself.
    """
    artifact = provider.read_artifact(ref, captured.artifact.value)
    manifest = json.loads(artifact.content)
    patch = base64.b64decode(manifest["patch_b64"])
    return {
        "artifact_id": captured.artifact.value,
        "patch_sha256": hashlib.sha256(patch).hexdigest(),
        "patch_bytes": len(patch),
        "base_commit": manifest["base_commit"],
        "end_commit": manifest["end_commit"],
        "job_id": job_id,
    }


def _seed_git_evidence_fixture(runtime: Path, config: dict) -> None:
    from flowgency.jobs.models import TicketJobTarget
    from flowgency.tickets.access import TicketAccessRegistry
    from flowgency.tickets.git_evidence import GitCaptureRequest
    from flowgency.tickets.models import TransitionRequest, UserTicketContext
    from flowgency.tickets.service import TicketService
    from flowgency.tickets.storages.registry import resolve_storage
    from flowgency.workflows.configuration import resolve_workflow_binding
    from flowgency.workflows.library import WorkflowLibrary

    config_path = runtime / "config.yaml"
    commits = _build_git_evidence_repository(runtime / "workspaces" / "newsletter")
    _seed_workflow_blueprint(
        runtime / "workflow-library", "delivery", _git_evidence_delivery_definition()
    )

    delivery_root = runtime / "tickets" / "delivery"
    provider = LocalTicketStorage(
        delivery_root, clock=lambda: datetime.fromisoformat(FIXED_NOW)
    )
    binding = StorageBinding(
        integration="local",
        config={"root": str(delivery_root)},
        team_id="newsletter",
        workflow_id="delivery",
    )
    ticket = _ticket_record(
        binding,
        ticket_id=GIT_EVIDENCE_TICKET_ID,
        number=109,
        title="Retain committed evidence for the delivery handbook",
        description="Capture the reviewed committed range and keep the retained patch readable after the work is closed.",
        state_id="backlog",
    )
    provider.create(ticket, _ticket_operation(ticket.id))
    ref = ticket.ref

    config_store = ConfigStore(config_path)
    job_store = JobStore(runtime / "memory-store")
    registry = TicketAccessRegistry(job_store)
    # The capture manifest requires a timezone-aware instant, exactly as the
    # job-side runtime supplies one; the fixture pins it instead of using now().
    def fixed_clock() -> datetime:
        return datetime.fromisoformat(FIXED_NOW)

    # The capture must name a ref this fixture's own written policy allows.
    publication_ref = config["teams"]["newsletter"]["git_publication"]["allowed_refs"][0]
    service = TicketService(
        config_store,
        WorkflowLibrary(runtime / "workflow-library"),
        lambda storage: resolve_storage(storage, clock=fixed_clock),
        registry.validate_context,
        clock=fixed_clock,
        resolve_git_job=registry.resolve_context,
    )
    user = UserTicketContext(team_id="newsletter")

    def workflow_binding():
        return resolve_workflow_binding(config_store.load(), "newsletter", "delivery")

    def version():
        return service.inspect(user, ref).version

    def capture(actor, label: str, transition_id: str, base: str, end: str):
        return service.capture_git_evidence(
            actor,
            service.inspect(actor, ref).version,
            GitCaptureRequest(
                transition_id=transition_id,
                field_id="committed-changes",
                base_commit=base,
                end_commit=end,
                publication_ref=publication_ref,
            ),
            _ticket_operation(label),
        )

    def accept(actor, label: str, transition_id: str, outputs: dict):
        service.transition(
            actor,
            service.inspect(actor, ref).version,
            TransitionRequest(transition_id=transition_id, outputs=outputs),
            _ticket_operation(label),
        )

    def open_run(job_id: str, agent_name: str):
        service.assign(user, version(), agent_name, _ticket_operation(f"assign-{job_id}"))
        record = provider.read(ref)
        assignment_event_id = next(
            event.id for event in reversed(record.events) if event.kind == "assigned"
        )
        target = TicketJobTarget(
            binding=workflow_binding().storage,
            ref=ref,
            assigned_agent=agent_name,
            assignment_event_id=assignment_event_id,
            context_digest=workflow_binding().context_digest,
        )
        authority, job_record = _git_evidence_job(
            runtime, config_path, job_id, agent_name, target
        )
        actor = registry.open(authority).context
        service.start_work(actor, version(), _ticket_operation(f"start-{job_id}"))
        return actor, authority, job_record

    def close_run(actor, authority, record, *, changed_files=None):
        service.end_work(
            actor, service.inspect(actor, ref).version, _ticket_operation(f"end-{actor.job_id}")
        )
        _settle_git_evidence_job(authority, record, changed_files=changed_files)
        service.assign(user, version(), None, _ticket_operation(f"release-{actor.job_id}"))

    builder, build_authority, build_record = open_run("fixture-evidence-build", "builder")
    empty = capture(builder, "capture-empty", "submit-draft", commits["bulk"], commits["tip"])
    accept(builder, "accept-empty", "submit-draft", {"committed-changes": empty.artifact})
    bulk = capture(builder, "capture-bulk", "submit-bulk", commits["rich"], commits["bulk"])
    accept(builder, "accept-bulk", "submit-bulk", {"committed-changes": bulk.artifact})
    close_run(builder, build_authority, build_record)

    reviewer, review_authority, review_record = open_run("fixture-evidence-review", "reviewer")
    # The reviewer only reuses the builder's artifact; it produces none itself.
    accept(
        reviewer,
        "accept-review",
        "request-changes",
        {"review-verdict": _HOSTILE_VERDICT, "committed-changes": bulk.artifact},
    )
    close_run(
        reviewer,
        review_authority,
        review_record,
        changed_files=[
            {"path": "docs/handbook.md", "status": "modified", "lines_added": 2, "lines_removed": 2}
        ],
    )

    finisher, final_authority, final_record = open_run("fixture-evidence-final", "builder")
    rich = capture(finisher, "capture-rich", "deliver", commits["base"], commits["rich"])
    accept(finisher, "accept-rich", "deliver", {"committed-changes": rich.artifact})
    close_run(finisher, final_authority, final_record)

    _write(
        runtime / GIT_EVIDENCE_INDEX / "git-evidence.json",
        json.dumps(
            {
                "ticket_id": GIT_EVIDENCE_TICKET_ID,
                "ticket_href": f"/newsletter/workflows/delivery/tickets/{GIT_EVIDENCE_TICKET_ID}",
                "current": _evidence_entry(provider, ref, rich, "fixture-evidence-final"),
                "bulk": _evidence_entry(provider, ref, bulk, "fixture-evidence-build"),
                "empty": _evidence_entry(provider, ref, empty, "fixture-evidence-build"),
                "reviewer_job_id": "fixture-evidence-review",
                "renamed_path": _APPENDIX_AFTER,
                "original_path": _APPENDIX_BEFORE,
                "binary_path": "assets/release-marker.bin",
                "bulk_path": _BULK_REPORT,
                "added_bulk_path": _ADDED_BULK_REPORT,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )



def _seed_private_prompts(runtime: Path) -> None:
    PromptStore(runtime / "prompts").create(
        "newsletter",
        "advisor",
        "local-triage",
        _prompt_bytes(
            "local-triage",
            "Private local triage.",
            "Audit the current release blockers and call out anything that needs a human decision.\n",
            argument_hint="Escalate blockers if the draft is stale.",
        ),
    )


def _seed_memory(runtime: Path, config: dict) -> None:
    store = MemoryStore(runtime / "memory-store")
    store.root.mkdir(parents=True, exist_ok=True)
    channel = resolve_memory_selector(
        MemorySelector(scope="channel", channel="brand-strategy"),
        job_id="ui-preview",
        team_key="newsletter",
        agent_name="advisor",
        routine_id=None,
        channels=config["memory"]["channels"],
        store_root=store.root,
    )
    store.ensure(channel)
    _write(channel.directory / "memory.md", "# Brand Strategy\n\nPrefer concise, evidence-led releases.\n")


def _clear_readonly(function, path, _error) -> None:
    # Git marks its object files read-only, so Windows refuses the plain
    # removal every other fixture directory accepts. The third argument is an
    # exception on 3.12+ and an ``exc_info`` triple on 3.11; neither is used.
    os.chmod(path, stat.S_IWRITE)
    function(path)


def _rmtree_including_read_only(runtime: Path, rmtree=None) -> None:
    """Remove a tree whose files may be read-only, on every supported Python.

    ``shutil.rmtree`` only grew ``onexc`` in 3.12 while this package supports
    3.11, so the error-handler keyword is chosen from the interpreter's own
    signature instead of being assumed.
    """
    remove = shutil.rmtree if rmtree is None else rmtree
    keyword = "onexc" if "onexc" in inspect.signature(remove).parameters else "onerror"
    try:
        remove(runtime, **{keyword: _clear_readonly})
    except OSError:
        remove(runtime, ignore_errors=True)
    if Path(runtime).exists():
        raise RuntimeError(f"UI runtime could not be removed: {runtime}")


def _safe_remove_runtime(runtime: Path) -> None:
    parent = RUNTIME_PARENT.resolve(strict=False)
    candidate = runtime.resolve(strict=False)
    if candidate.parent != parent or candidate.name != "current":
        raise RuntimeError(f"Refusing to remove unsafe UI runtime path: {candidate}")
    if not candidate.exists():
        return
    _rmtree_including_read_only(candidate)


def _reset_runtime_state(runtime: Path, *, fixture: str = "default") -> None:
    raw = yaml.safe_load(FIXTURE_CONFIG.read_text(encoding="utf-8"))
    config = _replace_runtime(raw, runtime)
    if fixture == GIT_EVIDENCE_FIXTURE:
        config["teams"]["newsletter"]["git_publication"] = {
            "mode": "local",
            "allowed_refs": [GIT_EVIDENCE_REF],
        }
    _write_runtime_config(runtime / "config.yaml", config)

    _clear_directory(runtime / "workflow-library")
    _clear_workflow_roots(runtime, config)
    _clear_directory(runtime / GIT_EVIDENCE_INDEX)

    memory_root = runtime / "memory-store"
    _clear_directory(memory_root / "ui-ticket-memory")
    _clear_directory(memory_root / ".jobs" / "newsletter")
    _clear_directory(memory_root / ".jobs" / "research")

    for path in (
        runtime / "teams" / "newsletter" / "logs",
        runtime / "teams" / "research" / "logs",
    ):
        _clear_directory(path)

    _seed_memory(runtime, config)
    _seed_ticket_workflows(runtime, config)
    _seed_jobs(runtime, runtime / "config.yaml")
    if fixture == ACTIVITY_LOGS_FIXTURE:
        _apply_activity_logs_fixture(runtime, runtime / "config.yaml")
    elif fixture == GIT_EVIDENCE_FIXTURE:
        _seed_git_evidence_fixture(runtime, config)
    elif fixture != "default":
        raise ValueError(f"Unknown UI fixture: {fixture}")


def _prepare_runtime() -> tuple[Path, Path]:
    runtime = RUNTIME_ROOT
    RUNTIME_PARENT.mkdir(parents=True, exist_ok=True)
    _safe_remove_runtime(runtime)
    runtime.mkdir()
    raw = yaml.safe_load(FIXTURE_CONFIG.read_text(encoding="utf-8"))
    config = _replace_runtime(raw, runtime)
    config_path = runtime / "config.yaml"
    _write_runtime_config(config_path, config)
    team = runtime / "teams" / "newsletter"
    (runtime / "teams" / "newsletter" / "editorial").mkdir(parents=True, exist_ok=True)
    (runtime / "workspaces" / "newsletter").mkdir(parents=True, exist_ok=True)
    (runtime / "workspaces" / "research").mkdir(parents=True, exist_ok=True)
    _seed_pipeline(team)
    _seed_team_scaffold(runtime / "teams" / "research")
    _seed_blueprint(
        runtime / "agent-library",
        "advisor",
        "Advisor",
        "daily-review",
        prompts=(
            (
                "daily-review",
                "Shared daily review.",
                "Review the current release plan and summarize the next decision.\n",
                "Mention the release window if relevant.",
            ),
            (
                "release-window",
                "Shared release window check.",
                "Verify the release window, rollout risk, and communication timing.\n",
                "Include the launch date and any blocked approvals.",
            ),
        ),
    )
    _seed_blueprint(
        runtime / "agent-library",
        "builder",
        "Builder",
        "publish-draft",
        prompts=(
            (
                "publish-draft",
                "Shared publish draft.",
                "Prepare the current draft for publication and flag any unresolved edits.\n",
                "Note whether publishing is blocked by review.",
            ),
        ),
    )
    _seed_blueprint(
        runtime / "agent-library",
        "reviewer",
        "Reviewer",
        "review-ticket",
        prompts=(
            (
                "review-ticket",
                "Shared review ticket prompt.",
                "Review the current ticket evidence and report whether the work is ready.\n",
                "Reference the most important blocking issue if one exists.",
            ),
        ),
    )
    _seed_blueprint(
        runtime / "agent-library",
        "researcher",
        "Researcher",
        "research-ticket",
        prompts=(
            (
                "research-ticket",
                "Shared research ticket prompt.",
                "Investigate the current ticket question and summarize the evidence.\n",
                "Include the most relevant sources or findings.",
            ),
        ),
    )
    (runtime / "compiled-agents").mkdir()
    _seed_private_prompts(runtime)
    _seed_memory(runtime, config)
    _seed_ticket_workflows(runtime, config)
    _seed_jobs(runtime, config_path)
    (runtime / "server.pid").write_text(str(os.getpid()), encoding="ascii")
    return runtime, config_path


def _port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.25)
        return probe.connect_ex(("127.0.0.1", port)) != 0


def _wait_ready(port: int, process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 30
    url = f"http://127.0.0.1:{port}/newsletter/"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Flowgency server exited with status {process.returncode}")
        try:
            with urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.1)
    raise TimeoutError(f"Flowgency server was not ready at {url}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if not _port_is_free(args.port):
        raise RuntimeError(f"Test port {args.port} is already in use; refusing to reuse an unknown server")

    runtime = RUNTIME_ROOT
    process: subprocess.Popen[bytes] | None = None

    def stop(_signum: int | None = None, _frame: object | None = None) -> None:
        if process is not None and process.poll() is None:
            process.terminate()

    try:
        runtime, config_path = _prepare_runtime()
        support_path = _ui_sitecustomize(runtime)
        env = os.environ.copy()
        env["FLOWGENCY_CONFIG"] = str(config_path)
        env["FLOWGENCY_UI_RUNTIME"] = str(runtime)
        env["FLOWGENCY_FIXED_NOW"] = FIXED_NOW
        env["PYTHONPATH"] = os.pathsep.join((str(support_path), str(ROOT)))
        command = [
            sys.executable,
            "-m",
            "flowgency.cli",
            "serve",
            "--host",
            "127.0.0.1",
            "--port",
            str(args.port),
            "--log-level",
            "warning",
        ]
        process = subprocess.Popen(command, cwd=ROOT, env=env)
        signal.signal(signal.SIGINT, stop)
        signal.signal(signal.SIGTERM, stop)
        _wait_ready(args.port, process)
        return process.wait()
    finally:
        stop()
        if process is not None:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        _safe_remove_runtime(runtime)


if __name__ == "__main__":
    raise SystemExit(main())