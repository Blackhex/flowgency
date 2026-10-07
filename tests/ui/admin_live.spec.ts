import { expect, test, type APIRequestContext, type Page } from '@playwright/test';

import { assertNoConsoleErrors, assertNoLayoutIssues, installBasePageSetup } from './layout';
import { expectNoNotice, expectOnlyNotice, expectQuietPolls } from './live_notice';

const SETTINGS_NOTICE = 'Settings changed since this form loaded. Reload to see the latest.';
const INTERVAL_NOTICE = 'Heartbeat interval changed since this page loaded. Reload to see the latest.';
const TEAM_NOTICE = 'Team configuration changed since this form loaded. Reload to see the latest.';
const NEW_TEAM_NOTICE = 'Configuration changed since this form loaded. Reload to see the latest.';
const UNAVAILABLE = 'This page is no longer available. Refresh to check.';
const RENAMED_TITLE = 'Flowgency UI Gate Renamed';

async function reset(request: APIRequestContext): Promise<void> {
  expect((await request.post('/__ui/reset', { data: { fixture: 'default' } })).status()).toBe(204);
}

async function change(request: APIRequestContext, name: string): Promise<void> {
  expect((await request.post('/__ui/live/change', { data: { case: name } })).status()).toBe(204);
}

const region = (page: Page, key: string) => page.locator(`[data-live-region="${key}"]`);
const keyed = (page: Page, key: string) => page.locator(`[data-live-key="${key}"]`);
const pageStatus = (page: Page) => page.locator('[data-live-status][role="status"]');
const loadedRevision = (page: Page) => page.locator('main input[name="revision"]').first();
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

test.describe('live admin settings', () => {
  test('a saved change elsewhere notifies once and never touches the draft or its revision', async ({ page, request }) => {
    await page.goto('/admin/');
    await expectNoNotice(page);
    await assertNoLayoutIssues(page);
    const original = await loadedRevision(page).inputValue();
    await page.locator('#title').fill('Draft title');
    const titleNode = await page.locator('#title').elementHandle();

    await change(request, 'admin-settings-changed');

    await expectOnlyNotice(page, SETTINGS_NOTICE);
    await expect(page.locator('#title')).toHaveValue('Draft title');
    await expect(loadedRevision(page)).toHaveValue(original);
    expect(await connected(titleNode!)).toBe(true);
    await expect(region(page, 'settings-status')).not.toContainText(original.slice(0, 12));
    await assertNoConsoleErrors(page);

    await page.reload();

    await expectNoNotice(page);
    await expect(page.locator('#title')).toHaveValue(RENAMED_TITLE);
  });

  test('unchanged polls leave the page untouched', async ({ page }) => {
    await page.goto('/admin/');
    await expectQuietPolls(page, [region(page, 'settings-status'), page.locator('[data-live-notices]')]);
  });
});

test.describe('live admin dispatch', () => {
  test('a remote interval change notifies while the typed interval survives', async ({ page, request }) => {
    await page.goto('/admin/dispatch');
    await expectNoNotice(page);
    await page.locator('#dispatch_interval').fill('45');

    await change(request, 'admin-settings-changed');

    await expectOnlyNotice(page, INTERVAL_NOTICE);
    await expect(page.locator('#dispatch_interval')).toHaveValue('45');
    await expect(region(page, 'dispatch-status')).toContainText('Dispatcher');
    await assertNoConsoleErrors(page);
  });

  test('a team schedule enabled elsewhere updates its row in place', async ({ page, request }) => {
    await page.goto('/admin/dispatch');
    const row = keyed(page, 'dispatch-team:research');
    await expect(row).toHaveAttribute('data-dispatch-enabled', 'false');
    const handle = await row.elementHandle();

    await change(request, 'admin-dispatch-changed');

    await expect(row).toHaveAttribute('data-dispatch-enabled', 'true');
    await expect(row).toContainText('Schedule enabled');
    expect(await connected(handle!)).toBe(true);
    await expect(keyed(page, 'dispatch-team:newsletter')).toHaveAttribute('data-dispatch-enabled', 'false');
    await assertNoLayoutIssues(page);
  });
});

test.describe('live admin integrations', () => {
  const rows = (page: Page) => page.locator('[data-live-region="integrations-available"] tbody tr');

  test('the listing follows the integration files and keeps unchanged rows', async ({ page, request }) => {
    await page.goto('/admin/integrations');
    await expect(rows(page)).toHaveCount(1);
    const widget = await keyed(page, 'available:acme.widget').elementHandle();
    await expect(region(page, 'integrations-status')).toContainText('1 available to register');

    await change(request, 'integration-available-added');

    await expect(rows(page)).toHaveCount(2);
    await expect(keyed(page, 'available:acme.gadget')).toBeVisible();
    await expect(region(page, 'integrations-status')).toContainText('2 available to register');
    expect(await connected(widget!)).toBe(true);

    await change(request, 'integration-registered');

    await expect(rows(page)).toHaveCount(1);
    await expect(keyed(page, 'available:acme.widget')).toHaveCount(0);
    await assertNoConsoleErrors(page);
  });

  test('a row whose button is focused stays until it is released', async ({ page, request }) => {
    await page.goto('/admin/integrations');
    const button = keyed(page, 'available-action:acme.widget').getByRole('button', { name: 'Register' });
    await button.focus();

    await change(request, 'integration-registered');

    await expect(region(page, 'integrations-status')).toContainText('0 available to register');
    await expect(keyed(page, 'available:acme.widget')).toHaveCount(1);
    await expect(button).toBeFocused();

    await button.blur();

    await expect(keyed(page, 'available:acme.widget')).toHaveCount(0);
    await expect(keyed(page, 'available:empty')).toBeVisible();
  });

  test('unregister asks for confirmation through a delegated handler and posts nothing when dismissed', async ({ page }) => {
    await page.goto('/admin/integrations');
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

    await keyed(page, 'integration-action:copilot').getByRole('button', { name: 'Unregister' }).click();

    expect(messages).toEqual(['Unregister copilot?']);
    expect(posts).toEqual([]);
    await expect(page).toHaveURL(/\/admin\/integrations$/);
  });
});

test.describe('live admin teams', () => {
  const card = (page: Page, key: string) => keyed(page, `admin-team:${key}`);

  test('remote create, change and removal update the cards without recreating the others', async ({ page, request }) => {
    await page.goto('/admin/teams');
    await expect(region(page, 'teams-status')).toHaveText('2 teams configured');
    const newsletter = await card(page, 'newsletter').elementHandle();
    const deleteForm = keyed(page, 'team-action:newsletter:delete');
    const deleteRevision = await deleteForm.locator('input[name="revision"]').inputValue();

    await change(request, 'admin-team-created');

    await expect(card(page, 'scribe-team')).toContainText('Scribe Team');
    await expect(region(page, 'teams-status')).toHaveText('3 teams configured');
    expect(await connected(newsletter!)).toBe(true);

    await change(request, 'admin-team-changed');

    await expect(card(page, 'newsletter')).toContainText('Newsletter Desk');
    await expect(card(page, 'newsletter')).toContainText('5 agents');
    await expect(deleteForm.locator('input[name="revision"]')).toHaveValue(deleteRevision);

    await change(request, 'admin-team-removed');

    await expect(card(page, 'research')).toHaveCount(0);
    await expect(region(page, 'teams-status')).toHaveText('2 teams configured');
    await assertNoConsoleErrors(page);
  });

  test('delete confirmation is delegated and a dismissed prompt sends nothing', async ({ page }) => {
    await page.goto('/admin/teams');
    const posts: string[] = [];
    page.on('request', (request) => {
      if (request.method() === 'POST') posts.push(request.url());
    });
    const messages: string[] = [];
    page.once('dialog', async (dialog) => {
      messages.push(dialog.message());
      await dialog.dismiss();
    });

    await keyed(page, 'team-action:research:delete').getByRole('button', { name: 'Delete' }).click();

    expect(messages).toEqual([
      "Delete team 'Research'? This only removes it from config — files on disk are not deleted.",
    ]);
    expect(posts).toEqual([]);
  });
});

test.describe('live admin team forms', () => {
  test('edit keeps the draft and loaded revision while the agent count and notice follow the saved team', async ({ page, request }) => {
    await page.goto('/admin/teams/newsletter/edit');
    await expectNoNotice(page);
    const original = await loadedRevision(page).inputValue();
    await page.locator('#name').fill('Draft team name');
    const link = region(page, 'team-edit-agents').getByRole('link');
    await expect(link).toContainText('Manage agents (4)');
    const linkNode = await link.elementHandle();

    await change(request, 'admin-team-changed');

    await expectOnlyNotice(page, TEAM_NOTICE);
    await expect(link).toContainText('Manage agents (5)');
    expect(await connected(linkNode!)).toBe(true);
    await expect(page.locator('#name')).toHaveValue('Draft team name');
    await expect(loadedRevision(page)).toHaveValue(original);
    await assertNoConsoleErrors(page);
  });

  test('a removed team reports the page unavailable and keeps what was loaded', async ({ page, request }) => {
    await page.goto('/admin/teams/research/edit');
    await page.locator('#name').fill('Unsaved research name');

    await change(request, 'admin-team-removed');

    await expect(pageStatus(page)).toBeVisible();
    await expect(pageStatus(page)).toContainText(UNAVAILABLE);
    await expect(page.locator('#name')).toHaveValue('Unsaved research name');
  });

  test('new keeps the typed key while a team created elsewhere raises the notice', async ({ page, request }) => {
    await page.goto('/admin/teams/new');
    await expectNoNotice(page);
    await page.locator('#key').fill('draft-team');

    await change(request, 'admin-team-created');

    await expectOnlyNotice(page, NEW_TEAM_NOTICE);
    await expect(page.locator('#key')).toHaveValue('draft-team');
    await assertNoConsoleErrors(page);
  });

  test('a rejected create keeps its error and the draft while polling the canonical read', async ({ page }) => {
    await page.goto('/admin/teams/new');
    await page.evaluate(() => {
      (document.querySelector('form[action="/admin/teams/create"]') as HTMLFormElement).noValidate = true;
    });
    await page.locator('#key').fill('draft-team');
    const snapshot = page.waitForResponse(
      (response) => response.request().method() === 'GET' && response.url().endsWith('/admin/teams/new?__live=1'),
    );

    await page.getByRole('button', { name: 'Create Team' }).click();

    await expect(page.getByText('Key, name, workspace path, and path are required.')).toBeVisible();
    expect((await snapshot).status()).toBe(200);
    await expect(page.getByText('Key, name, workspace path, and path are required.')).toBeVisible();
    await expect(page.locator('#key')).toHaveValue('draft-team');
    await assertNoConsoleErrors(page);
  });
});
