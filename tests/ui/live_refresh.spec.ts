import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import path from 'node:path';

import {
  expect,
  test,
  type APIRequestContext,
  type BrowserContext,
  type ElementHandle,
  type Page,
  type Route,
} from '@playwright/test';

import {
  assertNoConsoleErrors,
  assertOnlyExpectedConflictConsoleError,
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
    __raw: { applied: unknown[]; statuses: string[]; settled: number };
    __mountRaw: () => void;
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
    cancel: '<form method="post" action="/jobs/1/cancel" data-live-disposable data-live-key="job-action:cancel" data-testid="cancel-form">'
      + '<button type="submit" data-testid="cancel-button">Cancel</button></form>',
    resume: '<form method="post" action="/jobs/1/resume" data-live-disposable data-live-key="job-action:resume" data-testid="resume-form">'
      + '<button type="submit" data-testid="resume-button">Resume</button></form>',
    'plain-a': '<form method="post" action="/a" data-live-key="plain:a" data-testid="plain-a"><button type="submit">A</button></form>',
    'plain-b': '<form method="post" action="/b" data-live-key="plain:b" data-testid="plain-b"><button type="submit">B</button></form>',
    'unkeyed-a': '<form method="post" action="/a" data-testid="unkeyed-a"><button type="submit">A</button></form>',
    'unkeyed-b': '<form method="post" action="/b" data-testid="unkeyed-b"><button type="submit">B</button></form>',
    'wrap-div': wrappedForm('div', 'wrap-old'),
    'wrap-section': wrappedForm('section', 'wrap-new'),
  };
  return m.keys.map((key) => parts[key]).join('\n');
}

function wrappedForm(tag: string, testId: string): string {
  return `<${tag} data-testid="${testId}"><form method="post" action="/w" data-testid="wrapped-form">`
    + `<input id="wrapped-field" name="field" type="text" value=""></form></${tag}>`;
}

interface RootModel {
  options: string[];
  text: string;
  sibling: string;
}

function rootRegions(overrides: Partial<RootModel> = {}): { key: string; html: string }[] {
  const m: RootModel = { options: ['Alpha', 'Beta'], text: 'Root direct text', sibling: 'Sibling', ...overrides };
  return [
    {
      key: 'switcher',
      html: m.options.map((label, index) => `<option value="${'abcd'[index]}">${label}</option>`).join(''),
    },
    { key: 'root-text', html: `${m.text}<span data-testid="root-sibling">${m.sibling}</span>` },
  ];
}

function rootSnapshot(
  overrides: Partial<Model> = {},
  roots: Partial<RootModel> = {},
): LiveSnapshotShape {
  const base = snapshot(overrides);
  return { ...base, regions: [...base.regions, ...rootRegions(roots)] };
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

// A region root that is itself a control or holds direct text, like the real team switcher.
function rootsHtml(): string {
  const [switcher, text] = rootRegions();
  return `<select id="root-select" data-live-region="${switcher.key}" aria-label="Root select">${switcher.html}</select>`
    + `<p id="root-text" data-live-region="${text.key}">${text.html}</p>`;
}

function documentHtml(roots = false): string {
  return `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Live refresh contract</title></head>
<body>
<main id="app"><div data-live-region="page" id="page-region">${region()}</div>${roots ? rootsHtml() : ''}</main>
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
window.__raw = { applied: [], statuses: [], settled: 0 };
// An adapter whose payload is its own document rather than a region snapshot.
window.__mountRaw = () => {
  FlowgencyLive.register({
    key: 'raw',
    binding: () => ({ page: 'raw' }),
    url: () => '${SNAPSHOT_PATH}?raw=1',
    validate: (data) => {
      if (!data || typeof data !== 'object' || Array.isArray(data)) return false;
      return data.incompatible ? 'incompatible' : true;
    },
    apply: (data) => {
      window.__raw.applied.push(data);
      if (data.unavailable) return { accepted: false, deferred: false, unavailable: true };
      if (data.rejected) return { accepted: false, deferred: false };
      if (data.untouched) return { accepted: false, deferred: false, unchanged: true };
      if (data.applyIncompatible) return { accepted: false, deferred: false, incompatible: true };
      if (data.throws) throw new Error('apply failed');
      return { accepted: true, deferred: data.deferred === true };
    },
    status: (value) => window.__raw.statuses.push(value),
    settled: () => { window.__raw.settled += 1; },
  });
};
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

async function openLive(page: Page, options: { interval?: number; timeout?: number; roots?: boolean } = {}) {
  const server = new LiveServer();
  if (options.roots) server.current = rootSnapshot();
  await page.route(`**${DOCUMENT_PATH}`, (route) => route.fulfill({
    status: 200,
    contentType: 'text/html; charset=utf-8',
    body: documentHtml(options.roots),
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

// Reduces the page to `other` plus the given action forms; the default owned
// content (which a passive snapshot would defer) is disposed first.
async function showActions(page: Page, server: LiveServer, ...keys: string[]) {
  await page.evaluate(() => {
    for (const host of document.querySelectorAll('[data-live-key="owned-host"], [data-live-key="dialog-host"]')) host.remove();
  });
  server.current = snapshot({ keys: ['other', ...keys] });
  expect(await handleRefresh(page)).toBe('applied');
}

const testId = (page: Page, id: string) => page.getByTestId(id);
const connected = (handle: ElementHandle) => handle.evaluate((node) => node.isConnected);
const etagRetained = (page: Page) => page.evaluate(() => window.FlowgencyLive.handles.get('test')!.etag);

test.describe('disposable action forms', () => {
  test('a disposable form with the same key keeps its node while the rest updates', async ({ page }) => {
    const server = await openLive(page);
    await showActions(page, server, 'cancel');
    const form = await testId(page, 'cancel-form').elementHandle();

    server.current = snapshot({ other: 'Changed', keys: ['other', 'cancel'] });
    expect(await handleRefresh(page)).toBe('applied');

    await expect(testId(page, 'other')).toHaveText('Changed');
    expect(await connected(form!)).toBe(true);
    expect(await regionKeys(page)).toEqual(['other', 'job-action:cancel']);
  });

  test('a snapshot replacing one disposable form with another removes the old and inserts the new', async ({ page }) => {
    const server = await openLive(page);
    await showActions(page, server, 'cancel');
    const cancel = await testId(page, 'cancel-form').elementHandle();

    server.current = snapshot({ keys: ['other', 'resume'] });
    expect(await handleRefresh(page)).toBe('applied');

    expect(await connected(cancel!)).toBe(false);
    expect(await regionKeys(page)).toEqual(['other', 'job-action:resume']);
    await expect(testId(page, 'resume-form')).toHaveAttribute('action', '/jobs/1/resume');
    expect((await page.evaluate(() => window.__applied)).at(-1)).toEqual({ accepted: true, deferred: false });
    expect(await etagRetained(page)).toBe(etagOf(server.current));
  });

  test('a held disposable form defers the swap until it is released, then the new form appears', async ({ page }) => {
    const server = await openLive(page);
    await showActions(page, server, 'cancel');
    const cancel = await testId(page, 'cancel-form').elementHandle();
    await testId(page, 'cancel-button').focus();

    server.current = snapshot({ other: 'Swapped', keys: ['other', 'resume'] });
    expect(await handleRefresh(page)).toBe('deferred');

    await expect(testId(page, 'other')).toHaveText('Swapped');
    expect(await connected(cancel!)).toBe(true);
    await expect(testId(page, 'cancel-button')).toBeFocused();
    await expect(testId(page, 'resume-form')).toHaveCount(0);
    expect(await etagRetained(page)).toBe(etagOf(server.current));

    await page.evaluate(() => (document.activeElement as HTMLElement).blur());
    expect(await handleFlush(page)).toEqual({ accepted: true, deferred: false });

    expect(await connected(cancel!)).toBe(false);
    expect(await regionKeys(page)).toEqual(['other', 'job-action:resume']);
  });

  test('a held disposable form the server dropped stays until it is released', async ({ page }) => {
    const server = await openLive(page);
    await showActions(page, server, 'resume');
    const resume = await testId(page, 'resume-form').elementHandle();
    await testId(page, 'resume-button').focus();

    server.current = snapshot({ keys: ['other'] });
    expect(await handleRefresh(page)).toBe('deferred');
    expect(await connected(resume!)).toBe(true);
    await expect(testId(page, 'resume-button')).toBeFocused();

    await page.evaluate(() => (document.activeElement as HTMLElement).blur());
    expect(await handleFlush(page)).toEqual({ accepted: true, deferred: false });
    expect(await connected(resume!)).toBe(false);
  });

  test('an idle disposable form the server dropped is removed at once', async ({ page }) => {
    const server = await openLive(page);
    await showActions(page, server, 'cancel');
    const cancel = await testId(page, 'cancel-form').elementHandle();

    server.current = snapshot({ keys: ['other'] });
    expect(await handleRefresh(page)).toBe('applied');

    expect(await connected(cancel!)).toBe(false);
    expect(await regionKeys(page)).toEqual(['other']);
  });

  test('a form without data-live-disposable is never discarded by a passive snapshot', async ({ page }) => {
    const server = await openLive(page);
    await showActions(page, server, 'plain-a');
    const form = await testId(page, 'plain-a').elementHandle();

    server.current = snapshot({ keys: ['other'] });
    expect(await handleRefresh(page)).toBe('deferred');

    expect(await connected(form!)).toBe(true);
    expect(await etagRetained(page)).toBe(etagOf(server.current));
  });

  test('a different owned form is reported as deferred instead of being swallowed', async ({ page }) => {
    const server = await openLive(page);
    await showActions(page, server, 'plain-a');
    const form = await testId(page, 'plain-a').elementHandle();

    server.current = snapshot({ keys: ['other', 'plain-b'] });
    expect(await handleRefresh(page)).toBe('deferred');

    expect(await connected(form!)).toBe(true);
    await expect(testId(page, 'plain-b')).toHaveCount(0);
    expect((await page.evaluate(() => window.__applied)).at(-1)).toEqual({ accepted: true, deferred: true });
  });

  test('a different unkeyed owned form is reported as deferred instead of being swallowed', async ({ page }) => {
    const server = await openLive(page);
    await showActions(page, server, 'unkeyed-a');
    const form = await testId(page, 'unkeyed-a').elementHandle();

    server.current = snapshot({ keys: ['other', 'unkeyed-b'] });
    expect(await handleRefresh(page)).toBe('deferred');

    expect(await connected(form!)).toBe(true);
    await expect(testId(page, 'unkeyed-a')).toHaveAttribute('action', '/a');
    expect((await page.evaluate(() => window.__applied)).at(-1)).toEqual({ accepted: true, deferred: true });
  });

  test('the same unkeyed owned form is kept and the snapshot is applied', async ({ page }) => {
    const server = await openLive(page);
    await showActions(page, server, 'unkeyed-a');
    const form = await testId(page, 'unkeyed-a').elementHandle();

    server.current = snapshot({ other: 'Same form', keys: ['other', 'unkeyed-a'] });
    expect(await handleRefresh(page)).toBe('applied');

    expect(await connected(form!)).toBe(true);
    await expect(testId(page, 'other')).toHaveText('Same form');
  });
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
    await expect(page.locator(hostSelector)).toHaveAttribute('data-live-removed', '');
    await expect(page.locator(`${hostSelector} > [data-live-removed-notice][role="status"]`)).toContainText('no longer available');

    // The controller disposes the owned content; only then may the item go.
    await kept!.evaluate((node) => node.remove());
    expect(await handleFlush(page)).toEqual({ accepted: true, deferred: false });
    expect(await host!.evaluate((node) => node.isConnected)).toBe(false);
  });
}

test.describe('retained removed items are reported', () => {
  const WITHOUT_OWNED = ALL_KEYS.filter((key) => key !== 'owned' && key !== 'tail');
  const host = (page: Page) => page.locator('[data-live-key="owned-host"]');
  const notice = (page: Page) => page.locator('[data-live-key="owned-host"] [data-live-removed-notice]');

  test('a clean removed item keeps its notice once, and it clears when the item is listed again', async ({ page }) => {
    const server = await openLive(page);
    const original = await host(page).elementHandle();
    server.current = snapshot({ keys: WITHOUT_OWNED });
    expect(await handleRefresh(page)).toBe('deferred');
    server.current = snapshot({ other: 'Again', keys: WITHOUT_OWNED });
    expect(await handleRefresh(page)).toBe('deferred');

    await expect(notice(page)).toHaveCount(1);
    await expect(notice(page)).toHaveAttribute('role', 'status');

    server.current = snapshot({ other: 'Back' });
    expect(await handleRefresh(page)).toBe('applied');
    expect(await connected(original!)).toBe(true);
    await expect(host(page)).not.toHaveAttribute('data-live-removed', /.*/);
    await expect(notice(page)).toHaveCount(0);
    await expect(page.locator('[data-live-key="tail"]')).toHaveCount(1);
  });

  test('a removed item with a dirty form keeps the draft and reports the removal', async ({ page }) => {
    const server = await openLive(page);
    await page.locator('#owned-name').fill('typed draft');
    await page.evaluate(() => (document.activeElement as HTMLElement).blur());
    server.current = snapshot({ other: 'Remote change', keys: WITHOUT_OWNED });

    expect(await handleRefresh(page)).toBe('deferred');

    await expect(page.locator('#owned-name')).toHaveValue('typed draft');
    await expect(page.getByTestId('other')).toHaveText('Remote change');
    await expect(host(page)).toHaveAttribute('data-live-removed', '');
    await expect(notice(page)).toContainText('no longer available');

    server.current = snapshot({ other: 'Back' });
    expect(await handleRefresh(page)).toBe('applied');
    await expect(notice(page)).toHaveCount(0);
    await expect(page.locator('#owned-name')).toHaveValue('typed draft');
  });

  test('an item removed only for the focus hold is reported until it goes', async ({ page }) => {
    const server = await openLive(page);
    await select(page).focus();
    server.current = snapshot({ keys: ['other', 'tail', 'dialog', 'owned'] });

    expect(await handleRefresh(page)).toBe('deferred');
    await expect(heldItem(page)).toHaveAttribute('data-live-removed', '');

    await page.evaluate(() => (document.activeElement as HTMLElement).blur());
    expect(await handleFlush(page)).toEqual({ accepted: true, deferred: false });
    await expect(heldItem(page)).toHaveCount(0);
  });
});

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

test.describe('region roots', () => {
  const rootSelect = (page: Page) => page.locator('#root-select');
  const optionLabels = (page: Page) => rootSelect(page).evaluate(
    (element: HTMLSelectElement) => Array.from(element.options).map((option) => option.textContent),
  );
  const RENAMED = ['Alpha renamed', 'Beta renamed', 'Gamma'];

  test('a focused root select keeps its node, value and options while a sibling region updates', async ({ page }) => {
    const server = await openLive(page, { roots: true });
    await rootSelect(page).focus();
    await rootSelect(page).selectOption('b');
    const original = await rootSelect(page).elementHandle();

    server.current = rootSnapshot({ other: 'Sibling updated' }, { options: RENAMED });
    expect(await handleRefresh(page)).toBe('deferred');

    await expect(page.getByTestId('other')).toHaveText('Sibling updated');
    expect(await optionLabels(page)).toEqual(['Alpha', 'Beta']);
    expect(await rootSelect(page).inputValue()).toBe('b');
    expect(await original!.evaluate((node) => node.isConnected && node === document.activeElement)).toBe(true);

    await rootSelect(page).selectOption('a');
    await page.evaluate(() => (document.activeElement as HTMLElement).blur());
    expect(await handleFlush(page)).toEqual({ accepted: true, deferred: false });
    expect(await optionLabels(page)).toEqual(RENAMED);
    expect(await original!.evaluate((node) => node.isConnected)).toBe(true);
  });

  test('a focused root select that was not changed still keeps its options until released', async ({ page }) => {
    const server = await openLive(page, { roots: true });
    await rootSelect(page).focus();
    server.current = rootSnapshot({}, { options: RENAMED });

    expect(await handleRefresh(page)).toBe('deferred');
    expect(await optionLabels(page)).toEqual(['Alpha', 'Beta']);

    await page.evaluate(() => (document.activeElement as HTMLElement).blur());
    expect(await handleFlush(page)).toEqual({ accepted: true, deferred: false });
    expect(await optionLabels(page)).toEqual(RENAMED);
  });

  test('an idle root select is updated in place', async ({ page }) => {
    const server = await openLive(page, { roots: true });
    server.current = rootSnapshot({}, { options: RENAMED });

    expect(await handleRefresh(page)).toBe('applied');
    expect(await optionLabels(page)).toEqual(RENAMED);
  });

  test('releasing a root select applies the deferred target without another network read', async ({ page }) => {
    const server = await openLive(page, { roots: true });
    await rootSelect(page).focus();
    server.current = rootSnapshot({}, { options: RENAMED });
    expect(await handleRefresh(page)).toBe('deferred');
    const requests = server.requests.length;

    await page.evaluate(() => (document.activeElement as HTMLElement).blur());
    await page.clock.runFor(10);

    await expect.poll(() => optionLabels(page)).toEqual(RENAMED);
    expect(server.requests.length).toBe(requests);
  });

  test('selected direct text of a root is kept while its sibling elements update, then applied on release', async ({ page }) => {
    const server = await openLive(page, { roots: true });
    await page.locator('#root-text').evaluate((element) => {
      const range = document.createRange();
      range.selectNodeContents(element.firstChild!);
      const selection = window.getSelection()!;
      selection.removeAllRanges();
      selection.addRange(range);
    });
    server.current = rootSnapshot({}, { text: 'Changed direct text', sibling: 'Sibling changed' });

    expect(await handleRefresh(page)).toBe('deferred');

    await expect(page.getByTestId('root-sibling')).toHaveText('Sibling changed');
    expect(await page.locator('#root-text').evaluate((element) => element.firstChild!.nodeValue)).toBe('Root direct text');
    expect(await page.evaluate(() => window.getSelection()!.toString())).toBe('Root direct text');

    await page.evaluate(() => window.getSelection()!.removeAllRanges());
    expect(await handleFlush(page)).toEqual({ accepted: true, deferred: false });
    expect(await page.locator('#root-text').evaluate((element) => element.firstChild!.nodeValue)).toBe('Changed direct text');
  });

  test('unselected direct text of a root updates in the same pass', async ({ page }) => {
    const server = await openLive(page, { roots: true });
    server.current = rootSnapshot({}, { text: 'Changed direct text' });

    expect(await handleRefresh(page)).toBe('applied');
    expect(await page.locator('#root-text').evaluate((element) => element.firstChild!.nodeValue)).toBe('Changed direct text');
  });
});

test.describe('replacement of an item whose removal ownership blocks', () => {
  const count = (page: Page, selector: string) => page.locator(selector).count();

  test('an unkeyed wrapper replaced around a clean form is deferred, never duplicated', async ({ page }) => {
    const server = await openLive(page);
    await showActions(page, server, 'wrap-div');
    const original = await testId(page, 'wrapped-form').elementHandle();

    server.current = snapshot({ other: 'Wrapper changed', keys: ['other', 'wrap-section'] });
    expect(await handleRefresh(page)).toBe('deferred');

    await expect(testId(page, 'other')).toHaveText('Wrapper changed');
    expect(await count(page, '#page-region form')).toBe(1);
    expect(await count(page, '#wrapped-field')).toBe(1);
    expect(await count(page, '[data-testid="wrap-new"]')).toBe(0);
    expect(await connected(original!)).toBe(true);

    for (let flush = 0; flush < 2; flush += 1) {
      expect(await handleFlush(page)).toEqual({ accepted: true, deferred: true });
      expect(await count(page, '#page-region form')).toBe(1);
      expect(await count(page, '#wrapped-field')).toBe(1);
    }

    await page.evaluate(() => document.querySelector('[data-testid="wrap-old"]')!.remove());
    expect(await handleFlush(page)).toEqual({ accepted: true, deferred: false });
    expect(await count(page, '#page-region form')).toBe(1);
    expect(await count(page, '[data-testid="wrap-new"] #wrapped-field')).toBe(1);
  });

  test('an unkeyed wrapper replaced around a dirty form keeps the typed text and one set of ids', async ({ page }) => {
    const server = await openLive(page);
    await showActions(page, server, 'wrap-div');
    await page.locator('#wrapped-field').fill('typed draft');
    await page.evaluate(() => (document.activeElement as HTMLElement).blur());
    const original = await page.locator('#wrapped-field').elementHandle();

    server.current = snapshot({ keys: ['other', 'wrap-section'] });
    expect(await handleRefresh(page)).toBe('deferred');
    expect(await handleFlush(page)).toEqual({ accepted: true, deferred: true });

    expect(await count(page, '#wrapped-field')).toBe(1);
    expect(await count(page, '#page-region form')).toBe(1);
    expect(await original!.evaluate((node: HTMLInputElement) => node.isConnected && node.value)).toBe('typed draft');
  });
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

const rawRefresh = (page: Page) => page.evaluate(() => window.FlowgencyLive.handles.get('raw')!.refresh());
const rawEtag = (page: Page) => page.evaluate(() => window.FlowgencyLive.handles.get('raw')!.etag);

test('a validating adapter receives its own payload shape and decides compatibility', async ({ page }) => {
  const server = await openLive(page);
  await page.evaluate(() => window.__mountRaw());

  server.script.push({ body: { board: 'one' }, etag: '"raw-1"' });
  expect(await rawRefresh(page)).toBe('applied');
  expect(await page.evaluate(() => window.__raw.applied)).toEqual([{ board: 'one' }]);
  expect(await rawEtag(page)).toBe('"raw-1"');

  server.script.push({ body: { incompatible: true }, etag: '"raw-2"' });
  expect(await rawRefresh(page)).toBe('incompatible');
  server.script.push({ body: ['not', 'an', 'object'], etag: '"raw-3"' });
  expect(await rawRefresh(page)).toBe('failed');

  expect(await page.evaluate(() => window.__raw.applied)).toEqual([{ board: 'one' }]);
  expect(await rawEtag(page)).toBe('"raw-1"');
  expect(await page.evaluate(() => window.__raw.statuses)).toEqual(['healthy', 'incompatible', 'stale']);
});

test('an apply result that reports the target unavailable sets the unavailable status and keeps no ETag', async ({ page }) => {
  const server = await openLive(page);
  await page.evaluate(() => window.__mountRaw());

  server.script.push({ body: { unavailable: true }, etag: '"raw-gone"' });
  expect(await rawRefresh(page)).toBe('unavailable');
  expect(await rawEtag(page)).toBeNull();
  expect(await page.evaluate(() => window.FlowgencyLive.handles.get('raw')!.status)).toBe('unavailable');

  server.script.push({ body: { board: 'back' }, etag: '"raw-back"' });
  expect(await rawRefresh(page)).toBe('applied');
  expect(await page.evaluate(() => window.__raw.statuses)).toEqual(['unavailable', 'healthy']);
});

for (const [label, reply, outcome] of [
  ['unavailable', { unavailable: true }, 'unavailable'],
  ['not accepted', { rejected: true }, 'rejected'],
  ['incompatible', { applyIncompatible: true }, 'incompatible'],
  ['a throwing apply', { throws: true }, 'failed'],
] as const) {
  test(`an apply that ran and reports ${label} clears the retained ETag so the next read is a full 200`, async ({ page }) => {
    const server = await openLive(page);
    await page.evaluate(() => window.__mountRaw());
    server.current = { board: 'one' } as unknown as LiveSnapshotShape;
    const board = etagOf(server.current);

    expect(await rawRefresh(page)).toBe('applied');
    expect(await rawEtag(page)).toBe(board);

    server.script.push({ body: reply, etag: '"raw-other"' });
    expect(await rawRefresh(page)).toBe(outcome);
    expect(await rawEtag(page)).toBeNull();

    expect(await rawRefresh(page)).toBe('applied');
    expect(server.requests.map((request) => request.ifNoneMatch)).toEqual([undefined, board, undefined]);
    expect(await page.evaluate(() => window.__raw.applied)).toHaveLength(3);
    expect(await rawEtag(page)).toBe(board);
  });
}

test('a rejection that vouches the view was untouched keeps the retained ETag', async ({ page }) => {
  const server = await openLive(page);
  await page.evaluate(() => window.__mountRaw());
  server.current = { board: 'one' } as unknown as LiveSnapshotShape;
  const board = etagOf(server.current);

  expect(await rawRefresh(page)).toBe('applied');
  server.script.push({ body: { untouched: true }, etag: '"raw-untouched"' });
  expect(await rawRefresh(page)).toBe('rejected');
  expect(await rawEtag(page)).toBe(board);
  expect(await rawRefresh(page)).toBe('not-modified');
});

test('an accepted deferred apply keeps its ETag so an unchanged snapshot stays a 304', async ({ page }) => {
  const server = await openLive(page);
  await page.evaluate(() => window.__mountRaw());
  server.current = { board: 'one', deferred: true } as unknown as LiveSnapshotShape;
  const board = etagOf(server.current);

  expect(await rawRefresh(page)).toBe('deferred');
  expect(await rawEtag(page)).toBe(board);
  expect(await rawRefresh(page)).toBe('not-modified');
  expect(server.requests.map((request) => request.ifNoneMatch)).toEqual([undefined, board]);
});

test('settled runs after every read that finishes while visible, not for superseded or hidden reads', async ({ page }) => {
  const server = await openLive(page);
  await page.evaluate(() => window.__mountRaw());
  const settled = () => page.evaluate(() => window.__raw.settled);

  server.script.push({ body: { board: 'one' }, etag: '"raw-1"' });
  await rawRefresh(page);
  expect(await settled()).toBe(1);
  await rawRefresh(page);
  expect(await settled()).toBe(2);
  server.script.push({ status: 500, body: 'boom' });
  await rawRefresh(page);
  expect(await settled()).toBe(3);

  const slow = deferred();
  server.script.push({ body: { board: 'slow' }, gate: slow.promise });
  await page.evaluate(() => { window.__pending = window.FlowgencyLive.handles.get('raw')!.refresh(); });
  await expect.poll(() => server.requests.length).toBe(4);
  server.script.push({ body: { board: 'fast' }, etag: '"raw-fast"' });
  await rawRefresh(page);
  slow.release();
  expect(await page.evaluate(() => window.__pending)).toBe('superseded');
  expect(await settled()).toBe(4);

  const gate = deferred();
  server.script.push({ body: { board: 'hidden' }, gate: gate.promise });
  await page.evaluate(() => { window.__pending = window.FlowgencyLive.handles.get('raw')!.refresh(); });
  await setVisibility(page, 'hidden');
  gate.release();
  await page.evaluate(() => window.__pending);
  expect(await settled()).toBe(4);
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
const NON_LIVE_PAGE = '/__ui/non-live-page';

async function openRoster(page: Page): Promise<void> {
  await page.clock.install({ time: 0 });
  await page.goto('/newsletter/agents');
}

test('a page without live regions registers nothing and sends no periodic request', async ({ page }) => {
  const live: string[] = [];
  page.on('request', (request) => { if (request.url().includes('__live=1')) live.push(request.url()); });
  await page.clock.install({ time: 0 });
  // A purpose-built fixture page: every product page is live or retired, so none is a stable example.
  const response = await page.goto(NON_LIVE_PAGE);
  expect(response?.status()).toBe(200);
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

test('a server outage still renders a refresh glyph with no lucide fetch', async ({ page }) => {
  const lucideRequests: string[] = [];
  await page.route('**/lucide.min.js', (route) => {
    lucideRequests.push(route.request().url());
    return route.abort();
  });
  await openRoster(page);
  await page.clock.pauseAt(60_000);
  await expect.poll(() => page.evaluate(() => window.FlowgencyLive.handles.get('page')!.status)).toBe('healthy');

  await page.route(ROSTER_SNAPSHOT, (route) => route.fulfill({ status: 500, body: 'down' }));
  await page.clock.runFor(2000);

  const shell = page.locator('[data-live-status]');
  await expect(shell).toBeVisible();
  await expect(shell).toHaveAttribute('data-live-status', 'stale');
  const icon = shell.locator('[data-live-manual-refresh] svg');
  await expect(icon).toHaveCount(1);
  const box = await icon.boundingBox();
  expect(box?.width).toBeGreaterThan(0);
  expect(box?.height).toBeGreaterThan(0);
  expect(lucideRequests).toEqual([]);
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


// ── App-wide coverage matrix and cross-page behaviour ───────────────────────
// The inventory lives in live_coverage.json, which tests/test_live_refresh.py and
// tests/test_ui_fixture_server.py check against the app's routes and policies.

type CoverageEntry = {
  id: string;
  kind: string;
  route: string;
  url: string | null;
  snapshot?: string;
  handle?: string;
  fixture?: string;
  shell: 'team' | 'admin' | 'none';
  regions: string[];
  covered_by?: string;
  change?: { case: string; region: string; setup?: string[] };
};

const COVERAGE = JSON.parse(readFileSync(path.join(__dirname, 'live_coverage.json'), 'utf8')) as {
  navigation: Record<string, string[]>;
  entries: CoverageEntry[];
};
const REGION_ENTRIES = COVERAGE.entries.filter((entry) => ['snapshot', 'shell-only'].includes(entry.kind));
const WORKFLOW_ENTRIES = COVERAGE.entries.filter((entry) => entry.kind === 'workflow-snapshot');
const NON_BROWSER_ENTRIES = COVERAGE.entries.filter((entry) => entry.kind === 'lifecycle' || entry.covered_by);
const TAIL_LOG = 'advisor-live-tail.out';
const GIT_EVIDENCE_INDEX = path.join(__dirname, '.runtime', 'current', 'fixture-index', 'git-evidence.json');
const RESET_TIMEOUT_MS = 120_000;

async function resetFixture(request: APIRequestContext, fixture = 'default'): Promise<void> {
  expect((await request.post('/__ui/reset', { data: { fixture }, timeout: RESET_TIMEOUT_MS })).status()).toBe(204);
}

async function applyChange(request: APIRequestContext, name: string): Promise<void> {
  expect((await request.post('/__ui/live/change', { data: { case: name } })).status()).toBe(204);
}

const liveRegion = (page: Page, key: string) => page.locator(`[data-live-region="${key}"]`);
const handleKeys = (page: Page) => page.evaluate(() => Array.from(window.FlowgencyLive.handles.keys()));
const handleStatus = (page: Page, key = 'page') => page.evaluate((name) => window.FlowgencyLive.handles.get(name)!.status, key);
const refreshHandle = (page: Page, key = 'page') => page.evaluate((name) => window.FlowgencyLive.handles.get(name)!.refresh(), key);
const themeOf = (project: string): 'dark' | 'light' => (project.endsWith('dark') ? 'dark' : 'light');

async function extraPage(context: BrowserContext, project: string): Promise<Page> {
  const extra = await context.newPage();
  await installBasePageSetup(extra, themeOf(project));
  return extra;
}

async function coverageUrl(request: APIRequestContext, entry: CoverageEntry): Promise<string> {
  if (entry.url === '{tail-log-view}') {
    const listing = await (await request.get('/newsletter/logs')).text();
    const link = new RegExp(`href="(/newsletter/logs/view\\?[^"]*${TAIL_LOG}[^"]*)"`).exec(listing);
    if (!link) throw new Error('the tail log is not listed');
    return link[1].replace(/&amp;/g, '&');
  }
  if (entry.url === '{git-evidence-diff}') {
    const index = JSON.parse(readFileSync(GIT_EVIDENCE_INDEX, 'utf8'));
    return `${index.ticket_href}/artifacts/${index.current.artifact_id}/diff?source=ticket`;
  }
  return entry.url!;
}

// The move review page only ever exists as the result of a POST, so reach it the way a user does.
async function openCoverageEntry(page: Page, request: APIRequestContext, entry: CoverageEntry): Promise<void> {
  for (const setup of entry.change?.setup ?? []) await applyChange(request, setup);
  if (entry.id === 'agent-move') {
    await page.goto('/newsletter/agents');
    const form = page.locator('form[action$="/advisor/move"]');
    await form.locator('input[name="target_team"]').fill('research');
    await form.getByRole('button', { name: 'Move' }).click();
    await expect(page).toHaveURL('/newsletter/agents/advisor/move');
    return;
  }
  await page.goto(await coverageUrl(request, entry));
}

test.describe('coverage matrix', () => {
  test.beforeEach(async ({ request }) => {
    await resetFixture(request);
  });

  // Extra pages must be gone before the fixture is reset underneath them.
  test.afterEach(async ({ context, request }) => {
    for (const open of context.pages()) await open.close();
    await resetFixture(request);
  });

  for (const entry of REGION_ENTRIES) {
    test(`${entry.id} registers its regions and changes ${entry.change!.region} after ${entry.change!.case}`, async ({ page, request }) => {
      test.setTimeout(entry.fixture === 'git-evidence' ? 300_000 : 60_000);
      if (entry.fixture) await resetFixture(request, entry.fixture);
      await openCoverageEntry(page, request, entry);

      await expect.poll(() => handleKeys(page)).toEqual(['page']);
      const roots = await page.evaluate(
        () => Array.from(document.querySelectorAll('[data-live-region]'), (node) => node.getAttribute('data-live-region')),
      );
      expect(new Set(roots)).toEqual(new Set([...entry.regions, ...COVERAGE.navigation[entry.shell]]));

      const changing = liveRegion(page, entry.change!.region);
      const before = await changing.innerHTML();
      await applyChange(request, entry.change!.case);
      await refreshHandle(page);

      await expect.poll(() => changing.innerHTML()).not.toBe(before);
      await expect.poll(() => handleStatus(page)).toBe('healthy');
      await assertNoConsoleErrors(page);
    });
  }

  for (const entry of WORKFLOW_ENTRIES) {
    test(`${entry.id} changes ${entry.change!.region} after ${entry.change!.case} through the workflow transport`, async ({ page, request }) => {
      await page.goto(entry.url!);
      await expect.poll(() => handleKeys(page)).toEqual(['workflow']);
      const read = () => page.evaluate(
        async (url) => (await fetch(url, { cache: 'no-store' })).json() as Promise<Record<string, unknown>>,
        entry.snapshot!,
      );
      const before = await read();
      for (const key of entry.regions) expect(before).toHaveProperty(key);

      await applyChange(request, entry.change!.case);
      await refreshHandle(page, 'workflow');

      const after = await read();
      expect(after[entry.change!.region]).not.toEqual(before[entry.change!.region]);
      await expect.poll(() => handleStatus(page, 'workflow')).toBe('healthy');
      if (entry.id === 'workflow-board') {
        await expect(page.locator('[data-ticket-id="fixture-live-count"]')).toHaveCount(1);
      }
      await assertNoConsoleErrors(page);
    });
  }

  test('rows without a region adapter name the suite that exercises them', async () => {
    expect(NON_BROWSER_ENTRIES.map((entry) => entry.id)).toEqual(['setup', 'setup-session']);
    for (const entry of NON_BROWSER_ENTRIES) {
      const source = readFileSync(path.join(__dirname, '..', '..', entry.covered_by!), 'utf8');
      expect(source).toContain(entry.id === 'setup' ? entry.handle! : '/setup/session');
    }
  });
});

test.describe('cross-page behaviour', () => {
  test.beforeEach(async ({ request }) => {
    await resetFixture(request);
  });

  test.afterEach(async ({ context, request }) => {
    for (const open of context.pages()) await open.close();
    await resetFixture(request);
  });

  test('one remote change updates two open pages without reloading either', async ({ page, context, request }, testInfo) => {
    const second = await extraPage(context, testInfo.project.name);
    const navigations: string[] = [];
    for (const open of [page, second]) {
      open.on('framenavigated', (frame) => { if (frame === open.mainFrame()) navigations.push(frame.url()); });
    }
    await page.goto('/newsletter/');
    await second.goto('/newsletter/jobs');
    await expect.poll(() => handleKeys(second)).toEqual(['page']);
    navigations.length = 0;
    const advisor = page.locator('[data-live-key="agent:advisor"]');
    const queue = await liveRegion(second, 'jobs-list').innerHTML();

    await applyChange(request, 'inbox-routine-pending');
    await expect(advisor).toHaveAttribute('data-health-kind', 'overdue');
    await expect.poll(() => liveRegion(second, 'jobs-list').innerHTML()).not.toBe(queue);

    await applyChange(request, 'inbox-job-completes');
    await expect(advisor).toHaveAttribute('data-health-kind', 'healthy');
    await expect(liveRegion(page, 'fleet')).toContainText('healthy');
    await expect(liveRegion(second, 'jobs-list')).toContainText(/\bComplete\b/);
    expect(navigations).toEqual([]);
    await assertNoConsoleErrors(page);
    await assertNoConsoleErrors(second);
  });

  test('hiding the document pauses reads and showing it catches up immediately', async ({ page, request }) => {
    // Playwright's Chromium never reports a hidden tab, so the browser's own visibilitychange is dispatched instead.
    const reads: string[] = [];
    page.on('request', (seen) => { if (seen.url().includes('__live=1')) reads.push(seen.url()); });
    await page.goto('/newsletter/jobs');
    await expect.poll(() => reads.length).toBeGreaterThan(0);
    const listing = liveRegion(page, 'jobs-list');
    const before = await listing.innerHTML();

    await setVisibility(page, 'hidden');
    await page.waitForTimeout(300);
    const paused = reads.length;
    await applyChange(request, 'job-added');
    await page.waitForTimeout(4500);
    expect(reads.length).toBe(paused);
    expect(await listing.innerHTML()).toBe(before);

    await setVisibility(page, 'visible');
    await expect.poll(() => reads.length, { timeout: 1500 }).toBeGreaterThan(paused);
    await expect.poll(() => listing.innerHTML()).not.toBe(before);
    await assertNoConsoleErrors(page);
  });

  test('a delayed older reply is superseded by a newer read and never overwrites it', async ({ page, request }) => {
    await page.goto('/newsletter/');
    await expect.poll(() => handleStatus(page)).toBe('healthy');
    const gate = deferred();
    let held = 0;
    await page.route(/\/newsletter\/\?__live=1$/, async (route) => {
      held += 1;
      if (held > 1) return route.continue();
      const stale = await route.fetch();
      await gate.promise;
      try { await route.fulfill({ response: stale }); } catch { /* superseded: the page already aborted it */ }
    });
    const advisor = page.locator('[data-live-key="agent:advisor"]');
    const unchanged = await advisor.getAttribute('data-health-kind');
    const first = page.evaluate(() => window.FlowgencyLive.handles.get('page')!.refresh());
    await expect.poll(() => held).toBe(1);

    await applyChange(request, 'inbox-routine-pending');
    const newer = await refreshHandle(page);
    gate.release();

    expect(await first).toBe('superseded');
    expect(newer).toBe('applied');
    await expect(advisor).toHaveAttribute('data-health-kind', 'overdue');
    expect(unchanged).not.toBe('overdue');
    await page.waitForTimeout(500);
    await expect(advisor).toHaveAttribute('data-health-kind', 'overdue');
  });

  test('a reply whose body finishes after the document hides is discarded and applied on return', async ({ page, request }) => {
    await page.addInitScript(() => {
      const original = Response.prototype.text;
      Response.prototype.text = async function () {
        const body = await original.call(this);
        const gate = (window as unknown as { __bodyGate?: Promise<void> }).__bodyGate;
        if (gate) await gate;
        return body;
      };
    });
    await page.goto('/newsletter/');
    await expect.poll(() => handleStatus(page)).toBe('healthy');
    const advisor = page.locator('[data-live-key="agent:advisor"]');
    const unchanged = await advisor.getAttribute('data-health-kind');
    await page.evaluate(() => {
      const holder = window as unknown as { __bodyGate: Promise<void>; __releaseBody: () => void };
      holder.__bodyGate = new Promise<void>((resolve) => { holder.__releaseBody = resolve; });
    });
    await applyChange(request, 'inbox-routine-pending');
    const reply = page.waitForResponse((response) => response.url().endsWith('/newsletter/?__live=1'));
    const pending = page.evaluate(() => window.FlowgencyLive.handles.get('page')!.refresh());
    await reply;

    await setVisibility(page, 'hidden');
    await page.evaluate(() => {
      const holder = window as unknown as { __bodyGate: Promise<void> | null; __releaseBody: () => void };
      holder.__releaseBody();
      holder.__bodyGate = null;
    });
    expect(['discarded', 'hidden', 'paused']).toContain(await pending);
    await expect(advisor).toHaveAttribute('data-health-kind', unchanged!);

    await setVisibility(page, 'visible');
    await expect(advisor).toHaveAttribute('data-health-kind', 'overdue');
  });

  test('a page removed from under the reader reports unavailable and keeps its last good content', async ({ page, request }) => {
    const errors: string[] = [];
    page.on('console', (message) => { if (message.type() === 'error') errors.push(message.text()); });
    await applyChange(request, 'log-tall');
    await page.goto(await coverageUrl(request, { url: '{tail-log-view}' } as CoverageEntry));
    await expect.poll(() => handleStatus(page)).toBe('healthy');
    const content = liveRegion(page, 'log-content');
    const kept = await content.innerHTML();

    await applyChange(request, 'log-removed');
    await refreshHandle(page);

    await expect.poll(() => handleStatus(page)).toBe('unavailable');
    await expect(page.locator('[data-live-status]')).toBeVisible();
    expect(await content.innerHTML()).toBe(kept);
    // The browser reports the missing resource itself; nothing else may be logged.
    expect(errors.length).toBeGreaterThan(0);
    for (const text of errors) expect(text).toMatch(/Failed to load resource: .* 404/);
  });

  test('a save made after a remote change is rejected by its loaded revision and keeps the draft', async ({ page, request }) => {
    await page.goto('/newsletter/agents/advisor/runtime');
    const timeout = page.locator('input[name="timeout"]');
    const revision = page.locator('input[name="revision"]').first();
    const loaded = await revision.inputValue();
    await timeout.fill('1801');

    await applyChange(request, 'agent-team-runtime');
    await refreshHandle(page);
    await expect(timeout).toHaveValue('1801');
    await expect(revision).toHaveValue(loaded);

    const save = page.waitForResponse((response) => response.url().includes('/advisor/runtime') && response.request().method() === 'POST');
    await page.getByRole('button', { name: 'Save runtime', exact: true }).click();
    expect((await save).status()).toBe(409);
    await expect(page.getByText('config.yaml changed; reload before saving', { exact: true })).toBeVisible();
    await expect(timeout).toHaveValue('1801');
    await assertOnlyExpectedConflictConsoleError(page);
  });

  test('a relative-time-only change updates labels in place and nothing else', async ({ page, request }) => {
    await page.goto('/newsletter/');
    await expect.poll(() => handleStatus(page)).toBe('healthy');
    const keys = ['fleet', 'workflows', 'work-queue', 'attention', 'activity'];
    const snapshotOf = async () => Object.fromEntries(
      await Promise.all(keys.map(async (key) => [key, await liveRegion(page, key).innerHTML()])),
    ) as Record<string, string>;
    const before = await snapshotOf();
    const item = await liveRegion(page, 'activity').locator('[data-live-key]').first().elementHandle();

    await applyChange(request, 'inbox-clock-advances');
    await refreshHandle(page);

    await expect.poll(async () => (await snapshotOf()).activity).not.toBe(before.activity);
    const after = await snapshotOf();
    for (const key of keys.filter((name) => name !== 'activity')) expect(after[key]).toBe(before[key]);
    expect(await item!.evaluate((node) => node.isConnected)).toBe(true);
  });

  test('an executable fragment is rejected before it touches the page or the ETag', async ({ page }) => {
    await page.goto('/newsletter/');
    await expect.poll(() => handleStatus(page)).toBe('healthy');
    const accepted = await page.evaluate(() => window.FlowgencyLive.handles.get('page')!.etag);
    const fleet = liveRegion(page, 'fleet');
    const before = await fleet.innerHTML();

    await page.route(/\/newsletter\/\?__live=1$/, async (route) => {
      const { 'if-none-match': _conditional, ...headers } = route.request().headers();
      const response = await route.fetch({ headers });
      const body = await response.json() as LiveSnapshotShape;
      body.regions = body.regions.map((entry) => (
        entry.key === 'fleet' ? { ...entry, html: '<img src="x" onerror="window.__pwned = true">' } : entry
      ));
      await route.fulfill({ response, json: body, headers: { ...response.headers(), etag: '"poisoned"' } });
    });
    await refreshHandle(page);

    expect(await fleet.innerHTML()).toBe(before);
    expect(await page.evaluate(() => window.__pwned)).toBeUndefined();
    expect(await handleStatus(page)).not.toBe('healthy');
    expect(await page.evaluate(() => window.FlowgencyLive.handles.get('page')!.etag)).toBe(accepted);
  });

  test('setup credentials never appear in an Inbox snapshot, for the owner or anyone else', async ({ page, context, request }) => {
    await resetFixture(request, 'connected-setup');
    const meta = await (await request.get('/__ui/setup/meta')).json();
    await page.goto('/setup', { waitUntil: 'domcontentloaded' });
    await page.getByLabel('Flowgency data root', { exact: true }).fill(meta.data_root as string);
    await page.getByRole('button', { name: 'Continue in GitHub Copilot' }).click();
    await expect(page).toHaveURL(/\/setup\/session$/);
    expect((await request.post('/__ui/setup/ready')).status()).toBe(204);
    expect((await request.post('/__ui/setup/session/complete', { data: { scheduler_result: 'confirmed' } })).status()).toBe(200);
    await expect(page).toHaveURL(/\/newsletter\/$/);

    const credential = (await context.cookies()).find((cookie) => cookie.name === 'flowgency_setup')!.value;
    const token = await page.locator('#setup-stop-form input[name="setup_csrf"]').inputValue();
    expect(credential).not.toBe('');
    expect(token).not.toBe('');

    const owner = await page.evaluate(() => fetch('/newsletter/?__live=1', { cache: 'no-store' }).then((reply) => reply.text()));
    expect(owner).toContain('/setup/session?view=inspection');
    for (const secret of [credential, token, 'setup_csrf']) expect(owner).not.toContain(secret);

    const visitor = await request.get('/newsletter/?__live=1');
    const text = await visitor.text();
    const regions = Object.fromEntries((JSON.parse(text) as LiveSnapshotShape).regions.map((entry) => [entry.key, entry.html]));
    expect(regions['setup-session'].trim()).toBe('');
    for (const secret of [credential, token, 'setup_csrf', '/setup/session']) expect(text).not.toContain(secret);
    expect(await (await request.get('/newsletter/')).text()).not.toContain(token);
  });

  test('live reports release their observers and a navigation leaves no request behind', async ({ page, request }) => {
    await page.addInitScript(() => {
      const holder = window as unknown as { __observers: Set<object> };
      holder.__observers = new Set();
      const Native = window.ResizeObserver;
      window.ResizeObserver = class extends Native {
        constructor(callback: ResizeObserverCallback) {
          super(callback);
          holder.__observers.add(this);
        }
        disconnect() {
          holder.__observers.delete(this);
          super.disconnect();
        }
      };
    });
    const activityReads: string[] = [];
    page.on('request', (seen) => { if (seen.url().includes('/advisor/activity?__live=1')) activityReads.push(seen.url()); });
    const observers = () => page.evaluate(() => (window as unknown as { __observers: Set<object> }).__observers.size);

    await page.goto('/newsletter/agents/advisor/activity');
    await expect.poll(() => handleStatus(page)).toBe('healthy');
    const baseline = await observers();

    await applyChange(request, 'agent-report-history');
    await refreshHandle(page);
    await expect.poll(observers).toBeGreaterThan(baseline);

    await resetFixture(request);
    await refreshHandle(page);
    await expect.poll(observers).toBe(baseline);

    await page.goto('/newsletter/');
    await expect.poll(() => handleKeys(page)).toEqual(['page']);
    const settled = activityReads.length;
    await page.waitForTimeout(4500);
    expect(activityReads.length).toBe(settled);
    expect(await handleKeys(page)).toEqual(['page']);
    await assertNoConsoleErrors(page);
  });

  test('a focused native select keeps its node, focus, value and events while the roster updates', async ({ page, request }, testInfo) => {
    test.skip(testInfo.project.name.startsWith('mobile'), 'the browser-managed popup is a desktop interaction');
    await page.goto('/newsletter/agents');
    const select = page.locator('form[action$="/advisor/move"] select[name="memory_mode"]');
    const node = await select.elementHandle();
    await page.evaluate(() => {
      const target = document.querySelector('form[action$="/advisor/move"] select[name="memory_mode"]')!;
      const events: string[] = [];
      (window as unknown as { __selectEvents: string[] }).__selectEvents = events;
      for (const name of ['blur', 'focusout', 'change']) target.addEventListener(name, () => events.push(name));
    });
    // A real click opens the popup in a headed browser; headless Chromium cannot expose that open state, so the
    // assertions below are node identity, focus, value and the absence of blur/change during the refresh.
    await select.click();
    const value = await select.inputValue();
    const summary = await liveRegion(page, 'roster-summary').innerHTML();

    await applyChange(request, 'inbox-agent-added');
    await refreshHandle(page);
    await expect.poll(() => liveRegion(page, 'roster-summary').innerHTML()).not.toBe(summary);
    // The new row is a structural change beside the held row, so it waits for the release.
    await expect(liveRegion(page, 'roster-rows')).not.toContainText(/scribe/i);

    expect(await node!.evaluate((element) => element.isConnected && element === document.activeElement)).toBe(true);
    expect(await select.inputValue()).toBe(value);
    expect(await page.evaluate(() => (window as unknown as { __selectEvents: string[] }).__selectEvents)).toEqual([]);

    await page.keyboard.press('ArrowDown');
    await page.keyboard.press('Enter');
    expect(await select.inputValue()).not.toBe(value);
    expect(await page.evaluate(() => (window as unknown as { __selectEvents: string[] }).__selectEvents)).toEqual(['change']);

    await select.blur();
    await refreshHandle(page);
    // The chosen value is a draft, so the row stays protected and the new row keeps waiting.
    expect(await node!.evaluate((element) => element.isConnected)).toBe(true);
    await expect(liveRegion(page, 'roster-rows')).not.toContainText(/scribe/i);
  });
});
