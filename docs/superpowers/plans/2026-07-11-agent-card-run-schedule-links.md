# Agent Card Run and Schedule Links Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make regular agent-card last-run times open the corresponding stdout log and next-run times open the exact editable schedule row.

**Architecture:** Extend the existing server-rendered agent view model with a latest-stdout record and a next-run detail record while preserving the public `compute_next_run()` datetime contract. Carry each dispatch rule's original agent-local index into the prompt-centric view model, then render normal anchors from the agent card to the existing log viewer and the indexed prompt assignment row.

**Tech Stack:** Python 3.11+, FastAPI, Jinja2, Tailwind CSS CDN, pytest, FastAPI `TestClient`

## Global Constraints

- Apply the interaction only to regular cards on `/{group}/agents`; collapsed subagent cards remain whole-card profile links.
- Add no HTTP endpoints, database state, API request, or client-side click-resolution state.
- Preserve current dispatch scheduling semantics and the `compute_next_run(g, agent_name, dispatch_cfg) -> datetime | None` interface.
- Select the latest stdout by file modification time, ignore `.err` files, and keep superseded last-seen activity as an unlinked fallback.
- Link normal editable prompts to their exact assignment row; missing and underscore-prefixed system prompts fall back to `/admin/orgs/{group}/edit#rules-{agent}`.
- Keep the status dot, separator, and `Running` label non-interactive; starting a manual run must remove both idle links in place.
- URL-encode complete stdout paths and schedule fragments so Windows paths and configured agent names navigate correctly.
- Reuse the existing log viewer and prompt editor without changing their validation or save behavior.

## File Structure

- Modify `agency/app.py`: resolve latest stdout artifacts, preserve next-run rule identity, preserve prompt assignment rule indexes, and expose both records in agent card data.
- Modify `agency/templates/agents.html`: render accessible last-run and next-run anchors inside the existing live status label.
- Modify `agency/templates/prompts.html`: give editable schedule rows stable fragment IDs and visible target styling.
- Modify `tests/test_agent_status.py`: cover pure stdout selection, next-run detail identity, tie ordering, compatibility, prompt rule indexes, and agent view-model wiring.
- Modify `tests/test_agent_run.py`: cover rendered URLs, target IDs, fallbacks, superseded activity, and running-state guards.

Design reference: `docs/superpowers/specs/2026-07-11-agent-card-run-schedule-links-design.md`

---

### Task 1: Resolve the Latest Stdout Artifact

**Files:**
- Modify: `agency/app.py:972-986`
- Test: `tests/test_agent_status.py:1-70`

**Interfaces:**
- Consumes: `g["shared"] / "logs"`, whose completed run files live one date directory below the log root, and `agent_name: str`.
- Produces: `get_agent_last_run(g: dict, agent_name: str) -> dict | None`, returning `{"at": datetime, "path": str}` with an absolute path, or `None`.

- [ ] **Step 1: Write failing tests for stdout selection**

Replace the direct import from `agency.app` with this multiline import:

```python
from agency.app import (
    compute_next_run,
    get_agent_last_run,
    is_agent_running,
    relative_future,
)
```

Add these tests after `_group_with_logs()`:

```python
def test_agent_last_run_uses_newest_stdout_mtime(tmp_path):
    g = _group_with_logs(tmp_path)
    day = g["shared"] / "logs" / "2026-07-11"
    day.mkdir()
    older = day / "product-z-manual_prompt.out"
    newer = day / "product-a-manual_prompt.out"
    newest_stderr = day / "product-newest.err"
    older.write_text("older")
    newer.write_text("")
    newest_stderr.write_text("newer stderr")

    now = time.time()
    os.utime(older, (now - 120, now - 120))
    os.utime(newer, (now - 60, now - 60))
    os.utime(newest_stderr, (now, now))

    result = get_agent_last_run(g, "product")

    assert result == {
        "at": datetime.fromtimestamp(newer.stat().st_mtime),
        "path": str(newer.resolve()),
    }


def test_agent_last_run_ignores_stderr_and_other_agents(tmp_path):
    g = _group_with_logs(tmp_path)
    day = g["shared"] / "logs" / "2026-07-11"
    day.mkdir()
    (day / "product-failed.err").write_text("failed")
    (day / "editor-manual_prompt.out").write_text("other agent")

    assert get_agent_last_run(g, "product") is None
```

- [ ] **Step 2: Run the focused tests and verify the red state**

Run:

```bash
python -m pytest tests/test_agent_status.py -k "agent_last_run" -v
```

Expected: collection fails because `get_agent_last_run` cannot yet be imported from `agency.app`.

- [ ] **Step 3: Implement the minimal latest-stdout helper**

Add this function immediately before `get_agent_last_seen()` in `agency/app.py`:

```python
def get_agent_last_run(g: dict, agent_name: str) -> dict | None:
    """Return the newest stdout log path and timestamp for an agent."""
    logs_dir = g["shared"] / "logs"
    if not logs_dir.exists():
        return None

    stdout_files = (
        path
        for path in logs_dir.glob("*/*.out")
        if path.is_file() and path.name.startswith(f"{agent_name}-")
    )
    latest = max(stdout_files, key=lambda path: path.stat().st_mtime, default=None)
    if latest is None:
        return None

    modified_at = latest.stat().st_mtime
    return {
        "at": datetime.fromtimestamp(modified_at),
        "path": str(latest.resolve()),
    }
```

- [ ] **Step 4: Run the focused tests and verify the green state**

Run:

```bash
python -m pytest tests/test_agent_status.py -k "agent_last_run" -v
```

Expected: both latest-stdout tests pass.

- [ ] **Step 5: Commit the stdout resolver**

```bash
git add agency/app.py tests/test_agent_status.py
git commit -m "feat(agents): resolve latest stdout log"
```

---

### Task 2: Preserve Next-Run Rule Identity

**Files:**
- Modify: `agency/app.py:752-800`
- Modify: `agency/app.py:985-1043`
- Test: `tests/test_agent_status.py:60-155`

**Interfaces:**
- Consumes: the existing `dispatch_cfg` shape and marker-file semantics used by `compute_next_run()`.
- Produces: `compute_next_run_detail(g: dict, agent_name: str, dispatch_cfg: dict) -> dict | None`, returning `{"when": datetime, "prompt": str, "rule_index": int}`.
- Preserves: `compute_next_run(g: dict, agent_name: str, dispatch_cfg: dict) -> datetime | None` as a compatibility wrapper.
- Produces: every explicit entry in `collect_prompts(g)` has `rule_index: int`, equal to its original position in `dispatch.agents.{agent}`.

- [ ] **Step 1: Write failing tests for winner identity, tie order, and prompt inversion**

Update the `agency.app` import block to include the new detail helper:

```python
from agency.app import (
    compute_next_run,
    compute_next_run_detail,
    get_agent_last_run,
    is_agent_running,
    relative_future,
)
```

Add these tests after `test_next_run_returns_soonest()`:

```python
def test_next_run_detail_identifies_winning_rule(tmp_path):
    g = _group_with_logs(tmp_path)
    fixed_now = datetime(2026, 1, 15, 12, 0, 0)

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now

    cfg = {"enabled": True, "agents": {"product": [
        {"prompt": "later.md", "at": "17:00"},
        {"prompt": "soon.md", "at": "12:30"},
    ]}}

    with patch.object(app_module, "datetime", _Frozen):
        detail = compute_next_run_detail(g, "product", cfg)
        compatible_value = compute_next_run(g, "product", cfg)

    assert detail == {
        "when": fixed_now + timedelta(minutes=30),
        "prompt": "soon.md",
        "rule_index": 1,
    }
    assert compatible_value == detail["when"]


def test_next_run_detail_breaks_ties_by_config_order(tmp_path):
    g = _group_with_logs(tmp_path)
    fixed_now = datetime(2026, 1, 15, 12, 0, 0)

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now

    cfg = {"enabled": True, "agents": {"product": [
        {"prompt": "first.md", "at": "13:00"},
        {"prompt": "second.md", "at": "13:00"},
    ]}}

    with patch.object(app_module, "datetime", _Frozen):
        detail = compute_next_run_detail(g, "product", cfg)

    assert detail["prompt"] == "first.md"
    assert detail["rule_index"] == 0


def test_collect_prompts_preserves_original_agent_rule_index(tmp_path):
    g = _group_with_logs(tmp_path)
    g["agents"] = ["product"]
    prompts_dir = g["shared"] / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "routine.md").write_text("# Routine\n")
    groups_cfg = {"grp": {"dispatch": {"agents": {"product": [
        {"prompt": "missing.md", "at": "08:00"},
        {"prompt": "routine.md", "at": "09:00"},
    ]}}}}

    with patch.object(app_module, "GROUPS", groups_cfg):
        prompt = next(
            item for item in app_module.collect_prompts(g)
            if item["name"] == "routine.md"
        )

    assert prompt["assignments"] == [{
        "agent": "product",
        "condition": "",
        "rule_index": 1,
        "type": "at",
        "value": "09:00",
    }]
```

- [ ] **Step 2: Run the focused tests and verify the red state**

Run:

```bash
python -m pytest tests/test_agent_status.py -k "next_run_detail or original_agent_rule_index" -v
```

Expected: collection fails because `compute_next_run_detail` does not exist; after that symbol is introduced, the rule-index assertion remains red until prompt inversion preserves the original index.

- [ ] **Step 3: Extract detailed next-run calculation and keep the wrapper**

Replace the existing `compute_next_run()` implementation with these two functions:

```python
def compute_next_run_detail(
    g: dict,
    agent_name: str,
    dispatch_cfg: dict,
) -> dict | None:
    """Return the soonest scheduled run with its originating rule identity."""
    if not dispatch_cfg.get("enabled", False):
        return None
    rules = dispatch_cfg.get("agents", {}).get(agent_name, [])
    if not isinstance(rules, list):
        return None

    now = datetime.now()
    logs_root = g["shared"] / "logs"
    candidates: list[dict] = []

    for rule_index, rule in enumerate(rules):
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
                    f"{now.strftime('%Y-%m-%d')} {at_time}",
                    "%Y-%m-%d %H:%M",
                )
            except ValueError:
                continue
            if target <= now:
                target += timedelta(days=1)
        elif every_val:
            match = re.fullmatch(r"(\d+)(m|h)", every_val)
            if not match:
                continue
            value = int(match.group(1))
            seconds = value * 60 if match.group(2) == "m" else value * 3600
            stem = prompt.removesuffix(".md")
            marker = logs_root / f".last-{agent_name}-{stem}"
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
            "prompt": prompt,
            "rule_index": rule_index,
        })

    return min(candidates, key=lambda candidate: candidate["when"], default=None)


def compute_next_run(g: dict, agent_name: str, dispatch_cfg: dict) -> datetime | None:
    """Return the soonest upcoming dispatch datetime for an agent."""
    detail = compute_next_run_detail(g, agent_name, dispatch_cfg)
    return detail["when"] if detail else None
```

Python's `min()` returns the first item for equal keys, preserving config order for ties.

- [ ] **Step 4: Preserve the agent-local rule index in prompt assignments**

Change the inversion loop in `collect_prompts()` to enumerate each agent's rules and include the original index:

```python
    prompt_assignments: dict[str, list[dict]] = {}
    for agent_name, rules in dispatch_agents.items():
        for rule_index, rule in enumerate(rules):
            prompt_file = rule.get("prompt", "")
            if not prompt_file:
                continue
            if prompt_file not in prompt_assignments:
                prompt_assignments[prompt_file] = []
            entry = {
                "agent": agent_name,
                "condition": rule.get("condition", ""),
                "rule_index": rule_index,
            }
            if rule.get("at"):
                entry["type"] = "at"
                entry["value"] = rule["at"]
            elif rule.get("every"):
                entry["type"] = "every"
                entry["value"] = rule["every"]
            else:
                continue
            prompt_assignments[prompt_file].append(entry)
```

Leave inferred unscheduled assignment records unchanged; they do not correspond to persisted schedule rules and do not need `rule_index`.

- [ ] **Step 5: Run all agent-status tests**

Run:

```bash
python -m pytest tests/test_agent_status.py -v
```

Expected: all tests in `tests/test_agent_status.py` pass, including the pre-existing datetime-only contract tests.

- [ ] **Step 6: Commit next-run identity preservation**

```bash
git add agency/app.py tests/test_agent_status.py
git commit -m "feat(agents): preserve next-run rule identity"
```

---

### Task 3: Render Run and Schedule Navigation

**Files:**
- Modify: `agency/app.py:1115-1188`
- Modify: `agency/templates/agents.html:34-46`
- Modify: `agency/templates/prompts.html:68-105`
- Modify: `tests/test_agent_status.py:193-225`
- Modify: `tests/test_agent_run.py:1-145`

**Interfaces:**
- Consumes: `get_agent_last_run(g, agent_name) -> {"at": datetime, "path": str} | None` from Task 1.
- Consumes: `compute_next_run_detail(g, agent_name, dispatch_cfg) -> {"when": datetime, "prompt": str, "rule_index": int} | None` and `assignment["rule_index"]` from Task 2.
- Produces: regular-agent dictionaries with `last_run`, `last_seen`, `next_run`, and `next_run_detail` fields whose timestamps agree.
- Produces: `/{group}/logs/view?path={encoded_path}`, `/{group}/prompts#schedule-{agent}-{rule_index}`, and `/admin/orgs/{group}/edit#rules-{agent}` anchors.
- Preserves: `.js-status-label` as the manual-run script's replacement target, wrapping all idle timing content so `label.textContent = "Running"` removes both links.

- [ ] **Step 1: Write failing route and view-model tests**

Add `quote` and pytest to `tests/test_agent_run.py` imports:

```python
from urllib.parse import quote

import pytest
```

Add these helpers after `_setup_group()`:

```python
def _configure_schedule(prompt: str) -> None:
    app_mod.GROUPS["test"]["dispatch"] = {
        "enabled": True,
        "timeout": 1800,
        "agents": {
            "product": [{"prompt": prompt, "every": "6h"}],
        },
    }


def _write_stdout(group_path: Path) -> Path:
    day = group_path / "shared" / "logs" / "2026-07-11"
    day.mkdir()
    stdout_path = day / "product-manual_prompt-job-1.out"
    stdout_path.write_text("")
    return stdout_path
```

Add these route tests after the existing agent-page tests:

```python
def test_agents_page_links_last_stdout_and_next_schedule(tmp_path):
    group_path = _setup_group(tmp_path)
    stdout_path = _write_stdout(group_path)
    _configure_schedule("product-routine.md")
    client = TestClient(app)

    resp = client.get("/test/agents")

    assert resp.status_code == 200
    encoded_path = quote(str(stdout_path.resolve()), safe="/")
    assert f'href="/test/logs/view?path={encoded_path}"' in resp.text
    assert 'href="/test/prompts#schedule-product-0"' in resp.text
    assert "last run stdout log" in resp.text
    assert 'aria-label="Edit schedule for product-routine.md"' in resp.text


def test_prompts_page_marks_exact_schedule_target(tmp_path):
    _setup_group(tmp_path)
    _configure_schedule("product-routine.md")
    client = TestClient(app)

    resp = client.get("/test/prompts")

    assert resp.status_code == 200
    assert 'id="schedule-product-0"' in resp.text
    assert "scroll-mt-20" in resp.text
    assert "target:ring-2" in resp.text


@pytest.mark.parametrize(
    "prompt",
    ["missing.md", "_observation-system-steps.md"],
)
def test_agents_page_uses_group_settings_for_uneditable_schedule(
    tmp_path,
    prompt,
):
    _setup_group(tmp_path)
    _configure_schedule(prompt)
    client = TestClient(app)

    resp = client.get("/test/agents")

    assert resp.status_code == 200
    assert 'href="/admin/orgs/test/edit#rules-product"' in resp.text
    assert 'href="/test/prompts#schedule-product-0"' not in resp.text


def test_agents_page_keeps_superseded_activity_unlinked(tmp_path):
    group_path = _setup_group(tmp_path)
    day = group_path / "shared" / "logs" / "2026-07-11"
    day.mkdir()
    (day / "product-superseded.err").write_text("superseded failure")
    client = TestClient(app)

    resp = client.get("/test/agents")

    assert resp.status_code == 200
    assert "Just now" in resp.text
    assert "/test/logs/view?path=" not in resp.text
    assert "/test/prompts#schedule-" not in resp.text
    assert "last run stdout log" not in resp.text


def test_agents_page_running_status_has_no_time_links(tmp_path, monkeypatch):
    group_path = _setup_group(tmp_path)
    stdout_path = _write_stdout(group_path)
    _configure_schedule("product-routine.md")
    monkeypatch.setattr(app_mod, "is_agent_running", lambda *args, **kwargs: True)
    client = TestClient(app)

    resp = client.get("/test/agents")

    assert resp.status_code == 200
    assert "Running" in resp.text
    encoded_path = quote(str(stdout_path.resolve()), safe="/")
    assert f"/test/logs/view?path={encoded_path}" not in resp.text
    assert "/test/prompts#schedule-product-0" not in resp.text
```

Replace `test_collect_agents_includes_running_and_next_run()` with this complete version:

```python
def test_collect_agents_includes_running_and_next_run(tmp_path):
    group_path = tmp_path / "grp"
    agent_dir = group_path / "product"
    agent_dir.mkdir(parents=True)
    (agent_dir / "CLAUDE.md").write_text("# Product\n")
    shared = group_path / "shared"
    for sub in ("observations", "proposals", "decisions", "prompts", "logs"):
        (shared / sub).mkdir(parents=True)

    stdout_dir = shared / "logs" / "2026-07-11"
    stdout_dir.mkdir()
    stdout_path = stdout_dir / "product-manual_prompt-job-1.out"
    stdout_path.write_text("")

    g = {
        "key": "grp", "name": "Grp", "path": group_path,
        "agents": ["product"],
        "agents_full": [{"name": "product", "integration": "claude-code"}],
        "shared": shared,
    }

    _write_job(group_path, "running")

    groups_cfg = {"grp": {"dispatch": {"enabled": True, "agents": {
        "product": [{"prompt": "r.md", "every": "6h"}],
    }}}}

    with patch.object(app_module, "GROUPS", groups_cfg):
        agents, _subagents = app_module.collect_agents_with_identity(g)

    product = next(agent for agent in agents if agent["name"] == "product")
    assert product["running"] is True
    assert product["last_run"]["path"] == str(stdout_path.resolve())
    assert product["last_seen"] == product["last_run"]["at"]
    assert product["next_run"] == product["next_run_detail"]["when"]
    assert product["next_run_detail"]["prompt"] == "r.md"
    assert product["next_run_detail"]["rule_index"] == 0
```

- [ ] **Step 2: Run the focused tests and verify the red state**

Run:

```bash
python -m pytest tests/test_agent_status.py tests/test_agent_run.py -k "collect_agents or last_stdout or exact_schedule_target or uneditable_schedule or superseded_activity or running_status" -v
```

Expected: failures show missing `last_run`/`next_run_detail` fields, missing card anchors, and a missing schedule-row fragment ID. The superseded and running guard tests may already pass independently, but the focused selection must remain red until the feature fields and links exist.

- [ ] **Step 3: Wire both detail records into agent dictionaries**

In the regular-agent loop in `collect_agents_with_identity()`, resolve the two detail records once and build the final fields as follows:

```python
        last_run = get_agent_last_run(g, agent_name)
        last_seen = (
            last_run["at"]
            if last_run
            else get_agent_last_seen(g, agent_name)
        )
        next_run_detail = compute_next_run_detail(g, agent_name, dispatch_cfg)
        info = {
            "name": agent_name, "dir": agent_dir, **identity,
            "last_run": last_run,
            "last_seen": last_seen,
            "health": agent_health_status(last_seen),
            "open_observations": open_count,
            "is_subagent": identity["frontmatter"].get("subagent", False),
            "has_headshot": find_headshot(agent_dir) is not None,
            "integration": agent_int.name,
            "running": is_agent_running(g, agent_name, run_timeout),
            "next_run": (
                next_run_detail["when"] if next_run_detail else None
            ),
            "next_run_detail": next_run_detail,
        }
```

In the `_subagents` directory loop, expose the same consistent fields even though the collapsed subagent template does not link them:

```python
            last_run = get_agent_last_run(g, d.name)
            last_seen = (
                last_run["at"]
                if last_run
                else get_agent_last_seen(g, d.name)
            )
            next_run_detail = compute_next_run_detail(g, d.name, dispatch_cfg)
            subagents.append({
                "name": d.name, "dir": d, **identity,
                "last_run": last_run,
                "last_seen": last_seen,
                "health": agent_health_status(last_seen),
                "open_observations": open_count, "is_subagent": True,
                "has_headshot": find_headshot(d) is not None,
                "integration": sub_int.name,
                "running": is_agent_running(g, d.name, run_timeout),
                "next_run": (
                    next_run_detail["when"] if next_run_detail else None
                ),
                "next_run_detail": next_run_detail,
            })
```

- [ ] **Step 4: Render accessible card links inside the live status wrapper**

Replace the regular-card status span in `agency/templates/agents.html` with this block. Do not change the collapsed subagent card block.

```html
      <span class="flex items-center gap-1.5">
        {% if a.running %}
        <span class="js-status-dot inline-block w-2 h-2 rounded-full shrink-0 bg-emerald-400 animate-pulse"></span>
        <span class="js-status-label">Running</span>
        {% else %}
        <span class="js-status-dot inline-block w-2 h-2 rounded-full shrink-0 {% if a.health == 'green' %}bg-emerald-400{% elif a.health == 'amber' %}bg-amber-400{% else %}bg-red-400{% endif %}"></span>
        <span class="js-status-label flex items-center gap-1.5">
          {% if a.last_run %}
          <a href="/{{ group }}/logs/view?path={{ a.last_run.path | urlencode }}"
             class="rounded-sm hover:text-indigo-600 hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-300"
             title="Open stdout from {{ a.last_run.at.strftime('%Y-%m-%d %H:%M') }}"
             aria-label="Open {{ a.display_name }} last run stdout log">{{ a.last_run.at | relative_time }}</a>
          {% else %}
          <span title="{{ a.last_seen.strftime('%Y-%m-%d %H:%M') if a.last_seen else 'No activity' }}">{{ a.last_seen | relative_time }}</span>
          {% endif %}
          {% if a.next_run and a.next_run_detail %}
          <span class="text-gray-300">·</span>
          {% set prompt_names = a.prompts | map(attribute='name') | list %}
          {% if a.next_run_detail.prompt in prompt_names and not a.next_run_detail.prompt.startswith('_') %}
          <a href="/{{ group }}/prompts#schedule-{{ a.name | urlencode }}-{{ a.next_run_detail.rule_index }}"
          {% else %}
          <a href="/admin/orgs/{{ group }}/edit#rules-{{ a.name | urlencode }}"
          {% endif %}
             class="rounded-sm hover:text-indigo-600 hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-300"
             title="Edit {{ a.next_run_detail.prompt }} schedule - {{ a.next_run.strftime('%Y-%m-%d %H:%M') }}"
             aria-label="Edit schedule for {{ a.next_run_detail.prompt }}">{{ a.next_run | relative_future }}</a>
          {% endif %}
        </span>
        {% endif %}
      </span>
```

The existing manual-run JavaScript still executes `label.textContent = "Running"`. Because `.js-status-label` now wraps the entire idle timing sequence, that assignment removes the last-run anchor, separator, and next-run anchor together without any JavaScript changes.

- [ ] **Step 5: Add exact fragment targets to editable prompt rows**

In the `elif a.type` branch of `agency/templates/prompts.html`, replace the opening assignment-row `<div>` with:

```html
          <div id="schedule-{{ a.agent }}-{{ a.rule_index }}"
               class="flex items-center gap-2 assignment-row scroll-mt-20 rounded-lg transition-colors target:bg-indigo-50 target:ring-2 target:ring-indigo-200">
```

Keep the existing select controls and remove button unchanged. Jinja auto-escapes the row ID, while the card href URL-encodes the agent-derived fragment component.

- [ ] **Step 6: Run both focused test modules**

Run:

```bash
python -m pytest tests/test_agent_status.py tests/test_agent_run.py -v
```

Expected: all tests in both modules pass.

- [ ] **Step 7: Run the complete regression suite**

Run:

```bash
python -m pytest tests/ -q
```

Expected: the suite completes with zero failures.

- [ ] **Step 8: Perform a browser smoke test against the running dashboard**

Use the existing hot-reload server, or start it with:

```bash
christag-agency serve --reload --host 127.0.0.1
```

Open `http://127.0.0.1:8500/christag-agency/agents` and verify:

1. Hovering or focusing the last-run time reveals link styling; clicking it opens that agent's newest `.out` content in the existing log viewer.
2. Returning to Agents and clicking the next-run time opens Agent Prompts at the matching editable row.
3. The targeted row is scrolled into view and visibly highlighted.
4. Triggering a manual Run changes the status to non-link `Running` and removes both idle timing links.
5. Collapsed subagent cards still navigate as a single profile link.

- [ ] **Step 9: Commit the rendered navigation**

```bash
git add agency/app.py agency/templates/agents.html agency/templates/prompts.html tests/test_agent_status.py tests/test_agent_run.py
git commit -m "feat(agents): link run status to logs and schedules"
```