import { expect, test, type APIRequestContext, type Page } from '@playwright/test';

import { assertNoConsoleErrors, assertNoLayoutIssues, assertOnlyExpectedConflictConsoleError, installBasePageSetup } from './layout';
import { expectNoNotice, expectOnlyNotice, expectQuietPolls } from './live_notice';

const SOURCE_NOTICE = 'Blueprint source changed since this editor loaded. Reload to see the latest.';
const CHANNEL_LIST_NOTICE = 'Configuration changed since this form loaded. Reload to see the latest.';
const CHANNEL_CONFIG_NOTICE = 'Channel configuration changed since this form loaded. Reload to see the latest.';
const CHANNEL_CONTENT_NOTICE = 'Channel memory changed since this editor loaded. Reload to see the latest.';
const UNAVAILABLE = 'This page is no longer available. Refresh to check.';
const EDITED_TITLE = 'Advisor Prime';

const ADVISOR = '/admin/agent-library/blueprints/advisor';
const CHECKLIST = '.agents/skills/daily-review/checklist.md';
const RELEASE_WINDOW = '.agents/prompts/release-window.prompt.md';
const SKILL_PAGE = `${ADVISOR}/skills/daily-review?path=${encodeURIComponent(CHECKLIST)}`;
const PROMPTS_PAGE = `${ADVISOR}/prompts?path=${encodeURIComponent(RELEASE_WINDOW)}`;
const BRAND_STRATEGY = '/admin/memory-channels/brand-strategy';

async function reset(request: APIRequestContext): Promise<void> {
  expect((await request.post('/__ui/reset', { data: { fixture: 'default' } })).status()).toBe(204);
}

async function change(request: APIRequestContext, name: string): Promise<void> {
  expect((await request.post('/__ui/live/change', { data: { case: name } })).status()).toBe(204);
}

const region = (page: Page, key: string) => page.locator(`[data-live-region="${key}"]`);
const keyed = (page: Page, key: string) => page.locator(`[data-live-key="${key}"]`);
const pageStatus = (page: Page) => page.locator('[data-live-status][role="status"]');
const connected = (handle: { evaluate: (fn: (node: Element) => boolean) => Promise<boolean> }) =>
  handle.evaluate((node) => node.isConnected);

test.beforeEach(async ({ page, request }, testInfo) => {
  await reset(request);
  await installBasePageSetup(page, testInfo.project.name.endsWith('dark') ? 'dark' : 'light');
});

test.afterEach(async ({ page, request }) => {
  await page.close();
  await reset(request);
});

test.describe('live agent library list', () => {
  test('a blueprint added and removed elsewhere updates the cards without recreating the others', async ({ page, request }) => {
    await page.goto('/admin/agent-library');
    await assertNoLayoutIssues(page);
    const advisor = await keyed(page, 'blueprint:advisor').elementHandle();

    await change(request, 'library-blueprint-added');

    await expect(keyed(page, 'blueprint:designer')).toContainText('Designer');
    expect(await connected(advisor!)).toBe(true);

    await change(request, 'library-blueprint-removed');

    await expect(keyed(page, 'blueprint:designer')).toHaveCount(0);
    await assertNoConsoleErrors(page);
  });

  test('unchanged polls leave the page untouched', async ({ page }) => {
    await page.goto('/admin/agent-library');
    await expectQuietPolls(page, [region(page, 'library-blueprints')]);
  });
});

test.describe('live blueprint detail', () => {
  test('a source change elsewhere updates the read-only details and keeps the editor draft and baseline', async ({ page, request }) => {
    await page.goto(ADVISOR);
    await expectNoNotice(page);
    await assertNoLayoutIssues(page);
    const baseline = await page.locator('input[name="expected_digest"]').inputValue();
    await page.locator('#agents-content').fill('Local AGENTS.md draft');
    const editor = await page.locator('#agents-content').elementHandle();
    await expect(region(page, 'blueprint-header')).toContainText('Advisor');

    await change(request, 'library-source-changes');

    await expect(region(page, 'blueprint-header').getByRole('heading', { level: 1 })).toHaveText(EDITED_TITLE);
    await expect(region(page, 'blueprint-files')).toContainText('notes.md');
    await expectOnlyNotice(page, SOURCE_NOTICE);
    await expect(page.locator('#agents-content')).toHaveValue('Local AGENTS.md draft');
    await expect(page.locator('input[name="expected_digest"]')).toHaveValue(baseline);
    expect(await connected(editor!)).toBe(true);
    await assertNoConsoleErrors(page);

    await page.reload();

    await expectNoNotice(page);
    await expect(page.locator('#agents-content')).toHaveValue(/edited by the library case/);
  });

  test('a removed blueprint reports the page unavailable and keeps what was loaded', async ({ page, request }) => {
    await change(request, 'library-blueprint-added');
    await page.goto('/admin/agent-library/blueprints/designer');
    await page.locator('#agents-content').fill('Unsaved designer draft');

    await change(request, 'library-blueprint-removed');

    await expect(pageStatus(page)).toBeVisible();
    await expect(pageStatus(page)).toContainText(UNAVAILABLE);
    await expect(page.locator('#agents-content')).toHaveValue('Unsaved designer draft');
  });

  test('a rejected save keeps the warning and the draft while polling the canonical read', async ({ page }) => {
    await page.goto(ADVISOR);
    await page.locator('#agents-content').fill('Draft that will be rejected');
    await page.evaluate(() => {
      (document.querySelector('input[name="expected_digest"]') as HTMLInputElement).value = 'stale';
    });
    const snapshot = page.waitForResponse(
      (response) => response.request().method() === 'GET' && response.url().endsWith(`${ADVISOR}?__live=1`),
    );

    await page.getByRole('button', { name: 'Save AGENTS.md' }).click();

    await expect(page.getByText('Blueprint source changed; reload before saving')).toBeVisible();
    expect((await snapshot).status()).toBe(200);
    await expect(page.getByText('Blueprint source changed; reload before saving')).toBeVisible();
    await expect(page.locator('#agents-content')).toHaveValue('Draft that will be rejected');
    await assertOnlyExpectedConflictConsoleError(page);
  });

  test('unchanged polls leave the page untouched', async ({ page }) => {
    await page.goto(ADVISOR);
    await expectQuietPolls(page, [region(page, 'blueprint-header'), region(page, 'blueprint-files'), page.locator('[data-live-notices]')]);
  });
});

test.describe('live blueprint skills', () => {
  test('a source change elsewhere updates the file list and notifies while the draft survives', async ({ page, request }) => {
    await page.goto(SKILL_PAGE);
    await expectNoNotice(page);
    await assertNoLayoutIssues(page);
    const baseline = await page.locator('input[name="expected_digest"]').inputValue();
    await page.locator('textarea[name="content"]').fill('Local skill draft');
    await expect(region(page, 'skill-files').getByRole('link')).toHaveCount(2);

    await change(request, 'library-source-changes');

    await expect(region(page, 'skill-files').getByRole('link')).toHaveCount(3);
    await expect(region(page, 'skill-files')).toContainText('notes.md');
    await expectOnlyNotice(page, SOURCE_NOTICE);
    await expect(page.locator('textarea[name="content"]')).toHaveValue('Local skill draft');
    await expect(page.locator('input[name="expected_digest"]')).toHaveValue(baseline);
    await assertNoConsoleErrors(page);
  });

  test('the removed file reports the page unavailable and keeps the draft', async ({ page, request }) => {
    await page.goto(SKILL_PAGE);
    await page.locator('textarea[name="content"]').fill('Draft for a removed file');

    await change(request, 'library-selected-files-removed');

    await expect(pageStatus(page)).toContainText(UNAVAILABLE);
    await expect(page.locator('textarea[name="content"]')).toHaveValue('Draft for a removed file');
  });

  test('the skills alias page registers the resolved first file', async ({ page, request }) => {
    await page.goto(`${ADVISOR}/skills`);
    await expectNoNotice(page);

    await change(request, 'library-source-changes');

    await expectOnlyNotice(page, SOURCE_NOTICE);
  });
});

test.describe('live blueprint prompts', () => {
  test('a prompt added elsewhere appears in the list and notifies while every draft survives', async ({ page, request }) => {
    await page.goto(PROMPTS_PAGE);
    await expectNoNotice(page);
    await assertNoLayoutIssues(page);
    const baseline = await page.locator('input[name="expected_digest"]').first().inputValue();
    await page.locator('#selected-prompt-content').fill('Local prompt draft');
    await page.locator('#create-slug').fill('typed-slug');
    const list = await keyed(page, 'prompt:release-window').elementHandle();
    await expect(keyed(page, 'prompt:weekly-brief')).toHaveCount(0);

    await change(request, 'library-source-changes');

    await expect(keyed(page, 'prompt:weekly-brief')).toBeVisible();
    await expectOnlyNotice(page, SOURCE_NOTICE);
    expect(await connected(list!)).toBe(true);
    await expect(page.locator('#selected-prompt-content')).toHaveValue('Local prompt draft');
    await expect(page.locator('#create-slug')).toHaveValue('typed-slug');
    await expect(page.locator('input[name="expected_digest"]').first()).toHaveValue(baseline);
    await assertNoConsoleErrors(page);
  });

  test('the removed prompt reports the page unavailable and keeps the draft', async ({ page, request }) => {
    await page.goto(PROMPTS_PAGE);
    await page.locator('#selected-prompt-content').fill('Draft for a removed prompt');

    await change(request, 'library-selected-files-removed');

    await expect(pageStatus(page)).toContainText(UNAVAILABLE);
    await expect(page.locator('#selected-prompt-content')).toHaveValue('Draft for a removed prompt');
  });
});

test.describe('live memory channel list', () => {
  test('a channel added and removed elsewhere updates the rows and notifies while the draft survives', async ({ page, request }) => {
    await page.goto('/admin/memory-channels');
    await expectNoNotice(page);
    await assertNoLayoutIssues(page);
    await page.locator('#channel-key').fill('typed-key');
    const row = await keyed(page, 'channel:brand-strategy').elementHandle();

    await change(request, 'channel-added');

    await expect(keyed(page, 'channel:launch-notes')).toContainText('Launch Notes');
    await expectOnlyNotice(page, CHANNEL_LIST_NOTICE);
    expect(await connected(row!)).toBe(true);
    await expect(page.locator('#channel-key')).toHaveValue('typed-key');

    await change(request, 'channel-removed');

    await expect(keyed(page, 'channel:launch-notes')).toHaveCount(0);
    await assertNoConsoleErrors(page);
  });

  test('a rejected create keeps its error and the draft while polling the canonical read', async ({ page }) => {
    await page.goto('/admin/memory-channels');
    await page.locator('#channel-key').fill('Bad Key');
    const snapshot = page.waitForResponse(
      (response) => response.request().method() === 'GET' && response.url().endsWith('/admin/memory-channels?__live=1'),
    );

    await page.getByRole('button', { name: 'Create channel' }).click();

    await expect(page.getByText('Channel keys must be lowercase stable slugs.')).toBeVisible();
    expect((await snapshot).status()).toBe(200);
    await expect(page.getByText('Channel keys must be lowercase stable slugs.')).toBeVisible();
    await assertOnlyExpectedConflictConsoleError(page);
  });

  test('unchanged polls leave the page untouched', async ({ page }) => {
    await page.goto('/admin/memory-channels');
    await expectQuietPolls(page, [region(page, 'channels-table'), page.locator('[data-live-notices]')]);
  });
});

test.describe('live memory channel detail', () => {
  test('a memory edit elsewhere moves only the content notice and keeps the draft and baseline', async ({ page, request }) => {
    await page.goto(BRAND_STRATEGY);
    await expectNoNotice(page);
    await assertNoLayoutIssues(page);
    const baseline = await page.locator('input[name="content_revision"]').inputValue();
    const configBaseline = await page.locator('main input[name="revision"]').first().inputValue();
    await page.locator('#memory-content').fill('Local memory draft');
    await expect(region(page, 'channel-files')).toContainText('1 file');

    await change(request, 'channel-memory-changes');

    await expect(region(page, 'channel-files')).toContainText('2 files');
    await expectOnlyNotice(page, CHANNEL_CONTENT_NOTICE);
    await expect(page.locator('#memory-content')).toHaveValue('Local memory draft');
    await expect(page.locator('input[name="content_revision"]')).toHaveValue(baseline);
    await expect(page.locator('main input[name="revision"]').first()).toHaveValue(configBaseline);
    await assertNoConsoleErrors(page);

    await page.reload();

    await expectNoNotice(page);
    await expect(page.locator('#memory-content')).toHaveValue(/Revised in the live channel case/);
  });

  test('a metadata change elsewhere renames the heading and raises the configuration notice', async ({ page, request }) => {
    await page.goto(BRAND_STRATEGY);
    await page.locator('#display-name').fill('Typed name');
    const original = await page.locator('main input[name="revision"]').first().inputValue();

    await change(request, 'channel-metadata-changed');

    await expect(region(page, 'channel-header').getByRole('heading', { level: 1 })).toHaveText('Brand Strategy Council');
    await expectOnlyNotice(page, CHANNEL_CONFIG_NOTICE);
    await expect(page.locator('#display-name')).toHaveValue('Typed name');
    await expect(page.locator('main input[name="revision"]').first()).toHaveValue(original);
  });

  test('a removed channel reports the page unavailable and keeps the draft', async ({ page, request }) => {
    await change(request, 'channel-added');
    await page.goto('/admin/memory-channels/launch-notes');
    await page.locator('#memory-content').fill('Unsaved launch notes');

    await change(request, 'channel-removed');

    await expect(pageStatus(page)).toContainText(UNAVAILABLE);
    await expect(page.locator('#memory-content')).toHaveValue('Unsaved launch notes');
  });

  test('delete confirmation is delegated and a dismissed prompt sends nothing', async ({ page }) => {
    await page.goto(BRAND_STRATEGY);
    expect(await page.locator('main [onclick], main [onsubmit]').count()).toBe(0);
    const posts: string[] = [];
    page.on('request', (request) => {
      if (request.method() === 'POST') posts.push(request.url());
    });
    const messages: string[] = [];
    page.once('dialog', async (dialog) => {
      messages.push(dialog.message());
      await dialog.dismiss();
    });

    await page.getByRole('button', { name: 'Delete channel' }).click();

    expect(messages).toEqual(['Delete this memory channel?']);
    expect(posts).toEqual([]);
  });

  test('a rejected rekey keeps its error while polling the canonical read', async ({ page }) => {
    await page.goto(BRAND_STRATEGY);
    await page.locator('#new-key').fill('renamed-channel');
    const snapshot = page.waitForResponse(
      (response) => response.request().method() === 'GET' && response.url().endsWith(`${BRAND_STRATEGY}?__live=1`),
    );

    await page.getByRole('button', { name: 'Save metadata' }).click();

    await expect(page.getByText('Cannot rekey a referenced channel.')).toBeVisible();
    expect((await snapshot).status()).toBe(200);
    await expect(page.getByText('Cannot rekey a referenced channel.')).toBeVisible();
    await assertOnlyExpectedConflictConsoleError(page);
  });

  test('unchanged polls leave the page untouched', async ({ page }) => {
    await page.goto(BRAND_STRATEGY);
    await expectQuietPolls(page, [region(page, 'channel-header'), region(page, 'channel-files'), page.locator('[data-live-notices]')]);
  });
});
