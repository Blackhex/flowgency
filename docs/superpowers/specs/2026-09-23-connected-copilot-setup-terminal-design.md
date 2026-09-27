# Connected Copilot First-Run Setup

**Date:** 2026-09-23
**Status:** Approved (design)

## Problem

First-run setup opens Copilot in a separate console. The setup page only polls
for a complete canonical configuration, so it cannot show the conversation or
accept replies. Reloading the page does not reconnect to that console, and a
status-poll failure leaves the user with a fallback command but little context.

Configured agents use a different, headless Copilot invocation: they run with
noninteractive flags, closed stdin, and captured JSON output. Reusing that job
unchanged would remove the interactive questions and approvals that setup needs.

## Goals

1. Run the first Copilot setup conversation in a real terminal on the setup
  page where a safe Python-only PTY backend is available.
2. Share invocation infrastructure between explicit `headless` and `connected`
   modes, without changing existing headless agent behavior.
3. Keep the setup process alive across tab refreshes and brief disconnects.
4. Redirect when configuration becomes ready while keeping a still-running
   Copilot process visible and controllable from the dashboard.
5. Limit control of this privileged local process to the browser that started
   setup on the same computer.
6. Preserve the current external-terminal launch and command as a fallback.

## Non-Goals

- Adding connected-run controls to configured agent pages in this feature.
- Turning first-run setup into a team job before a valid team exists.
- Adding connected support to integrations other than Copilot yet.
- Persisting or replaying terminal transcripts across server restarts.
- Changing the setup skill, the schema-version-1 configuration authority, or
  the revision-checked atomic configuration write.
- Exposing terminal access to other devices or making localhost a security
  boundary against other processes and users on the same computer.
- Adding Node.js or a compiled Rust bridge as a production requirement.
- Enabling connected setup on Windows before process-tree ownership can be
  established without a pre-assignment execution window.

## Execution Modes And Ownership

Introduce an explicit `headless`/`connected` execution mode in the shared
integration launch and process-lifecycle layer. Keep `IntegrationRunRequest`,
existing job submission and records, headless CLI arguments, sandbox policy,
output parsing, and result reporting intact. Factor out only the applicable
executable resolution, launch environment, process ownership, timeout, and
stop-confirmation primitives; do not force interactive setup through
`BaseIntegration.run()` or fabricate a configured agent or job record.

Copilot builds its existing setup command from the selected data root, bundled
`flowgency-setup` skill, initial prompt, and `-i` interactive flag. Connected
mode attaches that command to a Flowgency-owned POSIX pseudo-terminal on hosts
with readable `/proc` process-group evidence, using `ptyprocess`. The same
launch layer retains a pipe-based, noninteractive path for agent jobs. Windows
uses the existing external-terminal setup and copyable command: pywinpty starts
its ConPTY child before Flowgency can assign a Job Object, and a descendant can
escape the proof of cleanup before assignment. Do not attempt that unsafe
connected launch or require pywinpty in production.
Interactive setup must not inherit headless-only flags such as
`--no-ask-user`, `--autopilot`, `--output-format json`, or closed stdin.

A connected capability is explicit rather than assumed for every integration.
Only Copilot setup consumes it in this feature. Other integrations and hosts
without working PTY support keep their existing external-terminal behavior.
The connected implementation may reuse lifecycle primitives from supervised
jobs, but must extend them for PTY I/O instead of pretending a terminal is a
captured subprocess pipe. This release uses native Windows validation only;
its result does not qualify POSIX connected setup. POSIX deployments need
separate native validation before relying on connected mode there. Windows
fallback must be tested; a future Windows connected mode requires a
separately reviewed safe backend.

## Setup Session

The setup route still validates the selected integration and data root, creates
the `InteractiveSetupRequest`, and does not write configuration. A server-owned
setup-session manager starts at most one connected setup process for that
Flowgency server. It stores the chosen root, integration, process identity,
session credential, connection/exit state, and a bounded output replay buffer
in memory. A repeated launch for the same browser and root reattaches rather
than spawning a second Copilot process; a different root requires an explicit
Stop before a new launch.

The process reader always drains the PTY, including when no tab is connected.
It retains at most 2 MiB of recent terminal bytes; a slow WebSocket client is
disconnected when its pending output exceeds 256 KiB, without blocking Copilot.
On reconnect, the page replays retained output and sends the current terminal
size so the CLI can redraw. If older terminal bytes were discarded, the page
says that the earlier output is unavailable; it must not present a partial
replay as a complete transcript. Terminal contents are never saved to disk or
application logs by default. The manager enforces one hour without input or
output and a four-hour absolute session limit, including after redirect.

One browser connection holds input ownership at a time. Reattaching from the
same browser transfers ownership and prevents an older tab from sending more
keystrokes. Input, terminal-size changes, process exit, Stop, and connection
state are distinct events with bounded message sizes. Stop and timeouts end
the owned process tree and confirm its exit before another launch is allowed;
an unconfirmed descendant is reported, not silently treated as stopped.
Server shutdown also attempts this cleanup. A server restart loses the
in-memory session, terminates its owned process, and shows a clear relaunch or
fallback path rather than pretending the old terminal can reconnect.

## Web Experience And Readiness

On a supported POSIX host, replace the waiting panel with an embedded terminal
after launch. Keep the selected data root and integration visible in a
compact header, and show connecting, connected, reconnecting, exited, and
stopped states. Package the terminal renderer and its resizing support as
local static assets so installed deployments work without a CDN. The fallback
command remains available but does not dominate a healthy connected session.
If PTY startup fails, keep the existing external-console launch and
copyable-command experience. If a process was already created, confirm its
cleanup before offering another launch; fallback must not duplicate a session.

`/setup/status` remains the authority for readiness: only a validated,
complete canonical configuration can produce `ready`. Terminal output and
process exit are not proof that setup succeeded. Poll failures show a retrying
status without hiding or disconnecting the terminal. If Copilot exits before
configuration is ready, leave its final output visible and offer Relaunch.

When configuration becomes ready, redirect the browser to the dashboard
automatically, even if Copilot is still running. Do not stop or detach the
server-owned process on redirect; it continues until natural exit, Stop, or a
session limit. While running, a small dashboard indicator links to a distinct
setup-session page where the same browser can reconnect and Stop the process.
This route remains reachable after `/setup` starts redirecting. Remove the
indicator when the process exits or is stopped. The dashboard does not expose
terminal output to other clients.

## Access And Safety

Any setup launch, whether it starts the connected session or the external
terminal, as well as connected session view, input, resize, state, and Stop
endpoints require a direct loopback client and a loopback `Host`. Unsafe HTTP
requests and all WebSocket upgrades require a matching same-origin `Origin`.
Check launch access before integration discovery or data-root preparation: a
remote or cross-origin POST cannot create a directory or spawn a process. The
initial setup page
issues a random same-site, HTTP-only browser cookie and a form anti-CSRF token.
Launch and HTTP controls require both; the session is bound to that cookie, and
WebSocket upgrades require the cookie and a matching `Origin` before acceptance.
Credentials are not placed in URLs, terminal output, or logs. A second browser
cannot attach to or replace an active session. Connected endpoints never accept
arbitrary shell commands or argv from the browser; Copilot builds the command
from the already validated setup request.

The initial setup session has no configured team permission policy. Connected
mode must not claim the sandbox guarantees of an existing headless agent job:
the interactive CLI can use the server user's filesystem and tool access.
State this plainly on the setup page. Loopback checks protect against remote
browser access even if `flowgency serve` binds to all interfaces; they are not
authentication against another local OS user or process. Keep CLI output out
of HTML interpolation, disable automatic actions from terminal escape/link
sequences, and bound incoming controls and outbound queues.

## Verification

- Preserve focused tests for existing headless Copilot flags, policy, result
  parsing, process containment, and the external setup fallback.
- Exercise connected launch, PTY input/output/resize, replay truncation,
  slow-client handling, single-writer handoff, session reuse, Stop, timeout,
  process-tree cleanup, exit, and server shutdown with a controllable test CLI.
- Verify loopback, `Host`, `Origin`, cookie, and anti-CSRF checks on HTTP and
  WebSocket paths, including remote clients and cross-origin browser requests.
  Cover both connected and external launch POSTs: a remote request naming a
  missing data root returns 403 without creating the root or launching an agent.
  A client from a separate OS network stack is sufficient for the live remote
  smoke check when the server logs a non-loopback peer and the rejected launch
  leaves its selected root absent; a separate physical device is not required.
- Test configuration readiness separately from process exit, automatic
  redirect with Copilot still running, dashboard return and Stop, disconnected
  polling, relaunch after failure, and startup without PTY support.
- Test the rendered setup and dashboard flow with Playwright; validate local
  terminal assets in an installed wheel. For this release run platform tests
  and smoke only on native Windows: verify the unchanged external launch and
  absence of any connected PTY process. Do not run POSIX or WSL gates as part
  of this release or claim their behavior is certified by Windows results.

## Alternatives Considered

- A separate local terminal server adds another service, ownership boundary,
  and access-control surface, especially on Windows.
- A node-pty helper or Rust PTY bridge could provide a portable backend but
  adds a new production runtime or compiled bridge. Neither is in scope.
- A browser chat adapter to headless Copilot would not preserve the CLI's
  interactive prompts, approvals, or full-screen terminal behavior.
- Mirroring an external console without input would not satisfy the requested
  in-page setup conversation or reconnectable control.