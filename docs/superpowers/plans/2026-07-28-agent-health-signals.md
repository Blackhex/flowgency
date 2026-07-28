# Agent Health Signals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the dashboard's elapsed-time agent health color with a four-state signal that distinguishes "never run" (gray) from a broken promise (red), where red means a failed job or a routine the dispatch runner did not fire on schedule.

**Architecture:** Marker-filename construction and `every`-interval parsing move out of the dispatch runner into `agency/dispatch/schedule.py` so the runner and the dashboard cannot drift. A new `agency/health.py` owns the health model as pure functions over plain values. `agency/jobs/store.py` gains `latest_terminal_job`. `agency/app.py` wires both fleet builders to the new module, repairs `compute_next_run_detail`, and publishes three partitioned counters that `home.html` renders.

**Tech Stack:** Python 3, FastAPI, Jinja2, Pydantic, pytest, Tailwind utility classes in templates.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-28-agent-health-signals-design.md`. Read it before Task 1.
- Work inside the worktree `.worktrees/agent-health-signals` on branch `agent-health-signals`. Run every command from that directory. Never commit to `master` or to the main checkout at `C:/Projekty/christag-agency`.
- The interpreter is `python` on `PATH`; this checkout has no `.venv`. Every `.venv/Scripts/python` below is to be read as `python`. Test command: `python -m pytest tests/ -q`. Single file: `python -m pytest tests/test_health.py -q`. Run from the worktree root so the local `agency` and `tests` packages resolve.
- Health values are exactly the strings `"green"`, `"amber"`, `"gray"`, `"red"`. No other spellings.
- Schedule states are exactly the strings `"overdue"` and `"due"`, or `None`.
- Grace window is `dispatch.interval + 2` minutes; the default interval is `15`, so the default grace is 17 minutes.
- Time is read through `agency.clock.now()`, never `datetime.now()` directly, so `AGENCY_FIXED_NOW` controls tests.
- Every commit message follows Conventional Commits with an imperative, lowercase, period-free description of at most 72 characters including the prefix.
- Do not stage or modify `config.yaml`, `config.yaml.lock`, or anything under `C:/Projekty/Agents/`.

---

### Task 1: Shared dispatch schedule primitives

**Files:**
- Create: `agency/dispatch/schedule.py`
- Modify: `agency/dispatch/run.py` (remove `_marker_safe` and the `re` import, rewrite `check_every_rule` on the shared parser, use the marker helpers at the four marker sites)
- Test: `tests/test_dispatch_schedule.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `marker_safe(value: str) -> str`
  - `parse_every(value: str | None) -> timedelta | None`
  - `every_marker_path(logs_root: Path, agent_name: str, routine_id: str) -> Path`
  - `at_marker_path(logs_root: Path, agent_name: str, routine_id: str, day: str) -> Path` where `day` is a `YYYY-MM-DD` string

This module is the single definition of everything the runner and the dashboard must agree on about schedules. Three copies of the `every` parse exist today, in `check_every_rule` and in `compute_next_run_detail`; Tasks 3 and 6 consume this one instead of adding a fourth.

- [ ] **Step 1: Write the failing test**

Create `tests/test_dispatch_schedule.py`:

```python
from datetime import timedelta
from pathlib import Path

import pytest

from agency.dispatch.schedule import (
    at_marker_path,
    every_marker_path,
    marker_safe,
    parse_every,
)


def test_marker_safe_replaces_unsafe_runs_with_a_single_hyphen():
    assert marker_safe("daily review/now") == "daily-review-now"


def test_marker_safe_keeps_dots_underscores_and_hyphens():
    assert marker_safe("a.b_c-d") == "a.b_c-d"


def test_marker_safe_strips_leading_and_trailing_dots_and_hyphens():
    assert marker_safe("--weekly..") == "weekly"


def test_marker_safe_falls_back_to_item_when_nothing_survives():
    assert marker_safe("///") == "item"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("30m", timedelta(minutes=30)),
        ("6h", timedelta(hours=6)),
        ("7d", timedelta(days=7)),
        ("0m", timedelta(0)),
    ],
)
def test_parse_every_reads_each_unit(value, expected):
    assert parse_every(value) == expected


@pytest.mark.parametrize("value", [None, "", "soon", "6", "h", "6 h", "6w", "-1d"])
def test_parse_every_rejects_malformed_intervals(value):
    assert parse_every(value) is None


def test_every_marker_path_sits_at_the_logs_root():
    path = every_marker_path(Path("/logs"), "product", "authority-audit")
    assert path == Path("/logs/.last-product-authority-audit")


def test_at_marker_path_sits_in_the_day_directory_and_repeats_the_day():
    path = at_marker_path(Path("/logs"), "product", "suite-health", "2026-07-28")
    assert path == Path("/logs/2026-07-28/.event-product-suite-health-2026-07-28")


def test_marker_paths_sanitize_both_identifiers():
    every = every_marker_path(Path("/logs"), "Team Lead", "diff review")
    at = at_marker_path(Path("/logs"), "Team Lead", "diff review", "2026-07-28")
    assert every.name == ".last-Team-Lead-diff-review"
    assert at.name == ".event-Team-Lead-diff-review-2026-07-28"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_dispatch_schedule.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'agency.dispatch.schedule'`

- [ ] **Step 3: Write the module**

Create `agency/dispatch/schedule.py`:

```python
"""Schedule primitives shared by the dispatch runner and the dashboard."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
import re

_UNSAFE = re.compile(r"[^a-zA-Z0-9._-]+")
_EVERY = re.compile(r"(\d+)(m|h|d)")
_UNIT_SECONDS = {"m": 60, "h": 3600, "d": 86400}


def marker_safe(value: str) -> str:
    return _UNSAFE.sub("-", value).strip(".-") or "item"


def parse_every(value: str | None) -> timedelta | None:
    """Return the period of an ``every`` schedule, or None when malformed."""
    match = _EVERY.fullmatch(value or "")
    if match is None:
        return None
    return timedelta(seconds=int(match.group(1)) * _UNIT_SECONDS[match.group(2)])


def every_marker_path(logs_root: Path, agent_name: str, routine_id: str) -> Path:
    name = f".last-{marker_safe(agent_name)}-{marker_safe(routine_id)}"
    return Path(logs_root) / name


def at_marker_path(
    logs_root: Path,
    agent_name: str,
    routine_id: str,
    day: str,
) -> Path:
    name = f".event-{marker_safe(agent_name)}-{marker_safe(routine_id)}-{day}"
    return Path(logs_root) / day / name
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_dispatch_schedule.py -q`
Expected: PASS, 20 passed

- [ ] **Step 5: Point the runner at the shared module**

In `agency/dispatch/run.py`, delete the now-unused `import re` from the imports, and add below `from agency.configuration import resolve_group_paths`:

```python
from agency.dispatch.schedule import at_marker_path, every_marker_path, parse_every
```

Replace `check_every_rule` entirely:

```python
def check_every_rule(marker_file: Path, interval_str: str) -> bool:
    """Check if enough time has elapsed since marker file mtime."""
    match = re.fullmatch(r"(\d+)(m|h|d)", interval_str)
    if not match:
        log.warning("Invalid every interval: %s", interval_str)
        return False
    val = int(match.group(1))
    unit = match.group(2)
    if unit == "m":
        seconds = val * 60
    elif unit == "h":
        seconds = val * 3600
    else:
        seconds = val * 86400
    if not marker_file.exists():
        return True
    elapsed = time.time() - marker_file.stat().st_mtime
    return elapsed >= seconds
```

with:

```python
def check_every_rule(marker_file: Path, interval_str: str) -> bool:
    """Check if enough time has elapsed since marker file mtime."""
    period = parse_every(interval_str)
    if period is None:
        log.warning("Invalid every interval: %s", interval_str)
        return False
    if not marker_file.exists():
        return True
    elapsed = time.time() - marker_file.stat().st_mtime
    return elapsed >= period.total_seconds()
```

Delete this function entirely:

```python
def _marker_safe(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip(".-") or "item"
```

Inside the routine loop, delete both of these lines:

```python
                marker_id = _marker_safe(routine.id)
                agent_marker = _marker_safe(agent_name)
```

Then replace the `should_run` block:

```python
                should_run = False
                if at_time:
                    event_marker = log_dir / f".event-{agent_marker}-{marker_id}-{today}"
                    if event_marker.exists():
                        continue
                    if check_at_rule(at_time, interval=interval):
                        should_run = True
                elif every_val:
                    every_marker = logs_root / f".last-{agent_marker}-{marker_id}"
                    if check_every_rule(every_marker, every_val):
                        should_run = True
```

with:

```python
                should_run = False
                if at_time:
                    event_marker = at_marker_path(logs_root, agent_name, routine.id, today)
                    if event_marker.exists():
                        continue
                    if check_at_rule(at_time, interval=interval):
                        should_run = True
                elif every_val:
                    every_marker = every_marker_path(logs_root, agent_name, routine.id)
                    if check_every_rule(every_marker, every_val):
                        should_run = True
```

And replace the touch block:

```python
                    # Touch markers
                    if at_time:
                        (log_dir / f".event-{agent_marker}-{marker_id}-{today}").touch()
                    elif every_val:
                        (logs_root / f".last-{agent_marker}-{marker_id}").touch()
```

with:

```python
                    # Touch markers
                    if at_time:
                        at_marker_path(logs_root, agent_name, routine.id, today).touch()
                    elif every_val:
                        every_marker_path(logs_root, agent_name, routine.id).touch()
```

`log_dir` is still used for the daily-limit glob, so leave its assignment in place.

- [ ] **Step 6: Run the dispatch tests to verify no behavior changed**

Run: `.venv/Scripts/python -m pytest tests/test_dispatch_run.py tests/test_dispatch_schedule.py -q`
Expected: PASS, all tests pass

- [ ] **Step 7: Commit**

```bash
git add agency/dispatch/schedule.py agency/dispatch/run.py tests/test_dispatch_schedule.py
git commit -m "refactor(dispatch): extract shared schedule primitives"
```

---

### Task 2: Newest terminal job lookup

**Files:**
- Modify: `agency/jobs/store.py` (add `TERMINAL_STATUSES`, `_iter_job_records`, `latest_terminal_job`; rewrite `active_jobs` to use the shared iterator)
- Test: `tests/test_job_store_terminal.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `latest_terminal_job(job_paths: Path | tuple[Path, ...], agent_name: str | None = None) -> JobRecord | None`

**Background the implementer needs:** `JobRecord` has `status`, `completed_at`, `started_at`, and `spec` (a `JobSpec` with `agent_name`, `job_id`, `created_at`). Terminal statuses are `complete`, `failed`, and `cancelled`. A cancelled record may have no `completed_at`, hence the fallback sort key.

- [ ] **Step 1: Write the failing test**

Create `tests/test_job_store_terminal.py`:

```python
from dataclasses import replace
from pathlib import Path

import pytest

from agency.jobs.authority import JobStore
from agency.jobs.models import BlueprintRef, JobRecord, JobSpec, MemoryBinding, RuntimePolicySnapshot
from agency.jobs.store import latest_terminal_job, write_job


def _spec(tmp_path: Path, job_id: str, agent_name: str, created_at: str) -> JobSpec:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("schema_version: 4\ngroups: {}\n", encoding="utf-8")
    return JobSpec(
        schema_version=3,
        job_id=job_id,
        config_path=str(config_path.resolve()),
        config_revision="cfg-1",
        group_key="grp",
        group_root=str(tmp_path.resolve()),
        agent_name=agent_name,
        workspace_root=str(tmp_path.resolve()),
        trigger="manual_prompt",
        integration_name="script",
        integration_config={},
        blueprint=BlueprintRef(
            key="product-blueprint",
            source_digest="digest-1",
            integration="script",
            projector_version="v1",
            cache_path=str((tmp_path / "cache" / "entry.py").resolve()),
        ),
        routine_id="daily-review",
        skill="daily-review",
        skill_arguments=(),
        task_input="# Routine\n",
        runtime_policy=RuntimePolicySnapshot(
            timeout=1800,
            sandbox_mode="unrestricted",
            sandbox_roots=(),
            tool_mode="all",
            tool_names=(),
        ),
        memory=MemoryBinding(
            selector={"scope": "agent", "version": 1, "group": "grp", "agent": agent_name},
            canonical_json='{"scope":"agent"}',
            memory_hash="memory-hash-1",
            path=str((tmp_path / "memory" / "memory-hash-1").resolve()),
        ),
        trigger_context=None,
        prompt_source={"type": "prompt", "path": "routine.md"},
        timeout_override=None,
        created_at=created_at,
    )


@pytest.fixture
def store(tmp_path):
    memory_root = tmp_path / "memory"
    job_store = JobStore(memory_root)
    job_store.group_root("grp").mkdir(parents=True, exist_ok=True)
    return job_store


def _write(store, tmp_path, job_id, *, agent_name="product", status, completed_at=None, created_at="2026-07-20T00:00:00+00:00"):
    spec = _spec(tmp_path, job_id, agent_name, created_at)
    record = replace(
        JobRecord.from_spec(spec),
        status=status,
        completed_at=completed_at,
    )
    write_job(store.path("grp", job_id), record)


def test_returns_none_when_no_jobs_exist(store):
    assert latest_terminal_job(tuple(store.paths("grp")), "product") is None


def test_ignores_active_records(store, tmp_path):
    _write(store, tmp_path, "job-running", status="running")
    _write(store, tmp_path, "job-queued", status="queued")
    assert latest_terminal_job(tuple(store.paths("grp")), "product") is None


def test_returns_the_newest_terminal_record_by_completed_at(store, tmp_path):
    _write(store, tmp_path, "job-old", status="complete", completed_at="2026-07-20T10:00:00+00:00")
    _write(store, tmp_path, "job-new", status="failed", completed_at="2026-07-21T10:00:00+00:00")
    record = latest_terminal_job(tuple(store.paths("grp")), "product")
    assert record is not None
    assert record.spec.job_id == "job-new"
    assert record.status == "failed"


def test_falls_back_to_created_at_when_completed_at_is_absent(store, tmp_path):
    _write(store, tmp_path, "job-early", status="cancelled", created_at="2026-07-20T00:00:00+00:00")
    _write(store, tmp_path, "job-late", status="cancelled", created_at="2026-07-22T00:00:00+00:00")
    record = latest_terminal_job(tuple(store.paths("grp")), "product")
    assert record is not None
    assert record.spec.job_id == "job-late"


def test_filters_by_agent_name(store, tmp_path):
    _write(store, tmp_path, "job-other", agent_name="writer", status="failed", completed_at="2026-07-22T10:00:00+00:00")
    _write(store, tmp_path, "job-mine", status="complete", completed_at="2026-07-21T10:00:00+00:00")
    record = latest_terminal_job(tuple(store.paths("grp")), "product")
    assert record is not None
    assert record.spec.job_id == "job-mine"


def test_skips_unreadable_records(store, tmp_path):
    _write(store, tmp_path, "job-good", status="complete", completed_at="2026-07-21T10:00:00+00:00")
    broken = store.path("grp", "job-broken")
    broken.write_text("not: [valid", encoding="utf-8")
    record = latest_terminal_job(tuple(store.paths("grp")), "product")
    assert record is not None
    assert record.spec.job_id == "job-good"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_job_store_terminal.py -q`
Expected: FAIL with `ImportError: cannot import name 'latest_terminal_job' from 'agency.jobs.store'`

- [ ] **Step 3: Implement in `agency/jobs/store.py`**

Replace the whole `active_jobs` function at the end of the file:

```python
def active_jobs(
    job_paths: Path | tuple[Path, ...],
    agent_name: str | None = None,
) -> list[JobRecord]:
    """Return persisted active jobs, optionally for one agent."""
    if isinstance(job_paths, Path):
        paths = tuple(sorted(job_paths.glob("*.yaml"))) if job_paths.is_dir() else ()
    else:
        paths = tuple(job_paths)
    records = []
    for path in paths:
        try:
            record = read_job(path)
        except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError):
            continue
        if record.status not in {"queued", "waiting_for_memory", "running"}:
            continue
        if agent_name is not None and record.spec.agent_name != agent_name:
            continue
        records.append(record)
    return records
```

with:

```python
ACTIVE_STATUSES = frozenset({"queued", "waiting_for_memory", "running"})
TERMINAL_STATUSES = frozenset({"complete", "failed", "cancelled"})


def _iter_job_records(
    job_paths: Path | tuple[Path, ...],
    agent_name: str | None,
):
    if isinstance(job_paths, Path):
        paths = tuple(sorted(job_paths.glob("*.yaml"))) if job_paths.is_dir() else ()
    else:
        paths = tuple(job_paths)
    for path in paths:
        try:
            record = read_job(path)
        except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError):
            continue
        if agent_name is not None and record.spec.agent_name != agent_name:
            continue
        yield record


def active_jobs(
    job_paths: Path | tuple[Path, ...],
    agent_name: str | None = None,
) -> list[JobRecord]:
    """Return persisted active jobs, optionally for one agent."""
    return [
        record
        for record in _iter_job_records(job_paths, agent_name)
        if record.status in ACTIVE_STATUSES
    ]


def latest_terminal_job(
    job_paths: Path | tuple[Path, ...],
    agent_name: str | None = None,
) -> JobRecord | None:
    """Return the newest finished job record, optionally for one agent."""
    records = [
        record
        for record in _iter_job_records(job_paths, agent_name)
        if record.status in TERMINAL_STATUSES
    ]
    if not records:
        return None
    return max(records, key=_terminal_sort_key)


def _terminal_sort_key(record: JobRecord) -> tuple[str, str]:
    stamp = record.completed_at or record.started_at or record.spec.created_at
    return (stamp or "", record.spec.job_id)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_job_store_terminal.py tests/test_job_authority.py tests/test_agent_status.py -q`
Expected: PASS, all tests pass

- [ ] **Step 5: Commit**

```bash
git add agency/jobs/store.py tests/test_job_store_terminal.py
git commit -m "feat(jobs): add newest terminal job lookup"
```

---

### Task 3: The health model

**Files:**
- Create: `agency/health.py`
- Test: `tests/test_health.py`

**Interfaces:**
- Consumes: `at_marker_path`, `every_marker_path`, `parse_every` from Task 1.
- Produces:
  - `RoutineSchedule` — a `NamedTuple` with fields `routine_id: str`, `at: str | None`, `every: str | None`, `enabled: bool`, `conditional: bool`
  - `routine_schedules(routines: Iterable[object]) -> tuple[RoutineSchedule, ...]` accepting either pydantic `Routine` models or their `model_dump(mode="json")` mappings
  - `schedule_state(schedules, *, logs_root: Path, agent_name: str, now: datetime, grace: timedelta) -> str | None` returning `"overdue"`, `"due"`, or `None`
  - `evaluate_agent_health(*, has_run: bool, last_job_failed: bool, schedule: str | None) -> str` returning one of `"green"`, `"amber"`, `"gray"`, `"red"`
  - `grace_window(dispatch_interval: int) -> timedelta`

- [ ] **Step 1: Write the failing test**

Create `tests/test_health.py`:

```python
from datetime import datetime, timedelta
import os

from agency.health import (
    RoutineSchedule,
    evaluate_agent_health,
    grace_window,
    routine_schedules,
    schedule_state,
)

NOW = datetime(2026, 7, 28, 12, 0, 0)
GRACE = timedelta(minutes=17)


def _logs(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir(exist_ok=True)
    return logs


def _state(tmp_path, *schedules, now=NOW):
    return schedule_state(
        schedules,
        logs_root=_logs(tmp_path),
        agent_name="product",
        now=now,
        grace=GRACE,
    )


def _at(routine_id="r", at="09:00", enabled=True, conditional=False):
    return RoutineSchedule(routine_id=routine_id, at=at, every=None, enabled=enabled, conditional=conditional)


def _every(routine_id="r", every="7d", enabled=True, conditional=False):
    return RoutineSchedule(routine_id=routine_id, at=None, every=every, enabled=enabled, conditional=conditional)


def test_grace_window_is_the_interval_plus_two_minutes():
    assert grace_window(15) == timedelta(minutes=17)
    assert grace_window(60) == timedelta(minutes=62)


def test_no_schedules_produce_no_state(tmp_path):
    assert _state(tmp_path) is None


def test_at_before_its_time_produces_no_state(tmp_path):
    assert _state(tmp_path, _at(at="18:00")) is None


def test_at_inside_the_grace_window_is_due(tmp_path):
    at_time = (NOW - timedelta(minutes=5)).strftime("%H:%M")
    assert _state(tmp_path, _at(at=at_time)) == "due"


def test_at_past_the_grace_window_is_overdue(tmp_path):
    at_time = (NOW - timedelta(hours=3)).strftime("%H:%M")
    assert _state(tmp_path, _at(at=at_time)) == "overdue"


def test_at_with_todays_marker_produces_no_state(tmp_path):
    logs = _logs(tmp_path)
    day = NOW.strftime("%Y-%m-%d")
    (logs / day).mkdir()
    (logs / day / f".event-product-r-{day}").touch()
    at_time = (NOW - timedelta(hours=3)).strftime("%H:%M")
    assert _state(tmp_path, _at(at=at_time)) is None


def test_at_ignores_a_marker_from_another_day(tmp_path):
    logs = _logs(tmp_path)
    stale_day = (NOW - timedelta(days=1)).strftime("%Y-%m-%d")
    (logs / stale_day).mkdir()
    (logs / stale_day / f".event-product-r-{stale_day}").touch()
    at_time = (NOW - timedelta(hours=3)).strftime("%H:%M")
    assert _state(tmp_path, _at(at=at_time)) == "overdue"


def test_malformed_at_produces_no_state(tmp_path):
    assert _state(tmp_path, _at(at="not-a-time")) is None


def test_every_without_a_marker_produces_no_state(tmp_path):
    assert _state(tmp_path, _every(every="1h")) is None


def test_every_before_the_interval_elapses_produces_no_state(tmp_path):
    logs = _logs(tmp_path)
    marker = logs / ".last-product-r"
    marker.touch()
    stamp = (NOW - timedelta(minutes=30)).timestamp()
    os.utime(marker, (stamp, stamp))
    assert _state(tmp_path, _every(every="6h")) is None


def test_every_inside_the_grace_window_is_due(tmp_path):
    logs = _logs(tmp_path)
    marker = logs / ".last-product-r"
    marker.touch()
    stamp = (NOW - timedelta(hours=6, minutes=5)).timestamp()
    os.utime(marker, (stamp, stamp))
    assert _state(tmp_path, _every(every="6h")) == "due"


def test_every_past_the_grace_window_is_overdue(tmp_path):
    logs = _logs(tmp_path)
    marker = logs / ".last-product-r"
    marker.touch()
    stamp = (NOW - timedelta(hours=9)).timestamp()
    os.utime(marker, (stamp, stamp))
    assert _state(tmp_path, _every(every="6h")) == "overdue"


def test_malformed_every_produces_no_state(tmp_path):
    logs = _logs(tmp_path)
    marker = logs / ".last-product-r"
    marker.touch()
    stamp = (NOW - timedelta(days=90)).timestamp()
    os.utime(marker, (stamp, stamp))
    assert _state(tmp_path, _every(every="soon")) is None


def test_disabled_routine_produces_no_state(tmp_path):
    at_time = (NOW - timedelta(hours=3)).strftime("%H:%M")
    assert _state(tmp_path, _at(at=at_time, enabled=False)) is None


def test_conditional_routine_produces_no_state(tmp_path):
    at_time = (NOW - timedelta(hours=3)).strftime("%H:%M")
    assert _state(tmp_path, _at(at=at_time, conditional=True)) is None


def test_overdue_wins_over_due_across_routines(tmp_path):
    due = (NOW - timedelta(minutes=5)).strftime("%H:%M")
    late = (NOW - timedelta(hours=3)).strftime("%H:%M")
    state = _state(tmp_path, _at(routine_id="a", at=due), _at(routine_id="b", at=late))
    assert state == "overdue"


def test_routine_schedules_reads_mappings():
    schedules = routine_schedules([
        {"id": "r", "schedule": {"at": "09:00"}, "enabled": True},
        {"id": "s", "schedule": {"every": "7d"}, "enabled": False, "condition": "pre-send"},
    ])
    assert schedules == (
        RoutineSchedule(routine_id="r", at="09:00", every=None, enabled=True, conditional=False),
        RoutineSchedule(routine_id="s", at=None, every="7d", enabled=False, conditional=True),
    )


def test_routine_schedules_reads_config_models():
    from agency.configuration.models import Routine

    routine = Routine(
        id="r",
        prompt={"scope": "blueprint", "name": "daily-review"},
        schedule={"at": "09:00"},
    )
    assert routine_schedules([routine]) == (
        RoutineSchedule(routine_id="r", at="09:00", every=None, enabled=True, conditional=False),
    )


def test_health_is_red_when_the_last_job_failed():
    assert evaluate_agent_health(has_run=True, last_job_failed=True, schedule=None) == "red"


def test_health_is_red_when_a_routine_is_overdue():
    assert evaluate_agent_health(has_run=True, last_job_failed=False, schedule="overdue") == "red"


def test_overdue_outranks_never_having_run():
    assert evaluate_agent_health(has_run=False, last_job_failed=False, schedule="overdue") == "red"


def test_health_is_amber_when_a_routine_is_due():
    assert evaluate_agent_health(has_run=True, last_job_failed=False, schedule="due") == "amber"


def test_health_is_gray_when_nothing_has_run():
    assert evaluate_agent_health(has_run=False, last_job_failed=False, schedule=None) == "gray"


def test_health_is_green_otherwise():
    assert evaluate_agent_health(has_run=True, last_job_failed=False, schedule=None) == "green"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_health.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'agency.health'`

- [ ] **Step 3: Write the module**

Create `agency/health.py`:

```python
"""Agent health signals derived from schedules and job outcomes."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import NamedTuple

from agency.dispatch.schedule import at_marker_path, every_marker_path, parse_every

OVERDUE = "overdue"
DUE = "due"


class RoutineSchedule(NamedTuple):
    routine_id: str
    at: str | None
    every: str | None
    enabled: bool
    conditional: bool


def grace_window(dispatch_interval: int) -> timedelta:
    return timedelta(minutes=dispatch_interval + 2)


def routine_schedules(routines: Iterable[object]) -> tuple[RoutineSchedule, ...]:
    """Normalize config routines, given as models or mappings, for scheduling."""
    schedules = []
    for routine in routines:
        schedule = _field(routine, "schedule") or {}
        schedules.append(
            RoutineSchedule(
                routine_id=str(_field(routine, "id") or ""),
                at=_optional_text(_field(schedule, "at")),
                every=_optional_text(_field(schedule, "every")),
                enabled=_field(routine, "enabled") is not False,
                conditional=bool(_field(routine, "condition")),
            )
        )
    return tuple(schedules)


def schedule_state(
    schedules: Iterable[RoutineSchedule],
    *,
    logs_root: Path,
    agent_name: str,
    now: datetime,
    grace: timedelta,
) -> str | None:
    """Return the strongest lateness across an agent's routines."""
    state = None
    for schedule in schedules:
        current = _routine_state(
            schedule,
            logs_root=logs_root,
            agent_name=agent_name,
            now=now,
            grace=grace,
        )
        if current == OVERDUE:
            return OVERDUE
        if current == DUE:
            state = DUE
    return state


def evaluate_agent_health(
    *,
    has_run: bool,
    last_job_failed: bool,
    schedule: str | None,
) -> str:
    if last_job_failed or schedule == OVERDUE:
        return "red"
    if schedule == DUE:
        return "amber"
    if not has_run:
        return "gray"
    return "green"


def _routine_state(
    schedule: RoutineSchedule,
    *,
    logs_root: Path,
    agent_name: str,
    now: datetime,
    grace: timedelta,
) -> str | None:
    if not schedule.enabled or schedule.conditional or not schedule.routine_id:
        return None
    if schedule.at:
        return _at_state(
            schedule,
            logs_root=logs_root,
            agent_name=agent_name,
            now=now,
            grace=grace,
        )
    if schedule.every:
        return _every_state(
            schedule,
            logs_root=logs_root,
            agent_name=agent_name,
            now=now,
            grace=grace,
        )
    return None


def _at_state(
    schedule: RoutineSchedule,
    *,
    logs_root: Path,
    agent_name: str,
    now: datetime,
    grace: timedelta,
) -> str | None:
    day = now.strftime("%Y-%m-%d")
    try:
        occurrence = datetime.strptime(f"{day} {schedule.at}", "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    if now < occurrence:
        return None
    marker = at_marker_path(logs_root, agent_name, schedule.routine_id, day)
    if marker.exists():
        return None
    return _lateness(now, occurrence, grace)


def _every_state(
    schedule: RoutineSchedule,
    *,
    logs_root: Path,
    agent_name: str,
    now: datetime,
    grace: timedelta,
) -> str | None:
    period = parse_every(schedule.every)
    if period is None:
        return None
    marker = every_marker_path(logs_root, agent_name, schedule.routine_id)
    try:
        fired_at = datetime.fromtimestamp(marker.stat().st_mtime)
    except OSError:
        return None
    return _lateness(now, fired_at + period, grace)


def _lateness(now: datetime, due_at: datetime, grace: timedelta) -> str | None:
    if now < due_at:
        return None
    return OVERDUE if now > due_at + grace else DUE


def _field(source: object, name: str):
    if isinstance(source, Mapping):
        return source.get(name)
    return getattr(source, name, None)


def _optional_text(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_health.py -q`
Expected: PASS, 24 passed

- [ ] **Step 5: Commit**

```bash
git add agency/health.py tests/test_health.py
git commit -m "feat(health): add schedule-aware agent health model"
```

---

### Task 4: Publish the dispatch interval on the group runtime

**Files:**
- Modify: `agency/web/state.py` (add one key to `runtime_group`)
- Test: `tests/test_config_normalization.py` (add one assertion)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `g["dispatch_interval"] -> int`, available to every consumer of a runtime group dictionary.

**Background:** `runtime_group` builds the plain dictionary the web layer calls `g`. `snapshot.config.agency.dispatch.interval` is an `int` defaulting to `15`.

- [ ] **Step 1: Write the failing test**

Open `tests/test_config_normalization.py` and find the test containing `assert runtime["job_paths"] == ()`. Add this line directly beneath it:

```python
    assert runtime["dispatch_interval"] == 15
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_config_normalization.py -q`
Expected: FAIL with `KeyError: 'dispatch_interval'`

- [ ] **Step 3: Add the key**

In `agency/web/state.py`, inside `runtime_group`, add the entry immediately after `"job_paths": job_store.paths(group_id),`:

```python
        "dispatch_interval": snapshot.config.agency.dispatch.interval,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_config_normalization.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agency/web/state.py tests/test_config_normalization.py
git commit -m "feat(web): expose the dispatch interval on runtime groups"
```

---

### Task 5: Wire both fleet builders to the health model

**Files:**
- Modify: `agency/app.py` (delete `agent_health_status`, add `_agent_health`, call it from `collect_agents_with_identity` and `build_dashboard_fleet`)
- Test: `tests/test_agent_health_fleet.py`

**Interfaces:**
- Consumes: `routine_schedules`, `schedule_state`, `evaluate_agent_health`, `grace_window` from Task 3; `latest_terminal_job` from Task 2; `g["dispatch_interval"]` from Task 4.
- Produces: `_agent_health(g, agent_name, routines, last_seen) -> str` in `agency/app.py`, used by both builders. Both callers already hold `last_seen`, so the helper must not re-scan the log tree for it. The `health` key on every fleet entry now carries one of `"green"`, `"amber"`, `"gray"`, `"red"`.

**Background:** `collect_agents_with_identity` iterates `g["agents_full"]`, which holds `model_dump(mode="json")` mappings, so routines arrive as dicts. `build_dashboard_fleet` iterates `group.agents.values()` from the config snapshot, so routines arrive as `Routine` models. `routine_schedules` accepts both, so `_agent_health` takes whatever the caller has.

- [ ] **Step 1: Write the failing test**

Create `tests/test_agent_health_fleet.py`:

```python
from datetime import datetime, timedelta
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from agency import app as app_module
from agency.jobs.authority import JobStore

NOW = datetime(2026, 7, 28, 12, 0, 0)


def _group(tmp_path, *, routines, dispatch_enabled=True):
    logs = tmp_path / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    memory_root = tmp_path / "memory"
    return {
        "key": "grp",
        "name": "Grp",
        "logs": logs,
        "observations": tmp_path / "observations",
        "proposals": tmp_path / "proposals",
        "decisions": tmp_path / "decisions",
        "agents": ["product"],
        "agents_full": [
            {
                "name": "product",
                "blueprint": "product-blueprint",
                "integration": "script",
                "routines": routines,
            }
        ],
        "dispatch": {"enabled": dispatch_enabled},
        "dispatch_interval": 15,
        "runtime": {"timeout": 1800},
        "job_paths": tuple(JobStore(memory_root).paths("grp")),
    }


def _health(tmp_path, *, routines, dispatch_enabled=True, now=NOW):
    group = _group(tmp_path, routines=routines, dispatch_enabled=dispatch_enabled)
    group["observations"].mkdir(parents=True, exist_ok=True)
    with patch.dict(os.environ, {"AGENCY_FIXED_NOW": now.isoformat()}):
        agents, _ = app_module.collect_agents_with_identity(group)
    return agents[0]["health"]


def _routine(routine_id="r", at=None, every=None):
    schedule = {"at": at} if at else {"every": every}
    return {
        "id": routine_id,
        "prompt": {"scope": "blueprint", "name": "daily-review"},
        "schedule": schedule,
    }


def test_agent_that_never_ran_without_routines_is_gray(tmp_path):
    assert _health(tmp_path, routines=[]) == "gray"


def test_agent_that_never_ran_before_its_scheduled_time_is_gray(tmp_path):
    assert _health(tmp_path, routines=[_routine(at="18:00")]) == "gray"


def test_agent_with_a_missed_occurrence_is_red(tmp_path):
    assert _health(tmp_path, routines=[_routine(at="08:00")]) == "red"


def test_a_missed_occurrence_is_ignored_when_dispatch_is_disabled(tmp_path):
    assert _health(tmp_path, routines=[_routine(at="08:00")], dispatch_enabled=False) == "gray"


def test_a_fired_occurrence_leaves_the_agent_gray(tmp_path):
    logs = tmp_path / "logs"
    day = NOW.strftime("%Y-%m-%d")
    (logs / day).mkdir(parents=True, exist_ok=True)
    (logs / day / f".event-product-r-{day}").touch()
    assert _health(tmp_path, routines=[_routine(at="08:00")]) == "gray"


def test_an_agent_with_a_recent_log_is_green(tmp_path):
    logs = tmp_path / "logs"
    day = NOW.strftime("%Y-%m-%d")
    (logs / day).mkdir(parents=True, exist_ok=True)
    log_file = logs / day / "product-manual_prompt-job-1.out"
    log_file.write_text("", encoding="utf-8")
    assert _health(tmp_path, routines=[]) == "green"


def test_an_agent_whose_last_run_is_ancient_is_still_green_without_a_schedule(tmp_path):
    logs = tmp_path / "logs"
    day = "2026-01-01"
    (logs / day).mkdir(parents=True, exist_ok=True)
    log_file = logs / day / "product-manual_prompt-job-1.out"
    log_file.write_text("", encoding="utf-8")
    stamp = (NOW - timedelta(days=120)).timestamp()
    os.utime(log_file, (stamp, stamp))
    assert _health(tmp_path, routines=[]) == "green"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_agent_health_fleet.py -q`
Expected: FAIL — `test_agent_that_never_ran_without_routines_is_gray` reports `assert 'red' == 'gray'`

- [ ] **Step 3: Replace `agent_health_status` in `agency/app.py`**

Add these imports next to the other `agency` imports at the top of the file:

```python
from agency.health import (
    evaluate_agent_health,
    grace_window,
    routine_schedules,
    schedule_state,
)
from agency.jobs.store import latest_terminal_job
```

Delete this function:

```python
def agent_health_status(last_seen: datetime | None) -> str:
    """Return health status based on last seen time. green/amber/red."""
    if last_seen is None:
        return "red"
    hours = (clock_now() - last_seen).total_seconds() / 3600
    if hours < 24:
        return "green"
    elif hours < 48:
        return "amber"
    return "red"
```

and put this in its place:

```python
def _agent_health(g: dict, agent_name: str, routines, last_seen: datetime | None) -> str:
    """Colour an agent from its schedule, its last run, and its last outcome."""
    dispatch_enabled = bool(g.get("dispatch", {}).get("enabled", False))
    schedules = routine_schedules(routines or ()) if dispatch_enabled else ()
    state = schedule_state(
        schedules,
        logs_root=Path(g["logs"]),
        agent_name=agent_name,
        now=clock_now(),
        grace=grace_window(int(g.get("dispatch_interval", 15))),
    )
    terminal = latest_terminal_job(tuple(g.get("job_paths", ())), agent_name)
    executed = terminal is not None and terminal.status in {"complete", "failed"}
    return evaluate_agent_health(
        has_run=last_seen is not None or executed,
        last_job_failed=terminal is not None and terminal.status == "failed",
        schedule=state,
    )
```

- [ ] **Step 4: Call it from `collect_agents_with_identity`**

In `collect_agents_with_identity`, replace this line inside the `info` dictionary:

```python
            "health": agent_health_status(last_seen),
```

with:

```python
            "health": _agent_health(g, agent_name, instance.get("routines"), last_seen),
```

- [ ] **Step 5: Call it from `build_dashboard_fleet`**

In `build_dashboard_fleet`, replace this line inside the appended dictionary:

```python
                "health": agent_health_status(last_seen),
```

with:

```python
                "health": _agent_health(g, instance.name, instance.routines, last_seen),
```

- [ ] **Step 6: Run the new tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_agent_health_fleet.py -q`
Expected: PASS, 7 passed

- [ ] **Step 7: Update the existing dashboard expectations**

In `tests/test_dashboard.py`, three assertions expect the old elapsed-time red for agents that have never run. Each of those agents now has no run on record and no fired schedule, so each becomes gray.

Replace in `test_dashboard_active_job_does_not_override_agent_health`:

```python
    assert fleet[0]["health"] == "red"
```

with:

```python
    assert fleet[0]["health"] == "gray"
```

Replace in `test_dashboard_fallback_preserves_exact_active_job_states`:

```python
    assert fleet["advisor"]["health"] == "red"
```

with:

```python
    assert fleet["advisor"]["health"] == "gray"
```

and:

```python
    assert fleet["researcher"]["health"] == "red"
```

with:

```python
    assert fleet["researcher"]["health"] == "gray"
```

Leave `assert fleet["writer"]["health"] == "green"` alone; `writer` has a log file written during the test.

- [ ] **Step 8: Run the dashboard tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_dashboard.py -q`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add agency/app.py tests/test_agent_health_fleet.py tests/test_dashboard.py
git commit -m "feat(dashboard): colour agents from schedules and outcomes"
```

---

### Task 6: Repair the next-run computation

**Files:**
- Modify: `agency/app.py` (`compute_next_run_detail` reads routines from `g["agents_full"]` and uses the shared marker helper)
- Test: `tests/test_agent_status.py` (rewrite the `compute_next_run` and `compute_next_run_detail` cases against the current config shape)

**Interfaces:**
- Consumes: `routine_schedules` from Task 3; `every_marker_path` and `parse_every` from Task 1.
- Produces: `compute_next_run_detail(g: dict, agent_name: str, dispatch_cfg: dict) -> dict | None` keeping its `{"when", "routine_id", "rule_index"}` return shape, and `compute_next_run(g, agent_name, dispatch_cfg) -> datetime | None` unchanged.

**Background:** Schema 4 puts routines on agent instances. `runtime_group` dumps only `GroupDispatch`, which has `enabled` and `daily_limit`, so `dispatch_cfg["routines"]` has never matched and `next_run` has been `None` for every agent. The `dispatch_cfg` argument keeps its place because callers pass it for the `enabled` flag.

- [ ] **Step 1: Write the failing test**

In `tests/test_agent_status.py`, replace the helper and every test from `test_next_run_disabled` through `test_next_run_detail_breaks_ties_by_config_order` with the block below. Keep `test_relative_future_*` and everything after them unchanged.

```python
def _group_with_routines(tmp_path, routines):
    logs = tmp_path / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    return {
        "key": "grp",
        "logs": logs,
        "agents_full": [
            {
                "name": "product",
                "blueprint": "product-blueprint",
                "integration": "script",
                "routines": [
                    {
                        "id": routine["id"],
                        "prompt": {"scope": "blueprint", "name": "daily-review"},
                        "schedule": {
                            key: value
                            for key, value in routine.items()
                            if key in {"at", "every"}
                        },
                        **({"condition": routine["condition"]} if "condition" in routine else {}),
                    }
                    for routine in routines
                ],
            }
        ],
    }


ENABLED = {"enabled": True}


def test_next_run_disabled(tmp_path):
    g = _group_with_routines(tmp_path, [{"id": "r", "every": "6h"}])
    assert compute_next_run(g, "product", {"enabled": False}) is None


def test_next_run_no_rules(tmp_path):
    g = _group_with_routines(tmp_path, [])
    assert compute_next_run(g, "product", ENABLED) is None


def test_next_run_unknown_agent(tmp_path):
    g = _group_with_routines(tmp_path, [{"id": "r", "every": "6h"}])
    assert compute_next_run(g, "missing", ENABLED) is None


def test_next_run_at_future(tmp_path):
    fixed_now = datetime(2026, 1, 15, 12, 0, 0)
    future = (fixed_now + timedelta(hours=2)).strftime("%H:%M")
    g = _group_with_routines(tmp_path, [{"id": "r", "at": future}])
    with patch.dict(os.environ, {"AGENCY_FIXED_NOW": fixed_now.isoformat()}):
        result = compute_next_run(g, "product", ENABLED)
    assert result is not None
    assert result.date() == fixed_now.date()
    assert result.strftime("%H:%M") == future


def test_next_run_at_past_rolls_to_tomorrow(tmp_path):
    fixed_now = datetime(2026, 1, 15, 12, 0, 0)
    past = (fixed_now - timedelta(hours=2)).strftime("%H:%M")
    g = _group_with_routines(tmp_path, [{"id": "r", "at": past}])
    with patch.dict(os.environ, {"AGENCY_FIXED_NOW": fixed_now.isoformat()}):
        result = compute_next_run(g, "product", ENABLED)
    assert result is not None
    assert result.date() == (fixed_now + timedelta(days=1)).date()


def test_next_run_every_no_marker_due_now(tmp_path):
    g = _group_with_routines(tmp_path, [{"id": "r", "every": "6h"}])
    before = datetime.now()
    result = compute_next_run(g, "product", ENABLED)
    assert result is not None
    assert result <= datetime.now() and result >= before - timedelta(seconds=5)


def test_next_run_every_with_marker(tmp_path):
    g = _group_with_routines(tmp_path, [{"id": "r", "every": "6h"}])
    marker = g["logs"] / ".last-product-r"
    marker.touch()
    two_hours_ago = time.time() - 2 * 3600
    os.utime(marker, (two_hours_ago, two_hours_ago))
    result = compute_next_run(g, "product", ENABLED)
    assert result is not None
    delta = (result - datetime.now()).total_seconds()
    assert 3.9 * 3600 < delta < 4.1 * 3600


def test_next_run_skips_condition_rule(tmp_path):
    g = _group_with_routines(tmp_path, [{"id": "gate", "at": "06:00", "condition": "pre-send"}])
    assert compute_next_run(g, "product", ENABLED) is None


def test_next_run_returns_soonest(tmp_path):
    fixed_now = datetime(2026, 1, 15, 12, 0, 0)
    soon = (fixed_now + timedelta(minutes=30)).strftime("%H:%M")
    later = (fixed_now + timedelta(hours=5)).strftime("%H:%M")
    g = _group_with_routines(tmp_path, [{"id": "a", "at": later}, {"id": "b", "at": soon}])
    with patch.dict(os.environ, {"AGENCY_FIXED_NOW": fixed_now.isoformat()}):
        result = compute_next_run(g, "product", ENABLED)
    assert result.strftime("%H:%M") == soon


def test_next_run_detail_identifies_winning_rule(tmp_path):
    fixed_now = datetime(2026, 1, 15, 12, 0, 0)
    g = _group_with_routines(tmp_path, [{"id": "later", "at": "17:00"}, {"id": "soon", "at": "12:30"}])
    with patch.dict(os.environ, {"AGENCY_FIXED_NOW": fixed_now.isoformat()}):
        detail = compute_next_run_detail(g, "product", ENABLED)
        compatible_value = compute_next_run(g, "product", ENABLED)
    assert detail == {
        "when": fixed_now + timedelta(minutes=30),
        "routine_id": "soon",
        "rule_index": 1,
    }
    assert compatible_value == detail["when"]


def test_next_run_detail_breaks_ties_by_config_order(tmp_path):
    fixed_now = datetime(2026, 1, 15, 12, 0, 0)
    g = _group_with_routines(tmp_path, [{"id": "first", "at": "13:00"}, {"id": "second", "at": "13:00"}])
    with patch.dict(os.environ, {"AGENCY_FIXED_NOW": fixed_now.isoformat()}):
        detail = compute_next_run_detail(g, "product", ENABLED)
    assert detail["routine_id"] == "first"
    assert detail["rule_index"] == 0


def test_next_run_skips_disabled_routine(tmp_path):
    g = _group_with_routines(tmp_path, [{"id": "r", "at": "13:00"}])
    g["agents_full"][0]["routines"][0]["enabled"] = False
    assert compute_next_run(g, "product", ENABLED) is None
```

Also update `test_collect_agents_includes_running_and_next_run` in the same file: delete the `"dispatch"` entry that carries routines and replace it with the group-level flag, since routines now come from `agents_full`:

```python
        "dispatch": {"enabled": True},
```

and add these two keys to the same dictionary so the health call has what it needs:

```python
        "dispatch_interval": 15,
        "runtime": {"timeout": 1800},
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_agent_status.py -q`
Expected: FAIL — the next-run tests report `assert None is not None`, because routines are read from a mapping that no longer carries them

- [ ] **Step 3: Rewrite `compute_next_run_detail` in `agency/app.py`**

Replace the whole function body between the docstring and the final `return`:

```python
def compute_next_run_detail(
    g: dict,
    agent_name: str,
    dispatch_cfg: dict,
) -> dict | None:
    """Return the soonest scheduled run with its originating rule identity."""
    if not dispatch_cfg.get("enabled", False):
        return None
    rules = dispatch_cfg.get("routines", {}).get(agent_name, [])
    if not isinstance(rules, list):
        return None

    now = clock_now()
    logs_root = Path(g["logs"])
    candidates: list[dict] = []

    for rule_index, rule in enumerate(rules):
        if not isinstance(rule, dict):
            continue
        routine_id = rule.get("id", "")
        if not routine_id or rule.get("condition"):
            continue

        at_time = rule.get("at", "")
        every_val = rule.get("every", "")

        if at_time:
            try:
                target = datetime.strptime(
                    f"{now.strftime('%Y-%m-%d')} {at_time}", "%Y-%m-%d %H:%M"
                )
            except ValueError:
                continue
            if target <= now:
                target += timedelta(days=1)
        elif every_val:
            match = re.fullmatch(r"(\d+)(m|h|d)", every_val)
            if not match:
                continue
            value = int(match.group(1))
            unit = match.group(2)
            seconds = value * 60 if unit == "m" else value * 3600 if unit == "h" else value * 86400
            marker = logs_root / f".last-{agent_name}-{routine_id}"
            target = (
                now
                if not marker.exists()
                else datetime.fromtimestamp(marker.stat().st_mtime)
                + timedelta(seconds=seconds)
            )
        else:
            continue

        candidates.append({
            "when": target,
            "routine_id": routine_id,
            "rule_index": rule_index,
        })

    return min(candidates, key=lambda candidate: candidate["when"], default=None)
```

with:

```python
def _agent_routines(g: dict, agent_name: str):
    for instance in g.get("agents_full", []):
        if instance.get("name") == agent_name:
            return instance.get("routines") or ()
    return ()


def compute_next_run_detail(
    g: dict,
    agent_name: str,
    dispatch_cfg: dict,
) -> dict | None:
    """Return the soonest scheduled run with its originating rule identity."""
    if not dispatch_cfg.get("enabled", False):
        return None

    now = clock_now()
    logs_root = Path(g["logs"])
    candidates: list[dict] = []

    for rule_index, schedule in enumerate(
        routine_schedules(_agent_routines(g, agent_name))
    ):
        if not schedule.enabled or schedule.conditional or not schedule.routine_id:
            continue

        if schedule.at:
            try:
                target = datetime.strptime(
                    f"{now.strftime('%Y-%m-%d')} {schedule.at}", "%Y-%m-%d %H:%M"
                )
            except ValueError:
                continue
            if target <= now:
                target += timedelta(days=1)
        elif schedule.every:
            period = parse_every(schedule.every)
            if period is None:
                continue
            marker = every_marker_path(logs_root, agent_name, schedule.routine_id)
            target = (
                now
                if not marker.exists()
                else datetime.fromtimestamp(marker.stat().st_mtime) + period
            )
        else:
            continue

        candidates.append({
            "when": target,
            "routine_id": schedule.routine_id,
            "rule_index": rule_index,
        })

    return min(candidates, key=lambda candidate: candidate["when"], default=None)
```

Add the shared helpers to the imports at the top of `agency/app.py`:

```python
from agency.dispatch.schedule import every_marker_path, parse_every
```

`re` stays imported; it is still used elsewhere in the file.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_agent_status.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agency/app.py tests/test_agent_status.py
git commit -m "fix(dashboard): read next run from instance routines"
```

---

### Task 7: Partitioned fleet counters and gray rendering

**Files:**
- Modify: `agency/app.py` (dashboard route context, around the `fleet_healthy` entry)
- Modify: `agency/templates/home.html:33-63`
- Test: `tests/test_dashboard.py` (add one test)

**Interfaces:**
- Consumes: the `health` values produced in Task 5.
- Produces: template context keys `fleet_healthy: int`, `fleet_never_run: int`, `fleet_attention: int`, alongside the unchanged `fleet_running: int`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_dashboard.py`:

```python
def test_dashboard_reports_never_run_agents_separately(monkeypatch, tmp_path, raw_config):
    client, _, group_root = _seed_dashboard_app(monkeypatch, tmp_path, raw_config)

    response = client.get("/newsletter/")

    assert response.status_code == 200
    assert "1 never run" in response.text
    assert "1 needs attention" not in response.text
    assert "text-gray-400" in response.text
    assert 'title="No run on record"' in response.text
```

The bare phrase `needs attention` also appears in a rendered HTML comment in
`base.html`, so the assertion must target the counter text `1 needs attention`.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_dashboard.py::test_dashboard_reports_never_run_agents_separately -q`
Expected: FAIL with `assert '1 never run' in ...`

- [ ] **Step 3: Publish the counters**

In `agency/app.py`, in the dashboard route's `TemplateResponse` context, replace:

```python
        "fleet_healthy": sum(1 for a in agents if a["health"] == "green"),
        "fleet_running": sum(1 for a in agents if a.get("job_status_key") == "running"),
```

with:

```python
        "fleet_healthy": sum(1 for a in agents if a["health"] == "green"),
        "fleet_never_run": sum(1 for a in agents if a["health"] == "gray"),
        "fleet_attention": sum(1 for a in agents if a["health"] in {"amber", "red"}),
        "fleet_running": sum(1 for a in agents if a.get("job_status_key") == "running"),
```

- [ ] **Step 4: Render gray in `agency/templates/home.html`**

Replace the background tint block:

```html
       class="inline-flex items-center gap-1.5 text-base whitespace-nowrap rounded px-2 py-0.5 transition-colors
              {% if a.health == 'red' %}bg-rose-50 dark:bg-rose-950/40 hover:bg-rose-100 dark:hover:bg-rose-900/40
              {% elif a.health == 'amber' %}bg-amber-50 dark:bg-amber-950/40 hover:bg-amber-100 dark:hover:bg-amber-900/40
              {% else %}hover:bg-gray-100 dark:hover:bg-gray-700{% endif %}">
```

with:

```html
       class="inline-flex items-center gap-1.5 text-base whitespace-nowrap rounded px-2 py-0.5 transition-colors
              {% if a.health == 'red' %}bg-rose-50 dark:bg-rose-950/40 hover:bg-rose-100 dark:hover:bg-rose-900/40
              {% elif a.health == 'amber' %}bg-amber-50 dark:bg-amber-950/40 hover:bg-amber-100 dark:hover:bg-amber-900/40
              {% elif a.health == 'gray' %}bg-gray-100/60 dark:bg-gray-800/60 hover:bg-gray-200 dark:hover:bg-gray-700
              {% else %}hover:bg-gray-100 dark:hover:bg-gray-700{% endif %}">
```

Replace the dot block:

```html
      {% else %}
      <span class="{% if a.health == 'green' %}text-emerald-500{% elif a.health == 'amber' %}text-amber-500{% else %}text-rose-500{% endif %} text-sm leading-none">&#9679;</span>
      {% endif %}
```

with:

```html
      {% else %}
      <span class="{% if a.health == 'green' %}text-emerald-500{% elif a.health == 'amber' %}text-amber-500{% elif a.health == 'gray' %}text-gray-400 dark:text-gray-500{% else %}text-rose-500{% endif %} text-sm leading-none"
            title="{% if a.health == 'green' %}Healthy{% elif a.health == 'amber' %}Run is due{% elif a.health == 'gray' %}No run on record{% else %}Needs attention{% endif %}">&#9679;</span>
      {% endif %}
```

Replace the footer line:

```html
    {{ fleet_agents|length }} agents · {{ fleet_healthy }} healthy{% if fleet_running %} · <span class="text-emerald-700 dark:text-emerald-300">{{ fleet_running }} running</span>{% endif %}{% if fleet_agents|length - fleet_healthy > 0 %} · <span class="text-amber-700 dark:text-amber-300">{{ fleet_agents|length - fleet_healthy }} needs attention</span>{% endif %}
```

with:

```html
    {{ fleet_agents|length }} agents · {{ fleet_healthy }} healthy{% if fleet_never_run %} · <span class="text-gray-500 dark:text-gray-400">{{ fleet_never_run }} never run</span>{% endif %}{% if fleet_running %} · <span class="text-emerald-700 dark:text-emerald-300">{{ fleet_running }} running</span>{% endif %}{% if fleet_attention %} · <span class="text-amber-700 dark:text-amber-300">{{ fleet_attention }} needs attention</span>{% endif %}
```

- [ ] **Step 5: Run the dashboard tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_dashboard.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add agency/app.py agency/templates/home.html tests/test_dashboard.py
git commit -m "feat(dashboard): report never-run agents apart from failures"
```

---

### Task 8: Documentation and full-suite verification

**Files:**
- Modify: `kb/dispatch.md` (document the health states)
- Test: the complete suite

- [ ] **Step 1: Find the place to document the states**

Open `kb/dispatch.md` and locate the section describing routines and markers. Add
the block from Step 2 directly after it, or at the end of the file if no such
section exists.

- [ ] **Step 2: Document the health states**

Add to `kb/dispatch.md`:

```markdown
## Agent health on the dashboard

The fleet bar colours each agent from its schedule and its last outcome.

- **Gray** — no run on record. The agent has produced no log and no finished job.
- **Green** — the agent has run, nothing is overdue, and the last job did not fail.
- **Amber** — a routine is due. The expected time has passed but is still inside
  the grace window of `agency.dispatch.interval` plus two minutes.
- **Red** — the last finished job failed, or an enabled routine is past its
  expected time by more than the grace window.

Lateness is measured against the markers the dispatch runner writes:
`<logs>/<date>/.event-<agent>-<routine>-<date>` for `at` rules and
`<logs>/.last-<agent>-<routine>` for `every` rules. A routine with no marker and
an `every` schedule produces no signal, because there is no reference point
before its first dispatch. Routines that are disabled or carry a `condition` are
never counted as late, matching what the runner actually fires.
```

- [ ] **Step 3: Run the complete suite**

Run: `.venv/Scripts/python -m pytest tests/ -q`
Expected: PASS, no failures and no errors

- [ ] **Step 4: Verify the real dashboard**

Run: `.venv/Scripts/python -m agency.app`

Open `http://127.0.0.1:8500/atreides/`. Confirm that Paul Atreides and Gurney Halleck render with gray dots, that the footer reads `5 agents · 3 healthy · 2 never run` with no `needs attention` segment, and that Thufir, Duncan, and Jessica stay green. Stop the server.

- [ ] **Step 5: Commit**

```bash
git add kb/dispatch.md
git commit -m "docs(dispatch): describe the agent health states"
```

---

## Integration

After Task 8, follow the repository's integration workflow in `AGENTS.md`: review the whole branch, rebase onto `master` if `master` has moved, fast-forward `master` to the reviewed tip, re-run the complete suite on `master`, push both branches, and remove the worktree with `git worktree remove .worktrees/agent-health-signals`.
