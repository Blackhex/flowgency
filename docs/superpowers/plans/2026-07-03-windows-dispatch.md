# Windows Dispatch Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the dispatch timer install/uninstall/status work on Windows by registering a Task Scheduler task via `pywin32`.

**Architecture:** Add three private Windows helper functions to `agency/dispatch/install.py` that mirror the existing Linux (systemd) and macOS (launchd) helpers, and wire them into the three public dispatchers that currently return "not yet implemented" for Windows. The helpers use the Task Scheduler 2.0 COM API (`win32com.client.Dispatch("Schedule.Service")`) to register/query/delete a single task named `AgencyDispatch` that runs `pythonw -m agency.dispatch.run` every N minutes under the interactive logon token.

**Tech Stack:** Python 3.11+, `pywin32` (Windows-only), Task Scheduler 2.0 COM API, pytest with `unittest.mock`.

## Global Constraints

- `pywin32` is Windows-only: add as `"pywin32; sys_platform == 'win32'"` — never an unconditional dependency.
- `import win32com.client` MUST happen inside the Windows helper functions, never at module top level, so `install.py` stays importable on Linux/macOS/CI.
- Run mode: user-level, interactive logon token (`TASK_LOGON_INTERACTIVE_TOKEN = 3`). No stored credentials, no elevation, runs only while logged in.
- No PowerShell or CMD console window may appear on each scheduled run: the task action must invoke `pythonw.exe` directly (never via `cmd`/`powershell`).
- Task name is exactly `AgencyDispatch`, registered in the root folder (`\`).
- Registration uses `TASK_CREATE_OR_UPDATE = 6` (idempotent re-install).
- `get_timer_status()` must return a dict with exactly the keys `installed` and `timer_active`, matching the Linux/macOS helpers.
- Tests must not import real `pywin32`; inject mocks via `patch.dict(sys.modules, ...)`.
- Commit after each task.

---

### Task 1: Dependency, constant, and Python launcher helper

**Files:**
- Modify: `pyproject.toml` (dependencies list)
- Modify: `agency/dispatch/install.py` (add import, constant, helper)
- Test: `tests/test_dispatch_install.py`

**Interfaces:**
- Produces: module constant `WINDOWS_TASK_NAME = "AgencyDispatch"`; function `_windows_python_launcher() -> str` returning the path to `pythonw.exe` (preferred) or `sys.executable` (fallback).

- [ ] **Step 1: Add the platform-conditional dependency**

In `pyproject.toml`, change the `dependencies` list to add the `pywin32` marker entry:

```toml
dependencies = [
    "fastapi>=0.116",
    "starlette<1.0",
    "uvicorn[standard]",
    "jinja2",
    "markdown",
    "pyyaml",
    "markupsafe",
    "python-multipart",
    "pywin32; sys_platform == 'win32'",
]
```

- [ ] **Step 2: Add the datetime import and task-name constant**

In `agency/dispatch/install.py`, add `from datetime import datetime` to the imports and add the constant near the other module constants (after `LAUNCHD_PLIST = "com.agency.dispatch"`):

```python
from datetime import datetime
```

```python
WINDOWS_TASK_NAME = "AgencyDispatch"
```

- [ ] **Step 3: Write the failing test for the launcher helper**

Add to `tests/test_dispatch_install.py` (ensure `from unittest.mock import patch` and `import sys` are present at the top):

```python
def test_windows_python_launcher_prefers_pythonw(tmp_path):
    (tmp_path / "python.exe").write_text("")
    (tmp_path / "pythonw.exe").write_text("")
    with patch("agency.dispatch.install.sys.executable", str(tmp_path / "python.exe")):
        from agency.dispatch.install import _windows_python_launcher
        assert _windows_python_launcher() == str(tmp_path / "pythonw.exe")


def test_windows_python_launcher_falls_back_to_executable(tmp_path):
    (tmp_path / "python.exe").write_text("")
    with patch("agency.dispatch.install.sys.executable", str(tmp_path / "python.exe")):
        from agency.dispatch.install import _windows_python_launcher
        assert _windows_python_launcher() == str(tmp_path / "python.exe")
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `python -m pytest tests/test_dispatch_install.py -k launcher -v`
Expected: FAIL with `ImportError: cannot import name '_windows_python_launcher'`

- [ ] **Step 5: Implement the launcher helper**

In `agency/dispatch/install.py`, add after the `uninstall_timer` function (before the `# ── Linux` section):

```python
# ── Windows (Task Scheduler) ─────────────────────────────────────────────────


def _windows_python_launcher() -> str:
    """Return pythonw.exe (no console window) if present, else sys.executable."""
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    if pythonw.exists():
        return str(pythonw)
    return str(exe)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python -m pytest tests/test_dispatch_install.py -k launcher -v`
Expected: PASS (2 passed)

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml agency/dispatch/install.py tests/test_dispatch_install.py
git commit -m "feat(dispatch): add pywin32 dep and Windows python launcher helper"
```

---

### Task 2: Install the Windows task

**Files:**
- Modify: `agency/dispatch/install.py` (`install_timer` Windows branch + `_install_windows`)
- Test: `tests/test_dispatch_install.py`

**Interfaces:**
- Consumes: `WINDOWS_TASK_NAME`, `_windows_python_launcher()` from Task 1.
- Produces: `_install_windows(config_path: str, interval: int) -> str | None` (returns `None` on success, error string on failure). `install_timer` returns its result for Windows.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_dispatch_install.py` (ensure `from unittest.mock import patch, MagicMock` and `import sys` are present):

```python
def test_install_windows_registers_task():
    fake_client = MagicMock()
    scheduler = fake_client.Dispatch.return_value
    task_def = scheduler.NewTask.return_value
    folder = scheduler.GetFolder.return_value
    trigger = task_def.Triggers.Create.return_value
    action = task_def.Actions.Create.return_value

    with patch("platform.system", return_value="Windows"), \
         patch.dict(sys.modules, {"win32com": MagicMock(), "win32com.client": fake_client}):
        from agency.dispatch.install import install_timer
        err = install_timer(r"C:\cfg\config.yaml", 15)

    assert err is None
    scheduler.Connect.assert_called_once()
    task_def.Triggers.Create.assert_called_once_with(1)   # TASK_TRIGGER_TIME
    assert trigger.Repetition.Interval == "PT15M"
    task_def.Actions.Create.assert_called_once_with(0)    # TASK_ACTION_EXEC
    assert action.Arguments == '-m agency.dispatch.run --config "C:\\cfg\\config.yaml"'
    folder.RegisterTaskDefinition.assert_called_once()
    reg_args = folder.RegisterTaskDefinition.call_args.args
    assert reg_args[0] == "AgencyDispatch"
    assert reg_args[2] == 6   # TASK_CREATE_OR_UPDATE
    assert reg_args[5] == 3   # TASK_LOGON_INTERACTIVE_TOKEN


def test_install_windows_without_pywin32_returns_error():
    with patch("platform.system", return_value="Windows"), \
         patch.dict(sys.modules, {"win32com": None, "win32com.client": None}):
        from agency.dispatch.install import install_timer
        err = install_timer("cfg", 15)
    assert err is not None
    assert "pywin32" in err
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_dispatch_install.py -k install_windows -v`
Expected: FAIL — `install_timer` returns the "not yet implemented" string, so `assert err is None` fails.

- [ ] **Step 3: Implement `_install_windows`**

In `agency/dispatch/install.py`, add below `_windows_python_launcher` (still in the Windows section):

```python
def _install_windows(config_path: str, interval: int) -> str | None:
    """Register the AgencyDispatch Task Scheduler task."""
    try:
        import win32com.client
    except ImportError:
        return (
            "pywin32 is required for Windows dispatch. "
            "Install it with: pip install pywin32"
        )
    try:
        launcher = _windows_python_launcher()
        working_dir = str(Path(__file__).parent.parent.parent)

        scheduler = win32com.client.Dispatch("Schedule.Service")
        scheduler.Connect()
        folder = scheduler.GetFolder("\\")
        task_def = scheduler.NewTask(0)

        task_def.RegistrationInfo.Description = "Agency Agent Dispatch"
        task_def.RegistrationInfo.Author = "Agency"

        settings = task_def.Settings
        settings.Enabled = True
        settings.StartWhenAvailable = True

        trigger = task_def.Triggers.Create(1)  # TASK_TRIGGER_TIME
        trigger.StartBoundary = datetime.now().replace(microsecond=0).isoformat()
        trigger.Repetition.Interval = f"PT{interval}M"

        action = task_def.Actions.Create(0)  # TASK_ACTION_EXEC
        action.Path = launcher
        action.Arguments = f'-m agency.dispatch.run --config "{config_path}"'
        action.WorkingDirectory = working_dir

        folder.RegisterTaskDefinition(
            WINDOWS_TASK_NAME,
            task_def,
            6,      # TASK_CREATE_OR_UPDATE
            None,   # user (current)
            None,   # password
            3,      # TASK_LOGON_INTERACTIVE_TOKEN
        )
        return None
    except Exception as e:
        return str(e)
```

- [ ] **Step 4: Wire `install_timer` to call it**

In `agency/dispatch/install.py`, replace the Windows branch of `install_timer`:

```python
    else:
        return "Windows timer installation is not yet implemented. Please set up a Task Scheduler entry manually."
```

with:

```python
    else:
        return _install_windows(config_path, interval)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_dispatch_install.py -k install_windows -v`
Expected: PASS (2 passed)

- [ ] **Step 6: Commit**

```bash
git add agency/dispatch/install.py tests/test_dispatch_install.py
git commit -m "feat(dispatch): install Windows Task Scheduler task via pywin32"
```

---

### Task 3: Report Windows task status

**Files:**
- Modify: `agency/dispatch/install.py` (`get_timer_status` Windows branch + `_status_windows`)
- Test: `tests/test_dispatch_install.py`

**Interfaces:**
- Consumes: `WINDOWS_TASK_NAME` from Task 1.
- Produces: `_status_windows() -> dict` with keys `installed` and `timer_active`. `get_timer_status` returns it for Windows.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_dispatch_install.py`:

```python
def test_status_windows_installed_and_active():
    fake_client = MagicMock()
    scheduler = fake_client.Dispatch.return_value
    folder = scheduler.GetFolder.return_value
    task = folder.GetTask.return_value
    task.Enabled = True
    task.State = 3  # TASK_STATE_READY

    with patch("platform.system", return_value="Windows"), \
         patch.dict(sys.modules, {"win32com": MagicMock(), "win32com.client": fake_client}):
        from agency.dispatch.install import get_timer_status
        status = get_timer_status()

    assert status == {"installed": True, "timer_active": True}


def test_status_windows_not_installed():
    fake_client = MagicMock()
    scheduler = fake_client.Dispatch.return_value
    folder = scheduler.GetFolder.return_value
    folder.GetTask.side_effect = Exception("The system cannot find the file specified")

    with patch("platform.system", return_value="Windows"), \
         patch.dict(sys.modules, {"win32com": MagicMock(), "win32com.client": fake_client}):
        from agency.dispatch.install import get_timer_status
        status = get_timer_status()

    assert status == {"installed": False, "timer_active": False}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_dispatch_install.py -k status_windows -v`
Expected: FAIL — `get_timer_status` returns `{"installed": False, "timer_active": False}` unconditionally, so `test_status_windows_installed_and_active` fails.

- [ ] **Step 3: Implement `_status_windows`**

In `agency/dispatch/install.py`, add below `_install_windows`:

```python
def _status_windows() -> dict:
    """Report whether the AgencyDispatch task exists and is active."""
    try:
        import win32com.client
    except ImportError:
        return {"installed": False, "timer_active": False}
    try:
        scheduler = win32com.client.Dispatch("Schedule.Service")
        scheduler.Connect()
        folder = scheduler.GetFolder("\\")
        task = folder.GetTask(WINDOWS_TASK_NAME)
    except Exception:
        return {"installed": False, "timer_active": False}
    # TASK_STATE_READY = 3, TASK_STATE_RUNNING = 4
    timer_active = bool(task.Enabled) and task.State in (3, 4)
    return {"installed": True, "timer_active": timer_active}
```

- [ ] **Step 4: Wire `get_timer_status` to call it**

In `agency/dispatch/install.py`, replace the Windows branch of `get_timer_status`:

```python
    else:
        return {"installed": False, "timer_active": False}
```

with:

```python
    else:
        return _status_windows()
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_dispatch_install.py -k status_windows -v`
Expected: PASS (2 passed)

- [ ] **Step 6: Commit**

```bash
git add agency/dispatch/install.py tests/test_dispatch_install.py
git commit -m "feat(dispatch): report Windows Task Scheduler status via pywin32"
```

---

### Task 4: Uninstall the Windows task

**Files:**
- Modify: `agency/dispatch/install.py` (`uninstall_timer` Windows branch + `_uninstall_windows`)
- Test: `tests/test_dispatch_install.py`

**Interfaces:**
- Consumes: `WINDOWS_TASK_NAME` from Task 1.
- Produces: `_uninstall_windows() -> str | None` (returns `None` on success, including when the task is already absent). `uninstall_timer` returns it for Windows.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_dispatch_install.py`:

```python
def test_uninstall_windows_deletes_task():
    fake_client = MagicMock()
    scheduler = fake_client.Dispatch.return_value
    folder = scheduler.GetFolder.return_value

    with patch("platform.system", return_value="Windows"), \
         patch.dict(sys.modules, {"win32com": MagicMock(), "win32com.client": fake_client}):
        from agency.dispatch.install import uninstall_timer
        err = uninstall_timer()

    assert err is None
    folder.DeleteTask.assert_called_once_with("AgencyDispatch", 0)


def test_uninstall_windows_missing_task_is_success():
    fake_client = MagicMock()
    scheduler = fake_client.Dispatch.return_value
    folder = scheduler.GetFolder.return_value
    folder.DeleteTask.side_effect = Exception("The system cannot find the file specified")

    with patch("platform.system", return_value="Windows"), \
         patch.dict(sys.modules, {"win32com": MagicMock(), "win32com.client": fake_client}):
        from agency.dispatch.install import uninstall_timer
        err = uninstall_timer()

    assert err is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_dispatch_install.py -k uninstall_windows -v`
Expected: FAIL — `uninstall_timer` returns the "not yet implemented" string, so `assert err is None` fails.

- [ ] **Step 3: Implement `_uninstall_windows`**

In `agency/dispatch/install.py`, add below `_status_windows`:

```python
def _uninstall_windows() -> str | None:
    """Delete the AgencyDispatch task. Missing task is treated as success."""
    try:
        import win32com.client
    except ImportError:
        return None
    try:
        scheduler = win32com.client.Dispatch("Schedule.Service")
        scheduler.Connect()
        folder = scheduler.GetFolder("\\")
        folder.DeleteTask(WINDOWS_TASK_NAME, 0)
        return None
    except Exception:
        # Task not found (or already removed) — treat as success.
        return None
```

- [ ] **Step 4: Wire `uninstall_timer` to call it**

In `agency/dispatch/install.py`, replace the Windows branch of `uninstall_timer`:

```python
    else:
        return "Windows timer uninstallation is not yet implemented."
```

with:

```python
    else:
        return _uninstall_windows()
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_dispatch_install.py -k uninstall_windows -v`
Expected: PASS (2 passed)

- [ ] **Step 6: Run the full suite to confirm no regressions**

Run: `python -m pytest tests/ -q`
Expected: PASS (all tests, including the 8 new Windows tests)

- [ ] **Step 7: Commit**

```bash
git add agency/dispatch/install.py tests/test_dispatch_install.py
git commit -m "feat(dispatch): uninstall Windows Task Scheduler task via pywin32"
```

---

## Manual Verification (real Windows box, outside CI)

After the automated tasks pass, verify on an actual Windows machine (CI cannot exercise real COM):

1. `pip install pywin32` in the environment.
2. From the dashboard admin dispatch page (`/admin/dispatch/install`), click **Set Up Dispatch** (or call `install_timer(str(CONFIG_PATH), 15)`).
3. Open **Task Scheduler** → confirm an `AgencyDispatch` task exists in the root folder, triggering every 15 minutes, action `pythonw.exe -m agency.dispatch.run --config ...`.
4. Wait for one fire (or right-click → Run) and confirm **no CMD/PowerShell window appears** and dispatch logs are written.
5. Confirm the admin page now shows dispatch as installed/active (`get_timer_status()` returns `installed: True`).
6. Uninstall (`uninstall_timer()`) and confirm the task disappears from Task Scheduler.

## Self-Review Notes

- **Spec coverage:** install → Task 2; status → Task 3; uninstall → Task 4; dependency + import safety + launcher → Task 1; no-console requirement → `pythonw.exe` in Task 1/2; testing strategy → tests in every task. All spec sections covered.
- **Type consistency:** `WINDOWS_TASK_NAME`, `_windows_python_launcher`, `_install_windows`, `_status_windows`, `_uninstall_windows` used consistently across tasks; status dict keys `installed`/`timer_active` match Linux/macOS helpers.
- **No placeholders:** every code and test step contains complete, runnable content.
