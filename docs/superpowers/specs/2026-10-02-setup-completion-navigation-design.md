# First-Run Completion Navigation Design

Date: 2026-10-02

## Problem

A connected first-run setup can publish a valid configuration while its
interactive Copilot CLI remains running. The real `/setup/status` endpoint
reported `ready` with a `/` redirect, but the terminal page explicitly suppressed
navigation for every connected-session view. The user therefore remained on the
setup terminal even though the application was available.

The user selected automatic dashboard navigation rather than a manual completion
action. Copilot must remain available through the dashboard's existing owner-only
`View terminal` link.

## Decision

Automatically navigate an initially waiting setup page when `/setup/status`
reports `ready` and supplies its existing redirect. Do not wait for the CLI to
exit: readiness comes from the validated canonical configuration and usable
application services, not from a terminal message or process exit.

A connected terminal page rendered after setup is already ready is an inspection
view. It must remain on the terminal so an explicit `View terminal` visit does
not immediately bounce back to the dashboard. Use the existing server-rendered
`session_view` and `status_state` values to distinguish these views. This needs
no browser storage, new session identity, query parameter, or endpoint.

This decision supersedes the blanket session-view no-redirect requirement in the
earlier connected-setup design only for a page awaiting first-run completion.
Already-ready terminal inspection remains unchanged.

## Behavior

- Plain setup waiting pages retain their existing automatic navigation.
- Connected pages rendered before readiness navigate when a later readiness
  poll reports `ready` and a redirect.
- Connected pages rendered with `status_state == "ready"` do not navigate.
- Missing, invalid, or incomplete configuration never triggers navigation.
- Failed readiness requests retain the existing bounded-interval retry behavior.
- Navigation does not stop Copilot, assert cleanup, revoke ownership, or change
  authentication, setup launch exclusion, Stop, reconnect, or shutdown behavior.
- Owner-only dashboard session controls and explicit terminal inspection remain
  available while the CLI is running.

## Implementation

Change only the readiness-poll navigation condition in
`flowgency/templates/setup.html`. Derive its redirect permission from existing
template context instead of unconditionally excluding `data-session-view`.

Update the existing readiness regression in `tests/ui/setup.spec.ts` to require
automatic dashboard arrival and prove the session is still running. Keep the
neighboring owner-only dashboard and terminal-return tests as regression gates.

No layout, copy, assets, schema, configuration files, or native backend changes
are required. There are no approved visual sketches for this navigation-only fix.

## Verification

Establish the required clean full Python baseline in the fix worktree. First
run the changed browser regression against unchanged production code and observe
the expected URL assertion fail. After the template change, run the focused
navigation and terminal-return checks, then the complete Python suite and
unchanged four-project headless browser matrix sequentially.

Review the complete branch, fast-forward master, verify the complete suite on
master, push both refs, preserve generated evidence, and remove the worktree.
Keep the existing live user configuration and Copilot session untouched.