"""Tests for the global job queue drain."""

from dataclasses import replace as dc_replace
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from agency.configuration.models import parse_config
from agency.jobs.authority import JobStore
from agency.jobs.launcher import LaunchResult
from agency.jobs.models import (
    BlueprintRef,
    JobRecord,
    JobSpec,
    MemoryBinding,
    RuntimePolicySnapshot,
)
from agency.jobs.queue import drain, has_drainer, queue_snapshot
from agency.jobs.store import read_job, write_job
from agency.jobs.worker import main as worker_main


def _make_spec(tmp_path: Path, job_id: str) -> JobSpec:
    config_path = tmp_path / "config.yaml"
    if not config_path.exists():
        config_path.write_text("schema_version: 4\ngroups: {}\n", encoding="utf-8")
    return JobSpec(
        schema_version=4,
        job_id=job_id,
        config_path=str(config_path.resolve()),
        config_revision="cfg-1",
        group_key="newsletter",
        group_root=str((tmp_path / "group").resolve()),
        agent_name="product",
        workspace_root=str((tmp_path / "workspace").resolve()),
        trigger="manual_prompt",
        integration_name="copilot",
        integration_config={"model": "gpt-5.4"},
        blueprint=BlueprintRef(
            key="writer",
            source_digest="digest-1",
            integration="copilot",
            projector_version="v1",
            cache_path=str(
                (tmp_path / "cache" / "copilot" / "v1" / "digest-1").resolve()
            ),
        ),
        routine_id="routine-1",
        skill=None,
        skill_arguments=(),
        task_input="# Routine\n",
        runtime_policy=RuntimePolicySnapshot(
            timeout=1800,
            sandbox_mode="restricted",
            sandbox_roots=(str(tmp_path.resolve()),),
            tool_mode="allowlist",
            tool_names=("shell", "write"),
        ),
        memory=MemoryBinding(
            selector={"scope": "run", "version": 1, "job": "placeholder"},
            canonical_json='{"job":"placeholder","scope":"run","version":1}',
            memory_hash="memory-hash-1",
            path=str((tmp_path / "memory" / "memory-hash-1").resolve()),
        ),
        trigger_context={"source": "test"},
        prompt_source={
            "type": "blueprint_prompt",
            "scope": "blueprint",
            "name": "daily-review",
            "source_path": ".agents/prompts/daily-review.prompt.md",
            "source_digest": "digest-1",
        },
        timeout_override=None,
        created_at="2026-07-15T00:00:00+00:00",
        private_prompts=(),
    )


class _RecordingLauncher:
    def __init__(self, worker_pid: int | None = os.getpid()):
        self.launched: list[str] = []
        self.fail_on: str | None = None
        self.worker_pid = worker_pid

    def launch(self, reference):
        if self.fail_on is not None and reference.job_id == self.fail_on:
            raise OSError(f"simulated launch failure for {reference.job_id}")
        self.launched.append(reference.job_id)
        return LaunchResult(worker_pid=self.worker_pid)


class _QueueFixture:
    def __init__(self, config, config_path, memory_store, store, launcher, tmp_path):
        self.config = config
        self.config_path = config_path
        self.memory_store = memory_store
        self._store = store
        self.launcher = launcher
        self._tmp_path = tmp_path

    def enqueue(self, job_id, *, due_at=None, status="queued", worker_pid=None):
        spec = _make_spec(self._tmp_path, job_id)
        record = JobRecord.from_spec(spec, due_at=due_at)
        if status != "queued" or worker_pid is not None:
            record = dc_replace(record, status=status, worker_pid=worker_pid)
        path = self._store.path("newsletter", job_id)
        write_job(path, record)

    def status(self, job_id):
        path = self._store.path("newsletter", job_id)
        return read_job(path).status

    def worker_argv(self, job_id):
        path = self._store.path("newsletter", job_id)
        record = read_job(path)
        ref = self._store.reference("newsletter", job_id, record.authority_digest)
        return ref.worker_args()


def _make_queue_fixture(tmp_path, *, pool: int = 3) -> _QueueFixture:
    memory_store = tmp_path / "memory"
    config_path = tmp_path / "config.yaml"
    workspace_path = tmp_path / "workspace"
    group_path = tmp_path / "groups" / "newsletter"
    workspace_path.mkdir(parents=True, exist_ok=True)

    raw = {
        "schema_version": 4,
        "agency": {
            "title": "Agency",
            "default_group": "newsletter",
            "ai_backend": "copilot",
            "agent_library": str(tmp_path / "agent-library"),
            "compilation_cache": str(tmp_path / "compiled-agents"),
            "memory_store": str(memory_store),
            "prompt_store": str(tmp_path / "prompts"),
            "jobs": {"pool": pool},
        },
        "groups": {
            "newsletter": {
                "name": "Newsletter",
                "workspace_path": str(workspace_path),
                "path": str(group_path),
                "default_integration": "copilot",
                "agents": [],
            },
        },
    }
    config_path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    config = parse_config(raw, config_path)
    store = JobStore(memory_store)
    launcher = _RecordingLauncher()
    return _QueueFixture(config, config_path, memory_store, store, launcher, tmp_path)


@pytest.fixture
def queue_fixture(tmp_path):
    return _make_queue_fixture(tmp_path)


@pytest.fixture
def queue_fixture_pool1(tmp_path):
    return _make_queue_fixture(tmp_path, pool=1)


def test_queue_orders_waiting_jobs_by_due_time(queue_fixture):
    queue_fixture.enqueue("late", due_at="2026-07-29T18:00:00")
    queue_fixture.enqueue("early", due_at="2026-07-29T08:00:00")
    view = queue_snapshot(queue_fixture.config, memory_store=queue_fixture.memory_store)
    assert [entry.record.spec.job_id for entry in view.waiting] == ["early", "late"]


def test_ties_break_on_job_id(queue_fixture):
    queue_fixture.enqueue("b", due_at="2026-07-29T08:00:00")
    queue_fixture.enqueue("a", due_at="2026-07-29T08:00:00")
    view = queue_snapshot(queue_fixture.config, memory_store=queue_fixture.memory_store)
    assert [entry.record.spec.job_id for entry in view.waiting] == ["a", "b"]


def test_drain_starts_up_to_the_pool_and_no_further(queue_fixture):
    for index in range(5):
        queue_fixture.enqueue(f"job{index}", due_at=f"2026-07-29T0{index}:00:00")
    started = drain(
        queue_fixture.config,
        memory_store=queue_fixture.memory_store,
        launcher=queue_fixture.launcher,
    )
    assert started == queue_fixture.config.agency.jobs.pool


def test_drain_claims_what_it_starts_so_a_second_drain_is_a_no_op(queue_fixture):
    queue_fixture.enqueue("only", due_at="2026-07-29T08:00:00")
    drain(queue_fixture.config, memory_store=queue_fixture.memory_store,
          launcher=queue_fixture.launcher)
    started_again = drain(
        queue_fixture.config,
        memory_store=queue_fixture.memory_store,
        launcher=queue_fixture.launcher,
    )
    assert started_again == 0
    assert queue_fixture.launcher.launched == ["only"]


def test_a_launcher_that_reports_no_pid_is_not_relaunched(queue_fixture):
    """systemd-run owns the process and reports no pid; the claim must still stick."""
    queue_fixture.launcher.worker_pid = None
    queue_fixture.enqueue("only", due_at="2026-07-29T08:00:00")
    drain(queue_fixture.config, memory_store=queue_fixture.memory_store,
          launcher=queue_fixture.launcher)
    started_again = drain(
        queue_fixture.config,
        memory_store=queue_fixture.memory_store,
        launcher=queue_fixture.launcher,
    )
    assert started_again == 0
    assert queue_fixture.launcher.launched == ["only"]
    assert queue_fixture.status("only") == "queued"


def test_a_pidless_launch_holds_a_pool_slot(queue_fixture_pool1):
    """Without a pid the launch stamp is the only thing bounding the pool."""
    queue_fixture_pool1.launcher.worker_pid = None
    queue_fixture_pool1.enqueue("first", due_at="2026-07-29T08:00:00")
    queue_fixture_pool1.enqueue("second", due_at="2026-07-29T09:00:00")
    drain(
        queue_fixture_pool1.config,
        memory_store=queue_fixture_pool1.memory_store,
        launcher=queue_fixture_pool1.launcher,
    )
    view = queue_snapshot(
        queue_fixture_pool1.config, memory_store=queue_fixture_pool1.memory_store
    )
    assert queue_fixture_pool1.launcher.launched == ["first"]
    assert view.running == 1


def test_a_ghost_running_record_does_not_hold_a_slot_forever(
    queue_fixture_pool1, monkeypatch
):
    # pool=1 means the ghost's slot is the only one; real job can only launch if
    # reconcile actually frees it. Patch the binding drain calls directly.
    monkeypatch.setattr("agency.jobs.reconciliation.worker_alive", lambda pid: False)
    queue_fixture_pool1.enqueue("ghost", status="running", worker_pid=999999)
    queue_fixture_pool1.enqueue("real", due_at="2026-07-29T08:00:00")
    drain(
        queue_fixture_pool1.config,
        memory_store=queue_fixture_pool1.memory_store,
        launcher=queue_fixture_pool1.launcher,
    )
    # Ghost must be "failed" to prove reconcile freed its slot (pool=1 ensures the
    # real job can only launch once that slot is free).
    assert queue_fixture_pool1.status("ghost") == "failed"
    assert "real" in queue_fixture_pool1.launcher.launched


def test_a_failing_launch_marks_the_job_and_the_drain_continues(queue_fixture):
    queue_fixture.launcher.fail_on = "first"
    queue_fixture.enqueue("first", due_at="2026-07-29T08:00:00")
    queue_fixture.enqueue("second", due_at="2026-07-29T09:00:00")
    drain(queue_fixture.config, memory_store=queue_fixture.memory_store,
          launcher=queue_fixture.launcher)
    assert queue_fixture.status("first") == "failed"
    assert "second" in queue_fixture.launcher.launched


def test_a_live_worker_counts_as_a_drainer(queue_fixture, monkeypatch):
    monkeypatch.setattr("agency.jobs.store.worker_alive", lambda pid: True)
    queue_fixture.enqueue("busy", status="running", worker_pid=4321)
    assert has_drainer(
        queue_fixture.config,
        memory_store=queue_fixture.memory_store,
        config_path=queue_fixture.config_path,
    ) is True


def test_with_nothing_alive_the_installed_timer_decides(queue_fixture, monkeypatch):
    monkeypatch.setattr(
        "agency.jobs.queue.get_timer_status",
        lambda path, interval: {"installed": False, "enabled": False},
    )
    assert has_drainer(
        queue_fixture.config,
        memory_store=queue_fixture.memory_store,
        config_path=queue_fixture.config_path,
    ) is False


def test_a_finishing_worker_starts_the_next_waiting_job(queue_fixture, monkeypatch):
    calls = []
    monkeypatch.setattr("agency.jobs.worker.drain", lambda *a, **k: calls.append(1))
    monkeypatch.setattr("agency.jobs.worker.execute_job", lambda ref: SimpleNamespace(status="complete"))
    queue_fixture.enqueue("only", due_at="2026-07-29T08:00:00")
    worker_main(queue_fixture.worker_argv("only"))
    assert calls == [1]
