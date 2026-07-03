# Windows Dispatch Support — Design

**Date:** 2026-07-03
**Status:** Approved (design), pending implementation plan
**Topic:** Make the dispatch timer install/uninstall/status work on Windows

## Problem

Agency's dispatch scheduler works on Linux (systemd) and macOS (launchd), but on
Windows all three operations in `agency/dispatch/install.py` return
"not yet implemented" placeholder strings. The admin dispatch page
(`/admin/dispatch/install`) shows a red banner telling the user to set up a Task
Scheduler entry manually. Windows users cannot install dispatch from the UI.

## Goal

Add a Windows backend so that `install_timer`, `uninstall_timer`, and
`get_timer_status` register, remove, and report on a real Windows Task Scheduler
task that runs the existing dispatch runner every N minutes.

## Non-Goals

- No change to the dispatch runner (`agency/dispatch/run.py`) — it is already
  cross-platform and platform-agnostic.
- No "run when logged off" support. Dispatch runs only while the user is logged
  in (interactive token, no stored credentials, no elevation).
- No change to Linux/macOS backends.
- No admin-page UI redesign beyond what naturally follows from status now
  returning real data on Windows (the existing template already branches on
  `installed` / `timer_active`).

## Decisions

- **Run mode:** user-level, interactive logon token. Runs only while the user is
  logged in. Mirrors the user-scoped systemd/launchd installs. No password
  storage, no admin rights.
- **Mechanism:** `pywin32` — the Task Scheduler 2.0 COM API via
  `win32com.client.Dispatch("Schedule.Service")`. Chosen over `schtasks.exe`
  (CLI) and PowerShell by explicit user preference, accepting the added
  Windows-only dependency for a structured, programmatic COM interface.

## Architecture

Only `agency/dispatch/install.py` changes. It gains three private functions that
parallel the existing Linux/macOS helpers, wired into the existing public
dispatchers:

| Public function | Windows branch (new) |
|-----------------|----------------------|
| `get_timer_status()` | `_status_windows()` |
| `install_timer(config_path, interval)` | `_install_windows(config_path, interval)` |
| `uninstall_timer()` | `_uninstall_windows()` |

The public functions already branch on `detect_platform()`; the `else` (windows)
branches currently return placeholder strings and will be changed to call the new
helpers.

### Task identity

- **Task name:** `AgencyDispatch`
- **Folder:** root (`\`)
- **Principal:** current user, `TASK_LOGON_INTERACTIVE_TOKEN` (3)
- **Registration flag:** `TASK_CREATE_OR_UPDATE` (6) — re-installing updates the
  existing task idempotently, matching the Linux/macOS "overwrite" behavior.

### Action

- **Executable:** prefer `pythonw.exe` resolved from the directory of
  `sys.executable` (avoids a console window flashing every N minutes); fall back
  to `sys.executable` if `pythonw.exe` is not present.
- **Arguments:** `-m agency.dispatch.run --config "<config_path>"`
- **Working directory:** the repository root (parent of the `agency` package),
  consistent with how the runner is invoked elsewhere.

### Trigger

- A `TimeTrigger` starting at registration time, with a `Repetition` interval of
  `PT<interval>M` (e.g. `PT15M`) and no repetition end / no duration limit, so it
  fires every `interval` minutes indefinitely.

## Dependency and import safety

- Add to `pyproject.toml` dependencies: `"pywin32; sys_platform == 'win32'"`.
  The environment marker ensures it is installed only on Windows, leaving
  Linux/macOS/CI installs unchanged.
- `import win32com.client` (and `pywintypes` for its `com_error`) happens
  **inside** the Windows helper functions, not at module top level. This keeps
  `install.py` importable on platforms where `pywin32` is absent.
- The import is wrapped in `try/except ImportError`; if `pywin32` is missing on a
  Windows machine, the helpers return a friendly error string
  ("pywin32 is required for Windows dispatch; install it with `pip install
  pywin32`") instead of raising.

## Operation details

### `_install_windows(config_path, interval) -> str | None`

1. Import `win32com.client` (guarded).
2. Resolve the `pythonw.exe`/`sys.executable` launcher path.
3. Connect to the scheduler service, get the root folder.
4. Create a new task definition; set registration info (author/description),
   settings (`StartWhenAvailable = True`, enabled), one `TimeTrigger` with the
   `PT<interval>M` repetition, and one `ExecAction` with the launcher + args +
   working dir.
5. `RegisterTaskDefinition("AgencyDispatch", definition, TASK_CREATE_OR_UPDATE,
   user_name, None, TASK_LOGON_INTERACTIVE_TOKEN)`.
6. Return `None` on success; on `com_error`, return a readable error string.

### `_status_windows() -> dict`

1. Import guarded; on ImportError return `{"installed": False,
   "timer_active": False}` (nothing could have been installed without it).
2. Connect, get root folder, `GetTask("AgencyDispatch")`.
3. If found: `{"installed": True, "timer_active": <task.Enabled and
   state == TASK_STATE_READY or TASK_STATE_RUNNING>}`.
4. If not found (`com_error`): `{"installed": False, "timer_active": False}`.

The returned dict shape matches the Linux/macOS helpers exactly.

### `_uninstall_windows() -> str | None`

1. Import guarded.
2. Connect, get root folder, `DeleteTask("AgencyDispatch", 0)`.
3. Treat "task not found" as success (idempotent). Return `None` on success, or
   an error string for other COM failures.

## Testing

`pywin32` is not installed on the Linux CI, so tests must not import it for real.

- Inject a mock `win32com.client` (and `pywintypes`) into `sys.modules` (via
  `unittest.mock.patch.dict`) and patch `platform.system` to return `"Windows"`.
- **Install test:** assert the mock scheduler chain is driven correctly — a task
  definition is created, the trigger repetition is `PT<interval>M`, the exec
  action targets the resolved python launcher with
  `-m agency.dispatch.run --config <path>`, and `RegisterTaskDefinition` is
  called with `TASK_CREATE_OR_UPDATE` and the interactive logon constant.
- **Status test (installed):** `GetTask` returns a mock task in READY state →
  `{"installed": True, "timer_active": True}`.
- **Status test (not installed):** `GetTask` raises the mock `com_error` →
  `{"installed": False, "timer_active": False}`.
- **Uninstall test:** `DeleteTask` is called with `"AgencyDispatch"`; a
  not-found `com_error` still yields success (`None`).
- **Import-missing test:** simulate `ImportError` for `win32com.client` and
  assert each helper returns the friendly error / safe status dict rather than
  raising.
- `detect_platform()` → `"windows"` routing is already covered by the existing
  test in `tests/test_dispatch_install.py`.

No real Task Scheduler calls occur in tests — the same mocking philosophy as the
Linux/macOS tests, which mock `subprocess.run`.

## Risks / Notes

- COM constant values are hard-coded (`TASK_CREATE_OR_UPDATE = 6`,
  `TASK_LOGON_INTERACTIVE_TOKEN = 3`, `TASK_STATE_*`) since we avoid importing the
  `taskscheduler` type library; they are stable, documented Windows constants.
- `pythonw.exe` running the dispatch means output goes to the runner's own log
  files (unchanged behavior), not a console — acceptable and expected.
- Manual verification on a real Windows box (install → observe task in Task
  Scheduler → wait for a fire → status shows active → uninstall) is part of the
  implementation plan's validation, since CI cannot exercise real COM.
