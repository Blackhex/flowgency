import { expect, test, type APIRequestContext, type Page } from '@playwright/test';
import { mkdir } from 'node:fs/promises';
import path from 'node:path';

import { assertNoConsoleErrors, assertNoTailwindCdnRequests, installBasePageSetup } from './layout';

const evidenceRoot = path.resolve('.superpowers', 'sdd', '2026-09-23-connected-copilot-setup-terminal', 'evidence');

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
  await page.goto('/setup');
  await page.getByLabel('Flowgency data root', { exact: true }).fill(dataRoot);
  await page.getByRole('button', { name: 'Continue in GitHub Copilot' }).click();
  await expect(page).toHaveURL(/\/setup\/session$/);
  await expect(page.locator('#setup-terminal .xterm-screen')).toBeVisible();
}

async function captureEvidence(page: Page, name: string): Promise<void> {
  await mkdir(evidenceRoot, { recursive: true });
  await page.screenshot({ path: path.join(evidenceRoot, name), fullPage: true });
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

test('connected setup terminal stays on the session view when setup becomes ready', async ({ page, request }) => {
  await launchConnectedTerminal(page, request);

  const ready = await request.post('/__ui/setup/ready');
  expect(ready.status()).toBe(204);

  await expect
    .poll(async () => (await (await request.get('/setup/status')).json()).state)
    .toBe('ready');

  await page.waitForTimeout(2000);
  await expect(page).toHaveURL(/\/setup\/session$/);
  await expect(page.locator('#setup-terminal .xterm-screen')).toBeVisible();

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
