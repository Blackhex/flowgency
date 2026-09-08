from dataclasses import replace
import json
from pathlib import Path

import yaml

from flowgency.blueprints.cache import active_pins, pin_artifact
from flowgency.jobs.authority import JobStore
from flowgency.jobs.models import BlueprintRef, JobRecord, JobSpec, MemoryBinding, RuntimePolicySnapshot
from flowgency.jobs.reconciliation import reconcile_jobs, worker_alive
from flowgency.memory.recovery import recover_publications
from flowgency.jobs.store import job_path, read_job, write_job
from flowgency.tickets.models import ActiveTicketRun, TicketEvent, TicketOperation, TicketRef, TicketRunReservation
from flowgency.workflows.configuration import resolve_workflow_binding

from tests._ticket_helpers import SEED_TIME, make_ticket_job_environment


def _job_store(tmp_path: Path) -> JobStore:
    return JobStore(tmp_path / "memory")


def running_decision_job(
    tmp_path: Path,
    pid: int = 999999,
    *,
    team_key: str = "test",
    memory_store_root: Path | None = None,
):
    team_dir = tmp_path / "team"
    decision = team_dir / "decisions" / "change.md"
    decision.parent.mkdir(parents=True)
    config_path = tmp_path / "config.yaml"
    config_path.write_text("schema_version: 1\nteams: {}\n", encoding="utf-8")
    spec = JobSpec(
        schema_version=5,
        job_id="decision-job",
        config_path=str(config_path.resolve()),
        config_revision="cfg-1",
        team_key=team_key,
        team_root=str(team_dir.resolve()),
        agent_name="product",
        workspace_root=str(team_dir.resolve()),
        trigger="decision",
        integration_name="script",
        integration_config={},
        blueprint=BlueprintRef(
            key="decision-blueprint",
            source_digest="digest-1",
            integration="script",
            projector_version="v1",
            cache_path=str((tmp_path / "compiled-agents" / "script" / "v1" / "digest-1" / "entry.py").resolve()),
        ),
        routine_id=None,
        skill=None,
        skill_arguments=(),
        task_input="run",
        runtime_policy=RuntimePolicySnapshot(
            timeout=1800,
            mode="unrestricted",
        ),
        memory=MemoryBinding(
            selector={"scope": "run", "version": 1, "job": "decision-job"},
            canonical_json='{"job":"decision-job","scope":"run","version":1}',
            memory_hash="memory-hash-1",
            path=str((tmp_path / "memory" / "memory-hash-1").resolve()),
        ),
        trigger_context={
            "decision_path": str(decision),
            "proposal_path": "proposal.md",
        },
        prompt_source={"type": "decision"},
        timeout_override=None,
        created_at="2026-07-15T00:00:00+00:00",
    )
    decision.write_text(
        f"---\nexecution_status: running\nexecution_job_id: {spec.job_id}\n---\n"
    )
    path = JobStore(memory_store_root or (tmp_path / "memory")).path(spec.team_key, spec.job_id)
    write_job(
        path,
        replace(JobRecord.from_spec(spec), status="running", worker_pid=pid),
    )
    return team_dir, decision, path


def reconcile_for_test(teams, tmp_path, *, memory_store_root=None):
    return reconcile_jobs(
        teams,
        memory_store_root=memory_store_root or (tmp_path / "memory"),
    )


def test_reconcile_accepts_teams_keyword_argument(tmp_path, monkeypatch):
    monkeypatch.setattr("flowgency.jobs.reconciliation.worker_alive", lambda pid: True)

    result = reconcile_jobs(teams={}, memory_store_root=tmp_path / "memory")

    assert result.failed == 0
    assert result.left_running == 0


def test_reconcile_leaves_live_worker_running(tmp_path, monkeypatch):
    team_dir, decision, path = running_decision_job(tmp_path)
    monkeypatch.setattr("flowgency.jobs.reconciliation.worker_alive", lambda pid: True)
    result = reconcile_for_test({"test": {"team_root": str(team_dir)}}, tmp_path)
    assert result.left_running == 1
    assert read_job(path).status == "running"
    assert "execution_status: running" in decision.read_text()


def test_reconcile_marks_confirmed_dead_worker_failed(tmp_path, monkeypatch):
    team_dir, decision, path = running_decision_job(tmp_path)
    monkeypatch.setattr("flowgency.jobs.reconciliation.worker_alive", lambda pid: False)
    result = reconcile_for_test({"test": {"team_root": str(team_dir)}}, tmp_path)
    assert result.failed == 1
    record = read_job(path)
    assert record.status == "failed"
    assert record.completed_at is not None
    assert record.execution_summary == "Worker process (PID 999999) was not found."
    assert "execution_status: failed" in decision.read_text()


def test_reconcile_releases_pin_for_dead_waiting_worker(tmp_path, monkeypatch):
    team_dir, _, path = running_decision_job(tmp_path)
    record = read_job(path)
    artifact = record.spec.blueprint.to_artifact()
    artifact.runtime_path.mkdir(parents=True, exist_ok=True)
    (artifact.runtime_path / "AGENTS.md").write_text("# Agent\n", encoding="utf-8")
    pin_artifact(record.spec.blueprint.cache_root, artifact.ref, record.spec.job_id)
    write_job(
        path,
        replace(record, status="waiting_for_memory", worker_pid=999999),
    )

    monkeypatch.setattr("flowgency.jobs.reconciliation.worker_alive", lambda pid: False)

    result = reconcile_for_test({"test": {"team_root": str(team_dir)}}, tmp_path)

    assert result.failed == 1
    assert active_pins(record.spec.blueprint.cache_root, artifact.ref) == ()


def test_reconcile_releases_pin_for_dead_running_worker_but_keeps_live_pin(
    tmp_path,
    monkeypatch,
):
    shared_store = tmp_path / "memory"
    dead_group, _, dead_path = running_decision_job(
        tmp_path / "dead",
        team_key="dead",
        memory_store_root=shared_store,
    )
    live_group, _, live_path = running_decision_job(
        tmp_path / "live",
        pid=123456,
        team_key="live",
        memory_store_root=shared_store,
    )
    dead_record = read_job(dead_path)
    live_record = read_job(live_path)
    dead_artifact = dead_record.spec.blueprint.to_artifact()
    live_artifact = live_record.spec.blueprint.to_artifact()
    dead_artifact.runtime_path.mkdir(parents=True, exist_ok=True)
    live_artifact.runtime_path.mkdir(parents=True, exist_ok=True)
    (dead_artifact.runtime_path / "AGENTS.md").write_text("# Agent\n", encoding="utf-8")
    (live_artifact.runtime_path / "AGENTS.md").write_text("# Agent\n", encoding="utf-8")
    pin_artifact(dead_record.spec.blueprint.cache_root, dead_artifact.ref, dead_record.spec.job_id)
    pin_artifact(live_record.spec.blueprint.cache_root, live_artifact.ref, live_record.spec.job_id)

    monkeypatch.setattr(
        "flowgency.jobs.reconciliation.worker_alive",
        lambda pid: False if pid == 999999 else True,
    )

    result = reconcile_for_test(
        {
            "dead": {"team_root": str(dead_group)},
            "live": {"team_root": str(live_group)},
        },
        tmp_path,
    )

    assert result.failed == 1
    assert result.left_running == 1
    assert active_pins(dead_record.spec.blueprint.cache_root, dead_artifact.ref) == ()
    assert active_pins(live_record.spec.blueprint.cache_root, live_artifact.ref) == (
        live_record.spec.job_id,
    )


def test_reconcile_projects_terminal_job_to_stale_decision(tmp_path):
    team_dir, decision, path = running_decision_job(tmp_path)
    record = read_job(path)
    write_job(
        path,
        replace(
            record,
            status="failed",
            completed_at="2026-07-11T22:14:14+00:00",
            execution_summary="Agent timed out after 300 seconds.",
        ),
    )

    reconcile_for_test({"test": {"team_root": str(team_dir)}}, tmp_path)

    decision_text = decision.read_text()
    assert "execution_status: failed" in decision_text
    assert "Agent timed out after 300 seconds." in decision_text


def test_reconcile_projects_complete_job_with_changed_files(tmp_path):
    """A terminal ``complete`` job carrying non-empty ``changed_files`` must
    project both its status and the captured files onto a stale ``running``
    decision. This is the exact behaviour the cross-tool capture advertises and
    was previously only asserted for a failed, empty-changes job."""
    team_dir, decision, path = running_decision_job(tmp_path)
    record = read_job(path)
    changed_files = [
        {"path": "a.py", "status": "modified", "lines_added": 3, "lines_removed": 1},
        {"path": "b.py", "status": "added", "lines_added": 10, "lines_removed": 0},
    ]
    write_job(
        path,
        replace(
            record,
            status="complete",
            completed_at="2026-07-11T22:14:14+00:00",
            changed_files=changed_files,
            execution_summary="Agent completed execution; captured 2 changed files.",
        ),
    )

    reconcile_for_test({"test": {"team_root": str(team_dir)}}, tmp_path)

    metadata = yaml.safe_load(decision.read_text().split("---")[1])
    assert metadata["execution_status"] == "complete"
    assert metadata["changed_files"] == changed_files
    assert metadata["execution_summary"] == (
        "Agent completed execution; captured 2 changed files."
    )
    assert read_job(path).status == "complete"


def test_reconcile_leaves_uncertain_worker_running(tmp_path, monkeypatch):
    team_dir, _, path = running_decision_job(tmp_path)
    monkeypatch.setattr("flowgency.jobs.reconciliation.worker_alive", lambda pid: None)
    result = reconcile_for_test({"test": {"team_root": str(team_dir)}}, tmp_path)
    assert result.left_running == 1
    assert read_job(path).status == "running"


def test_reconcile_fails_dead_waiting_worker(tmp_path, monkeypatch):
    team_dir, _, path = running_decision_job(tmp_path)
    record = read_job(path)
    write_job(
        path,
        replace(record, status="waiting_for_memory", worker_pid=999999),
    )

    monkeypatch.setattr("flowgency.jobs.reconciliation.worker_alive", lambda pid: False)

    result = reconcile_for_test({"test": {"team_root": str(team_dir)}}, tmp_path)

    assert result.failed == 1
    reconciled = read_job(path)
    assert reconciled.status == "failed"
    assert "999999" in (reconciled.execution_summary or "")


def test_reconcile_recovers_published_journal_before_failing_dead_worker(tmp_path, monkeypatch):
    from flowgency.configuration.models import MemorySelector
    from flowgency.jobs.models import JobSpec
    from flowgency.memory import MemoryStore, resolve_memory_selector
    from flowgency.memory.publication import apply_publication, prepare_publication

    team_dir = tmp_path / "team"
    config_path = tmp_path / "config.yaml"
    config_path.write_text("schema_version: 1\nteams: {}\n", encoding="utf-8")
    memory_binding = resolve_memory_selector(
        MemorySelector(scope="agent"),
        job_id="placeholder",
        team_key="test",
        agent_name="product",
        routine_id=None,
        channels={},
        store_root=tmp_path / "memory-store",
    )
    spec = JobSpec(
        schema_version=5,
        job_id="memory-job",
        config_path=str(config_path.resolve()),
        config_revision="cfg-1",
        team_key="test",
        team_root=str(team_dir.resolve()),
        agent_name="product",
        workspace_root=str(team_dir.resolve()),
        trigger="manual_prompt",
        integration_name="script",
        integration_config={},
        blueprint=BlueprintRef(
            key="memory-blueprint",
            source_digest="digest-1",
            integration="script",
            projector_version="v1",
            cache_path=str((tmp_path / "compiled-agents" / "script" / "v1" / "digest-1" / "entry.py").resolve()),
        ),
        routine_id="daily-review",
        skill=None,
        skill_arguments=(),
        task_input="run",
        runtime_policy=RuntimePolicySnapshot(
            timeout=1800,
            mode="unrestricted",
        ),
        memory=MemoryBinding(
            selector={"scope": "agent"},
            canonical_json=memory_binding.canonical_json,
            memory_hash=memory_binding.memory_hash,
            path=str(memory_binding.directory.resolve()),
        ),
        trigger_context=None,
        prompt_source={
            "type": "blueprint_prompt",
            "scope": "blueprint",
            "name": "daily-review",
            "source_path": ".agents/prompts/daily-review.prompt.md",
            "source_digest": "digest-1",
        },
        timeout_override=None,
        created_at="2026-07-15T00:00:00+00:00",
    )
    store_root = tmp_path / "memory-store"
    path = JobStore(store_root).path(spec.team_key, spec.job_id)
    write_job(path, replace(JobRecord.from_spec(spec), status="running", worker_pid=999999))

    store = MemoryStore(store_root)
    resolved = resolve_memory_selector(
        MemorySelector(scope="agent"),
        job_id=spec.job_id,
        team_key="test",
        agent_name="product",
        routine_id=None,
        channels={},
        store_root=store_root,
    )
    seeded = store.ensure(resolved)
    store.try_save(resolved, seeded.revision, {"memory.md": b"old\n"})
    stage = store.stage(resolved, job_id=spec.job_id)
    (stage.directory / "memory.md").write_bytes(b"new\n")
    prepared = prepare_publication(stage, job_store=JobStore(store_root).team_root("test"))
    try:
        apply_publication(prepared, crash_at="published")
    except Exception:
        pass

    monkeypatch.setattr("flowgency.jobs.reconciliation.worker_alive", lambda pid: False)

    result = reconcile_for_test(
        {"test": {"team_root": str(team_dir)}},
        tmp_path,
        memory_store_root=store_root,
    )

    assert result.failed == 0


def test_reconcile_retries_confirmed_pending_ticket_cleanup_on_original_binding_only(
    tmp_path,
    raw_config,
    monkeypatch,
):
    env = make_ticket_job_environment(tmp_path, raw_config, monkeypatch)
    ticket = env.create_assigned("builder")
    authority = env.running_job("builder", "run-a")

    with env.broker_session(authority) as (context, client):
        env.generation = context.session_id
        started = client.call(
            "start_work",
            {
                "version": env.read(ticket.ref).version.model_dump(mode="json"),
                "operation_id": "start-reconcile-retry",
            },
        )

    assert started["ok"] is True

    record = env.job_store.read(authority)
    write_job(
        authority.path,
        replace(
            record,
            status="complete",
            completed_at="2026-09-08T00:10:00+00:00",
            result_metadata={
                "ticket_cleanup": {
                    "status": "pending",
                    "job_id": record.spec.job_id,
                    "generation": env.generation,
                    "confirmed": True,
                    "reason": "exited",
                    "cleared": [],
                    "pending_cleanup": [ticket.ref.model_dump(mode="json")],
                }
            },
        ),
    )

    env.set_storage_root(env.root_b)
    current_binding = resolve_workflow_binding(
        env.store.load(),
        env.team_id,
        env.workflow_id,
    ).storage
    current_provider = env.current_provider()
    current_ref = TicketRef.from_binding(current_binding, ticket.ref.ticket_id)
    shadow = ticket.record.with_ref(current_ref).model_copy(
        update={
            "number": 0,
            "revision": 1,
            "pending_run": TicketRunReservation(
                job_id="shadow-job",
                request_id="shadow-request",
                assignee="builder",
                assignment_event_id="shadow-assignment",
            ),
            "active_run": ActiveTicketRun(
                job_id="shadow-active",
                session_id="shadow-session",
                started_at=SEED_TIME,
            ),
            "events": (
                TicketEvent(kind="opened", actor="system", summary="Shadow ticket"),
            ),
            "receipts": (),
            "created_at": SEED_TIME,
            "updated_at": SEED_TIME,
        }
    )
    current_provider.create(
        shadow,
        TicketOperation(operation_id="seed-shadow", request_digest="seed-shadow"),
    )
    shadow_path = current_provider._ticket_path(current_ref)
    shadow_before = shadow_path.read_text(encoding="utf-8")
    team_root = str(env.store.load().config.teams[env.team_id].path)
    Path(record.spec.config_path).unlink()

    result = reconcile_jobs(
        {env.team_id: {"team_root": team_root}},
        memory_store_root=env.job_store.memory_store,
    )

    assert result.failed == 0
    assert env.provider.read(ticket.ref).active_run is None
    assert shadow_path.read_text(encoding="utf-8") == shadow_before
    updated = read_job(authority.path)
    assert updated.result_metadata is not None
    assert updated.result_metadata["ticket_cleanup"]["status"] == "cleared"


def test_reconcile_retries_confirmed_ticket_cleanup_error_without_enumerated_pending_refs(
    tmp_path,
    raw_config,
    monkeypatch,
):
    env = make_ticket_job_environment(tmp_path, raw_config, monkeypatch)
    ticket = env.create_assigned("builder")
    authority = env.running_job("builder", "run-a")

    with env.broker_session(authority) as (context, client):
        env.generation = context.session_id
        started = client.call(
            "start_work",
            {
                "version": env.read(ticket.ref).version.model_dump(mode="json"),
                "operation_id": "start-reconcile-cleanup-error",
            },
        )

    assert started["ok"] is True

    record = env.job_store.read(authority)
    write_job(
        authority.path,
        replace(
            record,
            status="complete",
            completed_at="2026-09-08T00:10:00+00:00",
            result_metadata={
                "ticket_cleanup": {
                    "status": "error",
                    "job_id": record.spec.job_id,
                    "generation": env.generation,
                    "confirmed": True,
                    "reason": "exited",
                    "cleared": [],
                    "pending_cleanup": [],
                    "requires_retry": True,
                    "error": {
                        "phase": "cleanup",
                        "kind": "RuntimeError",
                        "message": "Ticket cleanup failed",
                    },
                }
            },
        ),
    )

    result = reconcile_jobs(
        {env.team_id: {"team_root": str(env.store.load().config.teams[env.team_id].path)}},
        memory_store_root=env.job_store.memory_store,
    )

    assert result.failed == 0
    assert env.provider.read(ticket.ref).active_run is None
    updated = read_job(authority.path)
    assert updated.result_metadata is not None
    assert updated.result_metadata["ticket_cleanup"]["status"] == "cleared"


def test_reconcile_consumes_cancelled_pending_reservation_sidecars(
    tmp_path,
    raw_config,
    monkeypatch,
):
    env = make_ticket_job_environment(tmp_path, raw_config, monkeypatch)
    ticket = env.create_assigned("builder")
    handle = env.coordinator.submit(env.user, ticket.version, "run-request")

    cancelled = replace(read_job(handle.path), status="cancelled")
    write_job(handle.path, cancelled)

    result = reconcile_jobs(
        {
            env.team_id: {
                "team_root": str(env.store.load().config.teams[env.team_id].path),
                "config_path": str(env.store.path),
            }
        },
        memory_store_root=env.job_store.memory_store,
    )

    assert result.failed == 0
    current = env.read(ticket.ref).record
    assert current.assignee == "builder"
    assert current.pending_run is None
    assert current.active_run is None
    assert env.jobs.read(handle).status == "cancelled"


def test_reconcile_consumes_recovery_pending_reservation_sidecars_without_touching_shadow_destination(
    tmp_path,
    raw_config,
    monkeypatch,
):
    env = make_ticket_job_environment(tmp_path, raw_config, monkeypatch)
    ticket = env.create_assigned("builder")
    backup = env.tmp_path / "root-a-backup"
    real_submit = env.coordinator.submitter

    def unavailable_after_create(request):
        handle = real_submit(request)
        env.root_a.rename(backup)
        return handle

    env.coordinator.submitter = unavailable_after_create

    handle = env.coordinator.submit(env.user, ticket.version, "run-request")
    reservation = env.coordinator._read_reservation(env.team_id, ticket.ref, "run-request")

    assert reservation is not None
    assert reservation.status == "recovery-pending"

    env.set_storage_root(env.root_b)
    current_binding = resolve_workflow_binding(
        env.store.load(),
        env.team_id,
        env.workflow_id,
    ).storage
    current_provider = env.current_provider()
    current_ref = TicketRef.from_binding(current_binding, ticket.ref.ticket_id)
    shadow = ticket.record.with_ref(current_ref).model_copy(
        update={
            "number": 0,
            "revision": 1,
            "pending_run": TicketRunReservation(
                job_id="shadow-job",
                request_id="shadow-request",
                assignee="builder",
                assignment_event_id="shadow-assignment",
            ),
            "active_run": ActiveTicketRun(
                job_id="shadow-active",
                session_id="shadow-session",
                started_at=SEED_TIME,
            ),
            "events": (
                TicketEvent(kind="opened", actor="system", summary="Shadow ticket"),
            ),
            "receipts": (),
            "created_at": SEED_TIME,
            "updated_at": SEED_TIME,
        }
    )
    current_provider.create(
        shadow,
        TicketOperation(operation_id="seed-shadow", request_digest="seed-shadow"),
    )
    shadow_path = current_provider._ticket_path(current_ref)
    shadow_before = shadow_path.read_text(encoding="utf-8")

    backup.rename(env.root_a)

    result = reconcile_jobs(
        {
            env.team_id: {
                "team_root": str(env.store.load().config.teams[env.team_id].path),
                "config_path": str(env.store.path),
            }
        },
        memory_store_root=env.job_store.memory_store,
    )

    assert result.failed == 0
    assert env.provider.read(ticket.ref).pending_run is None
    assert env.jobs.read(handle).status == "cancelled"
    assert shadow_path.read_text(encoding="utf-8") == shadow_before


def test_reconcile_dead_worker_records_pending_ticket_cleanup_and_revokes_sessions(
    tmp_path,
    raw_config,
    monkeypatch,
):
    from flowgency.tickets.models import TicketOperation

    env = make_ticket_job_environment(tmp_path, raw_config, monkeypatch)
    ticket = env.create_assigned("builder")
    authority = env.running_job("builder", "run-a")
    grant = env.access_registry.open(authority)
    binding = env.service.list_workflows(grant.context)[0]
    env.access_registry.register_target(grant.context, binding, ticket.ref)
    env.service.start_work(
        grant.context,
        env.read(ticket.ref).version,
        TicketOperation(operation_id="start-reconcile-dead", request_digest="start-reconcile-dead"),
    )
    access_path = env.access_registry._access_path(env.team_id, authority.job_id)

    monkeypatch.setattr("flowgency.jobs.reconciliation.worker_alive", lambda pid: False)

    result = reconcile_jobs(
        {
            env.team_id: {
                "team_root": str(env.store.load().config.teams[env.team_id].path),
                "config_path": str(env.store.path),
            }
        },
        memory_store_root=env.job_store.memory_store,
    )

    assert result.failed == 1
    current = env.read(ticket.ref).record
    assert current.active_run is not None
    record = read_job(authority.path)
    assert record.status == "failed"
    assert record.result_metadata is not None
    cleanup = record.result_metadata["ticket_cleanup"]
    assert cleanup["status"] == "pending"
    assert cleanup["confirmed"] is False
    assert cleanup["reason"] == "worker-missing"
    assert cleanup["pending_cleanup"] == [ticket.ref.model_dump(mode="json")]
    payload = json.loads(access_path.read_text(encoding="utf-8"))
    assert payload["sessions"] == {}
    assert payload["original_targets"][0]["ref"]["ticket_id"] == ticket.ref.ticket_id


def test_running_decision_without_job_id_is_not_failed(tmp_path):
    team_dir = tmp_path / "team"
    decision = team_dir / "decisions" / "orphan.md"
    decision.parent.mkdir(parents=True)
    decision.write_text("---\nexecution_status: running\n---\n")
    reconcile_for_test({"test": {"team_root": str(team_dir)}}, tmp_path)
    assert "execution_status: running" in decision.read_text()


def test_reconcile_ignores_malformed_job_and_logs_warning(tmp_path, caplog):
    jobs = _job_store(tmp_path).team_root("test")
    jobs.mkdir(parents=True)
    (jobs / "broken.yaml").write_text("spec: [")

    result = reconcile_for_test(
        {"test": {"team_root": str(tmp_path / "team")}},
        tmp_path,
    )

    assert result.failed == 0
    assert result.left_running == 0
    assert "broken.yaml" in caplog.text


def test_reconcile_invokes_global_recovery_once_with_no_job_records(
    tmp_path,
    monkeypatch,
):
    from flowgency.memory.recovery import RecoveryResult

    group_a = tmp_path / "a"
    group_b = tmp_path / "b"
    calls = []

    def observe(store_root, job_stores):
        calls.append((Path(store_root), dict(job_stores)))
        return RecoveryResult()

    monkeypatch.setattr("flowgency.jobs.reconciliation.recover_publications", observe)

    reconcile_jobs(
        {
            "a": {"team_root": str(group_a)},
            "b": {"team_root": str(group_b)},
        },
        memory_store_root=tmp_path / "memory-store",
    )

    assert calls == [
        (
            tmp_path / "memory-store",
            {
                "a": {
                    "job_store": (tmp_path / "memory-store" / ".jobs" / "a").resolve(),
                    "team_root": str(group_a),
                },
                "b": {
                    "job_store": (tmp_path / "memory-store" / ".jobs" / "b").resolve(),
                    "team_root": str(group_b),
                },
            },
        )
    ]


def test_reconcile_does_not_fail_recovery_blocked_dead_job(
    tmp_path,
    monkeypatch,
    caplog,
):
    from flowgency.memory.recovery import RecoveryResult

    team_dir, _, path = running_decision_job(tmp_path)
    monkeypatch.setattr(
        "flowgency.jobs.reconciliation.recover_publications",
        lambda *args: RecoveryResult(
            blocked_job_ids=("decision-job",),
            errors=("persistent recovery barrier",),
        ),
    )
    monkeypatch.setattr("flowgency.jobs.reconciliation.worker_alive", lambda pid: False)

    result = reconcile_for_test({"test": {"team_root": str(team_dir)}}, tmp_path)

    assert result.failed == 0
    assert read_job(path).status == "running"
    assert "manual intervention" in caplog.text


def test_worker_alive_rejects_missing_and_invalid_pids():
    assert worker_alive(None) is None
    assert worker_alive(0) is None
    assert worker_alive(-1) is None


