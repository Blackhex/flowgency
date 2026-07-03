# Agent Running & Next-Run Status Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show whether each agent is currently running and, when idle, its soonest next scheduled dispatch — on the agents list, the agent profile, and the home fleet bar.

**Architecture:** The dispatcher writes a short-lived `.running-<agent>` marker file around each run. The web app reads that marker to detect "running" and computes the next scheduled run on the fly from the existing dispatch rules and `.last-*` markers. Presentation reuses the existing health-dot + last-seen widget, adding a running state and a next-run segment.

**Tech Stack:** Python 3.11+, FastAPI, Jinja2, pytest with `unittest.mock`, Tailwind CSS (CDN).

## Global Constraints

- Filesystem-only — no database, no background state, no process-table scans.
- Config writes are not involved here; only reads of the group's dispatch config via `GROUPS[g["key"]]`.
- Times use local time (`datetime.now()`), consistent with the dispatcher.
- `every` interval format is `(\d+)(m|h)` only (matches `check_every_rule`).
- Marker file locations: running marker at `shared/logs/.running-<agent>`; recurrence marker at `shared/logs/.last-<agent>-<stem>` (already written by the dispatcher).
- Follow existing code style: pure helper functions taking a group dict `g`; Jinja filters registered via `templates.env.filters[...]`.

---

## File Structure

- `agency/dispatch/run.py` — add running-marker write/remove around the run in `_run_agent()`.
- `agency/app.py` — add `is_agent_running()`, `compute_next_run()`, `relative_future` filter; enrich `collect_agents_with_identity()`; add `running`/`next_run` to the `agent_profile` route; add `fleet_running` count to the `home` route.
- `agency/templates/agents.html` — combined status widget on main cards and subagent cards.
- `agency/templates/home.html` — pulsing dot for running agents + `· N running` in the footer.
- `agency/templates/agent_profile.html` — running badge / next-run line near the schedule pills.
- `tests/test_dispatch_run.py` — dispatcher marker lifecycle tests.
- `tests/test_agent_status.py` (new) — `is_agent_running` and `compute_next_run` tests.

---

### Task 1: Running marker in the dispatcher

**Files:**
- Modify: `agency/dispatch/run.py` (function `_run_agent`, around the `result = integration.run(...)` call)
- Test: `tests/test_dispatch_run.py`

**Interfaces:**
- Consumes: existing `_run_agent(group_path, agent_name, prompt_filename, timeout, log_dir, agent_config, agent_dir=None)`.
- Produces: side effect — creates `log_dir.parent / f".running-{agent_name}"` before the run and removes it in a `finally`. (`log_dir` is `shared/logs/<today>`, so its parent is `shared/logs`.)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_dispatch_run.py`:

```python
from unittest.mock import patch, MagicMock
from agency.dispatch.run import _run_agent


def _make_group(tmp_path):
    """Create a group with one agent dir and one prompt; return (group_path, log_dir)."""
    group_path = tmp_path / "grp"
    agent_dir = group_path / "product"
    agent_dir.mkdir(parents=True)
    (agent_dir / "CLAUDE.md").write_text("# Product\n")
    prompts = group_path / "shared" / "prompts"
    prompts.mkdir(parents=True)
    (prompts / "routine.md").write_text("do the thing")
    log_dir = group_path / "shared" / "logs" / "2026-07-03"
    log_dir.mkdir(parents=True)
    return group_path, agent_dir, log_dir


def test_run_agent_removes_running_marker_on_success(tmp_path):
    group_path, agent_dir, log_dir = _make_group(tmp_path)
    running_marker = log_dir.parent / ".running-product"

    fake_result = MagicMock(stdout="ok", stderr="", exit_code=0, duration_seconds=1.0)
    fake_integration = MagicMock(supports_execution=True)
    fake_integration.run.return_value = fake_result

    with patch("agency.dispatch.run.get_integration", return_value=fake_integration):
        _run_agent(group_path, "product", "routine.md", 1800, log_dir,
                   {"integration": "claude-code"}, agent_dir=agent_dir)

    assert not running_marker.exists()


def test_run_agent_marker_present_during_run(tmp_path):
    group_path, agent_dir, log_dir = _make_group(tmp_path)
    running_marker = log_dir.parent / ".running-product"
    seen = {}

    fake_integration = MagicMock(supports_execution=True)

    def _run(agent_dir_arg, prompt_path, timeout):
        seen["exists"] = running_marker.exists()
        return MagicMock(stdout="ok", stderr="", exit_code=0, duration_seconds=1.0)

    fake_integration.run.side_effect = _run

    with patch("agency.dispatch.run.get_integration", return_value=fake_integration):
        _run_agent(group_path, "product", "routine.md", 1800, log_dir,
                   {"integration": "claude-code"}, agent_dir=agent_dir)

    assert seen["exists"] is True


def test_run_agent_removes_marker_on_exception(tmp_path):
    group_path, agent_dir, log_dir = _make_group(tmp_path)
    running_marker = log_dir.parent / ".running-product"

    fake_integration = MagicMock(supports_execution=True)
    fake_integration.run.side_effect = RuntimeError("boom")

    with patch("agency.dispatch.run.get_integration", return_value=fake_integration):
        with pytest.raises(RuntimeError):
            _run_agent(group_path, "product", "routine.md", 1800, log_dir,
                       {"integration": "claude-code"}, agent_dir=agent_dir)

    assert not running_marker.exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_dispatch_run.py -k running_marker -v`
Expected: FAIL — marker is never created (`test_run_agent_marker_present_during_run` asserts `True` but gets `False`); the exception test fails because `_run_agent` currently swallows nothing and no marker logic exists.

- [ ] **Step 3: Write minimal implementation**

In `agency/dispatch/run.py`, locate this block in `_run_agent`:

```python
    log.info("  RUNNING: %s with %s (timeout %ds, integration %s)",
             agent_name, prompt_filename, timeout, integration_name)

    result = integration.run(agent_dir, prompt_path, timeout)
    out_file.write_text(result.stdout)
    err_file.write_text(result.stderr)

    if result.exit_code == 124:
        log.warning("  TIMEOUT: %s exceeded %ds", agent_name, timeout)
    elif result.exit_code != 0:
        log.error("  ERROR: %s exited with code %d", agent_name, result.exit_code)
    else:
        log.info("  DONE: %s (%.1fs)", agent_name, result.duration_seconds)
```

Replace it with:

```python
    log.info("  RUNNING: %s with %s (timeout %ds, integration %s)",
             agent_name, prompt_filename, timeout, integration_name)

    running_marker = log_dir.parent / f".running-{agent_name}"
    running_marker.touch()
    try:
        result = integration.run(agent_dir, prompt_path, timeout)
    finally:
        running_marker.unlink(missing_ok=True)

    out_file.write_text(result.stdout)
    err_file.write_text(result.stderr)

    if result.exit_code == 124:
        log.warning("  TIMEOUT: %s exceeded %ds", agent_name, timeout)
    elif result.exit_code != 0:
        log.error("  ERROR: %s exited with code %d", agent_name, result.exit_code)
    else:
        log.info("  DONE: %s (%.1fs)", agent_name, result.duration_seconds)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_dispatch_run.py -k running_marker -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add agency/dispatch/run.py tests/test_dispatch_run.py
git commit -m "feat(dispatch): write .running marker around agent runs"
```

---

### Task 2: `is_agent_running` helper

**Files:**
- Modify: `agency/app.py` (add helper near `get_agent_last_seen`, before `collect_agents_with_identity`)
- Test: `tests/test_agent_status.py` (create)

**Interfaces:**
- Consumes: a group dict `g` with `g["shared"]` = `Path(.../shared)`.
- Produces: `is_agent_running(g: dict, agent_name: str, timeout: int = 1800) -> bool`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_agent_status.py`:

```python
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from agency.app import is_agent_running


def _group(tmp_path):
    shared = tmp_path / "shared"
    (shared / "logs").mkdir(parents=True)
    return {"key": "grp", "path": tmp_path, "shared": shared}


def test_running_marker_fresh(tmp_path):
    g = _group(tmp_path)
    (g["shared"] / "logs" / ".running-product").touch()
    assert is_agent_running(g, "product", timeout=1800) is True


def test_running_marker_stale(tmp_path):
    g = _group(tmp_path)
    marker = g["shared"] / "logs" / ".running-product"
    marker.touch()
    old = time.time() - 3600  # 1h ago, older than 1800s timeout
    os.utime(marker, (old, old))
    assert is_agent_running(g, "product", timeout=1800) is False


def test_running_marker_absent(tmp_path):
    g = _group(tmp_path)
    assert is_agent_running(g, "product", timeout=1800) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_agent_status.py -v`
Expected: FAIL with `ImportError: cannot import name 'is_agent_running'`.

- [ ] **Step 3: Write minimal implementation**

In `agency/app.py`, immediately after the `get_agent_last_seen` function (ends before `def relative_time`), add:

```python
def is_agent_running(g: dict, agent_name: str, timeout: int = 1800) -> bool:
    """True if a fresh .running-<agent> marker exists in shared/logs.

    A marker older than `timeout` seconds is treated as stale (orphaned by a
    hard-killed process) and reported as not running, so the UI self-heals.
    """
    marker = g["shared"] / "logs" / f".running-{agent_name}"
    if not marker.exists():
        return False
    age = time.time() - marker.stat().st_mtime
    return age < timeout
```

Ensure `import time` is present at the top of `agency/app.py`. If it is not, add it with the other stdlib imports.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_agent_status.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add agency/app.py tests/test_agent_status.py
git commit -m "feat(agents): add is_agent_running helper"
```

---

### Task 3: `compute_next_run` helper

**Files:**
- Modify: `agency/app.py` (add helper directly after `is_agent_running`)
- Test: `tests/test_agent_status.py`

**Interfaces:**
- Consumes: a group dict `g` with `g["shared"]`; a `dispatch_cfg` dict shaped like `{"enabled": bool, "agents": {name: [rules]}}` where each rule is `{"prompt": str, "at": "HH:MM"} | {"prompt": str, "every": "6h"} | {..., "condition": str}`; `interval` int (unused for math but kept for signature symmetry with the dispatcher — omit if not needed).
- Produces: `compute_next_run(g: dict, agent_name: str, dispatch_cfg: dict) -> datetime | None`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_agent_status.py`:

```python
from agency.app import compute_next_run


def _group_with_logs(tmp_path):
    shared = tmp_path / "shared"
    (shared / "logs").mkdir(parents=True)
    return {"key": "grp", "path": tmp_path, "shared": shared}


def test_next_run_disabled(tmp_path):
    g = _group_with_logs(tmp_path)
    cfg = {"enabled": False, "agents": {"product": [{"prompt": "r.md", "every": "6h"}]}}
    assert compute_next_run(g, "product", cfg) is None


def test_next_run_no_rules(tmp_path):
    g = _group_with_logs(tmp_path)
    cfg = {"enabled": True, "agents": {}}
    assert compute_next_run(g, "product", cfg) is None


def test_next_run_at_future(tmp_path):
    g = _group_with_logs(tmp_path)
    future = (datetime.now() + timedelta(hours=2)).strftime("%H:%M")
    cfg = {"enabled": True, "agents": {"product": [{"prompt": "r.md", "at": future}]}}
    result = compute_next_run(g, "product", cfg)
    assert result is not None
    assert result.date() == datetime.now().date()
    assert result.strftime("%H:%M") == future


def test_next_run_at_past_rolls_to_tomorrow(tmp_path):
    g = _group_with_logs(tmp_path)
    past = (datetime.now() - timedelta(hours=2)).strftime("%H:%M")
    cfg = {"enabled": True, "agents": {"product": [{"prompt": "r.md", "at": past}]}}
    result = compute_next_run(g, "product", cfg)
    assert result is not None
    assert result.date() == (datetime.now() + timedelta(days=1)).date()


def test_next_run_every_no_marker_due_now(tmp_path):
    g = _group_with_logs(tmp_path)
    cfg = {"enabled": True, "agents": {"product": [{"prompt": "r.md", "every": "6h"}]}}
    before = datetime.now()
    result = compute_next_run(g, "product", cfg)
    assert result is not None
    assert result <= datetime.now() and result >= before - timedelta(seconds=5)


def test_next_run_every_with_marker(tmp_path):
    g = _group_with_logs(tmp_path)
    marker = g["shared"] / "logs" / ".last-product-r"
    marker.touch()
    two_hours_ago = time.time() - 2 * 3600
    os.utime(marker, (two_hours_ago, two_hours_ago))
    cfg = {"enabled": True, "agents": {"product": [{"prompt": "r.md", "every": "6h"}]}}
    result = compute_next_run(g, "product", cfg)
    # marker + 6h => ~4h from now
    assert result is not None
    delta = (result - datetime.now()).total_seconds()
    assert 3.9 * 3600 < delta < 4.1 * 3600


def test_next_run_skips_condition_rule(tmp_path):
    g = _group_with_logs(tmp_path)
    cfg = {"enabled": True, "agents": {"product": [
        {"prompt": "gate.md", "at": "06:00", "condition": "pre-send"},
    ]}}
    assert compute_next_run(g, "product", cfg) is None


def test_next_run_returns_soonest(tmp_path):
    g = _group_with_logs(tmp_path)
    soon = (datetime.now() + timedelta(minutes=30)).strftime("%H:%M")
    later = (datetime.now() + timedelta(hours=5)).strftime("%H:%M")
    cfg = {"enabled": True, "agents": {"product": [
        {"prompt": "a.md", "at": later},
        {"prompt": "b.md", "at": soon},
    ]}}
    result = compute_next_run(g, "product", cfg)
    assert result.strftime("%H:%M") == soon
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_agent_status.py -k next_run -v`
Expected: FAIL with `ImportError: cannot import name 'compute_next_run'`.

- [ ] **Step 3: Write minimal implementation**

In `agency/app.py`, directly after `is_agent_running`, add:

```python
def compute_next_run(g: dict, agent_name: str, dispatch_cfg: dict) -> datetime | None:
    """Soonest upcoming dispatch datetime for an agent, or None.

    Mirrors the dispatcher's rule semantics: skips condition rules and rules
    without a prompt. 'at HH:MM' -> next occurrence (today or tomorrow).
    'every Nm/Nh' -> .last-<agent>-<stem> mtime + interval (due now if absent).
    """
    if not dispatch_cfg.get("enabled", False):
        return None
    rules = dispatch_cfg.get("agents", {}).get(agent_name, [])
    if not isinstance(rules, list):
        return None

    now = datetime.now()
    logs_root = g["shared"] / "logs"
    candidates: list[datetime] = []

    for rule in rules:
        if not isinstance(rule, dict):
            continue
        prompt = rule.get("prompt", "")
        if not prompt or rule.get("condition"):
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
            candidates.append(target)
        elif every_val:
            match = re.fullmatch(r"(\d+)(m|h)", every_val)
            if not match:
                continue
            val = int(match.group(1))
            seconds = val * 60 if match.group(2) == "m" else val * 3600
            stem = prompt.removesuffix(".md")
            marker = logs_root / f".last-{agent_name}-{stem}"
            if not marker.exists():
                candidates.append(now)
            else:
                candidates.append(
                    datetime.fromtimestamp(marker.stat().st_mtime)
                    + timedelta(seconds=seconds)
                )

    return min(candidates) if candidates else None
```

Ensure `import re` and `from datetime import datetime, timedelta` are available at the top of `agency/app.py`. If `timedelta` is not already imported, add it to the existing `datetime` import.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_agent_status.py -v`
Expected: PASS (all `is_agent_running` and `compute_next_run` tests).

- [ ] **Step 5: Commit**

```bash
git add agency/app.py tests/test_agent_status.py
git commit -m "feat(agents): add compute_next_run helper"
```

---

### Task 4: `relative_future` template filter

**Files:**
- Modify: `agency/app.py` (add filter + registration next to `relative_time`, around line 1011)
- Test: `tests/test_agent_status.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `relative_future(dt: datetime | None) -> str`, registered as `templates.env.filters["relative_future"]`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_agent_status.py`:

```python
from agency.app import relative_future


def test_relative_future_none():
    assert relative_future(None) == ""


def test_relative_future_due_now():
    assert relative_future(datetime.now() - timedelta(minutes=1)) == "due now"


def test_relative_future_minutes():
    assert relative_future(datetime.now() + timedelta(minutes=5)) == "in 5m"


def test_relative_future_hours():
    assert relative_future(datetime.now() + timedelta(hours=2, minutes=1)) == "in 2h"


def test_relative_future_tomorrow():
    dt = datetime.now() + timedelta(days=1)
    assert relative_future(dt) == f"tomorrow {dt.strftime('%H:%M')}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_agent_status.py -k relative_future -v`
Expected: FAIL with `ImportError: cannot import name 'relative_future'`.

- [ ] **Step 3: Write minimal implementation**

In `agency/app.py`, directly after the line `templates.env.filters["relative_time"] = relative_time`, add:

```python
def relative_future(dt: datetime | None) -> str:
    """Format an upcoming datetime as 'in 5m', 'in 2h', 'tomorrow HH:MM', etc."""
    if dt is None:
        return ""
    now = datetime.now()
    seconds = int((dt - now).total_seconds())
    if seconds <= 0:
        return "due now"
    minutes = seconds // 60
    if minutes < 60:
        return f"in {minutes}m"
    hours = minutes // 60
    if hours < 24 and dt.date() == now.date():
        return f"in {hours}h"
    if dt.date() == (now + timedelta(days=1)).date():
        return f"tomorrow {dt.strftime('%H:%M')}"
    return dt.strftime("%Y-%m-%d %H:%M")


templates.env.filters["relative_future"] = relative_future
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_agent_status.py -k relative_future -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add agency/app.py tests/test_agent_status.py
git commit -m "feat(agents): add relative_future template filter"
```

---

### Task 5: Enrich `collect_agents_with_identity` with running/next_run

**Files:**
- Modify: `agency/app.py` (function `collect_agents_with_identity`, both the main-agent loop and the `_subagents` loop)
- Test: `tests/test_agent_status.py`

**Interfaces:**
- Consumes: `is_agent_running`, `compute_next_run`, `GROUPS`.
- Produces: each dict in the returned `(agents, subagents)` lists gains `running: bool` and `next_run: datetime | None`.

- [ ] **Step 1: Write the failing test**

This helper reads global `GROUPS` config, so the test drives it through the module. Append to `tests/test_agent_status.py`:

```python
from unittest.mock import patch
from agency import app as app_module


def test_collect_agents_includes_running_and_next_run(tmp_path):
    # Minimal group on disk
    group_path = tmp_path / "grp"
    agent_dir = group_path / "product"
    agent_dir.mkdir(parents=True)
    (agent_dir / "CLAUDE.md").write_text("# Product\n")
    shared = group_path / "shared"
    for sub in ("observations", "proposals", "decisions", "prompts", "logs"):
        (shared / sub).mkdir(parents=True)

    g = {
        "key": "grp", "name": "Grp", "path": group_path,
        "agents": ["product"], "agents_full": [{"name": "product", "integration": "claude-code"}],
        "shared": shared,
    }

    # Mark product as running
    (shared / "logs" / ".running-product").touch()

    groups_cfg = {"grp": {"dispatch": {"enabled": True, "agents": {
        "product": [{"prompt": "r.md", "every": "6h"}]}}}}

    with patch.object(app_module, "GROUPS", groups_cfg):
        agents, _subagents = app_module.collect_agents_with_identity(g)

    product = next(a for a in agents if a["name"] == "product")
    assert product["running"] is True
    assert "next_run" in product
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_agent_status.py -k collect_agents_includes -v`
Expected: FAIL with `KeyError: 'running'`.

- [ ] **Step 3: Write minimal implementation**

In `agency/app.py`, at the top of `collect_agents_with_identity`, resolve the group's dispatch config once. Change the opening:

```python
def collect_agents_with_identity(g: dict) -> tuple[list[dict], list[dict]]:
    """Build full agent info lists. Returns (agents, subagents)."""
    observations = list_observations(g)
    agents = []
    subagents = []
```

to:

```python
def collect_agents_with_identity(g: dict) -> tuple[list[dict], list[dict]]:
    """Build full agent info lists. Returns (agents, subagents)."""
    observations = list_observations(g)
    group_cfg = GROUPS.get(g["key"], {})
    dispatch_cfg = group_cfg.get("dispatch", {})
    run_timeout = dispatch_cfg.get("timeout", 1800)
    agents = []
    subagents = []
```

In the **main-agent loop**, find:

```python
        last_seen = get_agent_last_seen(g, agent_name)
        info = {
            "name": agent_name, "dir": agent_dir, **identity,
            "last_seen": last_seen,
            "health": agent_health_status(last_seen),
            "open_observations": open_count,
            "is_subagent": identity["frontmatter"].get("subagent", False),
            "has_headshot": find_headshot(agent_dir) is not None,
            "integration": agent_int.name,
        }
```

and replace with:

```python
        last_seen = get_agent_last_seen(g, agent_name)
        info = {
            "name": agent_name, "dir": agent_dir, **identity,
            "last_seen": last_seen,
            "health": agent_health_status(last_seen),
            "open_observations": open_count,
            "is_subagent": identity["frontmatter"].get("subagent", False),
            "has_headshot": find_headshot(agent_dir) is not None,
            "integration": agent_int.name,
            "running": is_agent_running(g, agent_name, run_timeout),
            "next_run": compute_next_run(g, agent_name, dispatch_cfg),
        }
```

In the **`_subagents` loop**, find:

```python
            last_seen = get_agent_last_seen(g, d.name)
            subagents.append({
                "name": d.name, "dir": d, **identity,
                "last_seen": last_seen,
                "health": agent_health_status(last_seen),
                "open_observations": open_count, "is_subagent": True,
                "has_headshot": find_headshot(d) is not None,
                "integration": sub_int.name,
            })
```

and replace with:

```python
            last_seen = get_agent_last_seen(g, d.name)
            subagents.append({
                "name": d.name, "dir": d, **identity,
                "last_seen": last_seen,
                "health": agent_health_status(last_seen),
                "open_observations": open_count, "is_subagent": True,
                "has_headshot": find_headshot(d) is not None,
                "integration": sub_int.name,
                "running": is_agent_running(g, d.name, run_timeout),
                "next_run": compute_next_run(g, d.name, dispatch_cfg),
            })
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_agent_status.py -k collect_agents_includes -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agency/app.py tests/test_agent_status.py
git commit -m "feat(agents): expose running/next_run in agent info"
```

---

### Task 6: Combined status widget on the agents list

**Files:**
- Modify: `agency/templates/agents.html` (main-card status span and subagent-card status span)

**Interfaces:**
- Consumes: `a.running` (bool), `a.next_run` (datetime | None), plus existing `a.health`, `a.last_seen`, and the `relative_time` / `relative_future` filters.
- Produces: no new data — presentation only.

- [ ] **Step 1: Update the main-card status span**

In `agency/templates/agents.html`, find:

```html
    <div class="flex items-center gap-2 text-xs text-gray-400 flex-wrap">
      <span class="flex items-center gap-1.5" title="{{ a.last_seen.strftime('%Y-%m-%d %H:%M') if a.last_seen else 'No activity' }}">
        <span class="inline-block w-2 h-2 rounded-full shrink-0 {% if a.health == 'green' %}bg-emerald-400{% elif a.health == 'amber' %}bg-amber-400{% else %}bg-red-400{% endif %}"></span>
        {{ a.last_seen | relative_time }}
      </span>
      {% if a.integration %}{{ a.integration | integration_badge }}{% endif %}
```

and replace with:

```html
    <div class="flex items-center gap-2 text-xs text-gray-400 flex-wrap">
      <span class="flex items-center gap-1.5" title="{{ a.last_seen.strftime('%Y-%m-%d %H:%M') if a.last_seen else 'No activity' }}">
        {% if a.running %}
        <span class="inline-block w-2 h-2 rounded-full shrink-0 bg-emerald-400 animate-pulse"></span>
        Running
        {% else %}
        <span class="inline-block w-2 h-2 rounded-full shrink-0 {% if a.health == 'green' %}bg-emerald-400{% elif a.health == 'amber' %}bg-amber-400{% else %}bg-red-400{% endif %}"></span>
        {{ a.last_seen | relative_time }}
        {% if a.next_run %}<span class="text-gray-300">·</span> next {{ a.next_run | relative_future }}{% endif %}
        {% endif %}
      </span>
      {% if a.integration %}{{ a.integration | integration_badge }}{% endif %}
```

- [ ] **Step 2: Update the subagent-card status span**

In the same file, find the subagent status span:

```html
      <div class="flex items-center gap-2 text-xs text-gray-400">
        <span class="inline-block px-1.5 py-0.5 rounded-full bg-gray-200 text-gray-500 text-xs font-medium">subagent</span>
        <span class="flex items-center gap-1.5" title="{{ a.last_seen.strftime('%Y-%m-%d %H:%M') if a.last_seen else 'No activity' }}">
          <span class="inline-block w-2 h-2 rounded-full shrink-0 {% if a.health == 'green' %}bg-emerald-400{% elif a.health == 'amber' %}bg-amber-400{% else %}bg-red-400{% endif %}"></span>
          {{ a.last_seen | relative_time }}
        </span>
      </div>
```

and replace with:

```html
      <div class="flex items-center gap-2 text-xs text-gray-400">
        <span class="inline-block px-1.5 py-0.5 rounded-full bg-gray-200 text-gray-500 text-xs font-medium">subagent</span>
        <span class="flex items-center gap-1.5" title="{{ a.last_seen.strftime('%Y-%m-%d %H:%M') if a.last_seen else 'No activity' }}">
          {% if a.running %}
          <span class="inline-block w-2 h-2 rounded-full shrink-0 bg-emerald-400 animate-pulse"></span>
          Running
          {% else %}
          <span class="inline-block w-2 h-2 rounded-full shrink-0 {% if a.health == 'green' %}bg-emerald-400{% elif a.health == 'amber' %}bg-amber-400{% else %}bg-red-400{% endif %}"></span>
          {{ a.last_seen | relative_time }}
          {% if a.next_run %}<span class="text-gray-300">·</span> next {{ a.next_run | relative_future }}{% endif %}
          {% endif %}
        </span>
      </div>
```

- [ ] **Step 3: Verify the templates still render**

Run: `python -c "from agency.app import templates; templates.get_template('agents.html')"`
Expected: no output, exit code 0 (template compiles).

- [ ] **Step 4: Commit**

```bash
git add agency/templates/agents.html
git commit -m "feat(agents): combined running/next-run status widget"
```

---

### Task 7: Fleet bar running indicator on the home dashboard

**Files:**
- Modify: `agency/app.py` (function `home`, the return context)
- Modify: `agency/templates/home.html` (fleet bar dot + footer summary)

**Interfaces:**
- Consumes: `agents` list from `collect_agents_with_identity` (now carrying `running`).
- Produces: `fleet_running` int in the `home` template context; template uses `a.running` and `fleet_running`.

- [ ] **Step 1: Add `fleet_running` to the home context**

In `agency/app.py`, in the `home` route return dict, find:

```python
        # Zone 1: Fleet
        "fleet_agents": agents,
        "fleet_healthy": sum(1 for a in agents if a["health"] == "green"),
```

and replace with:

```python
        # Zone 1: Fleet
        "fleet_agents": agents,
        "fleet_healthy": sum(1 for a in agents if a["health"] == "green"),
        "fleet_running": sum(1 for a in agents if a.get("running")),
```

- [ ] **Step 2: Update the fleet dot in the template**

In `agency/templates/home.html`, find:

```html
      <span class="text-base">{{ a.emoji or '~' }}</span>
      <span class="{% if a.health == 'green' %}text-emerald-500{% elif a.health == 'amber' %}text-amber-500{% else %}text-rose-500{% endif %} text-sm leading-none">&#9679;</span>
      <span class="text-gray-700 dark:text-gray-300 text-base">{{ a.display_name or a.name }}</span>
```

and replace with:

```html
      <span class="text-base">{{ a.emoji or '~' }}</span>
      {% if a.running %}
      <span class="text-emerald-500 text-sm leading-none animate-pulse" title="Running">&#9679;</span>
      {% else %}
      <span class="{% if a.health == 'green' %}text-emerald-500{% elif a.health == 'amber' %}text-amber-500{% else %}text-rose-500{% endif %} text-sm leading-none">&#9679;</span>
      {% endif %}
      <span class="text-gray-700 dark:text-gray-300 text-base">{{ a.display_name or a.name }}</span>
```

- [ ] **Step 3: Update the footer summary line**

In the same file, find:

```html
  <div class="mt-1.5 text-sm text-gray-500 dark:text-gray-400 font-mono">
    {{ fleet_agents|length }} agents · {{ fleet_healthy }} healthy{% if fleet_agents|length - fleet_healthy > 0 %} · <span class="text-amber-600 dark:text-amber-400">{{ fleet_agents|length - fleet_healthy }} needs attention</span>{% endif %}
  </div>
```

and replace with:

```html
  <div class="mt-1.5 text-sm text-gray-500 dark:text-gray-400 font-mono">
    {{ fleet_agents|length }} agents · {{ fleet_healthy }} healthy{% if fleet_running %} · <span class="text-emerald-600 dark:text-emerald-400">{{ fleet_running }} running</span>{% endif %}{% if fleet_agents|length - fleet_healthy > 0 %} · <span class="text-amber-600 dark:text-amber-400">{{ fleet_agents|length - fleet_healthy }} needs attention</span>{% endif %}
  </div>
```

- [ ] **Step 4: Verify the template renders**

Run: `python -c "from agency.app import templates; templates.get_template('home.html')"`
Expected: no output, exit code 0.

- [ ] **Step 5: Commit**

```bash
git add agency/app.py agency/templates/home.html
git commit -m "feat(home): show running agents in fleet bar"
```

---

### Task 8: Running badge / next-run line on the agent profile

**Files:**
- Modify: `agency/app.py` (function `agent_profile`, the context dict)
- Modify: `agency/templates/agent_profile.html` (near the schedule pills block)

**Interfaces:**
- Consumes: `is_agent_running`, `compute_next_run`, existing `dispatch_cfg` already computed in the route.
- Produces: `running: bool` and `next_run: datetime | None` in the `agent_profile` context; template renders them.

- [ ] **Step 1: Add running/next_run to the profile route**

In `agency/app.py`, in `agent_profile`, find:

```python
    # Get dispatch schedule for this agent
    group_cfg = GROUPS.get(g["key"], {})
    dispatch_cfg = group_cfg.get("dispatch", {})
    agent_schedule = dispatch_cfg.get("agents", {}).get(agent, [])
    dispatch_enabled = dispatch_cfg.get("enabled", False)
```

and replace with:

```python
    # Get dispatch schedule for this agent
    group_cfg = GROUPS.get(g["key"], {})
    dispatch_cfg = group_cfg.get("dispatch", {})
    agent_schedule = dispatch_cfg.get("agents", {}).get(agent, [])
    dispatch_enabled = dispatch_cfg.get("enabled", False)
    agent_running = is_agent_running(g, agent, dispatch_cfg.get("timeout", 1800))
    agent_next_run = compute_next_run(g, agent, dispatch_cfg)
```

Then, in the same route's `TemplateResponse` context dict, find:

```python
        "agent_schedule": agent_schedule,
        "dispatch_enabled": dispatch_enabled,
        "agent_integration": agent_int.name,
```

and replace with:

```python
        "agent_schedule": agent_schedule,
        "dispatch_enabled": dispatch_enabled,
        "agent_running": agent_running,
        "agent_next_run": agent_next_run,
        "agent_integration": agent_int.name,
```

- [ ] **Step 2: Render the state in the profile template**

In `agency/templates/agent_profile.html`, find the schedule block:

```html
      {% if agent_schedule %}
```

Immediately **before** that line, insert a status row:

```html
      {% if agent_running %}
      <div class="flex items-center gap-1.5 text-xs text-emerald-600 mb-1">
        <span class="inline-block w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"></span>
        Running now
      </div>
      {% elif agent_next_run %}
      <div class="text-xs text-gray-500 mb-1" title="{{ agent_next_run.strftime('%Y-%m-%d %H:%M') }}">
        Next run: {{ agent_next_run | relative_future }} ({{ agent_next_run.strftime('%H:%M') }})
      </div>
      {% endif %}
      {% if agent_schedule %}
```

- [ ] **Step 3: Verify the template renders**

Run: `python -c "from agency.app import templates; templates.get_template('agent_profile.html')"`
Expected: no output, exit code 0.

- [ ] **Step 4: Commit**

```bash
git add agency/app.py agency/templates/agent_profile.html
git commit -m "feat(profile): show running/next-run on agent profile"
```

---

### Task 9: Full suite green + manual smoke check

**Files:**
- None (verification only).

- [ ] **Step 1: Run the whole test suite**

Run: `python -m pytest tests/ -q`
Expected: all tests pass (existing suite + the new `tests/test_agent_status.py` and the marker tests in `tests/test_dispatch_run.py`).

- [ ] **Step 2: Smoke-check the running server**

The dashboard is served via the "Serve dashboard" task. Restart it if running, then open `http://127.0.0.1:8500/{default_group}/agents` and `.../` (home). Confirm:
- Idle agents show `● 19h ago · next in Xh` when a schedule exists, `● 19h ago` otherwise.
- Manually create a marker to simulate running: `New-Item -ItemType File "<group_path>/shared/logs/.running-<agent>"`, reload the page, confirm the card shows a pulsing dot + `Running` and the fleet footer shows `· 1 running`. Delete the marker afterward.

- [ ] **Step 3: Final commit (if any docs/notes changed)**

```bash
git status
# If nothing to commit, this task is complete.
```

---

## Self-Review

**Spec coverage:**
- Running marker (spec §1) → Task 1.
- `is_agent_running` (spec §2) → Task 2.
- `compute_next_run` (spec §2) → Task 3.
- `relative_future` filter (spec §4) → Task 4.
- Enrich `collect_agents_with_identity` (spec §3) → Task 5.
- Combined status widget on agents list (spec §5, §6) → Task 6.
- Home fleet bar (spec §6) → Task 7.
- Agent profile (spec §6) → Task 8.
- Testing (spec Testing section) → Tasks 1–5 include the specified unit tests; Task 9 runs the full suite.

**Placeholder scan:** No TBD/TODO; every code step shows complete code; every command has expected output.

**Type consistency:** `is_agent_running(g, agent_name, timeout=1800) -> bool`, `compute_next_run(g, agent_name, dispatch_cfg) -> datetime | None`, and `relative_future(dt) -> str` are used with matching signatures across Tasks 2–8. Fields `running` and `next_run` are produced in Task 5 and consumed in Tasks 6–7; `agent_running`/`agent_next_run` are produced and consumed in Task 8.
