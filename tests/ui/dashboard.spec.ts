import { expect, test, type Page } from '@playwright/test';

import { expectLayoutIntegrity } from './layout';

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
  await expectLayoutIntegrity(page);
  await expect(page).toHaveScreenshot('dashboard.png', {
    fullPage: true,
    mask: dashboardScreenshotMasks(page),
  });
});

test('jobs expose waiting, failed artifact, diagnostics hash, and empty state', async ({ page }) => {
  await page.goto('/newsletter/jobs');
  await expect(page.getByRole('heading', { name: 'Jobs in Newsletter' })).toBeVisible();
  await expect(page.getByText('Waiting for memory')).toBeVisible();
  await expect(page.getByText('Failed')).toBeVisible();
  await expect(page.locator('body')).not.toContainText('22222222222222222222222222222222');
  await expectLayoutIntegrity(page);

  await page.locator('div.bg-white').filter({ hasText: 'Waiting for memory' }).getByRole('link', { name: 'Details' }).press('Enter');
  await expect(page).toHaveURL(/job-waiting$/);
  await expect(page.getByText('Memory: Channel: Brand Strategy')).toBeVisible();
  await expectLayoutIntegrity(page);
  await expect(page).toHaveScreenshot('waiting-job.png', { fullPage: true });

  await page.goto('/newsletter/jobs/job-failed');
  await expect(page.getByRole('link', { name: 'Failed memory snapshot' })).toBeVisible();
  await expect(page.getByText(/Memory hash:/)).not.toBeVisible();
  await page.getByText('Diagnostics').press('Enter');
  await expect(page.getByText(/Memory hash: 2222/)).toBeVisible();
  await expectLayoutIntegrity(page);
  await expect(page).toHaveScreenshot('failed-job.png', { fullPage: true });

  await page.goto('/research/jobs');
  await expect(page.getByRole('heading', { name: 'Jobs in Research' })).toBeVisible();
  await expect(page.getByText('No jobs found.')).toBeVisible();
  await expectLayoutIntegrity(page);
});