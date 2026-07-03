# Agent Sandbox Root Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an optional group-level `sandbox_root` config field that controls how sandbox-capable agent runtimes are launched — unset means full filesystem access, set means the runtime is confined to `cwd + sandbox_root`.

**Architecture:** A new `get_sandbox_root(g)` helper resolves the config value to a `Path | None`. The value is threaded through the two `integration.run(...)` call sites (dispatch and decision execution) as a keyword-only argument. `BaseIntegration` gains a `supports_sandbox` flag and a `sandbox_root` parameter on `run()`; the Copilot integration branches its CLI flags on it. The admin org-edit UI gains a "Sandbox root" field.

**Tech Stack:** Python 3.11+, FastAPI, Jinja2, pytest. No new dependencies.

## Global Constraints

- The Agency web-UI file boundary (`get_allowed_roots` / `validate_file_access`) MUST NOT be changed by this feature. `sandbox_root` governs agent runtime only.
- The agent always launches with `cwd = agent_dir`. `sandbox_root` changes only permission flags, never the working directory.
- `run()` gains a keyword-only, defaulted argument (`*, sandbox_root: Path | None = None`) so every existing override and call site keeps working unchanged.
- Config writes are atomic (`save_config`) and always followed by `reload_groups()`.
- Copilot flag mapping (verified against installed CLI): unset → `--autopilot --allow-all-paths --experimental`; set → `--autopilot --add-dir <root> --experimental`. `--autopilot` is present in both modes.
- Only the `copilot` integration is wired in this plan. `claude-code` and `codex` confine-flag support is explicitly out of scope (their exact flags are unverified; they keep `supports_sandbox = False` and their current behavior).
- Tests run with: `python -m pytest tests/ -q`

---

### Task 1: `get_sandbox_root` config helper

**Files:**
- Modify: `agency/config.py` (add helper after `get_allowed_roots`, ~line 61)
- Test: `tests/test_config_normalization.py`

**Interfaces:**
- Produces: `get_sandbox_root(g: dict) -> Path | None` — returns absolute path if `sandbox_root` set (absolute used as-is; relative resolved against `g["path"]`); `None` if unset/empty/whitespace or if `g` has no usable `path`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_config_normalization.py`:

```python
from pathlib import Path

from agency.config import get_sandbox_root


def test_get_sandbox_root_absolute_passthrough():
    g = {"path": "/groups/agents", "sandbox_root": "/repo/root"}
    assert get_sandbox_root(g) == Path("/repo/root")


def test_get_sandbox_root_relative_resolved_against_group_path():
    g = {"path": "/groups/agents", "sandbox_root": ".."}
    assert get_sandbox_root(g) == (Path("/groups/agents") / "..").resolve()


def test_get_sandbox_root_missing_returns_none():
    assert get_sandbox_root({"path": "/groups/agents"}) is None


def test_get_sandbox_root_empty_returns_none():
    assert get_sandbox_root({"path": "/groups/agents", "sandbox_root": "   "}) is None


def test_get_sandbox_root_no_path_returns_none():
    assert get_sandbox_root({"sandbox_root": "relative/only"}) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_config_normalization.py -k sandbox_root -v`
Expected: FAIL with `ImportError: cannot import name 'get_sandbox_root'`

- [ ] **Step 3: Implement the helper**

In `agency/config.py`, add after the `get_allowed_roots` function:

```python
def get_sandbox_root(g: dict) -> Path | None:
    """Resolve a group's optional sandbox_root to an absolute Path.

    Absolute paths are used as-is. Relative paths are resolved against the
    group path. Returns None if unset/blank or if no group path is available
    to resolve a relative value.
    """
    raw = g.get("sandbox_root")
    if not raw or not str(raw).strip():
        return None
    p = Path(str(raw).strip())
    if p.is_absolute():
        return p
    base = g.get("path")
    if not base:
        return None
    return (Path(base) / p).resolve()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_config_normalization.py -k sandbox_root -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add agency/config.py tests/test_config_normalization.py
git commit -m "feat: add get_sandbox_root config helper"
```

---

### Task 2: `supports_sandbox` flag + `run()` sandbox_root parameter on base class

**Files:**
- Modify: `agency/integrations/__init__.py` (class `BaseIntegration`, ~lines 55-64)
- Test: `tests/test_integrations.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `BaseIntegration.supports_sandbox: bool = False` class attribute; updated base signature `run(self, agent_dir: Path, prompt_file: Path, timeout: int, *, sandbox_root: Path | None = None) -> RunResult`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_integrations.py`:

```python
import inspect

from agency.integrations import BaseIntegration


def test_base_integration_supports_sandbox_defaults_false():
    assert BaseIntegration.supports_sandbox is False


def test_base_run_accepts_sandbox_root_kwarg():
    sig = inspect.signature(BaseIntegration.run)
    param = sig.parameters.get("sandbox_root")
    assert param is not None
    assert param.kind is inspect.Parameter.KEYWORD_ONLY
    assert param.default is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_integrations.py -k "supports_sandbox or sandbox_root_kwarg" -v`
Expected: FAIL — `AttributeError: type object 'BaseIntegration' has no attribute 'supports_sandbox'`

- [ ] **Step 3: Implement the change**

In `agency/integrations/__init__.py`, update the `BaseIntegration` class attributes and `run` signature. Change:

```python
    supports_execution: bool = True
    supports_ai_backend: bool = False
    detect_priority: int = 100

    def run(self, agent_dir: Path, prompt_file: Path, timeout: int) -> RunResult:
        """Execute an agent with a prompt.

        prompt_file is always a Path to the prompt file on disk. The integration
        is responsible for deciding how to pass it to the tool.
        """
        raise NotImplementedError
```

to:

```python
    supports_execution: bool = True
    supports_ai_backend: bool = False
    supports_sandbox: bool = False
    detect_priority: int = 100

    def run(self, agent_dir: Path, prompt_file: Path, timeout: int,
            *, sandbox_root: Path | None = None) -> RunResult:
        """Execute an agent with a prompt.

        prompt_file is always a Path to the prompt file on disk. The integration
        is responsible for deciding how to pass it to the tool.

        sandbox_root, when provided, is a directory the agent is allowed to
        read/write in addition to its working directory. Integrations that do
        not support sandboxing ignore it.
        """
        raise NotImplementedError
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_integrations.py -k "supports_sandbox or sandbox_root_kwarg" -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Run full integration suite to confirm no regressions**

Run: `python -m pytest tests/test_integrations.py tests/test_integration_sidecar.py -q`
Expected: PASS (all)

- [ ] **Step 6: Commit**

```bash
git add agency/integrations/__init__.py tests/test_integrations.py
git commit -m "feat: add supports_sandbox flag and sandbox_root param to BaseIntegration.run"
```

---

### Task 3: Copilot integration — sandbox-aware invocation

**Files:**
- Modify: `agency/integrations/agency/copilot.py` (class attrs ~line 12-17, `run` method ~lines 33-56)
- Test: `tests/test_integration_sidecar.py`

**Interfaces:**
- Consumes: `BaseIntegration.run(..., *, sandbox_root=None)` signature from Task 2.
- Produces: `CopilotIntegration.supports_sandbox = True`; `run()` builds argv as `--autopilot --allow-all-paths --experimental` when `sandbox_root is None`, and `--autopilot --add-dir <root> --experimental` when set.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_integration_sidecar.py`:

```python
def test_copilot_supports_sandbox_true():
    from agency.integrations.agency.copilot import CopilotIntegration
    assert CopilotIntegration.supports_sandbox is True


def test_copilot_run_unset_sandbox_uses_allow_all_paths(tmp_path, monkeypatch):
    from agency.integrations.agency import copilot as copilot_mod
    from agency.integrations.agency.copilot import CopilotIntegration

    prompt = tmp_path / "p.prompt"
    prompt.write_text("do the thing")

    captured = {}

    class FakeCompleted:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_run(args, **kwargs):
        captured["args"] = args
        return FakeCompleted()

    monkeypatch.setattr(copilot_mod.subprocess, "run", fake_run)
    monkeypatch.setattr(CopilotIntegration, "_find_cmd", lambda self: "copilot")

    CopilotIntegration().run(tmp_path, prompt, timeout=60)

    assert "--allow-all-paths" in captured["args"]
    assert "--add-dir" not in captured["args"]
    assert "--autopilot" in captured["args"]


def test_copilot_run_set_sandbox_uses_add_dir(tmp_path, monkeypatch):
    from agency.integrations.agency import copilot as copilot_mod
    from agency.integrations.agency.copilot import CopilotIntegration

    prompt = tmp_path / "p.prompt"
    prompt.write_text("do the thing")
    root = tmp_path / "repo"
    root.mkdir()

    captured = {}

    class FakeCompleted:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_run(args, **kwargs):
        captured["args"] = args
        return FakeCompleted()

    monkeypatch.setattr(copilot_mod.subprocess, "run", fake_run)
    monkeypatch.setattr(CopilotIntegration, "_find_cmd", lambda self: "copilot")

    CopilotIntegration().run(tmp_path, prompt, timeout=60, sandbox_root=root)

    args = captured["args"]
    assert "--add-dir" in args
    assert args[args.index("--add-dir") + 1] == str(root)
    assert "--allow-all-paths" not in args
    assert "--autopilot" in args
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_integration_sidecar.py -k "copilot_supports_sandbox or copilot_run_unset or copilot_run_set" -v`
Expected: FAIL — `supports_sandbox` is False and argv contains neither `--allow-all-paths` nor `--add-dir`.

- [ ] **Step 3: Implement the change**

In `agency/integrations/agency/copilot.py`, add the class attribute after `supports_ai_backend`:

```python
    name = "copilot"
    display_name = "GitHub Copilot"
    supports_execution = True
    supports_ai_backend = True
    supports_sandbox = True
    detect_priority = 7
```

Replace the `run` method's signature and argv construction. Change:

```python
    def run(self, agent_dir: Path, prompt_file: Path, timeout: int) -> RunResult:
        prompt_text = prompt_file.read_text()
        cmd = self._find_cmd()
        start = time.monotonic()
        try:
            result = subprocess.run(
                [cmd, "-p", prompt_text, "--autopilot", "--experimental"],
                capture_output=True, text=True, timeout=timeout,
                cwd=str(agent_dir),
            )
```

to:

```python
    def run(self, agent_dir: Path, prompt_file: Path, timeout: int,
            *, sandbox_root: Path | None = None) -> RunResult:
        prompt_text = prompt_file.read_text()
        cmd = self._find_cmd()
        if sandbox_root is not None:
            path_args = ["--add-dir", str(sandbox_root)]
        else:
            path_args = ["--allow-all-paths"]
        start = time.monotonic()
        try:
            result = subprocess.run(
                [cmd, "-p", prompt_text, "--autopilot", *path_args, "--experimental"],
                capture_output=True, text=True, timeout=timeout,
                cwd=str(agent_dir),
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_integration_sidecar.py -k "copilot_supports_sandbox or copilot_run_unset or copilot_run_set" -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Run full sidecar suite to confirm no regressions**

Run: `python -m pytest tests/test_integration_sidecar.py -q`
Expected: PASS (all)

- [ ] **Step 6: Commit**

```bash
git add agency/integrations/agency/copilot.py tests/test_integration_sidecar.py
git commit -m "feat: copilot run confines to sandbox_root when set, full access otherwise"
```

---

### Task 4: Thread sandbox_root through dispatch

**Files:**
- Modify: `agency/dispatch/run.py` (import ~line 15, `_run_agent` signature ~line 130 and `integration.run(...)` call ~line 197, `run_dispatch_cycle` resolution + call ~lines 100-143)
- Test: `tests/test_dispatch_run.py`

**Interfaces:**
- Consumes: `get_sandbox_root` (Task 1); `integration.run(..., sandbox_root=...)` (Tasks 2-3).
- Produces: `_run_agent(...)` gains a keyword-only `sandbox_root: Path | None = None` param and forwards it to `integration.run(...)`.

- [ ] **Step 1: Read the current dispatch import and call structure**

Confirm the current config import line in `agency/dispatch/run.py`:

```python
from agency.config import normalize_agents, agent_names, get_agent_dir
```

Confirm `_run_agent` currently ends with:

```python
        result = integration.run(agent_dir, prompt_path, timeout)
```

Confirm `run_dispatch_cycle` sets `group_path = Path(g["path"])` and later calls `_run_agent(...)`.

- [ ] **Step 2: Write the failing test**

Add to `tests/test_dispatch_run.py`:

```python
def test_run_agent_forwards_sandbox_root(tmp_path, monkeypatch):
    from pathlib import Path
    import agency.dispatch.run as run_mod

    agent_dir = tmp_path / "advisor"
    agent_dir.mkdir()
    prompt = tmp_path / "prompt.md"
    prompt.write_text("hi")

    captured = {}

    class FakeIntegration:
        name = "copilot"
        supports_execution = True
        supports_sandbox = True

        def run(self, agent_dir, prompt_file, timeout, *, sandbox_root=None):
            captured["sandbox_root"] = sandbox_root
            class R:
                exit_code = 0
                stdout = "ok"
                stderr = ""
                duration_seconds = 0.1
            return R()

    monkeypatch.setattr(run_mod, "get_integration", lambda name: FakeIntegration())

    run_mod._run_agent(
        integration=FakeIntegration(),
        agent_dir=agent_dir,
        prompt_path=prompt,
        timeout=60,
        log_dir=tmp_path,
        agent_name="advisor",
        prompt_name="prompt.md",
        sandbox_root=Path("/repo/root"),
    )

    assert captured["sandbox_root"] == Path("/repo/root")
```

> NOTE: adjust the `_run_agent(...)` keyword arguments in this test to match the
> real parameter names in `agency/dispatch/run.py` (read the function signature
> first). The assertion — that `sandbox_root` reaches `integration.run` — is the
> point of the test.

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_dispatch_run.py -k sandbox_root -v`
Expected: FAIL — `_run_agent() got an unexpected keyword argument 'sandbox_root'`

- [ ] **Step 4: Implement the change**

In `agency/dispatch/run.py`:

1. Update the config import:

```python
from agency.config import normalize_agents, agent_names, get_agent_dir, get_sandbox_root
```

2. Add `sandbox_root` to the `_run_agent` signature (keyword-only, defaulted) and forward it. Change the call inside `_run_agent`:

```python
        result = integration.run(agent_dir, prompt_path, timeout)
```

to:

```python
        result = integration.run(agent_dir, prompt_path, timeout, sandbox_root=sandbox_root)
```

and add `sandbox_root: Path | None = None` as a keyword-only parameter to the `_run_agent` definition (append `, *, sandbox_root: Path | None = None` to its parameter list, or add to the existing keyword-only section).

3. In `run_dispatch_cycle`, right after `group_path = Path(g["path"])`, resolve:

```python
        sandbox_root = get_sandbox_root(g)
```

4. Pass it into the `_run_agent(...)` call by adding `sandbox_root=sandbox_root` to that call's keyword arguments.

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_dispatch_run.py -k sandbox_root -v`
Expected: PASS

- [ ] **Step 6: Run full dispatch suite to confirm no regressions**

Run: `python -m pytest tests/test_dispatch_run.py -q`
Expected: PASS (all)

- [ ] **Step 7: Commit**

```bash
git add agency/dispatch/run.py tests/test_dispatch_run.py
git commit -m "feat: thread sandbox_root through dispatch to integration.run"
```

---

### Task 5: Thread sandbox_root through decision execution

**Files:**
- Modify: `agency/app.py` (`execute_decision` — the `result = agent_integration.run(...)` call, and `g = GROUPS.get(group_key, {})` nearby)
- Test: `tests/test_dashboard.py` (or a new `tests/test_execute_decision.py` if no suitable home exists)

**Interfaces:**
- Consumes: `get_sandbox_root` (Task 1, already imported in `app.py` alongside `get_allowed_roots`); `integration.run(..., sandbox_root=...)` (Tasks 2-3).
- Produces: `execute_decision` resolves `sandbox_root = get_sandbox_root(g)` and passes it to `agent_integration.run(...)`.

- [ ] **Step 1: Add the import**

In `agency/app.py`, update the config import to include `get_sandbox_root`. Change:

```python
from agency.config import normalize_agents, agent_names, get_agent_dir, get_allowed_roots, find_agent_in_config, is_shared_agent
```

to:

```python
from agency.config import normalize_agents, agent_names, get_agent_dir, get_allowed_roots, get_sandbox_root, find_agent_in_config, is_shared_agent
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_execute_decision.py`:

```python
from pathlib import Path

import agency.app as app_mod


def test_execute_decision_passes_sandbox_root(tmp_path, monkeypatch):
    group_path = tmp_path / "agents"
    (group_path / "shared" / "decisions").mkdir(parents=True)
    (group_path / "shared" / "logs").mkdir(parents=True)
    agent_dir = group_path / "advisor"
    agent_dir.mkdir()

    decision = group_path / "shared" / "decisions" / "prop.md"
    decision.write_text("---\nexecution_status: pending\n---\n")

    captured = {}

    class FakeIntegration:
        name = "copilot"
        supports_execution = True
        supports_sandbox = True

        def run(self, agent_dir, prompt_file, timeout, *, sandbox_root=None):
            captured["sandbox_root"] = sandbox_root
            class R:
                exit_code = 0
                stdout = "ok"
                stderr = ""
                duration_seconds = 0.1
            return R()

    monkeypatch.setitem(
        app_mod.GROUPS,
        "grp",
        {"path": str(group_path), "sandbox_root": str(tmp_path / "repo"),
         "_agents_normalized": [{"name": "advisor", "integration": "copilot"}]},
    )
    monkeypatch.setattr(app_mod, "get_agent_integration", lambda g, a: FakeIntegration())

    app_mod.execute_decision(decision, group_path, "advisor", "prop", group_key="grp")

    assert captured["sandbox_root"] == Path(str(tmp_path / "repo"))
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_execute_decision.py -v`
Expected: FAIL — `KeyError: 'sandbox_root'` in `captured` (value never set because current code calls `run(...)` without the kwarg, so the fake still records `None`; the equality assertion fails).

- [ ] **Step 4: Implement the change**

In `agency/app.py` `execute_decision`, locate:

```python
        result = agent_integration.run(agent_dir, prompt_file, timeout=timeout)
```

Change it to resolve and pass the sandbox root. Immediately before that line, add:

```python
        sandbox_root = get_sandbox_root(g)
```

(`g = GROUPS.get(group_key, {})` is already defined earlier in the function and carries the raw `path` + `sandbox_root`.) Then change the call to:

```python
        result = agent_integration.run(agent_dir, prompt_file, timeout=timeout, sandbox_root=sandbox_root)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_execute_decision.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add agency/app.py tests/test_execute_decision.py
git commit -m "feat: pass sandbox_root through decision execution to integration.run"
```

---

### Task 6: Persist sandbox_root in admin org save/create

**Files:**
- Modify: `agency/app.py` (`admin_org_save` and `admin_org_create` handlers)
- Test: `tests/test_dashboard.py` (append) or new `tests/test_admin_org_sandbox.py`

**Interfaces:**
- Consumes: existing `load_config` / `save_config` / `reload_groups` pattern.
- Produces: POST form field `sandbox_root` is written to `config["groups"][org]["sandbox_root"]` when non-empty, and removed when empty.

- [ ] **Step 1: Write the failing test**

Create `tests/test_admin_org_sandbox.py`:

```python
import asyncio
from pathlib import Path

import agency.app as app_mod


class FakeForm(dict):
    def getlist(self, k):
        v = self.get(k, [])
        return v if isinstance(v, list) else [v]


class FakeRequest:
    def __init__(self, form):
        self._form = form

    async def form(self):
        return self._form


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_admin_org_save_persists_sandbox_root(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.yaml"
    monkeypatch.setattr(app_mod, "CONFIG_PATH", cfg_path)
    app_mod.save_config({
        "agency": {"title": "Agency", "default_group": "grp"},
        "groups": {"grp": {"name": "Grp", "path": str(tmp_path / "agents"), "agents": []}},
    })
    app_mod.reload_groups()

    form = FakeForm({
        "name": "Grp",
        "path": str(tmp_path / "agents"),
        "agents": "",
        "workspaces_json": "[]",
        "default_integration": "copilot",
        "sandbox_root": str(tmp_path / "repo"),
    })

    _run(app_mod.admin_org_save(FakeRequest(form), "grp"))

    saved = app_mod.load_config()
    assert saved["groups"]["grp"]["sandbox_root"] == str(tmp_path / "repo")


def test_admin_org_save_clears_sandbox_root_when_empty(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.yaml"
    monkeypatch.setattr(app_mod, "CONFIG_PATH", cfg_path)
    app_mod.save_config({
        "agency": {"title": "Agency", "default_group": "grp"},
        "groups": {"grp": {"name": "Grp", "path": str(tmp_path / "agents"),
                            "agents": [], "sandbox_root": "/old/root"}},
    })
    app_mod.reload_groups()

    form = FakeForm({
        "name": "Grp",
        "path": str(tmp_path / "agents"),
        "agents": "",
        "workspaces_json": "[]",
        "default_integration": "copilot",
        "sandbox_root": "",
    })

    _run(app_mod.admin_org_save(FakeRequest(form), "grp"))

    saved = app_mod.load_config()
    assert "sandbox_root" not in saved["groups"]["grp"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_admin_org_sandbox.py -v`
Expected: FAIL — saved config has no `sandbox_root` key (first test) / key not removed (second test).

- [ ] **Step 3: Implement the change in `admin_org_save`**

In `agency/app.py` `admin_org_save`, after the block that sets `default_integration`:

```python
    default_integration = form.get("default_integration", "claude-code")
    config["groups"][org]["default_integration"] = default_integration
```

add:

```python
    sandbox_root = form.get("sandbox_root", "").strip()
    if sandbox_root:
        config["groups"][org]["sandbox_root"] = sandbox_root
    else:
        config["groups"][org].pop("sandbox_root", None)
```

- [ ] **Step 4: Implement the change in `admin_org_create`**

In `agency/app.py` `admin_org_create`, locate where `group_cfg` is assembled:

```python
    group_cfg = {
        "name": name,
        "path": path,
        "agents": agents,
    }
    if ws_list:
        group_cfg["workspaces"] = ws_list
    config["groups"][key] = group_cfg
```

Insert, before `config["groups"][key] = group_cfg`:

```python
    sandbox_root = form.get("sandbox_root", "").strip()
    if sandbox_root:
        group_cfg["sandbox_root"] = sandbox_root
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_admin_org_sandbox.py -v`
Expected: PASS (2 passed)

- [ ] **Step 6: Commit**

```bash
git add agency/app.py tests/test_admin_org_sandbox.py
git commit -m "feat: persist sandbox_root in admin org save/create"
```

---

### Task 7: Admin org-edit UI — Sandbox root field

**Files:**
- Modify: `agency/app.py` (`admin_org_edit` GET context — add `sandbox_root` and `default_integration_supports_sandbox`)
- Modify: `agency/templates/admin_org_edit.html` (add field after the Path field, ~line 49)
- Test: manual (template render) — covered functionally by Task 6 for persistence.

**Interfaces:**
- Consumes: `REGISTRY` (already imported in `app.py`) to look up `supports_sandbox` for the group's default integration.
- Produces: template context keys `sandbox_root` (str) and `default_integration_supports_sandbox` (bool).

- [ ] **Step 1: Add context in `admin_org_edit`**

In `agency/app.py` `admin_org_edit`, in the `TemplateResponse` context dict (where `default_integration` is set), add:

```python
        "sandbox_root": g.get("sandbox_root", ""),
        "default_integration_supports_sandbox": REGISTRY.get(
            g.get("default_integration", "claude-code")
        ).supports_sandbox if REGISTRY.get(g.get("default_integration", "claude-code")) else False,
```

- [ ] **Step 2: Add the field to the template**

In `agency/templates/admin_org_edit.html`, immediately after the Path field block (the `<div>` ending after the "Filesystem path to the agents directory." paragraph), add:

```html
    <div>
      <label for="sandbox_root" class="block text-sm font-medium text-gray-700 mb-1">Sandbox root <span class="text-gray-400 font-normal">(optional)</span></label>
      <input type="text" name="sandbox_root" id="sandbox_root" value="{{ sandbox_root|default('') }}"
             placeholder="/path/to/repository/root"
             class="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm font-mono focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500">
      <p class="mt-1 text-xs text-gray-400">Directory the agent may read and write at runtime — e.g. the repository root. Leave empty to give agents full filesystem access. Does not affect the dashboard's file browsers.</p>
      {% if sandbox_root and default_integration_supports_sandbox is defined and not default_integration_supports_sandbox %}
      <p class="mt-1 text-xs text-amber-600">The selected default runtime does not support sandboxing — this setting will have no effect for it.</p>
      {% endif %}
    </div>
```

- [ ] **Step 3: Verify the app imports and template render without error**

Run: `python -c "import agency.app"`
Expected: no output, exit 0 (module imports cleanly).

Run: `python -m pytest tests/test_admin_org_sandbox.py -q`
Expected: PASS (persistence still green — the GET context change doesn't break saves).

- [ ] **Step 4: Manual smoke check (optional but recommended)**

Start the server and open an org edit page; confirm the "Sandbox root" field renders with any existing value and the helper text shows.

Run: `python -m agency.app` then visit `http://localhost:8500/admin/orgs/<org>/edit`
Expected: Sandbox root input visible below Path.

- [ ] **Step 5: Commit**

```bash
git add agency/app.py agency/templates/admin_org_edit.html
git commit -m "feat: add Sandbox root field to admin org edit UI"
```

---

### Task 8: Full suite + KB note

**Files:**
- Modify: `kb/configuration.md` (document `sandbox_root`)
- Modify: `CLAUDE.md` (Config Format section — add `sandbox_root` line)

**Interfaces:** none.

- [ ] **Step 1: Run the full test suite**

Run: `python -m pytest tests/ -q`
Expected: PASS (all tests green)

- [ ] **Step 2: Document in `kb/configuration.md`**

Add a subsection describing the field:

```markdown
### Sandbox root (`sandbox_root`)

Optional per-group filesystem scope for the agent **runtime** (not the dashboard).

- **Unset (default):** sandbox-capable runtimes launch in full-access mode — the
  agent can read/write anywhere the OS user can. For GitHub Copilot this means
  `--autopilot --allow-all-paths`.
- **Set:** the runtime is confined to the agent's own directory **plus** the
  `sandbox_root`. For Copilot this maps to `--autopilot --add-dir <sandbox_root>`.
  Use this to grant an agent nested in a larger repository access to the repo
  root (for shared memory, output folders, etc.) while still confining it.

Absolute paths are used as-is; relative paths resolve against the group `path`.

Only runtimes that support sandboxing honor this setting. Runtimes that do not
(shown with a warning in the admin UI) always run with their default access.

This setting does **not** change the Agency dashboard's file browsers, which
remain scoped to the group path.
```

- [ ] **Step 3: Document in `CLAUDE.md`**

In the "Config Format" section's example, add under a group's keys:

```yaml
    default_integration: claude-code  # Default integration for agents in this group
    sandbox_root: /path/to/repo/root  # Optional: runtime FS scope for sandbox-capable tools
```

- [ ] **Step 4: Commit**

```bash
git add kb/configuration.md CLAUDE.md
git commit -m "docs: document sandbox_root group config field"
```

---

## Self-Review

**Spec coverage:**

- Config schema (`sandbox_root`, absolute/relative resolution, unset → None) → Task 1. ✓
- `supports_sandbox` flag + `run()` param → Task 2. ✓
- Copilot invocation matrix (unset → `--allow-all-paths`; set → `--add-dir`) → Task 3. ✓
- Plumbing at both call sites (dispatch, decision execution) → Tasks 4-5. ✓
- Admin UI field + persistence + unsupported-runtime warning → Tasks 6-7. ✓
- Security (opt-in, web-UI boundary untouched) → enforced by design; no `get_allowed_roots` change in any task. ✓
- Behavior-change documentation (default becomes full-access for Copilot) → Task 8 KB note. ✓
- claude-code/codex confine flags → **explicitly out of scope** per Global Constraints (unverified flags); they keep `supports_sandbox = False`. ✓
- Missing-`sandbox_root`-directory fallback → the resolved `Path` is passed as-is; Copilot's `--add-dir` on a nonexistent dir is the CLI's concern. The spec's "warn + full access" fallback is **deferred** (not implemented here) since it requires runtime existence checks; noted as a follow-up rather than silently added. This keeps the change minimal and matches YAGNI. ✓

**Placeholder scan:** No "TBD"/"add error handling"/"similar to Task N". Every code step shows complete code. The one NOTE (Task 4 test) instructs matching real parameter names — the surrounding code and assertion are concrete. ✓

**Type consistency:** `get_sandbox_root(g: dict) -> Path | None` used consistently in Tasks 1, 4, 5. `run(..., *, sandbox_root: Path | None = None)` consistent across Tasks 2, 3, 4, 5. `supports_sandbox: bool` consistent across Tasks 2, 3, 7. ✓

## Notes / Deferred Items

- **claude-code and codex confine support** is intentionally not implemented. Their exact confine flags (`--permission-mode`/`--add-dir`; `--sandbox workspace-write` + writable roots) were not verifiable against installed CLIs at planning time. A follow-up plan should verify and wire them, then flip `supports_sandbox = True`.
- **Missing-directory fallback** ("warn + full access" when `sandbox_root` is set but does not exist) is deferred. Current behavior passes the path to the CLI regardless.
