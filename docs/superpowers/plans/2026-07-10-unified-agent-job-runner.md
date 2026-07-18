# Unified Agent Job Runner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route scheduled prompts, manually launched saved prompts, approved decisions, and decision retries through one durable detached-process job runner, with explicit proposal-selected decision executors.

**Architecture:** A versioned `JobSpec` is persisted atomically under each group's `shared/jobs` directory, then handed to a replaceable `JobLauncher`. The initial launcher starts `python -m agency.jobs.worker` as a detached process; the worker reloads configuration, resolves the integration, runs the immutable prompt snapshot, persists results, and projects decision state without importing the FastAPI app.

**Tech Stack:** Python 3.11+, dataclasses, PyYAML, `subprocess`, FastAPI/Jinja2, pytest, platform-native POSIX process APIs, and pywin32 on Windows.

## Global Constraints

- All current and future agent triggers submit through `submit_job`; trigger code must not call `integration.run()`.
- Detached workers must survive the dashboard, dispatch timer, or other process that submitted them.
- The launcher boundary must remain replaceable by a future daemon without changing `JobSpec`, submitters, or worker execution.
- Prompt content is immutable after submission; decision prompts embed proposal content and human answers.
- Concurrent jobs are allowed, including multiple jobs for the same agent; no per-agent lock or global concurrency limit is added.
- Proposal `execution_agent` is optional and falls back to `origin_agent`; invalid executors produce a visible error and are never replaced automatically.
- Existing integrations keep the signature `run(agent_dir, prompt_file, timeout, *, sandbox_root=None) -> RunResult`.
- Job, decision, and config writes use temporary files plus `os.replace`.
- Job paths are passed as subprocess argument-list elements with `shell=False`.
- Existing proposals and decisions remain readable; no filesystem migration command is required.
- Do not edit generated `build/`, `agency.egg-info/`, or `christag_agency.egg-info/` files.

## File Structure

### New production files

- `agency/jobs/__init__.py`: stable public submission and model exports.
- `agency/jobs/models.py`: versioned `JobSpec`, mutable `JobRecord`, `JobHandle`, validation constants, and serialization.
- `agency/jobs/store.py`: atomic job persistence, state transitions, and active-job queries.
- `agency/jobs/context.py`: config reload, normalized group/agent resolution, integration capability validation, timeout, and sandbox resolution.
- `agency/jobs/launcher.py`: `JobLauncher` protocol and cross-platform `DetachedProcessLauncher`.
- `agency/jobs/submission.py`: durable validation/write/launch transaction.
- `agency/jobs/execution.py`: sole orchestration-level `integration.run()` caller, isolated logs, and decision projection.
- `agency/jobs/worker.py`: `python -m agency.jobs.worker <job-path>` entry point.
- `agency/jobs/reconciliation.py`: conservative worker liveness checks and stale decision/job repair.
- `agency/jobs/prompts.py`: immutable decision prompt construction.

### Modified production files

- `agency/config.py`: add `load_config_path(path)` so workers never import `agency.app`.
- `agency/dispatch/run.py`: submit scheduled jobs and write schedule markers only after launch succeeds.
- `agency/app.py`: migrate manual, decision, retry, startup recovery, and running-state call sites to jobs APIs.
- `agency/templates/proposal_detail.html`: executor selector and validation error.
- `agency/templates/decision_detail.html`: retry executor selector and current job metadata.
- `kb/data-formats.md`: document proposal and decision execution fields.

### Tests

- `tests/test_job_models.py`: model serialization, atomic persistence, transitions, and active-job queries.
- `tests/test_job_submission.py`: context validation, detached flags, launch success/failure, and concurrent submission.
- `tests/test_job_execution.py`: worker execution, logs, failures, prompt snapshots, and guarded decision projection.
- `tests/test_job_reconciliation.py`: live, dead, uncertain, complete, and historical decision behavior.
- `tests/test_job_detached_process.py`: child continues after the submitting process exits.
- `tests/test_dispatch_run.py`: scheduled submission and marker ordering.
- `tests/test_agent_run.py`: manual saved-prompt submission and concurrent acceptance.
- `tests/test_execute_decision.py`: replace historical in-process execution tests with decision job route and projection tests.
- `tests/test_proposal_questions.py`: executor defaults, form rendering, and validation errors.

---

### Task 1: Durable Job Contract And Atomic Store

**Files:**
- Create: `agency/jobs/models.py`
- Create: `agency/jobs/store.py`
- Modify: `agency/config.py`
- Test: `tests/test_job_models.py`

**Interfaces:**
- Consumes: existing `yaml`, `normalize_agents()`, and group `path` configuration.
- Produces: `JobSpec.create(...) -> JobSpec`, `JobRecord.from_spec(spec) -> JobRecord`, `job_path(group_path, job_id) -> Path`, `read_job(path) -> JobRecord`, `write_job(path, record) -> None`, `transition_job(path, expected, status, **changes) -> JobRecord`, `active_jobs(group_path, agent_name=None) -> list[JobRecord]`, and `load_config_path(path) -> dict`.

- [ ] **Step 1: Write failing model and store tests**

Create `tests/test_job_models.py` with focused examples:

```python
from dataclasses import replace
from pathlib import Path

import pytest

from agency.config import load_config_path
from agency.jobs.models import JobRecord, JobSpec
from agency.jobs.store import InvalidJobTransition, active_jobs, job_path, read_job, transition_job, write_job


def make_spec(tmp_path: Path, *, agent: str = "product") -> JobSpec:
    return JobSpec.create(
        config_path=tmp_path / "config.yaml",
        group_key="test",
        agent_name=agent,
        trigger="manual_prompt",
        prompt_source={"type": "saved_prompt", "path": "shared/prompts/routine.md"},
        prompt_content="# Routine\n",
    )


def test_job_spec_round_trips_yaml_safe_data(tmp_path):
    spec = make_spec(tmp_path)
    record = JobRecord.from_spec(spec)
    path = job_path(tmp_path / "group", spec.job_id)

    write_job(path, record)

    assert read_job(path) == record
    assert read_job(path).spec.config_path == str((tmp_path / "config.yaml").resolve())


def test_transition_requires_expected_state(tmp_path):
    record = JobRecord.from_spec(make_spec(tmp_path))
    path = tmp_path / "job.yaml"
    write_job(path, record)

    running = transition_job(path, "queued", "running", worker_pid=123)
    assert running.status == "running"
    with pytest.raises(InvalidJobTransition):
        transition_job(path, "queued", "failed")


def test_active_jobs_keeps_concurrent_jobs_for_same_agent(tmp_path):
    group = tmp_path / "group"
    first = JobRecord.from_spec(make_spec(tmp_path))
    second = JobRecord.from_spec(make_spec(tmp_path))
    write_job(job_path(group, first.spec.job_id), first)
    write_job(job_path(group, second.spec.job_id), replace(second, status="running"))

    assert {job.spec.job_id for job in active_jobs(group, "product")} == {
        first.spec.job_id,
        second.spec.job_id,
    }


def test_load_config_path_does_not_depend_on_cwd(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("groups:\n  test:\n    path: /tmp/group\n")
    monkeypatch.chdir(tmp_path.parent)
    assert load_config_path(config_path)["groups"]["test"]["path"] == "/tmp/group"
```

- [ ] **Step 2: Run the tests and verify the missing-package failure**

Run:

```text
python -m pytest tests/test_job_models.py -v
```

Expected: collection fails with `ModuleNotFoundError: No module named 'agency.jobs'`.

- [ ] **Step 3: Implement `load_config_path` and the versioned models**

Add to `agency/config.py`:

```python
import yaml


def load_config_path(path: Path) -> dict:
    """Load an Agency YAML config from an explicit path."""
    path = Path(path)
    if not path.exists():
        return {"agency": {"title": "Agency", "default_group": ""}, "groups": {}}
    with path.open() as stream:
        return yaml.safe_load(stream) or {}
```

Create `agency/jobs/models.py` with these public shapes and exact field names:

```python
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

SCHEMA_VERSION = 1
VALID_TRIGGERS = {"scheduled_prompt", "manual_prompt", "decision", "decision_retry"}
VALID_STATUSES = {"queued", "running", "complete", "failed"}


@dataclass(frozen=True)
class JobSpec:
    schema_version: int
    job_id: str
    config_path: str
    group_key: str
    agent_name: str
    trigger: str
    prompt_source: dict[str, Any]
    prompt_content: str
    timeout_override: int | None
    created_at: str
    decision_context: dict[str, Any] | None

    @classmethod
    def create(cls, *, config_path: Path, group_key: str, agent_name: str,
               trigger: str, prompt_source: dict[str, Any], prompt_content: str,
               timeout_override: int | None = None,
               decision_context: dict[str, Any] | None = None) -> "JobSpec":
        return cls(
            schema_version=SCHEMA_VERSION,
            job_id=uuid4().hex,
            config_path=str(Path(config_path).resolve()),
            group_key=group_key,
            agent_name=agent_name,
            trigger=trigger,
            prompt_source=prompt_source,
            prompt_content=prompt_content,
            timeout_override=timeout_override,
            created_at=datetime.now(timezone.utc).isoformat(),
            decision_context=decision_context,
        )

    def validate(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"Unsupported job schema: {self.schema_version}")
        if self.trigger not in VALID_TRIGGERS:
            raise ValueError(f"Invalid job trigger: {self.trigger}")
        if not self.job_id or not self.group_key or not self.agent_name:
            raise ValueError("Job ID, group, and agent are required")
        if not self.prompt_content.strip():
            raise ValueError("Prompt content is required")


@dataclass
class JobRecord:
    spec: JobSpec
    status: str = "queued"
    worker_pid: int | None = None
    started_at: str | None = None
    completed_at: str | None = None
    stdout_path: str | None = None
    stderr_path: str | None = None
    exit_code: int | None = None
    duration_seconds: float | None = None
    changed_files: list[dict[str, Any]] = field(default_factory=list)
    execution_summary: str | None = None

    @classmethod
    def from_spec(cls, spec: JobSpec) -> "JobRecord":
        spec.validate()
        return cls(spec=spec)

    def to_dict(self) -> dict[str, Any]:
        return {"spec": asdict(self.spec), **{k: v for k, v in asdict(self).items() if k != "spec"}}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "JobRecord":
        spec = JobSpec(**data["spec"])
        spec.validate()
        fields = {key: value for key, value in data.items() if key != "spec"}
        record = cls(spec=spec, **fields)
        if record.status not in VALID_STATUSES:
            raise ValueError(f"Invalid job status: {record.status}")
        return record


@dataclass(frozen=True)
class JobHandle:
    job_id: str
    status: str
    path: Path
    worker_pid: int | None
```

The nested in-memory `spec` representation is serialized intentionally. It keeps immutable submission fields separate from mutable execution state while retaining one durable YAML document.

- [ ] **Step 4: Implement atomic persistence and transitions**

Create `agency/jobs/store.py`:

```python
from dataclasses import replace
import os
from pathlib import Path
import tempfile
from typing import Any

import yaml

from .models import JobRecord


class InvalidJobTransition(RuntimeError):
    pass


def job_path(group_path: Path, job_id: str) -> Path:
    return Path(group_path) / "shared" / "jobs" / f"{job_id}.yaml"


def write_job(path: Path, record: JobRecord) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, suffix=".yaml")
    try:
        with os.fdopen(descriptor, "w") as stream:
            yaml.safe_dump(record.to_dict(), stream, sort_keys=False)
        os.replace(temporary, path)
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise


def read_job(path: Path) -> JobRecord:
    with Path(path).open() as stream:
        return JobRecord.from_dict(yaml.safe_load(stream) or {})


def transition_job(path: Path, expected: str, status: str, **changes: Any) -> JobRecord:
    record = read_job(path)
    if record.status != expected:
        raise InvalidJobTransition(
            f"Job {record.spec.job_id} is {record.status}, expected {expected}"
        )
    updated = replace(record, status=status, **changes)
    write_job(path, updated)
    return updated


def active_jobs(group_path: Path, agent_name: str | None = None) -> list[JobRecord]:
    jobs_dir = Path(group_path) / "shared" / "jobs"
    if not jobs_dir.is_dir():
        return []
    records = []
    for path in jobs_dir.glob("*.yaml"):
        try:
            record = read_job(path)
        except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError):
            continue
        if record.status not in {"queued", "running"}:
            continue
        if agent_name is None or record.spec.agent_name == agent_name:
            records.append(record)
    return records
```

- [ ] **Step 5: Run focused tests**

Run:

```text
python -m pytest tests/test_job_models.py -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit the durable contract**

```bash
git add agency/config.py agency/jobs/models.py agency/jobs/store.py tests/test_job_models.py
git commit -m "feat(jobs): add durable job records"
```

---

### Task 2: Context Resolution, Detached Launcher, And Submission

**Files:**
- Create: `agency/jobs/context.py`
- Create: `agency/jobs/launcher.py`
- Create: `agency/jobs/submission.py`
- Create: `agency/jobs/__init__.py`
- Test: `tests/test_job_submission.py`

**Interfaces:**
- Consumes: `JobSpec`, `JobRecord`, store APIs, `load_config_path`, `normalize_agents`, `get_agent_dir`, `get_sandbox_root`, `detect_integration`, and `get_integration`.
- Produces: `resolve_job_context(spec) -> ResolvedJobContext`, `LaunchResult`, `JobLauncher`, `DetachedProcessLauncher.launch(path)`, `submit_job(spec, launcher=None) -> JobHandle`, `JobValidationError`, and `JobSubmissionError`.

- [ ] **Step 1: Write failing context, launcher, and submission tests**

Create `tests/test_job_submission.py`:

```python
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from agency.jobs import JobSpec, JobSubmissionError, JobValidationError, submit_job
from agency.jobs.launcher import (
    CREATE_NEW_PROCESS_GROUP, DETACHED_PROCESS,
    DetachedProcessLauncher, LaunchResult,
)
from agency.jobs.store import read_job


def configured_spec(tmp_path: Path, *, agent="product") -> JobSpec:
    group = tmp_path / "group"
    (group / agent).mkdir(parents=True)
    config = tmp_path / "config.yaml"
    config.write_text(
        "groups:\n  test:\n    name: Test\n    path: " + str(group).replace("\\", "/") +
        "\n    agents:\n      - name: " + agent + "\n        integration: script\n"
        "        integration_config:\n          command: echo ok\n"
    )
    return JobSpec.create(
        config_path=config,
        group_key="test",
        agent_name=agent,
        trigger="manual_prompt",
        prompt_source={"type": "saved_prompt", "path": "routine.md"},
        prompt_content="Run it",
    )


def test_submit_persists_then_launches(tmp_path):
    spec = configured_spec(tmp_path)
    launcher = Mock()
    launcher.launch.return_value = LaunchResult(worker_pid=4321)

    handle = submit_job(spec, launcher)

    record = read_job(handle.path)
    assert record.status == "queued"
    assert launcher.launch.call_args.args == (handle.path,)
    assert handle.worker_pid == 4321


def test_submit_marks_record_failed_when_launch_fails(tmp_path):
    spec = configured_spec(tmp_path)
    launcher = Mock()
    launcher.launch.side_effect = OSError("spawn denied")

    with pytest.raises(JobSubmissionError, match="spawn denied") as error:
        submit_job(spec, launcher)

    record = read_job(error.value.job_path)
    assert record.status == "failed"
    assert "spawn denied" in record.execution_summary


def test_submit_rejects_missing_or_non_executable_agent(tmp_path):
    spec = configured_spec(tmp_path, agent="missing")
    Path(spec.config_path).write_text(
        "groups:\n  test:\n    name: Test\n    path: " + str(tmp_path / "group").replace("\\", "/") +
        "\n    agents:\n      - name: missing\n        integration: sdk\n"
    )
    with pytest.raises(JobValidationError):
        submit_job(spec, Mock())


def test_windows_launcher_uses_detached_flags(tmp_path):
    with patch("agency.jobs.launcher.os.name", "nt"), patch("agency.jobs.launcher.subprocess.Popen") as popen:
        popen.return_value.pid = 77
        result = DetachedProcessLauncher().launch(tmp_path / "job.yaml")
    flags = popen.call_args.kwargs["creationflags"]
    assert flags & DETACHED_PROCESS
    assert flags & CREATE_NEW_PROCESS_GROUP
    assert result.worker_pid == 77


def test_posix_launcher_starts_new_session(tmp_path):
    with patch("agency.jobs.launcher.os.name", "posix"), patch("agency.jobs.launcher.subprocess.Popen") as popen:
        popen.return_value.pid = 78
        DetachedProcessLauncher().launch(tmp_path / "job.yaml")
    assert popen.call_args.kwargs["start_new_session"] is True
    assert popen.call_args.kwargs["shell"] is False
```

- [ ] **Step 2: Run the tests and verify imports fail**

Run:

```text
python -m pytest tests/test_job_submission.py -v
```

Expected: collection fails because `agency.jobs` does not yet export submission APIs.

- [ ] **Step 3: Implement context resolution without importing FastAPI**

Create `agency/jobs/context.py`. `ResolvedJobContext` must expose `config`, `group`, `group_path`, `agent_config`, `agent_dir`, `integration`, `timeout`, and `sandbox_root`. Resolve integrations in the same order as the current app: filesystem detection, configured integration, then `claude-code`.

```python
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agency.config import SandboxSpec, get_agent_dir, get_sandbox_root, load_config_path, normalize_agents
from agency.integrations import BaseIntegration, detect_integration, get_integration

from .models import JobSpec


class JobValidationError(ValueError):
    pass


@dataclass
class ResolvedJobContext:
    config: dict[str, Any]
    group: dict[str, Any]
    group_path: Path
    agent_config: dict[str, Any]
    agent_dir: Path
    integration: BaseIntegration
    timeout: int
    sandbox_root: SandboxSpec | None


def resolve_job_context(spec: JobSpec) -> ResolvedJobContext:
    spec.validate()
    config = load_config_path(Path(spec.config_path))
    raw_group = config.get("groups", {}).get(spec.group_key)
    if raw_group is None:
        raise JobValidationError(f"Unknown group: {spec.group_key}")
    group_path = Path(raw_group["path"])
    agents = normalize_agents(raw_group.get("agents", []), raw_group.get("default_integration", "claude-code"))
    agent_config = next((agent for agent in agents if agent["name"] == spec.agent_name), None)
    if agent_config is None:
        raise JobValidationError(f"Unknown agent: {spec.agent_name}")
    group = {**raw_group, "path": group_path, "agents_full": agents}
    agent_dir = get_agent_dir(group, spec.agent_name)
    if not agent_dir.is_dir():
        raise JobValidationError(f"Agent directory not found: {agent_dir}")
    integration = detect_integration(agent_dir) or get_integration(
        agent_config.get("integration", raw_group.get("default_integration", "claude-code"))
    )
    if not integration.supports_execution:
        raise JobValidationError(
            f"Integration '{integration.name}' does not support execution"
        )
    if hasattr(integration, "with_config") and agent_config.get("integration_config"):
        integration = integration.with_config(agent_config["integration_config"])
    dispatch = raw_group.get("dispatch", {})
    configured = dispatch.get("timeout", 1800)
    agent_dispatch = dispatch.get("agents", {}).get(spec.agent_name, {})
    if isinstance(agent_dispatch, dict):
        configured = agent_dispatch.get("timeout", configured)
    timeout = spec.timeout_override if spec.timeout_override is not None else configured
    return ResolvedJobContext(
        config=config,
        group=group,
        group_path=group_path,
        agent_config=agent_config,
        agent_dir=agent_dir,
        integration=integration,
        timeout=timeout,
        sandbox_root=get_sandbox_root(raw_group),
    )
```

- [ ] **Step 4: Implement the replaceable detached launcher**

Create `agency/jobs/launcher.py`:

```python
from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
import sys
from typing import Protocol

DETACHED_PROCESS = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


@dataclass(frozen=True)
class LaunchResult:
    worker_pid: int | None


class JobLauncher(Protocol):
    def launch(self, job_path: Path) -> LaunchResult: ...


class DetachedProcessLauncher:
    def launch(self, job_path: Path) -> LaunchResult:
        kwargs = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "close_fds": True,
            "shell": False,
        }
        if os.name == "nt":
            kwargs["creationflags"] = (
                DETACHED_PROCESS
                | CREATE_NEW_PROCESS_GROUP
                | CREATE_NO_WINDOW
            )
        else:
            kwargs["start_new_session"] = True
        process = subprocess.Popen(
            [sys.executable, "-m", "agency.jobs.worker", str(Path(job_path).resolve())],
            **kwargs,
        )
        return LaunchResult(worker_pid=process.pid)
```

Do not persist the launcher-returned PID into the queued record. The worker records its own PID during the atomic `queued -> running` transition, avoiding a race where the submitter overwrites a fast worker's status.

- [ ] **Step 5: Implement submit-after-durable-write behavior**

Create `agency/jobs/submission.py`:

```python
from dataclasses import replace
from pathlib import Path

from .context import JobValidationError, resolve_job_context
from .launcher import DetachedProcessLauncher, JobLauncher
from .models import JobHandle, JobRecord, JobSpec
from .store import job_path, write_job


class JobSubmissionError(RuntimeError):
    def __init__(self, message: str, job_path: Path):
        super().__init__(message)
        self.job_path = job_path


def submit_job(spec: JobSpec, launcher: JobLauncher | None = None) -> JobHandle:
    context = resolve_job_context(spec)
    path = job_path(context.group_path, spec.job_id)
    record = JobRecord.from_spec(spec)
    write_job(path, record)
    selected_launcher = launcher or DetachedProcessLauncher()
    try:
        result = selected_launcher.launch(path)
    except Exception as error:
        failed = replace(record, status="failed", execution_summary=f"Launch error: {error}")
        write_job(path, failed)
        raise JobSubmissionError(str(error), path) from error
    return JobHandle(spec.job_id, "queued", path, result.worker_pid)
```

Create `agency/jobs/__init__.py` with only stable exports:

```python
from .context import JobValidationError
from .launcher import DetachedProcessLauncher, JobLauncher, LaunchResult
from .models import JobHandle, JobRecord, JobSpec
from .submission import JobSubmissionError, submit_job

__all__ = [
    "DetachedProcessLauncher", "JobHandle", "JobLauncher", "JobRecord",
    "JobSpec", "JobSubmissionError", "JobValidationError", "LaunchResult",
    "submit_job",
]
```

- [ ] **Step 6: Run focused tests**

Run:

```text
python -m pytest tests/test_job_models.py tests/test_job_submission.py -v
```

Expected: all tests pass.

- [ ] **Step 7: Commit submission and launcher**

```bash
git add agency/jobs tests/test_job_submission.py
git commit -m "feat(jobs): submit detached agent jobs"
```

---

### Task 3: Worker Execution And Decision Projection

**Files:**
- Create: `agency/jobs/execution.py`
- Create: `agency/jobs/worker.py`
- Test: `tests/test_job_execution.py`
- Modify: `tests/test_execute_decision.py`

**Interfaces:**
- Consumes: `resolve_job_context`, job store transitions, and unchanged integration `RunResult`.
- Produces: `execute_job(job_path) -> JobRecord`, `project_decision(job_path, record) -> None`, and worker `main(argv=None) -> int`.

- [ ] **Step 1: Write failing execution tests**

Create `tests/test_job_execution.py` with a fixture that writes a queued record and monkeypatches `resolve_job_context`. Cover the state before and after `integration.run()`:

```python
from pathlib import Path
from types import SimpleNamespace

from agency.integrations import FileChange, RunResult
from agency.jobs.execution import execute_job
from agency.jobs.models import JobRecord, JobSpec
from agency.jobs.store import read_job, write_job


def queued_job(tmp_path: Path, *, decision_context=None):
    spec = JobSpec.create(
        config_path=tmp_path / "config.yaml",
        group_key="test",
        agent_name="product",
        trigger="decision" if decision_context else "manual_prompt",
        prompt_source={"type": "decision" if decision_context else "saved_prompt"},
        prompt_content="Immutable instructions",
        decision_context=decision_context,
    )
    path = tmp_path / "group" / "shared" / "jobs" / f"{spec.job_id}.yaml"
    write_job(path, JobRecord.from_spec(spec))
    return path, spec


def test_execute_job_transitions_writes_logs_and_changes(tmp_path, monkeypatch):
    path, spec = queued_job(tmp_path)
    seen = {}

    class Integration:
        supports_execution = True
        name = "fake"
        def run(self, agent_dir, prompt_file, timeout, *, sandbox_root=None):
            seen["running"] = read_job(path).status
            seen["prompt"] = prompt_file.read_text()
            return RunResult(0, "done", "warning", 1.25, [FileChange("a.py", "modified", 2, 1)])

    context = SimpleNamespace(
        agent_dir=tmp_path / "group" / "product",
        integration=Integration(), timeout=30, sandbox_root=None,
        group_path=tmp_path / "group",
    )
    context.agent_dir.mkdir(parents=True)
    monkeypatch.setattr("agency.jobs.execution.resolve_job_context", lambda ignored: context)

    result = execute_job(path)

    assert seen == {"running": "running", "prompt": "Immutable instructions"}
    assert result.status == "complete"
    assert Path(result.stdout_path).read_text() == "done"
    assert Path(result.stderr_path).read_text() == "warning"
    assert result.changed_files == [{"path": "a.py", "status": "modified", "lines_added": 2, "lines_removed": 1}]


def test_execute_job_records_exception_as_failed(tmp_path, monkeypatch):
    path, _ = queued_job(tmp_path)
    context = SimpleNamespace(
        agent_dir=tmp_path, timeout=30, sandbox_root=None,
        group_path=tmp_path / "group",
        integration=SimpleNamespace(run=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom"))),
    )
    monkeypatch.setattr("agency.jobs.execution.resolve_job_context", lambda ignored: context)

    result = execute_job(path)

    assert result.status == "failed"
    assert "boom" in result.execution_summary


def test_old_decision_job_cannot_overwrite_current_retry(tmp_path, monkeypatch):
    decisions = tmp_path / "group" / "shared" / "decisions"
    decisions.mkdir(parents=True)
    decision = decisions / "proposal.md"
    decision.write_text("---\nexecution_job_id: newer-job\nexecution_status: running\n---\n")
    path, _ = queued_job(tmp_path, decision_context={"decision_path": str(decision), "proposal_path": "proposal.md"})
    monkeypatch.setattr("agency.jobs.execution.resolve_job_context", lambda ignored: SimpleNamespace(
        agent_dir=tmp_path, timeout=30, sandbox_root=None, group_path=tmp_path / "group",
        integration=SimpleNamespace(run=lambda *args, **kwargs: RunResult(0, "done", "", 0.1)),
    ))

    execute_job(path)

    assert "execution_status: running" in decision.read_text()
```

Replace the direct `execute_decision()` tests in `tests/test_execute_decision.py` with projection assertions against `execute_job()`. Preserve assertions for sandbox propagation, executing agent, absolute stdout log path, changed files including an empty list, running status before invocation, success, and failure.

- [ ] **Step 2: Run tests and verify execution imports fail**

Run:

```text
python -m pytest tests/test_job_execution.py tests/test_execute_decision.py -v
```

Expected: collection fails with `ModuleNotFoundError: agency.jobs.execution`.

- [ ] **Step 3: Implement guarded decision frontmatter projection**

In `agency/jobs/execution.py`, implement private YAML frontmatter parsing and atomic writing locally; do not import `agency.app`. The projection must update only when the decision's current `execution_job_id` equals the completing job ID:

```python
def project_decision(record: JobRecord) -> None:
    context = record.spec.decision_context
    if not context:
        return
    decision_path = Path(context["decision_path"])
    metadata, body = _read_frontmatter(decision_path)
    if metadata.get("execution_job_id") != record.spec.job_id:
        return
    metadata.update({
        "execution_status": record.status,
        "execution_agent": record.spec.agent_name,
        "executed_by": record.spec.agent_name,
        "execution_log": record.stdout_path,
        "changed_files": record.changed_files,
        "execution_summary": record.execution_summary,
    })
    _write_frontmatter_atomic(decision_path, metadata, body)
```

Call it once after the running transition so the decision immediately shows the executor and `running`, and again after final record persistence.

- [ ] **Step 4: Implement the sole integration execution path**

Implement `execute_job(job_path)` with this control flow:

```python
def execute_job(job_path: Path) -> JobRecord:
    record = read_job(job_path)
    started = datetime.now(timezone.utc)
    record = transition_job(
        job_path, "queued", "running",
        worker_pid=os.getpid(), started_at=started.isoformat(),
    )
    project_decision(record)
    prompt_path = Path(job_path).with_suffix(".prompt")
    context = None
    try:
        context = resolve_job_context(record.spec)
        log_dir = context.group_path / "shared" / "logs" / started.strftime("%Y-%m-%d")
        log_dir.mkdir(parents=True, exist_ok=True)
        stem = f"{record.spec.agent_name}-{record.spec.trigger}-{record.spec.job_id}"
        stdout_path = log_dir / f"{stem}.out"
        stderr_path = log_dir / f"{stem}.err"
        prompt_path.write_text(record.spec.prompt_content)
        result = context.integration.run(
            context.agent_dir, prompt_path, context.timeout,
            sandbox_root=context.sandbox_root,
        )
        stdout_path.write_text(result.stdout)
        stderr_path.write_text(result.stderr)
        changes = [
            {"path": item.path, "status": item.status,
             "lines_added": item.lines_added, "lines_removed": item.lines_removed}
            for item in result.changed_files
        ]
        status = "complete" if result.exit_code == 0 else "failed"
        summary = (
            "Agent completed execution (inferred from exit code)."
            if status == "complete" else f"Agent exited with code {result.exit_code}."
        )
        final = transition_job(
            job_path, "running", status,
            completed_at=datetime.now(timezone.utc).isoformat(),
            stdout_path=str(stdout_path.resolve()), stderr_path=str(stderr_path.resolve()),
            exit_code=result.exit_code, duration_seconds=result.duration_seconds,
            changed_files=changes, execution_summary=summary,
        )
    except Exception as error:
        final = transition_job(
            job_path, "running", "failed",
            completed_at=datetime.now(timezone.utc).isoformat(),
            execution_summary=f"Execution error: {error}",
        )
    finally:
        prompt_path.unlink(missing_ok=True)
    project_decision(final)
    return final
```

Use `getattr(result, "changed_files", [])` to preserve compatibility with test doubles and older integrations. Exit code `124` follows the same failed path and summary.

- [ ] **Step 5: Implement the worker CLI**

Create `agency/jobs/worker.py`:

```python
import argparse
from pathlib import Path

from .execution import execute_job


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Execute one Agency job")
    parser.add_argument("job_path", type=Path)
    args = parser.parse_args(argv)
    result = execute_job(args.job_path.resolve())
    return 0 if result.status == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 6: Run focused tests**

Run:

```text
python -m pytest tests/test_job_execution.py tests/test_execute_decision.py -v
```

Expected: all tests pass.

- [ ] **Step 7: Commit worker execution**

```bash
git add agency/jobs/execution.py agency/jobs/worker.py tests/test_job_execution.py tests/test_execute_decision.py
git commit -m "feat(jobs): execute jobs in detached workers"
```

---

### Task 4: Migrate Scheduled And Manual Saved-Prompt Triggers

**Files:**
- Modify: `agency/dispatch/run.py`
- Modify: `agency/app.py`
- Modify: `tests/test_dispatch_run.py`
- Modify: `tests/test_agent_run.py`

**Interfaces:**
- Consumes: `JobSpec.create`, `submit_job`, and `CONFIG_PATH`.
- Produces: `run_dispatch_cycle(config, config_path, launcher=None)` that schedules durable jobs and `POST /{group}/agents/{agent}/run` returning the submitted `job_id`.

- [ ] **Step 1: Replace inline-run expectations with failing submission tests**

In `tests/test_dispatch_run.py`, keep schedule timing tests and replace `run_agent_prompt()` marker tests with cycle-level tests:

```python
def _enabled_config(group_path):
    return {
        "agency": {"dispatch": {"interval": 15}},
        "groups": {"test": {
            "path": str(group_path), "agents": ["product"],
            "dispatch": {
                "enabled": True,
                "agents": {"product": [{"prompt": "routine.md", "every": "1h"}]},
            },
        }},
    }


def test_due_schedule_submits_snapshot_then_touches_marker(tmp_path, monkeypatch):
    group_path, _, _ = _make_group(tmp_path)
    config_path = tmp_path / "config.yaml"
    config = _enabled_config(group_path)
    captured = []
    monkeypatch.setattr("agency.dispatch.run.submit_job", lambda spec, launcher=None: captured.append(spec) or object())

    run_dispatch_cycle(config, config_path)

    assert captured[0].trigger == "scheduled_prompt"
    assert captured[0].prompt_content == "do the thing"
    assert (group_path / "shared" / "logs" / ".last-product-routine").exists()


def test_schedule_does_not_touch_marker_when_submission_fails(tmp_path, monkeypatch):
    group_path, _, _ = _make_group(tmp_path)
    config = _enabled_config(group_path)
    monkeypatch.setattr("agency.dispatch.run.submit_job", lambda *args, **kwargs: (_ for _ in ()).throw(JobSubmissionError("no", tmp_path / "job")))
    run_dispatch_cycle(config, tmp_path / "config.yaml")
    assert not (group_path / "shared" / "logs" / ".last-product-routine").exists()
```

In `tests/test_agent_run.py`, change the success test to monkeypatch `agency.app.submit_job` and assert `trigger == "manual_prompt"`, snapshot content, agent, group, and response JSON `{"status": "started", "job_id": "job-1"}`. Replace `test_run_already_running_409` with:

```python
def test_run_allows_concurrent_jobs_for_same_agent(tmp_path, monkeypatch):
    _setup_group(tmp_path)
    calls = []
    monkeypatch.setattr("agency.app.submit_job", lambda spec: calls.append(spec) or SimpleNamespace(job_id=f"job-{len(calls)}"))
    client = TestClient(app)
    assert client.post("/test/agents/product/run", data={"prompt": "routine.md"}).status_code == 202
    assert client.post("/test/agents/product/run", data={"prompt": "routine.md"}).status_code == 202
    assert len(calls) == 2
```

- [ ] **Step 2: Run focused trigger tests and verify failures**

Run:

```text
python -m pytest tests/test_dispatch_run.py tests/test_agent_run.py -v
```

Expected: failures show dispatch still calls `run_agent_prompt` and manual routes still use FastAPI background tasks and reject a running agent.

- [ ] **Step 3: Migrate scheduled dispatch**

Change `run_dispatch_cycle` to:

```python
def run_dispatch_cycle(config: dict, config_path: Path | str, launcher=None) -> None:
```

At each due rule, read the prompt once, build a snapshot, and submit:

```python
prompt_path = group_path / "shared" / "prompts" / prompt
spec = JobSpec.create(
    config_path=Path(config_path),
    group_key=group_key,
    agent_name=agent_name,
    trigger="scheduled_prompt",
    prompt_source={"type": "saved_prompt", "path": str(prompt_path)},
    prompt_content=prompt_path.read_text(),
    timeout_override=agent_timeout,
)
try:
    submit_job(spec, launcher)
except (JobValidationError, JobSubmissionError, OSError) as error:
    log.error("  ERROR: could not submit %s/%s: %s", agent_name, prompt, error)
    continue
```

Touch `.event-*` or `.last-*` only after this block succeeds. Update `main()` to call `run_dispatch_cycle(config, Path(args.config).resolve())`. Delete `run_agent_prompt()` and its integration imports once no production call site remains.

- [ ] **Step 4: Migrate manual saved-prompt runs**

Remove `BackgroundTasks` from `agent_run`, delete the `is_agent_running` conflict check, and replace `background_tasks.add_task(...)` with:

```python
spec = JobSpec.create(
    config_path=CONFIG_PATH,
    group_key=group,
    agent_name=agent,
    trigger="manual_prompt",
    prompt_source={"type": "saved_prompt", "path": str(prompt_path)},
    prompt_content=prompt_path.read_text(),
    timeout_override=run_timeout,
)
try:
    handle = submit_job(spec)
except (JobValidationError, JobSubmissionError) as error:
    raise HTTPException(status_code=400, detail=str(error)) from error
return JSONResponse({"status": "started", "job_id": handle.job_id}, status_code=202)
```

Remove duplicated integration, agent configuration, sandbox, and log-directory resolution from this route; the worker owns those concerns.

- [ ] **Step 5: Run focused trigger tests**

Run:

```text
python -m pytest tests/test_dispatch_run.py tests/test_agent_run.py -v
```

Expected: all tests pass.

- [ ] **Step 6: Verify no migrated trigger calls integrations directly**

Run:

```text
rg "run_agent_prompt|background_tasks\.add_task|integration\.run" agency/dispatch/run.py agency/app.py
```

Expected: no `run_agent_prompt`; decision background-task matches may remain until Task 5; `integration.run` remains only in the historical decision function until Task 5.

- [ ] **Step 7: Commit saved-prompt trigger migration**

```bash
git add agency/dispatch/run.py agency/app.py tests/test_dispatch_run.py tests/test_agent_run.py
git commit -m "refactor(jobs): submit scheduled and manual runs"
```

---

### Task 5: Decision Executor Selection And Job Submission

**Files:**
- Create: `agency/jobs/prompts.py`
- Modify: `agency/app.py`
- Modify: `agency/templates/proposal_detail.html`
- Modify: `agency/templates/decision_detail.html`
- Modify: `tests/test_proposal_questions.py`
- Modify: `tests/test_execute_decision.py`
- Modify: `kb/data-formats.md`

**Interfaces:**
- Consumes: job submission, context validation, proposal/decision frontmatter, and template group context.
- Produces: `build_decision_prompt(proposal_body, answers) -> str`, decision metadata `execution_agent`, `execution_job_id`, `execution_job_history`, executor options in both forms, and synchronous launch error rendering.

- [ ] **Step 1: Write failing prompt, form, validation, creation, and retry tests**

Add this self-contained route fixture to `tests/test_proposal_questions.py` and reuse it from `tests/test_execute_decision.py`:

```python
from fastapi.testclient import TestClient
import agency.app as app_mod
from agency.app import app


def _setup_decision_group(tmp_path, monkeypatch, *, explicit_executor=True):
    group = tmp_path / "group"
    for agent in ("product", "engineer", "sdk-agent"):
        (group / agent).mkdir(parents=True)
    shared = group / "shared"
    for name in ("proposals", "decisions", "observations", "logs", "prompts"):
        (shared / name).mkdir(parents=True)
    metadata = {
        "origin_agent": "product", "status": "proposed",
        "questions": [{"id": "approve", "type": "boolean", "prompt": "Proceed?"}],
    }
    if explicit_executor:
        metadata["execution_agent"] = "engineer"
    proposal_path = shared / "proposals" / "change.md"
    proposal_path.write_text(
        "---\n" + yaml.safe_dump(metadata, sort_keys=False) + "---\n\nProposal body\n"
    )
    agents = [
        {"name": "product", "integration": "script", "integration_config": {"command": "echo ok"}},
        {"name": "engineer", "integration": "script", "integration_config": {"command": "echo ok"}},
        {"name": "sdk-agent", "integration": "sdk"},
    ]
    monkeypatch.setattr(app_mod, "CONFIG", {"groups": {"test": {"path": str(group), "agents": agents}}})
    monkeypatch.setattr(app_mod, "GROUPS", {"test": {
        "key": "test", "name": "Test", "path": group,
        "agents": [item["name"] for item in agents], "_agents_normalized": agents,
    }})
    return TestClient(app), proposal_path, shared / "decisions" / "change.md"


def test_proposal_form_defaults_executor_to_explicit_execution_agent(tmp_path, monkeypatch):
    client, _, _ = _setup_decision_group(tmp_path, monkeypatch)
    response = client.get("/test/proposals/change")
    assert response.status_code == 200
    assert '<option value="engineer" selected>' in response.text


def test_historical_proposal_defaults_executor_to_origin_agent(tmp_path, monkeypatch):
    client, _, _ = _setup_decision_group(tmp_path, monkeypatch, explicit_executor=False)
    response = client.get("/test/proposals/change")
    assert '<option value="product" selected>' in response.text


def test_invalid_executor_rerenders_without_creating_decision(tmp_path, monkeypatch):
    client, proposal_path, decision_path = _setup_decision_group(tmp_path, monkeypatch)
    response = client.post(
        "/test/proposals/change/decide",
        data={"answer_approve": "approved", "execution_agent": "sdk-agent"},
    )
    assert response.status_code == 400
    assert "does not support execution" in response.text
    assert not decision_path.exists()
    assert "status: proposed" in proposal_path.read_text()
```

Add to `tests/test_execute_decision.py`:

```python
from test_proposal_questions import _setup_decision_group


def test_decide_submits_embedded_snapshot_and_persists_job_id(tmp_path, monkeypatch):
    client, _, decision_path = _setup_decision_group(tmp_path, monkeypatch)
    captured = []
    monkeypatch.setattr("agency.app.submit_job", lambda spec: captured.append(spec) or SimpleNamespace(job_id=spec.job_id))
    response = client.post(
        "/test/proposals/change/decide",
        data={"answer_approve": "approved", "execution_agent": "engineer"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "approved" in captured[0].prompt_content
    assert "Proposal body" in captured[0].prompt_content
    metadata, _ = app_mod.parse_frontmatter(decision_path.read_text())
    assert metadata["execution_agent"] == "engineer"
    assert metadata["execution_job_id"] == captured[0].job_id
    assert metadata["execution_job_history"] == []


def test_retry_defaults_to_persisted_executor_and_appends_history(tmp_path, monkeypatch):
    client, _, decision_path = _setup_decision_group(tmp_path, monkeypatch)
    decision_path.write_text(
        "---\nproposal: change.md\nexecution_status: failed\n"
        "execution_agent: engineer\nexecution_job_id: old-job\n"
        "execution_job_history: []\n---\n"
    )
    captured = []
    monkeypatch.setattr("agency.app.submit_job", lambda spec: captured.append(spec) or SimpleNamespace(job_id=spec.job_id))
    response = client.post(
        "/test/decisions/change/retry",
        data={"execution_agent": "engineer"}, follow_redirects=False,
    )
    metadata, _ = app_mod.parse_frontmatter(decision_path.read_text())
    assert response.status_code == 303
    assert metadata["execution_job_history"] == ["old-job"]
    assert metadata["execution_job_id"] == captured[0].job_id


def test_launch_failure_rolls_back_new_decision(tmp_path, monkeypatch):
    client, proposal_path, decision_path = _setup_decision_group(tmp_path, monkeypatch)
    monkeypatch.setattr("agency.app.submit_job", lambda spec: (_ for _ in ()).throw(JobSubmissionError("spawn denied", proposal_path)))
    response = client.post(
        "/test/proposals/change/decide",
        data={"answer_approve": "approved", "execution_agent": "engineer"},
    )
    assert response.status_code == 400
    assert "spawn denied" in response.text
    assert "status: proposed" in proposal_path.read_text()
    assert not decision_path.exists()
```

- [ ] **Step 2: Run decision tests and verify failures**

Run:

```text
python -m pytest tests/test_proposal_questions.py tests/test_execute_decision.py -v
```

Expected: failures show missing executor context, no job IDs, and historical `origin_agent` background dispatch.

- [ ] **Step 3: Implement immutable decision prompt construction**

Create `agency/jobs/prompts.py`:

```python
import yaml


def build_decision_prompt(proposal_body: str, answers: dict) -> str:
    rendered_answers = yaml.safe_dump(answers, sort_keys=False).strip()
    return (
        "A human has decided this proposal. Act on the decision below.\n\n"
        "Proposal:\n"
        f"{proposal_body.strip()}\n\n"
        "Answers:\n"
        f"{rendered_answers}\n\n"
        "If approved or accepted, execute the proposed action. If deferred, "
        "acknowledge it without doing the deferred work. If rejected, close the "
        "loop without proceeding. Use choice and free-response answers as binding "
        "implementation guidance. Do not modify the Agency decision file."
    )
```

Test exact inclusion of proposal text and YAML answers, then ensure changing source files after `JobSpec.create()` does not change `spec.prompt_content`.

- [ ] **Step 4: Add executor-option and reusable proposal rendering helpers**

In `agency/app.py`, add a helper that lists only configured agents whose directories exist and integrations support execution. Add a proposal-context helper used by both GET and POST so validation errors can render the same page:

```python
def execution_agent_options(g: dict) -> list[str]:
    options = []
    for name in g["agents"]:
        try:
            resolve_agent_dir(g, name)
            if get_agent_integration(g, name).supports_execution:
                options.append(name)
        except (HTTPException, KeyError):
            continue
    return options
```

The proposal template context must include `execution_agents`, `selected_execution_agent`, and `decision_error`. Default selection is `meta.get("execution_agent") or meta.get("origin_agent", "")`.

- [ ] **Step 5: Render executor controls and errors**

In `agency/templates/proposal_detail.html`, immediately inside the unanswered form, render:

```html
{% if decision_error %}
<div class="mb-4 p-3 border border-red-300 bg-red-50 text-red-700 rounded-md" role="alert">{{ decision_error }}</div>
{% endif %}
<label for="execution-agent" class="block text-sm font-medium text-gray-700 mb-1">Implement with</label>
<select id="execution-agent" name="execution_agent" required class="w-full border border-gray-300 rounded-md px-3 py-2 mb-4">
  {% for agent_name in execution_agents %}
  <option value="{{ agent_name }}" {% if agent_name == selected_execution_agent %}selected{% endif %}>{{ agent_name }}</option>
  {% endfor %}
</select>
```

In `agency/templates/decision_detail.html`, show `execution_job_id` as muted monospace metadata and place an executor selector inside the failed retry form. The selector defaults to persisted `execution_agent`, and options come from `execution_agents`.

- [ ] **Step 6: Replace decision background execution with validated job submission**

Remove `BackgroundTasks` from `proposal_decide` and `decision_retry`. Delete `execute_decision()` after all tests use `execute_job()`.

For creation:

1. Read selected `execution_agent` from the form.
2. Reject it unless it appears in `execution_agent_options(g)`; render the proposal with HTTP 400 and the capability error.
3. Generate `JobSpec` before writing the decision so its ID can be persisted.
4. Atomically create the decision with `execution_status: pending`, `execution_agent`, `execution_job_id`, and `execution_job_history: []`.
5. Call `submit_job(spec)`.
6. If submission raises, delete only the newly created decision and render HTTP 400; leave proposal status `proposed`.
7. After successful launch, set proposal status to `decided` and redirect 303.

The `decision_context` must contain absolute `decision_path` and `proposal_path`. The prompt snapshot comes from `build_decision_prompt(proposal_body, answers)` and never asks the worker to re-read these files.

For retry:

1. Default the form to `decision.execution_agent`; for a historical decision use proposal `execution_agent`, then `origin_agent`.
2. Validate the submitted executor before changing decision fields.
3. Build the `decision_retry` spec so its job ID is known before launch.
4. Save the complete original decision text, then atomically append the previous nonempty `execution_job_id` once to `execution_job_history`, store the new ID and executor, clear stale summary/changed-files fields, and set `execution_status: pending`.
5. Call `submit_job(spec)`. This ordering ensures even a very fast worker sees its own job ID and may safely project `running` or completion state.
6. On submission failure, atomically restore the exact original decision text and return HTTP 400 with a useful error. Never leave the failed submission as the current job.

- [ ] **Step 7: Document frontmatter fields**

Update `kb/data-formats.md` proposal fields with optional `execution_agent`, and decision fields with `execution_agent`, `execution_job_id`, and `execution_job_history`. State the historical fallback order and that retries retain prior IDs.

- [ ] **Step 8: Run decision and template tests**

Run:

```text
python -m pytest tests/test_proposal_questions.py tests/test_execute_decision.py tests/test_dashboard.py -v
```

Expected: all tests pass.

- [ ] **Step 9: Verify all trigger paths use the jobs API**

Run:

```text
rg "integration\.run|background_tasks\.add_task|execute_decision|run_agent_prompt" agency
```

Expected: `integration.run` appears in `agency/jobs/execution.py` only; the other three patterns have no matches.

- [ ] **Step 10: Commit decision job migration**

```bash
git add agency/app.py agency/jobs/prompts.py agency/templates/proposal_detail.html agency/templates/decision_detail.html tests/test_proposal_questions.py tests/test_execute_decision.py kb/data-formats.md
git commit -m "feat(decisions): select and submit execution agents"
```

---

### Task 6: Running-State Queries And Conservative Reconciliation

**Files:**
- Create: `agency/jobs/reconciliation.py`
- Modify: `agency/jobs/store.py`
- Modify: `agency/jobs/__init__.py`
- Modify: `agency/app.py`
- Create: `tests/test_job_reconciliation.py`
- Modify: `tests/test_agent_run.py`

**Interfaces:**
- Consumes: persisted job records, decision job IDs, and application startup lifespan.
- Produces: `worker_alive(pid) -> bool | None`, `reconcile_jobs(groups) -> ReconciliationResult`, and agent running state derived from active job records.

- [ ] **Step 1: Write failing liveness and reconciliation tests**

Create `tests/test_job_reconciliation.py`:

```python
from dataclasses import replace
from pathlib import Path

from agency.jobs.models import JobRecord, JobSpec
from agency.jobs.reconciliation import reconcile_jobs, worker_alive
from agency.jobs.store import job_path, read_job, write_job


def running_decision_job(tmp_path: Path, pid: int = 999999):
    group = tmp_path / "group"
    decision = group / "shared" / "decisions" / "change.md"
    decision.parent.mkdir(parents=True)
    spec = JobSpec.create(
        config_path=tmp_path / "config.yaml", group_key="test", agent_name="product",
        trigger="decision", prompt_source={"type": "decision"}, prompt_content="run",
        decision_context={"decision_path": str(decision), "proposal_path": "proposal.md"},
    )
    decision.write_text(f"---\nexecution_status: running\nexecution_job_id: {spec.job_id}\n---\n")
    path = job_path(group, spec.job_id)
    write_job(path, replace(JobRecord.from_spec(spec), status="running", worker_pid=pid))
    return group, decision, path


def test_reconcile_leaves_live_worker_running(tmp_path, monkeypatch):
    group, decision, path = running_decision_job(tmp_path)
    monkeypatch.setattr("agency.jobs.reconciliation.worker_alive", lambda pid: True)
    result = reconcile_jobs({"test": {"path": str(group)}})
    assert result.left_running == 1
    assert read_job(path).status == "running"
    assert "execution_status: running" in decision.read_text()


def test_reconcile_marks_confirmed_dead_worker_failed(tmp_path, monkeypatch):
    group, decision, path = running_decision_job(tmp_path)
    monkeypatch.setattr("agency.jobs.reconciliation.worker_alive", lambda pid: False)
    result = reconcile_jobs({"test": {"path": str(group)}})
    assert result.failed == 1
    assert read_job(path).status == "failed"
    assert "execution_status: failed" in decision.read_text()


def test_reconcile_leaves_uncertain_worker_running(tmp_path, monkeypatch):
    group, _, path = running_decision_job(tmp_path)
    monkeypatch.setattr("agency.jobs.reconciliation.worker_alive", lambda pid: None)
    reconcile_jobs({"test": {"path": str(group)}})
    assert read_job(path).status == "running"


def test_historical_running_decision_without_job_id_is_not_failed(tmp_path):
    group = tmp_path / "group"
    decision = group / "shared" / "decisions" / "historical.md"
    decision.parent.mkdir(parents=True)
    decision.write_text("---\nexecution_status: running\n---\n")
    reconcile_jobs({"test": {"path": str(group)}})
    assert "execution_status: running" in decision.read_text()
```

Update the agent-profile test to create two queued/running job records and assert `is_agent_running(g, "product")` is true without `.running-product`.

- [ ] **Step 2: Run reconciliation tests and verify missing API failures**

Run:

```text
python -m pytest tests/test_job_reconciliation.py tests/test_agent_run.py -v
```

Expected: collection fails because reconciliation APIs do not exist; the running-state test fails because app logic still checks `.running-product`.

- [ ] **Step 3: Implement tri-state platform liveness**

Create `agency/jobs/reconciliation.py` with:

```python
def worker_alive(pid: int | None) -> bool | None:
    if not pid or pid <= 0:
        return None
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return None
        return True
    try:
        import win32api
        import win32con
        import win32process
        handle = win32api.OpenProcess(win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        try:
            return win32process.GetExitCodeProcess(handle) == win32con.STILL_ACTIVE
        finally:
            handle.Close()
    except Exception:
        return None
```

Return `None` whenever absence is not confirmed. Do not add an age-based failure threshold.

- [ ] **Step 4: Implement reconciliation and guarded decision failure projection**

Add `ReconciliationResult(failed, left_running)` and scan only `shared/jobs/*.yaml` records with `status == "running"`. If `worker_alive` is `False`, atomically transition the record to failed with completion timestamp and summary `Worker process (PID N) was not found.` Then call the guarded `project_decision(record)`. Leave `True` and `None` untouched. Ignore malformed records after logging a warning.

Do not scan decisions and infer orphaning from dashboard startup. Historical running decisions without job IDs are untouched because their worker liveness cannot be established.

- [ ] **Step 5: Replace marker-based running state and startup recovery**

Change `is_agent_running(g, agent_name, timeout=1800)` to:

```python
def is_agent_running(g: dict, agent_name: str, timeout: int = 1800) -> bool:
    return bool(active_jobs(g["path"], agent_name))
```

Keep the `timeout` parameter temporarily for call-site compatibility but document that persisted jobs are authoritative. Remove historical `.running-{agent}` creation and recovery code. In lifespan call `reconcile_jobs(GROUPS)` instead of `recover_orphaned_executions()`.

Export `reconcile_jobs` and `active_jobs` from `agency/jobs/__init__.py` only if app call sites need them; keep platform helpers internal unless tests import them directly.

- [ ] **Step 6: Run focused reconciliation and UI tests**

Run:

```text
python -m pytest tests/test_job_reconciliation.py tests/test_agent_run.py tests/test_dashboard.py -v
```

Expected: all tests pass.

- [ ] **Step 7: Commit reconciliation**

```bash
git add agency/jobs/reconciliation.py agency/jobs/store.py agency/jobs/__init__.py agency/app.py tests/test_job_reconciliation.py tests/test_agent_run.py
git commit -m "feat(jobs): reconcile detached worker state"
```

---

### Task 7: Prove Process Isolation And Run Full Verification

**Files:**
- Create: `tests/test_job_detached_process.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: installed editable package, script integration, `DetachedProcessLauncher`, worker CLI, and durable job store.
- Produces: an end-to-end regression test proving worker lifetime is independent from submitter lifetime and concise operational documentation.

- [ ] **Step 1: Write the failing subprocess isolation test**

Create `tests/test_job_detached_process.py`. The helper blocks on a gate file; the test opens that gate only after the submitter has exited. This proves the worker, not the submitter, owns the integration process.

Use this complete test body:

```python
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time

import yaml

from agency.jobs.store import read_job


def _shell_command(arguments):
    if os.name == "nt":
        return subprocess.list2cmdline([str(item) for item in arguments])
    return shlex.join(str(item) for item in arguments)


def test_detached_worker_survives_submitter_exit(tmp_path):
    repository = Path(__file__).resolve().parents[1]
    group_path = tmp_path / "group"
    agent_dir = group_path / "product"
    agent_dir.mkdir(parents=True)
    gate = tmp_path / "continue"
    sentinel = tmp_path / "completed"
    helper = tmp_path / "agent_helper.py"
    helper.write_text(
        "import pathlib, sys, time\n"
        "gate = pathlib.Path(sys.argv[1])\n"
        "deadline = time.monotonic() + 10\n"
        "while not gate.exists() and time.monotonic() < deadline:\n"
        "    time.sleep(0.05)\n"
        "if not gate.exists():\n"
        "    raise SystemExit(2)\n"
        "pathlib.Path(sys.argv[2]).write_text('done')\n"
    )
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump({"groups": {"test": {
        "name": "Test", "path": str(group_path),
        "agents": [{
            "name": "product", "integration": "script",
            "integration_config": {
                "command": _shell_command([sys.executable, helper, gate, sentinel]),
            },
        }],
    }}}))
    job_id_file = tmp_path / "job-id"
    parent_pid_file = tmp_path / "parent-pid"
    submitter_script = tmp_path / "submitter.py"
    submitter_script.write_text(
        "import os, pathlib, sys\n"
        "from agency.jobs import JobSpec, submit_job\n"
        "config, job_id_file, pid_file = map(pathlib.Path, sys.argv[1:])\n"
        "spec = JobSpec.create(config_path=config, group_key='test', "
        "agent_name='product', trigger='manual_prompt', "
        "prompt_source={'type': 'test'}, prompt_content='run')\n"
        "handle = submit_job(spec)\n"
        "job_id_file.write_text(handle.job_id)\n"
        "pid_file.write_text(str(os.getpid()))\n"
    )
    submitter = subprocess.Popen(
        [sys.executable, str(submitter_script), str(config_path),
         str(job_id_file), str(parent_pid_file)],
        cwd=repository,
    )
    assert submitter.wait(timeout=10) == 0
    submitter_pid = int(parent_pid_file.read_text())
    assert submitter.poll() == 0

    job_id = job_id_file.read_text().strip()
    record_path = group_path / "shared" / "jobs" / f"{job_id}.yaml"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and (
        not record_path.exists() or read_job(record_path).status == "queued"
    ):
        time.sleep(0.05)
    running = read_job(record_path)
    assert running.status == "running"
    assert running.worker_pid != submitter_pid
    assert not sentinel.exists()

    gate.touch()
    while time.monotonic() < deadline and read_job(record_path).status == "running":
        time.sleep(0.05)
    record = read_job(record_path)
    assert sentinel.read_text() == "done"
    assert record.status == "complete"
```

Do not start a dashboard server and do not skip either supported platform. The script integration already uses the platform shell; `_shell_command` supplies platform-correct quoting.

- [ ] **Step 2: Run the isolation test and verify it fails before fixture completion**

Run:

```text
python -m pytest tests/test_job_detached_process.py -v
```

Expected on the first pass: FAIL if the worker remains attached, cannot import the package, inherits blocking streams, or does not update durable status. Fix the demonstrated launcher or worker defect, rerun this exact test, and require PASS.

- [ ] **Step 3: Document unified job behavior**

Add a short `Agent jobs` subsection to `README.md` stating:

```markdown
### Agent jobs

Scheduled prompts, manual prompt runs, approved decisions, and decision retries all create durable records under `<group>/shared/jobs/`. Each job runs in a detached worker process, so stopping or restarting the dashboard does not stop the agent. Job records contain prompt snapshots and may contain operational paths; treat the group's `shared/` directory as private application data.
```

Mention that concurrent jobs for one agent are allowed and that proposal authors can set optional `execution_agent` frontmatter.

- [ ] **Step 4: Run focused job and trigger tests**

Run:

```text
python -m pytest tests/test_job_models.py tests/test_job_submission.py tests/test_job_execution.py tests/test_job_reconciliation.py tests/test_job_detached_process.py tests/test_dispatch_run.py tests/test_agent_run.py tests/test_execute_decision.py tests/test_proposal_questions.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Run the complete suite**

Run:

```text
python -m pytest tests/ -q
```

Expected: all tests pass with no collection errors, failures, or errors.

- [ ] **Step 6: Check architecture invariants and formatting**

Run:

```text
rg "integration\.run" agency
rg "background_tasks\.add_task|execute_decision|run_agent_prompt|\.running-" agency
python -m compileall -q agency
git diff --check
```

Expected:

- `integration.run` appears only in `agency/jobs/execution.py`.
- The historical orchestration and running-marker search produces no matches.
- `compileall` exits zero.
- `git diff --check` exits zero.

- [ ] **Step 7: Commit process proof and documentation**

```bash
git add tests/test_job_detached_process.py README.md
git commit -m "test(jobs): prove detached worker isolation"
```

- [ ] **Step 8: Review final history and worktree**

Run:

```text
git status --short
git log --oneline -7
```

Expected: worktree is clean and history contains one focused commit for each task.
