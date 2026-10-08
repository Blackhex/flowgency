import { expect, test, type APIRequestContext, type Page } from '@playwright/test';
import { mkdir, readFile } from 'node:fs/promises';
import path from 'node:path';

import { assertNoConsoleErrors, assertNoTailwindCdnRequests, installBasePageSetup } from './layout';

const evidenceRoot = path.resolve('.superpowers', 'sdd', '2026-10-03-setup-terminal-font-metrics', 'evidence');
const WIDE_MARKER = 'WWWWWWWW';
const NARROW_MARKER = 'iiiiiiii';

async function resetUiRuntime(request: APIRequestContext, fixture = 'default'): Promise<void> {
  const response = await request.post('/__ui/reset', { data: { fixture } });
  expect(response.status()).toBe(204);
}

async function connectedSetupDataRoot(request: APIRequestContext): Promise<string> {
  const meta = await (await request.get('/__ui/setup/meta')).json();
  return meta.data_root as string;
}

async function launchConnectedTerminal(page: Page, request: APIRequestContext): Promise<void> {
  await resetUiRuntime(request, 'connected-setup');
  const dataRoot = await connectedSetupDataRoot(request);
  await page.goto('/setup', { waitUntil: 'domcontentloaded' });
  await page.getByLabel('Flowgency data root', { exact: true }).fill(dataRoot);
  await page.getByRole('button', { name: 'Continue in GitHub Copilot' }).click();
  await expect(page).toHaveURL(/\/setup\/session$/);
  await expect(page.locator('#setup-terminal .xterm-screen')).toBeVisible();
}

type CompletionBody = {
  scheduler_result: string;
  limitations_acknowledged?: boolean;
  revision?: 'current' | 'stale';
};

async function completeSetup(
  request: APIRequestContext,
  data: CompletionBody = { scheduler_result: 'confirmed' },
) {
  return request.post('/__ui/setup/session/complete', { data });
}

async function markSetupReady(request: APIRequestContext): Promise<void> {
  const response = await request.post('/__ui/setup/ready');
  expect(response.status()).toBe(204);
}

async function setupStatus(page: Page): Promise<Record<string, any>> {
  return page.evaluate(() => fetch('/setup/status', { cache: 'no-store' }).then((response) => response.json()));
}

async function waitForStatusPolls(page: Page, count: number): Promise<void> {
  for (let poll = 0; poll < count; poll += 1) {
    await page.waitForResponse((response) => new URL(response.url()).pathname === '/setup/status');
  }
}

function trackSetupSockets(page: Page): string[] {
  const sockets: string[] = [];
  page.on('websocket', (socket) => {
    if (socket.url().includes('/setup/session/ws')) sockets.push(socket.url());
  });
  return sockets;
}

function trackStopRequests(page: Page): string[] {
  const stops: string[] = [];
  page.on('request', (request) => {
    if (new URL(request.url()).pathname === '/setup/session/stop') stops.push(request.method());
  });
  return stops;
}

function trackMainFrameNavigations(page: Page): string[] {
  const paths: string[] = [];
  page.on('framenavigated', (frame) => {
    if (frame === page.mainFrame()) paths.push(new URL(frame.url()).pathname);
  });
  return paths;
}

// The redirect destroys the document, so the pagehide handler records, while it
// is still alive, whether the original xterm node was still the live one.
async function trackTerminalContinuity(page: Page) {
  await page.evaluate(() => {
    const node = document.querySelector('#setup-terminal .xterm');
    window.addEventListener('pagehide', () => {
      sessionStorage.setItem('terminal-continuity', JSON.stringify({
        connected: Boolean(node && node.isConnected),
        same: document.querySelector('#setup-terminal .xterm') === node,
      }));
    });
  });
  const handle = await page.locator('#setup-terminal .xterm').elementHandle();
  if (!handle) throw new Error('xterm element is not present');
  return handle;
}

async function recordedContinuity(page: Page): Promise<unknown> {
  return page.evaluate(() => JSON.parse(sessionStorage.getItem('terminal-continuity') ?? 'null'));
}

async function runtimeConfigBytes(request: APIRequestContext): Promise<Buffer> {
  const dataRoot = await connectedSetupDataRoot(request);
  return readFile(path.join(path.dirname(dataRoot), 'config.yaml'));
}

async function captureEvidence(page: Page, name: string): Promise<void> {
  await mkdir(evidenceRoot, { recursive: true });
  await page.screenshot({ path: path.join(evidenceRoot, name), fullPage: true });
}

async function emitTerminalText(request: APIRequestContext, text: string): Promise<void> {
  const response = await request.post('/__ui/setup/session/emit', { data: { text } });
  expect(response.status()).toBe(204);
}

async function emitTerminalMetricMarkers(page: Page, request: APIRequestContext): Promise<void> {
  await emitTerminalText(request, `${WIDE_MARKER}\r\n${NARROW_MARKER}\r\n`);
  await expect(page.locator('#setup-terminal').getByText(WIDE_MARKER, { exact: true })).toBeVisible();
  await expect(page.locator('#setup-terminal').getByText(NARROW_MARKER, { exact: true })).toBeVisible();
}

async function terminalMarkerMetrics(page: Page, marker: string, fontFamily: string): Promise<false | {
  actualCellWidth: number;
  expectedCellWidth: number;
  renderedTextWidth: number;
  rowCount: number;
  rowHeight: number;
  screenHeight: number;
  screenWidth: number;
}> {
  return page.evaluate(({ font, text }) => {
    const context = document.createElement('canvas').getContext('2d');
    if (!context) return false;
    context.font = `13px ${font}`;
    const screen = document.querySelector<HTMLElement>('#setup-terminal .xterm-screen');
    const rows = document.querySelector<HTMLElement>('#setup-terminal .xterm-rows');
    const target = [...document.querySelectorAll<HTMLElement>('.xterm-rows span')]
      .find((span) => span.textContent === text);
    const firstRow = rows?.firstElementChild;
    if (!screen || !rows || !target || !(firstRow instanceof HTMLElement)) return false;
    const renderedTextWidth = target.getBoundingClientRect().width;
    return {
      actualCellWidth: renderedTextWidth / text.length,
      expectedCellWidth: context.measureText('W').width,
      renderedTextWidth,
      rowCount: rows.childElementCount,
      rowHeight: firstRow.getBoundingClientRect().height,
      screenHeight: screen.getBoundingClientRect().height,
      screenWidth: screen.getBoundingClientRect().width,
    };
  }, { font: fontFamily, text: marker });
}

async function assertTerminalMetrics(page: Page, request: APIRequestContext, expectedFontFamily: string): Promise<[number, number]> {
  await expect.poll(async () => {
    const wide = await terminalMarkerMetrics(page, WIDE_MARKER, expectedFontFamily);
    const narrow = await terminalMarkerMetrics(page, NARROW_MARKER, expectedFontFamily);
    if (!wide || !narrow) return false;
    const writes = await (await request.get('/__ui/setup/session/writes')).json();
    const latestSize = writes.sizes[writes.sizes.length - 1];
    if (!latestSize) return false;
    const rows = latestSize[0];
    const cols = latestSize[1];
    const cellWidth = (wide.actualCellWidth + narrow.actualCellWidth) / 2;
    const rowHeight = (wide.rowHeight + narrow.rowHeight) / 2;
    return Math.abs(wide.actualCellWidth - wide.expectedCellWidth) < 1
      && Math.abs(narrow.actualCellWidth - narrow.expectedCellWidth) < 1
      && Math.abs(wide.actualCellWidth - narrow.actualCellWidth) < 1
      && Math.abs(rows - wide.rowCount) < 1
      && Math.abs(rows - wide.screenHeight / rowHeight) < 1
      && Math.abs(cols - wide.screenWidth / cellWidth) < 1;
  }).toBe(true);
  const writes = await (await request.get('/__ui/setup/session/writes')).json();
  return writes.sizes[writes.sizes.length - 1];
}

async function assertTerminalInputAndState(page: Page, request: APIRequestContext, text: string): Promise<void> {
  await page.locator('#setup-terminal').click();
  await page.keyboard.type(text);
  await expect
    .poll(async () => {
      const writes = await (await request.get('/__ui/setup/session/writes')).json();
      return writes.writes.join('');
    })
    .toContain(text);
  const state = await page.evaluate(() => fetch('/setup/session/state', { cache: 'no-store' }).then((response) => response.json()));
  expect(state.state).toBe('running');
}

test.afterEach(async ({ request }) => {
  await resetUiRuntime(request, 'default');
});

// setup_complete.html is a standalone document (it does not extend base.html),
// so it needs its own regression: no Tailwind CDN dependency and a rendered,
// non-overflowing layout on both desktop and mobile viewports.
test.beforeEach(async ({ page }, testInfo) => {
  await installBasePageSetup(page, testInfo.project.name.endsWith('dark') ? 'dark' : 'light');
});

test('setup completion page renders its themed layout without the Tailwind CDN', async ({ page }) => {
  await page.goto('/setup/complete/newsletter');

  await expect(page.getByRole('heading', { name: 'Now go outside and touch grass for a while' })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Or, add another agent team →' })).toBeVisible();

  const overflowsViewport = await page.evaluate(() => {
    const root = document.documentElement;
    return root.scrollWidth > root.clientWidth + 1;
  });
  expect(overflowsViewport).toBe(false);

  await assertNoTailwindCdnRequests(page);
  await assertNoConsoleErrors(page);
});

test('connected setup terminal survives reload and status fetch failure', async ({ page, request }, testInfo) => {
  await launchConnectedTerminal(page, request);

  // Simulate a status fetch failure without a real network-level error:
  // Chromium logs a "Failed to load resource" console error for both an
  // aborted request and a non-2xx response, so return 200 with a body that
  // fails to parse as JSON instead — the app's own catch branch still fires.
  await page.route('**/setup/status', (route) => route.fulfill({ status: 200, contentType: 'text/plain', body: 'not json' }));
  await expect(page.locator('#status-message')).toContainText('Retrying');
  await page.unroute('**/setup/status');

  await page.reload();
  await expect(page.locator('#setup-terminal .xterm-screen')).toBeVisible();

  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1);
  expect(overflow).toBe(false);

  await captureEvidence(page, `connected-terminal-${testInfo.project.name}.png`);
  await assertNoConsoleErrors(page);
  await assertNoTailwindCdnRequests(page);
});

test('a failing status poll keeps the terminal during pending and during completion, then retries into the single redirect', async ({ page, request }) => {
  const sockets = trackSetupSockets(page);
  const stops = trackStopRequests(page);
  await launchConnectedTerminal(page, request);
  const terminal = await trackTerminalContinuity(page);
  await markSetupReady(request);
  await waitForStatusPolls(page, 1);
  await expect(page).toHaveURL(/\/setup\/session$/);
  const navigations = trackMainFrameNavigations(page);

  // (a) config is ready but the attempt is still pending: a failing poll
  // reports "Retrying" without losing the terminal or leaving the page.
  await page.route('**/setup/status', (route) => route.fulfill({ status: 200, contentType: 'text/plain', body: 'not json' }));
  await expect(page.locator('#status-message')).toContainText('Retrying');
  expect(await terminal.evaluate((node) => node.isConnected)).toBe(true);
  await expect(page).toHaveURL(/\/setup\/session$/);
  expect(navigations).toEqual([]);

  // (b) unrouting lets the next poll succeed and the message recover.
  await page.unroute('**/setup/status');
  await expect(page.locator('#status-message')).toContainText('Waiting for setup to report completion.');
  expect(await terminal.evaluate((node) => node.isConnected)).toBe(true);

  // Fail the poll that would carry the redirect: completing now must not
  // navigate until a later, successful poll actually observes it.
  await page.route('**/setup/status', (route) => route.fulfill({ status: 200, contentType: 'text/plain', body: 'not json' }));
  expect((await completeSetup(request)).status()).toBe(200);
  await expect(page.locator('#status-message')).toContainText('Retrying');
  await expect(page).toHaveURL(/\/setup\/session$/);
  expect(navigations).toEqual([]);
  expect(await terminal.evaluate((node) => node.isConnected)).toBe(true);

  // (c) recovery delivers exactly one redirect, with no new socket or Stop.
  await page.unroute('**/setup/status');
  await expect(page).toHaveURL(/\/newsletter\/$/);
  expect(navigations).toEqual(['/newsletter/']);
  expect(await recordedContinuity(page)).toEqual({ connected: true, same: true });
  expect(sockets).toHaveLength(1);
  expect(stops).toEqual([]);

  await assertNoConsoleErrors(page);
});

test('connected setup terminal forwards keyboard input as the owning browser', async ({ page, request }) => {
  await launchConnectedTerminal(page, request);

  await page.locator('#setup-terminal').click();
  await page.keyboard.type('echo hi');

  await expect
    .poll(async () => {
      const payload = await (await request.get('/__ui/setup/session/writes')).json();
      return payload.writes.join('');
    })
    .toContain('echo hi');

  await assertNoConsoleErrors(page);
});

test('setup terminal uses preferred font metrics when the terminal font is ready during startup', async ({ page, request }) => {
  await launchConnectedTerminal(page, request);
  await expect(page.locator('#terminal-connection')).toHaveText('Connected');
  await emitTerminalMetricMarkers(page, request);
  const [rows, cols] = await assertTerminalMetrics(page, request, '"JetBrains Mono"');
  expect(rows).toBeGreaterThanOrEqual(2);
  expect(cols).toBeGreaterThanOrEqual(20);
  await assertTerminalInputAndState(page, request, 'font-ready');
  await assertNoConsoleErrors(page);
});

test('late terminal font loading restores monospace metrics without resetting the session', async ({ page, request }) => {
  let releaseFont!: () => void;
  const heldFont = new Promise<void>((resolve) => {
    releaseFont = resolve;
  });
  const socketUrls: string[] = [];
  page.on('websocket', (socket) => {
    if (socket.url().includes('/setup/session/ws')) {
      socketUrls.push(socket.url());
    }
  });
  await page.route('https://fonts.gstatic.com/test/jetbrains-mono/normal.woff2', async (route) => {
    await heldFont;
    await route.fallback();
  });

  try {
    await launchConnectedTerminal(page, request);
    await expect(page.locator('#terminal-connection')).toHaveText('Connected');
    await emitTerminalMetricMarkers(page, request);
    await expect.poll(() => socketUrls.length).toBe(1);
    const initial = await (await request.get('/__ui/setup/session/writes')).json();
    const [initialRows, initialCols] = await assertTerminalMetrics(page, request, 'monospace');
    expect(initialRows).toBeGreaterThanOrEqual(2);
    expect(initialCols).toBeGreaterThanOrEqual(20);
    const terminalProbe = await page.evaluate(() => {
      const terminal = document.querySelector<HTMLElement>('#setup-terminal .xterm');
      const rows = document.querySelector<HTMLElement>('#setup-terminal .xterm-rows');
      if (!terminal || !rows) return false;
      const probe = 'late-font-terminal-probe';
      terminal.dataset.testProbe = probe;
      rows.dataset.testProbe = probe;
      return probe;
    });
    expect(terminalProbe).toBe('late-font-terminal-probe');

    releaseFont();
    await page.evaluate(() => document.fonts.load('400 13px "JetBrains Mono"'));
    const [rows, cols] = await assertTerminalMetrics(page, request, '"JetBrains Mono"');
    await expect(page.locator('#setup-terminal').getByText(WIDE_MARKER, { exact: true })).toBeVisible();
    await expect(page.locator('#setup-terminal').getByText(NARROW_MARKER, { exact: true })).toBeVisible();
    await expect.poll(() => socketUrls.length).toBe(1);
    await expect.poll(() => page.evaluate(() => ({
      terminal: document.querySelector<HTMLElement>('#setup-terminal .xterm')?.dataset.testProbe,
      rows: document.querySelector<HTMLElement>('#setup-terminal .xterm-rows')?.dataset.testProbe,
    }))).toEqual({
      terminal: 'late-font-terminal-probe',
      rows: 'late-font-terminal-probe',
    });
    await assertTerminalInputAndState(page, request, 'font-check');

    const final = await (await request.get('/__ui/setup/session/writes')).json();
    expect(final.sizes.length).toBeGreaterThanOrEqual(initial.sizes.length);
    expect(rows).toBeGreaterThanOrEqual(2);
    expect(cols).toBeGreaterThanOrEqual(20);
    await assertNoConsoleErrors(page);
  } finally {
    releaseFont();
  }
});

test('setup terminal falls back to monospace metrics when the terminal font response is empty', async ({ page, request }) => {
  const fontWarnings: string[] = [];
  page.on('console', (message) => {
    if (message.type() === 'warning') {
      fontWarnings.push(message.text());
    }
  });
  await page.route('https://fonts.gstatic.com/test/jetbrains-mono/normal.woff2', async (route) => {
    await route.fulfill({ status: 200, contentType: 'font/woff2', body: '' });
  });

  await launchConnectedTerminal(page, request);
  await expect(page.locator('#terminal-connection')).toHaveText('Connected');
  await emitTerminalMetricMarkers(page, request);
  const [rows, cols] = await assertTerminalMetrics(page, request, 'monospace');
  expect(rows).toBeGreaterThanOrEqual(2);
  expect(cols).toBeGreaterThanOrEqual(20);
  expect(fontWarnings.length === 0 || fontWarnings.every((message) => /font|decode|download|parsing|ots/i.test(message))).toBe(true);
  await assertTerminalInputAndState(page, request, 'font-empty');
  await assertNoConsoleErrors(page);
});

test('setup terminal falls back to monospace metrics when FontFaceSet.load is unavailable', async ({ page, request }) => {
  await page.addInitScript(() => {
    const prototype = Object.getPrototypeOf(document.fonts) as FontFaceSet & { load?: unknown };
    Object.defineProperty(prototype, 'load', {
      configurable: true,
      value: undefined,
    });
  });

  await launchConnectedTerminal(page, request);
  await expect(page.locator('#terminal-connection')).toHaveText('Connected');
  await emitTerminalMetricMarkers(page, request);
  const [rows, cols] = await assertTerminalMetrics(page, request, 'monospace');
  expect(rows).toBeGreaterThanOrEqual(2);
  expect(cols).toBeGreaterThanOrEqual(20);
  await assertTerminalInputAndState(page, request, 'font-missing-load');
  await assertNoConsoleErrors(page);
});

test('setup terminal falls back to monospace metrics when FontFaceSet.load returns empty results', async ({ page, request }) => {
  await page.addInitScript(() => {
    const prototype = Object.getPrototypeOf(document.fonts) as FontFaceSet & { load?: unknown };
    Object.defineProperty(prototype, 'load', {
      configurable: true,
      value: async () => [],
    });
  });

  await launchConnectedTerminal(page, request);
  await expect(page.locator('#terminal-connection')).toHaveText('Connected');
  await emitTerminalMetricMarkers(page, request);
  const [rows, cols] = await assertTerminalMetrics(page, request, 'monospace');
  expect(rows).toBeGreaterThanOrEqual(2);
  expect(cols).toBeGreaterThanOrEqual(20);
  await assertTerminalInputAndState(page, request, 'font-empty-result');
  await assertNoConsoleErrors(page);
});

for (const truncated of [false, true]) {
  test(`failed setup terminal retains diagnostics and stops reconnecting with truncated=${truncated}`, async ({ page, request }) => {
    await page.clock.install();
    let connections = 0;
    const diagnostic = 'Setup could not be started; cleanup could not be confirmed.';
    await page.routeWebSocket('**/setup/session/ws', (socket) => {
      connections += 1;
      socket.send(JSON.stringify({ state: 'failed', message: diagnostic, truncated }));
      socket.close();
    });

    await launchConnectedTerminal(page, request);
    await expect(page.locator('#terminal-connection')).toContainText(diagnostic);
    await expect(page.getByRole('button', { name: /^Relaunch/ })).toBeHidden();
    await page.clock.runFor(60_000);

    expect(connections).toBe(1);
    await expect(page.locator('#terminal-connection')).toContainText(diagnostic);
    await expect(page.getByRole('button', { name: /^Relaunch/ })).toBeHidden();
    await assertNoConsoleErrors(page);
  });
}

test('setup terminal bounds reconnects when accepted sockets never confirm running', async ({ page, request }) => {
  await page.clock.install();
  let connections = 0;
  await page.routeWebSocket('**/setup/session/ws', (socket) => {
    connections += 1;
    socket.send(JSON.stringify({ state: 'starting', truncated: false }));
    socket.close();
  });

  await launchConnectedTerminal(page, request);
  await expect(page.locator('#terminal-connection')).toHaveText('Reconnecting');
  for (let retry = 0; retry < 6 && connections < 7; retry += 1) {
    const previous = connections;
    await page.clock.runFor(8000);
    await expect.poll(() => connections).toBeGreaterThan(previous);
    await expect(page.locator('#terminal-connection')).toHaveText(/^Reconnecting$|^Setup session disconnected\./);
  }
  await page.clock.runFor(60_000);

  expect(connections).toBe(7);
  await expect(page.locator('#terminal-connection')).toContainText('Setup session disconnected.');
  await expect(page.getByRole('button', { name: /^Relaunch/ })).toBeHidden();
  await assertNoConsoleErrors(page);
});

test('connected setup terminal reports its initial size without a manual resize, and again after reconnect', async ({
  page,
  request,
}) => {
  await launchConnectedTerminal(page, request);

  // No window/viewport resize happens anywhere in this test: a size record
  // must show up purely from attaching, not from a later manual resize.
  await expect
    .poll(async () => (await (await request.get('/__ui/setup/session/writes')).json()).sizes.length)
    .toBeGreaterThanOrEqual(1);

  const afterAttach = await (await request.get('/__ui/setup/session/writes')).json();
  const [rows, cols] = afterAttach.sizes[afterAttach.sizes.length - 1];
  expect(rows).toBeGreaterThanOrEqual(2);
  expect(rows).toBeLessThanOrEqual(200);
  expect(cols).toBeGreaterThanOrEqual(20);
  expect(cols).toBeLessThanOrEqual(400);

  await page.reload();
  await expect(page.locator('#setup-terminal .xterm-screen')).toBeVisible();

  await expect
    .poll(async () => (await (await request.get('/__ui/setup/session/writes')).json()).sizes.length)
    .toBeGreaterThan(afterAttach.sizes.length);

  await assertNoConsoleErrors(page);
});

test('connected setup terminal does not navigate or prompt for an OSC 8 hyperlink', async ({ page, request }) => {
  // Installed before any navigation so it captures xterm's own (unfixed)
  // default link handler, which calls the bare global `confirm`/`window.open`.
  await page.addInitScript(() => {
    (window as unknown as { __oscLinkActivations: number }).__oscLinkActivations = 0;
    window.confirm = () => {
      (window as unknown as { __oscLinkActivations: number }).__oscLinkActivations += 1;
      return true;
    };
    window.open = () => {
      (window as unknown as { __oscLinkActivations: number }).__oscLinkActivations += 1;
      return null;
    };
  });

  await launchConnectedTerminal(page, request);

  const linkText = 'malicious-setup-link';
  const emit = await request.post('/__ui/setup/session/emit', {
    data: { text: `\u001b]8;;http://example.invalid/${linkText}\u0007${linkText}\u001b]8;;\u0007\r\n` },
  });
  expect(emit.status()).toBe(204);

  const link = page.locator('#setup-terminal').getByText(linkText, { exact: true });
  await expect(link).toBeVisible();

  // xterm's rows have pointer-events disabled and the `.xterm-screen`
  // element is the real mouse target (it derives the buffer cell from
  // clientX/Y itself), so a real hover/click must land there rather than on
  // the (non-interactive) text span underneath.
  const screen = page.locator('#setup-terminal .xterm-screen');
  const linkBox = await link.boundingBox();
  const screenBox = await screen.boundingBox();
  if (!linkBox || !screenBox) {
    throw new Error('Could not resolve OSC 8 link or terminal screen bounding box');
  }
  const position = { x: linkBox.x + linkBox.width / 2 - screenBox.x, y: linkBox.y + linkBox.height / 2 - screenBox.y };

  // Proof the OSC 8 link actually reached xterm's link layer (not just text
  // on screen): hovering a recognised link sets xterm's own pointer-cursor
  // class on the screen element before any click occurs.
  await screen.hover({ position });
  await expect(screen).toHaveClass(/xterm-cursor-pointer/);

  await screen.click({ position });
  await page.waitForTimeout(250);

  const activations = await page.evaluate(
    () => (window as unknown as { __oscLinkActivations: number }).__oscLinkActivations,
  );
  expect(activations).toBe(0);

  await assertNoConsoleErrors(page);
});

test('connected setup terminal warns instead of pretending truncated scrollback is present', async ({
  page,
  request,
}) => {
  await launchConnectedTerminal(page, request);

  const emit = await request.post('/__ui/setup/session/emit', { data: { length: 16384 } });
  expect(emit.status()).toBe(204);

  // /setup/session/state is owner-cookie-gated; the standalone `request`
  // fixture does not share the browser context's cookies, so read it through
  // the page itself.
  await expect
    .poll(async () =>
      page.evaluate(() => fetch('/setup/session/state', { cache: 'no-store' }).then((r) => r.json().then((p) => p.truncated))),
    )
    .toBe(true);

  await page.reload();
  await expect(page.locator('#terminal-connection')).toContainText('Earlier terminal output is unavailable.');

  await assertNoConsoleErrors(page);
});

test('connected setup terminal opens the dashboard after setup reports completion', async ({ page, request }) => {
  await launchConnectedTerminal(page, request);

  await markSetupReady(request);
  expect((await completeSetup(request)).status()).toBe(200);

  await expect(page).toHaveURL(/\/newsletter\/$/);
  await expect(page.getByRole('link', { name: 'View terminal' })).toBeVisible();
  const state = await page.evaluate(() => fetch('/setup/session/state', { cache: 'no-store' }).then((response) => response.json()));
  expect(state.state).toBe('running');

  await assertNoConsoleErrors(page);
});

test('setup waits for its workflow definition before opening the dashboard', async ({ page, request }) => {
  await launchConnectedTerminal(page, request);

  const missing = await request.post('/__ui/setup/ready?definition=missing');
  expect(missing.status()).toBe(204);
  await expect(page.locator('#status-message')).toContainText('cannot use blueprint');
  await expect(page).toHaveURL(/\/setup\/session$/);
  const incomplete = await (await request.get('/setup/status')).json();
  expect(incomplete.state).toBe('incomplete');
  expect(incomplete.redirect).toBeUndefined();

  const fixed = await request.post('/__ui/setup/ready?definition=valid');
  expect(fixed.status()).toBe(204);
  expect((await completeSetup(request)).status()).toBe(200);
  await expect(page).toHaveURL(/\/newsletter\/$/);
  const state = await page.evaluate(() =>
    fetch('/setup/session/state', { cache: 'no-store' }).then((response) => response.json())
  );
  expect(state.state).toBe('running');
  await assertNoConsoleErrors(page);
});

test('connected setup terminal stop ends the session', async ({ page, request }) => {
  await launchConnectedTerminal(page, request);

  await page.getByRole('button', { name: 'Stop', exact: true }).click();

  // A confirmed Stop returns the browser to the plain, editable setup form
  // so the user can choose a different data root or integration.
  await expect(page).toHaveURL(/\/setup$/);
  await expect(page.getByLabel('Flowgency data root', { exact: true })).toBeVisible();

  await expect
    .poll(async () =>
      page.evaluate(() => fetch('/setup/session/state', { cache: 'no-store' }).then((r) => r.json().then((p) => p.state))),
    )
    .toBe('stopped');

  // The stopped session's final output remains reachable for inspection.
  await page.goto('/setup/session');
  await expect(page.locator('#terminal-connection')).toContainText('The setup session was stopped.');

  await assertNoConsoleErrors(page);
});

test('native Windows setup embeds the terminal', async ({ page, request }, testInfo) => {
  test.skip(process.platform !== 'win32', 'exercises the native Windows connected-setup backend only');

  await launchConnectedTerminal(page, request);
  await expect(page.locator('#setup-terminal .xterm-screen')).toBeVisible();

  await page.locator('#setup-terminal').click();
  await page.keyboard.type('windows input');
  await expect
    .poll(async () => {
      const writes = await (await request.get('/__ui/setup/session/writes')).json();
      return writes.writes.join('');
    })
    .toContain('windows input');

  await page.reload();
  await expect(page.locator('#setup-terminal .xterm-screen')).toBeVisible();
  await captureEvidence(page, `native-windows-terminal-${testInfo.project.name}.png`);

  await page.getByRole('button', { name: 'Stop', exact: true }).click();
  await expect(page).toHaveURL(/\/setup$/);

  await assertNoConsoleErrors(page);
});

test('connected setup terminal offers Relaunch after Copilot exits, hidden while running', async ({ page, request }) => {
  await launchConnectedTerminal(page, request);

  const relaunch = page.getByRole('button', { name: /^Relaunch/ });
  await expect(relaunch).toBeHidden();

  const exitMarker = 'SETUP-EXIT-MARKER';
  const emitText = await request.post('/__ui/setup/session/emit', { data: { text: `${exitMarker}\r\n` } });
  expect(emitText.status()).toBe(204);
  const emitEof = await request.post('/__ui/setup/session/emit', { data: { eof: true } });
  expect(emitEof.status()).toBe(204);

  // Final output stays on screen; the exit is not proof configuration
  // succeeded, so the terminal must never be cleared or hidden on its own.
  await expect(page.locator('#setup-terminal').getByText(exitMarker, { exact: true })).toBeVisible();
  await expect(page.locator('#terminal-connection')).toContainText('The setup session exited.');
  await expect(relaunch).toBeVisible();
  const relaunchForm = page.locator('form', { has: relaunch });
  await expect(relaunchForm.locator('input[name="setup_csrf"]')).toHaveAttribute('value', /.+/);

  const rootBeforeRelaunch = await page
    .locator('.rounded-xl.border.border-gray-200.bg-gray-50', { hasText: 'Flowgency data root' })
    .locator('.font-mono')
    .innerText();

  // Reload the exited view: the Relaunch action is not a server-rendered
  // flag, so it must reappear only once the fresh page's own WebSocket
  // delivers its first state frame, not merely because it once appeared.
  await page.reload();
  await expect(page.locator('#setup-terminal').getByText(exitMarker, { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: /^Relaunch/ })).toBeVisible();

  await page.getByRole('button', { name: /^Relaunch/ }).click();
  await expect(page).toHaveURL(/\/setup\/session$/);
  await expect(page.locator('#setup-terminal .xterm-screen')).toBeVisible();
  await expect(page.locator('#terminal-connection')).toContainText('Connected');
  await expect(page.getByRole('button', { name: /^Relaunch/ })).toBeHidden();

  const rootAfterRelaunch = await page
    .locator('.rounded-xl.border.border-gray-200.bg-gray-50', { hasText: 'Flowgency data root' })
    .locator('.font-mono')
    .innerText();
  expect(rootAfterRelaunch).toBe(rootBeforeRelaunch);

  // A fresh fake process backs the new session: its own size record only
  // grows again once this new WebSocket attaches, proving it is a distinct,
  // healthy running session rather than the same exited one re-rendered.
  await expect
    .poll(async () => (await (await request.get('/__ui/setup/session/writes')).json()).sizes.length)
    .toBeGreaterThanOrEqual(1);

  await assertNoConsoleErrors(page);
});

test('dashboard surfaces the running setup session only for its owning browser', async ({ page, request, browser }, testInfo) => {
  await launchConnectedTerminal(page, request);

  await markSetupReady(request);
  expect((await completeSetup(request)).status()).toBe(200);

  // Setup reported completion but this browser's connected PTY is still owned
  // by the server; returning to the plain /setup URL redirects through the
  // dashboard's default team rather than back to the (now finished) form.
  await page.goto('/setup');
  await expect(page).toHaveURL(/\/newsletter\/$/);

  const sessionLink = page.getByRole('link', { name: 'View terminal' });
  await expect(sessionLink).toBeVisible();
  await expect(page.getByRole('button', { name: 'Stop setup session' })).toBeVisible();
  await captureEvidence(page, `dashboard-setup-session-${testInfo.project.name}.png`);

  await sessionLink.click();
  await expect(page).toHaveURL(/\/setup\/session\?view=inspection$/);
  await expect(page.locator('#setup-terminal .xterm-screen')).toBeVisible();

  // The inspection view keeps polling but stays on the terminal after completion.
  await waitForStatusPolls(page, 2);
  await expect(page).toHaveURL(/\/setup\/session\?view=inspection$/);

  await page.goto('/newsletter/');
  await expect(sessionLink).toBeVisible();

  const otherContext = await browser.newContext();
  const otherPage = await otherContext.newPage();
  await otherPage.goto('/newsletter/');
  await expect(otherPage.getByRole('link', { name: 'View terminal' })).toHaveCount(0);
  const deniedResponse = await otherPage.goto('/setup/session');
  expect(deniedResponse?.status()).toBe(403);
  await otherContext.close();

  await page.getByRole('button', { name: 'Stop setup session' }).click();
  await expect(page).toHaveURL(/\/newsletter\/$/);
  await expect(page.locator('[data-setup-session]')).toHaveCount(0);

  await assertNoConsoleErrors(page);
});


test('ready configuration does not finish an unanswered setup', async ({ page, request }) => {
  await launchConnectedTerminal(page, request);
  const terminal = await trackTerminalContinuity(page);
  await markSetupReady(request);

  await expect.poll(() => setupStatus(page)).toMatchObject({ state: 'ready', completion: { phase: 'pending' } });
  expect(await setupStatus(page)).not.toHaveProperty('redirect');
  await waitForStatusPolls(page, 2);
  await expect(page).toHaveURL(/\/setup\/session$/);
  expect(await terminal.evaluate((node) => node.isConnected)).toBe(true);

  await page.reload();
  await expect(page).toHaveURL(/\/setup\/session$/);
  await expect(page.locator('#setup-terminal .xterm-screen')).toBeVisible();
  await waitForStatusPolls(page, 1);
  await expect(page).toHaveURL(/\/setup\/session$/);
  expect(await setupStatus(page)).toMatchObject({ state: 'ready', completion: { phase: 'pending' } });
  await assertNoConsoleErrors(page);
});

const completingOutcomes: CompletionBody[] = [
  { scheduler_result: 'confirmed' },
  { scheduler_result: 'declined' },
  { scheduler_result: 'manual-only' },
  { scheduler_result: 'failed', limitations_acknowledged: true },
  { scheduler_result: 'unknown', limitations_acknowledged: true },
];

for (const outcome of completingOutcomes) {
  test(`acknowledged completion (${outcome.scheduler_result}) redirects once and keeps the terminal`, async ({ page, request }) => {
    const sockets = trackSetupSockets(page);
    const stops = trackStopRequests(page);
    await launchConnectedTerminal(page, request);
    const terminal = await trackTerminalContinuity(page);
    await markSetupReady(request);
    await waitForStatusPolls(page, 1);
    await expect(page).toHaveURL(/\/setup\/session$/);
    const configBefore = await runtimeConfigBytes(request);
    expect(await terminal.evaluate((node) => node.isConnected)).toBe(true);
    const navigations = trackMainFrameNavigations(page);

    const response = await completeSetup(request, outcome);
    expect(response.status()).toBe(200);
    expect((await response.json()).completion).toMatchObject({ phase: 'complete', redirect_allowed: true });

    await expect(page).toHaveURL(/\/newsletter\/$/);
    await page.waitForTimeout(2000);
    expect(navigations.filter((pathname) => pathname !== '/newsletter/')).toEqual([]);
    expect(navigations).toHaveLength(1);

    expect(await recordedContinuity(page)).toEqual({ connected: true, same: true });
    expect(sockets).toHaveLength(1);
    expect(stops).toEqual([]);
    expect((await runtimeConfigBytes(request)).equals(configBefore)).toBe(true);

    const writes = await (await request.get('/__ui/setup/session/writes')).json();
    expect(writes.writes.join('')).not.toContain('/exit');
    await expect(page.getByRole('link', { name: 'View terminal' })).toBeVisible();
    const state = await page.evaluate(() => fetch('/setup/session/state', { cache: 'no-store' }).then((r) => r.json()));
    expect(state).toMatchObject({ state: 'running', completion: { phase: 'complete' } });
    await assertNoConsoleErrors(page);
  });
}

const refusedOutcomes: CompletionBody[] = [
  { scheduler_result: 'failed' },
  { scheduler_result: 'failed', limitations_acknowledged: false },
  { scheduler_result: 'unknown' },
];

for (const outcome of refusedOutcomes) {
  test(`unacknowledged scheduler ${outcome.scheduler_result} (acknowledged=${String(outcome.limitations_acknowledged)}) is refused and never redirects`, async ({ page, request }) => {
    await launchConnectedTerminal(page, request);
    await markSetupReady(request);

    const refused = await completeSetup(request, outcome);
    expect(refused.status()).toBe(409);
    expect(await refused.json()).toMatchObject({ ok: false, code: 'invalid-completion' });

    await waitForStatusPolls(page, 2);
    await expect(page).toHaveURL(/\/setup\/session$/);
    expect(await setupStatus(page)).toMatchObject({ state: 'ready', completion: { phase: 'pending' } });
    expect(await setupStatus(page)).not.toHaveProperty('redirect');

    expect((await completeSetup(request, { ...outcome, limitations_acknowledged: true })).status()).toBe(200);
    await expect(page).toHaveURL(/\/newsletter\/$/);
    await assertNoConsoleErrors(page);
  });
}

test('a stale configuration revision is rejected and does not redirect', async ({ page, request }) => {
  await launchConnectedTerminal(page, request);
  await markSetupReady(request);

  const stale = await completeSetup(request, { scheduler_result: 'confirmed', revision: 'stale' });
  expect(stale.status()).toBe(409);
  expect(await stale.json()).toMatchObject({ ok: false, code: 'stale' });

  await waitForStatusPolls(page, 2);
  await expect(page).toHaveURL(/\/setup\/session$/);
  expect(await setupStatus(page)).toMatchObject({ completion: { phase: 'pending' } });
  expect(await setupStatus(page)).not.toHaveProperty('redirect');
  await assertNoConsoleErrors(page);
});

test('Stop never counts as completion', async ({ page, request }) => {
  await launchConnectedTerminal(page, request);
  await markSetupReady(request);

  await page.getByRole('button', { name: 'Stop', exact: true }).click();
  await expect(page).not.toHaveURL(/\/setup\/session/);

  const status = await setupStatus(page);
  expect(status).toMatchObject({ completion: { phase: 'cancelled' } });
  expect(status).not.toHaveProperty('redirect');
  const refused = await completeSetup(request);
  expect(refused.status()).toBe(409);
  expect(await refused.json()).toMatchObject({ ok: false, code: 'invalid-credentials' });

  await page.goto('/setup');
  await expect(page).toHaveURL(/\/setup$/);
  await expect(page.getByLabel('Flowgency data root', { exact: true })).toBeVisible();
  await assertNoConsoleErrors(page);
});

test('a crash without acknowledgement keeps the terminal and never redirects', async ({ page, request }) => {
  await launchConnectedTerminal(page, request);
  await markSetupReady(request);

  await emitTerminalText(request, 'CRASH-MARKER\r\n');
  const eof = await request.post('/__ui/setup/session/emit', { data: { eof: true } });
  expect(eof.status()).toBe(204);

  await expect(page.locator('#terminal-connection')).toContainText('The setup session exited.');
  await expect(page.locator('#setup-terminal').getByText('CRASH-MARKER', { exact: true })).toBeVisible();
  await waitForStatusPolls(page, 2);
  await expect(page).toHaveURL(/\/setup\/session$/);
  expect(await setupStatus(page)).toMatchObject({ state: 'ready', completion: { phase: 'attention' } });
  expect(await setupStatus(page)).not.toHaveProperty('redirect');
  const refused = await completeSetup(request);
  expect(refused.status()).toBe(409);
  expect(await refused.json()).toMatchObject({ ok: false, code: 'invalid-credentials' });
  await waitForStatusPolls(page, 1);
  await expect(page).toHaveURL(/\/setup\/session$/);
  await assertNoConsoleErrors(page);
});

test('a status reply from an older launch is ignored', async ({ page, request }) => {
  await launchConnectedTerminal(page, request);
  const terminal = await trackTerminalContinuity(page);
  await markSetupReady(request);

  await page.route('**/setup/status', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({
      state: 'ready',
      redirect: '/',
      completion: { launch_id: '0'.repeat(32), phase: 'complete', message: 'Setup is complete.' },
    }),
  }));
  await expect(page.locator('#status-message')).toContainText('A different setup launch is active');
  await waitForStatusPolls(page, 2);
  await expect(page).toHaveURL(/\/setup\/session$/);
  expect(await terminal.evaluate((node) => node.isConnected)).toBe(true);
  await page.unroute('**/setup/status');

  expect((await completeSetup(request)).status()).toBe(200);
  await expect(page).toHaveURL(/\/newsletter\/$/);
  await assertNoConsoleErrors(page);
});

test('the inspection view stays put after completion while the plain session view redirects', async ({ page, request }) => {
  await launchConnectedTerminal(page, request);
  await page.goto('/setup/session?view=inspection');
  await expect(page.locator('#setup-terminal .xterm-screen')).toBeVisible();
  await expect(page.locator('#status-message')).toHaveAttribute('data-setup-view', 'inspection');
  await markSetupReady(request);
  expect((await completeSetup(request)).status()).toBe(200);

  await waitForStatusPolls(page, 2);
  await expect(page).toHaveURL(/\/setup\/session\?view=inspection$/);
  expect(await setupStatus(page)).toMatchObject({ redirect: '/', completion: { phase: 'complete' } });

  await page.reload();
  await expect(page.locator('#setup-terminal .xterm-screen')).toBeVisible();
  await waitForStatusPolls(page, 2);
  await expect(page).toHaveURL(/\/setup\/session\?view=inspection$/);

  await page.goto('/setup/session');
  await expect(page).toHaveURL(/\/newsletter\/$/);
  await assertNoConsoleErrors(page);
});

const SETUP_STATUS_HANDLE = 'setup-status';
const SETUP_STATUS_INTERVAL_MS = 1500;

type LiveWindow = { FlowgencyLive?: { handles: Map<string, { status: string | null }> } };

async function setVisibility(page: Page, state: 'hidden' | 'visible'): Promise<void> {
  await page.evaluate((value) => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => value });
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => value === 'hidden' });
    document.dispatchEvent(new Event('visibilitychange'));
  }, state);
}

function trackStatusRequests(page: Page): string[] {
  const requests: string[] = [];
  page.on('request', (request) => {
    if (new URL(request.url()).pathname === '/setup/status') requests.push(request.url());
  });
  return requests;
}

async function setupWrites(request: APIRequestContext): Promise<string[]> {
  return (await (await request.get('/__ui/setup/session/writes')).json()).writes as string[];
}

test('setup status reads run on one shared lifecycle loop at the existing cadence', async ({ page, request }) => {
  const statusRequests = trackStatusRequests(page);
  await launchConnectedTerminal(page, request);

  await expect.poll(() => page.evaluate(
    (key) => (window as unknown as LiveWindow).FlowgencyLive?.handles.has(key) ?? false,
    SETUP_STATUS_HANDLE,
  )).toBe(true);
  expect(await page.evaluate(() => (window as unknown as LiveWindow).FlowgencyLive?.handles.size)).toBe(1);

  await waitForStatusPolls(page, 1);
  const before = statusRequests.length;
  await page.waitForTimeout(SETUP_STATUS_INTERVAL_MS * 3 + 300);
  const reads = statusRequests.length - before;
  // A second loop would double the cadence to six or more reads in this window.
  expect(reads).toBeGreaterThanOrEqual(2);
  expect(reads).toBeLessThanOrEqual(4);
  await assertNoConsoleErrors(page);
});

test('hiding and showing the setup page pauses and catches up status reads without touching the terminal stream', async ({ page, request }) => {
  const sockets = trackSetupSockets(page);
  const stops = trackStopRequests(page);
  const statusRequests = trackStatusRequests(page);
  await launchConnectedTerminal(page, request);
  const terminal = await trackTerminalContinuity(page);
  await waitForStatusPolls(page, 1);
  expect(sockets).toHaveLength(1);
  const writesBefore = await setupWrites(request);
  const navigations = trackMainFrameNavigations(page);

  await setVisibility(page, 'hidden');
  await page.waitForTimeout(300);
  const pausedAt = statusRequests.length;
  await page.waitForTimeout(SETUP_STATUS_INTERVAL_MS * 2 + 300);
  expect(statusRequests.length).toBe(pausedAt);

  // The configuration becomes ready while hidden: no read may run until the page is visible.
  await markSetupReady(request);
  await page.waitForTimeout(SETUP_STATUS_INTERVAL_MS + 300);
  expect(statusRequests.length).toBe(pausedAt);

  await setVisibility(page, 'visible');
  await expect.poll(() => statusRequests.length, { timeout: SETUP_STATUS_INTERVAL_MS - 500 }).toBeGreaterThan(pausedAt);
  await expect(page.locator('#status-message')).toContainText('Waiting for setup to report completion.');

  await setVisibility(page, 'hidden');
  await setVisibility(page, 'visible');
  await page.waitForTimeout(300);

  expect(sockets).toHaveLength(1);
  expect(stops).toEqual([]);
  expect(navigations).toEqual([]);
  expect(await terminal.evaluate((node) => node.isConnected)).toBe(true);
  expect(await page.locator('#setup-terminal .xterm').elementHandle().then((node) => node?.evaluate((n) => n.isConnected))).toBe(true);
  await expect(page).toHaveURL(/\/setup\/session$/);
  expect(await setupWrites(request)).toEqual(writesBefore);
  await assertNoConsoleErrors(page);
});

test('the terminal stream survives the ready transition and the completion redirect with one status loop', async ({ page, request }) => {
  const sockets = trackSetupSockets(page);
  const stops = trackStopRequests(page);
  await launchConnectedTerminal(page, request);
  const terminal = await trackTerminalContinuity(page);
  await markSetupReady(request);
  await waitForStatusPolls(page, 1);
  await expect(page).toHaveURL(/\/setup\/session$/);
  expect(await terminal.evaluate((node) => node.isConnected)).toBe(true);
  expect(sockets).toHaveLength(1);

  expect((await completeSetup(request)).status()).toBe(200);
  await expect(page).toHaveURL(/\/newsletter\/$/);
  expect(await recordedContinuity(page)).toEqual({ connected: true, same: true });
  expect(sockets).toHaveLength(1);
  expect(stops).toEqual([]);
  await assertNoConsoleErrors(page);
});
