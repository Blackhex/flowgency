# Manual Agent Run from Agent Cards Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user run any group prompt as any agent directly from the `/{group}/agents` cards, executing in the background in the same environment as the scheduled dispatcher.

**Architecture:** Promote the dispatcher's per-run core into one shared public helper used by both the timer and the web app (guaranteeing identical execution + environment inheritance). Add a `POST /{group}/agents/{agent}/run` route that schedules that helper via FastAPI `BackgroundTasks`. Render all group prompts on each agent card with a per-prompt detail link and a Run button that posts via `fetch()` and updates the card in place.

**Tech Stack:** Python 3.11+, FastAPI (`BackgroundTasks`, `Request.form()`), Jinja2, Tailwind (CDN), vanilla JS `fetch`, pytest + `fastapi.testclient.TestClient`.

## Global Constraints

- Edit only `agency/` — never edit the stale `build/lib/agency/` copy.
- `integration.run(...)` takes **no `env=` argument**; the subprocess inherits the parent process environment. No task may add an `env=` override anywhere.
- This feature adds no config keys.
- One concurrent run per agent: a second run while one is active returns HTTP 409.
- Path-safety: reject any `prompt` value containing `/` or `..`.
- `docs/` is gitignored — commit plan/spec/docs with `git add -f`.
- Do not stage or modify pre-existing unstaged changes (`.gitignore`, `.vscode/`).
- Run the suite with `python -m pytest tests/ -q`.

---

### Task 1: Promote dispatcher run core to a shared public helper

**Files:**
- Modify: `agency/dispatch/run.py` (rename `_run_agent` → `run_agent_prompt`; update its caller in `run_dispatch_cycle`)
- Test: `tests/test_dispatch_run.py` (update references from `_run_agent` to `run_agent_prompt`)

**Interfaces:**
- Consumes: nothing new.
- Produces: `run_agent_prompt(group_path: Path, agent_name: str, prompt_filename: str, timeout: int, log_dir: Path, agent_config: dict, agent_dir: Path | None = None, *, sandbox_root: Path | None = None) -> None` — touches `shared/logs/.running-{agent}` before running, calls `integration.run(agent_dir, prompt_path, timeout, sandbox_root=sandbox_root)`, writes `{agent}-{stem}-{HHMMSS}.out`/`.err` into `log_dir`, and always removes the marker in a `finally`.

- [ ] **Step 1: Update the existing dispatch test to the new name**

In `tests/test_dispatch_run.py`, replace every `_run_agent` reference (imports and call sites) with `run_agent_prompt`. Do not change assertions.

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_dispatch_run.py -q`
Expected: FAIL with `ImportError` / `AttributeError` for `run_agent_prompt` (symbol not defined yet).

- [ ] **Step 3: Rename the function and update its caller**

In `agency/dispatch/run.py`:
- Rename `def _run_agent(` to `def run_agent_prompt(` (keep the signature and body byte-for-byte otherwise).
- In `run_dispatch_cycle`, change the call `_run_agent(...)` to `run_agent_prompt(...)` (same arguments).

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_dispatch_run.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agency/dispatch/run.py tests/test_dispatch_run.py
git commit -m "refactor: expose run_agent_prompt as shared dispatch helper"
```

---

### Task 2: Add the manual-run route

**Files:**
- Modify: `agency/app.py` (add `JSONResponse` import; import `run_agent_prompt`; add route)
- Test: `tests/test_agent_run.py` (new)

**Interfaces:**
- Consumes: `run_agent_prompt(...)` from Task 1; existing helpers `get_group`, `resolve_agent_dir`, `is_agent_running`, `get_agent_integration`, `get_sandbox_root`, and the module global `GROUPS`.
- Produces: route `POST /{group}/agents/{agent}/run` — form field `prompt` (filename). Returns `JSONResponse({"status": "started"}, status_code=202)` on success; raises `HTTPException` 400 (invalid prompt / no execution support), 404 (unknown agent or prompt), 409 (agent already running).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_agent_run.py`:

```python
from pathlib import Path

from fastapi.testclient import TestClient

from agency.app import app, CONFIG, GROUPS


def _setup_group(tmp_path: Path) -> Path:
    group_path = tmp_path / "grp"
    (group_path / "product").mkdir(parents=True)
    prompts = group_path / "shared" / "prompts"
    prompts.mkdir(parents=True)
    (prompts / "routine.md").write_text("# Routine\n")
    (group_path / "shared" / "logs").mkdir(parents=True)
    CONFIG.clear()
    CONFIG.update({"groups": {"test": {"name": "Test", "path": str(group_path)}}})
    GROUPS.clear()
    GROUPS["test"] = {
        "key": "test",
        "name": "Test",
        "path": group_path,
        "shared": group_path / "shared",
        "agents": ["product"],
        "agents_full": [{"name": "product", "integration": "script"}],
        "_agents_normalized": [{"name": "product", "integration": "script"}],
        "dispatch": {"timeout": 1800},
    }
    return group_path


def test_run_returns_202_and_schedules(tmp_path, monkeypatch):
    _setup_group(tmp_path)
    calls = []
    monkeypatch.setattr("agency.app.is_agent_running", lambda *a, **k: False)
    monkeypatch.setattr("agency.app.run_agent_prompt", lambda *a, **k: calls.append((a, k)))
    client = TestClient(app)

    resp = client.post("/test/agents/product/run", data={"prompt": "routine.md"})

    assert resp.status_code == 202
    assert resp.json() == {"status": "started"}
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[1] == "product"
    assert args[2] == "routine.md"


def test_run_unknown_prompt_404(tmp_path, monkeypatch):
    _setup_group(tmp_path)
    monkeypatch.setattr("agency.app.is_agent_running", lambda *a, **k: False)
    client = TestClient(app)

    resp = client.post("/test/agents/product/run", data={"prompt": "nope.md"})

    assert resp.status_code == 404


def test_run_path_traversal_400(tmp_path, monkeypatch):
    _setup_group(tmp_path)
    monkeypatch.setattr("agency.app.is_agent_running", lambda *a, **k: False)
    client = TestClient(app)

    resp = client.post("/test/agents/product/run", data={"prompt": "../secret.md"})

    assert resp.status_code == 400


def test_run_already_running_409(tmp_path, monkeypatch):
    _setup_group(tmp_path)
    monkeypatch.setattr("agency.app.is_agent_running", lambda *a, **k: True)
    client = TestClient(app)

    resp = client.post("/test/agents/product/run", data={"prompt": "routine.md"})

    assert resp.status_code == 409
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_agent_run.py -q`
Expected: FAIL — 404/405 from a missing route (route not defined yet).

- [ ] **Step 3: Add imports**

In `agency/app.py`:
- Add `JSONResponse` to the responses import, e.g.:
  `from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, JSONResponse`
- Add near the other imports: `from agency.dispatch.run import run_agent_prompt`
- Confirm `datetime` is imported (`from datetime import datetime`); add it if missing.

- [ ] **Step 4: Add the route**

Add to `agency/app.py` (near the other `/{group}/agents/...` routes):

```python
@app.post("/{group}/agents/{agent}/run")
async def agent_run(request: Request, group: str, agent: str,
                    background_tasks: BackgroundTasks):
    g = get_group(group)
    agent_dir = resolve_agent_dir(g, agent)

    form = await request.form()
    prompt = (form.get("prompt") or "").strip()
    if not prompt or "/" in prompt or ".." in prompt:
        raise HTTPException(status_code=400, detail="Invalid prompt")
    prompt_path = g["shared"] / "prompts" / prompt
    if not prompt_path.is_file():
        raise HTTPException(status_code=404, detail="Prompt not found")

    raw_cfg = GROUPS.get(g["key"], {})
    dispatch_cfg = raw_cfg.get("dispatch", {})
    run_timeout = dispatch_cfg.get("timeout", 1800)
    if is_agent_running(g, agent, run_timeout):
        raise HTTPException(status_code=409, detail="Agent already running")

    integration = get_agent_integration(g, agent)
    if not integration.supports_execution:
        raise HTTPException(status_code=400,
                            detail="Integration does not support execution")

    agent_dispatch = dispatch_cfg.get("agents", {}).get(agent, {})
    if isinstance(agent_dispatch, dict):
        run_timeout = agent_dispatch.get("timeout", run_timeout)

    agent_config = {}
    for info in g.get("agents_full", []):
        if info.get("name") == agent:
            agent_config = info
            break

    sandbox_root = get_sandbox_root(
        {"sandbox_root": raw_cfg.get("sandbox_root"), "path": g["path"]}
    )
    log_dir = g["shared"] / "logs" / datetime.now().strftime("%Y-%m-%d")
    log_dir.mkdir(parents=True, exist_ok=True)

    background_tasks.add_task(
        run_agent_prompt, g["path"], agent, prompt, run_timeout,
        log_dir, agent_config, agent_dir=agent_dir, sandbox_root=sandbox_root,
    )
    return JSONResponse({"status": "started"}, status_code=202)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_agent_run.py -q`
Expected: PASS (4 passed).

- [ ] **Step 6: Commit**

```bash
git add agency/app.py tests/test_agent_run.py
git commit -m "feat: add manual agent run route"
```

---

### Task 3: Pass all group prompts to the agents template

**Files:**
- Modify: `agency/app.py` (the `agents_list` handler for `GET /{group}/agents`)
- Test: `tests/test_agent_run.py` (add one assertion, verified after Task 4)

**Interfaces:**
- Consumes: existing `collect_prompts(g)` (returns list of dicts with `name`, `slug`, ...).
- Produces: `prompts` key in the `agents.html` template context.

- [ ] **Step 1: Add prompts to the context**

In the `agents_list` handler in `agency/app.py`, compute `prompts = collect_prompts(g)` and add `"prompts": prompts` to the `TemplateResponse` context dict.

- [ ] **Step 2: Run the existing suite to verify nothing breaks**

Run: `python -m pytest tests/test_agent_run.py -q`
Expected: PASS (existing route tests unaffected; the rendered-prompt assertions arrive in Task 4).

- [ ] **Step 3: Commit**

```bash
git add agency/app.py
git commit -m "feat: pass group prompts to agents list"
```

---

### Task 4: Render prompt list + Run interaction on agent cards

**Files:**
- Modify: `agency/templates/agents.html`
- Test: `tests/test_agent_run.py` (add assertions for the prompt list, detail link, and Run button)

**Interfaces:**
- Consumes: `prompts` context from Task 3 (each item has `.name` and `.slug`); the run route from Task 2; per-agent `a.name`, `a.running`, `a.health`, `a.next_run`.
- Produces: agent cards each containing a prompt list where the prompt name links to `/{group}/prompts/{slug}` and a Run button (`data-prompt`, `data-agent`) posts to the run route.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_agent_run.py`:

```python
def test_agents_page_lists_prompts_with_run(tmp_path):
    _setup_group(tmp_path)
    client = TestClient(app)

    resp = client.get("/test/agents")

    assert resp.status_code == 200
    assert "routine.md" in resp.text
    assert 'data-prompt="routine.md"' in resp.text
    assert "/test/prompts/" in resp.text
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_agent_run.py::test_agents_page_lists_prompts_with_run -q`
Expected: FAIL — no `data-prompt=` attribute in the page.

- [ ] **Step 3: Restructure the card and add the prompt list**

In `agency/templates/agents.html`, for the regular-agent card:
- Change the outer `<a href=".../agents/{{ a.name }}">` wrapper into a `<div class="..." data-agent-card="{{ a.name }}">`.
- Keep the avatar + name as a link to `/{{ group }}/agents/{{ a.name }}` inside the header.
- Keep the status line, but give the status dot a hook `js-status-dot` and wrap the label text in `<span class="js-status-label">`. When `a.running`, render the emerald `animate-pulse` dot + "Running"; else the existing health dot + `relative_time`/`next_run`.
- Below the status line, add:

```html
<div class="mt-3 max-h-40 overflow-y-auto border-t border-gray-100 pt-2 space-y-1">
  {% for p in prompts %}
  <div class="flex items-center justify-between gap-2 text-xs js-prompt-row">
    <a href="/{{ group }}/prompts/{{ p.slug }}"
       class="truncate text-gray-600 hover:text-indigo-600">{{ p.name }}</a>
    <button type="button"
            class="js-run-btn shrink-0 px-2 py-0.5 rounded-md border border-gray-200 text-gray-700 hover:border-indigo-300 hover:text-indigo-600"
            data-agent="{{ a.name }}" data-prompt="{{ p.name }}">Run</button>
  </div>
  {% endfor %}
  {% if not prompts %}
  <div class="text-xs text-gray-400">No prompts</div>
  {% endif %}
</div>
```

- [ ] **Step 4: Add the run script**

Add once near the end of `agency/templates/agents.html` (page-scoped):

```html
<script>
document.addEventListener("click", async (e) => {
  const btn = e.target.closest(".js-run-btn");
  if (!btn) return;
  const agent = btn.dataset.agent;
  const prompt = btn.dataset.prompt;
  const card = document.querySelector(`[data-agent-card="${agent}"]`);
  const body = new FormData();
  body.append("prompt", prompt);
  const showErr = (msg) => {
    const row = btn.closest(".js-prompt-row");
    let err = row.querySelector(".js-run-err");
    if (!err) { err = document.createElement("span"); err.className = "js-run-err text-red-500"; row.appendChild(err); }
    err.textContent = msg;
  };
  try {
    const resp = await fetch(`/{{ group }}/agents/${agent}/run`, {method: "POST", body});
    if (resp.status === 202) {
      const dot = card.querySelector(".js-status-dot");
      const label = card.querySelector(".js-status-label");
      if (dot) dot.className = "js-status-dot inline-block w-2 h-2 rounded-full shrink-0 bg-emerald-400 animate-pulse";
      if (label) label.textContent = "Running";
      card.querySelectorAll(".js-run-btn").forEach((b) => { b.disabled = true; b.classList.add("opacity-40"); });
    } else {
      showErr(resp.status === 409 ? "Busy" : "Error");
    }
  } catch (_) {
    showErr("Error");
  }
});
</script>
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `python -m pytest tests/test_agent_run.py -q`
Expected: PASS (all agent-run tests).

- [ ] **Step 6: Manual verification**

Start the app (`python -m agency.app`), open `/{group}/agents`:
- Each card shows the prompt list; clicking a prompt name opens its detail page.
- Clicking Run does not navigate; the card dot flips to pulsing "Running" and Run buttons disable.
- Clicking Run again while running shows a "Busy" inline note (409).

- [ ] **Step 7: Commit**

```bash
git add agency/templates/agents.html tests/test_agent_run.py
git commit -m "feat: prompt list with run button on agent cards"
```

---

### Task 5: Full-suite verification and environment-parity check

**Files:** none (verification only)

**Interfaces:** none.

- [ ] **Step 1: Run the whole suite**

Run: `python -m pytest tests/ -q`
Expected: PASS (all tests green, including `tests/test_dispatch_run.py` and `tests/test_agent_run.py`).

- [ ] **Step 2: Confirm no environment override was introduced**

Run: `git grep -n "env=" -- agency/dispatch agency/app.py agency/integrations`
Expected: no `env=` passed to `subprocess`/`integration.run` in the run path (environment is inherited).

- [ ] **Step 3: Manual smoke of environment parity**

Trigger a real prompt from `/{group}/agents`, then confirm the log files
`shared/logs/<YYYY-MM-DD>/{agent}-{stem}-{HHMMSS}.out`/`.err` appear with the
same naming scheme as scheduled runs.
