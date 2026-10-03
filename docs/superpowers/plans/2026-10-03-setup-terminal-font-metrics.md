# Setup Terminal Font Metrics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent stale fallback-font measurements from degrading the embedded Copilot terminal.

**Architecture:** Explicitly load the existing terminal faces before measurement, bounded by a 2-second fallback. If the preferred face arrives later, switch the public font option from stable monospace to the preferred stack and refit without resetting the terminal or reconnecting its socket.

**Tech Stack:** JavaScript, xterm 6.0.0, FitAddon 0.11.0, esbuild, FastAPI fixtures, pytest, and Playwright.

## Global Constraints

- Approved spec: `docs/superpowers/specs/2026-10-03-setup-terminal-font-metrics-design.md`, commit `f41591b`; written spec approved in conversation.
- Worktree: `C:/Projekty/Flowgency/.worktrees/setup-terminal-font-metrics`.
- Branch: `feature/setup-terminal-font-metrics`; base: `92307ad5bcab6f4efe531c5205c658e505d1ffcd`.
- Preserve JetBrains Mono at 13px, the existing renderer, layout, and controls.
- Initial font wait is bounded at 2 seconds; unavailable, empty, failed, or timed-out font loading yields a usable monospace terminal.
- Late successful loading remeasures through public APIs, never private xterm services or manual character styles.
- Preserve input, replay, resizing, reconnect limits, diagnostics, Stop, completion navigation, and access controls.
- No live Copilot interruption, user input, configuration/data repair, font assets, dependency manifests, or unrelated snapshots.
- No approved visual sketch exists; reproduce the reported spacing, then inspect screenshots without changing layout.
- Run commands from the active worktree; establish its full baseline before implementation.
- Use measured Playwright 1.61.1/Chromium 1228 locally without save/lockfile to avoid known native-focus snapshot drift; do not edit snapshots/tolerances.
- Complete Python and browser gates are sequential; never overlap their shared UI runtime.
- Follow task/branch review and pre-authorized repository integration, full master gate, both pushes, and owned-worktree cleanup. Retain the branch.
- User-approved gate amendment: `docs/superpowers/specs/2026-10-03-live-ticket-probe-contract-design.md`, commit `a6910d2`; written spec approved. Task 2 may clarify ticket reporting permission prose and correct live-probe contracts/helpers, but may not change actual permissions, transport, auth, model, markers, or runtime data.

## Preparation

- [ ] Create `.venv` in this worktree and install `.[test]`.
- [ ] Install existing Node dependencies without a lockfile and use the measured browser version:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --disable-pip-version-check -e '.[test]'
npm install --no-save --no-package-lock --no-audit --no-fund @playwright/test@1.61.1
```

- [ ] Inspect the installed xterm public option/refresh implementation to confirm font-family changes remeasure before fitting. Use the local package/map, not private runtime APIs.
- [ ] Run and retain the unchanged full baseline from this checkout:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/ -q --junitxml=.superpowers/font-baseline.junit.xml
```

## File Responsibilities

- `tools/setup-terminal.js`: bounded face loading, initial selection, and late public-option font change.
- `flowgency/static/setup-terminal.js` and generated CSS/legal asset: existing deterministic build output only.
- `tests/ui/setup.spec.ts`: real font-response timing regressions and session/geometry assertions.
- Reuse `tests/ui/layout.ts` font interception and existing private setup runtime controls. Modify a helper only if a focused test proves it necessary; no new production test hooks.

### Task 1: Font Readiness And Delayed-Load Recovery

**Interfaces:** Existing `Terminal`, `FitAddon`, `terminal.options.fontFamily`, `fitAndResize`, `sendSize`, and the existing connected fixture. No new public app interface or module.

- [ ] **Step 1: Add the real delayed-font regression.**

Register the exact font route after `installBasePageSetup` so it overrides the
existing deterministic response until released, then falls back to that helper.
Use a deferred promise, not sleeps, and the existing `launchConnectedTerminal`.

```typescript
test('late terminal font loading restores monospace metrics without resetting the session', async ({ page, request }) => {
  let releaseFont!: () => void;
  const heldFont = new Promise<void>((resolve) => { releaseFont = resolve; });
  await page.route('https://fonts.gstatic.com/test/jetbrains-mono/normal.woff2', async (route) => {
    await heldFont;
    await route.fallback();
  });

  try {
    await launchConnectedTerminal(page, request);
    await expect(page.locator('#terminal-connection')).toHaveText('Connected');
    const initial = await (await request.get('/__ui/setup/session/writes')).json();
    releaseFont();
    await page.evaluate(() => document.fonts.load('400 13px "JetBrains Mono"'));

    await expect.poll(async () => page.evaluate(() => {
      const context = document.createElement('canvas').getContext('2d')!;
      context.font = '13px "JetBrains Mono"';
      const expectedWidth = context.measureText('W').width;
      const spans = [...document.querySelectorAll<HTMLElement>('.xterm-rows span')];
      const word = spans.find(span => /^[A-Za-z]{4,}$/.test(span.textContent || ''));
      if (!word) return false;
      const cellWidth = word.getBoundingClientRect().width / word.textContent!.length;
      return Math.abs(cellWidth - expectedWidth) < 1;
    })).toBe(true);

    await page.locator('#setup-terminal').click();
    await page.keyboard.type('font-check');
    await expect.poll(async () => {
      const writes = await (await request.get('/__ui/setup/session/writes')).json();
      return writes.writes.join('');
    }).toContain('font-check');
    const final = await (await request.get('/__ui/setup/session/writes')).json();
    expect(final.sizes.length).toBeGreaterThanOrEqual(initial.sizes.length);
    const [rows, cols] = final.sizes[final.sizes.length - 1];
    expect(rows).toBeGreaterThanOrEqual(2);
    expect(cols).toBeGreaterThanOrEqual(20);
    const state = await page.evaluate(() => fetch('/setup/session/state').then(response => response.json()));
    expect(state.state).toBe('running');
    await assertNoConsoleErrors(page);
  } finally {
    releaseFont();
  }
});
```

Ensure the held request survives beyond the 2-second bound: the terminal must
become connected on fallback before release. Add a known ASCII fixture marker
through the existing emit control if the current fake output has no suitable
word; do not assert text from a live user session. Measure the known marker
instead of an arbitrary colored span if needed. Assert the marker/history
survives late loading.
Do not demand an extra resize message when the fallback and preferred font
produce identical rows/columns. For a changed grid, compare the latest server
geometry with the rendered row count and screen/cell width using the same
device-pixel tolerance as the physical metric check.

- [ ] **Step 2: Observe the specific red failure.**

```powershell
npx playwright test tests/ui/setup.spec.ts --grep "late terminal font loading" --project=desktop-light
```

Expected: the current bundle retains fallback-sized cells after the font loads.
Fix test setup problems before claiming red evidence.

- [ ] **Step 3: Add the bounded loader and initial/late selection.**

Use focused constants and a local loader; do not add another module:

```javascript
const PREFERRED_TERMINAL_FONT = '"JetBrains Mono", monospace';
const FALLBACK_TERMINAL_FONT = 'monospace';
const TERMINAL_FONT_WAIT_MS = 2000;

const terminalFontReady = typeof document.fonts?.load === 'function'
  ? Promise.all([
      document.fonts.load('400 13px "JetBrains Mono"'),
      document.fonts.load('500 13px "JetBrains Mono"'),
    ]).then(faces => faces.every(group => group.length > 0), () => false)
  : Promise.resolve(false);

let fontWaitTimer;
const initialFontReady = await Promise.race([
  terminalFontReady,
  new Promise(resolve => {
    fontWaitTimer = window.setTimeout(() => resolve(false), TERMINAL_FONT_WAIT_MS);
  }),
]);
window.clearTimeout(fontWaitTimer);
```

Make the current IIFE async and initialize the terminal with the selected font
before `terminal.open`. Keep all current socket/terminal event handlers and
initial fitting order. Remove the ineffective fonts-ready-fit callback.
Register one late continuation after initialization:

```javascript
terminalFontReady.then(loaded => {
  if (!loaded || initialFontReady || !container.isConnected) return;
  terminal.options.fontFamily = PREFERRED_TERMINAL_FONT;
  fitAndResize();
});
```

Do not reset the terminal or call `connect` from this continuation. Reuse the
current resize path so new geometry reaches the active socket. Verify the
public option change actually refreshes cells with the failing test; if the
local xterm contract differs, adjust only this initialization path through
documented public options and retain the behavioral assertion.

- [ ] **Step 4: Rebuild and immediately rerun the same regression.**

```powershell
npm run build:terminal
npx playwright test tests/ui/setup.spec.ts --grep "late terminal font loading" --project=desktop-light
```

Expected: usable fallback, recovered metrics, running session and intact input.

- [ ] **Step 5: Extend immediate, failed, empty, and unavailable loading coverage.**

Use real responses for immediate loading. For a failed face, fulfill the exact
font response with an empty HTTP 200 body so it fails decoding without making
a network failure the assertion; inspect and account for console behavior
without suppressing unrelated errors. For missing API, set only the `load`
method unavailable in an init script, keeping the rest of FontFaceSet intact.
For an empty result, replace only `document.fonts.load` with an async empty
result. In every case assert connected status, usable terminal cells, ASCII
monospace W/i equality, working keyboard input, and running session state.
The unavailable/empty cases must not wait for an absent request.

Extract one private metric-check helper into the existing spec file when it
removes real duplication. Use literals/known fixture text for expected behavior
and canvas measurements only for physical font metrics, not xterm internals.

- [ ] **Step 6: Run complete setup coverage in all four default projects and inspect screenshots.**

```powershell
npx playwright test tests/ui/setup.spec.ts
.\.venv\Scripts\python.exe -m pytest tests/test_setup_assets.py -q
git diff --check
```

Run browser and Python sequentially. Check generated terminal/CSS/legal assets
remain deterministic and packaging includes the rebuilt bundle. Capture the
fake terminal in cold and late-loaded states for desktop/mobile, and inspect
character spacing and framing without rebaselining unrelated snapshots.

- [ ] **Step 7: Review and commit this focused implementation slice.**

```powershell
git add -- tools/setup-terminal.js flowgency/static/setup-terminal.js flowgency/static/setup-terminal.css flowgency/static/setup-terminal.js.LEGAL.txt tests/ui/setup.spec.ts
git commit -m "fix(setup): measure terminal after font readiness"
```

Stage only generated files that actually changed. Inspect file diagnostics and
review the task before whole-branch gates. Do not touch the live user browser
or answer its workspace question while testing.

### Task 2: Strict Semantic Live Ticket Acceptance

**Files:**
- Modify: `flowgency/tickets/reporting.py`
- Modify/test: `tests/test_ticket_reporting.py`
- Modify: `tests/_runtime_probe_helpers.py`
- Modify/test: `tests/test_ticket_runtime_live.py`
- Reuse: deterministic ticket fixtures and authenticated observer boundary.

**Interfaces:**
- Production `build_ticket_reporting_protocol` and `append_ticket_reporting_protocol` retain signatures and append-once behavior.
- Existing `record_ticket_tool_calls` entries retain all fields and gain safe copied `request_version` / `response_version` evidence where present.
- New test-only `assert_read_only_ticket_probe(calls, ticket_id)` and `assert_stale_refresh_sign_off_probe(calls, ticket_a_id, ticket_b_id)` assert semantic broker sequences, not fixed operation-ID names.

This task is explicitly approved scope expansion to resolve the blocked full
gate. No font implementation changes, model changes, weakened markers, new
public APIs, backend transport changes, or permission widening.

- [ ] **Step 1: Add a deterministic regression for valid recovery with different operation names.**

Add to the existing live-test module outside the installed-runtime conditional,
so it always runs without needing Copilot. Start with literal, hand-checked
observer entries matching the existing event schema and minimal version copies:

```python
def test_semantic_ticket_probe_accepts_fresh_recovery_operation_names():
  ref_a = {"binding_id": "binding-a", "team_id": "team-a",
       "workflow_id": "board-a", "ticket_id": "ticket-a"}
  ref_b = {"binding_id": "binding-b", "team_id": "team-a",
       "workflow_id": "board-b", "ticket_id": "ticket-b"}
  version_old = {"ref": ref_a, "revision": 3,
           "workflow_digest": "definition-a", "context_digest": "context-a"}
  version_new = {"ref": ref_a, "revision": 4,
           "workflow_digest": "definition-a", "context_digest": "context-a"}
  version_b = {"ref": ref_b, "revision": 2,
         "workflow_digest": "definition-b", "context_digest": "context-b"}
  calls = [
    {"tool": "ticket_get", "ticket_id": "ticket-a", "ok": True,
     "response_version": version_old},
    {"tool": "ticket_start_work", "ticket_id": "ticket-a", "ok": True,
     "operation_id": "begin-first-ticket", "request_version": version_old},
    {"tool": "ticket_transition", "ticket_id": "ticket-a", "ok": False,
     "error_code": "stale-ticket", "operation_id": "attempt-first",
     "request_version": version_old},
    {"tool": "ticket_get", "ticket_id": "ticket-a", "ok": True,
     "response_version": version_new},
    {"tool": "ticket_transition", "ticket_id": "ticket-a", "ok": True,
     "operation_id": "recovered-completion", "request_version": version_new},
    {"tool": "ticket_start_work", "ticket_id": "ticket-b", "ok": True,
     "operation_id": "begin-follow-up"},
    {"tool": "ticket_get", "ticket_id": "ticket-b", "ok": True,
     "response_version": version_b},
    {"tool": "ticket_sign_off", "ticket_id": "ticket-b", "ok": True,
     "operation_id": "recovered-sign-off", "request_version": version_b},
  ]
  assert_stale_refresh_sign_off_probe(calls, "ticket-a", "ticket-b")
```

This fixture tests the trace assertion contract, not a mocked broker. Keep the
existing real broker observer test to verify recording authenticity. Retained
final ticket states are asserted through the real service/provider in the live
test and deterministic fixture integrations, not fabricated in these entries.

- [ ] **Step 2: Run the exact red regression.**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_ticket_runtime_live.py::test_semantic_ticket_probe_accepts_fresh_recovery_operation_names -q
```

Expected: the semantic assertion helper is absent or the current fixed-ID
implementation rejects the valid trace. Do not modify font source or execute
another full suite before this local check.

- [ ] **Step 3: Implement small test-only sequence assertions.**

Place them in the existing module, not a new framework. Use an ordered search
with useful diagnostics; require successful operations on the intended ticket.

```python
def _find_probe_event(calls, start, *, tool, ticket_id, ok, error_code=None):
  for index in range(start, len(calls)):
    call = calls[index]
    if (call.get("tool") == tool
        and call.get("ticket_id") == ticket_id
        and call.get("ok") is ok
        and (error_code is None or call.get("error_code") == error_code)):
      return index, call
  raise AssertionError(f"Missing {tool} outcome for {ticket_id}: ok={ok}, error={error_code}")


def assert_read_only_ticket_probe(calls, ticket_id):
  assert calls, "No ticket broker calls were observed"
  assert all(call.get("tool") == "ticket_get" for call in calls), calls
  assert all(call.get("ticket_id") == ticket_id and call.get("ok") is True
         for call in calls), calls


def assert_stale_refresh_sign_off_probe(calls, ticket_a_id, ticket_b_id):
  start_a_index, _started_a = _find_probe_event(
    calls, 0, tool="ticket_start_work", ticket_id=ticket_a_id, ok=True,
  )
  stale_index, stale = _find_probe_event(
    calls, start_a_index + 1, tool="ticket_transition", ticket_id=ticket_a_id,
    ok=False, error_code="stale-ticket",
  )
  retry_index, retry = _find_probe_event(
    calls, stale_index + 1, tool="ticket_transition", ticket_id=ticket_a_id, ok=True,
  )
  fresh_reads = [call for call in calls[stale_index + 1:retry_index]
           if call.get("tool") == "ticket_get"
           and call.get("ticket_id") == ticket_a_id
           and call.get("ok") is True]
  assert fresh_reads, "Successful fresh read is required before completion retry"
  fresh = fresh_reads[-1]
  assert fresh.get("response_version"), fresh
  assert fresh["response_version"] != stale.get("request_version"), (stale, fresh)
  assert retry.get("request_version") == fresh["response_version"], (fresh, retry)
  assert isinstance(retry.get("operation_id"), str) and retry["operation_id"].strip(), retry

  start_index, _started = _find_probe_event(
    calls, 0, tool="ticket_start_work", ticket_id=ticket_b_id, ok=True,
  )
  signoff_index, signed_off = _find_probe_event(
    calls, start_index + 1, tool="ticket_sign_off", ticket_id=ticket_b_id, ok=True,
  )
  current_reads = [call for call in calls[start_index + 1:signoff_index]
           if call.get("tool") == "ticket_get"
           and call.get("ticket_id") == ticket_b_id
           and call.get("ok") is True]
  assert current_reads, "Successful current read is required before sign-off"
  current = current_reads[-1]
  assert current.get("response_version"), current
  assert signed_off.get("request_version") == current["response_version"], (current, signed_off)
  assert isinstance(signed_off.get("operation_id"), str) and signed_off["operation_id"].strip(), signed_off
```

Use the most recent successful read before each accepted mutation as shown,
including when intervening reads occur. Keep helper output and
types modest; eliminate unused local names. A fresh version must belong to the
same scoped ticket reference; comparing complete curated version objects does
not license accepting a revision-only match on another ticket.

Immediately rerun Step 2, then add negative cases that remove or corrupt each
required event/version. Require every negative trace to raise AssertionError,
including wrong ticket, no successful read, no stale denial, stale retry without
fresh read, old-version retry, no work start, and no accepted sign-off.

- [ ] **Step 4: Extend the real observer with minimal safe version evidence.**

In `record_ticket_tool_calls`, copy only the protocol's version fields from
request `payload["version"]` and returned command envelope's version. Inspect
the shared command response contract to use the actual response location.
Preserve existing ticket ID/error/operation fields and hooks. Never capture
token, headers, auth, or arbitrary payload contents. Use existing `ref` and
version models/parser, not JSON substring extraction.

The safe-copy boundary is:

```python
def _observed_version(value):
  if not isinstance(value, dict):
    return None
  ref = value.get("ref")
  if not isinstance(ref, dict):
    return None
  return {
    "ref": {name: ref[name] for name in
        ("binding_id", "team_id", "workflow_id", "ticket_id") if name in ref},
    **{name: value[name] for name in
       ("revision", "workflow_digest", "context_digest") if name in value},
  }
```

Add a real fixture/broker observation check proving captured get version and
subsequent request version match, with no credential fields. Keep failed stale
version evidence too. Do not log successful ticket payloads or private headers.

- [ ] **Step 5: Correct permission prose without changing grants.**

Update `_tool_sentence`/reporting text to describe the workspace filesystem tool
policy separately from supplied live ticket operations. Include this meaning:

```text
Your workspace/filesystem tool policy is an allowlist: read, search.
Live Flowgency ticket tools, when supplied by this job's authenticated ticket
channel, are governed separately and do not require workspace write access.
If the ticket tools are not supplied, report that blocker; do not invent a
ticket result, broaden network access, or modify ticket storage directly.
```

Retain configured tool names dynamically, no-tool/all-tool wording semantics,
non-ticket behavior, and append-once contract. Use failing consumer-level
protocol/rendering assertions and the unchanged live read probe as behavioral
evidence; avoid adding tests that only grep a new source-code sentence. Existing
protocol tests are output-contract tests; preserve meaningful unchanged checks.

- [ ] **Step 6: Wire semantic assertions into live probes and causal stale injection.**

The read-only test calls its semantic helper on real captured calls, while
keeping status/exit, MCP inventory, protected hashes, and final no-active-run
checks. Keep the exact fully scoped JSON ref; format it clearly for the agent
without replacing or fabricating fields.

Force staleness on the intended ticket A's first completion transition, detected
by operation and scoped payload ticket ID, not `complete-stale` spelling. Then
call the semantic helper and retain all current final ticket/result assertions.
Require the agent to inspect real failures and recover using current versions;
operation names are nonempty idempotency identifiers, not an acceptance regex.
Earlier denied sign-off attempts do not count as success. Final assignment and
active-run checks on B remain strict. Do not trust its final prose/exit alone.

- [ ] **Step 7: Run deterministic coverage, then the two unchanged real-runtime cases.**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_ticket_reporting.py tests/test_ticket_runtime_live.py -q -m "not real_runtime"
.\.venv\Scripts\python.exe -m pytest tests/test_ticket_runtime_live.py -q -k "restricted_agent_reads_ticket_over_http_without_editing or restricted_agent_refreshes_stale_transition_and_signs_off_second_ticket"
```

First command is focused deterministic coverage only, not a replacement full
gate. Keep the canonical model, real installed CLI, auth/network/timeout failure
policy and runtime marker unchanged. If live cases still fail, inspect fresh
selected/redacted evidence; do not retry until green, change models, skip, or
weaken semantics. Escalate a genuine persistent blocker.

- [ ] **Step 8: Review and commit this amended task.**

Inspect diagnostics and git diff --check. Report exact red/green proof, retained
negative contract coverage, actual live observations, warnings, changed files,
and remaining uncertainty. Stage only the listed task files:

```powershell
git add -- flowgency/tickets/reporting.py tests/test_ticket_reporting.py tests/_runtime_probe_helpers.py tests/test_ticket_runtime_live.py
git commit -m "fix(tests): assert semantic live ticket contracts"
```

Review this task before rerunning complete combined-branch gates. Preserve the
original failed full/recheck receipts and font-task completion in the ledger.

## Complete Verification And Integration

- [ ] Run complete Python suite and retain JUnit; then run default-headless full Playwright matrix.
- [ ] Review the branch against all five acceptance criteria and investigate failures within scope without snapshot/tolerance changes.
- [ ] Follow the repository's pre-authorized fast-forward, complete master suite, push both refs, evidence preservation, and owned-worktree removal/prune.
- [ ] Report actual checks and any baseline warning/tooling caveat; tell the user only browser reload is needed for rendering, not Copilot session termination.

## Plan Self-Review

- [x] All five acceptance criteria map to the one cohesive task and complete gates.
- [x] Late readiness uses the same promise and public font-option path as initialization.
- [x] Fixtures remain test-only and existing input/resize/security behavior is preserved.
- [x] No prose-substring tests, private renderer access, new assets, or unrelated cleanup.
- [x] Baseline, focused red/green, generated asset build, reviews, and sequential complete gates are explicit.