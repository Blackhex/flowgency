# First-Run Completion Navigation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Open the dashboard automatically when connected first-run setup is
ready, preserving the still-running CLI and explicit terminal inspection.

**Architecture:** The readiness poll uses the existing `session_view` and
`status_state` template context. An initially waiting page can redirect; an
already-ready connected terminal inspection cannot. Session ownership and process
lifecycle are unchanged.

**Tech Stack:** FastAPI, Jinja2, existing browser JavaScript, Playwright.

## Global Constraints

- Approved spec: `docs/superpowers/specs/2026-10-02-setup-completion-navigation-design.md`.
- No layout, copy, assets, schema, configuration files, or native backend changes.
- Navigation does not stop Copilot, assert cleanup, revoke ownership, or change
  authentication, setup launch exclusion, Stop, reconnect, or shutdown behavior.
- Missing, invalid, or incomplete configuration never triggers navigation.
- Owner-only dashboard session controls and explicit terminal inspection remain
  available while the CLI is running.
- No approved visual sketches exist for this navigation-only fix.
- Work only in `.worktrees/setup-completion-redirect` on its named feature branch.
- Keep Python and browser suites sequential; do not share fixture runtime gates.
- Do not print real terminal contents, authentication values, or user config.

## Task 1: Completion Navigation

**Files:**
- Modify: `flowgency/templates/setup.html`.
- Test: `tests/ui/setup.spec.ts`.

**Interfaces:**
- Consumes: `_setup_response` context `session_view: bool`, `status_state: str`;
  `GET /setup/status` JSON with `state` and optional `redirect`.
- Produces: automatic dashboard arrival from an initially waiting connected page,
  with an unchanged running setup session accessible through `View terminal`.

- [ ] Establish a clean complete Python baseline using this worktree's own
  `.venv` and `python -m pytest tests/ -q` before implementation.
- [ ] Replace the existing connected-readiness no-redirect test with this real
  browser regression, reusing the existing launch/reset helpers:

```typescript
test('connected setup terminal opens the dashboard when setup becomes ready', async ({ page, request }) => {
  await launchConnectedTerminal(page, request);

  const ready = await request.post('/__ui/setup/ready');
  expect(ready.status()).toBe(204);

  await expect(page).toHaveURL(/\/newsletter\/$/);
  await expect(page.getByRole('link', { name: 'View terminal' })).toBeVisible();
  const state = await page.evaluate(() => fetch('/setup/session/state', { cache: 'no-store' }).then((response) => response.json()));
  expect(state.state).toBe('running');

  await assertNoConsoleErrors(page);
});
```

- [ ] Run the regression before changing production code:
  `npm.cmd run test:ui -- tests/ui/setup.spec.ts --project=desktop-light -g "opens the dashboard when setup becomes ready"`.
  Expect the URL assertion to fail because the browser stays on `/setup/session`.
- [ ] Change the readiness guard in the existing template script:

```javascript
const preserveSessionView = sessionView && {{ 'true' if status_state == 'ready' else 'false' }};
```

  Require `!preserveSessionView`, rather than `!sessionView`, in the existing
  ready-plus-redirect condition. Keep all other polling behavior unchanged.
- [ ] Rerun the same focused regression. Then run the updated navigation and
  existing owner-only dashboard/terminal-return regressions across all projects:
  `npm.cmd run test:ui -- tests/ui/setup.spec.ts -g "opens the dashboard when setup becomes ready|dashboard surfaces the running setup session"`.
- [ ] Run complete `python -m pytest tests/ -q`, then complete
  `npm.cmd run test:ui` in default headless mode without updating snapshots.
- [ ] Review the task against the approved spec and review the whole narrow
  branch for regressions. Commit code and tests as
  `fix(setup): open dashboard when ready`.
- [ ] Fast-forward master only, run the full Python suite on master, and push
  both master and feature refs after verification. Keep current live user runtime
  and configuration untouched. Preserve gate evidence outside the worktree,
  remove/prune the worktree, and retain the feature branch.

## Final User Check

The current real trial is already configured and its dashboard is open. Do not
restart its live CLI or recreate its canonical configuration merely to test this
fix. Verify the new first-run transition through the browser fixture and verify
an explicit already-ready terminal visit remains stable.