# Compact Scheduled Next-Run Labels Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render every future schedule as `due in <compact duration>` and give the dashboard's ordinary next-run link the same neutral color as its last-run link.

**Architecture:** Keep `flowgency.health.relative_future(datetime | None) -> str` as the sole schedule formatter used by the dashboard and the agent Routines route. Change only that formatter's future text branches and the ordinary future-link classes in the dashboard template; schedule computation and warning-state rendering remain untouched.

**Tech Stack:** Python 3.13, FastAPI, Jinja2, pytest, Tailwind utility classes

## Global Constraints

- Future output is exactly `due in 1m`, `due in Nm`, `due in Nh`, or `due in Nd`.
- A datetime at or before the current time remains `due now`.
- Durations use one coarse unit; hours and days discard smaller units.
- Every surface using `relative_future` receives the new wording.
- Only the dashboard's ordinary future schedule link changes to `text-gray-600 dark:text-gray-300`.
- `due now`, `overdue`, job-status, and `no schedule` colors remain unchanged.
- Do not change schedule computation, dispatch timing, or add client-side countdowns.
- No approved visual assets are associated with this specification.

---

### Task 1: Shared Compact Future-Schedule Text

**Files:**
- Modify: `tests/test_agent_status.py:312-340`
- Modify: `tests/test_agent_detail.py:887-917`
- Modify: `flowgency/health.py:303-321`

**Interfaces:**
- Consumes: `flowgency.clock.now() -> datetime` and an optional `datetime` supplied to `relative_future`.
- Produces: `relative_future(dt: datetime | None) -> str`, returning a compact shared schedule label.

- [ ] **Step 1: Replace future-format unit tests with the approved duration table**

Keep `test_relative_future_none` and replace the current due-now and future-specific tests in `tests/test_agent_status.py` with:

```python
def test_relative_future_due_now(monkeypatch):
    fixed_now = datetime(2026, 9, 6, 12, 0)
    monkeypatch.setenv("FLOWGENCY_FIXED_NOW", fixed_now.isoformat())

    assert relative_future(fixed_now - timedelta(minutes=1)) == "due now"


@pytest.mark.parametrize(
    ("delta", "expected"),
    [
        (timedelta(seconds=30), "due in 1m"),
        (timedelta(minutes=7), "due in 7m"),
        (timedelta(hours=2, minutes=1), "due in 2h"),
        (timedelta(days=7, hours=4), "due in 7d"),
    ],
)
def test_relative_future_uses_compact_due_in_durations(
    monkeypatch,
    delta,
    expected,
):
    fixed_now = datetime(2026, 9, 6, 12, 0)
    monkeypatch.setenv("FLOWGENCY_FIXED_NOW", fixed_now.isoformat())

    assert relative_future(fixed_now + delta) == expected
```

- [ ] **Step 2: Make the Routines integration test require shared wording**

In `test_routines_get_fired_routine_shows_timestamp_and_next_occurrence`, use a fixed clock and an exact shared-label assertion:

```python
def test_routines_get_fired_routine_shows_timestamp_and_next_occurrence(
    monkeypatch,
    tmp_path,
    raw_config,
):
    """A fired routine shows its timestamp and compact relative next due time."""
    import os
    from datetime import datetime
    from flowgency.dispatch.schedule import every_marker_path

    fixed_now = datetime(2026, 9, 6, 10, 0)
    monkeypatch.setenv("FLOWGENCY_FIXED_NOW", fixed_now.isoformat())
    client, config_path = _seed_app(monkeypatch, tmp_path, raw_config)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    raw["teams"]["newsletter"]["dispatch"] = {"enabled": True}
    raw["teams"]["newsletter"]["agents"][0]["routines"].append(
        {
            "id": "hourly-check",
            "prompt": {"scope": "blueprint", "name": "pr-review"},
            "schedule": {"every": "6h"},
        }
    )
    config_path.write_text(
        yaml.safe_dump(raw, sort_keys=False),
        encoding="utf-8",
    )
    app_mod.refresh_services()

    logs_root = tmp_path / "groups" / "newsletter" / "logs"
    marker = every_marker_path(logs_root, "advisor", "hourly-check")
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.touch()
    stamp = fixed_now.timestamp()
    os.utime(marker, (stamp, stamp))

    response = client.get("/newsletter/agents/advisor/routines")

    assert response.status_code == 200
    assert "hourly-check" in response.text
    assert "due in 6h" in response.text
    assert "away" not in response.text
    assert fixed_now.strftime("%Y-%m-%d") in response.text
```

- [ ] **Step 3: Run the focused tests and verify RED**

Run:

```powershell
python -m pytest tests/test_agent_status.py tests/test_agent_detail.py -k "relative_future or fired_routine_shows_timestamp" -q
```

Expected: the future cases fail because production still returns `away`, `tomorrow HH:MM`, or an absolute timestamp; `None` and `due now` continue to pass.

- [ ] **Step 4: Implement compact output in the shared formatter**

Replace `relative_future` in `flowgency/health.py` with:

```python
def relative_future(dt: datetime | None) -> str:
    """Format an upcoming datetime as a compact due-in duration."""
    if dt is None:
        return ""
    now = clock_now()
    seconds = int((dt - now).total_seconds())
    if seconds <= 0:
        return "due now"
    minutes = max(1, round(seconds / 60))
    if minutes < 60:
        return f"due in {minutes}m"
    hours = minutes // 60
    if hours < 24:
        return f"due in {hours}h"
    return f"due in {hours // 24}d"
```

- [ ] **Step 5: Run the focused tests and verify GREEN**

Run:

```powershell
python -m pytest tests/test_agent_status.py tests/test_agent_detail.py -k "relative_future or fired_routine_shows_timestamp" -q
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit the shared behavior**

```powershell
git add flowgency/health.py tests/test_agent_status.py tests/test_agent_detail.py
git commit -m "fix(schedules): format compact next-run labels"
```

---

### Task 2: Neutral Dashboard Future-Link Color

**Files:**
- Modify: `tests/test_dashboard.py:565-574`
- Modify: `flowgency/templates/home.html:79-81`

**Interfaces:**
- Consumes: the `a.next_run` datetime and shared `relative_future` Jinja filter.
- Produces: a routines link whose text is `due in <duration>` and whose ordinary-state classes are `text-gray-600 dark:text-gray-300 hover:underline`.

- [ ] **Step 1: Add a rendered dashboard regression test**

Add this test after `test_fleet_cards_render_both_timing_values` in `tests/test_dashboard.py`:

```python
def test_future_schedule_link_matches_last_run_color(
    monkeypatch,
    tmp_path,
    raw_config,
):
    client, config_path, _ = _seed_dashboard_app(
        monkeypatch,
        tmp_path,
        raw_config,
    )
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    raw["teams"]["newsletter"]["dispatch"] = {"enabled": True}
    _write_yaml(config_path, raw)
    app_mod.refresh_services()
    app_mod.app.state.services = app_mod.build_services(config_path)
    monkeypatch.setenv("FLOWGENCY_FIXED_NOW", "2026-07-16T08:53:00")

    response = client.get("/newsletter/")

    assert response.status_code == 200
    assert (
        '<a href="/newsletter/agents/advisor/routines" '
        'class="text-gray-600 dark:text-gray-300 hover:underline">'
        "due in 7m</a>"
    ) in response.text
```

- [ ] **Step 2: Run the dashboard test and verify RED**

Run:

```powershell
python -m pytest tests/test_dashboard.py::test_future_schedule_link_matches_last_run_color -q
```

Expected: FAIL because the rendered future link still has `text-sky-700 dark:text-sky-300`.

- [ ] **Step 3: Match the future-link classes to the last-run link**

Change only the ordinary `a.next_run` branch in `flowgency/templates/home.html`:

```html
{% elif a.next_run %}
<a href="/{{ team }}/agents/{{ a.name }}/routines" class="text-gray-600 dark:text-gray-300 hover:underline">{{ a.next_run | relative_future }}</a>
{% else %}
```

Do not alter the job-status, overdue, due-now, or no-schedule branches.

- [ ] **Step 4: Run the dashboard test and verify GREEN**

Run:

```powershell
python -m pytest tests/test_dashboard.py::test_future_schedule_link_matches_last_run_color -q
```

Expected: PASS.

- [ ] **Step 5: Run adjacent dashboard warning-state coverage**

Run:

```powershell
python -m pytest tests/test_dashboard.py -k "future_schedule_link or overdue_agent_renders" -q
```

Expected: the neutral future link and unchanged overdue state both pass.

- [ ] **Step 6: Commit the dashboard presentation**

```powershell
git add flowgency/templates/home.html tests/test_dashboard.py
git commit -m "fix(ui): match next-run and last-run colors"
```

---

## Final Verification

- [ ] Run all directly affected modules:

```powershell
python -m pytest tests/test_agent_status.py tests/test_agent_detail.py tests/test_dashboard.py -q
```

- [ ] Run the complete repository suite:

```powershell
python -m pytest tests/ -q
```

- [ ] Verify the branch patch is clean and scoped:

```powershell
git diff --check master...HEAD
git status --short --branch
```

- [ ] After integration, inspect the live dashboard at `http://127.0.0.1:8500/flowgency/` and confirm the next-run link uses compact text and visually matches the last-run link in both light and dark themes.