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