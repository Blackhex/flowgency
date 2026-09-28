# Native Windows Connected Copilot Setup

**Date:** 2026-09-28
**Status:** Proposed written design; Windows in-page setup approved in conversation

## Problem And Goal

Flowgency's first-run setup terminal currently connects to Copilot only on
POSIX hosts. Native Windows always opens a separate console. The user needs the
same browser-owned, reconnectable setup terminal on Windows; Linux remains
optional. The existing `ConnectedProcess` interface, setup-session manager,
same-origin browser controls, xterm assets, readiness polling, and dashboard
link/Stop flow should remain the common application surface.

The original direct-pywinpty design was rejected because its `PTY.spawn()`
starts Copilot before Flowgency could attach it to a Windows Job Object. A
descendant started in that interval could survive Stop. Adding a library must
not weaken the existing requirement to prove the whole tree stopped before
another launch.

## Scope And Dependency

- Add a Windows-only `pywinpty` 3.x wheel dependency and force its native
  ConPTY backend. Its compiled wheel does not require a separately installed
  Node.js or Rust runtime. If ConPTY or safe ownership is unavailable, keep
  the existing external-console launch and copyable fallback command.
- Add production WebSocket support on Windows: the current plain `uvicorn`
  install does not include a WebSocket protocol, while the test extra does.
- Do not change the POSIX `ptyprocess` adapter or claim Windows tests qualify
  POSIX connected operation. Do not put first-run setup into durable jobs,
  dispatch, configured-agent sandboxes, or headless Copilot invocation.
- Preserve the current local peer/Host/Origin, signed-cookie, CSRF, single
  owner/session, bounded replay, input, size, and timeout contracts.

## Ownership Before Spawn

Flowgency creates a per-session Windows Job Object with
`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` and neither breakaway limit. Reuse the
proven Windows supervisor's process-handle, accounting, and stop-evidence
primitives rather than duplicating the entire headless job runner. Start a
minimal Python PTY helper with `CREATE_SUSPENDED`, assign its process handle
to the Job, and only then resume its primary thread. If creation or assignment
fails, terminate/wait on the suspended helper, retain any Job handle needed
for cleanup proof, and fail closed. The helper does not run uncontained.

Inside that already-contained helper, pywinpty may call `PTY.spawn()`
immediately. Force ConPTY, not the older winpty-agent backend. Windows
normally places children created with `CreateProcessW` into their parent's
Job when breakaway is not allowed; the verified ConPTY implementation does
not request a breakaway flag. The helper and Copilot child/grandchildren must
be shown to occupy the same Job in live Windows tests, including a child
created immediately at startup. If that ownership proof fails, Windows
connected capability must remain disabled; never launch directly and assign
the Copilot PID afterward.

The server owns the Job handle, not the helper. A server crash closes the
kill-on-close handle; explicit Stop, idle/absolute timeout, and shutdown
terminate the Job and query active process accounting until it is empty.
Do not infer whole-tree exit from the helper PID or root Copilot exit alone.
Unconfirmed/unknown accounting retains the handle and blocks a replacement
session; a later Stop/shutdown can retry. Preserve a known child exit status
only when available; never turn missing status into cleanup proof.

## Helper Protocol And I/O

The helper is a packaged Python module started with the server interpreter
and a trusted import location. It alone owns the ConPTY instance and its
read/write/resize operations. Server-to-helper stdin and helper-to-server
stdout are separate pipes; PTY output is not written to logs or HTML.
The server constructs the Copilot argv/environment and sends a single framed
startup message over the private pipe after the helper is contained. Neither
the browser nor a URL chooses arbitrary argv, and secrets are not placed in
the helper's command line. Subsequent bounded frames carry input, resize,
status/error, and raw output bytes. Reject malformed/oversized frames and
fail closed without spawning a second terminal. The startup frame has its
own finite size cap; output frames cannot exceed the existing reader budget.

Run ConPTY output draining and helper control input on separate threads so
blocked synchronous pipe I/O cannot deadlock resize, Stop, or teardown.
Keep Job termination and bounded reader joins in the parent; never close a
pipe descriptor while another thread is inside a read. The adapter implements
the existing `read`/`write`/`resize`/`alive`/`exit_code`/`stop` contract, so
the session manager and browser routes need no new process-specific API.
Startup succeeds only after the helper reports a usable ConPTY child. If
the helper dies or reports a spawn failure, only confirmed-empty Job evidence
allows the route's existing external-terminal fallback.

## Verification And Release

- Baseline the new worktree before implementation. Add RED/GREEN unit tests
  for the suspended-create -> Job-assign -> resume order; assignment failure,
  helper crash, partial startup, malformed frames, concurrent Stop/start,
  cancelled startup, and unconfirmed accounting. Assert no external fallback
  or replacement without confirmed cleanup.
- Use a real controllable Windows child that immediately spawns a grandchild;
  verify both are Job members and Stop, timeout, server shutdown, and helper
  failure leave neither alive. Also test root exit with a live descendant,
  binary PTY output, keyboard input, resize, EOF, and bounded reader teardown.
- Browser-test native Windows first-run setup with a fake controllable PTY,
  without overriding Windows capability to pretend it is POSIX. Confirm the
  in-page terminal, refresh/reconnect, owner-only controls, readiness redirect
  with background process, Stop, and fallbacks. Keep existing POSIX tests.
- Run the complete Python suite and relevant Playwright gates in the feature
  worktree, review the implementation, then run the complete master suite.
  Live Windows CLI smoke must demonstrate an actual in-page Copilot prompt,
  a harmless reply, refresh, Stop with Job empty, and no unintended config
  write. Authentication remains user-entered; no device code/token through
  the agent. A Windows fallback smoke checks forced containment failure.
- Update first-run docs to make Windows in-page setup the normal supported
  path and separate-console mode the safe fallback. Preserve the explicit
  caveat that POSIX needs independent native qualification.

## Rejected Alternatives

- Direct `pywinpty.PTY.spawn()` in the web server followed by Job assignment:
  leaves an uncontained execution window and cannot prove descendant exit.
- Reusing the durable job scheduler: first-run setup may have no team, job
  policy or record; only its low-level process ownership should be shared.
- A node-pty or portable-pty executable bridge: adds an extra production
  runtime/process distribution surface when the contained Python helper and
  a maintained Windows wheel can provide the same terminal API.