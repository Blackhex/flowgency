# App-Wide Non-Disruptive Live Refresh

Date: 2026-10-06
Branch: `feature/app-live-refresh`
Worktree: `.worktrees/live-app/`
Status: written specification approved on 2026-10-06; implementation not started.

## Problem And Evidence

Inbox displayed a red scheduled-run state after the job had progressed. Only a
manual page reload showed the current healthy state. The
[`home route`](../../../flowgency/app.py) computes fleet, attention, queue,
workflow, and activity data for the initial HTML response, and
[`home.html`](../../../flowgency/templates/home.html) does not refresh them.

The user expanded the requirement from Inbox cards to the whole application:
every currently open page displaying live data must update, including shared
navigation counts. This is not an Inbox-only feature.

[`workflow-board.js`](../../../flowgency/static/workflow-board.js) already uses
conditional JSON snapshots, keyed reconciliation, request-generation guards,
draft preservation, hidden-page pausing, and immediate visibility recovery.
The approved
[`workflow refresh specification`](2026-10-04-workflow-live-reconciliation-design.md)
defines interaction and concurrency guarantees that must be preserved.
[`base.html`](../../../flowgency/templates/base.html) has a shared shell but no
general live-data refresh coordinator. Connected setup terminals have their own
persistent transport and lifecycle.

## Approved Scope

Cover every live-data page and shared live region, not just agent cards or the
current team Inbox. Keep the existing layouts and navigation. Each visible
page updates independently; one browser tab does not refresh or navigate
another tab.

Normal HTTP snapshot refresh runs every two seconds while a document is
visible. Hidden documents pause it and cancel in-flight passive reads. Becoming
visible triggers an immediate catch-up request. Background browser throttling
is not a freshness guarantee.

Update data in place without full-page reloads or destructive page replacement.
Preserve local working state and existing streaming connections. Static
content without live regions needs no additional periodic request.

The independent setup-completion workstream controls whether first-run setup
may navigate away. This work does not equate live configuration readiness with
completion, change approved scheduling choices, or require that the setup gate
wait for the app-wide rollout.

No visual redesign, new frontend framework, runtime event bus, authority model,
or storage format is included. No approved visual sketch was produced during
brainstorming.

## Approaches And Decision

Use a shared polling coordinator with page-specific snapshot reconciliation.
It matches the existing workflow implementation and can observe changes made
through the app, background workers, CLI, or canonical filesystem sources.
The coordinator centralizes scheduling and lifecycle; adapters own meaning and
safe presentation updates.

Server-pushed change notifications were considered. They can reduce idle
traffic and latency but would require reliable notification coverage across
configuration, files, jobs, tickets, and other independently changing sources.
That additional infrastructure is not required for this scope.

Independent per-page timers were also considered. They reduce initial shared
work but duplicate visibility, retries, cancellation, and race handling and
make complete application coverage harder to verify. They are not the chosen
long-term refresh architecture.

## Coverage Boundaries

The implementation plan must inventory every actual route and tab with live
data and map it to an adapter or an existing compatible live controller. The
following page families are mandatory coverage, not an optional rollout list:

| Page Family | Data That Must Stay Current | Local State To Preserve |
| --- | --- | --- |
| Inbox | Fleet cards and totals, health attention, work queue, workflow summaries/issues, ticket activity and Activity | Focus, scroll, disclosures, current team |
| Agent roster and detail tabs | Roster membership, identity/read-only summaries, health, run/routine state, activity, log listings, memory metadata and other live read-only sections | Run dialogs, selected tabs, profile/runtime/permission/routine/prompt/blueprint editors and drafts |
| Jobs | Job membership, queue position, lifecycle, results, diagnostics, timestamps and current output | Filters, selected job, disclosures, output scroll/follow state |
| Logs | Directory and entry listings, current growing log content and log metadata | Filters, selected log, scroll position and follow mode |
| Workflows and tickets | Existing board/detail live data and shared summaries/counts | Selected ticket, filters, tabs, native selectors, edit state, drafts and dialogs |
| Workspaces | Read-only workspace membership and currently displayed changing details/status | Current workspace, filters and action state |
| Administration and libraries | Dispatch status; team/integration/library/channel listings; source and memory read-only details; other changing status/metadata | Settings, storage checks, source editors, form drafts and selected records |
| Shared shell | Currently displayed team/workflow/workspace membership, counts and availability indicators | Active navigation, open mobile navigation, focus and theme |
| Setup/terminal views | Lifecycle-owned readiness, session and connection status | Completion gate, terminal instance, input, output and socket continuity |

Editable source or memory text is a local working document, not an invitation
to overwrite it with a remote file. Refresh surrounding read-only metadata and
report external changes without replacing the editor's contents. A form page
is not exempt from refreshing its safe live regions merely because it has a
draft.

Pages that already stream live output keep their transport and buffering. Do
not add a second stream or re-create the terminal on a snapshot change.
Lifecycle-owned setup polling must obey its completion contract and the agreed
visibility policy; it is not a generic content replacement operation.

## Architecture And Data Flow

Introduce one small shared refresh coordinator available to page controllers.
It owns the two-second cadence, visibility transitions, passive-request
cancellation, non-overlap, and rescheduling after completion or action
settlement. Pages register adapters for their current route/binding. Reuse
existing workflow reconciliation rather than rewriting it or running duplicate
timers beside it.

An adapter defines its snapshot source, current binding, compatibility check,
and reconciliation function. It knows which nodes and values are server-owned
and which belong to interaction or drafts. The coordinator does not understand
ticket fields, agent health, configuration forms, or terminal output.

Server snapshots reuse the domain/read-model builders and presentation rules
used for initial rendering. Resolve canonical configuration through the
existing store and request snapshot boundaries. Do not introduce directory
discovery, native-file authority, alternate team identities, or startup repair.

Each logical snapshot computes related values together: Inbox fleet totals and
health attention must agree with its cards; queue summaries must agree with
their entries; workflow counts must agree with the corresponding read model.
Do not claim a global transaction across independent filesystem-backed stores.

JSON snapshots provide stable domain identities, applicable revisions or
versions, and web presentation values. Use ETags and conditional requests;
HTTP 304 leaves already-applied data and live controls untouched. ETags must
represent the full reconciled presentation, including changing relative-time
labels, rather than treating unchanged record IDs as unchanged visible data.

Reconcile repeated items by stable identity. Patch changed text, safe classes,
attributes, links, counts and read-only content; add, remove, move and reorder
items where safe. Keep stable page shells, controls and dialogs connected.
Preserve initially rendered HTML and JavaScript-disabled forms.

Use `textContent` for untrusted plain text. Rich content must retain the
existing server-side sanitization and explicit trusted-presentation boundaries.
Do not interpolate raw user Markdown, paths or names into client HTML. Snapshot
routes retain existing authorization and must not expose owner-only setup
information to another browser or user.

## Interaction And Concurrency

Passive refresh must not replace a control, move focus, blur/refocus a field,
change an actively held value or option set, close a menu/dialog, reset filters,
or change history, selected entities, scroll positions, theme or follow state.
Preserve native control identity, caret, selection and disclosure state.

Continue updating unaffected read-only data while the user interacts. Defer
only the affected control or structural operation, retaining its latest remote
presentation. Before applying a deferred update, recheck binding, local intent
and whether a new edit or action superseded it.

Never overwrite dirty drafts. Do not silently advance a form's hidden revision
independently of the values and baselines it protects. Preserve existing
revision-checked writes and stale-edit conflicts. Page-specific controllers may
merge clean fields only when their complete baseline/conflict rules support it;
the generic coordinator must not invent that merge.

Preserve the workflow contract for frozen assignment intent, dirty-field
baselines, focus-deferred values and authoritative action replies. Passive
snapshot protection must not prevent a user's successful action from confirming
its own committed value.

Capture binding, navigation and action generations before passive reads and
recheck after response-body parsing. Reject superseded responses and do not
apply an older read over a pending mutation or newer accepted action. Keep at
most one relevant passive refresh in flight per adapter, with no duplicate
polling loop. Resume after settlement.

Retain an ETag only for a response accepted by the current view. An ignored or
incompatible response must not cause later 304 replies to hide unapplied data.
Mutation authorization, validation and concurrency remain server-enforced.

## Failure Handling

Network failures, invalid responses or unusable snapshots leave the last good
display, controls and drafts intact. Show a concise non-blocking stale-data
status through the existing visual language and retry on the normal cadence.
A successful compatible refresh clears only its transient refresh error, not
an unrelated action error. Do not create unbounded or overlapping retry loops.

A changed binding or incompatible editable structure requires an explicit
manual refresh notice. Do not reload automatically when focus leaves a field.
Removal of a selected entity reports unavailability without silently choosing
another record, discarding its draft or navigating away. Safe list membership
changes alone are ordinary reconciliation, not incompatibility.

Hidden-page cancellation must not stop server jobs or terminal sessions.
Immediate visibility recovery must not reconnect an otherwise healthy stream
or replay a mutation. Each document maintains its own lifecycle and guards.

## Verification

Reuse existing domain, route, UI fixtures and Playwright helpers. The plan must
list concrete routes/tabs and focused tests for every coverage-table family;
testing Inbox alone cannot establish app-wide completion.

1. Remotely change live data while each covered page is open. Prove that lists,
   details, summaries, counts and links update without manual reload, full-page
   HTML polling or page-shell replacement.
2. Complete an initially pending agent job while Inbox remains open. Verify its
   card, health totals, attention items, queue and relevant activity converge
   together, including relative/due timestamps.
3. Keep multiple pages open. Verify each visible page refreshes independently,
   hidden pages cancel/pause passive reads, and return to visibility immediately
   catches up without replaying actions or navigation.
4. Retain actual DOM references to active controls and dialogs. Prove identity,
   native-selector interaction, caret, focus, filters, tabs, disclosures, dirty
   drafts and scroll/follow state survive changed snapshots.
5. Cover action/read, navigation/read and delayed-body races, ETag acceptance,
   304 responses, deferred updates and stale-write conflicts.
6. Cover transient failure and recovery, invalid JSON, incompatible view
   structure, removed selection, isolated action errors and manual-refresh-only
   recovery where required.
7. Exercise hostile names, paths and rich content through snapshots. Preserve
   sanitization, authorization and owner-only session boundaries.
8. Prove existing terminal instances, output buffering and live connections
   remain continuous. Refresh status must not bypass first-run completion.
9. Preserve existing workflow interaction regressions, JavaScript-disabled
   forms, accessibility, responsive layouts and desktop/mobile headless gates.

Establish a clean complete-suite baseline in the active feature worktree before
implementation. Iterate with behavior-scoped checks; run full Python and
browser suites sequentially before review and integration. Preserve failed
captures and do not widen screenshot tolerances or rewrite unrelated snapshots.

## Review And Delivery

This specification is a documentation-only commit. User review of the written
specification is required before a separate implementation plan is written.
The plan must name the complete page inventory and concrete adapters, shared
coordinator work, focused verification and rollout order. Review each adapter
task before dependent work and perform a whole-branch review.

The setup-completion change can be delivered independently first. Its
acknowledgement and exit behavior are not replaced by this coordinator.
Implementation and integration follow the repository's named-worktree,
separate-plan-commit, fast-forward, verification, publication and owned-worktree
cleanup requirements. Preserve unrelated and runtime-local files.