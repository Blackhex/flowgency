# Scheduled Work Recovery And The Job Pool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A routine missed while the machine was unavailable is recovered on the next dispatch cycle, and every local backend job runs through a globally bounded worker pool.

**Architecture:** Firing becomes one predicate over *the most recent occurrence at or before now*, bounded by a new `schedule.catch_up` field that defaults to `today`. Execution gains a queue: `submit_job_request` launches only when the pool has capacity and otherwise leaves the record in the existing `queued` status, and a shared `drain()` — called at the end of submission, by every worker as it exits, and at the start of every dispatch cycle — starts waiting jobs in due-time order. `dispatch.daily_limit` is removed.

**Tech Stack:** Python 3.11+, FastAPI, Jinja2, Pydantic, pytest, PyYAML. Filesystem-backed durable state, `agency/fs/locks.exclusive_lock` for mutual exclusion.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-30-scheduled-work-recovery-design.md`. Approved mockups, normative for layout, ordering, and copy: `docs/superpowers/specs/assets/2026-07-30-scheduled-work-recovery/inbox-queue-strip.png` and `docs/superpowers/specs/assets/2026-07-30-scheduled-work-recovery/queue-strip-states.png`.
- Work in the existing worktree `.worktrees/scheduled-work-recovery` on branch `feat/scheduled-work-recovery`. Run every command from that directory.
- Test command: `python -m pytest tests/ -q`. Single test: `python -m pytest tests/test_x.py::test_y -v`.
- `catch_up` accepts exactly `none`, `today`, `always`, or a duration matching the existing `every` grammar `(\d+)(m|h|d)`.
- When `catch_up` is absent the effective value is `today`, for every cadence. There is no cadence-derived default.
- `agency.jobs.pool` defaults to `4` and must be an integer `>= 1`.
- Queue order is ascending `due_at`, ties broken by ascending `job_id`. Global across all groups.
- `JobSpec` is digest-signed; **never** add a field to `JobSpec`, `JobSpec.to_dict`, or anything that feeds `immutable_digest()`. Existing stored records would fail their digest check on read. New job metadata goes on `JobRecord`, which tolerates missing keys via dataclass defaults.
- A record occupies a pool slot when its status is `running` or `waiting_for_memory`, or when its status is `queued` and its `worker_pid` is not confirmed absent. `worker_alive` returning `None` means "cannot tell" and counts as alive.
- Never write to the repository's own `config.yaml`. Only `config.yaml.example` and files under `examples/` are edited.
- Commit messages follow Conventional Commits and the seven rules, as described in `AGENTS.md`.

---

### Task 1: Catch-up vocabulary

**Files:**
- Modify: `agency/dispatch/schedule.py`
- Test: `tests/test_dispatch_schedule.py`

**Interfaces:**
- Consumes: existing `parse_every`.
- Produces:
  - `DEFAULT_CATCH_UP: str = "today"`
  - `CatchUp = NamedTuple("CatchUp", [("kind", str), ("period", timedelta | None)])` where `kind` is one of `"none"`, `"today"`, `"always"`, `"duration"`.
  - `parse_catch_up(value: str | None) -> CatchUp | None` — `None` for a malformed value, the default for an absent one.
  - `catch_up_allows(occurrence: datetime, now: datetime, bound: CatchUp, grace: timedelta) -> bool`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_dispatch_schedule.py`:

```python
from datetime import datetime, timedelta

from agency.dispatch.schedule import (
    DEFAULT_CATCH_UP,
    catch_up_allows,
    parse_catch_up,
)


def test_absent_catch_up_is_today():
    assert DEFAULT_CATCH_UP == "today"
    assert parse_catch_up(None).kind == "today"
    assert parse_catch_up("").kind == "today"


def test_catch_up_keywords_parse():
    assert parse_catch_up("none").kind == "none"
    assert parse_catch_up("today").kind == "today"
    assert parse_catch_up("always").kind == "always"


def test_catch_up_duration_parses_with_the_every_grammar():
    bound = parse_catch_up("36h")
    assert bound.kind == "duration"
    assert bound.period == timedelta(hours=36)


def test_malformed_catch_up_is_rejected():
    assert parse_catch_up("sometimes") is None
    assert parse_catch_up("24") is None
    assert parse_catch_up("-1h") is None


def test_none_allows_only_inside_the_grace_window():
    grace = timedelta(minutes=17)
    occurrence = datetime(2026, 7, 29, 8, 0)
    bound = parse_catch_up("none")
    assert catch_up_allows(occurrence, datetime(2026, 7, 29, 8, 10), bound, grace) is True
    assert catch_up_allows(occurrence, datetime(2026, 7, 29, 8, 30), bound, grace) is False


def test_today_allows_only_the_current_calendar_day():
    grace = timedelta(minutes=17)
    bound = parse_catch_up("today")
    occurrence = datetime(2026, 7, 29, 8, 0)
    assert catch_up_allows(occurrence, datetime(2026, 7, 29, 20, 0), bound, grace) is True
    assert catch_up_allows(occurrence, datetime(2026, 7, 30, 3, 0), bound, grace) is False


def test_always_allows_any_age():
    grace = timedelta(minutes=17)
    bound = parse_catch_up("always")
    occurrence = datetime(2026, 7, 20, 8, 0)
    assert catch_up_allows(occurrence, datetime(2026, 7, 29, 20, 0), bound, grace) is True


def test_duration_allows_up_to_and_including_the_bound():
    grace = timedelta(minutes=17)
    bound = parse_catch_up("24h")
    occurrence = datetime(2026, 7, 29, 8, 0)
    assert catch_up_allows(occurrence, datetime(2026, 7, 30, 8, 0), bound, grace) is True
    assert catch_up_allows(occurrence, datetime(2026, 7, 30, 8, 1), bound, grace) is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_dispatch_schedule.py -v`
Expected: FAIL with `ImportError: cannot import name 'DEFAULT_CATCH_UP'`.

- [ ] **Step 3: Implement**

Add to `agency/dispatch/schedule.py`:

```python
from datetime import datetime, timedelta
from typing import NamedTuple

DEFAULT_CATCH_UP = "today"
_KEYWORDS = ("none", "today", "always")


class CatchUp(NamedTuple):
    kind: str
    period: timedelta | None


def parse_catch_up(value: str | None) -> CatchUp | None:
    """Return the recovery bound, or None when the value is malformed."""
    text = (value or "").strip() or DEFAULT_CATCH_UP
    if text in _KEYWORDS:
        return CatchUp(text, None)
    period = parse_every(text)
    if period is None:
        return None
    return CatchUp("duration", period)


def catch_up_allows(
    occurrence: datetime,
    now: datetime,
    bound: CatchUp,
    grace: timedelta,
) -> bool:
    """Whether an occurrence is still worth running at ``now``."""
    age = now - occurrence
    if bound.kind == "always":
        return True
    if bound.kind == "today":
        return occurrence.date() == now.date()
    if bound.kind == "duration":
        return age <= bound.period
    return age < grace
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_dispatch_schedule.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agency/dispatch/schedule.py tests/test_dispatch_schedule.py
git commit -m "feat(dispatch): add the catch-up recovery vocabulary"
```

---

### Task 2: Most recent occurrence

**Files:**
- Modify: `agency/dispatch/schedule.py`
- Test: `tests/test_dispatch_schedule.py`

**Interfaces:**
- Consumes: `parse_every` from Task 1's module.
- Produces:
  - `last_at_occurrence(at: str, now: datetime) -> datetime | None`
  - `last_every_occurrence(anchor: datetime, every: str, now: datetime) -> datetime | None`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_dispatch_schedule.py`:

```python
from agency.dispatch.schedule import last_at_occurrence, last_every_occurrence


def test_at_occurrence_is_today_once_the_time_has_passed():
    now = datetime(2026, 7, 29, 11, 57)
    assert last_at_occurrence("08:00", now) == datetime(2026, 7, 29, 8, 0)


def test_at_occurrence_is_yesterday_before_the_time():
    now = datetime(2026, 7, 29, 3, 0)
    assert last_at_occurrence("08:00", now) == datetime(2026, 7, 28, 8, 0)


def test_at_occurrence_at_exactly_the_target_is_today():
    now = datetime(2026, 7, 29, 8, 0)
    assert last_at_occurrence("08:00", now) == datetime(2026, 7, 29, 8, 0)


def test_malformed_at_has_no_occurrence():
    assert last_at_occurrence("25:00", datetime(2026, 7, 29, 8, 0)) is None


def test_every_occurrence_steps_from_the_anchor():
    anchor = datetime(2026, 7, 26, 9, 0)
    now = datetime(2026, 7, 29, 11, 57)
    assert last_every_occurrence(anchor, "6h", now) == datetime(2026, 7, 29, 9, 0)


def test_every_occurrence_is_none_before_the_first_period_elapses():
    anchor = datetime(2026, 7, 29, 9, 0)
    now = datetime(2026, 7, 29, 11, 0)
    assert last_every_occurrence(anchor, "6h", now) is None


def test_malformed_every_has_no_occurrence():
    anchor = datetime(2026, 7, 29, 9, 0)
    assert last_every_occurrence(anchor, "soon", datetime(2026, 7, 30, 9, 0)) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_dispatch_schedule.py -k occurrence -v`
Expected: FAIL with `ImportError: cannot import name 'last_at_occurrence'`.

- [ ] **Step 3: Implement**

Add to `agency/dispatch/schedule.py`:

```python
from datetime import timedelta


def last_at_occurrence(at: str, now: datetime) -> datetime | None:
    """The newest ``at`` occurrence at or before ``now``, or None if malformed."""
    try:
        target = datetime.strptime(at.strip(), "%H:%M").time()
    except (AttributeError, ValueError):
        return None
    today = datetime.combine(now.date(), target)
    if now >= today:
        return today
    return today - timedelta(days=1)


def last_every_occurrence(
    anchor: datetime,
    every: str,
    now: datetime,
) -> datetime | None:
    """The newest ``every`` occurrence at or before ``now``, counted from the anchor."""
    period = parse_every(every)
    if period is None or period.total_seconds() <= 0:
        return None
    elapsed = (now - anchor).total_seconds()
    if elapsed < period.total_seconds():
        return None
    steps = int(elapsed // period.total_seconds())
    return anchor + steps * period
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_dispatch_schedule.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agency/dispatch/schedule.py tests/test_dispatch_schedule.py
git commit -m "feat(dispatch): compute the most recent schedule occurrence"
```

---

### Task 3: `catch_up` in the configuration model

**Files:**
- Modify: `agency/configuration/models.py:93-96` (`ScheduleRule`), `agency/configuration/models.py:468-488` (`_validate_rule`)
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: `parse_catch_up` from Task 1.
- Produces: `ScheduleRule.catch_up: str | None`, rejected at validation with code `invalid-dispatch-rule` when malformed.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:

```python
def test_schedule_accepts_a_catch_up_value(tmp_path):
    config = _write_minimal_config(tmp_path, catch_up="always")
    parsed = load_config(config)
    routine = parsed.groups["grp"].agents["product"].routines[0]
    assert routine.schedule.catch_up == "always"


def test_schedule_catch_up_defaults_to_none_in_the_model(tmp_path):
    config = _write_minimal_config(tmp_path)
    parsed = load_config(config)
    routine = parsed.groups["grp"].agents["product"].routines[0]
    assert routine.schedule.catch_up is None


def test_malformed_catch_up_is_rejected(tmp_path):
    config = _write_minimal_config(tmp_path, catch_up="sometimes")
    with pytest.raises(ValidationFailed) as error:
        load_config(config)
    assert any(issue.code == "invalid-dispatch-rule" for issue in error.value.issues)
```

Add the helper alongside them, matching the existing config fixtures in that file — a single group `grp` with one agent `product` carrying one routine whose schedule is `at: "08:00"` plus the optional `catch_up` line.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_config.py -k catch_up -v`
Expected: FAIL — `ScheduleRule` forbids the extra key `catch_up`.

- [ ] **Step 3: Implement**

In `agency/configuration/models.py`:

```python
class ScheduleRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    at: str | None = None
    every: str | None = None
    catch_up: str | None = None
```

Extend `_validate_rule`, after the existing at/every check:

```python
    from agency.dispatch.schedule import parse_catch_up

    catch_up = rule.get("catch_up")
    if catch_up is not None and parse_catch_up(str(catch_up)) is None:
        return _build_issue(
            code="invalid-dispatch-rule",
            scope=scope,
            field="schedule.catch_up",
            message=f"Invalid catch_up value: {catch_up}",
            hint="Use none, today, always, or a duration such as 30m, 8h, or 7d.",
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_config.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agency/configuration/models.py tests/test_config.py
git commit -m "feat(config): accept a catch_up bound on routine schedules"
```

---

### Task 4: Remove `dispatch.daily_limit`

**Files:**
- Modify: `agency/configuration/models.py:129-133` (`GroupDispatch`)
- Modify: `agency/configuration/patches.py:40-76` (`GroupDispatchPatch`, `GroupSettingsStatePatch`, `GroupCreateStatePatch`)
- Modify: `agency/web/routes/admin_groups.py:200-210,545-560`
- Modify: `agency/templates/admin_org_edit.html:130-145`
- Modify: `agency/dispatch/run.py:60-95`
- Modify: `config.yaml.example`, `examples/code-review-team/config.yaml`, `examples/content-team/config.yaml`, `kb/dispatch.md`, `kb/configuration.md`
- Test: `tests/test_config.py`, `tests/test_config_patches.py`, `tests/test_group_settings.py`, `tests/test_dispatch_run.py`

**Interfaces:**
- Produces: `GroupDispatch` with only `enabled: bool`. Any configuration carrying `daily_limit` fails validation.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_config.py`:

```python
def test_group_dispatch_rejects_daily_limit(tmp_path):
    config = _write_minimal_config(tmp_path, dispatch_daily_limit=20)
    with pytest.raises(ValidationFailed):
        load_config(config)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_config.py::test_group_dispatch_rejects_daily_limit -v`
Expected: FAIL — the key is currently accepted.

- [ ] **Step 3: Remove the field everywhere**

```python
class GroupDispatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    enabled: bool = False
```

In `agency/configuration/patches.py` delete `daily_limit` from `GroupDispatchPatch` and `dispatch_daily_limit` from `GroupSettingsStatePatch` and `GroupCreateStatePatch`, along with every write of those values into the raw mapping.

In `agency/web/routes/admin_groups.py` delete the `dispatch_daily_limit` context entry and the `daily_limit = int(...)` form read. In `agency/templates/admin_org_edit.html` delete the Daily Limit label and input.

In `agency/dispatch/run.py` delete `daily_limit = group.dispatch.daily_limit`, both `out_count = len(list(log_dir.glob("*.out")))` blocks, and the two limit checks. Keep the `log_dir.mkdir(parents=True, exist_ok=True)` call, because the marker path for an `at` rule lives under a day directory.

In `config.yaml.example` and both `examples/*/config.yaml`, delete the `daily_limit:` lines. In `kb/dispatch.md` delete the `daily_limit` sentence in the install section and the closing paragraph about a group reaching its limit. In `kb/configuration.md` delete the `daily_limit` row or line.

Update every test that constructs a config with `daily_limit`: `tests/test_dispatch_run.py::_write_config` loses its `daily_limit` parameter and the line it emits; `tests/test_group_settings.py` and `tests/test_config_patches.py` lose their `dispatch_daily_limit` arguments and assertions. Delete tests whose only subject is the limit — search with `python -m pytest tests/ -q -k daily_limit --collect-only`.

- [ ] **Step 4: Run the full suite**

Run: `python -m pytest tests/ -q`
Expected: PASS. The repository's own `config.yaml` is untouched and still carries the key; that is expected and is the operator's manual migration.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(dispatch)!: remove the per-group daily job limit"
```

Body must carry `BREAKING CHANGE: groups.<group>.dispatch.daily_limit is rejected; delete the key from config.yaml.`

---

### Task 5: The runner recovers the last missed occurrence

**Files:**
- Modify: `agency/dispatch/run.py`
- Test: `tests/test_dispatch_run.py`

**Interfaces:**
- Consumes: `parse_catch_up`, `catch_up_allows`, `last_at_occurrence`, `last_every_occurrence`, `at_marker_path`, `every_marker_path` from Tasks 1 and 2; `agency.clock.now`; `agency.health.grace_window`.
- Produces: `run_dispatch_cycle(config, config_path, launcher=None) -> None` with unchanged signature. `check_at_rule` and `check_every_rule` are deleted.

- [ ] **Step 1: Write the failing tests**

In `tests/test_dispatch_run.py`, delete the six `check_at_rule` / `check_every_rule` tests and their import, then add:

```python
import os
from datetime import datetime

from agency.dispatch.run import run_dispatch_cycle
from agency.dispatch.schedule import at_marker_path, every_marker_path


class _RecordingLauncher:
    def __init__(self):
        self.launched = []

    def launch(self, authority):
        self.launched.append(authority.job_id)
        return SimpleNamespace(worker_pid=4321)


def test_missed_morning_occurrence_recovers_later_the_same_day(
    tmp_path, monkeypatch
):
    workspace, group_root, config_path, _ = _make_group(tmp_path)
    _write_config(
        config_path,
        workspace,
        group_root,
        routines=[{"id": "suite-health", "prompt_name": "daily-review",
                   "schedule": {"at": "08:00"}}],
    )
    monkeypatch.setenv("AGENCY_FIXED_NOW", "2026-07-29T11:57:00")
    launcher = _RecordingLauncher()

    run_dispatch_cycle(None, config_path, launcher)

    assert len(launcher.launched) == 1
    marker = at_marker_path(
        group_root / "logs", "product", "suite-health", "2026-07-29"
    )
    assert marker.exists()


def test_recovery_marks_the_occurrence_day_not_today(tmp_path, monkeypatch):
    workspace, group_root, config_path, _ = _make_group(tmp_path)
    _write_config(
        config_path,
        workspace,
        group_root,
        routines=[{"id": "suite-health", "prompt_name": "daily-review",
                   "schedule": {"at": "08:00", "catch_up": "always"}}],
    )
    monkeypatch.setenv("AGENCY_FIXED_NOW", "2026-07-29T03:00:00")
    launcher = _RecordingLauncher()

    run_dispatch_cycle(None, config_path, launcher)

    assert len(launcher.launched) == 1
    assert at_marker_path(
        group_root / "logs", "product", "suite-health", "2026-07-28"
    ).exists()
    assert not at_marker_path(
        group_root / "logs", "product", "suite-health", "2026-07-29"
    ).exists()


def test_default_bound_forgets_yesterdays_occurrence(tmp_path, monkeypatch):
    workspace, group_root, config_path, _ = _make_group(tmp_path)
    _write_config(
        config_path,
        workspace,
        group_root,
        routines=[{"id": "suite-health", "prompt_name": "daily-review",
                   "schedule": {"at": "08:00"}}],
    )
    monkeypatch.setenv("AGENCY_FIXED_NOW", "2026-07-29T03:00:00")
    launcher = _RecordingLauncher()

    run_dispatch_cycle(None, config_path, launcher)

    assert launcher.launched == []


def test_an_already_marked_occurrence_does_not_run_again(tmp_path, monkeypatch):
    workspace, group_root, config_path, _ = _make_group(tmp_path)
    _write_config(
        config_path,
        workspace,
        group_root,
        routines=[{"id": "suite-health", "prompt_name": "daily-review",
                   "schedule": {"at": "08:00"}}],
    )
    marker = at_marker_path(
        group_root / "logs", "product", "suite-health", "2026-07-29"
    )
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.touch()
    monkeypatch.setenv("AGENCY_FIXED_NOW", "2026-07-29T11:57:00")
    launcher = _RecordingLauncher()

    run_dispatch_cycle(None, config_path, launcher)

    assert launcher.launched == []


def test_every_marker_anchors_on_the_occurrence_not_the_launch(
    tmp_path, monkeypatch
):
    workspace, group_root, config_path, _ = _make_group(tmp_path)
    _write_config(
        config_path,
        workspace,
        group_root,
        routines=[{"id": "audit", "prompt_name": "daily-review",
                   "schedule": {"every": "6h"}, "catch_up": "always"}],
    )
    marker = every_marker_path(group_root / "logs", "product", "audit")
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.touch()
    anchor = datetime(2026, 7, 29, 3, 0).timestamp()
    os.utime(marker, (anchor, anchor))
    monkeypatch.setenv("AGENCY_FIXED_NOW", "2026-07-29T11:57:00")

    run_dispatch_cycle(None, config_path, _RecordingLauncher())

    assert datetime.fromtimestamp(marker.stat().st_mtime) == datetime(
        2026, 7, 29, 9, 0
    )
```

`_write_config` must be extended to emit a `catch_up:` line under `schedule:` when the routine dict carries one.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_dispatch_run.py -v`
Expected: FAIL — the 11:57 case launches nothing today because the 17-minute window has closed.

- [ ] **Step 3: Replace the firing rule**

In `agency/dispatch/run.py`, delete `check_at_rule` and `check_every_rule`, and replace the per-routine body inside `run_dispatch_cycle` with:

```python
import os
from datetime import datetime

from agency.clock import now as clock_now
from agency.health import grace_window
from agency.dispatch.schedule import (
    at_marker_path,
    catch_up_allows,
    every_marker_path,
    last_at_occurrence,
    last_every_occurrence,
    parse_catch_up,
)


def _due_occurrence(routine, logs_root: Path, agent_name: str, now: datetime):
    """The occurrence this routine owes, with the marker that would record it."""
    at_time = routine.schedule.at or ""
    every_value = routine.schedule.every or ""
    if at_time:
        occurrence = last_at_occurrence(at_time, now)
        if occurrence is None:
            return None, None
        marker = at_marker_path(
            logs_root, agent_name, routine.id, occurrence.strftime("%Y-%m-%d")
        )
        return occurrence, marker
    if every_value:
        marker = every_marker_path(logs_root, agent_name, routine.id)
        try:
            anchor = datetime.fromtimestamp(marker.stat().st_mtime)
        except OSError:
            return now, marker
        return last_every_occurrence(anchor, every_value, now), marker
    return None, None
```

and, in the routine loop:

```python
                now = clock_now()
                bound = parse_catch_up(getattr(routine.schedule, "catch_up", None))
                if bound is None:
                    log.warning(
                        "  SKIP: %s/%s has an invalid catch_up", agent_name, routine.id
                    )
                    continue

                occurrence, marker = _due_occurrence(
                    routine, logs_root, agent_name, now
                )
                if occurrence is None or marker is None:
                    log.warning(
                        "  WARNING: rule for %s/%s has no usable schedule",
                        agent_name,
                        routine.id,
                    )
                    continue
                if routine.schedule.at and marker.exists():
                    continue
                if not catch_up_allows(occurrence, now, bound, grace_window(interval)):
                    continue
```

and replace the marker touch after a successful submission with:

```python
                    marker.parent.mkdir(parents=True, exist_ok=True)
                    marker.touch()
                    stamp = occurrence.timestamp()
                    os.utime(marker, (stamp, stamp))
```

An `every` routine with no marker keeps today's behavior of firing immediately: `_due_occurrence` returns `now` as its occurrence, which every bound admits.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_dispatch_run.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite and commit**

Run: `python -m pytest tests/ -q`

```bash
git add agency/dispatch/run.py tests/test_dispatch_run.py
git commit -m "feat(dispatch): recover the last missed occurrence"
```

---

### Task 6: The pool setting

**Files:**
- Modify: `agency/configuration/models.py:23-38`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `AgencyJobs(pool: int = 4)` and `AgencySettings.jobs: AgencyJobs`.

- [ ] **Step 1: Write the failing tests**

```python
def test_jobs_pool_defaults_to_four(tmp_path):
    parsed = load_config(_write_minimal_config(tmp_path))
    assert parsed.agency.jobs.pool == 4


def test_jobs_pool_is_read_from_config(tmp_path):
    parsed = load_config(_write_minimal_config(tmp_path, jobs_pool=2))
    assert parsed.agency.jobs.pool == 2


def test_jobs_pool_below_one_is_rejected(tmp_path):
    with pytest.raises(ValidationFailed):
        load_config(_write_minimal_config(tmp_path, jobs_pool=0))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_config.py -k jobs_pool -v`
Expected: FAIL with `AttributeError: 'AgencySettings' object has no attribute 'jobs'`.

- [ ] **Step 3: Implement**

```python
class AgencyJobs(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)
    pool: int = Field(default=4, ge=1)


class AgencySettings(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)
    title: str = "Agency"
    default_group: str = ""
    ai_backend: str = "claude-code"
    dispatch: AgencyDispatch = Field(default_factory=AgencyDispatch)
    jobs: AgencyJobs = Field(default_factory=AgencyJobs)
    agent_library: Path | None = None
    compilation_cache: Path | None = None
    memory_store: Path | None = None
    prompt_store: Path | None = None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_config.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agency/configuration/models.py tests/test_config.py
git commit -m "feat(config): add the global job pool size"
```

---

### Task 7: Due time on the job record

**Files:**
- Modify: `agency/jobs/models.py:116-129` (`JobRequest`), `agency/jobs/models.py:296-315` (`JobRecord`)
- Modify: `agency/jobs/submission.py:60-70`
- Test: `tests/test_jobs_models.py` (create if absent — check with `ls tests | grep job`)

**Interfaces:**
- Produces: `JobRequest.due_at: str | None = None` and `JobRecord.due_at: str | None = None`, both ISO-8601 strings. `JobRecord.from_spec(spec, due_at=None)` gains the keyword.

- [ ] **Step 1: Write the failing tests**

```python
def test_job_record_defaults_due_at_to_none(sample_spec):
    record = JobRecord.from_spec(sample_spec)
    assert record.due_at is None


def test_job_record_round_trips_due_at(sample_spec):
    record = JobRecord.from_spec(sample_spec, due_at="2026-07-29T08:00:00")
    restored = JobRecord.from_dict(record.to_dict())
    assert restored.due_at == "2026-07-29T08:00:00"


def test_a_record_written_before_due_at_still_loads(sample_spec):
    payload = JobRecord.from_spec(sample_spec).to_dict()
    payload.pop("due_at")
    assert JobRecord.from_dict(payload).due_at is None


def test_due_at_does_not_change_the_authority_digest(sample_spec):
    plain = JobRecord.from_spec(sample_spec)
    dated = JobRecord.from_spec(sample_spec, due_at="2026-07-29T08:00:00")
    assert plain.authority_digest == dated.authority_digest
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_jobs_models.py -v`
Expected: FAIL with `AttributeError: 'JobRecord' object has no attribute 'due_at'`.

- [ ] **Step 3: Implement**

In `JobRequest`, after `trigger_context`:

```python
    due_at: str | None = None
```

In `JobRecord`, after `session_id`:

```python
    due_at: str | None = None
```

and:

```python
    @classmethod
    def from_spec(cls, spec: JobSpec, *, due_at: str | None = None) -> "JobRecord":
        spec.validate()
        return cls(
            spec=spec,
            authority_digest=spec.immutable_digest(),
            due_at=due_at,
        )
```

`JobSpec` is untouched, so the digest is unchanged and records written before this field load with `due_at=None`.

In `agency/jobs/submission.py`, `_submit_resolved` gains a `due_at: str | None = None` keyword that it forwards to `JobRecord.from_spec`, and the `JobRecord(...)` rebuild in its exception handler carries `due_at=record.due_at`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/ -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agency/jobs/models.py agency/jobs/submission.py tests/test_jobs_models.py
git commit -m "feat(jobs): record the time a job was due"
```

---

### Task 8: Claiming and counting

**Files:**
- Modify: `agency/jobs/store.py`
- Test: `tests/test_job_store.py` (the file holding the existing `transition_job` tests — locate with `grep -rl "InvalidJobTransition" tests`)

**Interfaces:**
- Consumes: `worker_alive` from `agency/jobs/reconciliation.py`.
- Produces:
  - `queue_lock_path(store_root: Path) -> Path` — `<store_root>/.queue.lock`
  - `occupies_slot(record: JobRecord) -> bool`
  - `is_launchable(record: JobRecord) -> bool`
  - `claim_job(path: Path, worker_pid: int | None) -> JobRecord`

- [ ] **Step 1: Write the failing tests**

```python
from agency.jobs.store import claim_job, is_launchable, occupies_slot, queue_lock_path


def test_queue_lock_sits_beside_the_group_directories(tmp_path):
    assert queue_lock_path(tmp_path).name == ".queue.lock"
    assert queue_lock_path(tmp_path).parent == tmp_path


def test_running_and_waiting_records_occupy_a_slot(sample_spec):
    for status in ("running", "waiting_for_memory"):
        record = JobRecord.from_spec(sample_spec)
        record.status = status
        assert occupies_slot(record) is True


def test_an_unclaimed_queued_record_is_launchable(sample_spec):
    record = JobRecord.from_spec(sample_spec)
    assert occupies_slot(record) is False
    assert is_launchable(record) is True


def test_a_queued_record_with_a_dead_worker_is_launchable_again(
    sample_spec, monkeypatch
):
    monkeypatch.setattr("agency.jobs.store.worker_alive", lambda pid: False)
    record = JobRecord.from_spec(sample_spec)
    record.worker_pid = 999999
    assert occupies_slot(record) is False
    assert is_launchable(record) is True


def test_a_queued_record_with_an_unverifiable_worker_holds_its_slot(
    sample_spec, monkeypatch
):
    monkeypatch.setattr("agency.jobs.store.worker_alive", lambda pid: None)
    record = JobRecord.from_spec(sample_spec)
    record.worker_pid = 999999
    assert occupies_slot(record) is True
    assert is_launchable(record) is False


def test_claim_records_the_worker_without_changing_status(tmp_path, sample_spec):
    path = tmp_path / "job.yaml"
    write_job(path, JobRecord.from_spec(sample_spec))
    claimed = claim_job(path, 4321)
    assert claimed.status == "queued"
    assert claimed.worker_pid == 4321
    assert read_job(path).worker_pid == 4321


def test_claiming_a_job_that_left_the_queue_is_refused(tmp_path, sample_spec):
    path = tmp_path / "job.yaml"
    record = JobRecord.from_spec(sample_spec)
    record.status = "running"
    write_job(path, record)
    with pytest.raises(InvalidJobTransition):
        claim_job(path, 4321)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_job_store.py -k "claim or slot or launchable or queue_lock" -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement**

Add to `agency/jobs/store.py`:

```python
from agency.jobs.reconciliation import worker_alive


def queue_lock_path(store_root: Path) -> Path:
    return Path(store_root) / ".queue.lock"


def _worker_gone(record: JobRecord) -> bool:
    return record.worker_pid is not None and worker_alive(record.worker_pid) is False


def occupies_slot(record: JobRecord) -> bool:
    """Whether this record is holding one of the pool's slots."""
    if record.status in {"running", "waiting_for_memory"}:
        return True
    if record.status != "queued":
        return False
    return record.worker_pid is not None and not _worker_gone(record)


def is_launchable(record: JobRecord) -> bool:
    """Whether the drain may start this record now."""
    if record.status != "queued":
        return False
    return record.worker_pid is None or _worker_gone(record)


def claim_job(path: Path, worker_pid: int | None) -> JobRecord:
    """Record which worker owns a queued job, leaving its status alone."""
    with exclusive_lock(job_lock_path(path), wait=True):
        record = read_job(path)
        if record.status != "queued":
            raise InvalidJobTransition(
                f"Only queued jobs can be claimed, found {record.status!r}"
            )
        updated = replace(record, worker_pid=worker_pid)
        write_job(path, updated)
        return updated
```

`agency/jobs/reconciliation.py` imports from `store`, so import `worker_alive` lazily inside `_worker_gone` if the module-level import introduces a cycle. Verify with `python -c "import agency.jobs.store"`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/ -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agency/jobs/store.py tests/test_job_store.py
git commit -m "feat(jobs): claim and count pool slots"
```

---

### Task 9: The drain

**Files:**
- Create: `agency/jobs/queue.py`
- Create: `tests/test_job_queue.py`
- Modify: `agency/jobs/__init__.py`

**Interfaces:**
- Consumes: `JobStore`, `reconcile_jobs`, `read_job`, `claim_job`, `occupies_slot`, `is_launchable`, `queue_lock_path`, `default_launcher`, `get_timer_status`.
- Produces:
  - `QueueEntry = NamedTuple("QueueEntry", [("group_id", str), ("record", JobRecord), ("path", Path)])`
  - `queue_snapshot(config, *, memory_store: Path) -> QueueView` where `QueueView` is `NamedTuple` `(running: int, waiting: tuple[QueueEntry, ...], pool: int)`, `waiting` sorted by `(due_at or created_at, job_id)`
  - `has_drainer(config, *, memory_store: Path, config_path: Path) -> bool`
  - `drain(config, *, memory_store: Path, launcher=None) -> int` returning how many jobs it started

- [ ] **Step 1: Write the failing tests**

Create `tests/test_job_queue.py`:

```python
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


def test_a_ghost_running_record_does_not_hold_a_slot_forever(
    queue_fixture, monkeypatch
):
    monkeypatch.setattr("agency.jobs.store.worker_alive", lambda pid: False)
    queue_fixture.enqueue("ghost", status="running", worker_pid=999999)
    queue_fixture.enqueue("real", due_at="2026-07-29T08:00:00")
    drain(queue_fixture.config, memory_store=queue_fixture.memory_store,
          launcher=queue_fixture.launcher)
    assert "real" in queue_fixture.launcher.launched


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
```

Build `queue_fixture` in the same file: a `tmp_path` config with one group and a `pool`, a `JobStore` beneath it, an `enqueue(job_id, *, due_at=None, status="queued", worker_pid=None)` that writes a valid record, a `status(job_id)` reader, and a launcher recording `launched` with an optional `fail_on` that raises `OSError`. Reuse the spec-building helpers already used by `tests/test_job_store.py`. It also exposes `config`, `config_path`, `memory_store`, and `worker_argv(job_id)` returning the argv list `JobAuthorityRef.worker_args()` produces.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_job_queue.py -v`
Expected: FAIL — `agency.jobs.queue` does not exist.

- [ ] **Step 3: Implement**

Create `agency/jobs/queue.py`:

```python
"""The global job queue and its worker pool."""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path
from typing import NamedTuple

from agency.dispatch.install import get_timer_status
from agency.fs.locks import exclusive_lock

from .authority import JobStore
from .launcher import JobLauncher, default_launcher
from .models import JobRecord
from .reconciliation import reconcile_jobs
from .store import (
    claim_job,
    is_launchable,
    occupies_slot,
    queue_lock_path,
    read_job,
    write_job,
)

log = logging.getLogger("agency.jobs.queue")


class QueueEntry(NamedTuple):
    group_id: str
    record: JobRecord
    path: Path


class QueueView(NamedTuple):
    running: int
    waiting: tuple[QueueEntry, ...]
    pool: int


def _entries(config, memory_store: Path) -> list[QueueEntry]:
    store = JobStore(memory_store)
    entries: list[QueueEntry] = []
    for group_id in sorted(config.groups):
        group_dir = store.group_root(group_id)
        if not group_dir.is_dir():
            continue
        for path in sorted(group_dir.glob("*.yaml")):
            try:
                entries.append(QueueEntry(group_id, read_job(path), path))
            except Exception:
                continue
    return entries


def _order_key(entry: QueueEntry) -> tuple[str, str]:
    record = entry.record
    return (record.due_at or record.spec.created_at or "", record.spec.job_id)


def queue_snapshot(config, *, memory_store: Path) -> QueueView:
    """Current occupancy and the waiting jobs, oldest due first."""
    entries = _entries(config, memory_store)
    waiting = sorted(
        (entry for entry in entries if is_launchable(entry.record)),
        key=_order_key,
    )
    running = sum(1 for entry in entries if occupies_slot(entry.record))
    return QueueView(running, tuple(waiting), config.agency.jobs.pool)


def _group_roots(config) -> dict:
    return {
        group_id: {"group_root": str(group.path)}
        for group_id, group in config.groups.items()
    }


def has_drainer(config, *, memory_store: Path, config_path: Path) -> bool:
    """Whether anything will start a job that is left waiting."""
    if any(occupies_slot(entry.record) for entry in _entries(config, memory_store)):
        return True
    status = get_timer_status(config_path, config.agency.dispatch.interval)
    return bool(status.get("installed") and status.get("enabled"))


def drain(config, *, memory_store: Path, launcher: JobLauncher | None = None) -> int:
    """Start waiting jobs, oldest due first, while the pool has room."""
    selected = launcher or default_launcher()
    store = JobStore(memory_store)
    started = 0
    with exclusive_lock(queue_lock_path(store.root), wait=True):
        reconcile_jobs(_group_roots(config), memory_store_root=memory_store)
        view = queue_snapshot(config, memory_store=memory_store)
        capacity = view.pool - view.running
        for entry in view.waiting:
            if capacity <= 0:
                break
            reference = store.reference(
                entry.group_id,
                entry.record.spec.job_id,
                entry.record.authority_digest,
            )
            try:
                result = selected.launch(reference)
            except Exception as error:
                log.error("could not launch %s: %s", entry.record.spec.job_id, error)
                write_job(
                    entry.path,
                    replace(
                        entry.record,
                        status="failed",
                        execution_summary=f"Launch error: {error}",
                    ),
                )
                continue
            claim_job(entry.path, result.worker_pid)
            capacity -= 1
            started += 1
    return started
```

Export `drain`, `has_drainer`, and `queue_snapshot` from `agency/jobs/__init__.py` alongside `reconcile_jobs`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_job_queue.py -v && python -m pytest tests/ -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agency/jobs/queue.py agency/jobs/__init__.py tests/test_job_queue.py
git commit -m "feat(jobs): drain a global queue into a bounded worker pool"
```

---

### Task 10: Submission goes through the pool

**Files:**
- Modify: `agency/jobs/submission.py:60-136`
- Test: `tests/test_job_submission.py` (locate the existing submission tests with `grep -rl submit_job_request tests`)

**Interfaces:**
- Consumes: `drain`, `has_drainer`, `queue_snapshot` from Task 9.
- Produces: `submit_job_request` returning a `JobHandle` whose status is `"queued"` in both cases; the record is launched immediately only when the pool has room.

- [ ] **Step 1: Write the failing tests**

```python
def test_submission_launches_immediately_when_the_pool_has_room(submission_env):
    handle = submit_job_request(submission_env.request(), submission_env.launcher)
    assert submission_env.launcher.launched == [handle.job_id]


def test_submission_waits_when_the_pool_is_full(submission_env):
    submission_env.fill_pool()
    handle = submit_job_request(submission_env.request(), submission_env.launcher)
    assert submission_env.launcher.launched == []
    assert submission_env.status(handle.job_id) == "queued"


def test_a_waiting_job_records_its_due_time(submission_env):
    handle = submit_job_request(
        submission_env.request(due_at="2026-07-29T08:00:00"),
        submission_env.launcher,
    )
    assert submission_env.record(handle.job_id).due_at == "2026-07-29T08:00:00"


def test_submission_is_refused_when_nothing_can_drain(submission_env, monkeypatch):
    submission_env.fill_pool_with_dead_workers()
    monkeypatch.setattr(
        "agency.jobs.submission.has_drainer", lambda *a, **k: False
    )
    with pytest.raises(JobSubmissionError):
        submit_job_request(submission_env.request(), submission_env.launcher)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_job_submission.py -v`
Expected: FAIL — every submission launches unconditionally today.

`submission_env` is a fixture over the same `tmp_path` config the existing
submission tests build. It exposes `request(**overrides)` returning a valid
`JobRequest`, `launcher` recording `launched`, `record(job_id)` and
`status(job_id)` readers, `fill_pool()` writing `pool` records in status
`running` with live PIDs, and `fill_pool_with_dead_workers()` writing the same
records with PIDs that `worker_alive` reports as absent.

- [ ] **Step 3: Implement**

In `_submit_resolved`, after `job_store.create(record)` replace the unconditional launch with:

```python
    try:
        authority = job_store.create(record)
        view = queue_snapshot(config, memory_store=job_store.memory_store)
        if view.running < view.pool:
            result = selected_launcher.launch(authority)
            claim_job(authority.path, result.worker_pid)
            worker_pid = result.worker_pid
        else:
            if not has_drainer(
                config,
                memory_store=job_store.memory_store,
                config_path=Path(spec.config_path),
            ):
                raise JobSubmissionError(
                    "no running worker and no installed dispatcher can start "
                    "this job; install the dispatch timer or wait for a worker",
                    authority.path,
                )
            worker_pid = None
    except Exception as error:
        ...  # existing failure path, unchanged apart from due_at
```

`_submit_resolved` gains `config` and `due_at` keywords; `submit_job_request` passes `locked_snapshot.config` and `request.due_at`. Return `JobHandle(spec.job_id, "queued", authority.path, worker_pid)`.

At the very end of `submit_job_request`, outside the locked block and inside a `try/except Exception` that only logs, call:

```python
    drain(snapshot_config, memory_store=memory_store, launcher=launcher)
```

The drain runs after the group lock is released so it cannot deadlock against the submission that created the job.

In `agency/dispatch/run.py`, set `due_at=occurrence.isoformat()` on the `JobRequest` it builds.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/ -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agency/jobs/submission.py agency/dispatch/run.py tests/test_job_submission.py
git commit -m "feat(jobs): submit through the pool instead of launching directly"
```

---

### Task 11: Workers and cycles hand the baton on

**Files:**
- Modify: `agency/jobs/worker.py`
- Modify: `agency/dispatch/run.py:48-60`
- Modify: `agency/app.py:364-378`
- Test: `tests/test_job_queue.py`, `tests/test_dispatch_run.py`

**Interfaces:**
- Consumes: `drain` from Task 9.
- Produces: no new symbols; three call sites.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_finishing_worker_starts_the_next_waiting_job(queue_fixture, monkeypatch):
    calls = []
    monkeypatch.setattr("agency.jobs.worker.drain", lambda *a, **k: calls.append(1))
    monkeypatch.setattr("agency.jobs.worker.execute_job", lambda ref: SimpleNamespace(status="complete"))
    worker_main(queue_fixture.worker_argv("only"))
    assert calls == [1]


def test_a_dispatch_cycle_drains_before_it_evaluates_routines(tmp_path, monkeypatch):
    order = []
    monkeypatch.setattr("agency.dispatch.run.drain", lambda *a, **k: order.append("drain"))
    workspace, group_root, config_path, _ = _make_group(tmp_path)
    _write_config(config_path, workspace, group_root, routines=[])
    run_dispatch_cycle(None, config_path, _RecordingLauncher())
    assert order == ["drain"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_job_queue.py tests/test_dispatch_run.py -k drain -v`
Expected: FAIL — neither module imports `drain`.

- [ ] **Step 3: Implement**

`agency/jobs/worker.py`, after `execute_job` returns and before the return value is computed:

```python
    result = execute_job(reference)
    try:
        config = ConfigStore(Path(read_job(reference.path).spec.config_path)).load().config
        drain(config, memory_store=store.memory_store)
    except Exception:
        logging.getLogger("agency.jobs.worker").exception("drain after job failed")
    return 0 if result.status == "complete" else 1
```

The drain must never turn a completed job into a failed exit code, hence the bare `except Exception`.

`agency/dispatch/run.py`, immediately after the snapshot is resolved and before the group loop:

```python
    try:
        drain(resolved, memory_store=resolved.agency.memory_store)
    except Exception:
        log.exception("queue drain failed")
```

`agency/app.py` lifespan: replace the direct `reconcile_jobs(...)` call with `drain(...)`, which reconciles first. Keep the existing `try/except` around it.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/ -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agency/jobs/worker.py agency/dispatch/run.py agency/app.py tests/
git commit -m "feat(jobs): drain the queue from workers, cycles, and startup"
```

---

### Task 12: Queue position on the Jobs list

**Files:**
- Modify: `agency/web/routes/jobs.py:185-212` (`_job_rows`)
- Modify: `agency/templates/jobs.html`
- Test: `tests/test_jobs_routes.py` (locate with `grep -rl "/jobs" tests`)

**Interfaces:**
- Consumes: `queue_snapshot` from Task 9.
- Produces: each row dict gains `queue_position: int | None` and `due_at: str | None`.

- [ ] **Step 1: Write the failing tests**

```python
def test_waiting_jobs_show_their_position(client, queued_jobs):
    response = client.get("/grp/jobs")
    assert "1 of 3" in response.text


def test_a_running_job_has_no_position(client, running_job):
    response = client.get("/grp/jobs")
    assert "of 3" not in response.text


def test_a_queued_job_offers_cancel(client, queued_jobs):
    response = client.get("/grp/jobs")
    assert "/jobs/" in response.text and "cancel" in response.text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_jobs_routes.py -k position -v`
Expected: FAIL — no position is rendered.

- [ ] **Step 3: Implement**

In `_job_rows`, build the position map once:

```python
    view = queue_snapshot(snapshot.config, memory_store=job_store.memory_store)
    positions = {
        entry.record.spec.job_id: index + 1
        for index, entry in enumerate(view.waiting)
    }
```

and add to each row:

```python
        "queue_position": positions.get(record.spec.job_id),
        "queue_length": len(view.waiting),
        "due_at": record.due_at,
```

In `agency/templates/jobs.html`, beside the status badge:

```jinja
{% if row.queue_position %}
<span class="font-mono text-xs text-slate-500 dark:text-slate-400">{{ row.queue_position }} of {{ row.queue_length }}</span>
{% endif %}
```

Cancel already exists for queued jobs and needs no change.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_jobs_routes.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agency/web/routes/jobs.py agency/templates/jobs.html tests/test_jobs_routes.py
git commit -m "feat(jobs): show queue position on the jobs list"
```

---

### Task 13: The Inbox work queue strip

**Files:**
- Modify: `agency/app.py:1824-1868` (`home`)
- Modify: `agency/templates/home.html:114-116` (between Zone 2 and Zone 3)
- Test: `tests/test_dashboard.py`

**Interfaces:**
- Consumes: `queue_snapshot` from Task 9.
- Produces: template context key `work_queue` — a dict `{"running": int, "pool": int, "waiting": [{"position": int, "agent": str, "routine": str, "due": str, "href": str}]}`.

**Layout is normative.** Compare the rendered page against `docs/superpowers/specs/assets/2026-07-30-scheduled-work-recovery/inbox-queue-strip.png` and `queue-strip-states.png` before marking this task complete.

- [ ] **Step 1: Write the failing tests**

```python
def test_the_strip_sits_between_pipeline_and_the_attention_queue(client, waiting_jobs):
    body = client.get("/grp/").text
    assert body.index("Pipeline") < body.index("Work queue") < body.index("Attention Queue")


def test_the_strip_lists_waiting_jobs_in_due_order(client, waiting_jobs):
    body = client.get("/grp/").text
    assert body.index("suite-health") < body.index("docs-audit")


def test_the_strip_header_counts_running_and_waiting(client, waiting_jobs):
    body = client.get("/grp/").text
    assert "2 running" in body and "3 queued" in body


def test_an_empty_queue_keeps_one_idle_line(client):
    body = client.get("/grp/").text
    assert "idle" in body and "pool 4" in body
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_dashboard.py -k queue -v`
Expected: FAIL — `Work queue` does not appear.

- [ ] **Step 3: Implement**

In `home`, before the `TemplateResponse`. `_load_snapshot()` is the accessor the
route's neighbours already use — see `build_dashboard_fleet` — and it carries
`config.agency.memory_store`:

```python
    snapshot = _load_snapshot()
    view = queue_snapshot(
        snapshot.config, memory_store=snapshot.config.agency.memory_store
    )
    work_queue = {
        "running": view.running,
        "pool": view.pool,
        "waiting": [
            {
                "position": index + 1,
                "agent": entry.record.spec.agent_name,
                "routine": entry.record.spec.routine_id or "task",
                "due": entry.record.due_at or entry.record.spec.created_at,
                "href": f"/{entry.group_id}/jobs/{entry.record.spec.job_id}",
            }
            for index, entry in enumerate(view.waiting)
        ],
    }
```

and pass `"work_queue": work_queue`.

In `home.html`, directly after the Pipeline block and before the Zone 3 / Zone 4 row:

```jinja
<div class="rounded-lg border px-4 py-3 mb-3
     {% if work_queue.waiting or work_queue.running %}border-cyan-700 bg-cyan-950/20{% else %}bg-gray-50 dark:bg-gray-800/50 border-gray-200 dark:border-gray-700{% endif %}">
  <div class="flex items-center justify-between gap-2">
    <span class="text-sm font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wider">Work queue</span>
    {% if work_queue.waiting or work_queue.running %}
    <span class="flex gap-1.5">
      <span class="rounded bg-emerald-900/60 px-1.5 font-mono text-xs text-emerald-200">{{ work_queue.running }} running</span>
      <span class="rounded bg-cyan-900/60 px-1.5 font-mono text-xs text-cyan-100">{{ work_queue.waiting|length }} queued</span>
    </span>
    {% else %}
    <span class="font-mono text-xs text-gray-500 dark:text-gray-400">idle &middot; pool {{ work_queue.pool }}</span>
    {% endif %}
  </div>
  {% if work_queue.waiting %}
  <div class="mt-1.5 max-h-24 overflow-y-auto font-mono text-xs leading-6">
    {% for item in work_queue.waiting %}
    <a href="{{ item.href }}" class="block hover:underline text-gray-700 dark:text-gray-200">{{ item.position }} {{ item.agent }} / {{ item.routine }} <span class="text-gray-500 dark:text-gray-400">{{ item.due }}</span></a>
    {% endfor %}
  </div>
  {% endif %}
</div>
```

`max-h-24` is the four-row cap from the approved mockup; the list scrolls beyond it while the header counts stay whole.

- [ ] **Step 4: Run the tests and compare against the mockup**

Run: `python -m pytest tests/test_dashboard.py -v`
Expected: PASS. Then start the dashboard, open a group, and compare the strip against both reference PNGs — position, header counts, cap and scroll, and the idle line.

- [ ] **Step 5: Commit**

```bash
git add agency/app.py agency/templates/home.html tests/test_dashboard.py
git commit -m "feat(dashboard): show the work queue on the inbox"
```

---

### Task 14: Queued is not running on the fleet cards

**Files:**
- Modify: `agency/app.py:1170-1250` (`build_dashboard_fleet`, `_overlay_dashboard_job_state`)
- Modify: `agency/templates/home.html:44-52`
- Test: `tests/test_dashboard.py`

**Interfaces:**
- Produces: each fleet agent dict gains `queued: bool`; `running` stays true only for a job in `running` or `waiting_for_memory`.

- [ ] **Step 1: Write the failing tests**

```python
def test_an_agent_with_a_waiting_job_is_not_shown_as_running(client, waiting_jobs):
    body = client.get("/grp/").text
    assert "data-agent-running" not in body
    assert "queued" in body


def test_an_agent_with_a_started_job_is_still_shown_as_running(client, running_job):
    body = client.get("/grp/").text
    assert "data-agent-running" in body
```

`waiting_jobs` and `running_job` are fixtures writing one job record for agent
`product` in status `queued` with no `worker_pid`, and in status `running` with
a live PID, into the group's job store before the request.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_dashboard.py -k queued -v`
Expected: FAIL — a queued job currently renders the pulsing running dot.

- [ ] **Step 3: Implement**

In `_overlay_dashboard_job_state`, split the two states:

```python
    agent["running"] = current is not None and current.status in {
        "running",
        "waiting_for_memory",
    }
    agent["queued"] = current is not None and current.status == "queued"
```

In `home.html`, before the existing `{% if a.running %}` branch:

```jinja
        {% if a.queued %}
        <span class="ml-auto rounded bg-cyan-900/60 px-1.5 font-mono text-xs text-cyan-100" title="{{ a.health_sentence }}">queued</span>
        {% elif a.running %}
```

and close the chain with the existing `{% else %}` health dot.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_dashboard.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agency/app.py agency/templates/home.html tests/test_dashboard.py
git commit -m "feat(dashboard): distinguish queued agents from running ones"
```

---

### Task 15: Documentation

**Files:**
- Modify: `kb/dispatch.md`, `kb/configuration.md`, `config.yaml.example`, `AGENTS.md`
- Test: `tests/test_agency_setup_skill.py` (it asserts on documented configuration shape — run it)

- [ ] **Step 1: Update `kb/dispatch.md`**

Add a Recovery section stating the predicate, the `catch_up` values, the default of `today`, that the marker records the recovered occurrence's own day, and that an `every` marker anchors to the occurrence. Add a Queue section covering `agency.jobs.pool`, due-time FIFO, who drains, and the refusal when nothing can drain.

- [ ] **Step 2: Update `kb/configuration.md` and `config.yaml.example`**

Add `agency.jobs.pool` and `schedule.catch_up` with the values from the Global Constraints. Confirm no `daily_limit` remains: `grep -rn daily_limit kb config.yaml.example examples agency tests` must return nothing.

- [ ] **Step 3: Update `AGENTS.md`**

In the configuration sample, add `jobs: {pool: 4}` under `agency` and remove `daily_limit` from the group `dispatch` block.

- [ ] **Step 4: Run the suite**

Run: `python -m pytest tests/ -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kb config.yaml.example AGENTS.md
git commit -m "docs(dispatch): document recovery and the job pool"
```

---

## Notes for the implementer

- **The session ledger needs no task.** The spec's session-start ledger is the job record, which already carries `job_id`, `started_at`, and a `session_id` backfilled at completion. Task 7 adds the only missing field.
- **Health is deliberately unchanged.** `agency/health.py` still reports lateness only for an occurrence that is already past on the current day. With `catch_up: always` the runner can recover yesterday's occurrence at 03:00, which the dashboard never showed as overdue. That is accepted, not a bug to fix in this branch.
- **Do not touch `JobSpec`.** Every field on it feeds `immutable_digest()`, and `JobRecord.from_dict` raises on a digest mismatch, so a new spec field would make every previously written job record unreadable.
- **`worker_alive` has three answers.** `True`, `False`, and `None` for "cannot tell". Only `False` frees a slot.
- **Run the suite from the worktree root.** Running from another checkout resolves the wrong local `tests` package.
