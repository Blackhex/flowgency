from __future__ import annotations

import argparse
import base64
from datetime import datetime, timedelta, timezone
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
from fastapi.responses import JSONResponse

from flowgency.configuration.models import MemorySelector
from flowgency.configuration.store import ConfigStore
from flowgency.fs.atomic import atomic_write_bytes
from flowgency.fs.locks import exclusive_lock
from flowgency.integrations import REGISTRY, BaseIntegration
from flowgency.integrations.models import InteractiveSetupRequest, RuntimeLaunch
from flowgency.jobs.connected_process import connected_process_available as _REAL_CONNECTED_PROCESS_AVAILABLE
from flowgency.jobs.models import JobHandle
from flowgency.jobs.authority import JobStore
from flowgency.jobs.models import BlueprintRef, JobRecord, JobSpec, MemoryBinding, RuntimePolicySnapshot
from flowgency.jobs.store import cancel_job, read_job, transition_job, write_job
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
CONNECTED_SETUP_FIXTURE = "connected-setup"
WORKSPACES_FIXTURE = "workspaces"
SUPPORTED_UI_FIXTURES = frozenset(
    {"default", ACTIVITY_LOGS_FIXTURE, GIT_EVIDENCE_FIXTURE, CONNECTED_SETUP_FIXTURE, WORKSPACES_FIXTURE}
)
# A base-derived page that never opts in to live refresh; every product page is live or retired.
NON_LIVE_PAGE_PATH = "/__ui/non-live-page"
WORKSPACE_SOURCES_DIR = "workspace-sources"
WORKSPACE_NOTES_NAME = "notes.md"
WORKSPACE_SCRIPT_NAME = "session.sh"
WORKSPACE_REVIEW_SCRIPT_NAME = "review.sh"
WORKSPACE_NOTES_TEXT = "# Editorial notes\n\nSaved by the editor.\n"
WORKSPACE_CHANGED_NOTES_TEXT = "# Editorial notes\n\nChanged elsewhere by another process.\n"
WORKSPACE_SCRIPT_TEXT = "#!/bin/bash\ntmux new-session -d -s newsletter\n"
# Small enough that a browser test can exceed it deliberately (via the emit
# endpoint below) without pushing megabytes through a fake PTY queue.
CONNECTED_SETUP_REPLAY_LIMIT = 4096
COMPLETION_FIXTURE_SCHEDULER_RESULTS = frozenset(
    {"manual-only", "inactive", "declined", "confirmed", "failed", "unknown"}
)
COMPLETION_FIXTURE_REVISIONS = frozenset({"current", "stale"})
COMPLETION_FIXTURE_KEYS = frozenset({"scheduler_result", "limitations_acknowledged", "revision"})
LIVE_CHANGE_PATH = "/__ui/live/change"
LIVE_CHANGE_CASES = frozenset(
    {
        "navigation-membership",
        "navigation-workflow-count",
        "inbox-routine-pending",
        "inbox-job-completes",
        "inbox-agent-added",
        "inbox-agent-moved",
        "inbox-agent-removed",
        "inbox-ticket-activity",
        "inbox-queue-grows",
        "inbox-clock-advances",
        "agent-source-and-status",
        "agent-identity",
        "agent-active-job",
        "agent-routines",
        "agent-catalog-digest",
        "agent-log-membership",
        "agent-memory-revision",
        "agent-report-history",
        "agent-team-runtime",
        "agent-permissions",
        "job-finishes",
        "job-finishes-with-session",
        "job-added",
        "job-removed",
        "job-failure-artifacts",
        "job-memory-published",
        "log-tall",
        "log-appended",
        "log-truncated",
        "log-oversized",
        "log-removed",
        "log-listing-membership",
        "workspace-file-changes",
        "workspace-file-removed",
        "workspace-reordered",
        "workspace-removed",
        "workspace-added",
        "admin-settings-changed",
        "admin-dispatch-changed",
        "admin-team-created",
        "admin-team-changed",
        "admin-team-removed",
        "integration-registered",
        "integration-available-added",
        "library-source-changes",
        "library-blueprint-added",
        "library-blueprint-removed",
        "library-selected-files-removed",
        "channel-memory-changes",
        "channel-metadata-changed",
        "channel-added",
        "channel-removed",
    }
)
INBOX_CLOCK_ADVANCE = timedelta(minutes=30)
STALE_REVISION = "0" * 64
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


class FakeConnectedCopilotIntegration(BaseIntegration):
    """Stands in for the real Copilot integration only while the
    ``connected-setup`` fixture is active, so a browser test never resolves
    or launches a genuine Copilot CLI. It always reports itself as detected
    with the lowest possible ``detect_priority`` so it sorts first among
    launchable integrations regardless of what is actually installed on the
    machine running the tests."""

    name = "copilot"
    display_name = "GitHub Copilot"
    detect_priority = 0

    def detect(self, agent_dir: Path) -> bool:
        del agent_dir
        return True

    def interactive_setup_available(self) -> bool:
        return False

    def connected_setup_available(self) -> bool:
        return True

    def interactive_setup_fallback_command(self, request: InteractiveSetupRequest) -> str:
        return f"copilot -C {request.data_root} -i <prompt> --name \"Flowgency setup\""

    def connected_setup_launch(self, request: InteractiveSetupRequest) -> RuntimeLaunch:
        return RuntimeLaunch(
            argv=("connected-setup-fixture",),
            cwd=request.data_root,
            env={},
            mode="connected",
        )


_REAL_COPILOT_INTEGRATION: BaseIntegration | None = None
_CURRENT_FAKE_PROCESS = None


def _connected_setup_process_factory(launch):
    del launch
    global _CURRENT_FAKE_PROCESS
    from tests._connected_setup_helpers import FakeProcess

    process = FakeProcess()
    _CURRENT_FAKE_PROCESS = process
    return process


async def _reset_connected_setup_runtime(fixture: str) -> None:
    """Restore real integrations/availability and a fresh, non-fake setup
    session manager, then — only for the ``connected-setup`` fixture —
    replace them with test doubles that never touch a real Copilot CLI or
    Windows PTY. Running the restore unconditionally on every reset keeps
    fixtures order-independent. On Windows the real capability check is kept
    (never overridden to ``True``): Task 4 makes it genuinely available when
    its ConPTY/Job/WS dependencies are installed, and lying about it here
    would hide a real environment regression instead of exercising it."""
    global _CURRENT_FAKE_PROCESS
    import flowgency.web.routes.admin_teams as admin_teams_module
    import flowgency.web.setup_flow as setup_flow_module
    from flowgency.app import app
    from flowgency.web.setup_sessions import SetupSessionManager

    setup_flow_module.connected_process_available = _REAL_CONNECTED_PROCESS_AVAILABLE
    admin_teams_module.connected_process_available = _REAL_CONNECTED_PROCESS_AVAILABLE
    if _REAL_COPILOT_INTEGRATION is not None:
        REGISTRY["copilot"] = _REAL_COPILOT_INTEGRATION

    manager = getattr(app.state, "setup_sessions", None)
    if manager is not None:
        await manager.shutdown()
    _CURRENT_FAKE_PROCESS = None
    app.state.setup_sessions = SetupSessionManager()

    if fixture != CONNECTED_SETUP_FIXTURE:
        return

    if os.name == "nt":
        assert _REAL_CONNECTED_PROCESS_AVAILABLE(), (
            "Native Windows connected-setup capability is unavailable; install "
            "the Windows native connected-setup dependencies before running this fixture."
        )
    else:
        setup_flow_module.connected_process_available = lambda: True
        admin_teams_module.connected_process_available = lambda: True
    REGISTRY["copilot"] = FakeConnectedCopilotIntegration()
    await app.state.setup_sessions.shutdown()
    app.state.setup_sessions = SetupSessionManager(
        process_factory=_connected_setup_process_factory,
        replay_limit=CONNECTED_SETUP_REPLAY_LIMIT,
    )


def _completion_fixture_body(payload: object) -> tuple[str, bool, str]:
    """Validate the closed completion-fixture schema; raise ``ValueError`` on anything else."""
    if not isinstance(payload, dict):
        raise ValueError("Completion payload must be an object")
    unexpected = set(payload) - COMPLETION_FIXTURE_KEYS
    if unexpected:
        raise ValueError(f"Unsupported completion field: {sorted(unexpected)[0]}")
    scheduler_result = payload.get("scheduler_result")
    if scheduler_result not in COMPLETION_FIXTURE_SCHEDULER_RESULTS:
        raise ValueError("scheduler_result is required and must be a known scheduler outcome")
    acknowledged = payload.get("limitations_acknowledged", False)
    if not isinstance(acknowledged, bool):
        raise ValueError("limitations_acknowledged must be a boolean")
    revision = payload.get("revision", "current")
    if revision not in COMPLETION_FIXTURE_REVISIONS:
        raise ValueError("revision must be 'current' or 'stale'")
    return scheduler_result, acknowledged, revision


async def _complete_connected_setup(request: Request) -> Response:
    """Report completion for the prepared attempt through the real callback.

    The capability token never leaves this process: the report is sent over
    loopback HTTP to the real ``/setup/session/completion`` route, so the
    bearer, peer, schema, revision and readiness checks all run for real.
    """
    import asyncio

    from flowgency.app import app
    from flowgency.configuration.store import ConfigStore
    from flowgency.web.setup_completion import (
        SetupCompletionClientError,
        SetupCompletionCommand,
        completion_environment,
        submit_completion,
    )

    try:
        payload = await request.json()
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Completion payload must be valid JSON") from exc
    try:
        scheduler_result, acknowledged, revision = _completion_fixture_body(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    attempt = getattr(app.state.setup_sessions, "_completion", None)
    if attempt is None:
        raise HTTPException(status_code=404, detail="No prepared setup completion attempt")
    reported_revision = (
        STALE_REVISION if revision == "stale" else ConfigStore(attempt.config_path).load().revision
    )
    # model_construct skips client-side validation so the server's own refusal is exercised.
    command = SetupCompletionCommand.model_construct(
        launch_id=attempt.launch_id,
        revision=reported_revision,
        scheduler_result=scheduler_result,
        all_questions_answered=True,
        summary_delivered=True,
        limitations_acknowledged=acknowledged,
    )
    try:
        result = await asyncio.to_thread(
            submit_completion, command, completion_environment(attempt.launch)
        )
    except SetupCompletionClientError as error:
        return JSONResponse({"ok": False, "code": error.code}, status_code=409)
    return JSONResponse({"ok": True, "completion": result["completion"]})


def _live_change_case(payload: object) -> str:
    """Validate the closed live-change schema; raise ``ValueError`` on anything else."""
    if not isinstance(payload, dict) or set(payload) != {"case"}:
        raise ValueError("Live change payload must be an object with only a case")
    case = payload["case"]
    if not isinstance(case, str) or case not in LIVE_CHANGE_CASES:
        raise ValueError("Unsupported live change case")
    return case


def _apply_live_change(runtime: Path, case: str) -> None:
    """Change fixture state through ConfigStore or the ticket provider; a reset restores it."""
    if case == "navigation-membership":
        store = ConfigStore(runtime / "config.yaml")

        def patch(raw: dict) -> None:
            raw["teams"]["research"]["name"] = "Research updated"
            newsletter = raw["teams"]["newsletter"]
            newsletter["workflows"]["research-workflow"]["name"] = "Research updated"
            newsletter["workspaces"] = [{"name": "Reference", "type": "custom", "config": {}}]

        store.patch(store.load().revision, patch)
    elif case == "navigation-workflow-count":
        delivery_root = runtime / "tickets" / "delivery"
        provider = LocalTicketStorage(delivery_root, clock=lambda: datetime.fromisoformat(FIXED_NOW))
        binding = StorageBinding(
            integration="local",
            config={"root": str(delivery_root)},
            team_id="newsletter",
            workflow_id="delivery",
        )
        ticket = _ticket_record(
            binding,
            ticket_id="fixture-live-count",
            number=190,
            title="Counted by the live navigation",
            description="Added by the live-change fixture.",
            state_id="backlog",
        )
        provider.create(ticket, _ticket_operation(ticket.id))
    elif case in _INBOX_LIVE_CHANGES:
        _INBOX_LIVE_CHANGES[case](runtime)
    elif case in _AGENT_LIVE_CHANGES:
        _AGENT_LIVE_CHANGES[case](runtime)
    elif case in _JOB_LIVE_CHANGES:
        _JOB_LIVE_CHANGES[case](runtime)
    elif case in _LOG_LIVE_CHANGES:
        _LOG_LIVE_CHANGES[case](runtime)
    elif case in _WORKSPACE_LIVE_CHANGES:
        _WORKSPACE_LIVE_CHANGES[case](runtime)
    elif case in _ADMIN_LIVE_CHANGES:
        _ADMIN_LIVE_CHANGES[case](runtime)
    elif case in _LIBRARY_LIVE_CHANGES:
        _LIBRARY_LIVE_CHANGES[case](runtime)
    else:
        raise ValueError(f"Unknown live change case: {case}")


def _patch_runtime_config(runtime: Path, patch) -> None:
    store = ConfigStore(runtime / "config.yaml")
    store.patch(store.load().revision, patch)


def _inbox_routine_pending(runtime: Path) -> None:
    """Dispatch owes advisor's 09:00 routine; the seeded failure and waiting job would otherwise mask it."""

    def patch(raw: dict) -> None:
        raw["teams"]["newsletter"]["dispatch"] = {"enabled": True}

    _patch_runtime_config(runtime, patch)
    authority = JobStore(runtime / "memory-store")
    failed = authority.path("newsletter", "job-failed")
    failed.unlink(missing_ok=True)
    Path(f"{failed}.lock").unlink(missing_ok=True)
    cancel_job(authority.path("newsletter", "job-waiting"))


def _inbox_job_completes(runtime: Path) -> None:
    """Fire the owed occurrence the way the dispatcher does: marker, then a job that finishes."""
    day = FIXED_NOW[:10]
    marker = runtime / "teams" / "newsletter" / "logs" / day / f".event-advisor-daily-review-{day}"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.touch()
    job_id = "inbox-daily-review"
    path = JobStore(runtime / "memory-store").path("newsletter", job_id)
    write_job(path, JobRecord.from_spec(_job_spec(runtime, runtime / "config.yaml", job_id)))
    transition_job(path, "queued", "running", started_at="2026-07-16T11:59:00+00:00")
    transition_job(
        path,
        "running",
        "complete",
        completed_at="2026-07-16T11:59:30+00:00",
        duration_seconds=30,
    )


def _inbox_agent_added(runtime: Path) -> None:
    def patch(raw: dict) -> None:
        raw["teams"]["newsletter"]["agents"].append(
            {
                "name": "scribe",
                "blueprint": "reviewer",
                "integration": "ticket-test",
                "identity": {"display_name": "Scribe", "title": "Release Scribe", "emoji": "S"},
                "default_memory": {"scope": "agent"},
                "routines": [],
            }
        )

    _patch_runtime_config(runtime, patch)


def _inbox_agent_moved(runtime: Path) -> None:
    def patch(raw: dict) -> None:
        agents = raw["teams"]["newsletter"]["agents"]
        agents.insert(0, agents.pop(next(i for i, agent in enumerate(agents) if agent["name"] == "builder")))

    _patch_runtime_config(runtime, patch)


def _inbox_agent_removed(runtime: Path) -> None:
    def patch(raw: dict) -> None:
        agents = raw["teams"]["newsletter"]["agents"]
        agents[:] = [agent for agent in agents if agent["name"] != "researcher"]

    _patch_runtime_config(runtime, patch)


def _inbox_ticket_activity(runtime: Path) -> None:
    delivery_root = runtime / "tickets" / "delivery"
    provider = LocalTicketStorage(delivery_root, clock=lambda: datetime.fromisoformat(FIXED_NOW))
    binding = StorageBinding(
        integration="local",
        config={"root": str(delivery_root)},
        team_id="newsletter",
        workflow_id="delivery",
    )
    opened = TicketEvent(
        kind="opened",
        actor="local-user",
        summary="Ticket created",
        at=datetime.fromisoformat(FIXED_NOW) + timedelta(minutes=1),
    )
    ticket = _ticket_record(
        binding,
        ticket_id="fixture-live-activity",
        number=191,
        title="Surfaced by the live inbox",
        description="Added by the inbox live-change fixture.",
        state_id="backlog",
        events=(opened,),
    )
    provider.create(ticket, _ticket_operation(ticket.id))


def _inbox_queue_grows(runtime: Path) -> None:
    authority = JobStore(runtime / "memory-store")
    config_path = runtime / "config.yaml"
    for job_id, routine, due in (
        ("inbox-queued-1", "suite-health", "2026-07-16T08:00:00+00:00"),
        ("inbox-queued-2", "docs-audit", "2026-07-16T11:42:00+00:00"),
    ):
        spec = dataclasses.replace(
            _job_spec(runtime, config_path, job_id), agent_name="builder", routine_id=routine
        )
        write_job(authority.path("newsletter", job_id), JobRecord.from_spec(spec, due_at=due))


def _inbox_clock_advances(runtime: Path) -> None:
    del runtime
    current = datetime.fromisoformat(os.environ["FLOWGENCY_FIXED_NOW"])
    os.environ["FLOWGENCY_FIXED_NOW"] = (current + INBOX_CLOCK_ADVANCE).isoformat()


_INBOX_LIVE_CHANGES = {
    "inbox-routine-pending": _inbox_routine_pending,
    "inbox-job-completes": _inbox_job_completes,
    "inbox-agent-added": _inbox_agent_added,
    "inbox-agent-moved": _inbox_agent_moved,
    "inbox-agent-removed": _inbox_agent_removed,
    "inbox-ticket-activity": _inbox_ticket_activity,
    "inbox-queue-grows": _inbox_queue_grows,
    "inbox-clock-advances": _inbox_clock_advances,
}

ADVISOR_IDENTITY_TITLE = "Principal Strategist"
ADVISOR_EDITED_PROMPT_BODY = "Audit release blockers; an external editor changed this source.\n"


def _advisor_entry(raw: dict) -> dict:
    return next(agent for agent in raw["teams"]["newsletter"]["agents"] if agent["name"] == "advisor")


def _agent_identity(runtime: Path) -> None:
    def patch(raw: dict) -> None:
        _advisor_entry(raw)["identity"]["title"] = ADVISOR_IDENTITY_TITLE

    _patch_runtime_config(runtime, patch)


def _agent_active_job(runtime: Path) -> None:
    path = JobStore(runtime / "memory-store").path("newsletter", "agent-live-running")
    write_job(path, JobRecord.from_spec(_job_spec(runtime, runtime / "config.yaml", "agent-live-running")))
    transition_job(path, "queued", "running", started_at="2026-07-16T11:59:00+00:00")


def _agent_routines(runtime: Path) -> None:
    def patch(raw: dict) -> None:
        _advisor_entry(raw)["routines"].append(
            {
                "id": "weekly-sweep",
                "prompt": {"scope": "blueprint", "name": "release-window"},
                "schedule": {"every": "7d"},
                "memory": {"scope": "agent"},
            }
        )

    _patch_runtime_config(runtime, patch)


def _agent_catalog_digest(runtime: Path) -> None:
    """Change the private prompt source and the blueprint source behind advisor's digests."""
    store = PromptStore(runtime / "prompts")
    current = store.read("newsletter", "advisor", "local-triage")
    store.update(
        "newsletter",
        "advisor",
        "local-triage",
        expected_digest=current.document.digest,
        payload=_prompt_bytes(
            "local-triage",
            "Private local triage.",
            ADVISOR_EDITED_PROMPT_BODY,
            argument_hint="Escalate blockers if the draft is stale.",
        ),
    )
    _write(
        runtime / "agent-library" / "advisor" / "AGENTS.md",
        "# Advisor\n\nDeterministic release-gate instructions, edited externally.\n",
    )


def _agent_log_membership(runtime: Path) -> None:
    _write_log(
        runtime / "teams" / "newsletter" / "logs" / "2026-07-16" / "advisor-live-refresh.out",
        "live refresh log\n",
        mtime="2026-07-16T11:45:00+00:00",
    )


def _agent_memory_revision(runtime: Path) -> None:
    config = ConfigStore(runtime / "config.yaml").load().config
    store = MemoryStore(runtime / "memory-store")
    resolved = resolve_memory_selector(
        MemorySelector(scope="channel", channel="brand-strategy"),
        job_id="ui-preview",
        team_key="newsletter",
        agent_name="advisor",
        routine_id=None,
        channels=config.memory.channels,
        store_root=store.root,
    )
    current = store.read(resolved)
    store.try_update(
        resolved,
        current.revision,
        lambda snapshot: {**snapshot.files, "memory.md": b"# Brand Strategy\n\nRevised by another editor.\n"},
    )


def _agent_report_history(runtime: Path) -> None:
    config_path = runtime / "config.yaml"
    record = JobRecord.from_spec(
        dataclasses.replace(
            _job_spec(runtime, config_path, "advisor-live-report"),
            created_at="2026-07-16T11:40:00+00:00",
        )
    )
    record.status = "complete"
    record.started_at = "2026-07-16T11:40:00+00:00"
    record.completed_at = "2026-07-16T11:50:00+00:00"
    record.duration_seconds = 600
    record.execution_summary = "Published the live refresh handoff report."
    write_job(JobStore(runtime / "memory-store").path("newsletter", "advisor-live-report"), record)


def _agent_team_runtime(runtime: Path) -> None:
    def patch(raw: dict) -> None:
        raw["teams"]["newsletter"]["runtime"]["timeout"] = 3000

    _patch_runtime_config(runtime, patch)


def _agent_permissions(runtime: Path) -> None:
    (runtime / "reference-material").mkdir(parents=True, exist_ok=True)

    def patch(raw: dict) -> None:
        _advisor_entry(raw)["permissions"]["rules"].append(
            {"path": (runtime / "reference-material").as_posix(), "tools": ["read", "search"]}
        )

    _patch_runtime_config(runtime, patch)


def _agent_source_and_status(runtime: Path) -> None:
    _agent_identity(runtime)
    _agent_catalog_digest(runtime)
    _agent_active_job(runtime)


_AGENT_LIVE_CHANGES = {
    "agent-source-and-status": _agent_source_and_status,
    "agent-identity": _agent_identity,
    "agent-active-job": _agent_active_job,
    "agent-routines": _agent_routines,
    "agent-catalog-digest": _agent_catalog_digest,
    "agent-log-membership": _agent_log_membership,
    "agent-memory-revision": _agent_memory_revision,
    "agent-report-history": _agent_report_history,
    "agent-team-runtime": _agent_team_runtime,
    "agent-permissions": _agent_permissions,
}

LIVE_LOG_DAY = "2026-07-16"
LIVE_TAIL_LOG_NAME = "advisor-live-tail.out"
LIVE_TAIL_LOG_LINES = 120
LIVE_APPENDED_LINES = 10
LIVE_MEMBERSHIP_LOG_NAME = "advisor-live-membership.out"
LIVE_SECOND_ARTIFACT = "second-draft.md"
# Larger than the log preview's display limit, so the viewer reports truncation.
LIVE_OVERSIZED_LINES = 6000


def _job_store(runtime: Path) -> JobStore:
    return JobStore(runtime / "memory-store")


def _job_finishes(runtime: Path) -> None:
    path = _job_store(runtime).path("newsletter", "job-waiting")
    transition_job(
        path,
        "waiting_for_memory",
        "complete",
        completed_at="2026-07-16T12:00:30+00:00",
        duration_seconds=30,
    )


def _job_finishes_with_session(runtime: Path) -> None:
    path = _job_store(runtime).path("newsletter", "job-waiting")
    transition_job(
        path,
        "waiting_for_memory",
        "complete",
        completed_at="2026-07-16T12:00:30+00:00",
        duration_seconds=30,
        session_id="job-waiting-session",
    )


def _job_added(runtime: Path) -> None:
    write_job(
        _job_store(runtime).path("newsletter", "job-live-added"),
        JobRecord.from_spec(_job_spec(runtime, runtime / "config.yaml", "job-live-added")),
    )


def _job_removed(runtime: Path) -> None:
    path = _job_store(runtime).path("newsletter", "job-failed")
    path.unlink(missing_ok=True)
    Path(f"{path}.lock").unlink(missing_ok=True)


def _job_failure_artifacts(runtime: Path) -> None:
    authority = _job_store(runtime)
    path = authority.path("newsletter", "job-failed")
    artifact = authority.artifact_root("newsletter", "job-failed") / LIVE_SECOND_ARTIFACT
    _write(artifact, "# Second retained draft\n")
    record = read_job(path)
    retained = [
        *record.memory_publication["failed_artifacts"],
        {"name": artifact.name, "path": str(artifact.resolve()), "size": artifact.stat().st_size},
    ]
    write_job(path, dataclasses.replace(record, memory_publication={"failed_artifacts": retained}))


def _job_memory_published(runtime: Path) -> None:
    authority = _job_store(runtime)
    path = authority.path("newsletter", "job-failed")
    for artifact in authority.artifact_root("newsletter", "job-failed").glob("*.md"):
        artifact.unlink()
    write_job(path, dataclasses.replace(read_job(path), memory_publication={}))


_JOB_LIVE_CHANGES = {
    "job-finishes": _job_finishes,
    "job-finishes-with-session": _job_finishes_with_session,
    "job-added": _job_added,
    "job-removed": _job_removed,
    "job-failure-artifacts": _job_failure_artifacts,
    "job-memory-published": _job_memory_published,
}


def _live_log_dir(runtime: Path) -> Path:
    return runtime / "teams" / "newsletter" / "logs" / LIVE_LOG_DAY


def _log_tall(runtime: Path) -> None:
    lines = (f"tail line {number}" for number in range(1, LIVE_TAIL_LOG_LINES + 1))
    _write_log(
        _live_log_dir(runtime) / LIVE_TAIL_LOG_NAME,
        "\n".join(lines) + "\n",
        mtime="2026-07-16T11:50:00+00:00",
    )


def _log_appended(runtime: Path) -> None:
    lines = "".join(f"appended line {number}\n" for number in range(1, LIVE_APPENDED_LINES + 1))
    with (_live_log_dir(runtime) / LIVE_TAIL_LOG_NAME).open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(lines)


def _log_truncated(runtime: Path) -> None:
    _write(_live_log_dir(runtime) / LIVE_TAIL_LOG_NAME, "rotated line\n")


def _log_oversized(runtime: Path) -> None:
    _write(
        _live_log_dir(runtime) / LIVE_TAIL_LOG_NAME,
        "# oversized\n\n" + "oversized line\n" * LIVE_OVERSIZED_LINES,
    )


def _log_removed(runtime: Path) -> None:
    (_live_log_dir(runtime) / LIVE_TAIL_LOG_NAME).unlink(missing_ok=True)


def _log_listing_membership(runtime: Path) -> None:
    _write_log(
        _live_log_dir(runtime) / LIVE_MEMBERSHIP_LOG_NAME,
        "membership log\n",
        mtime="2026-07-16T11:55:00+00:00",
    )
    (_live_log_dir(runtime) / "advisor-job-failed.err").unlink(missing_ok=True)


_LOG_LIVE_CHANGES = {
    "log-tall": _log_tall,
    "log-appended": _log_appended,
    "log-truncated": _log_truncated,
    "log-oversized": _log_oversized,
    "log-removed": _log_removed,
    "log-listing-membership": _log_listing_membership,
}


def _workspace_sources(runtime: Path) -> Path:
    return runtime / WORKSPACE_SOURCES_DIR


def _workspace_entry(name: str, kind: str, path: Path) -> dict:
    if kind == "custom":
        config = {"label": "Notes", "config_path": str(path), "language": "markdown"}
    else:
        config = {"script_path": str(path)}
    return {"name": name, "type": kind, "config": config}


def _fixture_workspaces(runtime: Path) -> list[dict]:
    sources = _workspace_sources(runtime)
    return [
        _workspace_entry("Editorial Notes", "custom", sources / WORKSPACE_NOTES_NAME),
        _workspace_entry("Session Script", "tmux", sources / WORKSPACE_SCRIPT_NAME),
    ]


def _write_workspace_source(runtime: Path, name: str, text: str) -> None:
    # Bytes, so Windows does not rewrite the newlines the status line reports a size for.
    sources = _workspace_sources(runtime)
    sources.mkdir(parents=True, exist_ok=True)
    (sources / name).write_bytes(text.encode("utf-8"))


def _seed_workspace_sources(runtime: Path) -> None:
    _write_workspace_source(runtime, WORKSPACE_NOTES_NAME, WORKSPACE_NOTES_TEXT)
    _write_workspace_source(runtime, WORKSPACE_SCRIPT_NAME, WORKSPACE_SCRIPT_TEXT)


def _workspace_file_changes(runtime: Path) -> None:
    _write_workspace_source(runtime, WORKSPACE_NOTES_NAME, WORKSPACE_CHANGED_NOTES_TEXT)


def _workspace_file_removed(runtime: Path) -> None:
    (_workspace_sources(runtime) / WORKSPACE_NOTES_NAME).unlink(missing_ok=True)


def _workspace_reordered(runtime: Path) -> None:
    _patch_runtime_config(runtime, lambda raw: raw["teams"]["newsletter"]["workspaces"].reverse())


def _workspace_removed(runtime: Path) -> None:
    _patch_runtime_config(runtime, lambda raw: raw["teams"]["newsletter"]["workspaces"].pop())


def _workspace_added(runtime: Path) -> None:
    _write_workspace_source(runtime, WORKSPACE_REVIEW_SCRIPT_NAME, WORKSPACE_SCRIPT_TEXT)
    entry = _workspace_entry(
        "Review Script", "tmux", _workspace_sources(runtime) / WORKSPACE_REVIEW_SCRIPT_NAME
    )
    _patch_runtime_config(runtime, lambda raw: raw["teams"]["newsletter"]["workspaces"].append(entry))


_WORKSPACE_LIVE_CHANGES = {
    "workspace-file-changes": _workspace_file_changes,
    "workspace-file-removed": _workspace_file_removed,
    "workspace-reordered": _workspace_reordered,
    "workspace-removed": _workspace_removed,
    "workspace-added": _workspace_added,
}

INTEGRATION_SOURCE_DIR = "integration-source"
INTEGRATION_SOURCE_ENV = "FLOWGENCY_UI_INTEGRATION_SOURCE"
INTEGRATION_REGISTERED_MODULE = "acme.widget"
INTEGRATION_ADDED_MODULE = "acme.gadget"
ADMIN_SETTINGS_TITLE = "Flowgency UI Gate Renamed"
ADMIN_DISPATCH_INTERVAL = 30
ADMIN_CREATED_TEAM = "scribe-team"
ADMIN_CHANGED_TEAM_NAME = "Newsletter Desk"
_INTEGRATION_STUB = (
    "from flowgency.integrations import BaseIntegration, _register\n"
    "\n"
    "# Fixture module: the integration scan reads it as text and never imports it.\n"
)


def _integration_source(runtime: Path) -> Path:
    return runtime / INTEGRATION_SOURCE_DIR


def _write_integration_module(runtime: Path, module_path: str) -> None:
    author, name = module_path.split(".")
    _write(_integration_source(runtime) / author / f"{name}.py", _INTEGRATION_STUB)


def _seed_integration_source(runtime: Path) -> None:
    """A private copy of the integration listing the admin page scans, so the product tree is never written."""
    import flowgency.integrations as integrations_module

    source = _integration_source(runtime)
    _clear_directory(source)
    shutil.copyfile(Path(integrations_module.__file__).parent / "integrations.yaml", source / "integrations.yaml")
    _write_integration_module(runtime, INTEGRATION_REGISTERED_MODULE)


def _integration_registered(runtime: Path) -> None:
    config_path = _integration_source(runtime) / "integrations.yaml"
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    data["integrations"].append(INTEGRATION_REGISTERED_MODULE)
    _write(config_path, yaml.safe_dump(data, sort_keys=False))


def _integration_available_added(runtime: Path) -> None:
    _write_integration_module(runtime, INTEGRATION_ADDED_MODULE)


def _admin_settings_changed(runtime: Path) -> None:
    def patch(raw: dict) -> None:
        raw["flowgency"]["title"] = ADMIN_SETTINGS_TITLE
        raw["flowgency"]["dispatch"] = {"interval": ADMIN_DISPATCH_INTERVAL}

    _patch_runtime_config(runtime, patch)


def _admin_dispatch_changed(runtime: Path) -> None:
    _patch_runtime_config(runtime, lambda raw: raw["teams"]["research"].update(dispatch={"enabled": True}))


def _admin_created_team_paths(runtime: Path) -> tuple[Path, Path]:
    return runtime / "workspaces" / ADMIN_CREATED_TEAM, runtime / "groups" / ADMIN_CREATED_TEAM


def _admin_team_created(runtime: Path) -> None:
    workspace, team_path = _admin_created_team_paths(runtime)
    workspace.mkdir(parents=True, exist_ok=True)
    team_path.mkdir(parents=True, exist_ok=True)

    def patch(raw: dict) -> None:
        raw["teams"][ADMIN_CREATED_TEAM] = {
            "name": "Scribe Team",
            "workspace_path": workspace.as_posix(),
            "path": team_path.as_posix(),
            "default_integration": "copilot",
            "permissions": {"mode": "unrestricted", "rules": []},
            "agents": [],
            "workspaces": [],
        }

    _patch_runtime_config(runtime, patch)


def _admin_team_changed(runtime: Path) -> None:
    def patch(raw: dict) -> None:
        newsletter = raw["teams"]["newsletter"]
        newsletter["name"] = ADMIN_CHANGED_TEAM_NAME
        newsletter["dispatch"] = {"enabled": True}
        newsletter["agents"].append(
            {
                "name": "scribe",
                "blueprint": "reviewer",
                "integration": "ticket-test",
                "identity": {"display_name": "Scribe", "title": "Release Scribe", "emoji": "S"},
                "default_memory": {"scope": "agent"},
                "routines": [],
            }
        )

    _patch_runtime_config(runtime, patch)


def _admin_team_removed(runtime: Path) -> None:
    _patch_runtime_config(runtime, lambda raw: raw["teams"].pop("research"))


_ADMIN_LIVE_CHANGES = {
    "admin-settings-changed": _admin_settings_changed,
    "admin-dispatch-changed": _admin_dispatch_changed,
    "admin-team-created": _admin_team_created,
    "admin-team-changed": _admin_team_changed,
    "admin-team-removed": _admin_team_removed,
    "integration-registered": _integration_registered,
    "integration-available-added": _integration_available_added,
}

LIBRARY_ADDED_BLUEPRINT = "designer"
LIBRARY_EDITED_TITLE = "Advisor Prime"
LIBRARY_ADDED_SKILL_FILE = "notes.md"
LIBRARY_ADDED_PROMPT = "weekly-brief"
LIBRARY_REMOVED_SKILL_FILE = "checklist.md"
LIBRARY_REMOVED_PROMPT = "release-window"
CHANNEL_KEY = "brand-strategy"
CHANNEL_ADDED = "launch-notes"
CHANNEL_RENAMED = "Brand Strategy Council"
CHANNEL_APPENDIX = "appendix.md"
CHANNEL_EDITED_MEMORY = "# Brand Strategy\n\nRevised in the live channel case.\n"
ADVISOR_CHECKLIST_TEXT = "- Verify content\n"


def _advisor_source(runtime: Path) -> Path:
    return runtime / "agent-library" / "advisor"


def _advisor_skill_dir(runtime: Path) -> Path:
    return _advisor_source(runtime) / ".agents" / "skills" / "daily-review"


def _advisor_prompt_path(runtime: Path, name: str) -> Path:
    return _advisor_source(runtime) / ".agents" / "prompts" / f"{name}.prompt.md"


def _release_window_payload() -> bytes:
    return _prompt_bytes(
        LIBRARY_REMOVED_PROMPT,
        "Shared release window check.",
        "Verify the release window, rollout risk, and communication timing.\n",
        argument_hint="Include the launch date and any blocked approvals.",
    )


def _library_source_changes(runtime: Path) -> None:
    """Edit the advisor blueprint's AGENTS.md and add a skill file and a shared prompt."""
    _write(
        _advisor_source(runtime) / "AGENTS.md",
        f"# {LIBRARY_EDITED_TITLE}\n\nDeterministic release-gate instructions, edited by the library case.\n",
    )
    _write(_advisor_skill_dir(runtime) / LIBRARY_ADDED_SKILL_FILE, "- Added by the library case\n")
    _advisor_prompt_path(runtime, LIBRARY_ADDED_PROMPT).write_bytes(
        _prompt_bytes(LIBRARY_ADDED_PROMPT, "Weekly brief.", "Summarize the week.\n")
    )


def _library_blueprint_added(runtime: Path) -> None:
    _seed_blueprint(
        runtime / "agent-library",
        LIBRARY_ADDED_BLUEPRINT,
        "Designer",
        "design-review",
        prompts=(("design-review", "Shared design review.", "Review the design draft.\n", None),),
    )


def _library_blueprint_removed(runtime: Path) -> None:
    shutil.rmtree(runtime / "agent-library" / LIBRARY_ADDED_BLUEPRINT, ignore_errors=True)


def _library_selected_files_removed(runtime: Path) -> None:
    (_advisor_skill_dir(runtime) / LIBRARY_REMOVED_SKILL_FILE).unlink(missing_ok=True)
    _advisor_prompt_path(runtime, LIBRARY_REMOVED_PROMPT).unlink(missing_ok=True)


def _restore_library_sources(runtime: Path) -> None:
    """Undo every library case: edited, added and removed blueprint files."""
    _write(
        _advisor_source(runtime) / "AGENTS.md",
        "# Advisor\n\nDeterministic release-gate instructions.\n",
    )
    (_advisor_skill_dir(runtime) / LIBRARY_ADDED_SKILL_FILE).unlink(missing_ok=True)
    _write(_advisor_skill_dir(runtime) / LIBRARY_REMOVED_SKILL_FILE, ADVISOR_CHECKLIST_TEXT)
    _advisor_prompt_path(runtime, LIBRARY_ADDED_PROMPT).unlink(missing_ok=True)
    _advisor_prompt_path(runtime, LIBRARY_REMOVED_PROMPT).write_bytes(_release_window_payload())
    shutil.rmtree(runtime / "agent-library" / LIBRARY_ADDED_BLUEPRINT, ignore_errors=True)


def _channel_memory(runtime: Path, channel_key: str, channels: dict):
    store = MemoryStore(runtime / "memory-store")
    resolved = resolve_memory_selector(
        MemorySelector(scope="channel", channel=channel_key),
        job_id="ui-preview",
        team_key="newsletter",
        agent_name="advisor",
        routine_id=None,
        channels=channels,
        store_root=store.root,
    )
    return store, resolved


def _channel_memory_changes(runtime: Path) -> None:
    channels = ConfigStore(runtime / "config.yaml").load().config.memory.channels
    store, resolved = _channel_memory(runtime, CHANNEL_KEY, channels)
    current = store.read(resolved)
    store.try_update(
        resolved,
        current.revision,
        lambda snapshot: {
            **snapshot.files,
            "memory.md": CHANNEL_EDITED_MEMORY.encode("utf-8"),
            CHANNEL_APPENDIX: b"# Appendix\n",
        },
    )


def _channel_metadata_changed(runtime: Path) -> None:
    _patch_runtime_config(
        runtime, lambda raw: raw["memory"]["channels"][CHANNEL_KEY].update(display_name=CHANNEL_RENAMED)
    )


def _channel_added(runtime: Path) -> None:
    _patch_runtime_config(
        runtime,
        lambda raw: raw["memory"]["channels"].update({CHANNEL_ADDED: {"display_name": "Launch Notes"}}),
    )


def _channel_removed(runtime: Path) -> None:
    _patch_runtime_config(runtime, lambda raw: raw["memory"]["channels"].pop(CHANNEL_ADDED, None))


_LIBRARY_LIVE_CHANGES = {
    "library-source-changes": _library_source_changes,
    "library-blueprint-added": _library_blueprint_added,
    "library-blueprint-removed": _library_blueprint_removed,
    "library-selected-files-removed": _library_selected_files_removed,
    "channel-memory-changes": _channel_memory_changes,
    "channel-metadata-changed": _channel_metadata_changed,
    "channel-added": _channel_added,
    "channel-removed": _channel_removed,
}


def install_non_live_page(app):
    """Register the purpose-built page that extends the base layout and has no live policy."""
    from fastapi.responses import HTMLResponse

    from flowgency.app import get_team, team_context

    for route in app.router.routes:
        if getattr(route, "path", None) == NON_LIVE_PAGE_PATH:
            return route

    @app.get(NON_LIVE_PAGE_PATH, response_class=HTMLResponse, include_in_schema=False)
    async def non_live_page(request: Request) -> Response:
        context = team_context(get_team("newsletter"))
        return request.app.state.templates.TemplateResponse(
            request, "base.html", {"request": request, **context, "active": "none"}
        )

    return app.router.routes[-1]


def _install_ui_test_runtime() -> None:
    global _REAL_COPILOT_INTEGRATION
    import flowgency.jobs.submission as submission_module
    import flowgency.web.dependencies as web_dependencies
    from flowgency.app import app
    from tests._ticket_helpers import TicketRuntimeIntegration

    class UITicketRuntimeIntegration(TicketRuntimeIntegration):
        name = "ticket-test"
        display_name = "UI Ticket Test Runtime"

    REGISTRY["ticket-test"] = UITicketRuntimeIntegration()
    # Opt-in: the served process scans a private integration listing; in-process tests redirect it themselves.
    integration_source = os.environ.get(INTEGRATION_SOURCE_ENV)
    if integration_source:
        import flowgency.integrations as integrations_module

        integrations_module.INTEGRATIONS_DIR = Path(integration_source)
    if _REAL_COPILOT_INTEGRATION is None:
        _REAL_COPILOT_INTEGRATION = REGISTRY.get("copilot")
    submission_module.submit_job_request = _ui_submit_job_request
    web_dependencies.submit_job_request = _ui_submit_job_request

    # Idempotent and independent of the flag below, so a scoped removal never leaves the page absent.
    install_non_live_page(app)

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
        await _reset_connected_setup_runtime(fixture)
        _reset_runtime_state(runtime_root, fixture=fixture)
        return Response(status_code=204)

    @app.get("/__ui/setup/meta", include_in_schema=False)
    async def connected_setup_meta() -> Response:
        runtime_root = Path(os.environ["FLOWGENCY_UI_RUNTIME"])
        return JSONResponse({"data_root": str(runtime_root / "flowgency-data")})

    @app.post("/__ui/setup/ready", include_in_schema=False)
    async def connected_setup_ready(request: Request) -> Response:
        runtime_root = Path(os.environ["FLOWGENCY_UI_RUNTIME"])
        definition_mode = request.query_params.get("definition")
        if definition_mode not in {None, "missing", "valid"}:
            raise HTTPException(status_code=400, detail="Unsupported definition mode")
        config = _connected_setup_ready_config(runtime_root, definition_mode)
        _prepare_connected_setup_ready(runtime_root, config, definition_mode)
        _write_runtime_config(runtime_root / "config.yaml", config)
        from flowgency.web.dependencies import build_services

        app.state.services = build_services(runtime_root / "config.yaml")
        return Response(status_code=204)

    @app.get("/__ui/setup/session/writes", include_in_schema=False)
    async def connected_setup_session_writes() -> Response:
        if _CURRENT_FAKE_PROCESS is None:
            raise HTTPException(status_code=404, detail="No connected-setup fixture session")
        return JSONResponse(
            {
                "writes": [chunk.decode("utf-8", errors="replace") for chunk in _CURRENT_FAKE_PROCESS.writes],
                "sizes": [list(size) for size in _CURRENT_FAKE_PROCESS.sizes],
            }
        )

    @app.post("/__ui/setup/session/complete", include_in_schema=False)
    async def connected_setup_session_complete(request: Request) -> Response:
        return await _complete_connected_setup(request)

    @app.post("/__ui/setup/session/emit", include_in_schema=False)
    async def connected_setup_session_emit(request: Request) -> Response:
        if _CURRENT_FAKE_PROCESS is None:
            raise HTTPException(status_code=404, detail="No connected-setup fixture session")
        payload = await request.json()
        text = payload.get("text")
        if text is not None:
            if not isinstance(text, str):
                raise HTTPException(status_code=400, detail="text must be a string")
            _CURRENT_FAKE_PROCESS.output.put(text.encode("utf-8"))
            return Response(status_code=204)
        if payload.get("eof"):
            # FakeProcess.read() raises EOFError on a queued None, matching
            # a real PTY's natural end-of-stream so the manager finalizes
            # the session as "exited" rather than "stopped".
            _CURRENT_FAKE_PROCESS.output.put(None)
            return Response(status_code=204)
        remaining = int(payload.get("length", 0))
        while remaining > 0:
            chunk = min(remaining, 1024)
            _CURRENT_FAKE_PROCESS.output.put(b"x" * chunk)
            remaining -= chunk
        return Response(status_code=204)

    @app.post(LIVE_CHANGE_PATH, include_in_schema=False)
    async def live_change(request: Request) -> Response:
        try:
            payload = await request.json()
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="Live change payload must be valid JSON") from exc
        try:
            case = _live_change_case(payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        _apply_live_change(Path(os.environ["FLOWGENCY_UI_RUNTIME"]), case)
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


def _connected_setup_ready_config(runtime: Path, definition_mode: str | None) -> dict:
    raw = yaml.safe_load(FIXTURE_CONFIG.read_text(encoding="utf-8"))
    config = _replace_runtime(raw, runtime)
    if definition_mode is None:
        return config

    config["teams"]["newsletter"]["workflows"] = {
        "delivery": {
            "name": "Delivery",
            "blueprint": "software-delivery",
            "integration": "local",
            "integration_config": {
                "root": str((runtime / "tickets" / "delivery").resolve())
            },
        }
    }
    return config


def _prepare_connected_setup_ready(runtime: Path, config: dict, definition_mode: str | None) -> None:
    for root in _configured_workflow_roots(config):
        root.mkdir(parents=True, exist_ok=True)

    workflow_library = runtime / "workflow-library"
    selected_source = workflow_library / "software-delivery" / "workflow.yaml"
    if definition_mode is None:
        if not (workflow_library / "delivery" / "workflow.yaml").exists():
            _seed_workflow_blueprint(workflow_library, "delivery", _delivery_definition())
        if not (workflow_library / "research-workflow" / "workflow.yaml").exists():
            _seed_workflow_blueprint(workflow_library, "research-workflow", _research_definition())
        return
    if definition_mode == "missing":
        selected_source.unlink(missing_ok=True)
        return
    if definition_mode == "valid":
        from flowgency.setup_assets import workflow_example_root
        from flowgency.workflows.library import WorkflowLibrary

        packaged = WorkflowLibrary(workflow_example_root())
        runtime_library = WorkflowLibrary(workflow_library)
        if not selected_source.exists():
            runtime_library.create_candidate(
                "software-delivery",
                packaged.inspect("software-delivery").definition,
            )
        return
    raise ValueError(f"Unsupported definition mode: {definition_mode}")



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



def _local_triage_payload() -> bytes:
    return _prompt_bytes(
        "local-triage",
        "Private local triage.",
        "Audit the current release blockers and call out anything that needs a human decision.\n",
        argument_hint="Escalate blockers if the draft is stale.",
    )


def _seed_private_prompts(runtime: Path) -> None:
    PromptStore(runtime / "prompts").create("newsletter", "advisor", "local-triage", _local_triage_payload())


def _restore_external_sources(runtime: Path) -> None:
    """Undo live-change edits to sources that live outside the config and job stores."""
    atomic_write_bytes(
        PromptStore(runtime / "prompts").path("newsletter", "advisor", "local-triage"),
        _local_triage_payload(),
    )
    _restore_library_sources(runtime)


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
    # Files a live case added beside memory.md, and the memory of a channel it declared.
    for extra in channel.directory.glob("*.md"):
        if extra.name != "memory.md":
            extra.unlink()
    added = _channel_memory(runtime, CHANNEL_ADDED, {**config["memory"]["channels"], CHANNEL_ADDED: {}})[1]
    shutil.rmtree(added.directory, ignore_errors=True)


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
    if "FLOWGENCY_FIXED_NOW" in os.environ:
        os.environ["FLOWGENCY_FIXED_NOW"] = FIXED_NOW
    if fixture == CONNECTED_SETUP_FIXTURE:
        # No teams config yet: the setup page must render its guided form
        # (state "waiting") rather than the ready dashboard.
        (runtime / "config.yaml").unlink(missing_ok=True)
        (runtime / "flowgency-data").mkdir(parents=True, exist_ok=True)
        from flowgency.app import app
        from flowgency.web.dependencies import build_services

        app.state.services = build_services(runtime / "config.yaml")
        return

    raw = yaml.safe_load(FIXTURE_CONFIG.read_text(encoding="utf-8"))
    config = _replace_runtime(raw, runtime)
    if fixture == GIT_EVIDENCE_FIXTURE:
        config["teams"]["newsletter"]["git_publication"] = {
            "mode": "local",
            "allowed_refs": [GIT_EVIDENCE_REF],
        }
    _clear_directory(_workspace_sources(runtime))
    if fixture == WORKSPACES_FIXTURE:
        config["teams"]["newsletter"]["workspaces"] = _fixture_workspaces(runtime)
        _seed_workspace_sources(runtime)
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
    _seed_integration_source(runtime)
    for created in _admin_created_team_paths(runtime):
        shutil.rmtree(created, ignore_errors=True)
    _restore_external_sources(runtime)
    if fixture == ACTIVITY_LOGS_FIXTURE:
        _apply_activity_logs_fixture(runtime, runtime / "config.yaml")
    elif fixture == GIT_EVIDENCE_FIXTURE:
        _seed_git_evidence_fixture(runtime, config)
    elif fixture not in {"default", WORKSPACES_FIXTURE}:
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
    _seed_integration_source(runtime)
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
        env[INTEGRATION_SOURCE_ENV] = str(_integration_source(runtime))
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