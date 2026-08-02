"""A scheduled occurrence whose deferred launch fails must stay recoverable."""

import os
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from agency.dispatch.run import _due_occurrence, lost_occurrences, run_dispatch_cycle
from agency.dispatch.schedule import at_marker_path, every_marker_path
from agency.jobs import JobRequest, submit_job_request
from agency.jobs.authority import JobStore
from agency.jobs.launcher import LaunchResult
from agency.jobs.store import cancel_job, read_job


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
        "schema_version: 5\n"
        "agency:\n"
        "  title: Agency\n"
        "  default_group: newsletter\n"
        "  ai_backend: claude-code\n"
        "  agent_library: agent-library\n"
        "  compilation_cache: compiled-agents\n"
        "  memory_store: memory\n"
        "  prompt_store: prompts\n"
        "  dispatch:\n"
        "    interval: 15\n"
        "  jobs:\n"
        "    pool: 1\n"
        "groups:\n"
        "  newsletter:\n"
        "    name: Newsletter\n"
        "    workspace_path: workspaces/newsletter\n"
        "    path: agents/newsletter\n"
        "    default_integration: copilot\n"
        "    dispatch:\n"
        "      enabled: true\n"
        "    runtime:\n"
        "      timeout: 1800\n"
        "      sandbox:\n        mode: restricted\n        roots:\n          - repo\n"
        "      tools:\n        mode: allowlist\n        names:\n          - shell\n"
        "    agents:\n"
        "      - name: builder\n"
        "        blueprint: builder-blueprint\n"
        "        integration: copilot\n"
        "        capabilities:\n"
        "          write: true\n"
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
                group_key="newsletter",
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
        group_dir = JobStore(self.memory_store).group_root("newsletter")
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
    monkeypatch.setenv("AGENCY_FIXED_NOW", "2026-07-29T11:57:00")
    bench.fill_the_pool()
    bench.cycle()
    assert bench.launcher.launched == []
    assert [record.status for record in bench.scheduled()] == ["queued"]


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

    monkeypatch.setenv("AGENCY_FIXED_NOW", "2026-07-30T03:00:00")
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


def _record(routine, due_at, status, launched_at=None, worker_pid=None, trigger="scheduled_prompt"):
    return SimpleNamespace(
        spec=SimpleNamespace(
            trigger=trigger, agent_name="builder", routine_id=routine
        ),
        due_at=due_at,
        status=status,
        launched_at=launched_at,
        worker_pid=worker_pid,
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
