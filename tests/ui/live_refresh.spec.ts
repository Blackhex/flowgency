import { createHash } from 'node:crypto';

import { expect, test, type Page, type Route } from '@playwright/test';

import {
  assertNoConsoleErrors,
  installBasePageSetup,
  type LiveApplyResult,
  type LiveBindingShape,
  type LiveRefreshOutcome,
  type LiveSnapshotShape,
} from './layout';

declare global {
  interface Window {
    __mount: (interval?: number, timeout?: number) => void;
    __navigate: (binding: LiveBindingShape) => void;
    __applied: LiveApplyResult[];
    __statuses: string[];
    __generation: number;
    __bodyGate: Promise<void> | null;
    __releaseBody: (() => void) | null;
    __finish: (() => void) | null;
    __pending: Promise<LiveRefreshOutcome> | null;
    __blurs: number;
    __pwned?: boolean;
  }
}

const DOCUMENT_PATH = '/__live_test__/document';
const SNAPSHOT_PATH = '/__live_test__/snapshot';
const BINDING = { page: 'test', team: null, entity: null, tab: null, query: {} };

interface Model {
  label: string;
  other: string;
  tail: string;
  detail: string;
  dialogLabel: string;
  ownedText: string;
  formClass: string;
  detailsOpen: boolean;
  wrapHeld: boolean;
  logLines: number;
  keys: string[];
}

const ALL_KEYS = ['held', 'other', 'tail', 'disclosure', 'dialog', 'log', 'owned'];

function region(overrides: Partial<Model> = {}): string {
  const m: Model = {
    label: 'Held label',
    other: 'Initial',
    tail: 'Tail',
    detail: 'Detail text',
    dialogLabel: 'Dialog label',
    ownedText: 'Owned text',
    formClass: 'original',
    detailsOpen: false,
    wrapHeld: false,
    logLines: 20,
    keys: ALL_KEYS,
    ...overrides,
  };
  const held = `<div data-live-key="held"><span data-testid="held-label">${m.label}</span> `
    + '<select id="choice" name="choice"><option value="a" selected>A</option><option value="b">B</option></select> '
    + '<input id="note" name="note" type="text" value=""></div>';
  const lines = Array.from({ length: m.logLines }, (_, index) => `<div>line ${index + 1}</div>`).join('');
  const parts: Record<string, string> = {
    held: m.wrapHeld ? `<div data-live-key="wrap">${held}</div>` : held,
    other: `<div data-live-key="other" data-testid="other">${m.other}</div>`,
    tail: `<div data-live-key="tail" data-testid="tail">${m.tail}</div>`,
    disclosure: `<details data-live-key="disclosure"${m.detailsOpen ? ' open' : ''}><summary>Disclosure</summary>`
      + `<p data-testid="disclosure-text">${m.detail}</p></details>`,
    dialog: `<div data-live-key="dialog-host"><span data-testid="dialog-label">${m.dialogLabel}</span>`
      + '<dialog id="dlg" data-testid="dlg"><p>Dialog body</p></dialog></div>',
    log: `<div data-live-key="log" data-testid="log" data-live-follow style="height:60px;overflow:auto">${lines}</div>`,
    owned: '<div data-live-key="owned-host">'
      + `<form data-testid="owned-form" class="${m.formClass}"><label>Name <input id="owned-name" name="name"></label></form>`
      + `<div data-live-owned data-testid="owned-sub">${m.ownedText}</div>`
      + `<div class="xterm" data-testid="xterm">${m.ownedText}</div></div>`,
  };
  return m.keys.map((key) => parts[key]).join('\n');
}

function snapshot(overrides: Partial<Model> = {}, extra: Partial<LiveSnapshotShape> = {}): LiveSnapshotShape {
  return {
    format: 1,
    binding: BINDING,
    structure: 'test:1',
    revisions: {},
    regions: [{ key: 'page', html: region(overrides) }],
    ...extra,
  };
}

function etagOf(body: unknown): string {
  return `"${createHash('sha256').update(JSON.stringify(body)).digest('hex')}"`;
}

interface Reply {
  status?: number;
  body?: unknown;
  etag?: string;
  gate?: Promise<void>;
}

function deferred(): { promise: Promise<void>; release: () => void } {
  let release!: () => void;
  const promise = new Promise<void>((resolve) => { release = resolve; });
  return { promise, release };
}

class LiveServer {
  current: LiveSnapshotShape = snapshot();
  script: Reply[] = [];
  requests: { ifNoneMatch: string | undefined }[] = [];
  aborted = 0;

  async handle(route: Route): Promise<void> {
    const ifNoneMatch = route.request().headers()['if-none-match'];
    this.requests.push({ ifNoneMatch });
    const scripted = this.script.shift();
    if (scripted?.gate) await scripted.gate;
    let status = scripted?.status ?? 200;
    let body: unknown = scripted?.body ?? this.current;
    let etag = scripted?.etag ?? etagOf(body);
    if (!scripted && ifNoneMatch === etag) status = 304;
    const payload = status === 304 ? '' : (typeof body === 'string' ? body : JSON.stringify(body));
    try {
      await route.fulfill({
        status,
        contentType: 'application/json',
        headers: { ETag: etag, 'Cache-Control': 'private, no-cache' },
        body: payload,
      });
    } catch {
      // The page aborted this request before the reply was delivered.
    }
  }
}

function documentHtml(): string {
  return `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Live refresh contract</title></head>
<body>
<main id="app"><div data-live-region="page" id="page-region">${region()}</div></main>
<script type="application/json" id="initial">${JSON.stringify({
    binding: BINDING,
    structure: 'test:1',
    snapshotUrl: `${SNAPSHOT_PATH}?__live=1`,
  })}</script>
<script src="/static/live-refresh.js"></script>
<script>
window.__applied = [];
window.__statuses = [];
window.__generation = 0;
window.__bodyGate = null;
window.__releaseBody = null;
window.__finish = null;
window.__pending = null;
window.__blurs = 0;
const originalText = Response.prototype.text;
Response.prototype.text = async function () {
  const body = await originalText.call(this);
  if (window.__bodyGate) await window.__bodyGate;
  return body;
};
window.__mount = (interval = 2000, timeout = 15000) => {
  const root = document.getElementById('app');
  const initial = JSON.parse(document.getElementById('initial').textContent);
  let current = initial.binding;
  window.__navigate = (binding) => { current = binding; };
  const view = new FlowgencyLive.LiveRegionView(root, initial, {
    onDrop: () => { FlowgencyLive.handles.get('test').invalidate(); },
  });
  FlowgencyLive.register({
    key: 'test',
    interval,
    timeout,
    binding: () => current,
    url: () => initial.snapshotUrl,
    headers: () => ({ Accept: 'application/json' }),
    capture: () => window.__generation,
    isCurrent: (captured) => captured === window.__generation,
    apply: (snapshot) => {
      const result = view.apply(snapshot);
      window.__applied.push(result);
      return result;
    },
    status: (value) => {
      window.__statuses.push(value);
      view.setStatus(value);
    },
    flushDeferred: () => view.flushDeferred(),
    invalidate: () => view.invalidate(current),
    dispose: () => view.dispose(),
  });
};
</script>
</body></html>`;
}

async function openLive(page: Page, options: { interval?: number; timeout?: number } = {}) {
  const server = new LiveServer();
  await page.route(`**${DOCUMENT_PATH}`, (route) => route.fulfill({
    status: 200,
    contentType: 'text/html; charset=utf-8',
    body: documentHtml(),
  }));
  await page.route(`**${SNAPSHOT_PATH}*`, (route) => server.handle(route));
  page.on('requestfailed', (request) => {
    if (request.url().includes(SNAPSHOT_PATH) && /ERR_ABORTED/.test(request.failure()?.errorText ?? '')) {
      server.aborted += 1;
    }
  });
  await page.clock.install({ time: 0 });
  await page.goto(DOCUMENT_PATH);
  await page.clock.pauseAt(600_000);
  await page.evaluate(([interval, timeout]) => window.__mount(interval, timeout), [options.interval ?? 2000, options.timeout ?? 15000]);
  return server;
}

const handleRefresh = (page: Page) => page.evaluate(() => window.FlowgencyLive.handles.get('test')!.refresh());
const handleFlush = (page: Page) => page.evaluate(() => window.FlowgencyLive.handles.get('test')!.flushDeferred());
const statuses = (page: Page) => page.evaluate(() => window.__statuses);
const applied = (page: Page) => page.evaluate(() => window.__applied.length);

async function setVisibility(page: Page, state: 'hidden' | 'visible') {
  await page.evaluate((value) => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => value });
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => value === 'hidden' });
    document.dispatchEvent(new Event('visibilitychange'));
  }, state);
}

const heldItem = (page: Page) => page.locator('[data-live-key="held"]');
const select = (page: Page) => page.locator('[data-live-key="held"] select');
const regionKeys = (page: Page) => page.locator('#page-region').evaluate(
  (element) => Array.from(element.children).map((child) => child.getAttribute('data-live-key')),
);

test.beforeEach(async ({ page }, testInfo) => {
  await installBasePageSetup(page, testInfo.project.name.endsWith('dark') ? 'dark' : 'light');
});

test('a focused select keeps its node and focus while other items update', async ({ page }) => {
  const server = await openLive(page);
  await select(page).focus();
  const original = await select(page).elementHandle();
  await page.evaluate(() => {
    document.querySelector('[data-live-key="held"] select')!.addEventListener('blur', () => { window.__blurs += 1; });
  });
  server.current = snapshot({ other: 'Remote update', label: 'Renamed label' });

  expect(await handleRefresh(page)).toBe('applied');

  expect(await original!.evaluate((node) => node.isConnected && node === document.activeElement)).toBe(true);
  await expect(page.locator('[data-live-key="other"]')).toHaveText('Remote update');
  await expect(page.getByTestId('held-label')).toHaveText('Renamed label');
  expect(await page.evaluate(() => window.__blurs)).toBe(0);
  expect(await statuses(page)).toEqual(['healthy']);
  await assertNoConsoleErrors(page);
});

test('a chosen select value survives and reordering waits until the choice is released', async ({ page }) => {
  const server = await openLive(page);
  await select(page).selectOption('b');
  const original = await select(page).elementHandle();
  server.current = snapshot({ other: 'Reordered', keys: ['other', 'held', 'tail', 'disclosure', 'dialog', 'log', 'owned'] });

  expect(await handleRefresh(page)).toBe('deferred');

  await expect(page.getByTestId('other')).toHaveText('Reordered');
  expect((await regionKeys(page))[0]).toBe('held');
  expect(await select(page).inputValue()).toBe('b');
  expect(await original!.evaluate((node) => node.isConnected)).toBe(true);

  await select(page).selectOption('a');
  await page.evaluate(() => (document.activeElement as HTMLElement).blur());
  expect(await handleFlush(page)).toEqual({ accepted: true, deferred: false });
  expect((await regionKeys(page))[0]).toBe('other');
  expect(await original!.evaluate((node) => node.isConnected && (node as HTMLSelectElement).value)).toBe('a');
});

test('typed text and its caret are retained while the held item cannot be removed or reparented', async ({ page }) => {
  const server = await openLive(page);
  const note = page.locator('#note');
  await note.fill('draft text');
  await note.evaluate((input: HTMLInputElement) => input.setSelectionRange(2, 5));
  const original = await note.elementHandle();

  server.current = snapshot({ other: 'After typing', keys: ['other', 'tail', 'disclosure', 'dialog', 'log', 'owned'] });
  expect(await handleRefresh(page)).toBe('deferred');
  await expect(page.getByTestId('other')).toHaveText('After typing');
  expect(await original!.evaluate((node: HTMLInputElement) => (
    node.isConnected && node === document.activeElement && node.value === 'draft text'
      && node.selectionStart === 2 && node.selectionEnd === 5
  ))).toBe(true);
  expect(await regionKeys(page)).toContain('held');

  server.current = snapshot({ other: 'Reparented', wrapHeld: true });
  expect(await handleRefresh(page)).toBe('deferred');
  expect(await page.locator('#note').evaluate((node) => node.closest('[data-live-key="wrap"]') === null)).toBe(true);

  await note.fill('');
  await page.evaluate(() => (document.activeElement as HTMLElement).blur());
  expect(await handleFlush(page)).toEqual({ accepted: true, deferred: false });
  await expect(page.locator('[data-live-key="wrap"] #note')).toHaveCount(1);
});

test('a focused item removed remotely stays until released and only the latest target applies', async ({ page }) => {
  const server = await openLive(page);
  await select(page).focus();
  const original = await select(page).elementHandle();

  server.current = snapshot({ other: 'First', keys: ['other', 'tail', 'dialog', 'owned'] });
  expect(await handleRefresh(page)).toBe('deferred');
  server.current = snapshot({
    other: 'Second', tail: 'Latest tail', keys: ['other', 'tail', 'disclosure', 'dialog', 'owned'],
  });
  expect(await handleRefresh(page)).toBe('deferred');

  await expect(page.getByTestId('other')).toHaveText('Second');
  expect(await original!.evaluate((node) => node.isConnected && node === document.activeElement)).toBe(true);

  await page.evaluate(() => (document.activeElement as HTMLElement).blur());
  expect(await handleFlush(page)).toEqual({ accepted: true, deferred: false });
  expect(await regionKeys(page)).toEqual(['other', 'tail', 'disclosure', 'dialog-host', 'owned-host']);
  await expect(page.getByTestId('tail')).toHaveText('Latest tail');
  expect(await original!.evaluate((node) => node.isConnected)).toBe(false);
});

test('releasing focus applies the deferred target without another network read', async ({ page }) => {
  const server = await openLive(page);
  await select(page).focus();
  server.current = snapshot({ other: 'Deferred', keys: ['other', 'tail', 'dialog', 'owned'] });
  expect(await handleRefresh(page)).toBe('deferred');
  const requests = server.requests.length;

  await page.evaluate(() => (document.activeElement as HTMLElement).blur());
  await page.clock.runFor(10);

  await expect.poll(() => regionKeys(page)).toEqual(['other', 'tail', 'dialog-host', 'owned-host']);
  expect(server.requests.length).toBe(requests);
});

test('local details and dialog state is preserved while the item with an open dialog stays', async ({ page }) => {
  const server = await openLive(page);
  await page.locator('[data-live-key="disclosure"] summary').click();
  await expect(page.locator('details')).toHaveAttribute('open', '');
  await page.evaluate(() => (document.getElementById('dlg') as HTMLDialogElement).show());
  const dialog = await page.locator('#dlg').elementHandle();

  server.current = snapshot({
    detail: 'Detail remote',
    dialogLabel: 'Dialog remote',
    keys: ['held', 'other', 'tail', 'disclosure', 'log', 'owned'],
  });
  expect(await handleRefresh(page)).toBe('deferred');

  await expect(page.locator('details')).toHaveAttribute('open', '');
  await expect(page.getByTestId('disclosure-text')).toHaveText('Detail remote');
  expect(await dialog!.evaluate((node: HTMLDialogElement) => node.isConnected && node.open)).toBe(true);

  await page.evaluate(() => {
    const dlg = document.getElementById('dlg') as HTMLDialogElement;
    dlg.close();
    dlg.remove();
    (document.activeElement as HTMLElement).blur();
  });
  expect(await handleFlush(page)).toEqual({ accepted: true, deferred: false });
  expect(await dialog!.evaluate((node) => node.isConnected)).toBe(false);
  await expect(page.locator('[data-live-key="dialog-host"]')).toHaveCount(0);
});

test('details remain closed locally even when the server renders them open', async ({ page }) => {
  const server = await openLive(page);
  server.current = snapshot({ detailsOpen: true, detail: 'Opened remotely' });

  expect(await handleRefresh(page)).toBe('applied');

  await expect(page.getByTestId('disclosure-text')).toHaveText('Opened remotely');
  await expect(page.locator('details')).not.toHaveAttribute('open', /.*/);
});

test('controller-owned forms and subtrees are never morphed', async ({ page }) => {
  const server = await openLive(page);
  server.current = snapshot({ other: 'Outside change', ownedText: 'Server owned change', formClass: 'changed' });

  expect(await handleRefresh(page)).toBe('applied');

  await expect(page.getByTestId('other')).toHaveText('Outside change');
  await expect(page.getByTestId('owned-form')).toHaveClass('original');
  await expect(page.getByTestId('owned-sub')).toHaveText('Owned text');
  await expect(page.getByTestId('xterm')).toHaveText('Owned text');

  await page.locator('#owned-name').fill('typed');
  await page.evaluate(() => (document.activeElement as HTMLElement).blur());
  server.current = snapshot({ keys: ['held', 'other', 'tail', 'disclosure', 'dialog', 'log'] });
  expect(await handleRefresh(page)).toBe('deferred');
  await expect(page.locator('#owned-name')).toHaveValue('typed');
});

const OWNED_REMOVAL_CASES = [
  {
    name: 'a clean xterm', host: 'owned-host', omit: 'owned', keep: '[data-testid="xterm"]',
    others: ['[data-testid="owned-form"]', '[data-testid="owned-sub"]'],
  },
  {
    name: 'a clean data-live-owned subtree', host: 'owned-host', omit: 'owned', keep: '[data-testid="owned-sub"]',
    others: ['[data-testid="owned-form"]', '[data-testid="xterm"]'],
  },
  {
    name: 'a clean form', host: 'owned-host', omit: 'owned', keep: '[data-testid="owned-form"]',
    others: ['[data-testid="owned-sub"]', '[data-testid="xterm"]'],
  },
  { name: 'a closed dialog', host: 'dialog-host', omit: 'dialog', keep: '#dlg', others: [] as string[] },
];

for (const owned of OWNED_REMOVAL_CASES) {
  test(`a snapshot never destroys ${owned.name} inside a removed keyed item`, async ({ page }) => {
    const server = await openLive(page);
    await page.evaluate((selectors) => {
      for (const selector of selectors) document.querySelector(selector)!.remove();
    }, owned.others);
    const hostSelector = `[data-live-key="${owned.host}"]`;
    const host = await page.locator(hostSelector).elementHandle();
    const kept = await page.locator(`${hostSelector} ${owned.keep}`).elementHandle();

    server.current = snapshot({
      other: 'Remote change',
      keys: ALL_KEYS.filter((key) => key !== owned.omit && key !== 'tail'),
    });
    expect(await handleRefresh(page)).toBe('deferred');

    await expect(page.getByTestId('other')).toHaveText('Remote change');
    await expect(page.locator('[data-live-key="tail"]')).toHaveCount(0);
    expect(await host!.evaluate((node) => node.isConnected)).toBe(true);
    expect(await kept!.evaluate((node) => node.isConnected)).toBe(true);
    expect(await page.evaluate(() => window.FlowgencyLive.handles.get('test')!.etag)).toBe(etagOf(server.current));

    // The controller disposes the owned content; only then may the item go.
    await kept!.evaluate((node) => node.remove());
    expect(await handleFlush(page)).toEqual({ accepted: true, deferred: false });
    expect(await host!.evaluate((node) => node.isConnected)).toBe(false);
  });
}

test('a navigation drops the pending target of the previous binding', async ({ page }) => {
  const server = await openLive(page);
  await select(page).focus();
  server.current = snapshot({ other: 'Old record', keys: ['other', 'tail'] });
  expect(await handleRefresh(page)).toBe('deferred');

  const next = { ...BINDING, entity: 'record-2' };
  await page.evaluate((binding) => window.__navigate(binding), next);
  server.script.push({ status: 404, body: { detail: 'gone' } });
  expect(await page.evaluate(() => window.FlowgencyLive.handles.get('test')!.invalidate())).toBe('unavailable');
  expect(server.requests.at(-1)!.ifNoneMatch).toBeUndefined();

  await page.evaluate(() => (document.activeElement as HTMLElement).blur());
  expect(await handleFlush(page)).toEqual({ accepted: true, deferred: false });
  expect(await regionKeys(page)).toEqual(['held', 'other', 'tail', 'disclosure', 'dialog-host', 'log', 'owned-host']);

  server.current = snapshot({ other: 'New record' }, { binding: next });
  expect(await handleRefresh(page)).toBe('applied');
  await expect(page.getByTestId('other')).toHaveText('New record');
});

async function deferThenBreakTail(page: Page, server: LiveServer) {
  await select(page).focus();
  server.current = snapshot({
    other: 'Deferred', tail: 'Deferred tail', keys: ['other', 'held', 'tail', 'disclosure', 'dialog', 'log', 'owned'],
  });
  expect(await handleRefresh(page)).toBe('deferred');
  expect(await page.evaluate(() => window.FlowgencyLive.handles.get('test')!.etag)).toBe(etagOf(server.current));
  await page.evaluate(() => {
    const replacement = document.createElement('section');
    replacement.setAttribute('data-live-key', 'tail');
    replacement.textContent = 'Local tail';
    document.querySelector('[data-live-key="tail"]')!.replaceWith(replacement);
    (document.activeElement as HTMLElement).blur();
  });
}

async function restoreTail(page: Page) {
  await page.evaluate(() => {
    const replacement = document.createElement('div');
    replacement.setAttribute('data-live-key', 'tail');
    replacement.setAttribute('data-testid', 'tail');
    replacement.textContent = 'Local tail';
    document.querySelector('[data-live-key="tail"]')!.replaceWith(replacement);
  });
}

test('a deferred region that can no longer apply reports a rejection and the next poll converges', async ({ page }) => {
  const server = await openLive(page);
  await deferThenBreakTail(page, server);

  const result = await handleFlush(page);
  expect(result.accepted).toBe(false);
  expect(await page.evaluate(() => window.FlowgencyLive.handles.get('test')!.etag)).toBeNull();
  await expect.poll(() => statuses(page)).toContain('incompatible');
  expect(server.requests.at(-1)!.ifNoneMatch).toBeUndefined();

  await restoreTail(page);
  expect(await handleRefresh(page)).toBe('applied');
  expect(server.requests.at(-1)!.ifNoneMatch).toBeUndefined();
  await expect(page.getByTestId('other')).toHaveText('Deferred');
  expect((await regionKeys(page))[0]).toBe('other');
});

test('a release that drops a deferred region clears the ETag so the next read is a full 200', async ({ page }) => {
  const server = await openLive(page);
  await deferThenBreakTail(page, server);

  await page.clock.runFor(10);
  await expect.poll(() => statuses(page)).toContain('incompatible');
  expect(server.requests.at(-1)!.ifNoneMatch).toBeUndefined();
  expect(await page.evaluate(() => window.FlowgencyLive.handles.get('test')!.etag)).toBeNull();

  await restoreTail(page);
  expect(await handleRefresh(page)).toBe('applied');
  expect(server.requests.at(-1)!.ifNoneMatch).toBeUndefined();
  await expect(page.getByTestId('other')).toHaveText('Deferred');
});

test('a followed scroller stays at the bottom and an unfollowed one keeps its position', async ({ page }) => {
  const server = await openLive(page);
  const log = page.getByTestId('log');
  await log.evaluate((element) => { element.scrollTop = element.scrollHeight; });
  server.current = snapshot({ logLines: 30 });
  await handleRefresh(page);
  expect(await log.evaluate((element) => element.scrollHeight - element.scrollTop - element.clientHeight)).toBeLessThanOrEqual(2);

  await log.evaluate((element) => { element.scrollTop = 25; });
  server.current = snapshot({ logLines: 40 });
  await handleRefresh(page);
  expect(await log.evaluate((element) => element.scrollTop)).toBe(25);
});

test('selected page text is not rewritten under the user until the selection ends', async ({ page }) => {
  const server = await openLive(page);
  await page.getByTestId('other').evaluate((element) => {
    const range = document.createRange();
    range.selectNodeContents(element);
    const selection = window.getSelection()!;
    selection.removeAllRanges();
    selection.addRange(range);
  });
  server.current = snapshot({ other: 'Remote text', tail: 'Tail changed' });

  expect(await handleRefresh(page)).toBe('deferred');

  await expect(page.getByTestId('other')).toHaveText('Initial');
  await expect(page.getByTestId('tail')).toHaveText('Tail changed');
  expect(await page.evaluate(() => window.getSelection()!.toString())).toBe('Initial');

  await page.evaluate(() => window.getSelection()!.removeAllRanges());
  expect(await handleFlush(page)).toEqual({ accepted: true, deferred: false });
  await expect(page.getByTestId('other')).toHaveText('Remote text');
});

test('malformed and unsafe responses keep the old DOM and never become the ETag', async ({ page }) => {
  const server = await openLive(page);
  server.current = snapshot({ other: 'Accepted' });
  expect(await handleRefresh(page)).toBe('applied');
  const accepted = etagOf(server.current);

  server.script.push({ body: '{not json', etag: '"malformed"' });
  expect(await handleRefresh(page)).toBe('failed');
  server.script.push({ body: { unexpected: true }, etag: '"shape"' });
  expect(await handleRefresh(page)).toBe('failed');

  const unsafe = [
    '<script>window.__pwned = true</script>',
    '<img src="x" onerror="window.__pwned = true">',
    '<a href=" jav&#x61;script:window.__pwned = true">x</a>',
    '<iframe srcdoc="<p>x</p>"></iframe>',
    '<svg><script>window.__pwned = true</script></svg>',
  ];
  for (const markup of unsafe) {
    server.script.push({ body: snapshot({ other: `Unsafe ${markup}` }, { regions: [{ key: 'page', html: markup }] }), etag: '"unsafe"' });
    expect(await handleRefresh(page)).toBe('rejected');
  }

  await expect(page.getByTestId('other')).toHaveText('Accepted');
  expect(await page.evaluate(() => window.__pwned)).toBeUndefined();
  expect(await statuses(page)).toEqual(['healthy', 'stale']);
  await handleRefresh(page);
  expect(server.requests.at(-1)?.ifNoneMatch).toBe(accepted);
  expect(server.requests.slice(1, -1).map((request) => request.ifNoneMatch)).toEqual(Array(server.requests.length - 2).fill(accepted));
});

test('an incompatible binding, structure, region set or key tag is rejected before any mutation', async ({ page }) => {
  const server = await openLive(page);
  const before = await page.locator('#page-region').innerHTML();
  const wrongTag = region().replace('<div data-live-key="other" data-testid="other">', '<p data-live-key="other" data-testid="other">')
    .replace('Initial</div>', 'Initial</p>');
  const cases: LiveSnapshotShape[] = [
    snapshot({ other: 'Changed' }, { binding: { ...BINDING, entity: 'someone-else' } }),
    snapshot({ other: 'Changed' }, { binding: { ...BINDING, query: { filter: 'x' } } }),
    snapshot({ other: 'Changed' }, { structure: 'test:2' }),
    snapshot({ other: 'Changed' }, { regions: [{ key: 'unknown', html: '<p>x</p>' }] }),
    snapshot({ other: 'Changed' }, { regions: [{ key: 'page', html: wrongTag }] }),
    snapshot({ other: 'Changed' }, { regions: [{ key: 'page', html: `${region()}${region()}` }] }),
  ];
  for (const incompatible of cases) {
    server.script.push({ body: incompatible, etag: '"incompatible"' });
    expect(await handleRefresh(page)).toBe('incompatible');
    expect(await page.locator('#page-region').innerHTML()).toBe(before);
  }
  expect((await statuses(page)).every((value) => value === 'incompatible')).toBe(true);
  await expect(page.locator('#app')).toHaveAttribute('data-live-status', 'incompatible');
  server.current = snapshot({ other: 'Compatible again' });
  expect(await handleRefresh(page)).toBe('applied');
  expect(server.requests.every((request) => request.ifNoneMatch === undefined)).toBe(true);
  await expect(page.locator('#app')).toHaveAttribute('data-live-status', 'healthy');
});

test('304 responses and ETag acceptance follow only accepted snapshots', async ({ page }) => {
  const server = await openLive(page);
  server.current = snapshot({ other: 'One' });
  const first = etagOf(server.current);

  expect(await handleRefresh(page)).toBe('applied');
  expect(server.requests[0].ifNoneMatch).toBeUndefined();
  expect(await handleRefresh(page)).toBe('not-modified');
  expect(server.requests[1].ifNoneMatch).toBe(first);
  expect(await applied(page)).toBe(1);
  await expect(page.getByTestId('other')).toHaveText('One');

  await select(page).focus();
  server.current = snapshot({ other: 'Two', keys: ['other', 'held', 'tail', 'disclosure', 'dialog', 'log', 'owned'] });
  expect(await handleRefresh(page)).toBe('deferred');
  const deferredEtag = etagOf(server.current);
  expect(await handleRefresh(page)).toBe('not-modified');
  expect(server.requests[3].ifNoneMatch).toBe(deferredEtag);
  expect(await page.evaluate(() => window.FlowgencyLive.handles.get('test')!.etag)).toBe(deferredEtag);
});

test('overlapping manual reads abort the superseded request and only the latest applies', async ({ page }) => {
  const server = await openLive(page);
  const slow = deferred();
  server.script.push({ body: snapshot({ other: 'Stale response' }), gate: slow.promise });
  server.script.push({ body: snapshot({ other: 'Fresh response' }) });

  await page.evaluate(() => { window.__pending = window.FlowgencyLive.handles.get('test')!.refresh(); });
  await expect.poll(() => server.requests.length).toBe(1);
  expect(await handleRefresh(page)).toBe('applied');
  slow.release();

  expect(await page.evaluate(() => window.__pending)).toBe('superseded');
  await expect(page.getByTestId('other')).toHaveText('Fresh response');
  expect(server.aborted).toBe(1);
  expect(await applied(page)).toBe(1);
});

test('passive reads follow the cadence and never overlap a slow request', async ({ page }) => {
  const server = await openLive(page);
  const slow = deferred();
  server.script.push({ body: snapshot({ other: 'Slow' }), gate: slow.promise });

  await page.clock.runFor(1999);
  expect(server.requests.length).toBe(0);
  await page.clock.runFor(1);
  await expect.poll(() => server.requests.length).toBe(1);
  await page.clock.runFor(10_000);
  expect(server.requests.length).toBe(1);

  slow.release();
  await expect(page.getByTestId('other')).toHaveText('Slow');
  await page.clock.runFor(1999);
  expect(server.requests.length).toBe(1);
  await page.clock.runFor(1);
  await expect.poll(() => server.requests.length).toBe(2);
});

test('failures retain the DOM, report stale or unavailable and retry at the normal cadence', async ({ page }) => {
  const server = await openLive(page);
  server.script.push({ status: 500, body: 'boom' });
  await page.clock.runFor(2000);
  await expect.poll(() => statuses(page)).toEqual(['stale']);
  await expect(page.getByTestId('other')).toHaveText('Initial');

  server.script.push({ status: 404, body: { detail: 'gone' } });
  await page.clock.runFor(2000);
  await expect.poll(() => statuses(page)).toEqual(['stale', 'unavailable']);
  await expect(page.getByTestId('other')).toHaveText('Initial');
  await expect(page.locator('#app')).toHaveAttribute('data-live-status', 'unavailable');

  server.current = snapshot({ other: 'Recovered' });
  await page.clock.runFor(2000);
  await expect(page.getByTestId('other')).toHaveText('Recovered');
  expect(await statuses(page)).toEqual(['stale', 'unavailable', 'healthy']);
});

test('a request that never settles times out as stale and the cadence resumes', async ({ page }) => {
  const server = await openLive(page, { timeout: 3000 });
  server.script.push({ body: snapshot({ other: 'Never' }), gate: new Promise<void>(() => {}) });
  await page.clock.runFor(2000);
  await expect.poll(() => server.requests.length).toBe(1);
  await page.clock.runFor(3000);
  await expect.poll(() => statuses(page)).toEqual(['stale']);
  server.current = snapshot({ other: 'Retried' });
  await page.clock.runFor(2000);
  await expect(page.getByTestId('other')).toHaveText('Retried');
  expect(server.aborted).toBe(1);
});

test('hidden documents abort the read, stay quiet and recover immediately when visible', async ({ page }) => {
  const server = await openLive(page);
  const gate = deferred();
  server.script.push({ body: snapshot({ other: 'Aborted data' }), gate: gate.promise });
  await page.clock.runFor(2000);
  await expect.poll(() => server.requests.length).toBe(1);

  await setVisibility(page, 'hidden');
  await expect.poll(() => server.aborted).toBe(1);
  gate.release();
  await page.clock.runFor(30_000);
  expect(server.requests.length).toBe(1);
  expect(await handleRefresh(page)).toBe('hidden');
  expect(server.requests.length).toBe(1);
  await expect(page.getByTestId('other')).toHaveText('Initial');

  server.current = snapshot({ other: 'Caught up' });
  await setVisibility(page, 'visible');
  await expect(page.getByTestId('other')).toHaveText('Caught up');
  expect(server.requests.length).toBe(2);
});

test('actions pause reads, discard in-flight results and trigger one catch-up on settlement', async ({ page }) => {
  const server = await openLive(page);
  const gate = deferred();
  server.script.push({ body: snapshot({ other: 'During action' }), gate: gate.promise });
  await page.clock.runFor(2000);
  await expect.poll(() => server.requests.length).toBe(1);

  await page.evaluate(() => { window.__finish = window.FlowgencyLive.handles.get('test')!.beginAction(); });
  await expect.poll(() => server.aborted).toBe(1);
  gate.release();
  expect(await handleRefresh(page)).toBe('paused');
  await page.clock.runFor(20_000);
  expect(server.requests.length).toBe(1);
  await expect(page.getByTestId('other')).toHaveText('Initial');

  server.current = snapshot({ other: 'After action' });
  await page.evaluate(() => { window.__finish!(); window.__finish!(); });
  await expect(page.getByTestId('other')).toHaveText('After action');
  expect(server.requests.length).toBe(2);
  await page.clock.runFor(2000);
  await expect.poll(() => server.requests.length).toBe(3);
});

test('a body that finishes parsing after an action starts or the document hides is discarded', async ({ page }) => {
  const server = await openLive(page);
  server.current = snapshot({ other: 'Parsed late' });
  await page.evaluate(() => {
    window.__bodyGate = new Promise<void>((resolve) => { window.__releaseBody = resolve; });
    window.__pending = window.FlowgencyLive.handles.get('test')!.refresh();
  });
  await expect.poll(() => server.requests.length).toBe(1);
  await page.evaluate(() => { window.__finish = window.FlowgencyLive.handles.get('test')!.beginAction(); });
  await page.evaluate(() => { window.__releaseBody!(); });
  expect(await page.evaluate(() => window.__pending)).toBe('discarded');
  await expect(page.getByTestId('other')).toHaveText('Initial');
  expect(await applied(page)).toBe(0);

  await page.evaluate(() => {
    window.__bodyGate = new Promise<void>((resolve) => { window.__releaseBody = resolve; });
    window.__finish!();
  });
  await expect.poll(() => server.requests.length).toBe(2);
  await setVisibility(page, 'hidden');
  await page.evaluate(() => { window.__releaseBody!(); });
  await expect(page.getByTestId('other')).toHaveText('Initial');
  expect(await applied(page)).toBe(0);
  await page.evaluate(() => { window.__bodyGate = null; });
  await setVisibility(page, 'visible');
  await expect(page.getByTestId('other')).toHaveText('Parsed late');
});

test('controller navigation generations discard a stale parsed body', async ({ page }) => {
  const server = await openLive(page);
  server.current = snapshot({ other: 'Wrong navigation' });
  await page.evaluate(() => {
    window.__bodyGate = new Promise<void>((resolve) => { window.__releaseBody = resolve; });
    window.__pending = window.FlowgencyLive.handles.get('test')!.refresh();
  });
  await expect.poll(() => server.requests.length).toBe(1);
  await page.evaluate(() => { window.__generation += 1; window.__releaseBody!(); });
  expect(await page.evaluate(() => window.__pending)).toBe('discarded');
  await expect(page.getByTestId('other')).toHaveText('Initial');
});

test('invalidating a binding drops the ETag and the pending read', async ({ page }) => {
  const server = await openLive(page);
  server.current = snapshot({ other: 'Bound' });
  expect(await handleRefresh(page)).toBe('applied');
  const gate = deferred();
  server.script.push({ body: snapshot({ other: 'Old binding' }), gate: gate.promise });
  await page.evaluate(() => { window.__pending = window.FlowgencyLive.handles.get('test')!.refresh(); });
  await expect.poll(() => server.requests.length).toBe(2);

  expect(await page.evaluate(() => window.FlowgencyLive.handles.get('test')!.invalidate())).toBe('applied');
  gate.release();
  expect(await page.evaluate(() => window.__pending)).toBe('superseded');
  expect(server.requests[2].ifNoneMatch).toBeUndefined();
});

test('the handles registry is read-only and each key can be registered once', async ({ page }) => {
  const server = await openLive(page);
  const outcome = await page.evaluate(() => {
    const handles = window.FlowgencyLive.handles as Map<string, unknown>;
    const failures: string[] = [];
    for (const attempt of [() => handles.set('x', {}), () => handles.delete('test'), () => handles.clear()]) {
      try { attempt(); } catch (error) { failures.push((error as Error).name); }
    }
    let duplicate = '';
    try {
      window.FlowgencyLive.register({
        key: 'test', binding: () => ({ page: 'test' }), url: () => '/x', apply: () => ({ accepted: true, deferred: false }),
      });
    } catch (error) { duplicate = (error as Error).name; }
    return { failures, duplicate, has: handles.has('test'), size: handles.size };
  });
  expect(outcome).toEqual({ failures: ['TypeError', 'TypeError', 'TypeError'], duplicate: 'Error', has: true, size: 1 });

  await page.evaluate(() => window.FlowgencyLive.handles.get('test')!.dispose());
  expect(await page.evaluate(() => window.FlowgencyLive.handles.size)).toBe(0);
  await page.clock.runFor(10_000);
  expect(server.requests.length).toBe(0);
});


// ── Shared navigation shell: base-page registration ─────────────────────────

const ROSTER_SNAPSHOT = /\/newsletter\/agents\?__live=1$/;

async function openRoster(page: Page): Promise<void> {
  await page.clock.install({ time: 0 });
  await page.goto('/newsletter/agents');
}

test('a page without live regions registers nothing and sends no periodic request', async ({ page }) => {
  const live: string[] = [];
  page.on('request', (request) => { if (request.url().includes('__live=1')) live.push(request.url()); });
  await page.clock.install({ time: 0 });
  await page.goto('/newsletter/logs');
  await page.clock.pauseAt(600_000);
  await page.clock.runFor(10_000);

  expect(await page.evaluate(() => typeof window.FlowgencyLive)).toBe('undefined');
  await expect(page.locator('[data-live-status]')).toHaveCount(0);
  expect(live).toEqual([]);
});

test('an opted-in page registers one handle and sends a conditional poll', async ({ page }) => {
  const live: Record<string, string>[] = [];
  page.on('request', (request) => {
    if (request.url().includes('__live=1')) live.push(request.headers());
  });
  await openRoster(page);
  await page.clock.pauseAt(60_000);

  expect(await page.evaluate(() => Array.from(window.FlowgencyLive.handles.keys()))).toEqual(['page']);
  await expect(page.locator('[data-live-status]')).toBeHidden();
  await expect.poll(() => live.length).toBe(1);
  expect(live[0]['if-none-match']).toBeUndefined();
  expect(live[0].accept).toContain('application/json');

  await expect.poll(() => page.evaluate(() => window.FlowgencyLive.handles.get('page')!.status)).toBe('healthy');
  await page.clock.runFor(2000);
  await expect.poll(() => live.length).toBe(2);
  expect(live[1]['if-none-match']).toMatch(/^"[0-9a-f]{64}"$/);
  await expect(page.locator('[data-live-status]')).toBeHidden();
});

test('a passive failure shows the status shell and only a click retries', async ({ page }) => {
  await openRoster(page);
  await page.clock.pauseAt(60_000);
  await expect.poll(() => page.evaluate(() => window.FlowgencyLive.handles.get('page')!.status)).toBe('healthy');

  let navigations = 0;
  page.on('framenavigated', (frame) => { if (frame === page.mainFrame()) navigations += 1; });
  await page.route(ROSTER_SNAPSHOT, (route) => route.fulfill({ status: 500, body: 'down' }));
  await page.clock.runFor(2000);

  const shell = page.locator('[data-live-status]');
  await expect(shell).toBeVisible();
  await expect(shell).toHaveAttribute('data-live-status', 'stale');
  await expect(shell).toHaveAttribute('role', 'status');
  await expect(shell.locator('[data-live-status-label]')).not.toBeEmpty();
  await expect(shell.locator('[data-live-manual-refresh] svg')).toHaveCount(1);
  await expect(shell.getByRole('button', { name: 'Refresh page' })).toBeVisible();
  await page.clock.runFor(10_000);
  expect(navigations).toBe(0);

  await page.unroute(ROSTER_SNAPSHOT);
  await shell.getByRole('button', { name: 'Refresh page' }).click();
  await expect(shell).toBeHidden();
  expect(navigations).toBe(0);
});

test('an incompatible snapshot asks for a reload instead of mutating the page', async ({ page }) => {
  await openRoster(page);
  await page.clock.pauseAt(60_000);
  await expect.poll(() => page.evaluate(() => window.FlowgencyLive.handles.get('page')!.status)).toBe('healthy');

  await page.route(ROSTER_SNAPSHOT, async (route) => {
    const { 'if-none-match': _conditional, ...headers } = route.request().headers();
    const response = await route.fetch({ headers });
    const body = await response.json() as LiveSnapshotShape;
    await route.fulfill({ response, json: { ...body, structure: 'agents-shell:99' } });
  });
  const before = await page.locator('#sidebar').innerHTML();
  await page.clock.runFor(2000);

  const shell = page.locator('[data-live-status]');
  await expect(shell).toHaveAttribute('data-live-status', 'incompatible');
  await expect(shell).toBeVisible();
  expect(await page.locator('#sidebar').innerHTML()).toBe(before);

  await page.unroute(ROSTER_SNAPSHOT);
  await Promise.all([
    page.waitForEvent('framenavigated'),
    shell.getByRole('button', { name: 'Refresh page' }).click(),
  ]);
  await expect(page.locator('[data-live-status]')).toBeHidden();
});
