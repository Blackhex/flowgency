# Explicit First-Run Setup Completion

Date: 2026-10-06
Branch: `feature/setup-completion`
Worktree: `.worktrees/setup-done/`
Status: design sections approved; written specification awaiting user review.

## Problem And Evidence

The guided setup session published a valid configuration and the browser
automatically opened Inbox while Copilot was still asking whether to install
the singleton scheduler. The configuration was ready, but setup was unfinished.

[`inspect_setup_status`](../../../flowgency/web/setup_flow.py) checks canonical
configuration and workflow references. The
[`status route`](../../../flowgency/web/routes/admin_teams.py) returns a redirect
on readiness, and [`setup.html`](../../../flowgency/templates/setup.html) follows
that redirect without requiring conversational completion. The
[`session manager`](../../../flowgency/web/setup_sessions.py) records process
lifecycle, not successful completion of the setup procedure.

Connected Copilot setup uses interactive `-i` mode. The installed CLI documents
`/exit` as its interactive exit command. Its `-p` auto-exit mode is
non-interactive and documents automatic tool approval as a requirement.
Documented `/exit` support is not evidence that automatic exit is safe at an
arbitrary point in the interactive session.

## Approved Scope

Keep guided setup visible until its work is explicitly complete. Prefer a
graceful CLI exit before automatic navigation. If safe automatic exit cannot
be supported reliably, allow navigation through the explicit completion gate
while retaining access to the terminal.

This workstream is independent of app-wide live refresh and can be implemented
and integrated first. Preserve the existing setup experience, interactive
permission prompts, canonical configuration shape, approved activation choices,
and one atomic config write. Do not convert setup to non-interactive mode or
grant additional permissions to obtain automatic exit.

No visual redesign or new setup wizard is included. No approved mockup or
diagram was produced during brainstorming.

## Completion Contract

Configuration readiness is necessary but insufficient. Completion requires:

1. The saved canonical configuration, approved source artifacts, and configured
   workflow references pass the existing final validation requirements.
2. All required setup decisions are resolved and saved choices match the
   approved choices. No required question or operation remains outstanding.
3. Scheduler installation has been accepted or declined when applicable, and
   any accepted installation attempt and status check have settled.
4. Installation failure or unverifiable scheduler status has been reported and
   explicitly acknowledged by the user before proceeding with that limitation.
5. The final setup summary has been delivered, including the observed scheduler
   result and any acknowledged limitations.
6. The setup skill explicitly acknowledges completion for the current launch.

Declining scheduler installation permits completion; it does not disable saved
dispatch settings or prove automatic execution is operational. A manual-only
setup or inactive schedules must not acquire an unnecessary installation
question. An already-running scheduler can continue executing approved work;
this gate controls navigation, not the timing of previously approved execution.

Ordinary assistant turn completion, an idle prompt, elapsed time, terminal
silence, configuration existence, and exit code zero without acknowledgement
are not substitutes for this contract. Do not infer completion from ANSI text,
an apparent success sentence, or the absence of recent output.

## Session Architecture And Data Flow

Keep canonical readiness and setup completion as separate responsibilities:

- Existing config and workflow validators decide whether services can operate.
- The setup skill owns the ordering of questions, checks, scheduler decisions,
  and the final summary.
- A launch-bound completion handshake records explicit completion in temporary
  session state. It carries lifecycle information, not another team payload.
- The session manager owns process state, graceful-exit coordination, and
  confirmed shutdown evidence.
- The setup routes and browser controller decide whether automatic navigation
  is allowed using both readiness and the current session's completion state.

The handshake must be bound to the active launch and the configuration revision
being confirmed. Validate readiness and revision at acknowledgement; reject
stale or mismatched acknowledgements. Before navigation, recheck that the
acknowledged configuration remains current and valid. A relevant change
requires renewed confirmation rather than silently adopting unapproved data.

Completion is temporary lifecycle state. Do not add a config completion flag,
second configuration artifact, saved team proposal, or new control-plane
authority. Do not overload process state `exited` to mean setup succeeded.

Use the existing owner-only access boundary for browser session operations.
Authorize the agent handshake with a narrowly scoped launch capability that
cannot complete a different or replacement session. Do not expose its secret
in terminal output, fallback commands, browser markup, or durable records.
Repeated acknowledgement of the same launch and revision is idempotent;
acknowledgement replay against another launch is rejected.

The handshake applies to Flowgency-launched guided first-run setup. A manually
invoked setup skill without this launch context keeps its existing validation
and summary behavior and does not need a browser-session capability.

## Graceful Exit And Navigation

After the completion contract is satisfied, prefer the normal interactive exit
command. Issue it at most once and only at a supported, verified boundary where
it cannot cancel a task, answer a question, or overwrite user input. Verify this
behavior against the installed CLI and connected-process adapter before
enabling the exit path. Do not manufacture a safe boundary from silence or
terminal-text matching.

When this path is supported, wait for confirmed process-tree shutdown and a
successful exit result before redirecting. A short delay or a disconnected
WebSocket is not shutdown evidence. Do not send forced termination as a
completion mechanism; the explicit Stop action retains its existing purpose.

If a reliable automatic-exit path is unavailable, use the agreed fallback:
explicit acknowledged completion plus valid current configuration permits
redirect while the healthy CLI remains open. Keep the owner-only View terminal
and Stop controls available. Report process state honestly; do not claim the
CLI exited when it did not. An unexpected failure is not this fallback.

All automatic navigation originating from guided setup must honor the gate,
including readiness polling and returning to the setup entry/session routes.
Do not derive navigation intent solely from whether configuration happened to
be ready when the page was rendered. Reopening a terminal for inspection must
remain an inspection view rather than immediately redirecting again.

Normal startup with an existing valid configuration and no unfinished tracked
setup session continues to open the dashboard. Deliberately visiting an
operational dashboard must not be treated as completing a pending session.
Navigation and disconnect do not stop the CLI or satisfy completion.

## Failure Handling

Invalid configuration, missing approved source, revision drift, an unanswered
question, or unsettled work blocks automatic completion. Preserve the terminal
and explain the condition through the existing setup status surfaces.

User cancellation, explicit Stop, unexpected CLI failure, or loss of a trusted
completion signal cannot count as success. A natural exit without the explicit
completion acknowledgement requires attention even if configuration is valid.
A failed or unverifiable scheduler installation permits completion only after
the separate limitation acknowledgement described above.

Reject late state and handshake messages from a replaced launch. Do not
redirect based on stale polling responses. Keep browser/session authorization
and sanitized error reporting intact.

## Verification

Use existing setup route, session-manager, connected-process, skill-contract,
and Playwright fixtures. Do not interrupt the user's live setup session or
install a real platform scheduler during automated tests.

1. Publish valid configuration while a scheduler question remains unanswered;
   prove readiness does not navigate away, including after page refresh or a
   visit to the setup entry route.
2. Complete manual-only, inactive-schedule, accepted-installation, and
   declined-installation flows. Assert the correct saved settings and reported
   scheduler result, with no second activation write.
3. Exercise failed installation and unknown status. Verify that completion
   remains blocked until the limitation is explicitly acknowledged.
4. Prove the final summary and launch-bound acknowledgement precede exit and
   navigation. Mere idle state, success-looking output, or clean process exit
   cannot pass the gate.
5. Measure the graceful-exit path in an isolated disposable CLI session. Prove
   it does not cancel active work, alter a response, or require broad permission
   grants. Unsupported or inconclusive behavior selects the explicit fallback.
6. Test confirmed clean shutdown, healthy open-terminal fallback, unexpected
   exit, cancellation, explicit Stop, disconnect, and terminal inspection.
7. Reject unauthorized, stale-revision, wrong-launch, and replayed completion
   attempts. Cover replacement-session and delayed-poll races.
8. Preserve existing canonical validation, workflow completeness, atomic write,
   first-run navigation, accessibility, and terminal continuity regressions.

Establish a clean full-suite baseline in the active feature worktree before
implementation. Use focused checks while iterating, then run the required full
Python and browser suites sequentially before review and integration. Do not
change snapshot tolerances or unrelated runtime data to obtain passing gates.

## Review And Delivery

This specification is a documentation-only commit. User review of the written
specification is required before a separate implementation plan is written.
The plan must preserve the distinction between explicit completion and process
exit and include the isolated graceful-exit feasibility check.

Implementation and integration follow the repository's named-worktree,
separate-plan-commit, review, fast-forward, verification, publication, and
owned-worktree cleanup requirements. Preserve unrelated checkout changes and
runtime-local configuration, team state, logs, and scheduler installations.