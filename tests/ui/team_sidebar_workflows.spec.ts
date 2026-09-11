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