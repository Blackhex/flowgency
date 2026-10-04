# Read-Only Ticket Probe Tool Name

Date: 2026-10-04
Branch: `feature/workflow-live-reconciliation`
Worktree: `.worktrees/workflow-live-reconciliation/`
Status: concrete test-only design approved by the user.

## Evidence And Hypothesis

The read-only Copilot probe retrieves the authenticated 12-tool MCP catalog,
including ticket_get, but performs no broker operation. Its task requests the
generic ticket_get name. A retained successful restricted HTTP probe uses the
Copilot-visible name flowgency-tickets-ticket_get directly, without native tool
search. Explicit naming may avoid the failed discovery path; the strict live
check must confirm or falsify that hypothesis.

## Approved Correction

Change only tests/test_ticket_runtime_live.py::_read_only_agent_task to request
flowgency-tickets-ticket_get at both its tool declaration and explicit call
step. This helper's live consumers are Copilot-specific. Preserve the supplied
ticket reference and every read-only/no-other-tool restriction.

Add a neighboring offline generator regression for the qualified call name and
reference/read-only invariants. Do not change any live scoped-reference,
successful-read, MCP inventory, no-active-work, protected-hash, or no-write
assertion. Do not add mock success, retries, skips, or alternate acceptance.

The production adapter, canonical configuration, model, sandbox, permissions,
network consent, transport, SDK, dependencies, and saved user prompts remain
unchanged. No visual design or assets are involved.

## Verification And Handoff

Follow TDD for the offline regression and smallest wording edit, then task
review. The controller runs the existing strict live read-only test with the
corrected task and retains its JUnit receipt. A successful real broker read
under the same read-only policy is required; generator success alone is not.

If the live test still fails before a broker read, reject the naming hypothesis
and stop this prerequisite without stacking a workaround. If it passes, obtain
the complete isolated Python and browser baselines sequentially before starting
the approved workflow UI plan. Integrate neither a failed prerequisite nor
unverified UI work.

This is a separately scoped test prerequisite in its own atomic commits on the
existing isolated feature branch. Keep the specification and plan in separate
documentation-only commits before the test edit. Review and final publication
follow the repository workflow once all required gates pass.