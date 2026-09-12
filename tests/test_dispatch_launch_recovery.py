"""A scheduled occurrence whose deferred launch fails must stay recoverable."""

import os
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from flowgency.dispatch.run import _due_occurrence, lost_occurrences, run_dispatch_cycle
from flowgency.dispatch.schedule import at_marker_path, every_marker_path
from flowgency.jobs import JobRequest, submit_job_request
from flowgency.jobs.authority import JobStore
from flowgency.jobs.launcher import LaunchResult
from flowgency.jobs.store import cancel_job, read_job
from flowgency.jobs.execution import execute_job


class _FlakyLauncher:
    """A launcher whose next ``failures`` spawns fail, as a busy machine's would."""

    def __init__(self):
        self.launched: list[str] = []
        self.failures = 0

    def launch(self, reference):
        if self.failures > 0:
            self.failures -= 1
            raise OSError("no process handles left")
        self.launched.append(reference.job_id)
        return LaunchResult(worker_pid=os.getpid())


def _write_blueprint(root: Path) -> None:
    blueprint = root / "builder-blueprint"
    prompt_dir = blueprint / ".agents" / "prompts"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    (blueprint / "AGENTS.md").write_text("# Builder\n", encoding="utf-8")
    (prompt_dir / "daily-review.prompt.md").write_text(
        "---\nname: daily-review\ndescription: Review daily work.\n---\n\nRun it.\n",
        encoding="utf-8",
    )


def _write_config(tmp_path: Path, *, schedule: str) -> Path:
    workspace = tmp_path / "workspaces" / "newsletter"
    (workspace / "repo").mkdir(parents=True, exist_ok=True)
    (tmp_path / "agents" / "newsletter").mkdir(parents=True, exist_ok=True)
    _write_blueprint(tmp_path / "agent-library")
    config = tmp_path / "config.yaml"
    config.write_text(
        "schema_version: 1\n"
        "flowgency:\n"
        "  title: Flowgency\n"
        "  default_team: newsletter\n"
        "  ai_backend: claude-code\n"
        "  agent_library: agent-library\n"
        "  compilation_cache: compiled-agents\n"
        "  memory_store: memory\n"
        "  prompt_store: prompts\n"
        "  dispatch:\n"
        "    interval: 15\n"
        "  jobs:\n"
        "    pool: 1\n"
        "teams:\n"
        "  newsletter:\n"
        "    name: Newsletter\n"
        "    workspace_path: workspaces/newsletter\n"
        "    path: agents/newsletter\n"
        "    default_integration: copilot\n"
        "    dispatch:\n"
        "      enabled: true\n"
        "    permissions:\n"
        "      mode: restricted\n"
        "      rules:\n"
        "        - path: repo\n"
        "          tools: [shell]\n"
        "    runtime:\n"
        "      timeout: 1800\n"
        "    agents:\n"
        "      - name: builder\n"
        "        blueprint: builder-blueprint\n"
        "        integration: copilot\n"
        "        integration_config:\n"
        "          command: echo ok\n"
        "        default_memory:\n          scope: agent\n"
        "        routines:\n"
        "          - id: daily-review\n"
        "            prompt:\n"
        "              scope: blueprint\n"
        "              name: daily-review\n"
        f"{schedule}",
        encoding="utf-8",
    )
    return config


class _Bench:
    """A real installation: real submission, real queue, one worker slot."""

    def __init__(self, tmp_path: Path, schedule: str):
        self.config_path = _write_config(tmp_path, schedule=schedule)
        self.memory_store = tmp_path / "memory"
        self.logs_root = tmp_path / "agents" / "newsletter" / "logs"
        self.launcher = _FlakyLauncher()
        self._blocker = None

    def fill_the_pool(self) -> None:
        """Occupy the only slot with a real manual job that has been launched."""
        self._blocker = submit_job_request(
            JobRequest(
                config_path=self.config_path,
                team_key="newsletter",
                agent_name="builder",
                trigger="manual_prompt",
                task_input="",
                routine_id="daily-review",
            ),
            self.launcher,
        )
        self.launcher.launched.clear()

    def free_the_pool(self) -> None:
        cancel_job(self._blocker.path)

    def cycle(self) -> None:
        run_dispatch_cycle(None, self.config_path, self.launcher)

    def scheduled(self) -> list:
        group_dir = JobStore(self.memory_store).team_root("newsletter")
        records = [read_job(path) for path in sorted(group_dir.glob("*.yaml"))]
        return [
            record
            for record in records
            if record.spec.trigger == "scheduled_prompt"
        ]

@pytest.fixture
def at_bench(tmp_path):
    return _Bench(tmp_path, "            schedule:\n              at: '08:00'\n")


def _queue_the_occurrence(bench, monkeypatch):
    """Cycle one: the pool is full, so the job waits and the marker is stamped."""
    monkeypatch.setenv("FLOWGENCY_FIXED_NOW", "2026-07-29T11:57:00")
    bench.fill_the_pool()
    bench.cycle()
    assert bench.launcher.launched == []
    assert [record.status for record in bench.scheduled()] == ["queued"]


def _fail_scheduled_job_before_runtime(bench):
    import flowgency.jobs.execution as execution_module

    [record] = bench.scheduled()
    authority = JobStore(bench.memory_store).reference(
        "newsletter",
        record.spec.job_id,
        record.authority_digest,
    )
    original_create_launch_view = execution_module.create_launch_view
    try:
        execution_module.create_launch_view = (
            lambda *args, **kwargs: (_ for _ in ()).throw(
                ImportError("launch view import failed")
            )
        )
        return execute_job(authority)
    finally:
        execution_module.create_launch_view = original_create_launch_view


def test_a_failed_deferred_launch_leaves_the_occurrence_recoverable(
    at_bench, monkeypatch
):
    _queue_the_occurrence(at_bench, monkeypatch)
    marker = at_marker_path(
        at_bench.logs_root, "builder", "daily-review", "2026-07-29"
    )
    assert marker.exists()

    at_bench.free_the_pool()
    at_bench.launcher.failures = 1
    at_bench.cycle()

    statuses = sorted(record.status for record in at_bench.scheduled())
    assert statuses == ["failed", "queued"]
    launched = [
        record for record in at_bench.scheduled() if record.launched_at is not None
    ]
    assert len(launched) == 1
    assert at_bench.launcher.launched == [launched[0].spec.job_id]


@pytest.mark.parametrize(
    "bench_name",
    ["at_bench", "every_bench"],
)
def test_a_confirmed_before_runtime_failure_is_recovered_once(
    request,
    bench_name,
    monkeypatch,
):
    bench = request.getfixturevalue(bench_name)
    _queue_the_occurrence(bench, monkeypatch)

    bench.free_the_pool()
    failed = _fail_scheduled_job_before_runtime(bench)

    first_pass = bench.scheduled()
    assert len(first_pass) == 1
    assert first_pass[0].spec.job_id == failed.spec.job_id
    assert first_pass[0].status == "failed"
    assert first_pass[0].result_metadata == {
        "execution_failure": {"phase": "before_runtime"}
    }
    failed_due_at = first_pass[0].due_at

    bench.cycle()

    again = sorted(bench.scheduled(), key=lambda item: item.spec.job_id)
    assert len(again) == 2
    assert sorted(record.status for record in again) == ["failed", "queued"]
    assert {record.due_at for record in again} == {failed_due_at}
    recovered = [record for record in again if record.status == "queued"]
    assert len(recovered) == 1
    assert recovered[0].launched_at is not None
    assert bench.launcher.launched == [recovered[0].spec.job_id]

    bench.cycle()

    final_records = sorted(bench.scheduled(), key=lambda item: item.spec.job_id)
    assert len(final_records) == 2
    assert sorted(record.status for record in final_records) == ["failed", "queued"]


@pytest.mark.parametrize(
    "bench_name",
    ["at_bench", "every_bench"],
)
def test_repeated_before_runtime_failures_remain_recoverable(
    request,
    bench_name,
    monkeypatch,
):
    bench = request.getfixturevalue(bench_name)
    _queue_the_occurrence(bench, monkeypatch)

    bench.free_the_pool()
    first_failed = _fail_scheduled_job_before_runtime(bench)

    class _RepeatFailureLauncher:
        def launch(self, reference):
            import flowgency.jobs.execution as execution_module

            original_create_launch_view = execution_module.create_launch_view
            try:
                execution_module.create_launch_view = (
                    lambda *args, **kwargs: (_ for _ in ()).throw(
                        ImportError("launch view import failed")
                    )
                )
                execute_job(reference)
            finally:
                execution_module.create_launch_view = original_create_launch_view
            return LaunchResult(worker_pid=os.getpid())

    bench.launcher = _RepeatFailureLauncher()
    bench.cycle()

    records = sorted(bench.scheduled(), key=lambda item: item.spec.job_id)
    assert len(records) == 2
    assert [record.status for record in records] == ["failed", "failed"]
    assert all(
        record.result_metadata == {"execution_failure": {"phase": "before_runtime"}}
        for record in records
    )
    assert {record.due_at for record in records} == {first_failed.due_at}
    assert lost_occurrences(records) == {
        ("builder", "daily-review"): datetime.fromisoformat(first_failed.due_at)
    }


def test_a_recovered_occurrence_is_not_fired_a_third_time(at_bench, monkeypatch):
    _queue_the_occurrence(at_bench, monkeypatch)
    at_bench.free_the_pool()
    at_bench.launcher.failures = 1
    at_bench.cycle()

    at_bench.cycle()

    assert len(at_bench.scheduled()) == 2


def test_the_catch_up_bound_still_forgets_a_lost_occurrence(at_bench, monkeypatch):
    _queue_the_occurrence(at_bench, monkeypatch)
    at_bench.free_the_pool()
    at_bench.launcher.failures = 2
    at_bench.cycle()
    assert [record.status for record in at_bench.scheduled()] == ["failed", "failed"]

    monkeypatch.setenv("FLOWGENCY_FIXED_NOW", "2026-07-30T03:00:00")
    at_bench.cycle()

    assert len(at_bench.scheduled()) == 2


@pytest.fixture
def every_bench(tmp_path):
    bench = _Bench(tmp_path, "            schedule:\n              every: 6h\n")
    marker = every_marker_path(bench.logs_root, "builder", "daily-review")
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.touch()
    anchor = datetime(2026, 7, 29, 3, 0).timestamp()
    os.utime(marker, (anchor, anchor))
    return bench


def test_a_failed_deferred_launch_of_an_every_occurrence_is_offered_again(
    every_bench, monkeypatch
):
    """The anchor has already advanced, so the occurrence needs naming again."""
    _queue_the_occurrence(every_bench, monkeypatch)
    marker = every_marker_path(every_bench.logs_root, "builder", "daily-review")
    assert datetime.fromtimestamp(marker.stat().st_mtime) == datetime(2026, 7, 29, 9)

    every_bench.free_the_pool()
    every_bench.launcher.failures = 1
    every_bench.cycle()

    statuses = sorted(record.status for record in every_bench.scheduled())
    assert statuses == ["failed", "queued"]
    assert len(every_bench.launcher.launched) == 1


def _record(
    routine,
    due_at,
    status,
    launched_at=None,
    worker_pid=None,
    trigger="scheduled_prompt",
    result_metadata=None,
):
    return SimpleNamespace(
        spec=SimpleNamespace(
            trigger=trigger, agent_name="builder", routine_id=routine
        ),
        due_at=due_at,
        status=status,
        launched_at=launched_at,
        worker_pid=worker_pid,
        result_metadata=result_metadata,
    )


def test_only_a_job_that_never_reached_a_worker_reopens_its_occurrence():
    records = [
        _record("a", "2026-07-29T08:00:00", "failed"),
        _record("b", "2026-07-29T08:00:00", "failed", launched_at="2026-07-29T08:01:00"),
        _record("c", "2026-07-29T08:00:00", "cancelled"),
        _record("d", "2026-07-29T08:00:00", "complete"),
        # worker beat claim_job: worker_pid set, launched_at absent — still launched
        _record("e", "2026-07-29T08:00:00", "failed", worker_pid=12345),
    ]

    assert lost_occurrences(records) == {
        ("builder", "a"): datetime(2026, 7, 29, 8, 0)
    }


def test_a_launched_before_runtime_failure_reopens_its_occurrence():
    record = _record(
        "audit",
        "2026-07-29T08:00:00",
        "failed",
        launched_at="2026-07-29T08:00:01",
        worker_pid=12345,
        result_metadata={"execution_failure": {"phase": "before_runtime"}},
    )

    assert lost_occurrences([record]) == {
        ("builder", "audit"): datetime(2026, 7, 29, 8, 0)
    }


@pytest.mark.parametrize(
    "result_metadata",
    [
        None,
        "not-a-dict",
        {},
        {"execution_failure": "not-a-dict"},
        {"execution_failure": {"phase": "after_runtime"}},
        {
            "execution_failure": {"phase": "before_runtime"},
            "ticket_cleanup": "not-a-dict",
        },
        {
            "execution_failure": {"phase": "before_runtime"},
            "ticket_cleanup": None,
        },
        {
            "execution_failure": {"phase": "before_runtime"},
            "ticket_cleanup": {"confirmed": False, "requires_retry": False, "status": "idle", "pending_cleanup": []},
        },
        {
            "execution_failure": {"phase": "before_runtime"},
            "ticket_cleanup": {"confirmed": True, "requires_retry": True, "status": "idle", "pending_cleanup": []},
        },
        {
            "execution_failure": {"phase": "before_runtime"},
            "ticket_cleanup": {"confirmed": True, "requires_retry": False, "status": "pending", "pending_cleanup": []},
        },
        {
            "execution_failure": {"phase": "before_runtime"},
            "ticket_cleanup": {"confirmed": True, "requires_retry": False, "status": "error", "pending_cleanup": []},
        },
        {
            "execution_failure": {"phase": "before_runtime"},
            "ticket_cleanup": {
                "confirmed": True,
                "requires_retry": False,
                "status": "cleared",
                "pending_cleanup": [],
                "error": {"message": "broker close failed"},
            },
        },
        {
            "execution_failure": {"phase": "before_runtime"},
            "ticket_cleanup": {"confirmed": True, "requires_retry": False, "status": "cleared", "pending_cleanup": ["still-pending"]},
        },
        {
            "execution_failure": {"phase": "before_runtime"},
            "ticket_cleanup": {"confirmed": True, "requires_retry": False, "status": "cleared", "pending_cleanup": "not-a-list"},
        },
        {
            "execution_failure": {"phase": "before_runtime"},
            "ticket_cleanup": {"confirmed": True, "requires_retry": False, "status": ["cleared"], "pending_cleanup": []},
        },
        {
            "execution_failure": {"phase": "before_runtime"},
            "ticket_cleanup": {
                "confirmed": True,
                "requires_retry": False,
                "status": {"state": "cleared"},
                "pending_cleanup": [],
            },
        },
    ],
)
def test_launched_failures_without_settled_before_runtime_evidence_do_not_reopen(
    result_metadata,
):
    record = _record(
        "audit",
        "2026-07-29T08:00:00",
        "failed",
        launched_at="2026-07-29T08:00:01",
        worker_pid=12345,
        result_metadata=result_metadata,
    )

    assert lost_occurrences([record]) == {}


def test_a_launched_before_runtime_failure_with_settled_idle_cleanup_reopens():
    record = _record(
        "audit",
        "2026-07-29T08:00:00",
        "failed",
        launched_at="2026-07-29T08:00:01",
        worker_pid=12345,
        result_metadata={
            "execution_failure": {"phase": "before_runtime"},
            "ticket_cleanup": {
                "confirmed": True,
                "requires_retry": False,
                "status": "idle",
                "pending_cleanup": [],
            },
        },
    )

    assert lost_occurrences([record]) == {
        ("builder", "audit"): datetime(2026, 7, 29, 8, 0)
    }


def test_a_launched_before_runtime_failure_with_settled_cleared_cleanup_reopens():
    record = _record(
        "audit",
        "2026-07-29T08:00:00",
        "failed",
        launched_at="2026-07-29T08:00:01",
        worker_pid=12345,
        result_metadata={
            "execution_failure": {"phase": "before_runtime"},
            "ticket_cleanup": {
                "confirmed": True,
                "requires_retry": False,
                "status": "cleared",
                "pending_cleanup": [],
            },
        },
    )

    assert lost_occurrences([record]) == {
        ("builder", "audit"): datetime(2026, 7, 29, 8, 0)
    }


def test_a_later_job_for_the_same_routine_makes_an_earlier_loss_moot():
    records = [
        _record("a", "2026-07-29T08:00:00", "failed"),
        _record("a", "2026-07-30T08:00:00", "queued", launched_at="x"),
    ]

    assert lost_occurrences(records) == {}


def test_a_manual_launch_never_reopens_a_scheduled_occurrence():
    records = [
        _record("a", "2026-07-29T08:00:00", "failed", trigger="manual_prompt"),
    ]

    assert lost_occurrences(records) == {}


def test_a_lost_every_occurrence_is_offered_only_while_it_is_the_anchor(tmp_path):
    routine = SimpleNamespace(
        id="audit", schedule=SimpleNamespace(at=None, every="6h")
    )
    marker = every_marker_path(tmp_path, "builder", "audit")
    marker.touch()
    anchor = datetime(2026, 7, 29, 9, 0)
    os.utime(marker, (anchor.timestamp(), anchor.timestamp()))
    now = datetime(2026, 7, 29, 11, 0)

    assert _due_occurrence(routine, tmp_path, "builder", now)[0] is None
    assert _due_occurrence(routine, tmp_path, "builder", now, anchor)[0] == anchor
    stale = datetime(2026, 7, 29, 3, 0)
    assert _due_occurrence(routine, tmp_path, "builder", now, stale)[0] is None
