import { expect, test, type APIRequestContext, type Page } from '@playwright/test';

import { assertNoConsoleErrors, assertNoLayoutIssues, assertOnlyExpectedConflictConsoleError, installBasePageSetup } from './layout';
import { expectNoNotice, expectOnlyNotice, expectQuietPolls } from './live_notice';

const SOURCE_NOTICE = 'Workflow source changed since this editor loaded. Reload to see the latest.';
const EDITOR_CONFIG_NOTICE = 'Configuration changed since this editor loaded. Reload to see the latest.';
const FORM_CONFIG_NOTICE = 'Configuration changed since this form loaded. Reload to see the latest.';
const UNAVAILABLE = 'This page is no longer available. Refresh to check.';
const EDITED_NAME = 'Delivery Prime';
const RENAMED_WORKFLOW = 'Delivery Board Prime';

const LIBRARY = '/admin/workflow-library';
const DELIVERY = `${LIBRARY}/blueprints/delivery`;
const TRIAGE = `${LIBRARY}/blueprints/triage`;
const NEW_BLUEPRINT = `${LIBRARY}/blueprints/new`;
const SETTINGS = '/newsletter/workflows/delivery/settings';
const ADDED_SETTINGS = '/newsletter/workflows/triage-board/settings';
const CREATE = '/newsletter/workflows/new';

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

async function snapshotReads(page: Page, count: number): Promise<void> {
  for (let read = 0; read < count; read += 1) {
    await page.waitForResponse((response) => response.url().includes('__live=1'));
  }
}

// The editor previews a changed draft after a short debounce; settle it before the source or configuration moves.
async function fillEditorDraft(page: Page, selector: string, value: string): Promise<void> {
  const preview = page.waitForResponse(
    (response) => response.request().method() === 'POST' && response.url().endsWith('/preview'),
  );
  await page.locator(selector).fill(value);
  await preview;
}

test.beforeEach(async ({ page, request }, testInfo) => {
  await reset(request);
  await installBasePageSetup(page, testInfo.project.name.endsWith('dark') ? 'dark' : 'light');
});

test.afterEach(async ({ page, request }) => {
  await page.close();
  await reset(request);
});

test.describe('live workflow library', () => {
  test('blueprints and references added and removed elsewhere update the cards without recreating the others', async ({ page, request }) => {
    await page.goto(LIBRARY);
    await assertNoLayoutIssues(page);
    const delivery = await keyed(page, 'workflow-blueprint:delivery').elementHandle();

    await change(request, 'workflow-blueprint-added');

    await expect(keyed(page, 'workflow-blueprint:triage')).toContainText('Triage');
    expect(await connected(delivery!)).toBe(true);

    await change(request, 'workflow-added');

    await expect(keyed(page, 'workflow-reference:delivery:newsletter:triage-board')).toHaveText('Newsletter / Triage Board');
    expect(await connected(delivery!)).toBe(true);

    await change(request, 'workflow-removed');
    await change(request, 'workflow-blueprint-removed');

    await expect(keyed(page, 'workflow-reference:delivery:newsletter:triage-board')).toHaveCount(0);
    await expect(keyed(page, 'workflow-blueprint:triage')).toHaveCount(0);
    await assertNoConsoleErrors(page);
  });

  test('a source change elsewhere renames the card', async ({ page, request }) => {
    await page.goto(LIBRARY);

    await change(request, 'workflow-source-changes');

    await expect(keyed(page, 'workflow-blueprint:delivery').getByRole('heading', { level: 2 })).toHaveText(EDITED_NAME);
  });

  test('unchanged polls leave the page untouched', async ({ page }) => {
    await page.goto(LIBRARY);
    await expectQuietPolls(page, [region(page, 'workflow-library-blueprints')]);
  });
});

test.describe('live workflow blueprint editor', () => {
  test('a source change elsewhere keeps the draft and baselines and raises only the source notice', async ({ page, request }) => {
    await page.goto(DELIVERY);
    await expectNoNotice(page);
    await assertNoLayoutIssues(page);
    const digest = await page.locator('input[name="expected_digest"]').inputValue();
    const revision = await page.locator('input[name="expected_revision"]').inputValue();
    const description = page.locator('[data-editor-bind="description"]');
    await fillEditorDraft(page, '[data-editor-bind="description"]', 'Local description draft');
    const editor = await description.elementHandle();

    await change(request, 'workflow-source-changes');

    await expectOnlyNotice(page, SOURCE_NOTICE);
    await expect(description).toHaveValue('Local description draft');
    await expect(page.locator('input[name="expected_digest"]')).toHaveValue(digest);
    await expect(page.locator('input[name="expected_revision"]')).toHaveValue(revision);
    expect(await connected(editor!)).toBe(true);
    await assertNoConsoleErrors(page);

    await page.reload();

    await expectNoNotice(page);
    await expect(page.locator('[data-editor-bind="name"]')).toHaveValue(EDITED_NAME);
  });

  test('a configuration change elsewhere raises only the configuration notice and keeps the draft', async ({ page, request }) => {
    await page.goto(DELIVERY);
    await expectNoNotice(page);
    const revision = await page.locator('input[name="expected_revision"]').inputValue();
    await fillEditorDraft(page, '[data-editor-bind="description"]', 'Local description draft');

    await change(request, 'workflow-settings-changed');

    await expectOnlyNotice(page, EDITOR_CONFIG_NOTICE);
    await expect(page.locator('[data-editor-bind="description"]')).toHaveValue('Local description draft');
    await expect(page.locator('input[name="expected_revision"]')).toHaveValue(revision);
    await assertNoConsoleErrors(page);
  });

  test('an own save advances the loaded baselines so no stale notice appears', async ({ page }) => {
    await page.goto(DELIVERY);
    const digest = await page.locator('input[name="expected_digest"]').inputValue();
    await page.locator('[data-editor-bind="description"]').fill('Saved through the editor');
    await expect(page.getByRole('button', { name: 'Save', exact: true })).toBeEnabled();

    const saved = page.waitForResponse(
      (response) => response.url().endsWith(DELIVERY) && response.request().method() === 'POST',
    );
    await page.getByRole('button', { name: 'Save', exact: true }).click();
    await saved;

    await expect(page.getByRole('button', { name: 'Save', exact: true })).toBeDisabled();
    await expect(page.locator('input[name="expected_digest"]')).not.toHaveValue(digest);
    await snapshotReads(page, 2);
    await expectNoNotice(page);
    await assertNoConsoleErrors(page);
  });

  test('a rejected save keeps its issues and the draft while polling the canonical read', async ({ page, request }) => {
    await page.goto(DELIVERY);
    const description = page.locator('[data-editor-bind="description"]');
    await fillEditorDraft(page, '[data-editor-bind="description"]', 'Draft that will be rejected');
    await expect(page.locator('[data-editor-status]')).toHaveText('Unsaved changes');
    await change(request, 'workflow-source-changes');
    const snapshot = page.waitForResponse(
      (response) => response.request().method() === 'GET' && response.url().endsWith(`${DELIVERY}?__live=1`),
    );

    await page.getByRole('button', { name: 'Save', exact: true }).click();

    await expect(page.locator('[data-editor-issues]')).toContainText('Workflow source changed before save completed.');
    expect((await snapshot).status()).toBe(200);
    await expect(page.locator('[data-editor-issues]')).toContainText('Workflow source changed before save completed.');
    await expect(description).toHaveValue('Draft that will be rejected');
    await assertOnlyExpectedConflictConsoleError(page);
  });

  test('a removed blueprint reports the page unavailable and keeps what was loaded', async ({ page, request }) => {
    await change(request, 'workflow-blueprint-added');
    await page.goto(TRIAGE);
    await page.locator('[data-editor-bind="description"]').fill('Unsaved triage draft');

    await change(request, 'workflow-blueprint-removed');

    await expect(pageStatus(page)).toBeVisible();
    await expect(pageStatus(page)).toContainText(UNAVAILABLE);
    await expect(page.locator('[data-editor-bind="description"]')).toHaveValue('Unsaved triage draft');
  });

  test('unchanged polls leave the page untouched', async ({ page }) => {
    await page.goto(DELIVERY);
    await expectQuietPolls(page, [region(page, 'workflow-blueprint-source'), page.locator('[data-live-notices]')]);
  });
});

test.describe('live new workflow blueprint', () => {
  test('a configuration change elsewhere keeps the creation draft and raises the configuration notice', async ({ page, request }) => {
    await page.goto(NEW_BLUEPRINT);
    await expectNoNotice(page);
    await assertNoLayoutIssues(page);
    const revision = await page.locator('input[name="expected_revision"]').inputValue();
    await fillEditorDraft(page, '[data-editor-bind="name"]', 'Typed blueprint name');

    await change(request, 'workflow-settings-changed');

    await expectOnlyNotice(page, EDITOR_CONFIG_NOTICE);
    await expect(page.locator('[data-editor-bind="name"]')).toHaveValue('Typed blueprint name');
    await expect(page.locator('input[name="expected_revision"]')).toHaveValue(revision);
    await assertNoConsoleErrors(page);
  });

  test('unchanged polls leave the page untouched', async ({ page }) => {
    await page.goto(NEW_BLUEPRINT);
    await expectQuietPolls(page, [region(page, 'workflow-blueprint-source'), page.locator('[data-live-notices]')]);
  });
});

test.describe('live workflow settings', () => {
  test('a change elsewhere renames the heading and raises the configuration notice while the draft survives', async ({ page, request }) => {
    await page.goto(SETTINGS);
    await expectNoNotice(page);
    await assertNoLayoutIssues(page);
    const revision = await page.locator('input[name="expected_revision"]').inputValue();
    const name = page.getByLabel('Name', { exact: true });
    await name.fill('Typed workflow name');
    const form = await name.elementHandle();

    await change(request, 'workflow-settings-changed');

    await expect(region(page, 'workflow-settings-header').getByRole('heading', { level: 1 })).toHaveText(`${RENAMED_WORKFLOW} settings`);
    await expectOnlyNotice(page, FORM_CONFIG_NOTICE);
    await expect(name).toHaveValue('Typed workflow name');
    await expect(page.locator('input[name="expected_revision"]')).toHaveValue(revision);
    await expect(page.getByRole('button', { name: 'Save', exact: true })).toBeEnabled();
    expect(await connected(form!)).toBe(true);
    await assertNoConsoleErrors(page);

    await page.reload();

    await expectNoNotice(page);
    await expect(page.getByLabel('Name', { exact: true })).toHaveValue(RENAMED_WORKFLOW);
  });

  test('a storage check stays a local action and never a passive read', async ({ page }) => {
    await page.goto(SETTINGS);
    const posts: string[] = [];
    page.on('request', (request) => {
      if (request.method() === 'POST') posts.push(request.url());
    });

    await page.getByRole('button', { name: 'Check storage' }).click();

    await expect(page.locator('#workflow-storage-health')).toHaveText('Available');
    expect(posts).toHaveLength(1);
    expect(posts[0]).toMatch(/\/settings\/check-storage$/);
    await expectNoNotice(page);
    await assertNoConsoleErrors(page);
  });

  test('a removed workflow reports the page unavailable and keeps the draft', async ({ page, request }) => {
    await change(request, 'workflow-added');
    await page.goto(ADDED_SETTINGS);
    await page.getByLabel('Name', { exact: true }).fill('Unsaved board name');

    await change(request, 'workflow-removed');

    await expect(pageStatus(page)).toBeVisible();
    await expect(pageStatus(page)).toContainText(UNAVAILABLE);
    await expect(page.getByLabel('Name', { exact: true })).toHaveValue('Unsaved board name');
  });

  test('a stale save keeps the submitted draft and the warning while polling the canonical read', async ({ page, request }) => {
    await page.goto(SETTINGS);
    await change(request, 'workflow-settings-changed');
    await page.getByLabel('Name', { exact: true }).fill('Stale local draft');
    const snapshot = page.waitForResponse(
      (response) => response.request().method() === 'GET' && response.url().endsWith(`${SETTINGS}?__live=1`),
    );

    await page.getByRole('button', { name: 'Save', exact: true }).click();

    await expect(page.locator('body')).toContainText('reload before saving');
    expect((await snapshot).status()).toBe(200);
    await expect(page.locator('body')).toContainText('reload before saving');
    await expect(page.getByLabel('Name', { exact: true })).toHaveValue('Stale local draft');
    await assertOnlyExpectedConflictConsoleError(page);
  });

  test('unchanged polls leave the page untouched', async ({ page }) => {
    await page.goto(SETTINGS);
    await expectQuietPolls(page, [
      region(page, 'workflow-settings-header'),
      region(page, 'workflow-settings-source'),
      page.locator('[data-live-notices]'),
    ]);
  });
});

test.describe('live new workflow', () => {
  test('a blueprint added and a configuration change elsewhere keep the catalog and the draft', async ({ page, request }) => {
    await page.goto(CREATE);
    await expectNoNotice(page);
    await assertNoLayoutIssues(page);
    const revision = await page.locator('input[name="expected_revision"]').inputValue();
    const options = await page.locator('#workflow-blueprint option').allTextContents();
    await page.getByLabel('Name', { exact: true }).fill('Typed new workflow');

    await change(request, 'workflow-blueprint-added');
    await change(request, 'workflow-settings-changed');

    await expectOnlyNotice(page, FORM_CONFIG_NOTICE);
    await expect(page.locator('#workflow-blueprint option')).toHaveText(options);
    await expect(page.getByLabel('Name', { exact: true })).toHaveValue('Typed new workflow');
    await expect(page.locator('input[name="expected_revision"]')).toHaveValue(revision);
    await assertNoConsoleErrors(page);
  });

  test('unchanged polls stay quiet although the new workflow id is generated per request', async ({ page }) => {
    await page.goto(CREATE);
    await expectQuietPolls(page, [
      region(page, 'workflow-settings-header'),
      region(page, 'workflow-settings-source'),
      page.locator('[data-live-notices]'),
    ]);
  });
});
