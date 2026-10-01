# Native Atomic Windows ConPTY Setup

**Date:** 2026-10-01
**Status:** Native atomic-creation approach approved for implementation in conversation.
**Supersedes:** The helper-inheritance design dated 2026-09-28.

## Goal And Measured Evidence

Native Windows Copilot first-run setup must run in the existing browser
terminal while Flowgency owns the complete client process tree before it
can execute. Windows is primary; existing POSIX behavior is unchanged and
requires its own native qualification.

The contained pywinpty helper did not contain the measured real Copilot
ConPTY child, despite both breakaway flags being disabled. Alias and native
image launches failed alike. Direct suspended/assigned creation worked.
The replacement primitive has been measured with native ConPTY and
PROC_THREAD_ATTRIBUTE_JOB_LIST: a suspended Python and real Copilot 1.0.90-6
belong to the held Job before ResumeThread. Copilot version output and clean
exit were observed. Independent held-handle evidence also proves immediate
grandchild membership and death after Job termination. The diagnostic's
raw-JSON-line parser still fails on terminal formatting; it is not release
coverage and is not to be copied into production tests.

## Architecture

The server owns the ConPTY resources and a non-inheritable per-session Windows
Job with JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE and neither breakaway flag.
There is no helper-spawn inheritance assumption or post-execution assignment.
CreateProcessW receives both PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE and
PROC_THREAD_ATTRIBUTE_JOB_LIST, with CREATE_SUSPENDED,
EXTENDED_STARTUPINFO_PRESENT and CREATE_UNICODE_ENVIRONMENT. Flowgency
verifies the returned process handle belongs to its Job before ResumeThread.
The actual CLI launch comes from the existing trusted integration, never
arbitrary browser argv. PID identifies this created client, not a helper.

Native bindings use Python ctypes and installed pywin32 Job/accounting
primitives. No Node or Rust runtime, compiled fork, or executable bridge is
introduced. Remove the unused pywinpty production dependency after migration;
keep Windows production WebSocket support and POSIX ptyprocess dependency.

## Components And Interfaces

- flowgency/jobs/windows_conpty.py owns narrowly scoped Win32 bindings,
  pseudoconsole, pipe and attribute-list lifetimes. DLL loading is lazy and
  safe to import on POSIX. It exposes native_conpty_available(),
  WindowsConPTY(rows, cols), binary read(size), write(data), resize(rows, cols),
  begin_close(), wait_closed(deadline), close_streams(), and
  create_suspended_conpty_process(argv, cwd, env, pseudoconsole, job_handle).
  The latter returns process/thread handles and PID/thread ID; it does not
  resume the child or assume ownership can be inferred from an exited PID.
- flowgency/jobs/windows_job.py adds WindowsJobOwner.launch_conpty() for the
  atomic attribute-list path. It retains existing direct suspended launch,
  structured errors, uncertain-launch registry, shared Job factory and
  accounting. It verifies membership before resume and handles every
  post-create failure through original-process death plus empty Job proof.
- flowgency/jobs/windows_connected_process.py consumes the owner and native
  terminal, preserving ConnectedProcess and _OwnedTerminal tracking. Its
  dedicated reader drains raw bytes continuously into the existing bounded
  output queue. Windows availability requires native APIs, Job operations
  and production WebSocket protocol; it no longer depends on winpty.
- Remove windows_pty_helper.py, windows_pty_protocol.py and their unused tests
  only after replacement coverage and dependent tests have migrated. Do not
  retain an alternate unsafe helper backend.

The existing session manager, routes, access controls, xterm UI and readiness
logic remain shared. Headless execution and the POSIX adapter are not refactored.

## Native ABI And Ownership

Handle conversion accepts pywin32 PyHANDLE through int(handle) and ctypes
HANDLE through its value without detaching or losing ownership. The Job-list
attribute receives a live array of Job handles; the pseudoconsole attribute
receives the HPCON value, not the address of handle storage. Attribute-list,
environment and mutable command-line buffers survive CreateProcessW and are
then disposed. Environment is a double-NUL-terminated Unicode block, supplied
only from the validated RuntimeLaunch. No credentials, cookie or PTY output
are logged, passed in URLs or added to helper command lines.

The Job, process, thread and parent pipe handles are not inherited by the
client. Only the ConPTY association is passed via its creation attribute.
Close duplicate child-side channel ends after process creation. Keep the
Job and original process handles until confirmed cleanup; root exit alone
does not imply empty accounting. Missing/failed membership proof never resumes
the child and never labels unknown cleanup safe for fallback.

## I/O And Teardown

Output is drained on a dedicated thread using available-chunk reads, never
a buffered read that waits for a fixed-size block. Treat ConPTY as terminal
bytes, not a machine-readable line protocol. Preserve UTF-8 bytes, including
input split across writes, and pass resize to ResizePseudoConsole. Retain
the 1 MiB native output cap; session replay remains 2 MiB, slow-client limit
256 KiB, input limit 64 KiB, idle limit one hour and total limit four hours.

Stop, startup cancellation, timeout and shutdown first terminate/query the
Job with a finite deadline. Pseudoconsole closing runs independently while
the output reader continues draining final frames; never block its drainer
on the close operation. Do not close a stream under an active read or write.
Confirmed ProcessStopEvidence requires empty Job accounting, completed
pseudoconsole close, joined output reader, no in-flight API operations and
closed terminal resources. Unknown state retains resources/owner and stays
in the existing unconfirmed registry so replacement/fallback is refused.
A later Stop/start retry may finish the same close, not start another one.

Partial creation failures also clean every allocated resource through this
path. No created child means no process tree to kill, but unfinished ConPTY
or I/O teardown still prevents confirmed fallback. Server crash closes the
sole Job ownership handle and must reap client descendants.

## Verification And Release

Use the existing named feature worktree. Commit this design and its amended
implementation plan in separate documentation-only commits before production
edits. Establish a clean full Python baseline at the revised plan tip.
Controller owns long validation gates and their durable exit-code receipts;
subagents own focused TDD and scoped reviews.

Native tests must assert Job-list and pseudoconsole values, create-suspended
then verify then resume order, no pre-resume marker/output, and real immediate
descendant membership using markers/held handles instead of parsing PTY JSON.
Exercise startup errors, failed membership, resume failure, unknown process
or Job queries, bounded close/reader uncertainty, later retry, concurrent
Stop/start and cancellation. Existing direct-owner safety regressions stay.

Real native tests cover raw output, short-message delivery, Unicode env/argv,
split UTF-8 input, resize, natural root exit with a live descendant, Stop,
timeouts, shutdown and kill-on-close owner crash. A version command is only
an additional executable-specific containment test, not interactive proof.

Run complete Python and applicable browser gates before whole-branch review.
Repeat real isolated in-page Copilot launch, harmless reply, refresh, Stop
with empty Job and original identities gone, absent config, active-session
graceful restart and confirmed-clean external fallback. Human enters any
authentication/device code directly; none goes through model tools. No merge,
push or support claim until real containment and interactive smoke pass.
Then follow repository fast-forward, integrated-master full suite, push and
worktree cleanup requirements, preserving unrelated/runtime-local data.

## Alternatives

- Keeping helper inheritance fails the measured real CLI ownership gate.
- A pywinpty fork exposing atomic Job attributes adds wheel/build maintenance
  when the measured Windows API already provides the primitive.
- An executable bridge adds a distribution/runtime surface without removing
  the requirement to assign the actual client before execution.

This revision changes only the Windows process backend, not application
configuration authority, durable jobs, browser controls or frontend layout.