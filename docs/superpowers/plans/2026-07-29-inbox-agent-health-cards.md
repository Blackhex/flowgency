# Inbox Agent Health Cards Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the Inbox fleet bar into a card grid that shows every agent's last run and next run, names the fault on unhealthy agents, and files those faults as Attention Queue items.

**Architecture:** `agency/health.py` grows from returning a severity string to returning the routine that caused it, and pairs the existing colour with a reason `kind`. `agency/app.py` gains one `_apply_agent_status` helper that both fleet builders call, so the two code paths cannot drift. All user-visible strings are composed in Python and asserted in tests; the templates only place them.

**Tech Stack:** Python 3.11+, FastAPI, Jinja2, Tailwind utility classes, pytest, Playwright.

## Global Constraints

- Work in the existing worktree `.worktrees/inbox-agent-health-cards` on branch `feat/inbox-agent-health-cards`. Run every command from that directory.
- Run tests with `.venv/Scripts/python -m pytest`. Running from another checkout resolves the wrong local `tests` package.
- The specification is `docs/superpowers/specs/2026-07-29-inbox-agent-health-cards-design.md`. Its assets are `docs/superpowers/specs/assets/2026-07-29-inbox-agent-health-cards/fleet-cards.png` and `attention-queue.png`, rendered from `inbox-fleet-cards.html` in the same directory. The images are normative for layout, ordering, and copy; the prose is normative for behavior. Compare the rendered page against both images before calling Task 4 or Task 5 complete.
- The health model is fixed. Four colours, the precedence `job_failed > overdue > due > never_run > healthy`, `grace_window(interval) == interval + 2 minutes`, and the marker file names all stay exactly as they are. `evaluate_agent_health` keeps its signature and its return values.
- `running` is orthogonal: it changes the dot glyph and the next-run cell only. It never changes `color` or `kind`, and a running agent never produces a queue item.
- Cards render in configured order. Never sort by severity.
- Card elapsed values are coarse and single-unit (`3h`). Queue elapsed values are compound (`3h 46m`, `3m 55s`, `12s`, `2d 3h`), with a zero remainder omitted.
- Do not stage or modify `config.yaml`, `config.yaml.lock`, group-state directories, logs, or `.superpowers/`.
- Commit messages follow Conventional Commits with an imperative, lowercase, period-free description of at most 72 characters including the prefix.

---

## File Structure

| File | Responsibility | Change |
| --- | --- | --- |
| `agency/health.py` | Pure health model over plain values | Add `Lateness`, `schedule_lateness`, `AgentHealth`, `describe_agent_health`, `elapsed_coarse`, `elapsed_precise`; reimplement `schedule_state` on top of `schedule_lateness` |
| `agency/app.py` | Fleet assembly and dashboard route | Replace `_agent_health` with `_apply_agent_status`; add `_fault_line`, `_health_sentence`, `build_health_items`, the `initials` filter; wire the home route |
| `agency/templates/home.html` | Inbox rendering | Zone 1 becomes a card grid; Zone 3 gains health items |
| `agency/web/routes/agent_detail.py` | Agent Detail contexts | `_routines_context` gains a schedule status table |
| `agency/templates/agent_detail_routines.html` | Routines tab | Render the status table above the form |
| `tests/test_health.py` | Health model unit tests | Cases for the four new functions |
| `tests/test_agent_status.py` | Fleet assembly tests | Cases for `_apply_agent_status` |
| `tests/test_dashboard.py` | Rendered page tests | Cards, fault lines, queue items, counters |
| `tests/test_agent_detail.py` | Routines tab tests | Schedule status table |
| `tests/ui/dashboard.spec.ts` | Browser test | Fleet cards and timing anchors |

---

### Task 1: Lateness carries the offending routine

**Files:**
- Modify: `agency/health.py`
- Test: `tests/test_health.py`

**Interfaces:**
- Consumes: existing `RoutineSchedule`, `OVERDUE`, `DUE`, `at_marker_path`, `every_marker_path`, `parse_every`.
- Produces:
  - `Lateness(routine_id: str, state: str, due_at: datetime)` — a `NamedTuple`.
  - `schedule_lateness(schedules: Iterable[RoutineSchedule], *, logs_root: Path, agent_name: str, now: datetime, grace: timedelta) -> Lateness | None`
  - `elapsed_coarse(delta: timedelta) -> str`
  - `elapsed_precise(delta: timedelta) -> str`
  - `schedule_state` keeps its exact signature and return values.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_health.py`. Add `Lateness`, `elapsed_coarse`, `elapsed_precise`, and `schedule_lateness` to the existing `from agency.health import (...)` block at the top of the file.

```python
def _lateness(tmp_path, *schedules, now=NOW):
    return schedule_lateness(
        schedules,
        logs_root=_logs(tmp_path),
        agent_name="product",
        now=now,
        grace=GRACE,
    )


def test_lateness_is_none_when_nothing_is_late(tmp_path):
    assert _lateness(tmp_path, _at(at="18:00")) is None


def test_lateness_names_the_routine_and_its_due_time(tmp_path):
    result = _lateness(tmp_path, _at(routine_id="suite-health", at="08:00"))
    assert result == Lateness(
        routine_id="suite-health",
        state="overdue",
        due_at=datetime(2026, 7, 28, 8, 0),
    )


def test_overdue_outranks_due_regardless_of_order(tmp_path):
    due_at = (NOW - timedelta(minutes=5)).strftime("%H:%M")
    result = _lateness(
        tmp_path,
        _at(routine_id="soon", at=due_at),
        _at(routine_id="late", at="08:00"),
    )
    assert result.routine_id == "late"
    assert result.state == "overdue"


def test_earliest_due_wins_within_one_state(tmp_path):
    result = _lateness(
        tmp_path,
        _at(routine_id="nine", at="09:00"),
        _at(routine_id="eight", at="08:00"),
    )
    assert result.routine_id == "eight"


def test_exact_tie_breaks_on_configured_order(tmp_path):
    result = _lateness(
        tmp_path,
        _at(routine_id="first", at="08:00"),
        _at(routine_id="second", at="08:00"),
    )
    assert result.routine_id == "first"


def test_schedule_state_still_reports_only_severity(tmp_path):
    assert _state(tmp_path, _at(at="08:00")) == "overdue"
    assert _state(tmp_path, _at(at="18:00")) is None


def test_elapsed_coarse_uses_one_unit():
    assert elapsed_coarse(timedelta(seconds=30)) == "0m"
    assert elapsed_coarse(timedelta(minutes=46)) == "46m"
    assert elapsed_coarse(timedelta(hours=3, minutes=46)) == "3h"
    assert elapsed_coarse(timedelta(days=2, hours=3)) == "2d"


def test_elapsed_precise_adds_the_next_smaller_unit():
    assert elapsed_precise(timedelta(seconds=12)) == "12s"
    assert elapsed_precise(timedelta(minutes=3, seconds=55)) == "3m 55s"
    assert elapsed_precise(timedelta(hours=3, minutes=46)) == "3h 46m"
    assert elapsed_precise(timedelta(days=2, hours=3)) == "2d 3h"


def test_elapsed_precise_omits_a_zero_remainder():
    assert elapsed_precise(timedelta(hours=3)) == "3h"
    assert elapsed_precise(timedelta(minutes=5)) == "5m"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_health.py -v`
Expected: collection error, `ImportError: cannot import name 'Lateness' from 'agency.health'`.

- [ ] **Step 3: Add the lateness value and the elapsed formatters**

In `agency/health.py`, add `Lateness` directly below the existing `RoutineSchedule` class:

```python
class Lateness(NamedTuple):
    routine_id: str
    state: str
    due_at: datetime
```

Add both formatters below `grace_window`:

```python
def elapsed_coarse(delta: timedelta) -> str:
    """Render a duration as a single unit, for the fleet cards."""
    minutes = max(int(delta.total_seconds()) // 60, 0)
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h"
    return f"{hours // 24}d"


def elapsed_precise(delta: timedelta) -> str:
    """Render a duration with its next smaller unit, for queue sentences."""
    seconds = max(int(delta.total_seconds()), 0)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return _compound(seconds // 60, "m", seconds % 60, "s")
    if seconds < 86400:
        return _compound(seconds // 3600, "h", seconds % 3600 // 60, "m")
    return _compound(seconds // 86400, "d", seconds % 86400 // 3600, "h")


def _compound(major: int, major_unit: str, minor: int, minor_unit: str) -> str:
    if minor == 0:
        return f"{major}{major_unit}"
    return f"{major}{major_unit} {minor}{minor_unit}"
```

- [ ] **Step 4: Convert the private state helpers to return a `Lateness`**

Replace `schedule_state`, `_routine_state`, `_at_state`, `_every_state`, and `_lateness` in `agency/health.py` with:

```python
def schedule_lateness(
    schedules: Iterable[RoutineSchedule],
    *,
    logs_root: Path,
    agent_name: str,
    now: datetime,
    grace: timedelta,
) -> Lateness | None:
    """Return the strongest lateness across an agent's routines."""
    best: Lateness | None = None
    for schedule in schedules:
        current = _routine_lateness(
            schedule,
            logs_root=logs_root,
            agent_name=agent_name,
            now=now,
            grace=grace,
        )
        if current is None:
            continue
        if best is None or _rank(current) < _rank(best):
            best = current
    return best


def schedule_state(
    schedules: Iterable[RoutineSchedule],
    *,
    logs_root: Path,
    agent_name: str,
    now: datetime,
    grace: timedelta,
) -> str | None:
    """Return the strongest lateness across an agent's routines."""
    lateness = schedule_lateness(
        schedules,
        logs_root=logs_root,
        agent_name=agent_name,
        now=now,
        grace=grace,
    )
    return lateness.state if lateness is not None else None


def _rank(lateness: Lateness) -> tuple[int, datetime]:
    return (0 if lateness.state == OVERDUE else 1, lateness.due_at)


def _routine_lateness(
    schedule: RoutineSchedule,
    *,
    logs_root: Path,
    agent_name: str,
    now: datetime,
    grace: timedelta,
) -> Lateness | None:
    if not schedule.enabled or schedule.conditional or not schedule.routine_id:
        return None
    if schedule.at:
        return _at_lateness(
            schedule,
            logs_root=logs_root,
            agent_name=agent_name,
            now=now,
            grace=grace,
        )
    if schedule.every:
        return _every_lateness(
            schedule,
            logs_root=logs_root,
            agent_name=agent_name,
            now=now,
            grace=grace,
        )
    return None


def _at_lateness(
    schedule: RoutineSchedule,
    *,
    logs_root: Path,
    agent_name: str,
    now: datetime,
    grace: timedelta,
) -> Lateness | None:
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
    return _as_lateness(schedule.routine_id, now, occurrence, grace)


def _every_lateness(
    schedule: RoutineSchedule,
    *,
    logs_root: Path,
    agent_name: str,
    now: datetime,
    grace: timedelta,
) -> Lateness | None:
    period = parse_every(schedule.every)
    if period is None:
        return None
    marker = every_marker_path(logs_root, agent_name, schedule.routine_id)
    try:
        fired_at = datetime.fromtimestamp(marker.stat().st_mtime)
    except OSError:
        return None
    return _as_lateness(schedule.routine_id, now, fired_at + period, grace)


def _as_lateness(
    routine_id: str,
    now: datetime,
    due_at: datetime,
    grace: timedelta,
) -> Lateness | None:
    if now < due_at:
        return None
    state = OVERDUE if now > due_at + grace else DUE
    return Lateness(routine_id=routine_id, state=state, due_at=due_at)
```

- [ ] **Step 5: Run the health tests**

Run: `.venv/Scripts/python -m pytest tests/test_health.py -v`
Expected: PASS, including every pre-existing `schedule_state` and `evaluate_agent_health` case.

- [ ] **Step 6: Run the suites that consume the health module**

Run: `.venv/Scripts/python -m pytest tests/test_health.py tests/test_dashboard.py tests/test_agent_health_fleet.py tests/test_agent_status.py -q`
Expected: PASS. `schedule_state` is behaviour-preserving, so nothing downstream may change yet.

- [ ] **Step 7: Commit**

```bash
git add agency/health.py tests/test_health.py
git commit -m "feat(health): report which routine is late"
```

---

### Task 2: Pair the health colour with its reason

**Files:**
- Modify: `agency/health.py`
- Test: `tests/test_health.py`

**Interfaces:**
- Consumes: `Lateness` and `schedule_lateness` from Task 1; the unchanged `evaluate_agent_health`.
- Produces:
  - `AgentHealth(color: str, kind: str, routine_id: str | None, due_at: datetime | None, late: timedelta | None)` — a `NamedTuple`. `kind` is one of `healthy`, `never_run`, `due`, `overdue`, `job_failed`.
  - `describe_agent_health(*, has_run: bool, last_job_failed: bool, lateness: Lateness | None, now: datetime) -> AgentHealth`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_health.py`, adding `AgentHealth` and `describe_agent_health` to the import block.

```python
def _describe(has_run=True, last_job_failed=False, lateness=None):
    return describe_agent_health(
        has_run=has_run,
        last_job_failed=last_job_failed,
        lateness=lateness,
        now=NOW,
    )


def _late(state="overdue", routine_id="suite-health", hours=3):
    return Lateness(
        routine_id=routine_id,
        state=state,
        due_at=NOW - timedelta(hours=hours),
    )


def test_failed_job_is_red_and_named():
    assert _describe(last_job_failed=True) == AgentHealth("red", "job_failed", None, None, None)


def test_overdue_carries_the_routine_and_how_late_it_is():
    result = _describe(lateness=_late())
    assert result.color == "red"
    assert result.kind == "overdue"
    assert result.routine_id == "suite-health"
    assert result.due_at == NOW - timedelta(hours=3)
    assert result.late == timedelta(hours=3)


def test_due_is_amber_and_named():
    result = _describe(lateness=_late(state="due", hours=0))
    assert result.color == "amber"
    assert result.kind == "due"


def test_a_failed_job_outranks_an_overdue_routine():
    result = _describe(last_job_failed=True, lateness=_late())
    assert result.kind == "job_failed"
    assert result.routine_id is None


def test_no_run_on_record_is_gray_and_named():
    assert _describe(has_run=False) == AgentHealth("gray", "never_run", None, None, None)


def test_a_quiet_agent_that_has_run_is_healthy():
    assert _describe() == AgentHealth("green", "healthy", None, None, None)


def test_kind_never_contradicts_colour():
    cases = [
        _describe(last_job_failed=True),
        _describe(lateness=_late()),
        _describe(lateness=_late(state="due", hours=0)),
        _describe(has_run=False),
        _describe(),
    ]
    expected = {
        "job_failed": "red",
        "overdue": "red",
        "due": "amber",
        "never_run": "gray",
        "healthy": "green",
    }
    for result in cases:
        assert result.color == expected[result.kind]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_health.py -k describe -v`
Expected: collection error, `ImportError: cannot import name 'AgentHealth'`.

- [ ] **Step 3: Implement the description**

In `agency/health.py`, add below `evaluate_agent_health`:

```python
class AgentHealth(NamedTuple):
    color: str
    kind: str
    routine_id: str | None
    due_at: datetime | None
    late: timedelta | None


def describe_agent_health(
    *,
    has_run: bool,
    last_job_failed: bool,
    lateness: Lateness | None,
    now: datetime,
) -> AgentHealth:
    """Pair the health colour with the reason that produced it."""
    color = evaluate_agent_health(
        has_run=has_run,
        last_job_failed=last_job_failed,
        schedule=lateness.state if lateness is not None else None,
    )
    if last_job_failed:
        return AgentHealth(color, "job_failed", None, None, None)
    if lateness is not None:
        return AgentHealth(
            color,
            lateness.state,
            lateness.routine_id,
            lateness.due_at,
            now - lateness.due_at,
        )
    if not has_run:
        return AgentHealth(color, "never_run", None, None, None)
    return AgentHealth(color, "healthy", None, None, None)
```

`AgentHealth` must be declared before `describe_agent_health` uses it but may sit anywhere after `evaluate_agent_health`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_health.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agency/health.py tests/test_health.py
git commit -m "feat(health): describe why an agent is unhealthy"
```

---

### Task 3: One fleet-entry enricher for both builders

**Files:**
- Modify: `agency/app.py`
- Test: `tests/test_agent_status.py`

**Interfaces:**
- Consumes: `AgentHealth`, `describe_agent_health`, `Lateness`, `schedule_lateness`, `elapsed_coarse`, `elapsed_precise` from Tasks 1 and 2; the existing `get_agent_last_run`, `get_agent_last_seen`, `compute_next_run_detail`, `latest_executed_job`, `routine_schedules`, `grace_window`, `clock_now`.
- Produces: `_apply_agent_status(g: dict, agent: dict, routines, dispatch_cfg: dict) -> None`, which mutates `agent` in place, adding the keys `last_run`, `last_seen`, `next_run`, `next_run_detail`, `health`, `health_kind`, `health_routine`, `health_due_at`, `health_late`, `health_job`, `health_fault`, `health_sentence`. Tasks 4 and 5 read exactly these keys.
- Removes: `_agent_health`. It has two call sites, both replaced here.

**Why one helper:** `collect_agents_with_identity` computes the timing fields today and `build_dashboard_fleet` does not, yet `build_dashboard_fleet` is the path that actually renders whenever services are healthy. That asymmetry is why the timing values were invisible. Both builders must go through the same enricher.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_agent_status.py`. Extend the `from agency.app import (...)` block with `_apply_agent_status`.

```python
NOW = datetime(2026, 7, 29, 11, 46, 0)
ENABLED_DISPATCH = {"enabled": True}


def _fleet_group(tmp_path, routines):
    logs = tmp_path / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    return {
        "key": "grp",
        "logs": logs,
        "job_paths": (),
        "dispatch": {"enabled": True},
        "dispatch_interval": 15,
        "agents_full": [{"name": "product", "routines": routines}],
    }


def _enrich(tmp_path, routines):
    g = _fleet_group(tmp_path, routines)
    agent = {"name": "product"}
    with patch("agency.app.clock_now", return_value=NOW):
        _apply_agent_status(g, agent, routines, ENABLED_DISPATCH)
    return agent


def test_enricher_reports_an_overdue_routine(tmp_path):
    agent = _enrich(tmp_path, [{"id": "suite-health", "schedule": {"at": "08:00"}}])
    assert agent["health"] == "red"
    assert agent["health_kind"] == "overdue"
    assert agent["health_routine"] == "suite-health"
    assert agent["health_due_at"] == datetime(2026, 7, 29, 8, 0)
    assert agent["health_fault"] == "suite-health due 08:00"
    assert agent["health_sentence"] == (
        "Routine suite-health was due at 08:00 and has not run — 3h 46m late."
    )


def test_enricher_reports_a_never_run_agent(tmp_path):
    agent = _enrich(tmp_path, [])
    assert agent["health"] == "gray"
    assert agent["health_kind"] == "never_run"
    assert agent["health_fault"] == ""
    assert agent["health_sentence"] == "No run on record"


def test_enricher_supplies_the_timing_pair(tmp_path):
    agent = _enrich(tmp_path, [{"id": "nightly", "schedule": {"at": "23:00"}}])
    assert agent["next_run"] == datetime(2026, 7, 29, 23, 0)
    assert agent["next_run_detail"]["routine_id"] == "nightly"
    assert agent["last_run"] is None


def test_enricher_ignores_schedules_when_dispatch_is_off(tmp_path):
    g = _fleet_group(tmp_path, [{"id": "suite-health", "schedule": {"at": "08:00"}}])
    g["dispatch"] = {"enabled": False}
    agent = {"name": "product"}
    with patch("agency.app.clock_now", return_value=NOW):
        _apply_agent_status(g, agent, g["agents_full"][0]["routines"], {"enabled": False})
    assert agent["health_kind"] == "never_run"
    assert agent["next_run"] is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_agent_status.py -k enricher -v`
Expected: `ImportError: cannot import name '_apply_agent_status' from 'agency.app'`.

- [ ] **Step 3: Extend the health imports in `agency/app.py`**

The existing block imports `evaluate_agent_health`, `grace_window`, `routine_schedules`, and `schedule_state` from `agency.health`. Replace `schedule_state` with the new names and drop `evaluate_agent_health`, which is now reached through `describe_agent_health`:

```python
from agency.health import (
    describe_agent_health,
    elapsed_coarse,
    elapsed_precise,
    grace_window,
    routine_schedules,
    schedule_lateness,
)
```

Leave `compute_next_run_detail`'s own imports of `every_marker_path` and `parse_every` alone.

- [ ] **Step 4: Replace `_agent_health` with the enricher**

Delete `_agent_health` entirely and put this in its place:

```python
def _fault_line(status, now: datetime) -> str:
    """One terse line for the card, empty when there is nothing wrong."""
    if status.kind == "job_failed":
        return "last job failed"
    if status.kind not in ("overdue", "due"):
        return ""
    same_day = status.due_at.date() == now.date()
    stamp = status.due_at.strftime("%H:%M" if same_day else "%Y-%m-%d %H:%M")
    return f"{status.routine_id} due {stamp}"


def _health_sentence(status, job, now: datetime) -> str:
    """The full explanation, shared by the card tooltip and the queue item."""
    if status.kind == "job_failed":
        return (
            f"Job {job.spec.job_id[:8]} exited {job.exit_code} after "
            f"{elapsed_precise(timedelta(seconds=job.duration_seconds or 0))}, "
            f"{relative_time(_job_finished_at(job))}."
        )
    if status.kind == "overdue":
        return (
            f"Routine {status.routine_id} was due at "
            f"{status.due_at.strftime('%H:%M')} and has not run — "
            f"{elapsed_precise(status.late)} late."
        )
    if status.kind == "due":
        return (
            f"Routine {status.routine_id} came due "
            f"{elapsed_precise(status.late)} ago; the dispatcher has not "
            "picked it up yet."
        )
    if status.kind == "never_run":
        return "No run on record"
    return "Healthy"


def _job_finished_at(job) -> datetime | None:
    stamp = job.completed_at or job.started_at
    if not stamp:
        return None
    try:
        return datetime.fromisoformat(stamp).replace(tzinfo=None)
    except ValueError:
        return None


def _apply_agent_status(g: dict, agent: dict, routines, dispatch_cfg: dict) -> None:
    """Attach run timing and the health reason to one fleet entry."""
    name = agent["name"]
    now = clock_now()
    last_run = get_agent_last_run(g, name)
    last_seen = last_run["at"] if last_run else get_agent_last_seen(g, name)
    detail = compute_next_run_detail(g, name, dispatch_cfg)

    dispatch_enabled = bool(g.get("dispatch", {}).get("enabled", False))
    schedules = routine_schedules(routines or ()) if dispatch_enabled else ()
    lateness = schedule_lateness(
        schedules,
        logs_root=Path(g["logs"]),
        agent_name=name,
        now=now,
        grace=grace_window(int(g.get("dispatch_interval", 15))),
    )
    executed = latest_executed_job(tuple(g.get("job_paths", ())), name)
    status = describe_agent_health(
        has_run=last_seen is not None or executed is not None,
        last_job_failed=executed is not None and executed.status == "failed",
        lateness=lateness,
        now=now,
    )

    agent.update(
        {
            "last_run": last_run,
            "last_seen": last_seen,
            "next_run": detail["when"] if detail else None,
            "next_run_detail": detail,
            "health": status.color,
            "health_kind": status.kind,
            "health_routine": status.routine_id,
            "health_due_at": status.due_at,
            "health_late": status.late,
            "health_job": executed,
            "health_fault": _fault_line(status, now),
            "health_sentence": _health_sentence(status, executed, now),
        }
    )
```

`_health_sentence` calls `relative_time`, which is defined earlier in the module, and `timedelta`, which `agency/app.py` already imports.

- [ ] **Step 5: Route `collect_agents_with_identity` through the enricher**

In `collect_agents_with_identity`, delete the `last_run`, `last_seen`, and `next_run_detail` locals and the `last_run`, `last_seen`, `health`, `next_run`, and `next_run_detail` keys from the `info` literal, then call the enricher. The loop body becomes:

```python
    for instance in g.get("agents_full", []):
        agent_name = instance["name"]
        identity = instance.get("identity") or {}
        open_count = sum(1 for c in observations if c.get("agent") == agent_name and c.get("status") == "open")
        info = {
            "name": agent_name,
            "display_name": identity.get("display_name") or agent_name,
            "title": identity.get("title", ""),
            "emoji": identity.get("emoji", ""),
            "open_observations": open_count,
            "is_subagent": False,
            "has_headshot": False,
            "integration": instance["integration"],
            "running": is_agent_running(g, agent_name, run_timeout),
        }
        _apply_agent_status(g, info, instance.get("routines"), dispatch_cfg)
        agents.append(info)
```

- [ ] **Step 6: Route `build_dashboard_fleet` through the enricher**

In `build_dashboard_fleet`, delete the `last_run` and `last_seen` locals, remove `"health": _agent_health(...)` from the appended literal, and call the enricher after the append. The loop body becomes:

```python
    dispatch_cfg = g.get("dispatch", {})
    for instance in group.agents.values():
        current = _newest_active_job(tuple(g.get("job_paths", ())), instance.name)
        selector = (
            current.spec.memory.selector
            if current is not None
            else (instance.default_memory.model_dump(mode="json") if instance.default_memory is not None else {"scope": "agent"})
        )
        fleet.append(
            {
                "name": instance.name,
                "display_name": instance.identity.display_name or instance.name,
                "title": instance.identity.title,
                "emoji": instance.identity.emoji,
                "blueprint": instance.blueprint,
                "integration": instance.integration,
                "open_observations": sum(1 for item in observations if item.get("agent") == instance.name and item.get("status") == "open"),
                "memory_label": _dashboard_memory_label(selector, snapshot.config.memory.channels),
            }
        )
        _apply_agent_status(g, fleet[-1], instance.routines, dispatch_cfg)
        _overlay_dashboard_job_state(fleet[-1], current, g["key"])
```

`_overlay_dashboard_job_state` must stay last: it owns `running`, which the enricher does not set on this path.

- [ ] **Step 7: Run the fleet tests**

Run: `.venv/Scripts/python -m pytest tests/test_agent_status.py tests/test_agent_health_fleet.py tests/test_dashboard.py -q`
Expected: PASS. If a test fails on a missing `health` key, the enricher call was placed before the dict existed.

- [ ] **Step 8: Run the full suite**

Run: `.venv/Scripts/python -m pytest tests/ -q`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add agency/app.py tests/test_agent_status.py
git commit -m "refactor(dashboard): enrich fleet entries in one place"
```

---

### Task 4: Fleet cards

**Files:**
- Modify: `agency/app.py`, `agency/templates/home.html`
- Test: `tests/test_dashboard.py`

**Interfaces:**
- Consumes: every `health_*`, `last_run`, and `next_run` key `_apply_agent_status` sets in Task 3.
- Produces: an `initials` Jinja filter, `initials(name: str) -> str`; the Zone 1 card grid.

**Reference:** `docs/superpowers/specs/assets/2026-07-29-inbox-agent-health-cards/fleet-cards.png`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_dashboard.py`:

```python
def test_initials_filter_builds_a_two_letter_avatar():
    assert app_mod.initials("Duncan Idaho") == "DI"
    assert app_mod.initials("Lady Jessica Atreides") == "LJ"
    assert app_mod.initials("advisor") == "AD"
    assert app_mod.initials("") == "?"


def test_fleet_cards_render_both_timing_values(monkeypatch, tmp_path, raw_config):
    client, _, group_root = _seed_dashboard_app(monkeypatch, tmp_path, raw_config)

    response = client.get("/newsletter/")

    assert response.status_code == 200
    assert "never run" in response.text
    assert "/newsletter/agents/advisor/routines" in response.text


def test_overdue_agent_renders_a_fault_line(monkeypatch, tmp_path, raw_config):
    client, _, group_root = _seed_dashboard_app(monkeypatch, tmp_path, raw_config)
    (group_root / "logs" / "2026-07-16" / "advisor-run.out").write_text("x", encoding="utf-8")

    with patch("agency.app.clock_now", return_value=datetime(2026, 7, 16, 12, 0)):
        response = client.get("/newsletter/")

    assert "daily-review due 09:00" in response.text
    assert 'title="Routine daily-review was due at 09:00' in response.text
```

`patch` and `datetime` are already imported by this module; confirm before adding duplicates.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_dashboard.py -k "initials or fleet_cards or fault_line" -v`
Expected: `AttributeError: module 'agency.app' has no attribute 'initials'`.

- [ ] **Step 3: Add the `initials` filter**

In `agency/app.py`, add beside the other filter registrations, immediately after `templates.env.filters["relative_future"] = relative_future`:

```python
def initials(name: str) -> str:
    """Two-letter avatar for an agent with no configured emoji."""
    words = (name or "").split()
    if not words:
        return "?"
    if len(words) == 1:
        return words[0][:2].upper()
    return (words[0][0] + words[1][0]).upper()


templates.env.filters["initials"] = initials
```

- [ ] **Step 4: Replace Zone 1 in `agency/templates/home.html`**

Replace the whole `<div class="flex flex-wrap items-center gap-x-3 gap-y-1.5">` block and its `{% for a in fleet_agents %}` body — everything between `{% if fleet_agents %}` and the `<div class="mt-1.5 ...">` counter line — with:

```html
  <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2 items-start">
    {% for a in fleet_agents %}
    <div class="rounded-lg border px-3 py-2 transition-colors
         {% if a.health == 'red' %}border-rose-200 dark:border-rose-900 bg-rose-50 dark:bg-rose-950/25
         {% elif a.health == 'amber' %}border-amber-200 dark:border-amber-900 bg-amber-50 dark:bg-amber-950/25
         {% elif a.health == 'gray' %}border-gray-200 dark:border-gray-700 bg-gray-100/60 dark:bg-gray-800/60 opacity-75
         {% else %}border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800{% endif %}">

      <a href="{{ a.profile_href or ('/' ~ group ~ '/agents/' ~ a.name) }}" class="flex items-center gap-2 group">
        <span class="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-gray-200 dark:bg-gray-700 text-xs font-semibold text-gray-700 dark:text-gray-200">{{ a.emoji or (a.display_name or a.name) | initials }}</span>
        <span class="truncate text-sm font-semibold text-gray-900 dark:text-gray-100 group-hover:underline">{{ a.display_name or a.name }}</span>
        {% if a.open_observations %}<span class="rounded-full bg-amber-100 dark:bg-amber-900/50 px-1.5 text-xs font-mono text-amber-700 dark:text-amber-300">{{ a.open_observations }}</span>{% endif %}
        {% if a.running %}
        <span class="ml-auto text-emerald-500 text-xs leading-none animate-pulse" title="Running">&#9679;</span>
        {% else %}
        <span class="ml-auto text-xs leading-none {% if a.health == 'green' %}text-emerald-500{% elif a.health == 'amber' %}text-amber-500{% elif a.health == 'gray' %}text-gray-400 dark:text-gray-500{% else %}text-rose-500{% endif %}"
              title="{{ a.health_sentence }}">&#9679;</span>
        {% endif %}
      </a>

      {% if a.title %}<div class="mt-0.5 truncate text-xs text-gray-500 dark:text-gray-400">{{ a.title }}</div>{% endif %}

      <div class="mt-1.5 flex flex-wrap gap-1">
        {% if a.blueprint %}<span class="rounded bg-gray-200 dark:bg-gray-700 px-1.5 text-xs text-gray-700 dark:text-gray-200">{{ a.blueprint }}</span>{% endif %}
        {% if a.integration %}<span class="rounded bg-slate-200 dark:bg-slate-700 px-1.5 text-xs text-slate-700 dark:text-slate-200">{{ a.integration }}</span>{% endif %}
      </div>

      <div class="mt-2 flex items-center justify-between border-t border-gray-200 dark:border-gray-700 pt-1.5 font-mono text-xs">
        {% if a.last_run %}
        <a href="/{{ group }}/logs/view?path={{ a.last_run.path | urlencode }}"
           class="text-gray-600 dark:text-gray-300 hover:underline"
           title="{{ a.last_run.at.strftime('%Y-%m-%d %H:%M') }}">{{ a.last_run.at | relative_time }}</a>
        {% else %}
        <span class="text-gray-500 dark:text-gray-400">never run</span>
        {% endif %}

        {% if a.running %}
        <span class="text-emerald-600 dark:text-emerald-400">running</span>
        {% elif a.health_kind == 'overdue' %}
        <a href="/{{ group }}/agents/{{ a.name }}/routines" class="text-rose-600 dark:text-rose-300 hover:underline">overdue {{ a.health_late | elapsed }}</a>
        {% elif a.health_kind == 'due' %}
        <a href="/{{ group }}/agents/{{ a.name }}/routines" class="text-amber-600 dark:text-amber-300 hover:underline">due now</a>
        {% elif a.next_run %}
        <a href="/{{ group }}/agents/{{ a.name }}/routines" class="text-sky-600 dark:text-sky-300 hover:underline">{{ a.next_run | relative_future }}</a>
        {% else %}
        <span class="text-gray-400 dark:text-gray-500">&mdash;</span>
        {% endif %}
      </div>

      {% if a.health_fault %}
      <div class="mt-1 text-xs {% if a.health == 'red' %}text-rose-700 dark:text-rose-200{% else %}text-amber-700 dark:text-amber-200{% endif %}">{{ a.health_fault }}</div>
      {% endif %}
    </div>
    {% endfor %}
  </div>
```

Leave the `<div class="mt-1.5 ...">` counter line and the `{% else %}No agents configured{% endif %}` branch untouched.

- [ ] **Step 5: Register the `elapsed` filter the template uses**

The template renders `a.health_late | elapsed`. In `agency/app.py`, beside the `initials` registration:

```python
templates.env.filters["elapsed"] = elapsed_coarse
```

- [ ] **Step 6: Run the dashboard tests**

Run: `.venv/Scripts/python -m pytest tests/test_dashboard.py -v`
Expected: PASS, including `test_dashboard_reports_never_run_agents_separately`, whose `'title="No run on record"'` assertion is satisfied by `health_sentence` on the gray dot.

- [ ] **Step 7: Compare the rendered page against the sketch**

Run: `.venv/Scripts/python -m agency.app`
Open `http://127.0.0.1:8500/atreides/`. Confirm against `docs/superpowers/specs/assets/2026-07-29-inbox-agent-health-cards/fleet-cards.png`: three cards per row, initials avatars, Duncan Idaho rose with `overdue 3h` and the fault line `suite-health due 08:00`, Paul Atreides dimmed and reading `never run` and an em dash, the other three green with a `Nd ago` / `Nd away` pair. Stop the server.

- [ ] **Step 8: Commit**

```bash
git add agency/app.py agency/templates/home.html tests/test_dashboard.py
git commit -m "feat(dashboard): render the fleet as health cards"
```

---

### Task 5: Attention Queue health items

**Files:**
- Modify: `agency/app.py`, `agency/templates/home.html`
- Test: `tests/test_dashboard.py`

**Interfaces:**
- Consumes: the `health_kind`, `health_sentence`, `health_job`, and `running` keys from Task 3.
- Produces: `build_health_items(g: dict, agents: list[dict]) -> list[dict]`, each item having `name`, `display_name`, `kind`, `label`, `sentence`, `last_line`, `routines_href`, `job_href`, `run_href`; and the `health_items` template variable.

**Reference:** `docs/superpowers/specs/assets/2026-07-29-inbox-agent-health-cards/attention-queue.png`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_dashboard.py`:

```python
def test_health_items_cover_faults_and_skip_healthy_agents():
    g = {"key": "newsletter"}
    agents = [
        {"name": "a", "display_name": "A", "health_kind": "healthy", "health_sentence": "Healthy", "health_job": None},
        {"name": "b", "display_name": "B", "health_kind": "never_run", "health_sentence": "No run on record", "health_job": None},
        {"name": "c", "display_name": "C", "health_kind": "overdue", "health_sentence": "Routine r was due.", "health_job": None},
        {"name": "d", "display_name": "D", "health_kind": "due", "health_sentence": "Routine r came due.", "health_job": None},
    ]
    items = app_mod.build_health_items(g, agents)

    assert [item["name"] for item in items] == ["c", "d"]
    assert items[0]["label"] == "overdue"
    assert items[0]["sentence"] == "Routine r was due."
    assert items[0]["routines_href"] == "/newsletter/agents/c/routines"
    assert items[0]["run_href"] == "/newsletter/agents/c"
    assert items[0]["last_line"] == ""


def test_a_running_agent_produces_no_health_item():
    g = {"key": "newsletter"}
    agents = [{"name": "c", "display_name": "C", "health_kind": "overdue", "health_sentence": "s", "health_job": None, "running": True}]

    assert app_mod.build_health_items(g, agents) == []


def test_overdue_agent_appears_in_the_attention_queue(monkeypatch, tmp_path, raw_config):
    client, _, group_root = _seed_dashboard_app(monkeypatch, tmp_path, raw_config)
    (group_root / "logs" / "2026-07-16" / "advisor-run.out").write_text("x", encoding="utf-8")

    with patch("agency.app.clock_now", return_value=datetime(2026, 7, 16, 12, 0)):
        response = client.get("/newsletter/")

    assert "Routine daily-review was due at 09:00" in response.text
    assert "No items need attention right now." not in response.text
    assert "Open routine" in response.text


def test_never_run_agent_produces_no_queue_item(monkeypatch, tmp_path, raw_config):
    client, _, group_root = _seed_dashboard_app(monkeypatch, tmp_path, raw_config)

    response = client.get("/newsletter/")

    assert "No items need attention right now." in response.text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_dashboard.py -k "health_items or running_agent or attention_queue or no_queue_item" -v`
Expected: `AttributeError: module 'agency.app' has no attribute 'build_health_items'`.

- [ ] **Step 3: Implement the builder**

In `agency/app.py`, add immediately after `build_dashboard_fleet`:

```python
_HEALTH_LABELS = {
    "job_failed": "last run failed",
    "overdue": "overdue",
    "due": "due",
}


def build_health_items(g: dict, agents: list[dict]) -> list[dict]:
    """Turn unhealthy fleet entries into Attention Queue rows."""
    key = g["key"]
    items = []
    for agent in agents:
        label = _HEALTH_LABELS.get(agent.get("health_kind"))
        if label is None or agent.get("running"):
            continue
        job = agent.get("health_job")
        items.append(
            {
                "name": agent["name"],
                "display_name": agent.get("display_name") or agent["name"],
                "kind": agent["health_kind"],
                "label": label,
                "sentence": agent.get("health_sentence", ""),
                "last_line": _last_run_line(job),
                "routines_href": f"/{key}/agents/{agent['name']}/routines",
                "job_href": f"/{key}/jobs/{job.spec.job_id}" if job is not None else "",
                "run_href": f"/{key}/agents/{agent['name']}",
            }
        )
    return items


def _last_run_line(job) -> str:
    if job is None:
        return ""
    finished = _job_finished_at(job)
    if finished is None:
        return ""
    outcome = "failed" if job.status == "failed" else "succeeded"
    duration = elapsed_precise(timedelta(seconds=job.duration_seconds or 0))
    return f"last run {finished.strftime('%Y-%m-%d %H:%M')} · {outcome} in {duration}"
```

- [ ] **Step 4: Wire the home route**

In the dashboard route, directly below `agents = build_dashboard_fleet(g)`, add:

```python
    health_items = build_health_items(g, agents)
```

Add `"health_items": health_items,` to the `TemplateResponse` context beside `"actionable_proposals"`, and change the `needs_action_count` assignment to include them:

```python
    needs_action_count = len(actionable_proposals) + len(floated_open_observations) + len(health_items)
```

`needs_action_count` is computed before `agents`, so move its assignment to sit after the `health_items` line.

- [ ] **Step 5: Render health items in Zone 3**

In `agency/templates/home.html`, change the Attention Queue guard from

```html
    {% if actionable_proposals or open_observations or floated_observations %}
```

to

```html
    {% if health_items or actionable_proposals or open_observations or floated_observations %}
```

and insert this block immediately after that line, above the `{# ─ Proposals (high priority) ─ #}` comment:

```html
    {# ─ Agent health (highest priority) ─ #}
    {% for h in health_items %}
    <div class="block border rounded-md px-3 py-2.5 mb-2 {% if h.kind == 'due' %}border-amber-200 dark:border-amber-800 bg-amber-50/50 dark:bg-amber-950/30{% else %}border-rose-200 dark:border-rose-800 bg-rose-50/50 dark:bg-rose-950/30{% endif %}">
      <div class="flex items-center gap-1.5">
        <span class="text-sm font-semibold {% if h.kind == 'due' %}text-amber-700 dark:text-amber-300{% else %}text-rose-700 dark:text-rose-300{% endif %}">{{ h.label }}</span>
        <span class="inline-block px-2 py-0.5 rounded-full text-xs bg-gray-200 dark:bg-gray-700 text-gray-700 dark:text-gray-200">{{ h.display_name }}</span>
      </div>
      <div class="text-base text-gray-800 dark:text-gray-200 mt-0.5 leading-tight">{{ h.sentence }}</div>
      {% if h.last_line %}
      <div class="mt-1 font-mono text-xs text-gray-500 dark:text-gray-400">{{ h.last_line }}</div>
      {% endif %}
      <div class="mt-1.5 flex flex-wrap gap-3 text-sm">
        <a href="{{ h.routines_href }}" class="text-agency-700 dark:text-agency-200 hover:underline">Open routine</a>
        {% if h.job_href %}<a href="{{ h.job_href }}" class="text-agency-700 dark:text-agency-200 hover:underline">Last job log</a>{% endif %}
        <a href="{{ h.run_href }}" class="text-agency-700 dark:text-agency-200 hover:underline">Run now</a>
      </div>
    </div>
    {% endfor %}
```

- [ ] **Step 6: Run the dashboard tests**

Run: `.venv/Scripts/python -m pytest tests/test_dashboard.py -v`
Expected: PASS.

- [ ] **Step 7: Compare the rendered queue against the sketch**

Run: `.venv/Scripts/python -m agency.app`
Open `http://127.0.0.1:8500/atreides/`. Confirm against `attention-queue.png`: the header counts one item, the card reads `overdue`, a `Duncan Idaho` pill, the full sentence with the compound `3h NNm late`, the monospace last-run line, and the three links. Confirm the fleet footer's `1 needs attention` and the queue can no longer disagree. Stop the server.

- [ ] **Step 8: Run the full suite**

Run: `.venv/Scripts/python -m pytest tests/ -q`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add agency/app.py agency/templates/home.html tests/test_dashboard.py
git commit -m "feat(dashboard): file agent faults in the attention queue"
```

---

### Task 6: Schedule status on the Routines tab

**Files:**
- Modify: `agency/web/routes/agent_detail.py`, `agency/templates/agent_detail_routines.html`
- Test: `tests/test_agent_detail.py`

**Interfaces:**
- Consumes: `schedule_lateness`, `elapsed_coarse`, `grace_window`, `routine_schedules` from Tasks 1 and 2; `parse_every`, `at_marker_path`, `every_marker_path` from `agency.dispatch.schedule`; the existing `resolve_group_paths`.
- Produces: a `routine_status` key in `_routines_context`, a list of `{"routine_id", "schedule", "last_fired", "next_due"}` dictionaries in configured order.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_agent_detail.py`. The module seeds its client with
`_seed_app(monkeypatch, tmp_path, raw_config)` and its fixture agent is
`advisor` in group `newsletter`, carrying one `daily-review` routine scheduled
`at 09:00`.

```python
def test_routines_get_lists_schedule_status(monkeypatch, tmp_path, raw_config):
    client, _ = _seed_app(monkeypatch, tmp_path, raw_config)

    response = client.get("/newsletter/agents/advisor/routines")

    assert response.status_code == 200
    assert "Schedule status" in response.text
    assert "Last fired" in response.text
    assert "Next due" in response.text
    assert "at 09:00" in response.text
    assert "never" in response.text
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_agent_detail.py -k schedule_status -v`
Expected: FAIL, `assert 'Schedule status' in ...`.

- [ ] **Step 3: Build the status rows**

In `agency/web/routes/agent_detail.py`, add above `_routines_context`:

```python
def _routine_status(snapshot, group_id: str, instance) -> list[dict[str, Any]]:
    """Pair each configured routine with what actually fired."""
    group = snapshot.config.groups[group_id]
    logs_root = resolve_group_paths(group).logs
    now = clock_now()
    grace = grace_window(int(snapshot.config.agency.dispatch.interval))
    rows = []
    for schedule in routine_schedules(instance.routines):
        if schedule.at:
            spec = f"at {schedule.at}"
            day = now.strftime("%Y-%m-%d")
            marker = at_marker_path(logs_root, instance.name, schedule.routine_id, day)
        elif schedule.every:
            spec = f"every {schedule.every}"
            marker = every_marker_path(logs_root, instance.name, schedule.routine_id)
        else:
            spec = "no schedule"
            marker = None
        if schedule.conditional:
            spec = "conditional"
        rows.append(
            {
                "routine_id": schedule.routine_id,
                "enabled": schedule.enabled,
                "schedule": spec,
                "last_fired": _marker_stamp(marker),
                "next_due": _next_due_text(
                    schedule, logs_root, instance.name, now, grace
                ),
            }
        )
    return rows


def _marker_stamp(marker) -> str:
    if marker is None:
        return "never"
    try:
        return datetime.fromtimestamp(marker.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
    except OSError:
        return "never"


def _next_due_text(schedule, logs_root, agent_name, now, grace) -> str:
    if schedule.conditional or not schedule.enabled:
        return "—"
    lateness = schedule_lateness(
        (schedule,),
        logs_root=logs_root,
        agent_name=agent_name,
        now=now,
        grace=grace,
    )
    if lateness is None:
        return "on schedule"
    if lateness.state == "overdue":
        return f"overdue {elapsed_coarse(now - lateness.due_at)}"
    return "due now"
```

Add the imports this needs to the top of `agency/web/routes/agent_detail.py`.
`resolve_group_paths` is already imported from `agency.configuration`; add only:

```python
from datetime import datetime

from agency.clock import now as clock_now
from agency.dispatch.schedule import at_marker_path, every_marker_path
from agency.health import (
    elapsed_coarse,
    grace_window,
    routine_schedules,
    schedule_lateness,
)
```

In `_routines_context`, add `"routine_status": _routine_status(snapshot, group_id, instance),` to the `result` literal.

- [ ] **Step 4: Render the table**

In `agency/templates/agent_detail_routines.html`, insert above the `<form ...>` element:

```html
  {% if routine_status %}
  <div class="border-t border-gray-200 pt-4">
    <h3 class="font-semibold text-gray-900">Schedule status</h3>
    <table class="mt-2 w-full text-left text-sm text-gray-700">
      <thead class="text-xs uppercase tracking-wider text-gray-500">
        <tr><th class="py-1">Routine</th><th class="py-1">Schedule</th><th class="py-1">Last fired</th><th class="py-1">Next due</th></tr>
      </thead>
      <tbody>
        {% for r in routine_status %}
        <tr class="border-t border-gray-100">
          <td class="py-1 font-mono text-xs {% if not r.enabled %}line-through text-gray-400{% endif %}">{{ r.routine_id }}</td>
          <td class="py-1 font-mono text-xs">{{ r.schedule }}</td>
          <td class="py-1 font-mono text-xs">{{ r.last_fired }}</td>
          <td class="py-1 font-mono text-xs">{{ r.next_due }}</td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
  {% endif %}
```

- [ ] **Step 5: Run the agent detail tests**

Run: `.venv/Scripts/python -m pytest tests/test_agent_detail.py -q`
Expected: PASS. The `409` re-render paths pass `overrides` that omit `routine_status`; the `{% if routine_status %}` guard keeps those responses working, since an undefined name is falsy in Jinja.

- [ ] **Step 6: Commit**

```bash
git add agency/web/routes/agent_detail.py agency/templates/agent_detail_routines.html tests/test_agent_detail.py
git commit -m "feat(agents): show what each routine actually fired"
```

---

### Task 7: Browser coverage and final verification

**Files:**
- Modify: `tests/ui/dashboard.spec.ts`

**Interfaces:**
- Consumes: the rendered markup from Tasks 4 and 5.
- Produces: no code other tasks depend on. This is the closing gate.

- [ ] **Step 1: Read the existing spec**

Read `tests/ui/dashboard.spec.ts`. Its Attention Queue locator uses `page.getByText('Attention Queue', { exact: true })`, which still matches. Note the fixture config at `tests/ui/fixtures/config.yaml` and which agents it defines.

- [ ] **Step 2: Add the fleet card assertions**

Append a test that asserts one card per configured agent and that a card exposes a link to that agent's routines:

```ts
test('fleet cards expose run timing', async ({ page }) => {
  await page.goto('/newsletter/');
  const fleet = page.locator('a[href$="/routines"]');
  await expect(fleet.first()).toBeVisible();
});
```

Adapt the group segment and the agent count to the fixture you read in Step 1. Do not invent a group name.

- [ ] **Step 3: Run the browser tests**

Run: `npx playwright test tests/ui/dashboard.spec.ts`
Expected: PASS. If Playwright browsers are missing, run `npx playwright install chromium` first.

- [ ] **Step 4: Run the complete suite**

Run: `.venv/Scripts/python -m pytest tests/ -q`
Expected: PASS, with no skips introduced by this branch.

- [ ] **Step 5: Confirm the working tree holds nothing unrelated**

Run: `git status --short`
Expected: only files this plan names. `config.yaml`, `config.yaml.lock`, logs, and `.superpowers/` must not appear.

- [ ] **Step 6: Commit**

```bash
git add tests/ui/dashboard.spec.ts
git commit -m "test(dashboard): cover fleet cards in the browser suite"
```

---

## Self-Review

**Spec coverage.** Health reason model → Tasks 1 and 2. Fleet card anatomy, avatar, dot, dimming, observation pill → Task 4. Timing cells and their links → Tasks 3 and 4. Fault line → Tasks 3 and 4. Queue items, sentences, last-run line, three links, `needs_action_count`, empty state → Task 5. Routines tab table → Task 6. Testing section → distributed across every task, with the browser spec in Task 7.

**Known deviation.** The spec says `collect_agents_with_identity` already computes the timing fields; that is true, but `build_dashboard_fleet` is the path that renders whenever services are healthy and it computed none of them. Task 3 resolves this by routing both builders through one enricher, which satisfies the spec's intent and removes the asymmetry that hid the data.

**Type consistency.** `Lateness` is produced in Task 1 and consumed in Tasks 2, 3, and 6 with the same field names. `AgentHealth` is produced in Task 2 and consumed only through `_apply_agent_status`. The `health_*` dictionary keys set in Task 3 are the exact keys read in Tasks 4 and 5. `elapsed_coarse` is registered as the `elapsed` Jinja filter in Task 4 and used unregistered in Task 6.

**Rollback.** Every task is a single commit with its own green suite, so any task can be reverted without touching its neighbours.
