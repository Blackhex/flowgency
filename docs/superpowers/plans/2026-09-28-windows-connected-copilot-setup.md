# Native Windows Connected Copilot Setup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make native Windows first-run Copilot setup run in Flowgency's existing browser terminal, while proving whole-tree cleanup before replacement.

**Architecture:** Start a Python PTY helper suspended, attach it to a kill-on-close Windows Job Object, and resume it only after containment succeeds. The helper uses the Windows-only pywinpty ConPTY wheel, communicating through bounded framed pipes with the server-side `ConnectedProcess` adapter. The existing setup session, browser access controls, xterm UI, and POSIX backend remain unchanged.

**Tech Stack:** Python 3.11+, pywin32 Job Objects, pywinpty 3.x ConPTY, `uvicorn[standard]` WebSockets on Windows, FastAPI, pytest, Playwright.

## Global Constraints

- Windows in-page first-run setup is the primary goal; Linux/POSIX remains optional and its existing adapter must not be changed or claimed qualified by Windows tests.
- Copilot alone gains connected setup. Never route first-run setup through durable jobs, dispatch, configured-agent sandbox policy, or headless Copilot flags.
- Flowgency must create the helper with `CREATE_SUSPENDED`, assign it to a per-session `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` Job Object with neither breakaway limit, and resume only after successful assignment. Direct `pywinpty.PTY.spawn()` in the web server is forbidden.
- Force pywinpty's ConPTY backend; compiled Windows Python wheels are allowed, but no separate Node.js or Rust runtime is required. An unavailable backend, missing WebSocket support, failed assignment, or unconfirmed cleanup leaves Windows connected mode unavailable or blocked and keeps the existing separate-console fallback only when cleanup is proven.
- The server owns the Job handle. Stop, timeout, shutdown, failed startup, and helper failure must confirm empty Job accounting before releasing the slot; an unknown result retains the handle for retry and blocks replacement. Root/helper exit alone is not cleanup proof.
- Preserve direct-loopback peer/Host, same-origin Origin, signed-cookie and CSRF checks before any data-root preparation; only the starting local browser may control one session. Do not pass arbitrary browser argv, credentials in URLs/command lines/logs, or PTY output through HTML.
- Preserve the setup manager's 2 MiB replay, 256 KiB slow-client limit, 64 KiB input bound, one-hour idle/four-hour total limits, session_view no-ready-redirect, and configuration-derived readiness. A browser disconnect never stops the server-owned session.
- Work in the ignored `.worktrees/windows-connected-setup/` branch worktree. Run commands/tests there, stage only task-owned files, review each task before dependent work, run the full Python suite before review/completion, and preserve `config.yaml`, locks, teams, logs, and other runtime-local data.

## File Structure

- `flowgency/jobs/windows_pty_protocol.py`: bounded typed length-prefixed pipe frames shared by the parent adapter and helper. No process ownership here.
- `flowgency/jobs/windows_job.py`: Windows Job Object creation, suspended helper spawn, assignment, resume, membership/accounting, and confirmed stop. Reuse the supervisor's Job accounting from `flowgency/jobs/processes.py`; never wrap the durable job scheduler.
- `flowgency/jobs/windows_pty_helper.py`: packaged `python -u -m` entry point; receives launch data only after containment, forces `winpty.Backend.ConPTY`, and owns PTY read/write/resize threads.
- `flowgency/jobs/windows_connected_process.py`: server-side `ConnectedProcess` adapter, framed transport, startup handshake, output buffering, and Job-backed evidence. Reuses `_OwnedTerminal` and fail-closed tracking in `connected_process.py`.
- `flowgency/jobs/connected_process.py`: choose Windows adapter only when safe capability checks succeed, keeping the POSIX implementation and public API.
- `flowgency/jobs/processes.py`: expose/reuse small Job-creation/accounting primitives currently private in the headless Windows supervisor; do not restructure the unrelated headless execution path.
- `pyproject.toml`: Windows pywinpty wheel and production WebSocket protocol; keep POSIX ptyprocess marker.
- `tests/test_windows_pty_protocol.py`, `tests/test_windows_job.py`, `tests/test_windows_connected_process.py`: framed-protocol, real containment, adapter, failure-path and cleanup coverage. Extend `tests/test_connected_process.py` and existing setup route/session tests only where their Windows fail-closed assertions must change.
- `tests/ui/server.py`, `tests/ui/setup.spec.ts`: use fake `ConnectedProcess` on Windows without pretending the platform is POSIX, exercise launch/refresh/Stop/fallback. Existing xterm source, templates and routes remain common unless a failing browser regression identifies a real gap.
- `README.md`, `kb/getting-started.md`, the earlier connected-setup spec/plan: update Windows UX and qualify POSIX separately after verification.

## Preflight

The feature worktree was created at `feature/windows-connected-setup` from master `716955a`. Before the first code edit, its full Python baseline was **3163 passed, 19 skipped, one existing Starlette/AnyIO warning**. An initial run had two intermittent, unrelated Windows ticket HTTP connection resets; both tests passed in isolation, and the unchanged full rerun was green. Do not fix those transport tests in this feature. `npm ci` completed from the existing lockfile with zero reported vulnerabilities. The written design is `docs/superpowers/specs/2026-09-28-windows-connected-copilot-setup-design.md` at commit `dbeff8f`.

First verify a real contained Python helper can create a pywinpty ConPTY child and immediate grandchild in the same Job. If this fails, STOP: keep `connected_process_available()` false on Windows and report the observed containment failure rather than shipping an unowned terminal.
The approved design has no sketches or mockups to archive; reuse the existing
setup terminal layout and compare new Windows browser screenshots with the
current desktop/mobile setup evidence.

### Task 1: Frame The Private Helper Channel

**Files:**
- Create: `flowgency/jobs/windows_pty_protocol.py`
- Test: `tests/test_windows_pty_protocol.py`

**Interfaces:**
- Produces `FrameType(IntEnum)` with `START=1`, `INPUT=2`, `RESIZE=3`, `READY=4`, `OUTPUT=5`, `EXIT=6`, `ERROR=7`; `MAX_START_BYTES = 1 << 20`, `MAX_DATA_BYTES = 64 * 1024`.
- Produces `write_frame(stream: BinaryIO, kind: FrameType, payload: bytes) -> None` and `read_frame(stream: BinaryIO) -> tuple[FrameType, bytes] | None`. A complete EOF at the next frame header returns `None`; partial header/payload, unknown type, and oversize frame raise `ValueError`. Only `START` may use the larger cap. Output/input bytes are unmodified by framing.
- Produces `encode_start(launch: RuntimeLaunch, rows: int, cols: int) -> bytes` and `decode_start(payload: bytes) -> tuple[RuntimeLaunch, int, int]`. Use `json.dumps`/`json.loads`, require nonempty string argv, absolute cwd, string/string environment, `mode="connected"`, `2 <= rows <= 200`, `20 <= cols <= 400`; serialize no auth cookie or browser-provided command.

- [ ] **Step 1: Write failing protocol tests.** In `tests/test_windows_pty_protocol.py`:

```python
import io
from pathlib import Path
import pytest
from flowgency.integrations.models import RuntimeLaunch
from flowgency.jobs.windows_pty_protocol import FrameType, decode_start, encode_start, read_frame, write_frame


def test_output_frame_round_trips_terminal_bytes():
    data = io.BytesIO()
    write_frame(data, FrameType.OUTPUT, b"\x1b[31mhello\x00\xff")
    data.seek(0)
    assert read_frame(data) == (FrameType.OUTPUT, b"\x1b[31mhello\x00\xff")
    assert read_frame(data) is None


def test_start_round_trips_launch_and_unicode(tmp_path: Path):
    launch = RuntimeLaunch(("copilot", "-i", "Příprava"), tmp_path, {"SAFE": "ž"}, "connected")
    assert decode_start(encode_start(launch, 24, 80)) == (launch, 24, 80)


@pytest.mark.parametrize("wire", [b"\x01", b"\xff\x00\x00\x00\x00", b"\x05\x00\x01\x00\x01"])
def test_rejects_partial_unknown_or_oversized_frame(wire: bytes):
    with pytest.raises(ValueError):
        read_frame(io.BytesIO(wire))
```

Add explicit tests for a truncated payload, oversize START, invalid JSON and type/dimension validation. `struct.Struct("!BI")` is the five-byte header (type + unsigned payload length); do not parse by splitting strings.

- [ ] **Step 2: Run RED.** `./.venv/Scripts/python.exe -m pytest tests/test_windows_pty_protocol.py -q` from the feature worktree. Expected: import of the not-yet-created protocol module fails.
- [ ] **Step 3: Implement framing and validation.** Use `IntEnum`, `struct.Struct("!BI")`, bounded exact reads, and structured JSON. The core API is:

```python
def write_frame(stream: BinaryIO, kind: FrameType, payload: bytes) -> None:
    limit = MAX_START_BYTES if kind == FrameType.START else MAX_DATA_BYTES
    if len(payload) > limit:
        raise ValueError("PTY frame is too large")
    stream.write(_HEADER.pack(kind, len(payload)) + payload)
    stream.flush()
```

Reject NUL in argv/env, keep `RuntimeLaunch.cwd` as a resolved `Path`, and reject a negative/zero size before writing START. Distinguish clean EOF from truncated frames.
- [ ] **Step 4: Verify GREEN.** Run `tests/test_windows_pty_protocol.py -q` (all listed cases) and `tests/test_repository_boundaries.py -q`; then the full `tests/ -q` suite on current code before task review.
- [ ] **Step 5: Review and commit.** `git diff --check`, stage only protocol and its test, commit `feat(setup): frame windows pty controls`.

### Task 2: Prove Pre-Spawn Job Containment

**Files:**
- Modify: `flowgency/jobs/processes.py` (reuse Job creation/accounting)
- Create: `flowgency/jobs/windows_job.py`
- Test: `tests/test_windows_job.py`, `tests/test_runtime_process_lifecycle.py`
- Modify: `pyproject.toml` (Windows-only pywinpty runtime wheel and Windows production WebSocket support)

**Interfaces:**
- Consumes `RuntimeProcessLifecycle`, `_job_exit_status(job_handle, deadline)` from `processes.py`; a `WindowsJobOwner` is not a durable JobRecord.
- Produces `WindowsJobOwner.launch(argv: tuple[str, ...], cwd: Path, env: dict[str, str], stdin_handle: int, stdout_handle: int) -> WindowsJobOwner`: sets helper std handles, creates its helper with `CREATE_SUSPENDED | CREATE_NO_WINDOW`, assigns to a new kill-on-close Job with neither breakaway flag, *then* resumes. Holds `job_handle`, `process_handle`, `pid`, and a non-inheritable Job handle in the parent.
- Produces `WindowsJobOwner.contains(pid: int) -> bool` via `win32api.OpenProcess` + `win32job.IsProcessInJob` against its held Job; `stop(deadline: float) -> tuple[bool, str]` calls `TerminateJobObject` and `_job_exit_status`, returning `(True, "stopped")` only for empty accounting; `alive() -> bool` checks Job accounting, `exit_code() -> int | None` reads helper's known exit code, and `close_confirmed() -> None` closes handles only when a prior Stop proved empty.

- [ ] **Step 1: Write the failing ordering and failure tests.** In `tests/test_windows_job.py` (Windows-only skip marker), wrap pywin32 calls with monkeypatch recorders:

```python
import os
import sys
import time

import win32api
import win32con
import win32job
import win32pipe
import win32process
import win32security

from flowgency.jobs.windows_job import WindowsJobOwner


def test_helper_is_assigned_before_resume(monkeypatch, tmp_path):
    events = []
    security = win32security.SECURITY_ATTRIBUTES()
    security.bInheritHandle = 1
    child_stdin, parent_stdin = win32pipe.CreatePipe(security, 0)
    parent_stdout, child_stdout = win32pipe.CreatePipe(security, 0)
    win32api.SetHandleInformation(parent_stdin, win32con.HANDLE_FLAG_INHERIT, 0)
    win32api.SetHandleInformation(parent_stdout, win32con.HANDLE_FLAG_INHERIT, 0)

    assign = win32job.AssignProcessToJobObject
    resume = win32process.ResumeThread

    def recording_assign(job, process):
        events.append("assign")
        return assign(job, process)

    def recording_resume(thread):
        events.append("resume")
        return resume(thread)

    monkeypatch.setattr(win32job, "AssignProcessToJobObject", recording_assign)
    monkeypatch.setattr(win32process, "ResumeThread", recording_resume)
    owner = None
    try:
        owner = WindowsJobOwner.launch(
            (sys.executable, "-c", "pass"), tmp_path, os.environ.copy(),
            int(child_stdin), int(child_stdout),
        )
        assert events == ["assign", "resume"]
    finally:
        if owner is not None:
            confirmed, _reason = owner.stop(time.monotonic() + 5)
            assert confirmed
            owner.close_confirmed()
        for handle in (child_stdin, parent_stdin, parent_stdout, child_stdout):
            win32api.CloseHandle(handle)
```

The test fixture must create inheritable *child* pipe ends, non-inheritable *parent* ends, and close its own duplicates; do not use fabricated integers for handles. Add `test_assign_failure_never_resumes_and_terminates_suspended_helper`, `test_unknown_job_accounting_retains_handle`, and a real child/grandchild case that asserts `owner.contains(helper_pid)`, `owner.contains(child_pid)` and `owner.contains(grandchild_pid)` BEFORE Stop, then that Stop confirms Job empty and all identities are gone. For the real proof, launch a tiny Python helper that creates a grandchild *immediately*, reports both PIDs through stdout, and waits on a bounded Event/pipe; no Copilot CLI or user files. Spawn via `WindowsJobOwner.launch`, never a direct pywinpty launch. Add `test_job_close_reaps_tree_after_owner_crash`: start the same contained helper from a separate Python test process, return the grandchild PID through a pipe, forcibly terminate that owner process (without calling its Stop), and assert its descendant identity is no longer alive because the non-inherited Job handle closed. On a CI host lacking nested Job assignment, fail closed and report rather than skipping a containment failure.
- [ ] **Step 2: Run RED.** Run `./.venv/Scripts/python.exe -m pytest tests/test_windows_job.py -q`; expect missing `WindowsJobOwner`.
- [ ] **Step 3: Add the smallest shared Job primitives and owner.** Factor the existing headless supervisor's `CreateJobObject` + `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` setup into `create_kill_on_close_windows_job()` in `processes.py`, then call it from both headless runner and new `windows_job.py`; do not change headless timeout semantics. In `launch` use a `try/finally` whose failure path terminates/waits the suspended helper, checks Job accounting, and never resumes on assignment failure. Treat accounting exceptions as `unknown`, retain handles/registry until retry; register any uncertain owner so the next launch is refused. Do not let the child inherit the Job handle. Use existing `subprocess.list2cmdline` rather than string concatenation for the helper command.
    In `pyproject.toml`, change the Windows dependency from plain `uvicorn` to
    `uvicorn[standard]` and add the Windows-only wheel:

```toml
"uvicorn[standard]; sys_platform == 'win32'",
"pywinpty>=3.0.5,<4; sys_platform == 'win32'",
"uvicorn[standard]; sys_platform != 'win32'",
"ptyprocess>=0.7,<1; sys_platform != 'win32'",
```

    Keep the existing `websockets` test extra; Windows production must now also
    be able to upgrade `/setup/session/ws`. `WindowsJobOwner.launch` creates a
    helper using `win32process.CreateProcess` with
    `CREATE_SUSPENDED | CREATE_NO_WINDOW`, assigns with
    `win32job.AssignProcessToJobObject`, and only then calls
    `win32process.ResumeThread`. Release a Job handle only after
    `_job_exit_status(job_handle, deadline) == "empty"`.
- [ ] **Step 4: Verify real inheritance gate.** Run `tests/test_windows_job.py tests/test_runtime_process_lifecycle.py -q` on *native Windows*. Real immediate-grandchild membership and cleanup are non-skippable. If either PID is outside the Job, STOP; leave Windows `connected_process_available()` false, report failure and revisit the design before Tasks 3-5.
- [ ] **Step 5: Review and commit.** Run the full Python suite, `git diff --check`, stage only this task's files, commit `feat(runtime): contain windows pty helper`.

### Task 3: Run pywinpty Inside The Owned Helper

**Files:**
- Create: `flowgency/jobs/windows_pty_helper.py`
- Test: `tests/test_windows_pty_helper.py` (unit/real ConPTY helper), extend `tests/test_windows_job.py`

**Interfaces:**
- Consumes `FrameType`, `read_frame`, `write_frame`, `decode_start` (Task 1), and `WindowsJobOwner.launch` (Task 2) for real tests.
- Produces `main(stdin: BinaryIO, stdout: BinaryIO) -> int` invoked by `python -u -m flowgency.jobs.windows_pty_helper` inside the already-contained helper. It reads one START before importing `winpty`, forces `winpty.PTY(cols, rows, backend=winpty.Backend.ConPTY)`, calls `pty.spawn(appname=argv[0], cmdline=subprocess.list2cmdline(argv), cwd=str(cwd), env=environment_block)`, and sends READY with JSON `{ "pid": child_pid }` only after successful ConPTY spawn. Test exact pywinpty argv behavior: its `cmdline` must not duplicate `argv[0]` on this version. Startup data remains in the private pipe, never a command line.
    Construct `environment_block` in the helper from the already validated
    `launch.env` mapping, matching pywinpty's high-level wrapper:

```python
environment_block = "\0".join(f"{key}={value}" for key, value in launch.env.items()) + "\0"
pty = winpty.PTY(cols, rows, backend=winpty.Backend.ConPTY)
pty.spawn(
        appname=launch.argv[0],
        cmdline=subprocess.list2cmdline(list(launch.argv)),
        cwd=str(launch.cwd),
        env=environment_block,
)
```

    Task 1's START validation must reject NUL or `=` in env keys, NUL in env
    values/argv, and missing argv. Verify a real child receives intended Unicode
    env/argv before treating helper READY as success.
- After READY, one thread drains `pty.read(blocking=True)` continuously and emits OUTPUT bytes; the other consumes INPUT/RESIZE frames, calls `pty.write(text)`/`pty.set_size(cols, rows)`, and sends EXIT when the child terminates. ConPTY produces UTF-8 terminal text; encode pywinpty's returned `str` to UTF-8 with no lossy replacement. Use an incremental UTF-8 decoder for INPUT frames if multi-byte characters span frames. A helper-side write lock serializes OUTPUT/EXIT/ERROR frames. Never print PTY text or raw startup errors to stderr/logs.

- [ ] **Step 1: Write RED tests.** In `tests/test_windows_pty_helper.py`, fake `winpty.PTY` to assert the backend is `Backend.ConPTY`, parsed `cwd`/`argv`/environment and READY only after spawn; send INPUT `b"yes\r"`/RESIZE `{"rows":30,"cols":100}` and assert corresponding fake operations and binary OUTPUT. Add invalid START/unsupported type and pywinpty spawn-failure ERROR cases that do not claim READY. Extend `tests/test_windows_job.py` with a real Job-owned helper round trip launching a Python program that immediately spawns a grandchild: assert native pywinpty PID and grandchild both satisfy `owner.contains(pid)`. Example test assertion:

```python
import json
import msvcrt
import os
import queue
import re
import threading
import time

parent_output = os.fdopen(
    msvcrt.open_osfhandle(parent_stdout.Detach(), os.O_RDONLY | os.O_BINARY),
    "rb", buffering=0,
)

def next_frame(timeout: float):
    result = queue.Queue(maxsize=1)
    def read_once():
        try:
            result.put(read_frame(parent_output))
        except BaseException as error:
            result.put(error)
    threading.Thread(target=read_once, daemon=True).start()
    frame = result.get(timeout=timeout)
    if isinstance(frame, BaseException):
        raise frame
    return frame

try:
    kind, payload = next_frame(10)
    assert kind == FrameType.READY
    assert owner.contains(json.loads(payload)["pid"])
    output = bytearray()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        frame = next_frame(max(0.01, deadline - time.monotonic()))
        assert frame is not None
        if frame[0] == FrameType.OUTPUT:
            output.extend(frame[1])
            match = re.search(rb"grandchild-pid:(\d+)", output)
            if match:
                assert owner.contains(int(match.group(1)))
                break
    else:
        raise AssertionError("ConPTY grandchild did not report its PID")
finally:
    assert owner.stop(time.monotonic() + 5) == (True, "stopped")
    owner.close_confirmed()
    parent_output.close()
```

- [ ] **Step 2: Run RED.** Run `tests/test_windows_pty_helper.py -q`; expect import of the missing helper to fail.
- [ ] **Step 3: Implement the helper.** Use a bounded frame writer, two terminal I/O threads, `winpty.PTY` directly (not `PtyProcess`, which decodes through a socket and can obscure UTF-8 boundaries), and `contextlib.suppress` only for known teardown I/O errors. If it cannot force ConPTY, send ERROR and exit without spawning. Never use shell execution for PTY argv. EOF or malformed controls close the helper; the parent still owns and proves the Job empty.
    Expose the packaged module entry point without exposing startup argv on the
    helper's command line:

```python
if __name__ == "__main__":
        raise SystemExit(main(sys.stdin.buffer, sys.stdout.buffer))
```

    The parent invokes it as `sys.executable -u -m flowgency.jobs.windows_pty_helper`
    and sends the START frame only after Job assignment and resume.
- [ ] **Step 4: Verify GREEN.** Run helper and real Windows Job tests. If ConPTY spawns but its child/grandchild are not in the Job, STOP rather than adding direct-PID killing as a substitute. Run the full Python suite before review.
- [ ] **Step 5: Review and commit.** `git diff --check`, stage only helper and related tests, commit `feat(setup): run contained conpty helper`.

### Task 4: Expose The Windows ConnectedProcess Adapter

**Files:**
- Create: `flowgency/jobs/windows_connected_process.py`
- Modify: `flowgency/jobs/connected_process.py`
- Test: `tests/test_windows_connected_process.py`, `tests/test_connected_process.py`, `tests/test_setup_sessions.py`

**Interfaces:**
- Consumes `WindowsJobOwner`, framed protocol, and helper from Tasks 1-3.
- Produces `WindowsConnectedProcess.spawn(launch: RuntimeLaunch, *, rows: int, cols: int) -> WindowsConnectedProcess`, implementing existing `ConnectedProcess` (`pid`, `read`, `write`, `resize`, `alive`, `exit_code`, `stop(lifecycle) -> ProcessStopEvidence`). `pid` names the helper; `alive()` reflects Job membership, not helper exit. Startup writes START to contained helper, accepts READY only after `owner.contains(child_pid)` succeeds, and returns a running adapter; any ERROR/EOF/timeout triggers Job stop and `ConnectedLaunchError(cleanup_confirmed=...)`.
- `connected_process_available()` returns true on Windows only if `winpty.Backend.ConPTY`, pywin32 Job operations and the production WS protocol are present. `start_connected_process()` validates mode, size/env, retries unconfirmed processes, then dispatches the Windows or unchanged POSIX adapter. Missing capabilities fail closed without spawning; only proven-clean `ConnectedLaunchError` permits external fallback.

- [ ] **Step 1: Write failing availability, I/O and cleanup tests.** Replace `test_windows_connected_process_gate_never_imports_a_pty_backend` in `tests/test_connected_process.py` with Windows tests that monkeypatch `winpty` unavailable and show `connected_process_available() is False`/no spawn; with dependencies present, show true. Add `tests/test_windows_connected_process.py` cases for READY+output, UTF-8 input/output (including split multi-byte input), size propagation, helper root exit while a grandchild lives, helper crash/partial READY, unknown Job accounting, later Stop retry, and cancellation during startup. Preserve existing POSIX tests unchanged. An integration assertion:

```python
from tests.test_connected_process import _read_until

launch = RuntimeLaunch((sys.executable, "-u", "-c", "print('connected', flush=True)"), tmp_path, os.environ.copy(), "connected")
process = start_connected_process(launch)
try:
    assert b"connected" in _read_until(process, rb"connected").group(0)
finally:
    assert process.stop(RuntimeProcessLifecycle("setup", "windows-test")).confirmed
```

- [ ] **Step 2: Run RED.** Windows test above currently raises `ConnectedLaunchError` before spawning; focused `tests/test_windows_connected_process.py tests/test_connected_process.py -q` must fail for the new Windows behavior.
- [ ] **Step 3: Implement Windows adapter and select it.** Make `WindowsConnectedProcess` use `_OwnedTerminal`'s `_stop_tree`, `_track` and `_retry_unconfirmed` behavior; its bounded reader parses OUTPUT/EXIT/ERROR and preserves a known child exit code. Use `threading.Lock` for writes and size frames, a bounded queue for output rather than collecting an unbounded transcript, and finite startup/Stop deadlines. `stop` kills/queries Job first, then joins reader threads and closes handles only after empty accounting and reader drain. If either is unknown, retain owner/process handles for retry; avoid closing a pipe descriptor while a reader still owns it. Return the same evidence semantics as POSIX.
- [ ] **Step 4: Verify GREEN.** Run the Windows adapter, manager concurrency/cancellation, process lifecycle and headless Copilot regressions. Run the complete Python suite on Windows; real Windows child/grandchild cleanup must pass before enabling the connected route.
- [ ] **Step 5: Review and commit.** `git diff --check`, stage only adapter/gate/test files, commit `feat(setup): enable owned windows pty`.

### Task 5: Connect Native Windows To The Existing Setup Page

**Files:**
- Modify: `tests/ui/server.py`, `tests/ui/setup.spec.ts`
- Test: `tests/test_server.py`, `tests/test_setup_flow.py`, `tests/test_setup_assets.py`
- Modify existing route/template only if a failing regression proves a real contract gap; do not fork a second Windows UI.

**Interfaces:**
- Consumes `connected_process_available()` and `start_connected_process()` from Task 4 and unchanged `SetupSessionManager(process_factory=...)`. The existing `GET /setup`, `POST /setup/launch`, `GET /setup/session`, WS control route, dashboard indicator and xterm bundle are the browser surface.
- Produces Windows in-page setup when a contained ConPTY helper is available, and a copyable external fallback only after cleanup is confirmed. The fake UI fixture uses `SetupSessionManager(process_factory=fake)` but must not override Windows capability to pretend it is a POSIX host.

- [ ] **Step 1: Write RED browser and HTTP tests.** In `tests/ui/server.py`, remove the `connected_process_available = lambda: True` assignments for Windows from `_reset_connected_setup_runtime`; assert the real Windows capability is true in a Windows CI with Task 4 installed. Keep the fake integration/process factory so no browser test spawns a real Copilot CLI. Add a Windows-only Playwright test to `tests/ui/setup.spec.ts` (or extend the existing connected setup test) that reaches `/setup/session` after submitting the same-origin/CSRF form, sees `#setup-terminal .xterm-screen`, types input, reloads and Stop returns to the editable form. Add a server regression in `tests/test_server.py` that injects a `ConnectedLaunchError(cleanup_confirmed=True)` and asserts fallback only then; inject an unconfirmed error and assert no external launch. Existing route tests should remain green.

```typescript
test('native Windows setup embeds the terminal', async ({ page, request }) => {
  test.skip(process.platform !== 'win32');
    await launchConnectedTerminal(page, request);
  await expect(page.locator('#setup-terminal .xterm-screen')).toBeVisible();
    await page.locator('#setup-terminal').click();
    await page.keyboard.type('windows input');
    await expect.poll(async () => {
        const writes = await (await request.get('/__ui/setup/session/writes')).json();
        return writes.writes.join('');
    }).toContain('windows input');
    await page.reload();
    await expect(page.locator('#setup-terminal .xterm-screen')).toBeVisible();
    await page.getByRole('button', { name: 'Stop', exact: true }).click();
    await expect(page).toHaveURL(/\/setup$/);
});
```

- [ ] **Step 2: Run RED.** Windows `npm run test:ui -- tests/ui/setup.spec.ts --project desktop-light` must fail without the Windows backend or capability when the fixture override is removed.
- [ ] **Step 3: Reuse common UI only.** Make the fixture's reset replace only the process factory/registered fake integration, while restoring real availability in both `flowgency.web.setup_flow` and `admin_teams`. Do not change xterm source unless the Windows test reveals a reproducible escape/size/input defect; rebuild generated JS/CSS only when source changes. Keep launch security unchanged.
- [ ] **Step 4: Verify GREEN.** Run `tests/test_server.py tests/test_setup_flow.py tests/test_setup_assets.py -q`, build terminal/CSS from pinned `npm ci`, and run all four Playwright setup projects. Run full Python suite before task review. Assert no terminal output enters templates and the default fixture still restores real integrations.
- [ ] **Step 5: Review and commit.** Stage only Task 5 files and any generated assets actually changed, commit `test(setup): verify native windows terminal` (or `feat(setup): connect windows browser` if production code changed).

### Task 6: Document, Smoke-Test, Review, Integrate

**Files:**
- Modify: `README.md`, `kb/getting-started.md` (docs-only commit)
- Test/review: `tests/test_windows_job.py`, `tests/test_windows_pty_helper.py`, `tests/test_windows_connected_process.py`, `tests/test_connected_process.py`, `tests/test_setup_sessions.py`, `tests/test_server.py`, `tests/ui/setup.spec.ts`.

**Interfaces:**
- Consumes Windows Job-backed `ConnectedProcess` and common browser/manager from Tasks 1-5; creates no new application API.

- [ ] **Step 1: Update first-run docs.** Replace Windows separate-console-as-normal copy with native Windows in-page setup and state that the separate console is the safe fallback if ConPTY/Job/WebSocket support is unavailable or safe cleanup cannot be confirmed. Keep the server-user privilege warning, read-only browser limitation, data-root/skill/atomic-write guidance, and POSIX qualification caveat. Example:

```markdown
On native Windows, Copilot setup runs in this browser's terminal when the
contained ConPTY backend is available. If it cannot prove process cleanup,
Stop blocks another launch; a separate console is offered only after safe
cleanup. Closing the tab does not Stop the server-owned session.
```

- [ ] **Step 2: Validate docs and commit separately.** Run `tests/test_repository_boundaries.py tests/test_setup_assets.py tests/test_server.py -q` and `git diff --check`; stage only README and guide, commit `docs(setup): explain windows in-page setup`.
- [ ] **Step 3: Run final automated gates.** Run `./.venv/Scripts/python.exe -m pytest tests/ -q`, `npm ci`, `npm run build:terminal`, `npm run build:css`, `npm run test:ui -- tests/ui/setup.spec.ts`, and the repository-wide `npm run test:ui`. Verify generated assets match committed bytes and `git status --short` shows only intentional changes.
- [ ] **Step 4: Native Windows smoke.** Use an isolated absent `FLOWGENCY_CONFIG`, throwaway data root and loopback port distinct from any existing dashboard. Start server from this feature worktree and verify module path; in a browser, launch native Windows Copilot in-page, type one harmless reply (the human enters any auth/device code directly), refresh/reconnect, Stop and prove Job accounting empty and no child/grandchild remains, config absent. Repeat with forced contained-launch failure to prove external fallback only after cleanup confirmation. Test graceful server restart with an active session. Never touch the real config or user Copilot session.
- [ ] **Step 5: Whole-branch review and integration.** Review against the design's ownership, browser security, headless compatibility and fallback gates. Fix findings in the owning task with focused tests and final full suite. Then follow `AGENTS.md`: recheck master ancestry and dirty state, rebase only if master advanced, fast-forward only, re-run full Python suite on integrated master, push master and feature branch, remove/prune the isolated worktree after preserving runtime-local files, and start the final trial server from master on a free loopback port. Do not claim Windows in-page support or integrate while real Job containment or authenticated interactive smoke remains unproven.