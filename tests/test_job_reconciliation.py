from dataclasses import replace
from pathlib import Path

import yaml

from agency.blueprints.cache import active_pins, pin_artifact
from agency.jobs.authority import JobStore
from agency.jobs.models import BlueprintRef, JobRecord, JobSpec, MemoryBinding, RuntimePolicySnapshot
from agency.jobs.reconciliation import reconcile_jobs, worker_alive
from agency.memory.recovery import recover_publications
from agency.jobs.store import job_path, read_job, write_job


def _job_store(tmp_path: Path) -> JobStore:
    return JobStore(tmp_path / "memory")


def running_decision_job(
    tmp_path: Path,
    pid: int = 999999,
    *,
    group_key: str = "test",
    memory_store_root: Path | None = None,
):
    group = tmp_path / "group"
    decision = group / "decisions" / "change.md"
    decision.parent.mkdir(parents=True)
    config_path = tmp_path / "config.yaml"
    config_path.write_text("schema_version: 3\ngroups: {}\n", encoding="utf-8")
    spec = JobSpec(
        schema_version=3,
        job_id="decision-job",
        config_path=str(config_path.resolve()),
        config_revision="cfg-1",
        group_key=group_key,
        group_root=str(group.resolve()),
        agent_name="product",
        workspace_root=str(group.resolve()),
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
            sandbox_mode="unrestricted",
            sandbox_roots=(),
            tool_mode="all",
            tool_names=(),
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
    path = JobStore(memory_store_root or (tmp_path / "memory")).path(spec.group_key, spec.job_id)
    write_job(
        path,
        replace(JobRecord.from_spec(spec), status="running", worker_pid=pid),
    )
    return group, decision, path


def reconcile_for_test(groups, tmp_path, *, memory_store_root=None):
    return reconcile_jobs(
        groups,
        memory_store_root=memory_store_root or (tmp_path / "memory"),
    )


def test_reconcile_leaves_live_worker_running(tmp_path, monkeypatch):
    group, decision, path = running_decision_job(tmp_path)
    monkeypatch.setattr("agency.jobs.reconciliation.worker_alive", lambda pid: True)
    result = reconcile_for_test({"test": {"group_root": str(group)}}, tmp_path)
    assert result.left_running == 1
    assert read_job(path).status == "running"
    assert "execution_status: running" in decision.read_text()


def test_reconcile_marks_confirmed_dead_worker_failed(tmp_path, monkeypatch):
    group, decision, path = running_decision_job(tmp_path)
    monkeypatch.setattr("agency.jobs.reconciliation.worker_alive", lambda pid: False)
    result = reconcile_for_test({"test": {"group_root": str(group)}}, tmp_path)
    assert result.failed == 1
    record = read_job(path)
    assert record.status == "failed"
    assert record.completed_at is not None
    assert record.execution_summary == "Worker process (PID 999999) was not found."
    assert "execution_status: failed" in decision.read_text()


def test_reconcile_releases_pin_for_dead_waiting_worker(tmp_path, monkeypatch):
    group, _, path = running_decision_job(tmp_path)
    record = read_job(path)
    artifact = record.spec.blueprint.to_artifact()
    artifact.runtime_path.mkdir(parents=True, exist_ok=True)
    (artifact.runtime_path / "AGENTS.md").write_text("# Agent\n", encoding="utf-8")
    pin_artifact(record.spec.blueprint.cache_root, artifact.ref, record.spec.job_id)
    write_job(
        path,
        replace(record, status="waiting_for_memory", worker_pid=999999),
    )

    monkeypatch.setattr("agency.jobs.reconciliation.worker_alive", lambda pid: False)

    result = reconcile_for_test({"test": {"group_root": str(group)}}, tmp_path)

    assert result.failed == 1
    assert active_pins(record.spec.blueprint.cache_root, artifact.ref) == ()


def test_reconcile_releases_pin_for_dead_running_worker_but_keeps_live_pin(
    tmp_path,
    monkeypatch,
):
    shared_store = tmp_path / "memory"
    dead_group, _, dead_path = running_decision_job(
        tmp_path / "dead",
        group_key="dead",
        memory_store_root=shared_store,
    )
    live_group, _, live_path = running_decision_job(
        tmp_path / "live",
        pid=123456,
        group_key="live",
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
        "agency.jobs.reconciliation.worker_alive",
        lambda pid: False if pid == 999999 else True,
    )

    result = reconcile_for_test(
        {
            "dead": {"group_root": str(dead_group)},
            "live": {"group_root": str(live_group)},
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
    group, decision, path = running_decision_job(tmp_path)
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

    reconcile_for_test({"test": {"group_root": str(group)}}, tmp_path)

    decision_text = decision.read_text()
    assert "execution_status: failed" in decision_text
    assert "Agent timed out after 300 seconds." in decision_text


def test_reconcile_projects_complete_job_with_changed_files(tmp_path):
    """A terminal ``complete`` job carrying non-empty ``changed_files`` must
    project both its status and the captured files onto a stale ``running``
    decision. This is the exact behaviour the cross-tool capture advertises and
    was previously only asserted for a failed, empty-changes job."""
    group, decision, path = running_decision_job(tmp_path)
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

    reconcile_for_test({"test": {"group_root": str(group)}}, tmp_path)

    metadata = yaml.safe_load(decision.read_text().split("---")[1])
    assert metadata["execution_status"] == "complete"
    assert metadata["changed_files"] == changed_files
    assert metadata["execution_summary"] == (
        "Agent completed execution; captured 2 changed files."
    )
    assert read_job(path).status == "complete"


def test_reconcile_leaves_uncertain_worker_running(tmp_path, monkeypatch):
    group, _, path = running_decision_job(tmp_path)
    monkeypatch.setattr("agency.jobs.reconciliation.worker_alive", lambda pid: None)
    result = reconcile_for_test({"test": {"group_root": str(group)}}, tmp_path)
    assert result.left_running == 1
    assert read_job(path).status == "running"


def test_reconcile_fails_dead_waiting_worker(tmp_path, monkeypatch):
    group, _, path = running_decision_job(tmp_path)
    record = read_job(path)
    write_job(
        path,
        replace(record, status="waiting_for_memory", worker_pid=999999),
    )

    monkeypatch.setattr("agency.jobs.reconciliation.worker_alive", lambda pid: False)

    result = reconcile_for_test({"test": {"group_root": str(group)}}, tmp_path)

    assert result.failed == 1
    reconciled = read_job(path)
    assert reconciled.status == "failed"
    assert "999999" in (reconciled.execution_summary or "")


def test_reconcile_recovers_published_journal_before_failing_dead_worker(tmp_path, monkeypatch):
    from agency.configuration.models import MemorySelector
    from agency.jobs.models import JobSpec
    from agency.memory import MemoryStore, resolve_memory_selector
    from agency.memory.publication import apply_publication, prepare_publication

    group = tmp_path / "group"
    config_path = tmp_path / "config.yaml"
    config_path.write_text("schema_version: 3\ngroups: {}\n", encoding="utf-8")
    memory_binding = resolve_memory_selector(
        MemorySelector(scope="agent"),
        job_id="placeholder",
        group_key="test",
        agent_name="product",
        routine_id=None,
        channels={},
        store_root=tmp_path / "memory-store",
    )
    spec = JobSpec(
        schema_version=3,
        job_id="memory-job",
        config_path=str(config_path.resolve()),
        config_revision="cfg-1",
        group_key="test",
        group_root=str(group.resolve()),
        agent_name="product",
        workspace_root=str(group.resolve()),
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
        skill="daily-review",
        skill_arguments=(),
        task_input="run",
        runtime_policy=RuntimePolicySnapshot(
            timeout=1800,
            sandbox_mode="unrestricted",
            sandbox_roots=(),
            tool_mode="all",
            tool_names=(),
        ),
        memory=MemoryBinding(
            selector={"scope": "agent"},
            canonical_json=memory_binding.canonical_json,
            memory_hash=memory_binding.memory_hash,
            path=str(memory_binding.directory.resolve()),
        ),
        trigger_context=None,
        prompt_source={"type": "routine", "routine_id": "daily-review"},
        timeout_override=None,
        created_at="2026-07-15T00:00:00+00:00",
    )
    store_root = tmp_path / "memory-store"
    path = JobStore(store_root).path(spec.group_key, spec.job_id)
    write_job(path, replace(JobRecord.from_spec(spec), status="running", worker_pid=999999))

    store = MemoryStore(store_root)
    resolved = resolve_memory_selector(
        MemorySelector(scope="agent"),
        job_id=spec.job_id,
        group_key="test",
        agent_name="product",
        routine_id=None,
        channels={},
        store_root=store_root,
    )
    seeded = store.ensure(resolved)
    store.try_save(resolved, seeded.revision, {"memory.md": b"old\n"})
    stage = store.stage(resolved, job_id=spec.job_id)
    (stage.directory / "memory.md").write_bytes(b"new\n")
    prepared = prepare_publication(stage, job_store=JobStore(store_root).group_root("test"))
    try:
        apply_publication(prepared, crash_at="published")
    except Exception:
        pass

    monkeypatch.setattr("agency.jobs.reconciliation.worker_alive", lambda pid: False)

    result = reconcile_for_test(
        {"test": {"group_root": str(group)}},
        tmp_path,
        memory_store_root=store_root,
    )

    assert result.failed == 0
    assert read_job(path).status == "complete"
    assert read_job(path).memory_publication is not None


def test_running_decision_without_job_id_is_not_failed(tmp_path):
    group = tmp_path / "group"
    decision = group / "decisions" / "orphan.md"
    decision.parent.mkdir(parents=True)
    decision.write_text("---\nexecution_status: running\n---\n")
    reconcile_for_test({"test": {"group_root": str(group)}}, tmp_path)
    assert "execution_status: running" in decision.read_text()


def test_reconcile_ignores_malformed_job_and_logs_warning(tmp_path, caplog):
    jobs = _job_store(tmp_path).group_root("test")
    jobs.mkdir(parents=True)
    (jobs / "broken.yaml").write_text("spec: [")

    result = reconcile_for_test(
        {"test": {"group_root": str(tmp_path / "group")}},
        tmp_path,
    )

    assert result.failed == 0
    assert result.left_running == 0
    assert "broken.yaml" in caplog.text


def test_reconcile_invokes_global_recovery_once_with_no_job_records(
    tmp_path,
    monkeypatch,
):
    from agency.memory.recovery import RecoveryResult

    group_a = tmp_path / "a"
    group_b = tmp_path / "b"
    calls = []

    def observe(store_root, job_stores):
        calls.append((Path(store_root), dict(job_stores)))
        return RecoveryResult()

    monkeypatch.setattr("agency.jobs.reconciliation.recover_publications", observe)

    reconcile_jobs(
        {
            "a": {"group_root": str(group_a)},
            "b": {"group_root": str(group_b)},
        },
        memory_store_root=tmp_path / "memory-store",
    )

    assert calls == [
        (
            tmp_path / "memory-store",
            {
                "a": {
                    "job_store": (tmp_path / "memory-store" / ".jobs" / "a").resolve(),
                    "group_root": str(group_a),
                },
                "b": {
                    "job_store": (tmp_path / "memory-store" / ".jobs" / "b").resolve(),
                    "group_root": str(group_b),
                },
            },
        )
    ]


def test_reconcile_does_not_fail_recovery_blocked_dead_job(
    tmp_path,
    monkeypatch,
    caplog,
):
    from agency.memory.recovery import RecoveryResult

    group, _, path = running_decision_job(tmp_path)
    monkeypatch.setattr(
        "agency.jobs.reconciliation.recover_publications",
        lambda *args: RecoveryResult(
            blocked_job_ids=("decision-job",),
            errors=("persistent recovery barrier",),
        ),
    )
    monkeypatch.setattr("agency.jobs.reconciliation.worker_alive", lambda pid: False)

    result = reconcile_for_test({"test": {"group_root": str(group)}}, tmp_path)

    assert result.failed == 0
    assert read_job(path).status == "running"
    assert "manual intervention" in caplog.text


def test_worker_alive_rejects_missing_and_invalid_pids():
    assert worker_alive(None) is None
    assert worker_alive(0) is None
    assert worker_alive(-1) is None
