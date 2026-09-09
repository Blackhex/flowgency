from __future__ import annotations

import argparse
from datetime import datetime, timezone
import dataclasses
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

import yaml

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
    from fastapi import Response
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
    async def reset_ui_runtime() -> Response:
        runtime_root = Path(os.environ["FLOWGENCY_UI_RUNTIME"])
        _reset_runtime_state(runtime_root)
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


def _set_mtime(path: Path, value: str) -> None:
    timestamp = datetime.fromisoformat(value).astimezone(timezone.utc).timestamp()
    os.utime(path, (timestamp, timestamp))


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


def _safe_remove_runtime(runtime: Path) -> None:
    parent = RUNTIME_PARENT.resolve(strict=False)
    candidate = runtime.resolve(strict=False)
    if candidate.parent != parent or candidate.name != "current":
        raise RuntimeError(f"Refusing to remove unsafe UI runtime path: {candidate}")
    shutil.rmtree(candidate, ignore_errors=True)


def _reset_runtime_state(runtime: Path) -> None:
    raw = yaml.safe_load(FIXTURE_CONFIG.read_text(encoding="utf-8"))
    config = _replace_runtime(raw, runtime)
    _write_runtime_config(runtime / "config.yaml", config)

    _clear_directory(runtime / "workflow-library")
    _clear_directory(runtime / "tickets")

    memory_root = runtime / "memory-store"
    _clear_directory(memory_root / "ui-ticket-memory")
    _clear_directory(memory_root / ".jobs" / "newsletter")
    _clear_directory(memory_root / ".jobs" / "research")

    for path in (
        runtime / "teams" / "newsletter" / "logs" / "2026-07-16",
        runtime / "teams" / "research" / "logs" / "2026-07-16",
    ):
        _clear_directory(path)

    _seed_memory(runtime, config)
    _seed_ticket_workflows(runtime, config)
    _seed_jobs(runtime, runtime / "config.yaml")


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