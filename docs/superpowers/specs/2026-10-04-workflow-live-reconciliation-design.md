# Non-Disruptive Workflow Refresh

Date: 2026-10-04
Branch: `feature/workflow-live-reconciliation`
Worktree: `.worktrees/workflow-live-reconciliation/`
Status: design sections approved; written specification awaiting review.

## Failure And Evidence

The workflow board polls its JSON snapshot every two seconds. On an HTTP 200
response, `WorkflowBoardController.refreshBoard` does not consume that snapshot.
Instead it calls `loadPage`, fetches the current HTML page, and invokes
`applyPageHtml`, which replaces the entire workflow page and ticket dialog.

The controller restores drafts and focus afterward, but a newly focused select
is not the original select. Removing the original node closes its native popup.
Consequently, an agent assignment menu can collapse before the user chooses an
agent. Restoring focus cannot restore browser-managed popup state.

Existing board and expanded-ticket snapshot routes already return the relevant
typed data and support ETags and HTTP 304 responses. The controller already has
serialized ticket actions, request sequencing, draft baselines, assignment
rendering, and selected-card updates. Extend those local responsibilities rather
than introducing another transport or frontend framework.

## Approved Scope

Use JSON snapshot reconciliation for automatic refresh on both the board with
its ticket inspector and the expanded ticket page. Preserve the existing
layout, native controls, filters, tabs, edit mode, new-ticket dialog, and
JavaScript-disabled forms.

Keep the two-second polling cadence, conditional ETag requests, hidden-page
pausing, and immediate refresh when the document becomes visible. Polling must
not fetch page HTML, replace the workflow page or inspector, change history or
the selected ticket, recreate live controls, or deliberately move focus or
scroll positions.

Intentional navigation continues to use the existing HTML path: selecting or
closing a ticket, expanding it, returning to the board, changing board filters,
and browser back/forward. This feature does not redesign navigation or change
ticket creation and mutation route semantics.

Do not change canonical configuration, ticket storage, job ownership, workflow
transitions, agent permissions, integrations, dependencies, or runtime-local
data. Resolving the separate external-runtime baseline failure described below
is not an application change authorized by this specification.

## Reconciliation Architecture

Separate automatic snapshot application from intentional page navigation in
the existing controller. A changed snapshot is parsed, checked against the
loaded view, and applied directly. There is no follow-up HTML request.

Reconcile board columns by their stable column key and cards by ticket identity
within the current binding. Preserve existing nodes for unchanged identities;
patch labels, title, assignment, run badge, selection styling, and link targets
only when their values differ. Create, remove, reorder, and move cards according
to the server snapshot. Update column counts, empty states, board totals, and
the working count consistently with that same snapshot.

Apply selected-ticket detail to its existing inspector nodes. Update state,
title, assignment, run state, clean inputs, read description, outputs,
requirements, history, and data issues without replacing the inspector shell,
tabs, edit region, forms, or dialog. Key repeated read-only rows by their existing
domain IDs so unaffected content and disclosure state remain stable.

Use `textContent` for untrusted plain text and existing route conventions for
links. Read descriptions must retain the existing server-side `ticket_markdown`
sanitization using `nh3`. Carry the sanitized description as additive web
presentation data in the same JSON response where necessary, including detail
mutation replies used by reconciliation. Do not introduce a second client-side
Markdown sanitizer or put raw ticket Markdown into `innerHTML`. Preserve the
meaning and contents of the domain view and ticket version fields.

The loaded binding and editable field IDs and kinds define what can be safely
reconciled. A different binding, mismatched selected-ticket identity, missing
required structure, or added/removed/type-changed editable field requires a
manual refresh notice. A normal change of ticket state or card membership does
not require refreshing the page. Reject incompatible snapshots before applying
partial changes to the view or adopting their mutation versions.

## Live Interaction And Drafts

Preserve DOM identity for form controls throughout polling. Do not assign a
focused control's value, replace its options, or change its disabled state while
the user is interacting with it. This includes ticket assignment, input fields,
board filters, and the open new-ticket dialog. Update surrounding read-only
content and safe controls normally; interaction is not a reason to stop polling
the whole board.

Defer an affected control's remote presentation until interaction finishes.
Retain only the latest applicable value, not a queue of every poll. On release,
apply it only if no local edit or submitted selection supersedes it. Similarly,
defer structural changes that would remove or relocate a focused card until
focus leaves that card. Preserve the selected tab, edit mode, caret, and local
drafts without blur/refocus tricks.

Synchronize current controls into the existing draft state before applying a
snapshot. Update clean, unfocused fields and their baselines from the server.
Never overwrite dirty values. A dirty draft may advance to a newer ticket
version only when every dirty field's original baseline still matches the
server. Otherwise retain its original base version so saving exposes the
existing stale-ticket conflict rather than overwriting a remote edit.

A remote update deferred while a clean field has focus must not become a new
baseline for text the user subsequently edits against the old displayed value.
Preserve that interaction's baseline for the conflict check. Preserve edits
made while a poll or action response is in flight, not only drafts captured
when the request started.

## Assignment And Concurrency

Capture an immutable copy of the selected ticket's version at the start of an
assignee selection interaction. Bind it to that ticket and selection attempt.
Pass that exact version with the selected value into the existing action queue;
do not read a newer polled version when the queued request eventually executes.
Programmatic changes without an earlier focus event capture their version when
the selection intent is received.

Any server version change during the interaction may produce the existing
HTTP 409 conflict. Do not automatically rebase or retry the assignment. On a
conflict, reconcile the current server assignment without replacing the control
and show an actionable assignment error. Preserve unrelated input drafts. A
new deliberate selection begins a new attempt using the current version, even
if focus has remained on the same select since the previous attempt.

Successful assignment and run requests continue to preserve ticket state and
use their authoritative JSON replies. Local action settlement may update its
own control's committed state; the protection against passive polling must not
prevent confirmation of the user's action. Keep the existing serialized action
queue and stale-version enforcement for input saves and runs.

A poll records the current navigation and action generations before requesting
data. Recheck them after fetching and parsing the body. Reject responses for an
older selection, filter navigation, or action generation, and do not apply a
snapshot over a pending mutation. Resume polling after action settlement. This
prevents an older poll from rolling back a newly accepted action or advancing a
draft to the wrong version.

Retain the ETag only for a snapshot successfully reconciled with the current
view. An ignored or incompatible response must not cause a later HTTP 304 to
conceal data that was never applied. Keep visibility cancellation and request
sequence checks effective throughout asynchronous body parsing.

## Failure Handling

Keep refresh status distinct from ticket action errors. A failed request,
invalid JSON, or unusable snapshot leaves the current UI and drafts intact,
reports a non-blocking refresh status, and retries on the normal cadence. A
successful compatible poll clears only the transient refresh status, never an
unrelated action error.

An incompatible view shows `Refresh required` with an explicit manual refresh
action. Do not reload automatically, including after focus leaves a control.
Unavailable or deleted selected tickets retain the local inspector and draft
with an availability status until the user intentionally navigates away or
refreshes; do not silently close the selection or adopt an unrelated ticket.
The server remains authoritative for rejecting unavailable or stale mutations.

HTTP 304 leaves the applied data and live controls untouched. Hidden documents
cancel scheduled polling and abort in-flight refreshes. Returning to visibility
requests current data without triggering page navigation.

## Verification

Reuse the existing workflow board Playwright suite, fixtures, remote-update
helpers, and mutation routes. Add behavior tests rather than relying on source
text assertions or element locators that silently resolve replacement nodes.

1. Retain a reference to the actual assignee DOM node, focus/open it, apply a
   changed snapshot, and prove that the same node remains connected and focused,
   with its displayed selection and options undisturbed. Complete an agent
   selection afterward and verify the server accepted it when its version is
   unchanged. Exercise a real keyboard selection sequence where supported, and
   perform a desktop native-popup smoke check; `selectOption` alone does not
   prove the popup stayed open.
2. Observe browser requests after initial load. Changed polling responses and
   visibility recovery must issue no page HTML request. Verify HTTP 304 and
   repeated changed snapshots do not mutate live controls.
3. Remotely create, remove, update, and move tickets. Verify keyed cards, column
   membership/order, counts, empty states, assignment, title, state, and queued,
   working, and idle status updates without replacing the page or inspector.
4. Verify clean fields and sanitized read descriptions update, while focused
   values, dirty drafts, edit mode, tabs, caret, and an open creation dialog
   survive. Test deferred-value release and edits made during in-flight work.
5. Change assignment remotely during an open local selection. Verify the local
   POST uses the interaction's original version, receives a conflict, does not
   overwrite the remote assignment, shows the current assignment and an error,
   and allows a new deliberate selection with a fresh version.
6. Cover poll/action and poll/navigation races, including delayed body parsing.
   Prove older responses cannot replace a newer selection or accepted mutation,
   overwrite drafts, or incorrectly advance the stored ETag.
7. Cover hidden-page abort/pause, visibility recovery, transient request/JSON
   failures and recovery, separate action errors, missing tickets, and an
   incompatible field schema. Require a manual refresh for incompatibility and
   prohibit automatic HTML replacement on every failure path.
8. Preserve existing assignment, input-save, run, history, artifact-link,
   navigation/filter, accessibility, responsive, and JavaScript-disabled tests.
   Run the existing desktop/mobile headless projects without changing unrelated
   snapshots or tolerances.

Follow the repository gates with the explicit user-approved exception below:
record the complete worktree baseline before implementation; use focused tests
while iterating; run complete Python and browser suites sequentially; review the
change; then perform the pre-authorized fast-forward, master verification,
publication, and owned-worktree cleanup.

## Baseline Investigation

Before any application change, the original global-user Python run at commit
`f6b5d8e` produced 3353 passed, 19 skipped, and three failures. Two wheel-isolation
tests could not import user-site PyYAML under `python -S`.

An ignored worktree-local virtual environment with the declared test extras
resolved both packaging checks. The targeted rerun produced two passed and one
failed. The remaining read-only live Copilot probe on CLI `1.0.92-3` completed
without making any ticket broker call. Its retained conversation reports a
refusal because a required tool-search tool was not supplied. This is evidence
of the refusal, not proof of a network or authentication failure or of the
actual tool catalog supplied to the model.

The complete isolated baseline is not yet established. Do not skip the runtime
probe, loosen assertions, switch models, widen grants, or change transport to
make this UI work appear green. Further runtime corrections require their own
authorized scope. The focused test receipt is retained under the ignored
worktree evidence directory.

### User-Approved Known-Failure Exception

On 2026-10-04 the user selected proceeding with the UI fix under an explicit
known-failure exception rather than continuing runtime diagnosis. The one
accepted external-runtime failure is
tests/test_ticket_runtime_live.py::test_restricted_agent_reads_ticket_over_http_without_editing[copilot].
Its test, markers, assertions, and security checks remain intact and execute in
every complete suite. Retain its failed receipts and disclose its actual status
at baseline, feature validation, integrated master verification, and completion.

The exception permits implementation and integration only when this is the sole
pre-existing failure and there are no new failures or errors. The complete
browser matrix and all behavior-scoped UI checks must pass. Do not broaden the
exception silently if another failure occurs, nor describe a red full suite as
green. Runtime metadata experiments are separated from this UI feature; the
unintegrated eager-exposure change must not be bundled into it.

## Alternatives

- Approved: consume the existing JSON snapshots and reconcile keyed nodes in
  place. This keeps live data and browser interaction intact and removes a
  redundant HTML fetch, at the cost of focused reconciliation logic.
- Not selected: pause polling whenever a control has focus. This is smaller,
  but makes unrelated statuses stale during long edits and retains the
  disruptive page-replacement mechanism when polling resumes.
- Rejected: retain whole-page or inspector HTML replacement and restore focus
  afterward. It reuses server markup but cannot preserve an open native popup
  or the identity of a live control.

## Acceptance Criteria

1. Automatic refresh no longer collapses the agent selection control, discards
   local interaction state, or performs a page HTML fetch.
2. Board and selected-ticket data remain current through in-place updates on
   both board and expanded-ticket pages.
3. Concurrent assignment is conflict-preserving, not silently last-writer-wins;
   dirty drafts retain the existing optimistic-concurrency protections.
4. Refresh failures and incompatible data preserve the loaded UI and recover or
   request explicit refresh without hiding unrelated action errors.
5. Navigation, HTML fallback, storage, jobs, security, and the existing visual
   design remain unchanged. Required baselines, reviews, and verification gates
   are satisfied before claiming implementation complete.