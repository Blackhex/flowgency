# Native Atomic Windows ConPTY Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the measured-failing helper-inheritance Windows setup backend with native ConPTY whose actual CLI is assigned atomically to the server-owned Job before execution.

**Architecture:** Create the native pseudoconsole and a kill-on-close Job in the parent server. Create the real CLI suspended with both PSEUDOCONSOLE and JOB_LIST attributes, verify membership, then resume. Keep the existing ConnectedProcess, session manager, browser security and frontend; confirm cleanup only after empty Job and finished native I/O teardown.

**Tech Stack:** Python 3.11+, ctypes Win32 APIs, pywin32 Job/accounting, Windows Uvicorn WebSockets, pytest and the existing Playwright gate.

## Global Constraints

- Implement the approved `docs/superpowers/specs/2026-10-01-windows-atomic-conpty-design.md`; the 2026-09-28 helper design is historical.
- Stay in `C:/Projekty/Flowgency/.worktrees/windows-connected-setup`, branch `feature/windows-connected-setup`; preserve main checkout and runtime-local data.
- The actual CLI receives `PROC_THREAD_ATTRIBUTE_JOB_LIST` and `PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE` with `CREATE_SUSPENDED | EXTENDED_STARTUPINFO_PRESENT | CREATE_UNICODE_ENVIRONMENT`. Verify membership before resume, never execute first and assign afterward.
- The parent retains a non-inheritable `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` Job with neither breakaway flag. Reuse shared creation/accounting; do not restructure headless supervision.
- Convert PyHANDLE with `int(handle)`; pass HPCON value, not `byref(HPCON)`. Keep Job-array, attribute-list, command-line and environment buffers alive through CreateProcessW.
- Preserve actual trusted RuntimeLaunch argv/cwd/env; never accept arbitrary browser commands or log credentials/PTY contents. Missing backend or uncertain cleanup fails closed.
- Keep existing POSIX adapter and browser peer/Host/Origin/cookie/CSRF behavior unchanged. Copilot alone gets connected first-run setup; no durable-job routing.
- Preserve 1 MiB native queue, 2 MiB replay, 256 KiB slow-client, 64 KiB input and one-hour/four-hour limits; browser disconnect never stops the server-owned session.
- Confirmed stop requires empty Job, original process death where needed, completed pseudoconsole close, joined output reader and no in-flight I/O. Unknown retains resources/registry and blocks replacement/fallback until retry confirms.
- No close of a stream under an active read/write. Drain final ConPTY frames while its asynchronous close completes. Natural root exit with a descendant is not cleanup evidence.
- Windows is primary; no WSL qualification gate and no Windows claim qualifying POSIX.
- Controller owns full suites, final receipts, commits after validation, native interactive smoke and integration. Implementers run focused TDD and return without an active unreported process or a pending full-suite claim.

## Baseline And Evidence

Existing feature tip `2259ce7` has 3224 Python passes/19 skips and 642 repository-wide UI passes/2 skips. Native prototype captures prove real Copilot 1.0.90-6 contained before resume with output and clean exit, plus independent held-handle immediate-grandchild containment/death. The prototype's raw-JSON PTY parser is still red: use marker files and process handles in production tests, never copy that parser.

Commit this plan separately from its already committed design. Establish a fresh full Python baseline at the plan tip before the first production edit. Preserve the old plan's ignored ledger and captures; this plan gets its own SDD workspace/ledger. Existing UI layout and screenshots remain normative; no new sketch was approved and no visual redesign is required.

## Files And Ownership

- New `flowgency/jobs/windows_conpty.py`: lazy Win32 bindings, native console/pipe resources, available-byte read/write/resize, independent close state, attribute-list child creation.
- Existing `flowgency/jobs/windows_job.py`: add atomic ConPTY launch, retaining direct-launch tests/uncertain registry and shared Job factory.
- Existing `flowgency/jobs/windows_connected_process.py`: replace helper framing with raw native console, reuse _OwnedTerminal and bounded queue.
- Existing `flowgency/jobs/connected_process.py`: preserve platform selection/public API; change only an evidenced Windows contract gap.
- New `tests/test_windows_conpty.py` and existing native owner/adapter/session/gate tests: native primitive, real process containment and failure paths; reuse current native interpreter helper.
- Retire `windows_pty_helper.py`, `windows_pty_protocol.py`, their dedicated tests, and pywinpty dependency after migration. Update helper-specific tests instead of discarding their behavioral coverage.
- Operator docs, historical specs/plans and generated CSS only where migration changes their truthful content or reproducible output.

### Task 1: Own Native ConPTY And Atomic CLI Creation

**Files:**
- Create `flowgency/jobs/windows_conpty.py`, `tests/test_windows_conpty.py`.
- Modify `flowgency/jobs/windows_job.py`, `tests/test_windows_job.py`.

**Interfaces:**
- `native_conpty_available() -> bool` checks NT and required native functions without allocation/spawn; module imports safely on POSIX.
- `WindowsConPTY(rows: int, cols: int)` initializes resource state without starting a child. `open() -> None` allocates pipes/HPCON while retaining all partial resources on the object for cleanup.
- `pseudoconsole: int` exposes the opaque live HPCON value.
- `read(size: int = 65536) -> bytes | None`: available bytes, `None` for no current bytes, `b''` for broken/closed output. Probe with PeekNamedPipe; read at most available bytes, no fixed-size buffered wait.
- `write(data: bytes) -> None`, `resize(rows: int, cols: int) -> None`, `begin_close() -> None`, `wait_closed(deadline: float) -> bool`, `close_streams() -> None` preserve original handle ownership and idempotent independent close.
- `create_suspended_conpty_process(argv: tuple[str, ...], cwd: Path, env: dict[str, str], pseudoconsole: int, job_handle: object) -> tuple[object, object, int, int]` returns real process/thread handles plus IDs, never resumes. Every partial post-create error must retain or safely hand back created process handles, not masquerade as no process created.
- `WindowsJobOwner.launch_conpty(argv: tuple[str, ...], cwd: Path, env: dict[str, str], pseudoconsole: int) -> WindowsJobOwner` uses the shared Job factory, structured cleanup errors/registry and membership proof before resume.

- [ ] **Step 1: Write RED ABI and ownership tests.** Import the new native module (initially absent). Add fake API recorders proving PyHANDLE conversion, HPCON value, nonzero Job-array contents, Unicode double-NUL environment, mutable command-line lifetime and the three creation flags. Use platform-conditional imports for real tests.

```python
def test_job_handle_conversion_preserves_pywin32_handle():
    from flowgency.jobs.windows_conpty import handle_value
    from flowgency.jobs.processes import create_kill_on_close_windows_job

    job = create_kill_on_close_windows_job()
    try:
        assert handle_value(job) == int(job) != 0
    finally:
        job.Close()
```

Define `handle_value(handle: object) -> int` in the native module as the one conversion utility. Do not detach borrowed handles. Add failure tests for each allocation/attribute/create/membership/resume stage; assert no resume on failed proof, retained handles on unknown helper/process exit query, and confirmed cleanup only after original process death AND Job emptiness. Keep current direct-owner regressions intact.

- [ ] **Step 2: Run RED.** `./.venv/Scripts/python.exe -m pytest tests/test_windows_conpty.py tests/test_windows_job.py -q`. New import/method is absent; record that failure honestly.
- [ ] **Step 3: Implement native resources and atomic owner.** Copy only the measured ABI essentials, not the 1200-line ignored probe or its JSON-line parser. Bind with explicit argtypes/restype, including ResizePseudoConsole. Keep all parent handles non-inheritable; `bInheritHandles=False` in client creation. The critical attribute values are:

```python
job_array = (HANDLE * 1)(HANDLE(handle_value(job_handle)))
attribute_list.update(PROC_THREAD_ATTRIBUTE_JOB_LIST,
                      ctypes.cast(job_array, ctypes.c_void_p),
                      ctypes.sizeof(job_array))
attribute_list.update(PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE,
                      HPCON(pseudoconsole), ctypes.sizeof(HPCON))
creation_flags = (CREATE_SUSPENDED | EXTENDED_STARTUPINFO_PRESENT
                  | CREATE_UNICODE_ENVIRONMENT)
```

Implement `AttributeList` and these typed constants as private native binding details; native creation itself supplies them. Owner sequence is create Job, create suspended attributed client, verify returned process handle in held Job, ResumeThread, close thread handle. Use existing `_fail_before_resume` / `_process_terminated` / `_retry_uncertain` for structured failures, broadening only to ensure every created handle remains owned. Native `open()` failures leave the object's partial resources accessible; no partially allocated constructor may disappear behind a confirmed-clean exception. `begin_close` starts one close worker, stores close result, and is retry-idempotent; streams remain open until drainer and callers are done.

- [ ] **Step 4: Verify real primitive.** Use marker files, not PTY JSON: monkeypatch the real ResumeThread recorder to assert `owner.contains(pid)` and absent marker just before the real resume. After resume await marker with a bounded Event/deadline; inspect original process handles and Job PID list before Stop. Immediate grandchild must be a member before termination and original identity dead afterward. Drainer services `WindowsConPTY.read()` while close runs; only close streams after reader done. All tests own/clean their exact process handles, no unrelated PID termination.

```python
def recording_resume(thread_handle):
    assert not marker.exists()
    assert win32job.IsProcessInJob(captured_process_handle, captured_job_handle)
    return original_resume(thread_handle)
```

The test captures both handles at native create/Job factory call sites; use genuine private handles, not fabricated integers. Add owner-process crash test using a native suspended/Job-list client; terminate only that test owner and prove root/descendant death from held identities. Cover root exit with live descendant and finite close/reader timeout. Real containment failure is not skipped.

- [ ] **Step 5: Gate and review.** Focused native/owner/lifecycle tests; controller full Python suite; stage only Task 1 files after final exit receipt, `git diff --check`, commit `feat(runtime): create atomic job-owned conpty`. Independent task review before adapter migration.

### Task 2: Replace The Windows ConnectedProcess Adapter

**Files:**
- Modify `flowgency/jobs/windows_connected_process.py`, `tests/test_windows_connected_process.py`, `tests/test_connected_process.py`, `tests/test_setup_sessions.py`.
- Modify common connected_process only if an actual Windows regression needs it; POSIX implementation/semantics stay unchanged.

**Interfaces:** Consume Task 1's native object and `launch_conpty`. Preserve `WindowsConnectedProcess.spawn(launch, *, rows, cols)`, `pid/read/write/resize/alive/exit_code/stop(lifecycle)` and `_OwnedTerminal` registry behavior. `pid` is the actual created client. Keep `_owner` available for existing ownership observers, but no helper READY/EXIT messages are needed.

- [ ] **Step 1: Write RED adapter tests.** Native availability without winpty, unavailable native API/Job/WS gate/no spawn; trusted launch argv/env/size; real short output, Unicode argv/env, split UTF-8 input and resize. Use current `_native_python_launch` and `_read_visible_until` helpers, supplying native executable image for Python tests.

```python
def test_native_adapter_does_not_require_winpty(monkeypatch):
    import sys
    from flowgency.jobs.connected_process import connected_process_available

    monkeypatch.setitem(sys.modules, "winpty", None)
    assert connected_process_available() is True
```

This is Windows-only with real native/Job/WS dependencies present, not a POSIX impersonation. Missing native API counterpart asserts False before any allocation. Extend real owner/adapter tests using marker files: root death with live descendant must keep `alive()` true; after Job empty output must finish rather than wait forever for an unclosed pseudoconsole.

- [ ] **Step 2: Run RED.** Focused `tests/test_windows_connected_process.py tests/test_connected_process.py tests/test_setup_sessions.py -q`; old backend still requires winpty and framed helpers.
- [ ] **Step 3: Migrate with the shared stop contract.** Construct native resource state and an internal adapter before allocating/spawning so all partial failures can be tracked. Open terminal, start its bounded drainer, atomically launch owner, set actual PID, and return only after successful verification/resume. Feed raw native bytes to the existing queue; writes send bytes directly and resize calls native API. Natural empty Job triggers the same idempotent console-close operation while output continues draining; never close on root exit alone.

```python
chunk = self._terminal.read(65536)
if chunk is None:
    if self._owner is not None and not self._owner.alive():
        self._terminal.begin_close()
    self._reader_wakeup.wait(0.01)
elif chunk:
    self._append_output(chunk)
else:
    self._reader_done.set()
```

Define `_terminal`, `_reader_wakeup`, `_reader_done` as adapter state; use a loop/finally so every exit records reader completion/error. Stop verifies Job empty, begins console close, waits within the supplied deadline while its drainer runs, joins it and in-flight public operations, then closes streams/owner. Refine `_stop_outcome` exactly as the existing Windows adapter does: any unfinished close/read/handle release yields unconfirmed evidence and registry retention. Partial no-child failures still require console/I/O cleanup. Preserve known root exit code only when available and whole-Job liveness separately.

- [ ] **Step 4: Verify failure contracts.** Test allocation/create/verification/resume failures, stopped before/after spawn, cancellation while native creation is paused, empty Job with stuck console close, stuck reader/write, unknown Job/process query, later Stop/replacement retry, concurrent Stop/start and bounded 1 MiB output. Test-owned native processes/manager tasks use bounded finally cleanup. Native output and authentication text never enter logs/HTML.

- [ ] **Step 5: Gate and review.** Focused adapter/manager/server/lifecycle/headless regressions, controller complete Python, commit `feat(setup): use atomic native windows terminal`, independent task review.

### Task 3: Retire Failed Helper And Migrate Remaining Coverage

**Files:**
- Delete `flowgency/jobs/windows_pty_helper.py`, `flowgency/jobs/windows_pty_protocol.py`, `tests/test_windows_pty_helper.py`, `tests/test_windows_pty_protocol.py`.
- Modify `tests/test_windows_job.py`, `tests/test_setup_sessions.py`, `tests/test_windows_connected_process.py` only for obsolete helper-specific setup still present.
- Modify `pyproject.toml`, generated `flowgency/static/tailwind.css` if reproducible rebuild changes it, and stale backend documentation.

**Interfaces:** No new API. Native path must have equivalent launch validation/failure/cancellation/UTF-8/resize and bounded teardown coverage before deleting dedicated framing tests.

- [ ] **Step 1: Prove remaining helper-only dependencies.** Exact symbol search for `windows_pty_helper|windows_pty_protocol|_helper_launch|winpty` in production/tests. Map each real helper/cancellation test to the Task 1/2 native equivalent. Do not remove direct WindowsJobOwner launch safety tests or skip real ownership cases.
- [ ] **Step 2: Validate replacement validation coverage before removal.** Add native startup tests for empty argv, wrong mode, relative/invalid cwd, NUL argv, empty/equals/NUL env keys, nonstring values and malformed dimensions. No invalid launch allocates native resources. Existing Windows UI `SetupSessionManager(process_factory=fake)` keeps real capability and remains unchanged.

```python
def test_invalid_environment_does_not_allocate_native_console(monkeypatch, tmp_path):
    from flowgency.integrations.models import RuntimeLaunch
    from flowgency.jobs.connected_process import ConnectedLaunchError, start_connected_process

    allocated = []
    monkeypatch.setattr("flowgency.jobs.windows_conpty.WindowsConPTY.open",
                        lambda self: allocated.append(self))
    launch = RuntimeLaunch(("copilot",), tmp_path, {"BAD=KEY": "value"}, "connected")
    with pytest.raises(ConnectedLaunchError):
        start_connected_process(launch)
    assert allocated == []
```

- [ ] **Step 3: Remove only obsolete path.** Delete listed modules/tests; replace helper-specific real tests with native API/paused launch tests already reviewed. Remove Windows pywinpty requirement, keep pywin32 and `uvicorn[standard]`, keep existing websockets test extra and POSIX ptyprocess. Correct its stale production-WebSocket comment. Update historical helper spec/plan notices, current native spec references and operator copy only where necessary; do not claim live interactive smoke already done.
- [ ] **Step 4: Verify and rebuild.** Focused native/owner/adapter/manager/asset/boundary tests, pinned `npm ci`, terminal/CSS builds. Generated bytes must match committed sources; commit intentional CSS change if deleting helper removes its false arbitrary-value candidate. Setup Playwright four projects through real Windows capability. Controller full Python gate.
- [ ] **Step 5: Commit/review.** Stage only retirement/migration/dependency/docs/intentional generated files, commit `refactor(setup): retire windows pty helper`. Independent review verifies behavior was migrated, not discarded.

### Task 4: Qualify Real Windows And Integrate

**Files:** Test/report/review the Task 1-3 files, existing UI setup tests, README/guide, and the ignored native smoke harness/evidence. No new public observer route or production testing switch.

**Interfaces:** Real application uses native ConnectedProcess. Adapt ignored smoke instrumentation to `launch_conpty` and actual client PID; fault mode injects a native attribute/create failure rather than obsolete AssignProcessToJobObject. Preserve runtime token, exact loopback/Origin, original held identities, and refusal to delete/reuse config/evidence.

- [ ] **Step 1: Final automated gates.** Controller complete Python, repeated deterministic terminal/CSS builds, all four setup projects and repository-wide UI suite. Save full output and exit receipts; don't count focused retries as a green full run. Confirm native backend works with winpty unimportable and real production WindowsApps-resolved launch, not only a custom Python command.
- [ ] **Step 2: Real live Windows smoke.** Fresh absent config, private auth-only Copilot home, throwaway data root and free loopback port; verify feature module path. Launch actual Copilot from the real CSRF/cookie form, observe in-page prompt, send one harmless reply, refresh/reconnect, Stop, and prove empty Job/original identities gone/finished readers/closed console/config absent. Human enters any auth directly; do not capture device codes/tokens in agent logs. Preserve all failed trial evidence.
- [ ] **Step 3: Real failure/restart smoke.** Inject native creation failure into ignored test harness only, prove safe external fallback after completed resource cleanup and no fallback on uncertainty. Start another active session, gracefully shut down server and verify Job empty/no recorded survivor; restart fresh absent-config server without reusing old receipt files. Direct-PID termination may clean a failed diagnostic only after identity verification, never substitute for backend ownership proof.
- [ ] **Step 4: Final whole-branch review.** Package full feature diff from original master merge base, review Windows ownership, native ABI, handle lifetimes, cancellation/IO teardown, browser security, preserved headless/POSIX behavior and migrated coverage. Include old deferred minors and actual native receipts. Resolve blockers through owning-slice tests; controller full gate after production fixes.
- [ ] **Step 5: Pre-authorized integration.** Follow AGENTS.md: protect main dirty/runtime data; rebase feature only if master advanced, rerun complete suite, fast-forward only. Full integrated-master suite, push master and feature, preserve private/ignored evidence and runtime-local artifacts before worktree remove/prune. Start final trial server from master on a free loopback port and report URL plus verified support/remaining caveats. No integration or support claim on an unproven real ownership/interactive gate.