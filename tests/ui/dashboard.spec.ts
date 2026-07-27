import { expect, test, type Page } from '@playwright/test';

import { assertNoConsoleErrors, assertNoLayoutIssues, installConsoleErrorGate } from './layout';

function dashboardScreenshotMasks(page: Page) {
  const attentionQueue = page.locator('main > div > div').filter({
    has: page.getByText('Attention Queue', { exact: true }),
  }).first();
  return [
    attentionQueue.getByText('advisor', { exact: true }),
    attentionQueue.getByText('proposed', { exact: true }),
    attentionQueue.getByText('floated', { exact: true }),
  ];
}

test.beforeEach(async ({ page }, testInfo) => {
  installConsoleErrorGate(page);
  await page.addInitScript((theme) => {
    if (!localStorage.getItem('theme')) localStorage.setItem('theme', theme);
  }, testInfo.project.name.endsWith('dark') ? 'dark' : 'light');
});

test('dashboard reports selected group pipeline and durable job semantics', async ({ page }) => {
  await page.goto('/newsletter/');
  await expect(page.getByText('2 agents')).toBeVisible();
  await expect(page.getByText('Blueprint: advisor')).toBeVisible();
  await expect(page.getByText('copilot')).toBeVisible();
  await expect(page.getByRole('link', { name: 'waiting for memory' })).toBeVisible();
  await expect(page.getByRole('link', { name: /Advisor/ }).first()).toHaveAttribute('href', '/newsletter/agents/advisor/profile');
  await expect(page.locator('body')).not.toContainText('Add Instance');
  await assertNoLayoutIssues(page);
  await expect(page).toHaveScreenshot('dashboard.png', {
    fullPage: true,
    mask: dashboardScreenshotMasks(page),
  });
  await assertNoConsoleErrors(page);
});

test('jobs expose waiting, failed artifact, diagnostics hash, and empty state', async ({ page }) => {
  await page.goto('/newsletter/jobs');
  await expect(page.getByRole('heading', { name: 'Jobs in Newsletter' })).toBeVisible();
  await expect(page.getByText('Waiting for memory')).toBeVisible();
  await expect(page.getByText('Failed')).toBeVisible();
  await expect(page.locator('body')).not.toContainText('22222222222222222222222222222222');
  await assertNoLayoutIssues(page);

  await page.locator('div.bg-white').filter({ hasText: 'Waiting for memory' }).getByRole('link', { name: 'Details' }).press('Enter');
  await expect(page).toHaveURL(/job-waiting$/);
  await expect(page.getByText('Memory: Channel: Brand Strategy')).toBeVisible();
  await assertNoLayoutIssues(page);
  await expect(page).toHaveScreenshot('waiting-job.png', { fullPage: true });

  await page.goto('/newsletter/jobs/job-failed');
  await expect(page.getByRole('link', { name: 'Failed memory snapshot' })).toBeVisible();
  await expect(page.getByText(/Memory hash:/)).not.toBeVisible();
  await page.getByText('Diagnostics').press('Enter');
  await expect(page.getByText(/Memory hash: 2222/)).toBeVisible();
  const stdoutLog = page.getByText(/^Stdout log:/);
  const stderrLog = page.getByText(/^Stderr log:/);
  await expect(stdoutLog).toHaveText(/advisor-job-failed\.out$/);
  await expect(stderrLog).toHaveText(/advisor-job-failed\.err$/);
  await assertNoLayoutIssues(page);
  // Log paths are absolute and vary per checkout, so compare them as text only.
  await expect(page).toHaveScreenshot('failed-job.png', { fullPage: true, mask: [stdoutLog, stderrLog] });

  await page.goto('/research/jobs');
  await expect(page.getByRole('heading', { name: 'Jobs in Research' })).toBeVisible();
  await expect(page.getByText('No jobs found.')).toBeVisible();
  await assertNoLayoutIssues(page);
  await assertNoConsoleErrors(page);
});