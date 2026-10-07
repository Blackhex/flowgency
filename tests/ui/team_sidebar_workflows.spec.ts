import { expect, test } from '@playwright/test';
import { readFile, rm, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { parse, stringify } from 'yaml';

import { assertNoConsoleErrors, assertNoLayoutIssues, installBasePageSetup } from './layout';

const runtimeRoot = path.join(__dirname, '.runtime', 'current');
const runtimeConfigPath = path.join(runtimeRoot, 'config.yaml');

async function resetUiRuntime(request: Parameters<typeof test.beforeEach>[0]['request']): Promise<void> {
  const response = await request.post('/__ui/reset');
  expect(response.status()).toBe(204);
}

type RuntimeConfig = {
  teams: Record<string, { workflows?: Record<string, unknown>; workspaces?: unknown[] }>;
};

async function rewriteRuntimeConfig(mutator: (config: RuntimeConfig) => void): Promise<void> {
  const current = await readFile(runtimeConfigPath, 'utf8');
  const config = parse(current) as RuntimeConfig;
  const before = JSON.stringify(config);
  mutator(config);
  expect(JSON.stringify(config)).not.toBe(before);
  await writeFile(runtimeConfigPath, stringify(config), 'utf8');
}

async function openSidebarIfNeeded(page: Parameters<typeof test.beforeEach>[0]['page'], projectName: string): Promise<void> {
  if (projectName.startsWith('mobile')) {
    await page.getByRole('button', { name: 'Open navigation', exact: true }).click();
  }
}

test.beforeEach(async ({ page, request }, testInfo) => {
  await resetUiRuntime(request);
  await installBasePageSetup(page, testInfo.project.name.endsWith('dark') ? 'dark' : 'light');
});

test.afterEach(async ({ page, request }) => {
  await page.close();
  await resetUiRuntime(request);
});

test('team pages share the current team workflow sidebar', async ({ page }, testInfo) => {
  await page.goto('/newsletter/');
  await openSidebarIfNeeded(page, testInfo.project.name);

  await expect(page.getByText('Workflows', { exact: true })).toBeVisible();
  await expect(page.getByRole('link', { name: 'New workflow', exact: true })).toBeVisible();
  await expect(page.locator('nav#sidebar a[href="/newsletter/workflows/delivery"]')).toHaveCount(1);
  await expect(page.locator('nav#sidebar a[href="/newsletter/workflows/research-workflow"]')).toHaveCount(1);
  await expect(page.locator('nav#sidebar')).not.toContainText('Observations');
  await expect(page.locator('nav#sidebar')).not.toContainText('Proposals');
  await expect(page.locator('nav#sidebar')).not.toContainText('Decisions');

  await page.locator('nav#sidebar a[href="/newsletter/workflows/research-workflow"]').click();
  await expect(page).toHaveURL('/newsletter/workflows/research-workflow');
  await expect(page.locator('nav#sidebar a[href="/newsletter/workflows/research-workflow"]')).toHaveClass(/active/);

  await page.goto('/newsletter/agents/advisor/profile');
  await openSidebarIfNeeded(page, testInfo.project.name);
  await expect(page.locator('nav#sidebar a[href="/newsletter/workflows/delivery"]')).toHaveCount(1);
  await expect(page.locator('nav#sidebar a[href="/newsletter/workflows/research-workflow"]')).toHaveCount(1);

  await page.goto('/newsletter/jobs');
  await openSidebarIfNeeded(page, testInfo.project.name);
  await expect(page.locator('nav#sidebar a[href="/newsletter/workflows/delivery"]')).toHaveCount(1);
  await expect(page.locator('nav#sidebar a[href="/newsletter/workflows/research-workflow"]')).toHaveCount(1);

  await page.goto('/newsletter/logs');
  await openSidebarIfNeeded(page, testInfo.project.name);
  await expect(page.locator('nav#sidebar a[href="/newsletter/workflows/delivery"]')).toHaveCount(1);
  await expect(page.locator('nav#sidebar a[href="/newsletter/workflows/research-workflow"]')).toHaveCount(1);

  await page.goto('/newsletter/workspaces');
  await openSidebarIfNeeded(page, testInfo.project.name);
  await expect(page.locator('nav#sidebar a[href="/newsletter/workflows/delivery"]')).toHaveCount(1);
  await expect(page.locator('nav#sidebar a[href="/newsletter/workflows/research-workflow"]')).toHaveCount(1);
  await assertNoLayoutIssues(page);
  await assertNoConsoleErrors(page);
});

test('sidebar marks unavailable workflows instead of showing a false zero count', async ({ page }, testInfo) => {
  await rm(path.join(runtimeRoot, 'tickets', 'research'), { recursive: true, force: true });

  await page.goto('/newsletter/');
  await openSidebarIfNeeded(page, testInfo.project.name);

  const unavailable = page.locator('nav#sidebar [data-workflow-state="unavailable"]');
  await expect(unavailable).toContainText('Unavailable');
  await expect(page.locator('nav#sidebar a[href="/newsletter/workflows/research-workflow"]')).toContainText('Research');
  await expect(page.locator('nav#sidebar a[href="/newsletter/workflows/research-workflow"] [data-workflow-state="count"]')).toHaveCount(0);
  await assertNoLayoutIssues(page);
  await assertNoConsoleErrors(page);
});

test('sidebar still offers new workflow when the team has no configured workflows', async ({ page }, testInfo) => {
  await rewriteRuntimeConfig((config) => {
    config.teams.newsletter.workflows = {};
  });

  await page.goto('/newsletter/');
  await openSidebarIfNeeded(page, testInfo.project.name);

  await expect(page.getByText('Workflows', { exact: true })).toBeVisible();
  await expect(page.getByRole('link', { name: 'New workflow', exact: true })).toHaveAttribute('href', '/newsletter/workflows/new');
  await expect(page.locator('nav#sidebar a[href^="/newsletter/workflows/"]')).toHaveCount(1);
  await assertNoLayoutIssues(page);
  await assertNoConsoleErrors(page);
});

test('second team sidebar stays isolated when it has no configured workflows', async ({ page }, testInfo) => {
  await page.goto('/research/');
  await openSidebarIfNeeded(page, testInfo.project.name);

  await expect(page.getByText('Workflows', { exact: true })).toBeVisible();
  await expect(page.getByRole('link', { name: 'New workflow', exact: true })).toHaveAttribute('href', '/research/workflows/new');
  await expect(page.locator('nav#sidebar a[href="/newsletter/workflows/delivery"]')).toHaveCount(0);
  await expect(page.locator('nav#sidebar a[href="/newsletter/workflows/research-workflow"]')).toHaveCount(0);
  await expect(page.locator('nav#sidebar a[href^="/research/workflows/"]')).toHaveCount(1);
  await assertNoLayoutIssues(page);
  await assertNoConsoleErrors(page);
});

type RequestFixture = Parameters<typeof test.beforeEach>[0]['request'];
type PageFixture = Parameters<typeof test.beforeEach>[0]['page'];

async function applyLiveChange(request: RequestFixture, change: string): Promise<void> {
  const response = await request.post('/__ui/live/change', { data: { case: change } });
  expect(response.status()).toBe(204);
}

async function openLiveRoster(page: PageFixture): Promise<void> {
  await page.clock.install({ time: 0 });
  await page.goto('/newsletter/agents');
  await page.clock.pauseAt(600_000);
}

// Drives the 2000 ms cadence deterministically until the assertion holds.
async function refreshUntil(page: PageFixture, assertion: () => Promise<void>): Promise<void> {
  await expect(async () => {
    await page.clock.runFor(2000);
    await assertion();
  }).toPass({ timeout: 15_000, intervals: [100] });
}

const navigationSurface = (page: PageFixture) => page.locator('#sidebar');
const workflowRow = (page: PageFixture, workflow: string) => page.locator(`#sidebar [data-live-key="workflow:${workflow}"]`);

test('a remote membership change refreshes the shared navigation in place', async ({ page, request }) => {
  await openLiveRoster(page);
  await page.evaluate(() => (window as unknown as { toggleTheme: () => void }).toggleTheme());
  const theme = await page.evaluate(() => ({
    dark: document.documentElement.classList.contains('dark'),
    label: document.getElementById('theme-label')!.textContent,
  }));
  const navigation = await navigationSurface(page).elementHandle();
  const agentsLink = await page.locator('#sidebar a[href="/newsletter/agents"]').elementHandle();
  await expect(navigationSurface(page)).not.toContainText('Research updated');
  await expect(page.locator('#sidebar a[href="/newsletter/workspaces"]')).toHaveCount(0);

  await applyLiveChange(request, 'navigation-membership');
  await refreshUntil(page, () => expect(navigationSurface(page)).toContainText('Research updated', { timeout: 1500 }));

  expect(await navigation!.evaluate((node) => node.isConnected)).toBe(true);
  expect(await agentsLink!.evaluate((node) => node.isConnected)).toBe(true);
  await expect(page.locator('#sidebar a[href="/newsletter/agents"]')).toHaveClass(/active/);
  await expect(page.locator('#team-switcher option[value="research"]')).toHaveText('Research updated');
  await expect(page.locator('#team-switcher')).toHaveValue('newsletter');
  await expect(workflowRow(page, 'research-workflow')).toContainText('Research updated');
  await expect(page.locator('#sidebar a[href="/newsletter/workspaces"]')).toHaveCount(1);
  expect(await page.evaluate(() => ({
    dark: document.documentElement.classList.contains('dark'),
    label: document.getElementById('theme-label')!.textContent,
  }))).toEqual(theme);
  await expect(page.locator('[data-live-status]')).toBeHidden();
  await assertNoConsoleErrors(page);
});

test('a remote change to a workflow count updates only that badge', async ({ page, request }) => {
  await openLiveRoster(page);
  const badge = workflowRow(page, 'delivery').locator('[data-workflow-state="count"]');
  const before = Number(await badge.textContent());
  const row = await workflowRow(page, 'delivery').elementHandle();

  await applyLiveChange(request, 'navigation-workflow-count');
  await refreshUntil(page, () => expect(badge).toHaveText(String(before + 1), { timeout: 1500 }));

  expect(await row!.evaluate((node) => node.isConnected)).toBe(true);
  await assertNoConsoleErrors(page);
});

test('a workflow whose storage disappears turns unavailable, never zero', async ({ page }) => {
  await openLiveRoster(page);
  await expect(workflowRow(page, 'research-workflow').locator('[data-workflow-state="count"]')).toHaveCount(1);

  await rm(path.join(runtimeRoot, 'tickets', 'research'), { recursive: true, force: true });
  await refreshUntil(page, () => expect(
    workflowRow(page, 'research-workflow').locator('[data-workflow-state="unavailable"]'),
  ).toHaveText('Unavailable', { timeout: 1500 }));

  await expect(workflowRow(page, 'research-workflow').locator('[data-workflow-state="count"]')).toHaveCount(0);
  await assertNoConsoleErrors(page);
});

test('a focused navigation link keeps focus and the open mobile menu through a refresh', async ({ page, request }, testInfo) => {
  await openLiveRoster(page);
  await openSidebarIfNeeded(page, testInfo.project.name);
  const jobs = page.locator('#sidebar a[href="/newsletter/jobs"]');
  await jobs.focus();
  const focused = await jobs.elementHandle();

  await applyLiveChange(request, 'navigation-membership');
  await refreshUntil(page, () => expect(workflowRow(page, 'research-workflow')).toContainText('Research updated', { timeout: 1500 }));

  expect(await focused!.evaluate((node) => node.isConnected)).toBe(true);
  await expect(jobs).toBeFocused();
  expect(await page.evaluate(() => (document.getElementById('sidebar') as HTMLElement).inert)).toBe(false);
  if (testInfo.project.name.startsWith('mobile')) {
    await expect(page.locator('#mobile-menu-button')).toHaveAttribute('aria-expanded', 'true');
    await expect(page.locator('#overlay')).toBeVisible();
  }
  // The new workspace link would shift the focused item, so it waits for the blur.
  await expect(page.locator('#sidebar a[href="/newsletter/workspaces"]')).toHaveCount(0);

  await jobs.evaluate((node) => (node as HTMLElement).blur());
  await page.clock.runFor(50);
  await expect(page.locator('#sidebar a[href="/newsletter/workspaces"]')).toHaveCount(1);
  await assertNoConsoleErrors(page);
});
