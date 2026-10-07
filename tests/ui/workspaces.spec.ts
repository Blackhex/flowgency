import { expect, test, type APIRequestContext, type Page } from '@playwright/test';

import { assertNoConsoleErrors, assertNoLayoutIssues, installBasePageSetup } from './layout';

const WORKSPACES_FIXTURE = 'workspaces';
const SAVED_TEXT = '# Editorial notes\n\nSaved by the editor.\n';
const CHANGED_TEXT = '# Editorial notes\n\nChanged elsewhere by another process.\n';
const CHANGED_NOTICE = 'This file changed outside the editor. Reload to see the saved version.';
const INCOMPATIBLE = 'This page has changed. Refresh to load the current version.';
const UNAVAILABLE = 'This page is no longer available. Refresh to check.';

const bytes = (text: string) => Buffer.byteLength(text, 'utf8');

async function reset(request: APIRequestContext, fixture = 'default'): Promise<void> {
  expect((await request.post('/__ui/reset', { data: { fixture } })).status()).toBe(204);
}

async function change(request: APIRequestContext, name: string): Promise<void> {
  expect((await request.post('/__ui/live/change', { data: { case: name } })).status()).toBe(204);
}

const metadata = (page: Page) => page.locator('[data-live-region="workspace-metadata"]');
const notices = (page: Page) => page.locator('[data-live-notices]');
const pageStatus = (page: Page) => page.locator('[data-live-status][role="status"]');
const rows = (page: Page) => page.locator('[data-live-region="workspaces-list"] > [data-live-key^="workspace:"]');
const row = (page: Page, name: string) => rows(page).filter({ hasText: name });

async function snapshotUrl(request: APIRequestContext, path: string): Promise<string> {
  const html = await (await request.get(path)).text();
  const match = /<script type="application\/json" id="live-initial">(.*?)<\/script>/s.exec(html);
  expect(match).not.toBeNull();
  return JSON.parse(match![1]).url as string;
}

test.beforeEach(async ({ page, request }, testInfo) => {
  await reset(request, WORKSPACES_FIXTURE);
  await installBasePageSetup(page, testInfo.project.name.endsWith('dark') ? 'dark' : 'light');
});

test.afterEach(async ({ page, request }) => {
  await page.close();
  await reset(request);
});

test.describe('live workspace file page', () => {
  async function openEditor(page: Page, draft: string) {
    await page.goto('/newsletter/workspaces/0/file');
    await page.getByRole('button', { name: 'Edit', exact: true }).click();
    const editor = page.locator('textarea[name="content"]');
    await editor.fill(draft);
    return editor;
  }

  test('shows the file status and keeps the page free of any permanent banner', async ({ page }) => {
    await page.goto('/newsletter/workspaces/0/file');

    await expect(metadata(page)).toContainText(`${bytes(SAVED_TEXT)} bytes · Last changed`);
    await expect(notices(page)).toHaveCount(1);
    await expect(notices(page).locator('p')).toHaveCount(0);
    await expect(pageStatus(page)).toBeHidden();
    await assertNoLayoutIssues(page);
    await assertNoConsoleErrors(page);
  });

  test('an external change moves the metadata and notifies while the draft stays untouched', async ({ page, request }) => {
    const editor = await openEditor(page, 'Unsaved workspace configuration');
    const editorNode = await editor.elementHandle();
    await expect(metadata(page)).toContainText(`${bytes(SAVED_TEXT)} bytes`);

    await change(request, 'workspace-file-changes');

    await expect(metadata(page)).toContainText(`${bytes(CHANGED_TEXT)} bytes`);
    await expect(metadata(page)).toContainText('changed');
    await expect(notices(page)).toContainText(CHANGED_NOTICE);
    await expect(editor).toHaveValue('Unsaved workspace configuration');
    expect(await editorNode!.evaluate((node) => node.isConnected)).toBe(true);
    await expect(page.locator('#rendered')).toContainText('Saved by the editor.');
    await expect(page.locator('#rendered')).not.toContainText('Changed elsewhere');
    await assertNoConsoleErrors(page);

    await page.reload();

    await expect(notices(page).locator('p')).toHaveCount(0);
    await expect(page.locator('#rendered')).toContainText('Changed elsewhere by another process.');
  });

  test('a removed source reports the file missing and still never touches the draft', async ({ page, request }) => {
    const editor = await openEditor(page, 'Draft that must survive');

    await change(request, 'workspace-file-removed');

    await expect(metadata(page)).toContainText('File not found');
    await expect(notices(page)).toContainText(CHANGED_NOTICE);
    await expect(editor).toHaveValue('Draft that must survive');
    await expect(pageStatus(page)).toBeHidden();
  });

  test('a workspace that now occupies the position is reported incompatible and shows no foreign content', async ({ page, request }) => {
    const editor = await openEditor(page, 'Draft for the first workspace');

    await change(request, 'workspace-reordered');

    await expect(pageStatus(page)).toBeVisible();
    await expect(pageStatus(page)).toContainText(INCOMPATIBLE);
    await expect(editor).toHaveValue('Draft for the first workspace');
    await expect(page.locator('#rendered')).toContainText('Saved by the editor.');
    await expect(page.locator('body')).not.toContainText('tmux new-session');
    await expect(metadata(page)).toContainText('notes.md');
  });

  test('a removed workspace reports the page unavailable and keeps what was loaded', async ({ page, request }) => {
    await page.goto('/newsletter/workspaces/1/file');
    await expect(page.locator('#rendered')).toContainText('tmux new-session');

    await change(request, 'workspace-removed');

    await expect(pageStatus(page)).toBeVisible();
    await expect(pageStatus(page)).toContainText(UNAVAILABLE);
    await expect(page.locator('#rendered')).toContainText('tmux new-session');
  });

  test('snapshots refuse files outside the plugin allowlist and a malformed binding', async ({ request }) => {
    const url = await snapshotUrl(request, '/newsletter/workspaces/0/file');
    expect((await request.get(url)).status()).toBe(200);

    for (const foreign of ['/etc/passwd', 'C:\\Windows\\win.ini', '../../config.yaml']) {
      const tampered = url.replace(/path=[^&]*/, `path=${encodeURIComponent(foreign)}`);
      const response = await request.get(tampered);
      expect(response.status(), foreign).toBe(403);
      expect(await response.text()).not.toContain('root:');
    }
    const malformed = url.replace(/__live_identity=[^&]*/, '__live_identity=NOT-A-HASH');
    expect((await request.get(malformed)).status()).toBe(400);
    expect((await request.get(url.replace(/&?__live_identity=[^&]*/, ''))).status()).toBe(400);
  });

  test('a second allowed file keeps its own binding', async ({ page, request }) => {
    await page.goto('/newsletter/workspaces/1/file');
    await expect(metadata(page)).toContainText(`${bytes('#!/bin/bash\ntmux new-session -d -s newsletter\n')} bytes`);

    await change(request, 'workspace-file-changes');

    await expect(pageStatus(page)).toBeHidden();
    await expect(notices(page).locator('p')).toHaveCount(0);
  });
});

test.describe('live workspace list', () => {
  test('lists the seeded workspaces and announces their count', async ({ page }) => {
    await page.goto('/newsletter/workspaces');

    await expect(rows(page)).toHaveCount(2);
    await expect(page.locator('[data-live-region="workspaces-status"]')).toHaveText('2 workspaces configured');
    await assertNoLayoutIssues(page);
    await assertNoConsoleErrors(page);
  });

  test('a reorder moves the rows without recreating them', async ({ page, request }) => {
    await page.goto('/newsletter/workspaces');
    const notes = await row(page, 'Editorial Notes').elementHandle();
    const script = await row(page, 'Session Script').elementHandle();
    await expect(rows(page).first()).toContainText('Editorial Notes');

    await change(request, 'workspace-reordered');

    await expect(rows(page).first()).toContainText('Session Script');
    expect(await notes!.evaluate((node) => node.isConnected)).toBe(true);
    expect(await script!.evaluate((node) => node.isConnected)).toBe(true);
    await expect(row(page, 'Session Script').getByRole('link', { name: 'View Files' }))
      .toHaveAttribute('href', '/newsletter/workspaces/0/file');
    await expect(row(page, 'Editorial Notes').getByRole('link', { name: 'View Files' }))
      .toHaveAttribute('href', '/newsletter/workspaces/1/file');
    await assertNoConsoleErrors(page);
  });

  test('an added and a removed workspace change the list and the announced count', async ({ page, request }) => {
    await page.goto('/newsletter/workspaces');
    const notes = await row(page, 'Editorial Notes').elementHandle();

    await change(request, 'workspace-added');

    await expect(rows(page)).toHaveCount(3);
    await expect(row(page, 'Review Script')).toBeVisible();
    await expect(page.locator('[data-live-region="workspaces-status"]')).toHaveText('3 workspaces configured');
    expect(await notes!.evaluate((node) => node.isConnected)).toBe(true);

    await change(request, 'workspace-removed');

    await expect(rows(page)).toHaveCount(2);
    await expect(row(page, 'Review Script')).toHaveCount(0);
    await expect(page.locator('[data-live-region="workspaces-status"]')).toHaveText('2 workspaces configured');
  });

  test('a missing source file is reported on its workspace row', async ({ page, request }) => {
    await page.goto('/newsletter/workspaces');
    await expect(page.getByText('Config file not found')).toHaveCount(0);

    await change(request, 'workspace-file-removed');

    await expect(row(page, 'Editorial Notes')).toContainText('Config file not found: Notes');
    await expect(row(page, 'Session Script')).not.toContainText('Config file not found');
  });
});

test('a team without workspaces keeps its empty state', async ({ page, request }) => {
  await reset(request);
  await page.goto('/newsletter/workspaces');

  await expect(page.getByText('No workspaces configured for this team.')).toBeVisible();
  await expect(page.locator('[data-live-region="workspaces-status"]')).toHaveText('No workspaces configured');
  await assertNoConsoleErrors(page);
});
