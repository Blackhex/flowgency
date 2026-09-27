# Connected Copilot Setup Terminal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Host first-run Copilot setup in a reconnectable browser terminal on supported POSIX hosts, preserving headless jobs and the external-terminal fallback on Windows.

**Architecture:** A typed launch contract exposes `headless` and `connected` modes without turning setup into a team job. A PTY adapter and single-session manager own Copilot independently of the browser tab; local, browser-bound HTTP/WebSocket endpoints connect it to the setup page and a post-redirect dashboard link. Readiness still comes only from canonical configuration validation.

**Tech Stack:** Python 3.11+, FastAPI/Starlette, ptyprocess (POSIX), xterm.js with its fit addon and esbuild, Jinja2, pytest, and Playwright. Node.js is a build-time tool, not a production runtime.

## Global Constraints

- Work only in `.worktrees/connected-setup-terminal` on `feature/connected-setup-terminal`; establish a clean `python -m pytest tests/ -q` baseline there before implementation, review each task before dependent work, and rerun the full suite before review and completion.
- `config.yaml` with `schema_version: 1` remains the sole control-plane authority; setup writes it only through the existing revision-checked atomic flow.
- Configured agents remain `headless`; connected setup does not create a team job, change job records or policy, or add connected controls to agent pages.
- Copilot alone gains connected setup in this feature; other integrations and unsupported PTY hosts retain the existing external-terminal launch and copyable command.
- Connected Copilot uses `-i` with the selected data root and packaged skill. Do not inherit `--no-ask-user`, `--autopilot`, `--output-format json`, or closed stdin from headless jobs.
- Use `ptyprocess` with `/proc` process-group evidence for connected setup on supported POSIX hosts. On Windows, never start a connected PTY: retain the existing external-terminal launch. Do not add Node.js, pywinpty or a Rust bridge to production dependencies; smoke-test POSIX connected mode and Windows fallback independently.
- One running setup session per server, one input owner, at most 2 MiB of recent output, and at most 256 KiB queued for a slow WebSocket client. Stop after one hour without input or output or four hours total.
- Do not persist terminal transcripts; reconnect after tab loss, but never promise replay after server restart. Confirm process-tree termination before relaunch after Stop or failed PTY startup.
- Require a direct loopback client, loopback `Host`, same-origin `Origin` for unsafe HTTP and WebSocket upgrades, a browser-bound HTTP-only same-site cookie, and anti-CSRF token for ALL setup launch POSTs (external and connected) and HTTP controls. Guard before integration discovery or data-root preparation; a remote POST cannot create a root or start an agent. Never put credentials in URLs or logs.
- Treat interactive setup as running with the server user's privileges, not a configured agent's sandbox; say so in the UI. Do not permit arbitrary argv or shell commands from the browser.
- Redirect automatically when validated configuration becomes ready; leave Copilot running until exit, Stop, or timeout. Show only its owner's dashboard link back to the terminal and Stop control while it runs.
- Package all terminal JS/CSS in the wheel for offline use. Run Python tests from the active worktree root and preserve unrelated files and runtime state.

---

## File Structure

- `flowgency/integrations/models.py`: typed `ExecutionMode` and `RuntimeLaunch` contract.
- `flowgency/integrations/__init__.py`, `flowgency/integrations/flowgency/copilot.py`: optional connected setup capability and Copilot command/environment construction; preserve headless execution and existing external launch.
- `flowgency/jobs/processes.py`, `flowgency/jobs/connected_process.py`: reuse process identity/termination primitives for POSIX PTY I/O and fail closed on Windows without starting a child.
- `flowgency/web/setup_security.py`: loopback/Host/Origin checks, browser-bound credential, and CSRF checks independent of configuration.
- `flowgency/web/setup_sessions.py`: single server-owned session, bounded output replay, input ownership, timers, and cleanup.
- `flowgency/web/routes/admin_teams.py`: retain data-root validation and status authority; choose connected launch or unchanged external fallback.
- `flowgency/web/routes/setup_terminal.py`: owner-only terminal view, session state/Stop HTTP endpoints, and WebSocket input/output/resize.
- `flowgency/web/setup_flow.py`, `flowgency/web/routes/__init__.py`: discover PTY-only Copilot setup and register the terminal router.
- `flowgency/app.py`: attach manager to app lifespan, include terminal routes, and provide owner-only status to the team dashboard.
- `flowgency/templates/setup.html`, `flowgency/templates/home.html`: integrated setup terminal and compact dashboard return/Stop control.
- `tools/setup-terminal.js`, `package.json`, `package-lock.json`, `flowgency/static/setup-terminal.js`, `flowgency/static/setup-terminal.css`: locally bundled terminal renderer and generated assets; `pyproject.toml` gains a POSIX-only PTY dependency (its `static/*` wheel rule already packages generated assets).
- `tests/test_interactive_setup.py`, `tests/test_copilot_launch_arguments.py`: new mode contract and unchanged headless/external behavior.
- `tests/_connected_setup_helpers.py`, `tests/test_connected_process.py`, `tests/test_setup_security.py`, `tests/test_setup_sessions.py`: one reusable fake PTY, platform supervision, local browser access, and in-memory session behavior.
- `tests/test_setup_flow.py`, `tests/test_server.py`, `tests/test_team_settings.py`, `tests/test_dashboard.py`, `tests/test_setup_assets.py`, `tests/ui/setup.spec.ts`, `tests/ui/server.py`: selector/route, prior bootstrap, dashboard, wheel, and browser regressions using existing fixtures.
- `kb/getting-started.md`, `README.md`: local-access, permissions, reconnect, and fallback guidance.

## Preflight

From the feature worktree root, run `python -m pytest tests/ -q` before making implementation changes. Record the pass count and stop to investigate any baseline failure without editing unrelated tests. The approved design is `docs/superpowers/specs/2026-09-23-connected-copilot-setup-terminal-design.md` (revised at commit `94ebe48` to defer Windows connected mode). After each task, review its focused diff and tests before starting a dependent task. Do not stage or rewrite the runtime-local `config.yaml`, locks, team state, or unrelated changes.

### Task 1: Share A Typed Copilot Launch Contract

**Files:**
- Modify: `flowgency/integrations/models.py` (after `LiveTicketTransport`, near `InteractiveSetupRequest`).
- Modify: `flowgency/integrations/__init__.py` (imports and `BaseIntegration` setup hooks).
- Modify: `flowgency/integrations/flowgency/copilot.py` (setup launch and existing `run`).
- Test: `tests/test_interactive_setup.py`, `tests/test_copilot_launch_arguments.py`.

**Interfaces:**
- Consumes: existing `InteractiveSetupRequest`, `CopilotIntegration._interactive_setup_command()`, `_launch_environment()`, and `IntegrationRunRequest`.
- Produces: `ExecutionMode = Literal["headless", "connected"]`; `RuntimeLaunch(argv: tuple[str, ...], cwd: Path, env: Mapping[str, str], mode: ExecutionMode)`; `BaseIntegration.connected_setup_available() -> bool` and `connected_setup_launch(request: InteractiveSetupRequest) -> RuntimeLaunch`. Task 2 consumes `RuntimeLaunch`; Task 5 calls the connected hook.

- [ ] **Step 1: Write the failing integration tests.** Add these tests to `tests/test_interactive_setup.py` beside the existing Copilot launch tests:

```python
def test_base_integration_does_not_support_connected_setup(tmp_path: Path) -> None:
    integration = BaseIntegration()
    request = InteractiveSetupRequest(tmp_path, tmp_path / "config.yaml", "Set up Flowgency.")
    assert integration.connected_setup_available() is False
    with pytest.raises(IntegrationError):
        integration.connected_setup_launch(request)


def test_copilot_connected_setup_launch_uses_interactive_command_without_private_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(CopilotIntegration, "_interactive_setup_command_prefix", lambda self: ("copilot",))
    monkeypatch.setenv("FLOWGENCY_PRIVATE_KEY", "not-for-copilot")
    request = InteractiveSetupRequest(tmp_path, tmp_path / "config.yaml", "Use the flowgency-setup skill.")
    launch = CopilotIntegration().connected_setup_launch(request)
    assert launch.mode == "connected"
    assert launch.cwd == tmp_path.resolve()
    assert launch.argv == (
        "copilot", "-C", str(tmp_path.resolve()), "--add-dir",
        str(copilot_discovery_root()), "-i", request.prompt, "--name", "Flowgency setup",
    )
    assert "FLOWGENCY_PRIVATE_KEY" not in launch.env
    assert "--no-ask-user" not in launch.argv
```

- [ ] **Step 2: Run the tests to verify red.** Run `python -m pytest tests/test_interactive_setup.py -k connected_setup -q`. Expected: failures because `connected_setup_available` and `connected_setup_launch` do not exist.

- [ ] **Step 3: Introduce the contract and implement Copilot's launch descriptor.** In `models.py`, define:

```python
ExecutionMode = Literal["headless", "connected"]


@dataclass(frozen=True)
class RuntimeLaunch:
    argv: tuple[str, ...]
    cwd: Path
    env: Mapping[str, str]
    mode: ExecutionMode
```

Export `RuntimeLaunch` from `integrations/__init__.py`. Add default hooks to `BaseIntegration`:

```python
def connected_setup_available(self) -> bool:
    return False

def connected_setup_launch(self, request: InteractiveSetupRequest) -> RuntimeLaunch:
    raise IntegrationError(f"{self.display_name or self.name} does not support connected setup.")
```

In `CopilotIntegration`, reuse the existing skill path and Windows executable resolution:

```python
def connected_setup_available(self) -> bool:
    try:
        self._interactive_setup_command_prefix()
    except IntegrationError:
        return False
    return True

def connected_setup_launch(self, request: InteractiveSetupRequest) -> RuntimeLaunch:
    data_root = request.data_root.resolve(strict=True)
    return RuntimeLaunch(
        argv=tuple(self._interactive_setup_command(request)),
        cwd=data_root,
        env=self._launch_environment(None),
        mode="connected",
    )
```

Within `CopilotIntegration.run`, after constructing `cmd_args` and `run_env`, create `RuntimeLaunch(tuple(cmd_args), request.launch_dir, run_env, "headless")`. Pass `list(launch.argv)`, `launch.cwd`, and `dict(launch.env)` to the existing `run_supervised` or `subprocess.run` call without changing timeout, flags, stdin, sandbox, or result parsing. Keep `launch_interactive_setup` and its fallback unchanged.

- [ ] **Step 4: Run focused regressions to verify green.** Run `python -m pytest tests/test_interactive_setup.py tests/test_copilot_launch_arguments.py tests/test_copilot_sandbox_policy.py -q`. Expected: new tests and all existing headless/external setup tests pass.

- [ ] **Step 5: Review and commit this isolated contract.** Inspect `git diff --check` and the changed `CopilotIntegration.run` call sites, then stage only the listed files and commit with `feat(runtime): share copilot launch modes`.

### Task 2: Run And Contain A Connected PTY

**Files:**
- Modify: `pyproject.toml` (POSIX-only runtime dependency; remove the unreviewed Windows pywinpty dependency).
- Modify: `flowgency/jobs/processes.py` (extract reusable POSIX group stop primitive).
- Create: `flowgency/jobs/connected_process.py` (PTY adapters and capability check).
- Test: `tests/test_connected_process.py`, `tests/test_runtime_process_lifecycle.py`.

**Interfaces:**
- Consumes: Task 1's `RuntimeLaunch`; existing `RuntimeProcessLifecycle`, `ProcessStopEvidence`, `_capture_posix_group_identity`, `_signal_owned_posix_group`, `_owned_posix_group_status`, `_job_exit_status`, and `read_process_identity` from `flowgency.jobs.processes`.
- Produces: `ConnectedLaunchError(IntegrationError)` with a `cleanup_confirmed: bool` field; `connected_process_available() -> bool` (always false on Windows); `start_connected_process(launch: RuntimeLaunch, *, rows: int = 24, cols: int = 80) -> ConnectedProcess`; a `ConnectedProcess` protocol implemented by `PosixConnectedProcess`, with `pid: int`, `read(size: int = 65536) -> bytes`, `write(data: bytes) -> None`, `resize(rows: int, cols: int) -> None`, `alive() -> bool`, `exit_code() -> int | None`, and `stop(lifecycle: RuntimeProcessLifecycle) -> ProcessStopEvidence`. Task 4 owns the reader and calls these methods.

- [ ] **Step 1: Write the failing platform and containment tests.** In `tests/test_connected_process.py`, test a mode rejection on both platforms and an interactive child on each supported host:

```python
import os
import sys
from pathlib import Path

import pytest

from flowgency.integrations.models import RuntimeLaunch
from flowgency.jobs.processes import RuntimeProcessLifecycle, process_identity_state, read_process_identity
from flowgency.jobs.connected_process import start_connected_process


def test_connected_process_rejects_headless_launch(tmp_path):
    launch = RuntimeLaunch((sys.executable,), tmp_path, os.environ.copy(), "headless")
    with pytest.raises(ValueError, match="connected"):
        start_connected_process(launch)


@pytest.mark.skipif(os.name == "nt" or not Path("/proc/self/stat").is_file(), reason="POSIX /proc PTY check")
def test_posix_connected_process_has_tty_and_accepts_input(tmp_path):
    command = (sys.executable, "-u", "-c", "import sys; print(sys.stdin.isatty(), flush=True); print(input(), flush=True)")
    launch = RuntimeLaunch(command, tmp_path, os.environ.copy(), "connected")
    process = start_connected_process(launch)
    try:
        assert b"True" in process.read()
        process.resize(30, 100)
        process.write(b"approved\n")
        assert b"approved" in process.read()
    finally:
        evidence = process.stop(RuntimeProcessLifecycle("setup", "posix-test"))
        assert evidence.confirmed


@pytest.mark.skipif(os.name != "nt", reason="Windows fallback check")
def test_windows_connected_process_is_unavailable_without_spawning(tmp_path):
    from flowgency.jobs.connected_process import ConnectedLaunchError, connected_process_available
    launch = RuntimeLaunch((sys.executable, "-c", "print('must not run')"), tmp_path, os.environ.copy(), "connected")
    assert connected_process_available() is False
    with pytest.raises(ConnectedLaunchError) as raised:
        start_connected_process(launch)
    assert raised.value.cleanup_confirmed is True
    assert "Windows" in str(raised.value)
```

Add a process-tree test beside those cases (import `time` and `Path`). It runs on a POSIX host with `/proc`:

```python
def test_connected_process_stop_reaps_child_tree(tmp_path):
    if os.name == "nt" or not Path("/proc/self/stat").is_file():
        pytest.skip("POSIX group evidence requires /proc")
    executable, env = sys.executable, os.environ.copy()
    script = (
        "import subprocess,sys,time; "
        "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)']); "
        "print(child.pid,flush=True); time.sleep(120)"
    )
    launch = RuntimeLaunch((executable, "-u", "-c", script), tmp_path, env, "connected")
    process = start_connected_process(launch)
    try:
        child_pid = int(process.read().strip().splitlines()[0])
        identity = read_process_identity(child_pid)
        assert identity is not None
    finally:
        evidence = process.stop(RuntimeProcessLifecycle("setup", "tree-test"))
    assert evidence.confirmed
    assert process_identity_state(identity) != "alive"
```

Add a Windows-only test that monkeypatches `builtins.__import__` to fail if `winpty` is imported, then asserts `connected_process_available()` stays false and `start_connected_process()` raises `ConnectedLaunchError(cleanup_confirmed=True)` without attempting an import or spawn. It must pass even if pywinpty is **not installed**, since Windows no longer depends on it. A POSIX failure test monkeypatches `_capture_posix_group_identity` to return `None` and asserts the spawned child is cleaned up. Skip POSIX smoke tests where `/proc/self/stat` is absent; those hosts use external fallback.

- [ ] **Step 2: Run the new tests to verify red.** Run `python -m pytest tests/test_connected_process.py -q`. Expected: import error because `flowgency.jobs.connected_process` does not exist.

- [ ] **Step 3: Add the POSIX dependency and implement the PTY adapter.** In `pyproject.toml` retain `"ptyprocess>=0.7,<1; sys_platform != 'win32'"` and remove the Windows `pywinpty` dependency from the previous Task 2 commit. Install from the worktree with `python -m pip install -e .`. Define the `ConnectedProcess(Protocol)` signatures from the Interfaces block, implement the POSIX class, and use this strict mode gate in `connected_process.py`:

```python
class ConnectedLaunchError(IntegrationError):
    def __init__(self, message: str, *, cleanup_confirmed: bool):
        super().__init__(message)
        self.cleanup_confirmed = cleanup_confirmed


def start_connected_process(launch: RuntimeLaunch, *, rows: int = 24, cols: int = 80) -> ConnectedProcess:
    if launch.mode != "connected":
        raise ValueError("A connected PTY requires connected launch mode")
    if os.name == "nt":
        raise ConnectedLaunchError("Connected setup is unavailable on Windows; use the external terminal", cleanup_confirmed=True)
    return PosixConnectedProcess.spawn(launch, rows=rows, cols=cols)
```

On POSIX, require readable `/proc/self/stat` for existing group identity checks, use `PtyProcess.spawn(list(launch.argv), cwd=str(launch.cwd), env=dict(launch.env), dimensions=(rows, cols))`, immediately capture its process group, and fail closed if that capture is unavailable. Read/write bytes and call `setwinsize(rows, cols)` for resize. Extract `terminate_owned_posix_group(group_identity, *, timeout: float) -> Literal["empty", "active", "unknown", "reused"]` in `jobs/processes.py` from the signal/status part of `_terminate_owned_posix_group`; keep a shared lower-level signal/status helper so the headless path still skips root reaping when signaling was `unavailable`, but **does reap after a successful signal even if the later group status is `unknown`**, as it did before commit `83eceb9`. Update the headless regression tests for both cases. Check group status before reaping the leader: `ptyprocess.isalive()` calls `waitpid`, so using only that method can miss live descendants. Read `pty.exitstatus` only after group completion. Stop the PTY group, drain/close the PTY, and return `ProcessStopEvidence(lifecycle.job_id, lifecycle.generation, status == "empty", "stopped" if status == "empty" else status)`; refuse another launch when confirmation fails.

On Windows, `connected_process_available()` always returns false and `start_connected_process()` rejects connected launches before importing pywinpty, constructing a PTY, or creating any child. Keep the existing external-terminal path as the only Windows setup launcher. On POSIX, availability requires `ptyprocess` and parsable `/proc/self/stat` process-group evidence. If POSIX startup fails before spawning, or cleanup is confirmed, raise `ConnectedLaunchError(message, cleanup_confirmed=True)`; if a spawned process cannot be proved stopped, raise with `cleanup_confirmed=False`. Only confirmed cleanup allows external fallback.

- [ ] **Step 4: Run PTY and headless lifecycle regressions to verify green.** Run `python -m pytest tests/test_connected_process.py tests/test_runtime_process_lifecycle.py tests/test_interactive_setup.py -q` on Windows and a POSIX host with `/proc`. Expected: Windows fail-closed gate and existing external launch pass without starting a connected process; native PTY I/O, resize, process-tree stop and headless containment pass on POSIX. A platform-only skip is never counted as a smoke pass.

- [ ] **Step 5: Review and commit PTY supervision.** Inspect failure cleanup and `git diff --check`, then stage only Task 2 files and commit with `feat(runtime): supervise connected pty processes`.

### Task 3: Bind Setup Control To One Local Browser

**Files:**
- Create: `flowgency/web/setup_security.py`.
- Test: `tests/test_setup_security.py`.

**Interfaces:**
- Consumes: Starlette `HTTPConnection`, `Request`, `Response`, and `WebSocket` (no team configuration).
- Produces: `SetupAccessDenied`; `SetupBrowserAccess(secret: bytes | None = None)`; `ensure_browser(request: Request) -> tuple[str, str, bool]` (credential, CSRF, newly issued); `set_cookie(response: Response, credential: str, request: Request) -> None`; `require_http(request: Request, csrf: str | None = None, *, unsafe: bool = False) -> str`; `require_ws(websocket: WebSocket) -> str`; `is_owner(request: Request, credential: str) -> bool`. Tasks 5 and 7 call these exact methods.

- [ ] **Step 1: Write the failing local-access and CSRF tests.** Use a small test-only FastAPI app in `tests/test_setup_security.py` so the helper is reviewed without changing setup routes:

```python
import pytest
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from flowgency.web.setup_security import SetupAccessDenied, SetupBrowserAccess


def _app() -> FastAPI:
    app = FastAPI()
    access = SetupBrowserAccess(secret=b"s" * 32)

    @app.get("/token")
    async def token(request: Request):
        try:
            credential, csrf, issued = access.ensure_browser(request)
        except SetupAccessDenied:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        response = JSONResponse({"csrf": csrf})
        if issued:
            access.set_cookie(response, credential, request)
        return response

    @app.get("/owner")
    async def owner(request: Request):
        return JSONResponse({"owner": access.is_owner(request, app.state.owner)})

    @app.post("/control")
    async def control(request: Request):
        form = await request.form()
        try:
            credential = access.require_http(request, str(form.get("csrf", "")), unsafe=True)
        except SetupAccessDenied:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        return JSONResponse({"credential": credential})

    @app.websocket("/control/ws")
    async def control_ws(websocket: WebSocket):
        try:
            access.require_ws(websocket)
        except SetupAccessDenied:
            await websocket.close(code=1008)
            return
        await websocket.accept()
        await websocket.send_text("connected")

    return app


def test_setup_token_is_bound_to_local_browser_and_csrf():
    with TestClient(_app(), base_url="http://127.0.0.1:8500", client=("127.0.0.1", 10001)) as client:
        csrf = client.get("/token").json()["csrf"]
        assert client.post("/control", data={"csrf": csrf}, headers={"Origin": "http://127.0.0.1:8500"}).status_code == 200
        assert client.post("/control", data={"csrf": "wrong"}, headers={"Origin": "http://127.0.0.1:8500"}).status_code == 403
        assert client.post("/control", data={"csrf": csrf}, headers={"Origin": "http://evil.test"}).status_code == 403
        assert client.post("/control", data={"csrf": csrf}).status_code == 403
        with client.websocket_connect("/control/ws", headers={"Origin": "http://127.0.0.1:8500"}) as ws:
            assert ws.receive_text() == "connected"
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/control/ws", headers={"Origin": "http://evil.test"}) as ws:
                ws.receive_text()
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/control/ws") as ws:
                ws.receive_text()


def test_setup_control_rejects_remote_peer_and_rebound_host():
    app = _app()
    with TestClient(app, base_url="http://127.0.0.1:8500", client=("192.0.2.9", 10002)) as remote:
        assert remote.get("/token").status_code == 403
    with TestClient(app, base_url="http://evil.test:8500", client=("127.0.0.1", 10003)) as rebound:
        assert rebound.get("/token").status_code == 403


def test_second_browser_is_not_session_owner():
    app = _app()
    with TestClient(app, base_url="http://127.0.0.1:8500", client=("127.0.0.1", 10004)) as first:
        first.get("/token")
        app.state.owner = first.cookies.get("flowgency_setup")
        with TestClient(app, base_url="http://127.0.0.1:8500", client=("127.0.0.1", 10005)) as second:
            second.get("/token")
            assert first.get("/owner").json() == {"owner": True}
            assert second.get("/owner").json() == {"owner": False}
```

Do not return the browser credential in the actual setup routes: the `/control` JSON value above is only a test harness assertion target. Verify the real routes never place cookie values in response URLs or error details.

- [ ] **Step 2: Run red.** Run `python -m pytest tests/test_setup_security.py -q`. Expected: import error for `flowgency.web.setup_security`.

- [ ] **Step 3: Implement browser-scoped local checks.** Use `secrets.token_urlsafe(32)` for the random credential, HMAC-SHA256 with the server secret to sign the cookie and derive the form CSRF value, and `hmac.compare_digest` on each check. Build `SetupBrowserAccess` so `ensure_browser()` rejects non-loopback peers and untrusted `Host`, reuses only a correctly signed cookie, and returns a fresh one otherwise. `set_cookie()` uses `httponly=True`, `samesite="strict"`, `path="/"` (the team dashboard needs it), and `secure=request.url.scheme == "https"`. The central validation is:

```python
def _require_local_origin(connection: HTTPConnection, *, unsafe: bool) -> None:
    client = connection.client
    host = connection.headers.get("host", "")
    try:
        parsed = urlsplit("http://" + host)
        local = client is not None and ip_address(client.host).is_loopback
        allowed_host = parsed.hostname in {"127.0.0.1", "::1", "localhost"}
        valid_port = parsed.port is None or 1 <= parsed.port <= 65535
    except ValueError:
        raise SetupAccessDenied("Local setup access required") from None
    if not local or not allowed_host or not valid_port or parsed.username is not None:
        raise SetupAccessDenied("Local setup access required")
    if unsafe:
        scheme = "https" if connection.url.scheme in {"https", "wss"} else "http"
        if connection.headers.get("origin") != f"{scheme}://{host}":
            raise SetupAccessDenied("Setup origin does not match")
```

Reject malformed Host values with paths/fragments as well as userinfo; never rely on forwarded client headers. `require_http` calls this guard, verifies cookie signature, and compares the supplied CSRF for unsafe requests. `require_ws` calls it with `unsafe=True` and verifies cookie signature before accepting; WebSocket does not use a form token. `is_owner` calls the safe GET guard, checks the signed cookie, then compares it with the session owner's credential. Keep the HMAC secret in `app.state.setup_access` for the app's lifetime; a restart invalidates the cookie and destroys the matching in-memory session.

- [ ] **Step 4: Run green.** Run `python -m pytest tests/test_setup_security.py -q`. Expected: valid local requests pass and remote, rebound-host, foreign/missing-origin, wrong-CSRF, missing-cookie, and second-browser cases fail closed.

- [ ] **Step 5: Review and commit local access.** Inspect `git diff --check` and the response-cookie flags, stage only Task 3 files, then commit with `feat(setup): guard connected browser access`.

### Task 4: Own One Reconnectable Setup Session

**Files:**
- Create: `flowgency/web/setup_sessions.py`.
- Modify: `flowgency/app.py` (app-owned manager and lifespan shutdown).
- Create: `tests/_connected_setup_helpers.py` (queue-backed test PTY also used by Task 5).
- Test: `tests/test_setup_sessions.py`.

**Interfaces:**
- Consumes: Task 1's `RuntimeLaunch`, Task 2's `ConnectedProcess`/`start_connected_process`/`ConnectedLaunchError`, existing `RuntimeProcessLifecycle` and `ProcessStopEvidence`, and Task 3's `SetupBrowserAccess`.
- Produces: `SetupSessionConflict`; immutable `SetupSessionSnapshot(state: SetupSessionState, integration_name: str, data_root: Path, output: bytes, truncated: bool, fallback_command: str, exit_code: int | None, message: str)`; `SetupSessionManager.start(owner: str, integration_name: str, launch: RuntimeLaunch, fallback_command: str) -> SetupSessionSnapshot`, `snapshot(owner: str) -> SetupSessionSnapshot | None`, `attach(owner: str) -> tuple[SetupSessionSnapshot, str, asyncio.Queue[bytes | None]]`, `send_input(owner: str, connection_id: str, data: bytes) -> None`, `resize(owner: str, connection_id: str, rows: int, cols: int) -> None`, `consumed(owner: str, connection_id: str, byte_count: int) -> None`, `detach(owner: str, connection_id: str) -> None`, `stop(owner: str) -> ProcessStopEvidence`, `enforce_limits() -> None`, `shutdown() -> None`. All methods except `snapshot` are async. Task 5 consumes the manager through `request.app.state.setup_sessions`.

- [ ] **Step 1: Write a failing fake-process test.** Put the deterministic fake in `tests/_connected_setup_helpers.py` so the route tests can reuse it. Its reader wakes on Stop:

```python
import queue
from flowgency.jobs.processes import ProcessStopEvidence


class FakeProcess:
    pid = 4321

    def __init__(self):
        self.output: queue.Queue[bytes | None] = queue.Queue()
        self.writes: list[bytes] = []
        self.sizes: list[tuple[int, int]] = []
        self.running = True

    def read(self, size: int = 65536) -> bytes:
        chunk = self.output.get()
        if chunk is None:
            raise EOFError
        return chunk

    def write(self, data: bytes) -> None:
        self.writes.append(data)

    def resize(self, rows: int, cols: int) -> None:
        self.sizes.append((rows, cols))

    def alive(self) -> bool:
        return self.running

    def exit_code(self) -> int | None:
        return None if self.running else 0

    def stop(self, lifecycle) -> ProcessStopEvidence:
        self.running = False
        self.output.put(None)
        return ProcessStopEvidence(lifecycle.job_id, lifecycle.generation, True, "stopped")
```

In `tests/test_setup_sessions.py`, import `asyncio`, `Path`, `pytest`, `RuntimeLaunch`, `FakeProcess`, `SetupSessionConflict`, and `SetupSessionManager` and add:

```python
from tests._connected_setup_helpers import FakeProcess


def test_setup_session_reuses_owner_and_transfers_single_writer(tmp_path: Path):
    async def exercise():
        fake = FakeProcess()
        launches = []

        def start(launch):
            launches.append(launch)
            return fake

        manager = SetupSessionManager(process_factory=start)
        launch = RuntimeLaunch(("copilot",), tmp_path, {}, "connected")
        first = await manager.start("owner", "copilot", launch, "copilot -i setup")
        repeated = await manager.start("owner", "copilot", launch, "copilot -i setup")
        assert first.state == repeated.state == "running"
        assert len(launches) == 1
        _, old_id, _ = await manager.attach("owner")
        _, new_id, _ = await manager.attach("owner")
        with pytest.raises(SetupSessionConflict):
            await manager.send_input("owner", old_id, b"no")
        await manager.send_input("owner", new_id, b"yes\r")
        await manager.resize("owner", new_id, 30, 100)
        assert fake.writes == [b"yes\r"]
        assert fake.sizes == [(30, 100)]
        with pytest.raises(SetupSessionConflict):
            await manager.start("other", "copilot", launch, "copilot -i setup")
        await manager.shutdown()
        assert fake.running is False

    asyncio.run(exercise())
```

Add this bounded-output/idle check in the same test file:

```python
def test_setup_session_bounds_output_and_expires_when_idle(tmp_path: Path):
    async def exercise():
        clock = [0.0]
        fake = FakeProcess()
        manager = SetupSessionManager(
            process_factory=lambda launch: fake, now=lambda: clock[0],
            replay_limit=8, client_limit=4,
        )
        await manager.start("owner", "copilot", RuntimeLaunch(("copilot",), tmp_path, {}, "connected"), "fallback")
        _, connection_id, pending = await manager.attach("owner")
        fake.output.put(b"abcdefghij")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 1
        while manager.snapshot("owner").output != b"cdefghij":
            assert loop.time() < deadline
            await asyncio.sleep(0.01)
        assert manager.snapshot("owner").truncated is True
        assert await asyncio.wait_for(pending.get(), timeout=1) is None
        assert fake.running is True
        clock[0] = 3601
        await manager.enforce_limits()
        assert manager.snapshot("owner").state == "stopped"
        await manager.detach("owner", connection_id)
        await manager.shutdown()

    asyncio.run(exercise())
```

For the separate four-hour test, start at `clock[0] == 0`, attach, advance to `14399`, call `send_input` to refresh activity, advance to `14401`, then `enforce_limits()` and assert state is `stopped`. For the unconfirmed-stop test, subclass `FakeProcess.stop` to return `ProcessStopEvidence(lifecycle.job_id, lifecycle.generation, False, "descendants-still-running")`; assert `manager.stop("owner")` reports `confirmed is False` and a new `start` raises `SetupSessionConflict`. These tests use `asyncio.run(exercise())`, not the real CLI.

- [ ] **Step 2: Run red.** Run `python -m pytest tests/test_setup_sessions.py -q`. Expected: import error for `flowgency.web.setup_sessions`.

- [ ] **Step 3: Implement session state and bounded streaming.** Define the public immutable snapshot and a private session holding the process, owner credential, lifecycle generation, reader/timer tasks, `bytearray` replay buffer, subscribers, and monotonic start/activity times. Use `asyncio.Lock` to serialize start/attach/stop, and `asyncio.to_thread` for PTY start/read/write/resize/stop so blocking terminal I/O does not block FastAPI. The snapshot and bounded fanout use:

```python
SetupSessionState = Literal["starting", "running", "exited", "stopped", "failed"]


@dataclass(frozen=True)
class SetupSessionSnapshot:
    state: SetupSessionState
    integration_name: str
    data_root: Path
    output: bytes
    truncated: bool
    fallback_command: str
    exit_code: int | None
    message: str


def _append_output(self, chunk: bytes) -> None:
    self._output.extend(chunk)
    if len(self._output) > self.replay_limit:
        self._output = self._output[-self.replay_limit:]
        self._truncated = True
    self._last_activity = self._now()
    for subscriber in tuple(self._subscribers.values()):
        if subscriber.pending_bytes + len(chunk) > self.client_limit:
            subscriber.queue.put_nowait(None)
            self._subscribers.pop(subscriber.connection_id, None)
        else:
            subscriber.pending_bytes += len(chunk)
            subscriber.queue.put_nowait(chunk)
```

The WebSocket consumer calls `manager.consumed(owner, connection_id, len(chunk)) -> None` after each successful `send_bytes` so a fast client does not accumulate pending bytes. Define that method in this task along with `detach(owner, connection_id)`, which removes only the named subscriber and clears input ownership only if it still belongs to that connection. At `attach`, lock, capture the current snapshot, register the queue, and revoke the previous writer before releasing the lock; send snapshot replay before draining new queue events. Reject input over 64 KiB and resize outside 2..200 rows or 20..400 columns. Treat an EOF as terminal exit, call `stop()` to confirm the entire process tree, preserve final output and exit code, and mark a failed/unconfirmed stop as `failed` rather than freeing the active slot. Only a confirmed stopped or exited session can be replaced; a different root while running raises `SetupSessionConflict`. On cancellation during startup, await or shield the pending PTY creation and clean it up before releasing the lock.

Use one loop task to call `enforce_limits()` periodically; compare `now - last_activity > 3600` and `now - started > 14400` before invoking Stop. Use a launch lock to serialize competing starts and a separate state lock for fanout/ownership: reserve a `starting` slot, release the state lock during `asyncio.to_thread(process_factory, launch)`, then publish the process under the state lock. On `ConnectedLaunchError(cleanup_confirmed=False)`, retain a blocked session state; otherwise a confirmed failed start may fall back externally. On cancellation, shield the pending startup, await its result, stop it, and leave the slot blocked if stop cannot be confirmed. Never await PTY I/O while holding the state lock. Set `app.state.setup_access = SetupBrowserAccess()` once per server process and install a fresh manager in app lifespan before requests; call `await app.state.setup_sessions.shutdown()` in `finally`. Reuse the app's single worker and do not persist output or credentials on disk.

- [ ] **Step 4: Run focused session and existing startup tests to verify green.** Run `python -m pytest tests/test_setup_sessions.py tests/test_server.py -q`. Expected: new lifecycle cases and preexisting setup/startup tests pass; an interrupted PTY launch never leaves a duplicate process.

- [ ] **Step 5: Review and commit the manager.** Check `git diff --check`, review lock boundaries and shutdown cleanup, stage only Task 4 files, and commit with `feat(setup): own reconnectable terminal session`.

### Task 5: Serve The Connected Setup Session

**Files:**
- Modify: `flowgency/web/setup_flow.py` (integration availability).
- Modify: `flowgency/web/routes/admin_teams.py` (local launch, form token, unchanged readiness).
- Create: `flowgency/web/routes/setup_terminal.py` (state, view, Stop, WebSocket).
- Modify: `flowgency/web/routes/__init__.py`, `flowgency/app.py` (router registration before the team catch-all).
- Test: `tests/test_setup_flow.py`, `tests/test_server.py`, `tests/test_team_settings.py`.

**Interfaces:**
- Consumes: `CopilotIntegration.connected_setup_launch`, `connected_process_available`, `SetupBrowserAccess`, `SetupSessionManager`, and existing `inspect_setup_status`/`_setup_status_with_fresh_services`.
- Produces: `GET /setup/session` (owner-only terminal view even when config is ready); `GET /setup/session/state` (state, exit code, truncation, message only); `POST /setup/session/stop` (owner/CSRF, 303 after confirmed stop); `WS /setup/session/ws` (initial state JSON, binary output, input/resize JSON). `/setup/status` retains its exact JSON contract and remains the only readiness source.

- [ ] **Step 1: Write failing selector, launch and WebSocket tests.** In `tests/test_setup_flow.py`, extend the existing `_Integration` fixture with a connected flag defaulting to false and add:

```python
def test_launchable_integrations_keeps_copilot_when_only_pty_exists(tmp_path: Path, monkeypatch):
    integration = _Integration("copilot", "GitHub Copilot", 5, interactive=False, detected=False)
    monkeypatch.setattr(integration, "connected_setup_available", lambda: True)
    monkeypatch.setattr("flowgency.web.setup_flow.connected_process_available", lambda: True)
    assert launchable_integrations({"copilot": integration}, tmp_path) == (integration,)
```

In `tests/test_server.py`, add a connected test double alongside `_LaunchIntegration`:

```python
from flowgency.integrations.models import RuntimeLaunch
from flowgency.web.setup_sessions import SetupSessionManager
from tests._connected_setup_helpers import FakeProcess


class _ConnectedLaunchIntegration(_LaunchIntegration):
    def __init__(self):
        super().__init__()
        self.connected_requests = []

    def connected_setup_available(self) -> bool:
        return True

    def connected_setup_launch(self, request) -> RuntimeLaunch:
        self.connected_requests.append(request)
        return RuntimeLaunch(("copilot", "-i", request.prompt), request.data_root, {}, "connected")


def test_connected_launch_is_local_idempotent_and_streams_output(tmp_path, monkeypatch):
    config_path = _configure_missing_config(tmp_path, monkeypatch)
    root = tmp_path / "Flowgency"
    integration = _ConnectedLaunchIntegration()
    monkeypatch.setattr(
        "flowgency.web.routes.admin_teams.launchable_integrations",
        lambda integrations, data_root: (integration,),
    )
    monkeypatch.setattr("flowgency.web.routes.admin_teams.connected_process_available", lambda: True)
    process = FakeProcess()
    launches = []
    def start(launch):
        launches.append(launch)
        return process
    manager = SetupSessionManager(process_factory=start)
    monkeypatch.setattr(app_mod, "SetupSessionManager", lambda: manager)
    with TestClient(app_mod.app, base_url="http://127.0.0.1:8500", client=("127.0.0.1", 50001)) as client:
        page = client.get("/setup")
        csrf = re.search(r'name="setup_csrf" value="([^"]+)"', page.text).group(1)
        form = {"data_root": str(root), "integration": "copilot", "setup_csrf": csrf}
        headers = {"Origin": "http://127.0.0.1:8500"}
        first = client.post("/setup/launch", data=form, headers=headers, follow_redirects=False)
        assert first.status_code == 303 and first.headers["location"] == "/setup/session"
        repeat = client.post("/setup/launch", data=form, headers=headers, follow_redirects=False)
        assert repeat.status_code == 303 and repeat.headers["location"] == "/setup/session"
        assert len(integration.connected_requests) == 2
        assert len(launches) == 1
        assert integration.requests == []
        assert not config_path.exists()
        assert client.get("/setup/session/state").json()["state"] == "running"
        with client.websocket_connect("ws://127.0.0.1:8500/setup/session/ws", headers=headers) as ws:
            assert ws.receive_json()["state"] == "running"
            process.output.put(b"Hello from Copilot")
            assert ws.receive_bytes() == b"Hello from Copilot"
            ws.send_json({"type": "input", "data": "yes\r"})
```

The second POST should call `connected_setup_launch` twice to validate the same request but the manager's `process_factory` only once; count calls there too. Add route tests for a remote peer with a missing root (403, root not created, no launch even when the form carries a token copied from a local browser), an invalid CSRF/Origin, a foreign cookie on state/WS (403/1008), malformed/overlong WS messages (1003/1009), and failure to confirm PTY cleanup (409; no fallback launch). A generic/unexpected connected spawn error also returns an error without an external fallback; only `ConnectedLaunchError(cleanup_confirmed=True)` permits a fallback launch. Cover a WS connection attached during `starting` when a clean launch failure removes the session: its `None` close marker must not dereference a missing snapshot or emit an ASGI task exception. Update all six existing `/setup/launch` POST tests in `tests/test_server.py` and `test_setup_launch_preserves_existing_bootstrap_config` in `tests/test_team_settings.py` to GET `/setup` first with a direct-loopback TestClient, then send the signed cookie, form `setup_csrf`, and matching `Origin`; keep their existing invalid-integration/relative-root error assertions. After writing a ready config using existing `_materialize_ready_config`, assert `/setup/status` still returns exactly `{"state":"ready","redirect":"/"}` while `GET /setup/session` returns 200 for the owner; `POST /setup/session/stop` then returns 303 and confirms process Stop. Repeat one existing external fallback test through the authenticated local launch path.

- [ ] **Step 2: Run red.** Run `python -m pytest tests/test_setup_flow.py::test_launchable_integrations_keeps_copilot_when_only_pty_exists tests/test_server.py::test_connected_launch_is_local_idempotent_and_streams_output -q`. Expected: missing connected selector and session routes.

- [ ] **Step 3: Wire connected launch without changing readiness.** In `setup_flow.launchable_integrations`, accept an integration if the old `interactive_setup_available()` is true or its connected capability and the host PTY check both pass; retain existing detection sort order. Use `getattr(integration, "connected_setup_available", lambda: False)` for existing duck-typed test integrations. In `_setup_response`, add `connected`, `session_view`, and `setup_csrf` template values; on the initial local `GET /setup`, call `app.state.setup_access.ensure_browser`, set its cookie on the returned response, and place the token in the form. If that same owner has a session and config is not ready, redirect `GET /setup` to `/setup/session`; once config is ready, keep its existing redirect to `/`. Remote viewers may still see the external setup form, but cannot start an agent. In `POST /setup/launch`, parse the form and check `require_http(request, form.get("setup_csrf"), unsafe=True)` for BOTH external and connected modes before the readiness redirect, `_setup_integrations`, data-root validation, or `prepare_writable_directory`. A missing/invalid local credential returns 403 without probing folders or launching anything. Only after this guard, keep the existing readiness redirect, integration selection/invalid-choice response and root-validation errors for local clients:

```python
form = await request.form()
try:
    owner = request.app.state.setup_access.require_http(
        request, str(form.get("setup_csrf", "")), unsafe=True,
    )
except SetupAccessDenied:
    return JSONResponse({"error": "Local setup access required."}, status_code=403)

integration = launchable_by_name[requested_integration]
connected = (
    requested_integration == "copilot"
    and getattr(integration, "connected_setup_available", lambda: False)()
    and connected_process_available()
)
resolved_data_root = prepare_writable_directory(Path(data_root_value), label="Flowgency data root")
setup_request = InteractiveSetupRequest(
    data_root=resolved_data_root,
    config_path=services.config_path.resolve(),
    prompt=build_setup_prompt(resolved_data_root, services.config_path, selected_integration=requested_integration),
)
if connected:
    launch = integration.connected_setup_launch(setup_request)
    fallback = integration.interactive_setup_fallback_command(setup_request)
    await request.app.state.setup_sessions.start(owner, integration.name, launch, fallback)
    return RedirectResponse("/setup/session", status_code=303)
```

Keep the existing path validation/error rendering around the excerpt above and the external `.launch_interactive_setup` path for other integrations/unsupported PTYs (after the same local-access guard). Catch `ConnectedLaunchError`: only when `cleanup_confirmed` is true may the route try the existing external launcher and display its fallback command; otherwise return 409 naming the unconfirmed stop and leave the slot blocked. Map any other unexpected spawn exception to a safe error without a fallback launch, since the manager must retain a blocked slot without proof of cleanup. Map `SetupSessionConflict` to 409 with the existing session link; never replace a running session for a different root or browser. Import/export `setup_terminal_router` through `flowgency/web/routes/__init__.py` and include it in `app.py` before `/{team}/`.

- [ ] **Step 4: Implement owner-only state, terminal view, Stop and WebSocket.** Provide a route-local HTTP guard mapping `SetupAccessDenied` to 403:

```python
def _require_setup_owner(request: Request, csrf: str | None = None, *, unsafe: bool = False) -> str:
    try:
        return request.app.state.setup_access.require_http(request, csrf, unsafe=unsafe)
    except SetupAccessDenied as exc:
        raise HTTPException(status_code=403, detail="Local setup access required") from exc
```

`GET /setup/session` uses `_require_setup_owner(request)` and `manager.snapshot(owner)`; when no session exists, redirect to `/setup` if config is incomplete or `/` if ready. When a session exists, obtain the owner form token through `ensure_browser(request)` and call `_setup_response` with `waiting=True`, `connected=True`, `session_view=True`, `data_root_value=str(snapshot.data_root)`, `selected_integration=snapshot.integration_name`, `selected_integration_name=services.integrations[snapshot.integration_name].display_name`, `fallback_command=snapshot.fallback_command`, and `setup_csrf=csrf`. This renders even when configuration is ready; use `_setup_status_with_fresh_services` so the displayed readiness state is current. `GET /setup/session/state` returns state, exit code, truncation and message, never output or credential. For Stop, require the form token, then redirect based on canonical status:

```python
@router.post("/setup/session/stop")
async def stop_setup_session(request: Request, services: FlowgencyServices = Depends(get_services)):
    form = await request.form()
    owner = _require_setup_owner(request, str(form.get("setup_csrf", "")), unsafe=True)
    evidence = await request.app.state.setup_sessions.stop(owner)
    if not evidence.confirmed:
        return JSONResponse({"error": evidence.reason}, status_code=409)
    status = inspect_setup_status(services.config_store)
    return RedirectResponse("/" if status.state == "ready" else "/setup", status_code=303)
```

For `WS /setup/session/ws`, reject invalid Origin/cookie before `accept()` (close 1008), then `attach(owner)` and send a JSON state frame containing state, truncation and exit code, followed by `snapshot.output` in 16 KiB binary frames. Use these control and output loops (import `json` and `WebSocketDisconnect`):

```python
async def _receive_controls(websocket: WebSocket, manager: SetupSessionManager, owner: str, connection_id: str) -> None:
    while True:
        raw = await websocket.receive_text()
        if len(raw.encode("utf-8")) > 65536:
            await websocket.close(code=1009)
            return
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            await websocket.close(code=1003)
            return
        if not isinstance(event, dict):
            await websocket.close(code=1003)
            return
        try:
            if event.get("type") == "input" and isinstance(event.get("data"), str):
                await manager.send_input(owner, connection_id, event["data"].encode("utf-8"))
            elif (event.get("type") == "resize"
                  and type(event.get("rows")) is int and type(event.get("cols")) is int):
                await manager.resize(owner, connection_id, event["rows"], event["cols"])
            else:
                await websocket.close(code=1003)
                return
        except (SetupSessionConflict, ValueError):
            await websocket.close(code=1008)
            return


async def _send_output(websocket: WebSocket, manager: SetupSessionManager, owner: str,
                       connection_id: str, pending: asyncio.Queue[bytes | None]) -> None:
    while True:
        chunk = await pending.get()
        if chunk is None:
            state = manager.snapshot(owner)
            if state is None:
                await websocket.send_json({"type": "state", "state": "unavailable", "message": "Setup session is no longer available."})
                return
            await websocket.send_json({"type": "state", "state": state.state, "message": state.message})
            return
        await websocket.send_bytes(chunk)
        await manager.consumed(owner, connection_id, len(chunk))
```

In the WebSocket route, start those two tasks after `accept()`, the initial state JSON and replay; `await asyncio.wait({sender, receiver}, return_when=asyncio.FIRST_COMPLETED)`, cancel/gather pending tasks, and `await manager.detach(owner, connection_id)` in `finally`. Catch `WebSocketDisconnect` at the handler boundary so a closed tab only detaches. A disconnect or redirect never stops the PTY. Do not interpolate PTY text into HTML.

- [ ] **Step 5: Run focused server regressions to verify green.** Run `python -m pytest tests/test_setup_flow.py tests/test_server.py tests/test_team_settings.py tests/test_interactive_setup.py -q`. Expected: local connected session controls and bootstrap tests pass, existing external fallback tests remain green under the local guard, and readiness status remains configuration-derived.

- [ ] **Step 6: Review and commit the routes.** Check `git diff --check`, verify security checks precede filesystem preparation, stage only Task 5 files and commit with `feat(setup): serve connected copilot session`.

### Task 6: Render The Terminal With Local Assets

**Files:**
- Create: `tools/setup-terminal.js` (browser terminal source).
- Modify: `package.json`, `package-lock.json` (pinned terminal/addon/bundler and build script).
- Create (generated): `flowgency/static/setup-terminal.js`, `flowgency/static/setup-terminal.css`, plus generated legal-notice assets if emitted.
- Modify: `flowgency/templates/setup.html`, `flowgency/static/tailwind.css` (generated when setup classes change).
- Modify: `tests/ui/server.py`, `tests/ui/setup.spec.ts` (isolated first-run fixture and browser flow).
- Test: `tests/test_setup_assets.py` (wheel contents).

**Interfaces:**
- Consumes: Task 5's `connected`, `session_view`, and `setup_csrf` template values, `/setup/session/ws` binary output and JSON controls, `/setup/session/state`, `/setup/status`, and `/setup/session/stop`.
- Produces: bundled `/static/setup-terminal.js` and `/static/setup-terminal.css`, an `#setup-terminal` element with a fixed responsive height, status element `#terminal-connection`, and a reconnecting browser terminal. Task 7 uses the same `/setup/session` view after readiness.

- [ ] **Step 1: Write failing asset and browser checks.** Add a wheel assertion in `tests/test_setup_assets.py` using its existing `built_wheel` fixture:

```python
def test_wheel_contains_connected_terminal_assets(built_wheel: Path):
        with ZipFile(built_wheel) as archive:
                for name in ("setup-terminal.js", "setup-terminal.css"):
                        source = REPO_ROOT / "flowgency" / "static" / name
                        assert archive.read(f"flowgency/static/{name}") == source.read_bytes()
```

Extend the existing UI fixture in `tests/ui/server.py` with a `connected-setup` reset option. On reset, call `await app.state.setup_sessions.shutdown()` before replacing it with `SetupSessionManager(process_factory=lambda launch: FakeProcess())`; remove only the fixture runtime's `config.yaml`, use a test integration that returns a fixed `RuntimeLaunch` and fallback command, then rebuild `app.state.services` from the fixture config path. The UI server runs on Windows, where connected mode is deliberately unavailable: override `connected_process_available` in both `flowgency.web.setup_flow` and `flowgency.web.routes.admin_teams` **only for this fixture**, and restore the real functions on reset to `default`. This makes browser tests exercise the fake session, never a real Windows PTY. Add a test-only `GET /__ui/setup/meta` returning `{"data_root": str(runtime / "flowgency-data")}` and `POST /__ui/setup/ready` that atomically writes the existing complete fixture config through `_write_runtime_config` and refreshes services. Resetting to `default` must restore real integrations and a fresh non-fake session manager so tests remain order-independent. This fixture never invokes a real Copilot CLI.

Add a Playwright test to `tests/ui/setup.spec.ts` using the existing `installBasePageSetup` setup and `assertNoConsoleErrors`/`assertNoTailwindCdnRequests` helpers:

```typescript
test('connected setup terminal survives reload and status fetch failure', async ({ page, request }) => {
    await request.post('/__ui/reset', { data: { fixture: 'connected-setup' } });
    const { data_root: dataRoot } = await (await request.get('/__ui/setup/meta')).json();
    await page.goto('/setup');
    await page.getByLabel('Flowgency data root').fill(dataRoot);
    await page.getByRole('button', { name: 'Continue in GitHub Copilot' }).click();
    await expect(page.locator('#setup-terminal .xterm-screen')).toBeVisible();
    await page.route('**/setup/status', (route) => route.abort());
    await expect(page.locator('#status-message')).toContainText('Retrying');
    await page.unroute('**/setup/status');
    await page.reload();
    await expect(page.locator('#setup-terminal .xterm-screen')).toBeVisible();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1);
    expect(overflow).toBe(false);
    await assertNoConsoleErrors(page);
    await assertNoTailwindCdnRequests(page);
});
```

Use the four configured desktop/mobile, light/dark projects to check container bounds and status/fallback text. Add a browser check that a truncated replay displays a warning and does not pretend the missing scrollback is present; do not rely on raw PTY output as HTML.

- [ ] **Step 2: Run red.** Run `python -m pytest tests/test_setup_assets.py::test_wheel_contains_connected_terminal_assets -q` and `npm run test:ui -- tests/ui/setup.spec.ts --project desktop-light`. Expected: missing terminal assets and fixture/terminal elements.

- [ ] **Step 3: Bundle xterm locally and implement browser I/O.** Install pinned dev dependencies with `npm install --save-dev --save-exact @xterm/xterm@6.0.0 @xterm/addon-fit@0.11.0 esbuild@0.28.2`. Add the following `package.json` script and commit its updated lockfile:

```json
"build:terminal": "esbuild tools/setup-terminal.js --bundle --format=iife --target=es2020 --minify --legal-comments=external --outfile=flowgency/static/setup-terminal.js"
```

At the top of `tools/setup-terminal.js`, import `Terminal`, `FitAddon`, and `@xterm/xterm/css/xterm.css`. Esbuild emits a sibling `setup-terminal.css`; stage that CSS, the JS and emitted `.LEGAL.txt` notices. The browser code opens a real WebSocket at the current host without a credential in the URL, uses the cookie automatically, and forwards only two control shapes:

```javascript
const terminal = new Terminal({ fontFamily: 'JetBrains Mono', fontSize: 13, convertEol: true });
const fit = new FitAddon();
terminal.loadAddon(fit);
terminal.open(document.querySelector('#setup-terminal'));
fit.fit();
const url = `${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/setup/session/ws`;
const socket = new WebSocket(url);
socket.binaryType = 'arraybuffer';
terminal.onData((data) => {
    if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: 'input', data }));
});
terminal.onResize(({ rows, cols }) => {
    if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: 'resize', rows, cols }));
});
socket.addEventListener('message', ({ data }) => {
    if (typeof data !== 'string') {
        terminal.write(new Uint8Array(data));
        return;
    }
    const state = JSON.parse(data);
    document.querySelector('#terminal-connection').textContent = state.truncated
        ? 'Earlier terminal output is unavailable.' : state.state;
});
```

Wrap the excerpt in a `connect()` function with a bounded retry timer on transport loss, clear/replay on reconnect, a `ResizeObserver` and `document.fonts.ready`-driven fit, `disableStdin` when disconnected/exited, and a terminal-state check that stops retrying on policy close 1008 or missing session (server restart). Display connecting, connected, reconnecting, exited, and stopped text via `textContent`; do not install web-link/clipboard/window-action addons or allow terminal escape sequences to trigger navigation or clipboard writes. The browser never creates CLI argv or reads credentials.

- [ ] **Step 4: Render a stable setup view and preserve readiness polling.** In `setup.html`, include local terminal CSS/JS only when `connected`; reuse its current data-root/integration summary as a compact header, put the terminal in a fixed responsive height (`h-96 md:h-[34rem]`, width constrained to its parent), and move the fallback command into a `<details>` element. Show the essential warning that interactive setup has the server user's file/tool access, an aria-live connection state, and a CSRF-protected Stop form. In the `waiting and connected` branch render:

```html
<p class="text-sm text-amber-900">Interactive setup can use this computer's server-user access to files and tools.</p>
<div id="setup-terminal" class="h-96 w-full min-w-0 md:h-[34rem]" data-session-view="{{ 'true' if session_view else 'false' }}"></div>
<p id="terminal-connection" role="status" aria-live="polite" class="text-sm text-gray-700">Connecting</p>
<details>
    <summary>Fallback command</summary>
    <textarea id="fallback-command" readonly>{{ fallback_command }}</textarea>
</details>
<form action="/setup/session/stop" method="post">
    <input type="hidden" name="setup_csrf" value="{{ setup_csrf }}">
    <button type="submit" title="Stop setup session">Stop</button>
</form>
```

Use the existing Tailwind focus/border classes for the summary, textarea and Stop button. Include `<input type="hidden" name="setup_csrf" value="{{ setup_csrf }}">` in the initial setup form and relaunch form. Keep the external waiting UI unchanged. In the existing 1500 ms status poll use:

```javascript
if (payload.state === 'ready' && payload.redirect && !sessionView) {
    window.location.assign(payload.redirect);
    return;
}
statusMessage.textContent = payload.message || defaultMessages[payload.state] || '';
```

Read `sessionView` from the rendered `data-session-view` attribute. On a status fetch failure, set `statusMessage.textContent = 'Status check failed. Retrying.'` and keep polling without hiding or disconnecting the terminal. Rebuild Tailwind with `npm run build:css`.

- [ ] **Step 5: Run focused checks to verify green.** Run `npm run build:terminal`, `npm run build:css`, `python -m pytest tests/test_setup_assets.py -q`, and `npm run test:ui -- tests/ui/setup.spec.ts` from the worktree root. The current `playwright.config.ts` invokes `.venv\Scripts\python.exe`; if the ignored worktree has no local venv, first run `python -m venv .venv` and `.venv\Scripts\python.exe -m pip install -e ".[test]"` there. Expected: wheel assets match local bytes, every browser project has a nonblank terminal and no viewport overflow, console errors, or remote Tailwind dependency.

- [ ] **Step 6: Review and commit the frontend.** Verify actual terminal text is never interpolated, run `git diff --check`, and stage only Task 6 files (including generated CSS/JS/legal notices and lockfile). Commit with `feat(setup): embed local copilot terminal`.

### Task 7: Keep A Background Setup Session Visible On The Dashboard

**Files:**
- Modify: `flowgency/app.py` (owner-only dashboard context).
- Modify: `flowgency/templates/home.html` (small inline session status/link/Stop form).
- Test: `tests/test_dashboard.py`, `tests/ui/setup.spec.ts`.

**Interfaces:**
- Consumes: `SetupBrowserAccess.require_http`, `SetupBrowserAccess.ensure_browser`, `SetupSessionManager.snapshot`, Task 5's owner-only `/setup/session` and `/setup/session/stop`, and Task 6's `session_view` no-redirect behavior.
- Produces: `setup_session` context (running or unconfirmed-stop status, link, CSRF) only for the initiating local browser on the team home page. No transcript, CLI argv, or credential is inserted into the dashboard.

- [ ] **Step 1: Write a failing dashboard test.** In `tests/test_dashboard.py`, seed the existing ready team and use this local-owner/other-browser test. Import `replace` from `dataclasses`:

```python
from dataclasses import replace
from starlette.requests import Request
from flowgency.web.setup_sessions import SetupSessionSnapshot


def test_dashboard_shows_setup_session_only_for_its_local_owner(monkeypatch, tmp_path, raw_config):
    _seed_dashboard_app(monkeypatch, tmp_path, raw_config)
    with TestClient(app_mod.app, base_url="http://127.0.0.1:8500", client=("127.0.0.1", 50001)) as client:
        scope = {
            "type": "http", "scheme": "http", "path": "/setup",
            "client": ("127.0.0.1", 50001), "server": ("127.0.0.1", 8500),
            "headers": [(b"host", b"127.0.0.1:8500")],
        }
        owner, csrf, _ = app_mod.app.state.setup_access.ensure_browser(Request(scope))
        current = SetupSessionSnapshot("running", "copilot", tmp_path, b"", False, "fallback", None, "")

        class OwnedSession:
            def snapshot(self, claimant):
                return current if claimant == owner else None

            async def shutdown(self):
                return None

        monkeypatch.setattr(app_mod.app.state, "setup_sessions", OwnedSession())
        client.cookies.set("flowgency_setup", owner)
        response = client.get("/newsletter/")
        assert response.status_code == 200
        assert 'href="/setup/session"' in response.text
        assert 'action="/setup/session/stop"' in response.text
        assert owner not in response.text

        other = TestClient(app_mod.app, base_url="http://127.0.0.1:8500", client=("127.0.0.1", 50002))
        remote = TestClient(app_mod.app, base_url="http://127.0.0.1:8500", client=("192.0.2.9", 50003))
        assert 'href="/setup/session"' not in other.get("/newsletter/").text
        assert 'href="/setup/session"' not in remote.get("/newsletter/").text
        current = replace(current, state="failed", message="Could not confirm process exit")
        assert "Setup session needs attention" in client.get("/newsletter/").text
```

In `tests/ui/setup.spec.ts`, reset the `connected-setup` fixture, launch through the form, then call `POST /__ui/setup/ready`. Assert the browser moves to `/newsletter/` while its fake PTY is still alive, the session link is visible, following it stays on `/setup/session` even after the next ready status response, and Stop removes the dashboard indicator. Open a second Playwright browser context with no setup cookie and assert it cannot see the indicator or reach `/setup/session`. This test covers the explicit decision to redirect automatically but let Copilot finish in the background.

- [ ] **Step 2: Run red.** Run `python -m pytest tests/test_dashboard.py -k setup_session -q` and `npm run test:ui -- tests/ui/setup.spec.ts --project desktop-light`. Expected: the dashboard context/link is absent although the owner session is running.

- [ ] **Step 3: Add an owner-only inline dashboard status.** In `home(request, team)` in `app.py`, guard the optional session lookup without changing any existing fleet/workflow data:

```python
setup_session = None
access = request.app.state.setup_access
try:
    owner = access.require_http(request)
except SetupAccessDenied:
    owner = None
if owner is not None:
    session = request.app.state.setup_sessions.snapshot(owner)
    if session is not None and session.state in {"running", "failed"}:
        credential, csrf, _ = access.ensure_browser(request)
        if credential == owner:
            setup_session = {"href": "/setup/session", "csrf": csrf,
                             "state": session.state, "message": session.message}
```

Add `"setup_session": setup_session` to the existing `TemplateResponse` context. At the top of `home.html` content, insert one compact full-width inline band rather than a nested card:

```html
{% if setup_session %}
<div class="mb-3 flex flex-wrap items-center gap-3 border-l-4 border-emerald-600 bg-emerald-50 px-3 py-2 text-sm text-gray-900" data-setup-session>
    <span class="font-semibold">{{ "Setup session running" if setup_session.state == "running" else "Setup session needs attention" }}</span>
  <a href="{{ setup_session.href }}" class="underline">View terminal</a>
  <form action="/setup/session/stop" method="post" class="ml-auto">
    <input type="hidden" name="setup_csrf" value="{{ setup_session.csrf }}">
    <button type="submit" title="Stop setup session" aria-label="Stop setup session">Stop</button>
  </form>
</div>
{% endif %}
```

Match the existing theme tokens for dark mode and mobile wrapping without changing other dashboard sections. Keep this indicator out of the agent work queue: connected first-run setup is not a team job. When the process exits naturally or is confirmed stopped, the manager reports non-running and the indicator disappears; an unconfirmed Stop keeps the attention link visible. The same browser can still inspect a retained exit/error state by opening its session view until session replacement or server restart.

- [ ] **Step 4: Run focused dashboard/browser checks to verify green.** Run `python -m pytest tests/test_dashboard.py tests/test_server.py -q`, `npm run build:css`, and `npm run test:ui -- tests/ui/setup.spec.ts`. Expected: only the initiating browser sees the indicator, it can return and Stop, and the ready-session page does not auto-redirect again.

- [ ] **Step 5: Review and commit dashboard integration.** Check `git diff --check`, stage only Task 7 files (including regenerated `flowgency/static/tailwind.css` when changed), then commit with `feat(setup): surface active session on dashboard`.

### Task 8: Document, Smoke-Test And Review The Whole Feature

**Files:**
- Modify: `kb/getting-started.md` (first-run flow and security boundary).
- Modify: `README.md` (short first-run handoff).
- Verify: `tests/test_interactive_setup.py`, `tests/test_connected_process.py`, `tests/test_setup_security.py`, `tests/test_setup_sessions.py`, `tests/test_server.py`, `tests/test_dashboard.py`, `tests/test_setup_assets.py`, `tests/ui/setup.spec.ts`.

**Interfaces:**
- Consumes: the complete connected flow from Tasks 1-7; creates no new runtime API.
- Produces: accurate operator instructions, platform verification evidence, and a reviewed branch ready for the integration sequence required by `AGENTS.md`.

- [ ] **Step 1: Write the user-facing guidance.** Replace the first-run paragraph in `kb/getting-started.md` with copy that includes these exact operational facts, and add a short Copilot sentence after the first-run paragraph in `README.md`:

```markdown
Open setup from a browser on the same computer as Flowgency. On supported
POSIX hosts, GitHub Copilot setup runs in the page's terminal; refresh or
reopen the page to reconnect. When the configuration is ready, Flowgency opens
the dashboard while Copilot may continue running. Use the dashboard's Setup
session link to return or Stop.
The setup CLI has the server user's access to files and tools; configured
agent sandbox permissions do not apply before the first team exists. Windows
continues to launch Copilot in a separate console, with a copyable fallback
command; it does not offer connected setup. On POSIX, use the external terminal
if the PTY cannot start. Closing the browser tab does not stop a connected
session, but a server restart ends its in-memory connection and transcript.
```

Keep the existing data-root, packaged skill, project-workspace question, and atomic-write instructions in both documents. No claims of remote browser access, persisted terminal transcripts, or general connected agent runs.

- [ ] **Step 2: Validate the guide and commit documentation.** Run `git diff --check` and `python -m pytest tests/test_setup_assets.py tests/test_server.py -q`. Expected: no formatting errors, packaged assets still present, and first-run route contract still passes. Stage only `kb/getting-started.md` and `README.md`; commit with `docs(setup): explain connected first-run session`.

- [ ] **Step 3: Run the full automated gates from the feature worktree.** Run `python -m pytest tests/ -q`, `npm ci`, `npm run build:terminal`, `npm run build:css`, and `npm run test:ui -- tests/ui/setup.spec.ts`. Run any repository-wide Playwright UI suite required for frontend changes with `npm run test:ui`. Expected: green full Python and UI suites; generated terminal JS/CSS and Tailwind CSS match what is committed, not only local untracked build artifacts. Run `git diff --check` and `git status --short` afterward; generated runtime data and lock files under the fixture/runtime trees must not be staged.

- [ ] **Step 4: Perform platform smoke gates.** On Windows, run `python -m pytest tests/test_connected_process.py tests/test_runtime_process_lifecycle.py tests/test_interactive_setup.py -q`. From the feature worktree set `$env:FLOWGENCY_CONFIG="$env:TEMP\flowgency-connected-smoke.yaml"` and start `python -m flowgency.cli serve --host 127.0.0.1 --port 8501` (use another free port if needed). Open `http://127.0.0.1:8501/setup` locally and select Copilot with a throwaway data root: confirm it opens the existing separate console, the fallback command is copyable, and no connected PTY session is created; close the console after a harmless reply. Do not claim an in-page Windows terminal. On a POSIX host with `/proc` support, run the native PTY and lifecycle tests, set `FLOWGENCY_CONFIG` to a throwaway absolute path, and start a real Copilot setup session in the page. Confirm an actual prompt appears, type a harmless reply, refresh to reconnect, and Stop; check that the process tree exits and that no unintended configuration was written. For a second throwaway POSIX session, gracefully restart the server and confirm the process tree stops and the page no longer offers a stale reconnect. If a real Copilot CLI is unavailable on the POSIX host, retain the external launch there until the smoke test passes.

For the live remote guard, a client from a separate OS network stack is sufficient when the server logs a non-loopback peer, its setup launch returns 403, and the selected root remains absent. A separate physical device is not required.

- [ ] **Step 5: Review before integration.** Review the whole branch against `docs/superpowers/specs/2026-09-23-connected-copilot-setup-terminal-design.md`, especially the exact headless flags/policy, one owner, configuration readiness, port/Origin checks, Windows connected capability always disabled, unchanged external launch, POSIX group containment, no persisted transcript, and automatic redirect with background session. Repair any finding in its owning task and rerun its focused tests plus the full suites. Once review and tests pass, follow `AGENTS.md`: fast-forward `master` only (rebase feature and rerun tests first if `master` advanced), preserve/stash and restore any uncommitted master changes, rerun the full Python suite on master, push both `master` and the feature branch to origin, remove the worktree and prune stale entries, and retain the feature branch. Never fold unrelated runtime-local files or user changes into this feature. After integration, start `python -m flowgency.cli serve --host 127.0.0.1 --port 8501` from `master` (or another free port) and give the user its URL; the test server started from the removed worktree is not the final trial environment.