# Singleton Dashboard Dispatch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace project-specific schedulers with one verified per-user Agency scheduler that drives every enabled group in the singleton dashboard config and reports schedule configuration separately from runtime health.

**Architecture:** `agency/dispatch/install.py` remains the only platform scheduler boundary and exposes a rich status dictionary plus guarded install/uninstall operations. The dashboard and a new `christag-agency dispatch` CLI consume that API, while Agency Setup writes rules into the singleton `config.yaml` and invokes the CLI instead of generating scheduler scripts. The existing Python heartbeat runner remains the sole scheduled execution path.

**Tech Stack:** Python 3.11+, pytest, FastAPI, Jinja2, PyYAML, Windows Task Scheduler COM (`pywin32`), user systemd, launchd/plistlib, PowerShell for the one-time Windows cutover

## Global Constraints

- Support exactly one Agency dashboard and one authoritative `config.yaml` per OS user; multiple dashboard configs for one user are unsupported.
- Use one Agency-managed scheduler per user: `AgencyDispatch` on Windows, `agency-dispatch.timer` plus `agency-dispatch.service` on Linux, or `com.agency.dispatch` on macOS.
- Keep all group schedules in the singleton config; do not add a config registry or standalone dispatch manifest.
- Do not add production discovery, compatibility, or migration behavior for project-specific superseded schedulers.
- Keep `agency.dispatch.interval` as desired configuration with a default of 15 minutes and an accepted range of 5 through 120 minutes.
- Treat runtime scheduler inspection as authoritative; ignore any persisted `agency.dispatch.installed` key.
- Use current-user, non-elevated scheduling and never store credentials or weaken PowerShell execution policy.
- Keep `at`, `every`, and `condition` rule semantics unchanged; `at` values use the scheduler host's local time.
- Label group configuration as **Schedule enabled** and reserve **Dispatcher active/inactive/misconfigured** for host runtime state.
- Perform the host cutover only from the authoritative checkout after code commits are integrated; never point the scheduler at a temporary worktree.

## File Structure

- `agency/dispatch/install.py`: canonical path comparison, shared `TimerStatus`, guarded replacement/removal, and complete platform definition inspection.
- `agency/config.py`: explicit-path atomic config writes for CLI use.
- `agency/cli.py`: `dispatch install`, `dispatch status`, and `dispatch uninstall` without import-time dashboard config dependence.
- `agency/app.py`: dashboard routes and contexts adapted to runtime status; interval repair delegated to the shared installer.
- `agency/templates/admin_dispatch.html`: active, inactive, and misconfigured dispatcher states with setup/repair actions.
- `agency/templates/admin_groups.html`, `agency/templates/admin_org_edit.html`, `agency/templates/agent_profile.html`: group schedule state independent of dispatcher health.
- `skills/agency-setup/SKILL.md`: config-native schedule registration and official scheduler CLI invocation.
- `skills/agency-setup/references/dispatch-templates.md`: prompt and interactive workspace templates only.
- `kb/dispatch.md`, `kb/configuration.md`, `kb/setup-skill.md`, `README.md`, `CLAUDE.md`: singleton ownership documentation.
- `tests/test_dispatch_install.py`: shared contract, platform matching, conflicts, idempotency, and removal.
- `tests/test_cli.py`: nested commands, explicit config, interval persistence, forwarding, and exit status.
- `tests/test_admin_dispatch.py`: dashboard state independence, copy, controls, and delegation.
- `tests/test_dispatch_run.py`: multi-group heartbeat and marker deduplication.
- `tests/test_agency_setup_skill.py`: no scheduler artifacts and required official CLI workflow.

---

### Task 1: Cross-Platform Scheduler Definition Contract

**Files:**
- Modify: `agency/dispatch/install.py`
- Modify: `tests/test_dispatch_install.py`

**Interfaces:**
- Consumes: `WINDOWS_TASK_NAME = "AgencyDispatch"` and `_windows_python_launcher() -> str`.
- Produces: `TimerStatus`; `get_timer_status(config_path: str | Path, interval: int = 15) -> TimerStatus`; `install_timer(config_path: str | Path, interval: int = 15, replace: bool = False) -> str | None`; `uninstall_timer(config_path: str | Path, force: bool = False) -> str | None`.
- `TimerStatus` keys: `state`, `installed`, `enabled`, `timer_active`, `definition_matches`, `config_conflict`, `config_path`, `interval`, `expected_config_path`, `expected_interval`, `mismatches`, and `error`.

- [ ] **Step 1: Write failing Windows definition and conflict tests**

Add `Path`, import the module as `dispatch_install`, update old no-argument status calls, and append these focused tests to `tests/test_dispatch_install.py`:

```python
from pathlib import Path

import agency.dispatch.install as dispatch_install


def _configure_windows_task(fake_client, config_path, interval=15, enabled=True, state=3):
    scheduler = fake_client.Dispatch.return_value
    task = scheduler.GetFolder.return_value.GetTask.return_value
    task.Enabled = enabled
    task.State = state
    action = task.Definition.Actions.Item.return_value
    action.Path = dispatch_install._windows_python_launcher()
    action.Arguments = f'-m agency.dispatch.run --config "{Path(config_path).resolve()}"'
    trigger = task.Definition.Triggers.Item.return_value
    trigger.Repetition.Interval = f"PT{interval}M"
    return task


def test_status_windows_matching_definition_is_active(tmp_path):
    fake_client = MagicMock()
    config_path = tmp_path / "config.yaml"
    _configure_windows_task(fake_client, config_path)
    with patch("platform.system", return_value="Windows"), patch.dict(
        sys.modules,
        {"win32com": MagicMock(), "win32com.client": fake_client},
    ):
        status = get_timer_status(config_path, 15)
    assert status["state"] == "active"
    assert status["installed"] is True
    assert status["enabled"] is True
    assert status["definition_matches"] is True
    assert status["config_conflict"] is False
    assert status["config_path"] == str(config_path.resolve())
    assert status["interval"] == 15
    assert status["mismatches"] == []
    assert status["error"] is None


def test_status_windows_reports_config_conflict(tmp_path):
    fake_client = MagicMock()
    expected = tmp_path / "expected.yaml"
    other = tmp_path / "other.yaml"
    _configure_windows_task(fake_client, other)
    with patch("platform.system", return_value="Windows"), patch.dict(
        sys.modules,
        {"win32com": MagicMock(), "win32com.client": fake_client},
    ):
        status = get_timer_status(expected, 15)
    assert status["state"] == "misconfigured"
    assert status["config_conflict"] is True
    assert status["config_path"] == str(other.resolve())
    assert status["mismatches"] == ["config_path"]


def test_status_windows_reports_action_and_interval_mismatches(tmp_path):
    fake_client = MagicMock()
    config_path = tmp_path / "config.yaml"
    task = _configure_windows_task(fake_client, config_path, interval=30)
    task.Definition.Actions.Item.return_value.Path = str(tmp_path / "wrong.exe")
    task.Definition.Actions.Item.return_value.Arguments = (
        f'-m wrong.module --config "{config_path.resolve()}"'
    )
    with patch("platform.system", return_value="Windows"), patch.dict(
        sys.modules,
        {"win32com": MagicMock(), "win32com.client": fake_client},
    ):
        status = get_timer_status(config_path, 15)
    assert status["state"] == "misconfigured"
    assert status["config_conflict"] is False
    assert status["mismatches"] == ["executable", "module", "interval"]


def test_status_windows_disabled_matching_task_is_inactive(tmp_path):
    fake_client = MagicMock()
    config_path = tmp_path / "config.yaml"
    _configure_windows_task(fake_client, config_path, enabled=False)
    with patch("platform.system", return_value="Windows"), patch.dict(
        sys.modules,
        {"win32com": MagicMock(), "win32com.client": fake_client},
    ):
        status = get_timer_status(config_path, 15)
    assert status["state"] == "inactive"
    assert status["definition_matches"] is True
    assert status["enabled"] is False
    assert status["timer_active"] is False


def test_install_refuses_another_config_without_replace(tmp_path, monkeypatch):
    requested = tmp_path / "requested.yaml"
    other = tmp_path / "other.yaml"
    status = dispatch_install._make_status(
        expected_config_path=requested,
        expected_interval=15,
        installed=True,
        enabled=True,
        timer_active=True,
        config_path=other,
        interval=15,
    )
    calls = []
    monkeypatch.setattr(dispatch_install, "get_timer_status", lambda path, interval: status)
    monkeypatch.setattr(dispatch_install, "_install_windows", lambda path, interval: calls.append((path, interval)))
    monkeypatch.setattr(dispatch_install, "detect_platform", lambda: "windows")
    error = install_timer(requested, 15)
    assert "another config" in error
    assert calls == []


def test_install_replace_overwrites_conflicting_global_task(tmp_path, monkeypatch):
    requested = tmp_path / "requested.yaml"
    other = tmp_path / "other.yaml"
    status = dispatch_install._make_status(
        expected_config_path=requested,
        expected_interval=15,
        installed=True,
        enabled=True,
        timer_active=True,
        config_path=other,
        interval=15,
    )
    calls = []
    monkeypatch.setattr(dispatch_install, "get_timer_status", lambda path, interval: status)
    monkeypatch.setattr(dispatch_install, "_install_windows", lambda path, interval: calls.append((path, interval)))
    monkeypatch.setattr(dispatch_install, "detect_platform", lambda: "windows")
    assert install_timer(requested, 15, replace=True) is None
    assert calls == [(str(requested.resolve()), 15)]


def test_install_repairs_same_config_without_replace(tmp_path, monkeypatch):
    requested = tmp_path / "requested.yaml"
    status = dispatch_install._make_status(
        expected_config_path=requested,
        expected_interval=15,
        installed=True,
        enabled=True,
        timer_active=True,
        config_path=requested,
        interval=30,
    )
    calls = []
    monkeypatch.setattr(dispatch_install, "get_timer_status", lambda path, interval: status)
    monkeypatch.setattr(dispatch_install, "_install_windows", lambda path, interval: calls.append((path, interval)))
    monkeypatch.setattr(dispatch_install, "detect_platform", lambda: "windows")
    assert install_timer(requested, 15) is None
    assert calls == [(str(requested.resolve()), 15)]


def test_install_repairs_installed_unreadable_definition(tmp_path, monkeypatch):
    requested = tmp_path / "requested.yaml"
    status = dispatch_install._make_status(
        expected_config_path=requested,
        expected_interval=15,
        installed=True,
        mismatches=["definition"],
        error="definition could not be parsed",
    )
    calls = []
    monkeypatch.setattr(dispatch_install, "get_timer_status", lambda path, interval: status)
    monkeypatch.setattr(dispatch_install, "_install_windows", lambda path, interval: calls.append((path, interval)))
    monkeypatch.setattr(dispatch_install, "detect_platform", lambda: "windows")
    assert install_timer(requested, 15) is None
    assert calls == [(str(requested.resolve()), 15)]


def test_uninstall_requires_force_for_another_config(tmp_path, monkeypatch):
    requested = tmp_path / "requested.yaml"
    other = tmp_path / "other.yaml"
    status = dispatch_install._make_status(
        expected_config_path=requested,
        expected_interval=15,
        installed=True,
        enabled=True,
        timer_active=True,
        config_path=other,
        interval=15,
    )
    calls = []
    monkeypatch.setattr(dispatch_install, "get_timer_status", lambda path, interval: status)
    monkeypatch.setattr(dispatch_install, "_uninstall_windows", lambda: calls.append(True))
    monkeypatch.setattr(dispatch_install, "detect_platform", lambda: "windows")
    assert "another config" in uninstall_timer(requested)
    assert calls == []
    assert uninstall_timer(requested, force=True) is None
    assert calls == [True]


def test_uninstall_force_removes_installed_unreadable_definition(tmp_path, monkeypatch):
    requested = tmp_path / "requested.yaml"
    status = dispatch_install._make_status(
        expected_config_path=requested,
        expected_interval=15,
        installed=True,
        mismatches=["definition"],
        error="definition could not be parsed",
    )
    calls = []
    monkeypatch.setattr(dispatch_install, "get_timer_status", lambda path, interval: status)
    monkeypatch.setattr(dispatch_install, "_uninstall_windows", lambda: calls.append(True))
    monkeypatch.setattr(dispatch_install, "detect_platform", lambda: "windows")
    assert "could not be parsed" in uninstall_timer(requested)
    assert uninstall_timer(requested, force=True) is None
    assert calls == [True]
```

- [ ] **Step 2: Run the focused tests and verify the old API fails**

Run:

```powershell
python -m pytest tests/test_dispatch_install.py -v -k "windows or another_config or conflicting_global or requires_force"
```

Expected: FAIL because status does not accept expected config/interval, rich fields are absent, and install/uninstall lack guards.

- [ ] **Step 3: Add the shared type, normalization helpers, and public guards**

Add `os`, `re`, `Literal`, and `TypedDict`, then replace the three public operations in `agency/dispatch/install.py`:

```python
import os
import re
from typing import Literal, TypedDict


TimerState = Literal["active", "inactive", "misconfigured"]


class TimerStatus(TypedDict):
    state: TimerState
    installed: bool
    enabled: bool
    timer_active: bool
    definition_matches: bool
    config_conflict: bool
    config_path: str | None
    interval: int | None
    expected_config_path: str
    expected_interval: int
    mismatches: list[str]
    error: str | None


def _canonical_config_path(config_path: str | Path) -> str:
    return str(Path(config_path).expanduser().resolve())


def _paths_equal(left: str | Path, right: str | Path) -> bool:
    return os.path.normcase(_canonical_config_path(left)) == os.path.normcase(
        _canonical_config_path(right)
    )


def _extract_config_path(arguments: str) -> str | None:
    match = re.search(r'(?:^|\s)--config\s+(?:"([^"]+)"|\'([^\']+)\'|(\S+))', arguments)
    if not match:
        return None
    value = next(group for group in match.groups() if group is not None)
    return _canonical_config_path(value)


def _parse_iso_minutes(value: str) -> int | None:
    match = re.fullmatch(r"PT(\d+)M", value or "")
    return int(match.group(1)) if match else None


def _make_status(
    *,
    expected_config_path: str | Path,
    expected_interval: int,
    installed: bool,
    enabled: bool = False,
    timer_active: bool = False,
    config_path: str | Path | None = None,
    interval: int | None = None,
    mismatches: list[str] | None = None,
    error: str | None = None,
) -> TimerStatus:
    expected_path = _canonical_config_path(expected_config_path)
    actual_path = _canonical_config_path(config_path) if config_path else None
    mismatch_list = list(mismatches or [])
    if installed and actual_path is None and "config_path" not in mismatch_list:
        mismatch_list.append("config_path")
    elif installed and actual_path and not _paths_equal(actual_path, expected_path):
        if "config_path" not in mismatch_list:
            mismatch_list.append("config_path")
    if installed and interval != expected_interval and "interval" not in mismatch_list:
        mismatch_list.append("interval")
    definition_matches = installed and not mismatch_list
    state: TimerState
    if installed and mismatch_list:
        state = "misconfigured"
    elif installed and enabled and timer_active:
        state = "active"
    else:
        state = "inactive"
    return {
        "state": state,
        "installed": installed,
        "enabled": enabled,
        "timer_active": timer_active,
        "definition_matches": definition_matches,
        "config_conflict": bool(installed and actual_path and not _paths_equal(actual_path, expected_path)),
        "config_path": actual_path,
        "interval": interval,
        "expected_config_path": expected_path,
        "expected_interval": expected_interval,
        "mismatches": mismatch_list,
        "error": error,
    }


def get_timer_status(config_path: str | Path, interval: int = 15) -> TimerStatus:
    platform_name = detect_platform()
    if platform_name == "linux":
        return _status_linux(config_path, interval)
    if platform_name == "macos":
        return _status_macos(config_path, interval)
    return _status_windows(config_path, interval)


def install_timer(config_path: str | Path, interval: int = 15, replace: bool = False) -> str | None:
    canonical_path = _canonical_config_path(config_path)
    status = get_timer_status(canonical_path, interval)
    if status["error"] and not status["installed"]:
        return status["error"]
    if status["config_conflict"] and not replace:
        return f"Agency dispatcher already targets another config: {status['config_path']}. Re-run with explicit replacement approval."
    platform_name = detect_platform()
    if platform_name == "linux":
        return _install_linux(canonical_path, interval)
    if platform_name == "macos":
        return _install_macos(canonical_path, interval)
    return _install_windows(canonical_path, interval)


def uninstall_timer(config_path: str | Path, force: bool = False) -> str | None:
    canonical_path = _canonical_config_path(config_path)
    status = get_timer_status(canonical_path)
    if status["error"] and (not status["installed"] or not force):
        return status["error"]
    if not status["installed"]:
        return None
    if status["config_conflict"] and not force:
        return f"Agency dispatcher targets another config: {status['config_path']}. Re-run with explicit force approval."
    platform_name = detect_platform()
    if platform_name == "linux":
        return _uninstall_linux()
    if platform_name == "macos":
        return _uninstall_macos()
    return _uninstall_windows()
```

- [ ] **Step 4: Inspect the Windows task definition**

Replace `_status_windows()` and canonicalize `_install_windows()` arguments:

```python
def _status_windows(config_path: str | Path, interval: int) -> TimerStatus:
    try:
        from win32com.client import Dispatch
    except ImportError:
        return _make_status(
            expected_config_path=config_path,
            expected_interval=interval,
            installed=False,
            error="pywin32 is required for Windows dispatch. Install it with: pip install pywin32",
        )
    try:
        scheduler = Dispatch("Schedule.Service")
        scheduler.Connect()
        folder = scheduler.GetFolder("\\")
    except Exception as error:
        return _make_status(
            expected_config_path=config_path,
            expected_interval=interval,
            installed=False,
            error=str(error),
        )
    try:
        task = folder.GetTask(WINDOWS_TASK_NAME)
    except Exception:
        return _make_status(
            expected_config_path=config_path,
            expected_interval=interval,
            installed=False,
        )
    mismatches: list[str] = []
    actual_config_path = None
    actual_interval = None
    try:
        action = task.Definition.Actions.Item(1)
        if not _paths_equal(action.Path, _windows_python_launcher()):
            mismatches.append("executable")
        arguments = str(action.Arguments or "")
        if "-m agency.dispatch.run" not in arguments:
            mismatches.append("module")
        actual_config_path = _extract_config_path(arguments)
    except Exception:
        mismatches.append("action")
    try:
        trigger = task.Definition.Triggers.Item(1)
        actual_interval = _parse_iso_minutes(str(trigger.Repetition.Interval or ""))
    except Exception:
        mismatches.append("trigger")
    enabled = bool(task.Enabled)
    timer_active = enabled and task.State in (3, 4)
    return _make_status(
        expected_config_path=config_path,
        expected_interval=interval,
        installed=True,
        enabled=enabled,
        timer_active=timer_active,
        config_path=actual_config_path,
        interval=actual_interval,
        mismatches=mismatches,
    )
```

Inside `_install_windows()`, set `canonical_path = _canonical_config_path(config_path)` and use:

```python
        action.Arguments = f'-m agency.dispatch.run --config "{canonical_path}"'
```

In the existing `test_install_windows_registers_task()`, make the preflight status
see an absent task before asserting registration:

```python
    folder.GetTask.side_effect = Exception("task not found")
```

Update every existing uninstall test to pass a concrete config path.

- [ ] **Step 5: Run the Windows/shared tests**

Run:

```powershell
python -m pytest tests/test_dispatch_install.py -v -k "windows or another_config or conflicting_global or requires_force"
```

Expected: PASS. Preserve the missing-pywin32 and task-not-found tests by updating them to the rich status contract.

#### Linux and macOS Definition Parity

**Files:**
- Modify: `agency/dispatch/install.py`
- Modify: `tests/test_dispatch_install.py`

**Interfaces:**
- Consumes: `TimerStatus`, `_make_status()`, `_canonical_config_path()`, and `_paths_equal()` implemented above in this task.
- Produces: `_status_linux(config_path, interval) -> TimerStatus`, `_status_macos(config_path, interval) -> TimerStatus`, and canonical idempotent systemd/launchd definitions.

- [ ] **Step 6: Add failing Linux and macOS definition tests**

Append these tests to `tests/test_dispatch_install.py`:

```python
def test_linux_status_validates_units_config_and_interval(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    python_path = tmp_path / "python3"
    python_path.write_text("", encoding="utf-8")
    monkeypatch.setattr(dispatch_install, "SYSTEMD_USER_DIR", tmp_path / "systemd")
    monkeypatch.setattr(dispatch_install, "_linux_python_launcher", lambda: python_path)
    monkeypatch.setattr(
        dispatch_install.subprocess,
        "run",
        lambda command, **kwargs: MagicMock(
            stdout="enabled\n" if "is-enabled" in command else "active\n"
        ),
    )
    assert dispatch_install._install_linux(str(config_path), 15) is None
    status = dispatch_install._status_linux(config_path, 15)
    assert status["state"] == "active"
    assert status["config_path"] == str(config_path.resolve())
    assert status["interval"] == 15
    assert status["mismatches"] == []


def test_linux_status_reports_wrong_config_and_interval(tmp_path, monkeypatch):
    expected = tmp_path / "expected.yaml"
    other = tmp_path / "other.yaml"
    python_path = tmp_path / "python3"
    unit_dir = tmp_path / "systemd"
    unit_dir.mkdir()
    (unit_dir / "agency-dispatch.service").write_text(
        f'ExecStart="{python_path}" -m agency.dispatch.run --config "{other.resolve()}"\n',
        encoding="utf-8",
    )
    (unit_dir / "agency-dispatch.timer").write_text(
        "[Timer]\nOnUnitActiveSec=30m\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(dispatch_install, "SYSTEMD_USER_DIR", unit_dir)
    monkeypatch.setattr(dispatch_install, "_linux_python_launcher", lambda: python_path)
    monkeypatch.setattr(
        dispatch_install.subprocess,
        "run",
        lambda command, **kwargs: MagicMock(
            stdout="enabled\n" if "is-enabled" in command else "active\n"
        ),
    )
    status = dispatch_install._status_linux(expected, 15)
    assert status["state"] == "misconfigured"
    assert status["config_conflict"] is True
    assert status["mismatches"] == ["config_path", "interval"]


def test_macos_status_validates_plist_config_and_interval(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    python_path = tmp_path / "python3"
    python_path.write_text("", encoding="utf-8")
    monkeypatch.setattr(dispatch_install, "LAUNCHD_AGENTS_DIR", tmp_path / "LaunchAgents")
    monkeypatch.setattr(dispatch_install, "_macos_python_launcher", lambda: python_path)
    monkeypatch.setattr(
        dispatch_install.subprocess,
        "run",
        lambda command, **kwargs: MagicMock(returncode=0),
    )
    assert dispatch_install._install_macos(str(config_path), 15) is None
    status = dispatch_install._status_macos(config_path, 15)
    assert status["state"] == "active"
    assert status["config_path"] == str(config_path.resolve())
    assert status["interval"] == 15
    assert status["mismatches"] == []


def test_macos_status_reports_wrong_module_and_interval(tmp_path, monkeypatch):
    import plistlib

    config_path = tmp_path / "config.yaml"
    python_path = tmp_path / "python3"
    launch_agents = tmp_path / "LaunchAgents"
    launch_agents.mkdir()
    with (launch_agents / "com.agency.dispatch.plist").open("wb") as plist_file:
        plistlib.dump(
            {
                "Label": "com.agency.dispatch",
                "ProgramArguments": [
                    str(python_path),
                    "-m",
                    "wrong.module",
                    "--config",
                    str(config_path.resolve()),
                ],
                "StartInterval": 1800,
                "RunAtLoad": True,
            },
            plist_file,
        )
    monkeypatch.setattr(dispatch_install, "LAUNCHD_AGENTS_DIR", launch_agents)
    monkeypatch.setattr(dispatch_install, "_macos_python_launcher", lambda: python_path)
    monkeypatch.setattr(
        dispatch_install.subprocess,
        "run",
        lambda command, **kwargs: MagicMock(returncode=0),
    )
    status = dispatch_install._status_macos(config_path, 15)
    assert status["state"] == "misconfigured"
    assert status["mismatches"] == ["module", "interval"]


def test_uninstall_linux_removes_both_units(tmp_path, monkeypatch):
    unit_dir = tmp_path / "systemd"
    unit_dir.mkdir()
    for name in ("agency-dispatch.timer", "agency-dispatch.service"):
        (unit_dir / name).write_text("unit", encoding="utf-8")
    calls = []
    monkeypatch.setattr(dispatch_install, "SYSTEMD_USER_DIR", unit_dir)
    monkeypatch.setattr(
        dispatch_install.subprocess,
        "run",
        lambda command, **kwargs: calls.append(command) or MagicMock(returncode=0),
    )
    assert dispatch_install._uninstall_linux() is None
    assert not (unit_dir / "agency-dispatch.timer").exists()
    assert not (unit_dir / "agency-dispatch.service").exists()
    assert ["systemctl", "--user", "daemon-reload"] in calls


def test_uninstall_macos_unloads_and_removes_plist(tmp_path, monkeypatch):
    launch_agents = tmp_path / "LaunchAgents"
    launch_agents.mkdir()
    plist_path = launch_agents / "com.agency.dispatch.plist"
    plist_path.write_text("plist", encoding="utf-8")
    calls = []
    monkeypatch.setattr(dispatch_install, "LAUNCHD_AGENTS_DIR", launch_agents)
    monkeypatch.setattr(
        dispatch_install.subprocess,
        "run",
        lambda command, **kwargs: calls.append(command) or MagicMock(returncode=0),
    )
    assert dispatch_install._uninstall_macos() is None
    assert not plist_path.exists()
    assert calls == [["launchctl", "unload", str(plist_path)]]
```

- [ ] **Step 7: Run the Unix backend tests and verify shallow status fails**

Run:

```powershell
python -m pytest tests/test_dispatch_install.py -v -k "linux_status or macos_status"
```

Expected: FAIL because the status helpers do not parse expected config, action, and interval.

- [ ] **Step 8: Implement structured systemd generation and inspection**

Add `shlex`, then replace Linux install/status with:

```python
import shlex


def _linux_python_launcher() -> Path:
    candidate = Path(__file__).parent.parent.parent / ".venv" / "bin" / "python3"
    return candidate if candidate.exists() else Path(sys.executable)


def _systemd_quote(value: str | Path) -> str:
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _install_linux(config_path: str, interval: int) -> str | None:
    try:
        launcher = _linux_python_launcher()
        canonical_path = _canonical_config_path(config_path)
        SYSTEMD_USER_DIR.mkdir(parents=True, exist_ok=True)
        (SYSTEMD_USER_DIR / "agency-dispatch.service").write_text(
            "[Unit]\nDescription=Agency Agent Dispatch\n\n"
            "[Service]\nType=oneshot\n"
            f"ExecStart={_systemd_quote(launcher)} -m agency.dispatch.run --config {_systemd_quote(canonical_path)}\n"
            f"Environment=PATH={_build_path_env()}\nEnvironment=HOME=%h\n",
            encoding="utf-8",
        )
        (SYSTEMD_USER_DIR / "agency-dispatch.timer").write_text(
            "[Unit]\nDescription=Agency Agent Dispatch Timer\n\n"
            f"[Timer]\nOnBootSec={interval}m\nOnUnitActiveSec={interval}m\nPersistent=true\n\n"
            "[Install]\nWantedBy=timers.target\n",
            encoding="utf-8",
        )
        subprocess.run(
            ["systemctl", "--user", "daemon-reload"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        subprocess.run(
            ["systemctl", "--user", "enable", "--now", "agency-dispatch.timer"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        return None
    except Exception as error:
        return str(error)


def _status_linux(config_path: str | Path, interval: int) -> TimerStatus:
    service_file = SYSTEMD_USER_DIR / "agency-dispatch.service"
    timer_file = SYSTEMD_USER_DIR / "agency-dispatch.timer"
    installed = service_file.exists() or timer_file.exists()
    if not installed:
        return _make_status(
            expected_config_path=config_path,
            expected_interval=interval,
            installed=False,
        )
    mismatches: list[str] = []
    actual_config_path = None
    actual_interval = None
    if not service_file.exists() or not timer_file.exists():
        mismatches.append("units")
    if service_file.exists():
        service_text = service_file.read_text(encoding="utf-8")
        exec_line = next(
            (line.removeprefix("ExecStart=") for line in service_text.splitlines() if line.startswith("ExecStart=")),
            "",
        )
        try:
            arguments = shlex.split(exec_line)
        except ValueError:
            arguments = []
        if not arguments or not _paths_equal(arguments[0], _linux_python_launcher()):
            mismatches.append("executable")
        if arguments[1:3] != ["-m", "agency.dispatch.run"]:
            mismatches.append("module")
        if "--config" in arguments:
            config_index = arguments.index("--config") + 1
            if config_index < len(arguments):
                actual_config_path = _canonical_config_path(arguments[config_index])
    if timer_file.exists():
        timer_text = timer_file.read_text(encoding="utf-8")
        match = re.search(r"^OnUnitActiveSec=(\d+)m$", timer_text, re.MULTILINE)
        actual_interval = int(match.group(1)) if match else None
    try:
        enabled_result = subprocess.run(
            ["systemctl", "--user", "is-enabled", "agency-dispatch.timer"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        active_result = subprocess.run(
            ["systemctl", "--user", "is-active", "agency-dispatch.timer"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        enabled = enabled_result.stdout.strip() == "enabled"
        active = active_result.stdout.strip() == "active"
        inspection_error = None
    except Exception as error:
        enabled = False
        active = False
        inspection_error = str(error)
    return _make_status(
        expected_config_path=config_path,
        expected_interval=interval,
        installed=True,
        enabled=enabled,
        timer_active=enabled and active,
        config_path=actual_config_path,
        interval=actual_interval,
        mismatches=mismatches,
        error=inspection_error,
    )
```

- [ ] **Step 9: Implement structured launchd generation and inspection**

Add `plistlib`, then replace macOS install/status with:

```python
import plistlib


def _macos_python_launcher() -> Path:
    candidate = Path(__file__).parent.parent.parent / ".venv" / "bin" / "python3"
    return candidate if candidate.exists() else Path(sys.executable)


def _install_macos(config_path: str, interval: int) -> str | None:
    try:
        canonical_path = _canonical_config_path(config_path)
        plist_path = LAUNCHD_AGENTS_DIR / f"{LAUNCHD_PLIST}.plist"
        LAUNCHD_AGENTS_DIR.mkdir(parents=True, exist_ok=True)
        if plist_path.exists():
            subprocess.run(
                ["launchctl", "unload", str(plist_path)],
                capture_output=True,
                text=True,
                timeout=10,
            )
        with plist_path.open("wb") as plist_file:
            plistlib.dump(
                {
                    "Label": LAUNCHD_PLIST,
                    "ProgramArguments": [
                        str(_macos_python_launcher()),
                        "-m",
                        "agency.dispatch.run",
                        "--config",
                        canonical_path,
                    ],
                    "StartInterval": interval * 60,
                    "RunAtLoad": True,
                },
                plist_file,
            )
        subprocess.run(
            ["launchctl", "load", str(plist_path)],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        return None
    except Exception as error:
        return str(error)


def _status_macos(config_path: str | Path, interval: int) -> TimerStatus:
    plist_path = LAUNCHD_AGENTS_DIR / f"{LAUNCHD_PLIST}.plist"
    if not plist_path.exists():
        return _make_status(
            expected_config_path=config_path,
            expected_interval=interval,
            installed=False,
        )
    mismatches: list[str] = []
    try:
        with plist_path.open("rb") as plist_file:
            definition = plistlib.load(plist_file)
        arguments = definition.get("ProgramArguments", [])
        if not arguments or not _paths_equal(arguments[0], _macos_python_launcher()):
            mismatches.append("executable")
        if arguments[1:3] != ["-m", "agency.dispatch.run"]:
            mismatches.append("module")
        actual_config_path = None
        if "--config" in arguments:
            config_index = arguments.index("--config") + 1
            if config_index < len(arguments):
                actual_config_path = _canonical_config_path(arguments[config_index])
        seconds = definition.get("StartInterval")
        actual_interval = seconds // 60 if isinstance(seconds, int) and seconds % 60 == 0 else None
    except (OSError, ValueError, plistlib.InvalidFileException) as error:
        return _make_status(
            expected_config_path=config_path,
            expected_interval=interval,
            installed=True,
            mismatches=["definition"],
            error=str(error),
        )
    try:
        result = subprocess.run(
            ["launchctl", "list", LAUNCHD_PLIST],
            capture_output=True,
            text=True,
            timeout=5,
        )
        active = result.returncode == 0
        inspection_error = None
    except Exception as error:
        active = False
        inspection_error = str(error)
    return _make_status(
        expected_config_path=config_path,
        expected_interval=interval,
        installed=True,
        enabled=active,
        timer_active=active,
        config_path=actual_config_path,
        interval=actual_interval,
        mismatches=mismatches,
        error=inspection_error,
    )
```

- [ ] **Step 10: Run and commit the complete cross-platform contract**

Run:

```powershell
python -m pytest tests/test_dispatch_install.py -v
```

Expected: PASS for Windows, Linux, macOS, conflicts, missing dependencies, and idempotent removal.

Commit:

```powershell
git add agency/dispatch/install.py tests/test_dispatch_install.py
git commit -m "feat(dispatch): validate global scheduler definitions"
```

---

### Task 2: Official Dispatch CLI and Explicit Atomic Config Writes

**Files:**
- Modify: `agency/config.py`
- Modify: `agency/cli.py`
- Modify: `tests/test_config_normalization.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Consumes: Scheduler API from Task 1.
- Produces: `save_config_path(path: Path, config: dict) -> None`; `cmd_dispatch(args: argparse.Namespace) -> int`; approved nested CLI.
- `dispatch status` exits: `0` active, `1` absent, `2` inactive, `3` misconfigured, `4` inspection or operation failure.

- [ ] **Step 1: Add failing atomic-save and CLI tests**

Append to `tests/test_config_normalization.py`:

```python
import os
import yaml

from agency.config import save_config_path


def test_save_config_path_atomically_replaces_destination(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    replacements = []
    real_replace = os.replace

    def recording_replace(source, destination):
        replacements.append((Path(source), Path(destination)))
        real_replace(source, destination)

    monkeypatch.setattr("agency.config.os.replace", recording_replace)
    save_config_path(
        config_path,
        {"agency": {"dispatch": {"interval": 30}}, "groups": {}},
    )
    assert replacements[0][1] == config_path
    saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert saved["agency"]["dispatch"]["interval"] == 30
```

Append to `tests/test_cli.py` and add `pytest`/`yaml` imports:

```python
def _dispatch_status(state="active", installed=True, error=None):
    return {
        "state": state,
        "installed": installed,
        "enabled": state == "active",
        "timer_active": state == "active",
        "definition_matches": installed and state != "misconfigured",
        "config_conflict": False,
        "config_path": None,
        "interval": 15 if installed else None,
        "expected_config_path": "C:/config.yaml",
        "expected_interval": 15,
        "mismatches": [] if state != "misconfigured" else ["interval"],
        "error": error,
    }


def test_cli_help_shows_dispatch_subcommands():
    result = subprocess.run(
        [sys.executable, "-m", "agency.cli", "dispatch", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert all(command in result.stdout for command in ("install", "status", "uninstall"))


def test_cmd_dispatch_install_persists_interval_and_forwards_replace(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("agency: {}\ngroups: {}\n", encoding="utf-8")
    calls = []
    monkeypatch.setattr(cli, "install_timer", lambda path, interval, replace=False: calls.append((path, interval, replace)))
    monkeypatch.setattr(cli, "get_timer_status", lambda path, interval: _dispatch_status())
    exit_code = cli.cmd_dispatch(
        Namespace(dispatch_command="install", config=str(config_path), interval=30, replace=True, force=False)
    )
    assert exit_code == 0
    assert calls == [(str(config_path.resolve()), 30, True)]
    saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert saved["agency"]["dispatch"] == {"interval": 30}


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (_dispatch_status(), 0),
        (_dispatch_status(state="inactive", installed=False), 1),
        (_dispatch_status(state="inactive", installed=True), 2),
        (_dispatch_status(state="misconfigured", installed=True), 3),
        (_dispatch_status(state="inactive", installed=False, error="unavailable"), 4),
    ],
)
def test_dispatch_status_exit_codes(tmp_path, monkeypatch, status, expected):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("agency:\n  dispatch:\n    interval: 15\ngroups: {}\n", encoding="utf-8")
    monkeypatch.setattr(cli, "get_timer_status", lambda path, interval: status)
    args = Namespace(dispatch_command="status", config=str(config_path), interval=None, replace=False, force=False)
    assert cli.cmd_dispatch(args) == expected


def test_cmd_dispatch_uninstall_forwards_force(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("agency: {}\ngroups: {}\n", encoding="utf-8")
    calls = []
    monkeypatch.setattr(cli, "uninstall_timer", lambda path, force=False: calls.append((path, force)))
    args = Namespace(dispatch_command="uninstall", config=str(config_path), interval=None, replace=False, force=True)
    assert cli.cmd_dispatch(args) == 0
    assert calls == [(str(config_path.resolve()), True)]
```

- [ ] **Step 2: Run the new tests and verify missing interfaces fail**

Run:

```powershell
python -m pytest tests/test_config_normalization.py tests/test_cli.py -v -k "save_config_path or dispatch"
```

Expected: FAIL because explicit-path saving and the dispatch CLI do not exist.

- [ ] **Step 3: Add explicit atomic config saving**

Add `os` and `tempfile` imports plus this function to `agency/config.py`:

```python
def save_config_path(path: Path, config: dict) -> None:
    """Atomically write an Agency config to an explicit path."""
    destination = path.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_path = tempfile.mkstemp(dir=destination.parent, suffix=".yaml")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as config_file:
            yaml.dump(config, config_file, default_flow_style=False, sort_keys=False)
        os.replace(temporary_path, destination)
    except Exception:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)
        raise
```

- [ ] **Step 4: Add command handlers and exact exit semantics**

Import `Namespace`, explicit config helpers, and scheduler functions in `agency/cli.py`, then add:

```python
def _dispatch_config_path(args: Namespace) -> Path:
    selected = Path(args.config).expanduser() if args.config else CONFIG_PATH
    config_path = selected.resolve()
    if not config_path.is_file():
        raise ValueError(f"Agency config not found: {config_path}")
    return config_path


def _dispatch_interval(config: dict) -> int:
    return int(config.get("agency", {}).get("dispatch", {}).get("interval", 15))


def _dispatch_status_exit_code(status: dict) -> int:
    if status["error"]:
        return 4
    if not status["installed"]:
        return 1
    if status["state"] == "inactive":
        return 2
    if status["state"] == "misconfigured":
        return 3
    return 0


def _print_dispatch_status(status: dict) -> None:
    if status["error"]:
        print(f"Dispatcher inspection failed: {status['error']}", file=sys.stderr)
    elif not status["installed"]:
        print("Dispatcher absent")
    elif status["state"] == "misconfigured":
        print("Dispatcher misconfigured: " + ", ".join(status["mismatches"]))
    elif status["state"] == "inactive":
        print("Dispatcher inactive")
    else:
        print(f"Dispatcher active: heartbeat every {status['expected_interval']} minutes")


def cmd_dispatch(args: Namespace) -> int:
    try:
        config_path = _dispatch_config_path(args)
    except ValueError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 4
    config = load_config_path(config_path)
    interval = args.interval if args.interval is not None else _dispatch_interval(config)
    if args.dispatch_command == "install":
        if args.interval is not None:
            dispatch_config = config.setdefault("agency", {}).setdefault("dispatch", {})
            dispatch_config.pop("installed", None)
            dispatch_config["interval"] = interval
            save_config_path(config_path, config)
        error = install_timer(config_path, interval, replace=args.replace)
        if error:
            print(f"Error: {error}", file=sys.stderr)
            return 4
        status = get_timer_status(config_path, interval)
        _print_dispatch_status(status)
        return _dispatch_status_exit_code(status)
    if args.dispatch_command == "status":
        status = get_timer_status(config_path, interval)
        _print_dispatch_status(status)
        return _dispatch_status_exit_code(status)
    error = uninstall_timer(config_path, force=args.force)
    if error:
        print(f"Error: {error}", file=sys.stderr)
        return 4
    print("Dispatcher removed")
    return 0
```

- [ ] **Step 5: Add the nested parser and bypass import-time config for dispatch**

Add this parser block in `main()`:

```python
    dispatch_parser = sub.add_parser("dispatch", help="Manage the global dispatcher")
    dispatch_sub = dispatch_parser.add_subparsers(dest="dispatch_command", required=True)
    install_parser = dispatch_sub.add_parser("install", help="Install or repair the dispatcher")
    install_parser.add_argument("--config")
    install_parser.add_argument("--interval", type=int, choices=range(5, 121))
    install_parser.add_argument("--replace", action="store_true")
    install_parser.set_defaults(force=False)
    status_parser = dispatch_sub.add_parser("status", help="Inspect the dispatcher")
    status_parser.add_argument("--config")
    status_parser.set_defaults(interval=None, replace=False, force=False)
    uninstall_parser = dispatch_sub.add_parser("uninstall", help="Remove the dispatcher")
    uninstall_parser.add_argument("--config")
    uninstall_parser.add_argument("--force", action="store_true")
    uninstall_parser.set_defaults(interval=None, replace=False)
```

Change the old guard to `if args.command not in ("serve", "dispatch")`, add `"dispatch": cmd_dispatch` to the handler map, and propagate nonzero integer results:

```python
    result = dispatch[args.command](args)
    if isinstance(result, int) and result:
        raise SystemExit(result)
```

- [ ] **Step 6: Run and commit config/CLI tests**

Run:

```powershell
python -m pytest tests/test_config_normalization.py tests/test_cli.py -v
```

Expected: PASS, including subprocess help and direct handler tests.

Commit:

```powershell
git add agency/config.py agency/cli.py tests/test_config_normalization.py tests/test_cli.py
git commit -m "feat(cli): manage singleton dispatcher"
```

---

### Task 3: Dashboard Runtime Status and Independent Schedule UI

**Files:**
- Create: `tests/test_admin_dispatch.py`
- Modify: `agency/app.py`
- Modify: `agency/templates/admin_dispatch.html`
- Modify: `agency/templates/admin_groups.html`
- Modify: `agency/templates/admin_org_edit.html`
- Modify: `agency/templates/agent_profile.html`

**Interfaces:**
- Consumes: Rich `TimerStatus` and guarded `install_timer()` from Task 1.
- Produces: `get_dispatch_status() -> TimerStatus`; `install_dispatch(interval: int | None = None, replace: bool = False) -> str | None`; platform-neutral UI with always-visible group schedules.

- [ ] **Step 1: Create failing dashboard regression tests**

Create `tests/test_admin_dispatch.py`:

```python
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

import agency.app as app_mod


def _status(state="inactive", installed=False, conflict=False, mismatches=None):
    return {
        "state": state,
        "installed": installed,
        "enabled": state == "active",
        "timer_active": state == "active",
        "definition_matches": installed and state != "misconfigured",
        "config_conflict": conflict,
        "config_path": "C:/other/config.yaml" if conflict else None,
        "interval": 15 if installed else None,
        "expected_config_path": "C:/agency/config.yaml",
        "expected_interval": 15,
        "mismatches": list(mismatches or []),
        "error": None,
    }


def _configure_admin(tmp_path: Path, monkeypatch, scheduler_status):
    group_path = tmp_path / "agents"
    (group_path / "shared" / "prompts").mkdir(parents=True)
    (group_path / "shared" / "prompts" / "routine.md").write_text("# Routine\n", encoding="utf-8")
    (group_path / "product").mkdir()
    config_path = tmp_path / "config.yaml"
    config = {
        "agency": {
            "title": "Agency",
            "default_group": "test",
            "dispatch": {"installed": True, "interval": 15},
        },
        "groups": {
            "test": {
                "name": "Test Agents",
                "path": str(group_path),
                "agents": ["product"],
                "dispatch": {
                    "enabled": True,
                    "timeout": 300,
                    "daily_limit": 15,
                    "agents": {"product": [{"prompt": "routine.md", "at": "07:00"}]},
                },
            },
        },
    }
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    monkeypatch.setattr(app_mod, "CONFIG_PATH", config_path)
    monkeypatch.setattr(app_mod, "_get_timer_status", lambda path, interval: scheduler_status)
    app_mod.reload_groups()
    return TestClient(app_mod.app)


def test_dispatch_status_ignores_persisted_installed_flag(tmp_path, monkeypatch):
    _configure_admin(tmp_path, monkeypatch, _status())
    status = app_mod.get_dispatch_status()
    assert status["installed"] is False
    assert status["state"] == "inactive"


def test_group_page_labels_config_as_schedule_enabled(tmp_path, monkeypatch):
    client = _configure_admin(tmp_path, monkeypatch, _status())
    response = client.get("/admin/groups")
    assert response.status_code == 200
    assert "Schedule enabled" in response.text
    assert "Dispatch on" not in response.text


def test_group_schedule_controls_remain_visible_when_dispatcher_inactive(tmp_path, monkeypatch):
    client = _configure_admin(tmp_path, monkeypatch, _status())
    response = client.get("/admin/orgs/test/edit")
    assert response.status_code == 200
    assert "Dispatch Schedule" in response.text
    assert "will not run until the global dispatcher is active" in response.text
    assert "Save Dispatch Config" in response.text


def test_dispatch_page_uses_platform_neutral_inactive_copy(tmp_path, monkeypatch):
    client = _configure_admin(tmp_path, monkeypatch, _status())
    response = client.get("/admin/dispatch")
    assert response.status_code == 200
    assert "Dispatcher inactive" in response.text
    assert "Set Up Dispatcher" in response.text
    assert "system scheduler" in response.text
    assert "systemd timer" not in response.text


def test_dispatch_page_shows_runtime_inspection_error(tmp_path, monkeypatch):
    failed_status = _status()
    failed_status["error"] = "Task Scheduler service is unavailable"
    client = _configure_admin(tmp_path, monkeypatch, failed_status)
    response = client.get("/admin/dispatch")
    assert response.status_code == 200
    assert "Dispatcher inactive" in response.text
    assert "Task Scheduler service is unavailable" in response.text


def test_dispatch_page_shows_guarded_conflict_repair(tmp_path, monkeypatch):
    client = _configure_admin(
        tmp_path,
        monkeypatch,
        _status(
            state="misconfigured",
            installed=True,
            conflict=True,
            mismatches=["config_path", "interval"],
        ),
    )
    response = client.get("/admin/dispatch")
    assert response.status_code == 200
    assert "Dispatcher misconfigured" in response.text
    assert "config_path" in response.text
    assert "interval" in response.text
    assert "Repair Dispatcher" in response.text
    assert 'name="replace" value="true"' in response.text


def test_dispatch_install_route_forwards_explicit_replacement(tmp_path, monkeypatch):
    client = _configure_admin(tmp_path, monkeypatch, _status())
    calls = []
    monkeypatch.setattr(
        app_mod,
        "install_dispatch",
        lambda interval=None, replace=False: calls.append((interval, replace)),
    )
    response = client.post(
        "/admin/dispatch/install",
        data={"replace": "true"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert calls == [(None, True)]


def test_interval_update_repairs_dispatcher_through_shared_api(tmp_path, monkeypatch):
    client = _configure_admin(tmp_path, monkeypatch, _status(state="active", installed=True))
    calls = []
    monkeypatch.setattr(
        app_mod,
        "install_timer",
        lambda path, interval, replace=False: calls.append((path, interval, replace)),
    )
    response = client.post(
        "/admin/settings",
        data={
            "title": "Agency",
            "default_group": "test",
            "ai_backend": "copilot",
            "theme": "",
            "dispatch_interval": "30",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert calls == [(str(app_mod.CONFIG_PATH.resolve()), 30, False)]
    saved = yaml.safe_load(app_mod.CONFIG_PATH.read_text(encoding="utf-8"))
    assert saved["agency"]["dispatch"]["interval"] == 30
    assert "installed" not in saved["agency"]["dispatch"]
```

- [ ] **Step 2: Run the dashboard tests and verify current behavior fails**

Run:

```powershell
python -m pytest tests/test_admin_dispatch.py -v
```

Expected: FAIL because the stale installed flag is merged, schedules are hidden, copy is systemd-specific, and replacement is not forwarded.

- [ ] **Step 3: Make dashboard status runtime-authoritative**

Replace the dispatch helpers in `agency/app.py`:

```python
def get_dispatch_status() -> dict:
    """Return runtime scheduler status for the active singleton config."""
    config = load_config()
    interval = config.get("agency", {}).get("dispatch", {}).get("interval", 15)
    return _get_timer_status(CONFIG_PATH.resolve(), interval)


def install_dispatch(interval: int | None = None, replace: bool = False) -> str | None:
    """Install or repair the scheduler for the active singleton config."""
    config = load_config()
    dispatch_config = config.setdefault("agency", {}).setdefault("dispatch", {})
    dispatch_config.pop("installed", None)
    desired_interval = interval if interval is not None else dispatch_config.get("interval", 15)
    if interval is not None:
        dispatch_config["interval"] = desired_interval
        save_config(config)
        reload_groups()
    return install_timer(str(CONFIG_PATH.resolve()), desired_interval, replace=replace)
```

In `admin_save_settings()`, remove direct `systemctl` calls. Persist the desired interval, remove `installed`, then repair any existing scheduler through `install_timer()`:

```python
    dispatch_interval = None
    if dispatch_interval_raw:
        try:
            candidate_interval = int(dispatch_interval_raw)
        except (ValueError, TypeError):
            candidate_interval = 0
        if 5 <= candidate_interval <= 120:
            dispatch_interval = candidate_interval
            dispatch_config = config.setdefault("agency", {}).setdefault("dispatch", {})
            dispatch_config.pop("installed", None)
            dispatch_config["interval"] = dispatch_interval

    save_config(config)
    reload_groups()
    dispatch_error = ""
    if dispatch_interval is not None:
        runtime_status = _get_timer_status(CONFIG_PATH.resolve(), dispatch_interval)
        if runtime_status["installed"]:
            dispatch_error = install_timer(
                str(CONFIG_PATH.resolve()),
                dispatch_interval,
                replace=False,
            ) or ""
    if dispatch_error:
        return templates.TemplateResponse(
            request,
            "admin_dispatch.html",
            {"request": request, **admin_context("dispatch", dispatch_error=dispatch_error)},
            status_code=409,
        )
```

Read and forward explicit replacement in the install route:

```python
@app.post("/admin/dispatch/install", response_class=HTMLResponse)
async def admin_dispatch_install(request: Request):
    """Install or repair the global platform scheduler."""
    form = await request.form()
    error = install_dispatch(replace=form.get("replace") == "true")
    if error:
        return templates.TemplateResponse(
            request,
            "admin_dispatch.html",
            {"request": request, **admin_context("dispatch", dispatch_error=error)},
            status_code=409,
        )
    return RedirectResponse("/admin/dispatch", status_code=303)
```

- [ ] **Step 4: Pass dispatcher activity to every group edit render**

In `admin_org_edit()`, the warning render in `admin_org_save()`, and `admin_org_autodetect()`, replace `dispatch_installed` with:

```python
        "dispatcher_active": get_dispatch_status()["state"] == "active",
```

Do not gate prompt or schedule data on that value.

- [ ] **Step 5: Render platform-neutral global status and guarded repair**

In `agency/templates/admin_dispatch.html`, stop branching the whole page on `dispatch.installed`. Use this status/action block above the retained interval and group sections:

```html
<div class="bg-white rounded-xl border border-gray-200 p-6 space-y-6">
  <div class="flex items-start justify-between gap-4">
    <div>
      {% if dispatch.state == 'active' %}
      <p class="text-sm font-medium text-gray-900">Dispatcher active</p>
      <p class="mt-2 text-sm text-gray-500">The user-level system scheduler checks all enabled groups every {{ dispatch.expected_interval }} minutes.</p>
      {% elif dispatch.state == 'misconfigured' %}
      <p class="text-sm font-medium text-gray-900">Dispatcher misconfigured</p>
      <p class="mt-2 text-sm text-gray-500">Mismatched fields: {{ dispatch.mismatches | join(', ') }}</p>
      {% else %}
      <p class="text-sm font-medium text-gray-900">Dispatcher inactive</p>
      <p class="mt-2 text-sm text-gray-500">Set up the user-level system scheduler to run enabled group schedules.</p>
      {% endif %}
    {% if dispatch.error %}
    <p class="mt-2 text-sm text-red-700">{{ dispatch.error }}</p>
    {% endif %}
    </div>
    {% if dispatch.state != 'active' %}
    <form method="post" action="/admin/dispatch/install"
          {% if dispatch.config_conflict %}onsubmit="return confirm('Replace the dispatcher for {{ dispatch.config_path }} with this dashboard config?')"{% endif %}>
      {% if dispatch.config_conflict %}<input type="hidden" name="replace" value="true">{% endif %}
      <button type="submit" class="px-4 py-2 bg-indigo-600 text-white text-sm font-medium rounded-lg hover:bg-indigo-700 transition-colors">
        {{ 'Set Up Dispatcher' if not dispatch.installed else 'Repair Dispatcher' }}
      </button>
    </form>
    {% endif %}
  </div>
</div>
```

Bind the interval input to `dispatch.expected_interval`. Keep interval and per-group status visible in all three states.

- [ ] **Step 6: Make every group-level label describe schedule configuration**

Apply these exact template changes:

```html
<!-- agency/templates/admin_groups.html -->
Schedule enabled

<!-- agency/templates/agent_profile.html -->
title="Schedule {{ 'enabled' if dispatch_enabled else 'disabled' }}"
No schedule configured
```

In `agency/templates/admin_org_edit.html`, replace the old installed-state condition with edit mode plus an amber warning:

```html
{% if mode == 'edit' %}
  {% if not dispatcher_active %}
  <div class="mb-4 p-3 bg-amber-50 border border-amber-200 rounded-lg text-sm text-amber-800">
    Enabled schedules will not run until the global dispatcher is active.
  </div>
  {% endif %}
```

Keep the existing schedule form and its final edit-mode `{% endif %}`.

- [ ] **Step 7: Run and commit dashboard tests**

Run:

```powershell
python -m pytest tests/test_admin_dispatch.py tests/test_server.py tests/test_agent_run.py -v
```

Expected: PASS with no direct platform command from interval updates and no schedule-form gate on persisted config.

Commit:

```powershell
git add agency/app.py agency/templates/admin_dispatch.html agency/templates/admin_groups.html agency/templates/admin_org_edit.html agency/templates/agent_profile.html tests/test_admin_dispatch.py
git commit -m "fix(dashboard): separate schedules from dispatcher health"
```

---

### Task 4: Agency Setup Uses the Singleton Scheduler

**Files:**
- Modify: `skills/agency-setup/SKILL.md`
- Modify: `skills/agency-setup/references/dispatch-templates.md`
- Modify: `tests/test_agency_setup_skill.py`

**Interfaces:**
- Consumes: `christag-agency dispatch install --config PATH` and `christag-agency dispatch status --config PATH` from Task 2.
- Produces: Config-native 07:00/21:00 rules, no generated scheduler artifacts, and optional verified singleton scheduling.

- [ ] **Step 1: Replace old template assertions with failing singleton assertions**

Replace `test_windows_templates_enumerate_real_copilot_executable()` and append:

```python
def test_setup_uses_official_singleton_scheduler_cli():
    skill = SKILL_PATH.read_text(encoding="utf-8")
    assert "christag-agency dispatch install --config" in skill
    assert "christag-agency dispatch status --config" in skill
    assert "exactly one Agency dashboard" in skill
    assert "does not create a fallback project scheduler" in skill


def test_setup_does_not_generate_project_scheduler_artifacts():
    combined = SKILL_PATH.read_text(encoding="utf-8") + DISPATCH_TEMPLATES_PATH.read_text(encoding="utf-8")
    forbidden = [
        "agents/shared/dispatch.ps1",
        "agents/shared/install-dispatch.ps1",
        "agents/shared/dispatch.sh",
        "## Windows Scheduled Task Installer Template",
        "## Systemd Timer Template",
        "## Systemd Service Template",
    ]
    for text in forbidden:
        assert text not in combined


def test_setup_writes_schedule_rules_directly_from_assignments():
    skill = SKILL_PATH.read_text(encoding="utf-8")
    registration = skill.split("### 4.7 Agency Registration", maxsplit=1)[1].split(
        "### 4.8 Singleton Scheduler Setup",
        maxsplit=1,
    )[0]
    assert 'at: "07:00"' in registration
    assert 'at: "21:00"' in registration
    assert "Phase 2 dispatch assignment" in registration
    assert "generated platform dispatch script" not in registration


def test_windows_launcher_still_resolves_real_copilot_executable():
    templates = DISPATCH_TEMPLATES_PATH.read_text(encoding="utf-8")
    launcher = templates.split("## Windows Terminal Launch Script Template", maxsplit=1)[1]
    assert "Get-Command copilot -All" in launcher
    assert "-ieq '.exe'" in launcher
    assert "-EncodedCommand" in launcher
    assert "Invoke-Expression" not in launcher
```

- [ ] **Step 2: Run skill tests and verify current generation fails**

Run:

```powershell
python -m pytest tests/test_agency_setup_skill.py -v
```

Expected: FAIL because the skill still generates per-project dispatchers and scheduler installers.

- [ ] **Step 3: Rewrite setup phases around config-native schedules**

In `skills/agency-setup/SKILL.md`, remove `dispatch` from the frontmatter
description's generated-directory list, change the Phase 4 reference description
to "prompts and interactive workspace launchers", and make runtime profile control
identity/workspace only:

```markdown
| Profile | Identity file | Agent command | Workspace |
|---------|---------------|---------------|-----------|
| Claude/Linux | `CLAUDE.md` | `claude --dangerously-skip-permissions` | tmux |
| Copilot/Windows | `AGENTS.md` | `copilot --autopilot --experimental` | Windows Terminal/PowerShell |
```

Replace Phase 4.4 with:

```markdown
### 4.4 Schedule Definitions

Do not generate a dispatcher, Task Scheduler installer, systemd unit, launchd
plist, or project-specific scheduler artifact. Agency's global 15-minute
heartbeat runs schedule rules stored in the singleton dashboard config.

Record each approved Phase 2 dispatch assignment for Phase 4.7:

- `morning` creates the routine rule at `"07:00"`.
- `evening` creates the routine rule at `"21:00"`.
- `morning, evening` creates both routine rules.
- A generated cleanup prompt creates an additional cleanup rule at `"21:00"`.
- All `at` values use the scheduler host's local time.

Use `dispatch.timeout: 300` and `dispatch.daily_limit: 15`. Marker
deduplication, logs, timeout enforcement, and job lifecycle belong to Agency's
Python dispatcher and job system.
```

At the start of Phase 4.7, add:

```markdown
Agency supports exactly one Agency dashboard and one authoritative `config.yaml`
per OS user. `$AGENCY_CONFIG` wins when valid. If more than one remaining valid
candidate exists, ask which config is authoritative; never register or schedule
all candidates.
```

Replace script-derived rules with direct Phase 2 assignment rules:

```yaml
dispatch:
  enabled: true
  timeout: 300
  daily_limit: 15
  agents:
    morning-agent:
    - prompt: morning-agent-routine.md
      at: "07:00"
    cleanup-agent:
    - prompt: cleanup-agent-routine.md
      at: "21:00"
    - prompt: cleanup-agent-cleanup.md
      at: "21:00"
```

Require assignment order preservation and de-duplication of identical prompt/time pairs.

- [ ] **Step 4: Replace platform scheduler setup with the official CLI**

Replace Phase 4.8 with:

```markdown
### 4.8 Singleton Scheduler Setup

Only offer scheduler setup after registration and on-disk verification succeed.
If no authoritative Agency config was found, report that registration and
scheduling were not completed and do not create a fallback project scheduler.

Ask: "Enable the global Agency dispatcher? It checks all enabled groups every 15
minutes. (Y/n)"

If yes:

1. Resolve the selected config to a canonical absolute path.
2. Run `christag-agency dispatch install --config "{config_path}"` as the current user.
3. Run `christag-agency dispatch status --config "{config_path}"`.
4. Treat only exit status 0 as verified active scheduling.
5. If install reports another config, ask before rerunning with `--replace`.
6. Never request credentials, elevation, or a weaker execution policy.
7. If the CLI is unavailable, report the exact command to run after Agency is
   installed; do not generate another scheduler implementation.
```

Update Phase 5 to report global dispatcher status instead of a generated timer.

- [ ] **Step 5: Remove scheduler templates and preserve prompt/workspace templates**

Delete these complete headings and bodies from `skills/agency-setup/references/dispatch-templates.md`:

```text
## dispatch.sh Template
## PowerShell Dispatch Template
## Windows Scheduled Task Installer Template
## Systemd Timer Template
## Systemd Service Template
```

Retain the prompt, cleanup, coordinator, tmux launcher, and Windows Terminal
launcher sections. Retain the coordinator prompt's existing direct feedback orchestration examples;
they run inside a due job and are not scheduler infrastructure. Preserve absolute
`copilot.exe` resolution in both the coordinator example and Windows launcher.

- [ ] **Step 6: Run and commit skill tests**

Run:

```powershell
python -m pytest tests/test_agency_setup_skill.py -v
```

Expected: PASS; `.github/skills/agency-setup` still resolves to the canonical skill.

Commit:

```powershell
git add skills/agency-setup/SKILL.md skills/agency-setup/references/dispatch-templates.md tests/test_agency_setup_skill.py
git commit -m "fix(agency-setup): use global dispatcher"
```

---

### Task 5: Multi-Group Runner Proof and Documentation

**Files:**
- Modify: `tests/test_dispatch_run.py`
- Modify: `kb/dispatch.md`
- Modify: `kb/configuration.md`
- Modify: `kb/setup-skill.md`
- Modify: `README.md`
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: `run_dispatch_cycle(config, config_path, launcher=None)` and all behavior from Tasks 1-4.
- Produces: Proof that one heartbeat handles multiple enabled groups without duplicate events; user and maintainer documentation matching singleton ownership.

- [ ] **Step 1: Add multi-group and duplicate-event regression tests**

Append to `tests/test_dispatch_run.py`:

```python
def test_one_heartbeat_submits_due_work_for_multiple_enabled_groups(tmp_path, monkeypatch):
        first_path, _, _ = _make_group(tmp_path / "first")
        second_path, _, _ = _make_group(tmp_path / "second")
        config = {
                "agency": {"dispatch": {"interval": 15}},
                "groups": {
                        "first": _enabled_config(first_path)["groups"]["test"],
                        "second": _enabled_config(second_path)["groups"]["test"],
                },
        }
        submitted = []
        monkeypatch.setattr(
                "agency.dispatch.run.submit_job",
                lambda spec, launcher=None: submitted.append((spec.group_key, spec.agent_name)),
        )
        run_dispatch_cycle(config, tmp_path / "config.yaml")
        assert submitted == [("first", "product"), ("second", "product")]


def test_repeated_heartbeat_does_not_duplicate_daily_at_rule(tmp_path, monkeypatch):
        group_path, _, _ = _make_group(tmp_path)
        config = _enabled_config(group_path)
        config["groups"]["test"]["dispatch"]["agents"]["product"] = [
                {"prompt": "routine.md", "at": datetime.now().strftime("%H:%M")},
        ]
        submitted = []
        monkeypatch.setattr(
                "agency.dispatch.run.submit_job",
                lambda spec, launcher=None: submitted.append(spec),
        )
        run_dispatch_cycle(config, tmp_path / "config.yaml")
        run_dispatch_cycle(config, tmp_path / "config.yaml")
        assert len(submitted) == 1


def test_disabled_group_is_skipped_in_multi_group_config(tmp_path, monkeypatch):
        enabled_path, _, _ = _make_group(tmp_path / "enabled")
        disabled_path, _, _ = _make_group(tmp_path / "disabled")
        disabled_group = _enabled_config(disabled_path)["groups"]["test"]
        disabled_group["dispatch"]["enabled"] = False
        config = {
                "agency": {"dispatch": {"interval": 15}},
                "groups": {
                        "enabled": _enabled_config(enabled_path)["groups"]["test"],
                        "disabled": disabled_group,
                },
        }
        submitted = []
        monkeypatch.setattr(
                "agency.dispatch.run.submit_job",
                lambda spec, launcher=None: submitted.append(spec.group_key),
        )
        run_dispatch_cycle(config, tmp_path / "config.yaml")
        assert submitted == ["enabled"]
```

The current `_make_group()` already uses `parents=True`; retain that behavior so nested test roots work.

- [ ] **Step 2: Run runner tests before changing production code**

Run:

```powershell
python -m pytest tests/test_dispatch_run.py -v
```

Expected: PASS because the existing runner already iterates enabled groups and
touches per-rule markers after successful submission. An unexpected failure
blocks this plan and must be investigated before changing runner behavior.

- [ ] **Step 3: Rewrite dispatch and configuration guides**

Update `kb/dispatch.md` with these exact facts and commands:

```markdown
- Agency supports one dashboard config and one user-level dispatcher per OS user.
- `dispatch.enabled: true` means a group's schedule is configured; it does not
    prove the host dispatcher is active.
- The global dispatcher checks every enabled group every 15 minutes by default.

    ```text
    christag-agency dispatch install --config C:\path\to\config.yaml
    christag-agency dispatch status --config C:\path\to\config.yaml
    christag-agency dispatch uninstall --config C:\path\to\config.yaml
    ```

- Windows uses `AgencyDispatch`, Linux uses `agency-dispatch.timer` and
    `agency-dispatch.service`, and macOS uses `com.agency.dispatch`.
- Condition rules remain skipped by the Python heartbeat. External event
    automation may submit corresponding work, but it is outside Agency's managed
    scheduler and must not create another Agency dispatcher.
```

Remove the Windows "not yet automated" statement, direct sequential integration execution claims, project-specific timer guidance, and `agency.dispatch.installed` from examples.

In `kb/configuration.md`, replace the persisted installed row with:

```markdown
| `dispatch.interval` | `15` | Desired global heartbeat interval in minutes (5-120) |
```

State that installed/active state is inspected from the OS scheduler and is never authoritative YAML data.

- [ ] **Step 4: Update setup and repository overview documentation**

In `kb/setup-skill.md`, change each profile's Dispatch value to `Agency global dispatcher`, replace generated dispatcher/installer claims with config-native schedules and official CLI verification, and remove generated-script safety claims.

Replace the Agency Setup overview paragraph in `README.md` with:

```markdown
It analyzes your project, proposes a tailored agent team, generates identities,
memory, shared prompts, and an interactive workspace, then atomically registers
the group and its schedules with the singleton Agency dashboard. With approval,
it verifies Agency's one global user-level dispatcher; it never creates a
project-specific scheduler.
```

Update `CLAUDE.md` architecture, config, route-helper, platform support, and setup sections to state:

```markdown
- one authoritative `config.yaml` and one global platform scheduler per user;
- `agency.dispatch.interval` is desired configuration and no
    `agency.dispatch.installed` key is authoritative;
- `agency/dispatch/install.py` supports systemd, launchd, and Windows Task
    Scheduler and validates the complete definition;
- the CLI exposes `dispatch install|status|uninstall`;
- Agency Setup writes rules to config and never creates scheduler scripts or
    units.
```

Keep `/admin/dispatch/install` documented as the dashboard setup/repair endpoint.

- [ ] **Step 5: Search for forbidden stale claims**

Run:

```powershell
$stale = rg -n "installed:\s*true|Set after first dispatch init|Windows:.*Not yet automated|agents/shared/(dispatch|install-dispatch)|runs independently via its own timer|using a systemd timer" README.md CLAUDE.md kb skills/agency-setup
if ($LASTEXITCODE -eq 0) { $stale; throw 'Stale singleton-dispatch documentation remains.' }
if ($LASTEXITCODE -ne 1) { throw "rg failed with exit code $LASTEXITCODE" }
```

Expected: no matches and no thrown error.

- [ ] **Step 6: Run focused and full verification**

Run:

```powershell
python -m pytest tests/test_dispatch_run.py tests/test_agency_setup_skill.py -v
python -m pytest tests/ -q
```

Expected: both commands PASS with zero failures.

- [ ] **Step 7: Commit runner proof and documentation**

```powershell
git add tests/test_dispatch_run.py kb/dispatch.md kb/configuration.md kb/setup-skill.md README.md CLAUDE.md
git commit -m "docs(dispatch): document singleton scheduler"
```

---

### Task 6: Verify UI and Replace the Local Windows Scheduler

**Files:**
- Delete ignored local file: `agents/shared/dispatch.ps1`
- Delete ignored local file: `agents/shared/install-dispatch.ps1`
- Modify if `--interval 15` adds the desired key: `config.yaml`
- No production source files

**Interfaces:**
- Consumes: Fully tested CLI, dashboard, setup, and scheduler implementation from Tasks 1-5.
- Produces: One active local `AgencyDispatch` pointing at the authoritative checkout; no `christag-agency-dispatch` or obsolete generated scheduler scripts.

- [ ] **Step 1: Confirm a clean authoritative checkout**

Run from the permanent repository root:

```powershell
$root = (Get-Location).Path
$gitRoot = (git rev-parse --show-toplevel).Trim()
if ($root -ne $gitRoot) { throw "Run cutover from repository root: $gitRoot" }
if ($gitRoot -match '[\\/]\.worktrees[\\/]') { throw 'Do not schedule an isolated worktree.' }
if (git status --porcelain) { throw 'Commit or resolve working-tree changes before cutover.' }
$configPath = (Resolve-Path .\config.yaml).Path
if (Get-ScheduledTask -TaskName 'AgencyDispatch' -ErrorAction SilentlyContinue) {
    python -m agency.cli dispatch status --config $configPath
    throw 'AgencyDispatch already exists. Resolve its config ownership before cutover.'
}
Write-Output "AUTHORITATIVE_CONFIG=$configPath"
```

Expected: the printed path is the permanent `christag-agency\config.yaml` and
there is no pre-existing global task whose ownership could be overwritten.

- [ ] **Step 2: Capture and disable the superseded task without deleting it**

Run:

```powershell
$supersededTask = Get-ScheduledTask -TaskName 'christag-agency-dispatch' -ErrorAction Stop
$supersededInfo = Get-ScheduledTaskInfo -TaskName 'christag-agency-dispatch'
$supersededTask | Select-Object TaskName, State | Format-Table
$supersededInfo | Select-Object LastRunTime, LastTaskResult, NextRunTime | Format-List
Disable-ScheduledTask -TaskName 'christag-agency-dispatch' | Out-Null
if ((Get-ScheduledTask -TaskName 'christag-agency-dispatch').State -ne 'Disabled') {
    throw 'superseded scheduler did not enter Disabled state.'
}
```

Expected: the definition remains available for rollback but cannot fire.

- [ ] **Step 3: Install and verify the global scheduler with rollback**

Run:

```powershell
python -m agency.cli dispatch install --config $configPath --interval 15
if ($LASTEXITCODE -ne 0) {
    python -m agency.cli dispatch uninstall --config $configPath --force
    Enable-ScheduledTask -TaskName 'christag-agency-dispatch' | Out-Null
    throw 'Global installation failed; superseded scheduler re-enabled.'
}
python -m agency.cli dispatch status --config $configPath
if ($LASTEXITCODE -ne 0) {
    python -m agency.cli dispatch uninstall --config $configPath --force
    Enable-ScheduledTask -TaskName 'christag-agency-dispatch' | Out-Null
    throw 'Global verification failed; superseded scheduler re-enabled.'
}
$globalTask = Get-ScheduledTask -TaskName 'AgencyDispatch' -ErrorAction Stop
$globalInfo = Get-ScheduledTaskInfo -TaskName 'AgencyDispatch'
[pscustomobject]@{
    TaskName = $globalTask.TaskName
    State = $globalTask.State
    Enabled = $globalTask.Settings.Enabled
    Execute = ($globalTask.Actions | Select-Object -First 1).Execute
    Arguments = ($globalTask.Actions | Select-Object -First 1).Arguments
    NextRunTime = $globalInfo.NextRunTime
} | Format-List
```

Expected: `AgencyDispatch` is enabled/ready, its action contains the canonical config path, and its next run is within 15 minutes.

- [ ] **Step 4: Trigger one heartbeat and verify completion without blind sleeping**

Run:

```powershell
$beforeJobs = @(
    Get-ChildItem .\agents\shared\jobs -Filter '*.yaml' -File -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty FullName
)
$before = (Get-ScheduledTaskInfo -TaskName 'AgencyDispatch').LastRunTime
$scheduler = New-Object -ComObject 'Schedule.Service'
$scheduler.Connect()
$registeredTask = $scheduler.GetFolder('\').GetTask('AgencyDispatch')
$runningTask = $registeredTask.Run($null)
if ($runningTask.EnginePID) {
    Wait-Process -Id $runningTask.EnginePID -Timeout 30 -ErrorAction SilentlyContinue
}
$info = Get-ScheduledTaskInfo -TaskName 'AgencyDispatch'
if ($info.LastRunTime -le $before -or $info.LastTaskResult -ne 0) {
    python -m agency.cli dispatch uninstall --config $configPath --force
    Enable-ScheduledTask -TaskName 'christag-agency-dispatch' | Out-Null
    throw "Heartbeat failed with result $($info.LastTaskResult); superseded scheduler re-enabled."
}
$info | Select-Object LastRunTime, LastTaskResult, NextRunTime | Format-List
$afterJobs = @(
  Get-ChildItem .\agents\shared\jobs -Filter '*.yaml' -File -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty FullName
)
$newJobs = @($afterJobs | Where-Object { $_ -notin $beforeJobs })
$env:AGENCY_NEW_JOBS = $newJobs -join [System.IO.Path]::PathSeparator
@'
import os
from pathlib import Path

import yaml

paths = [Path(value) for value in os.environ.get("AGENCY_NEW_JOBS", "").split(os.pathsep) if value]
identities = []
for path in paths:
    record = yaml.safe_load(path.read_text(encoding="utf-8"))
    spec = record["spec"]
    identities.append(
        (
            spec["group_key"],
            spec["agent_name"],
            spec["prompt_source"].get("path"),
        )
    )
if len(identities) != len(set(identities)):
    raise SystemExit(f"Duplicate scheduled submissions: {identities}")
print(f"Verified {len(identities)} unique scheduled submission(s).")
'@ | python -
if ($LASTEXITCODE -ne 0) {
  python -m agency.cli dispatch uninstall --config $configPath --force
  Enable-ScheduledTask -TaskName 'christag-agency-dispatch' | Out-Null
  throw 'Duplicate-submission verification failed; superseded scheduler re-enabled.'
}
```

Expected: `LastRunTime` advances, `LastTaskResult` is `0`, and every new
scheduled `(group, agent, prompt)` identity occurs at most once.

- [ ] **Step 5: Remove the disabled task and obsolete ignored files**

Run:

```powershell
Unregister-ScheduledTask -TaskName 'christag-agency-dispatch' -Confirm:$false
if (Get-ScheduledTask -TaskName 'christag-agency-dispatch' -ErrorAction SilentlyContinue) {
    throw 'superseded task still exists.'
}
```

Delete the two generated files with `apply_patch`:

```text
*** Delete File: agents/shared/dispatch.ps1
*** Delete File: agents/shared/install-dispatch.ps1
```

Do not delete `agents/shared/start-agents.ps1`; it is the independent interactive launcher.

- [ ] **Step 6: Commit desired interval configuration only if changed**

Run:

```powershell
git diff -- config.yaml
if (git status --porcelain -- config.yaml) {
    git add config.yaml
    git commit -m "chore(dispatch): configure global heartbeat"
}
```

Expected: the only possible config change is `agency.dispatch.interval: 15`; no `installed` key is added.

- [ ] **Step 7: Start the dashboard and verify desktop/mobile UI with Playwright**

Run the existing **Serve dashboard (hot-reload)** VS Code task. At `http://127.0.0.1:8500`, capture 1280x800 and 390x844 screenshots and verify:

```text
/admin/groups
- Enabled groups display "Schedule enabled".
- No group displays "Dispatch on".

/admin/dispatch
- Displays "Dispatcher active" and a 15-minute heartbeat.
- Does not display setup/repair buttons or "systemd timer".

/admin/orgs/agents/edit
- Dispatch Schedule controls are visible.
- No inactive-dispatcher warning is visible.
- Controls, labels, and buttons do not overlap at either viewport.
```

Expected: screenshots and browser snapshots satisfy every assertion; browser console contains no new error.

- [ ] **Step 8: Run final automated and host-state verification**

Run:

```powershell
python -m pytest tests/ -q
$agencyTasks = Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object {
    $_.TaskName -in @('AgencyDispatch', 'christag-agency-dispatch')
}
if (@($agencyTasks).Count -ne 1 -or $agencyTasks[0].TaskName -ne 'AgencyDispatch') {
    $agencyTasks | Select-Object TaskName, State | Format-Table
    throw 'Expected exactly one AgencyDispatch task.'
}
python -m agency.cli dispatch status --config (Resolve-Path .\config.yaml).Path
if ($LASTEXITCODE -ne 0) { throw 'Final dispatcher status is not active.' }
git status --short
```

Expected: full tests pass, exactly one global task remains, CLI status exits `0`, and the working tree is clean.

---

## Execution Notes

- Tasks 1-5 may run in an isolated worktree.
- Integrate Tasks 1-5 into the permanent checkout before Task 6.
- Task 6 changes real per-user Task Scheduler state and must not run in a temporary checkout, container, or remote environment.
- If Task 6 rolls back, stop and report the exact failed verification; do not redesign the scheduler during cutover.
