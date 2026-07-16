import { expect, test, type Page } from '@playwright/test';

test.beforeEach(async ({ page }, testInfo) => {
  await page.addInitScript((theme) => localStorage.setItem('theme', theme), testInfo.project.name.endsWith('dark') ? 'dark' : 'light');
});

async function expectPageFits(page: Page) {
  const geometry = await page.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    content: document.documentElement.scrollWidth,
    clipped: [...document.querySelectorAll<HTMLElement>('main h1, main h2, main a, main button')]
      .filter((element) => element.getAttribute('aria-label') !== 'Dismiss pipeline tip')
      .filter((element) => element.scrollWidth > element.clientWidth + 1 || element.scrollHeight > element.clientHeight + 1)
      .map((element) => element.textContent?.trim()),
  }));
  expect(geometry.content).toBeLessThanOrEqual(geometry.viewport + 1);
  expect(geometry.clipped).toEqual([]);
}

test('dashboard reports selected group pipeline and durable job semantics', async ({ page }) => {
  await page.goto('/newsletter/');
  await expect(page.getByText('2 agents')).toBeVisible();
  await expect(page.getByText('Blueprint: advisor')).toBeVisible();
  await expect(page.getByText('copilot')).toBeVisible();
  await expect(page.getByRole('link', { name: 'waiting for memory' })).toBeVisible();
  await expect(page.getByRole('link', { name: /Advisor/ }).first()).toHaveAttribute('href', '/newsletter/agents/advisor/profile');
  await expect(page.locator('body')).not.toContainText('Add Instance');
  await expectPageFits(page);
  await expect(page).toHaveScreenshot('dashboard.png', { fullPage: true });
});

test('jobs expose waiting, failed artifact, diagnostics hash, and empty state', async ({ page }) => {
  await page.goto('/newsletter/jobs');
  await expect(page.getByRole('heading', { name: 'Jobs in Newsletter' })).toBeVisible();
  await expect(page.getByText('Waiting for memory')).toBeVisible();
  await expect(page.getByText('Failed')).toBeVisible();
  await expect(page.locator('body')).not.toContainText('22222222222222222222222222222222');

  await page.locator('div.bg-white').filter({ hasText: 'Waiting for memory' }).getByRole('link', { name: 'Details' }).press('Enter');
  await expect(page).toHaveURL(/job-waiting$/);
  await expect(page.getByText('Memory: Channel: Brand Strategy')).toBeVisible();
  await expect(page).toHaveScreenshot('waiting-job.png', { fullPage: true });

  await page.goto('/newsletter/jobs/job-failed');
  await expect(page.getByRole('link', { name: 'Failed memory snapshot' })).toBeVisible();
  await expect(page.getByText(/Memory hash:/)).not.toBeVisible();
  await page.getByText('Diagnostics').press('Enter');
  await expect(page.getByText(/Memory hash: 2222/)).toBeVisible();
  await expectPageFits(page);
  await expect(page).toHaveScreenshot('failed-job.png', { fullPage: true });

  await page.goto('/research/jobs');
  await expect(page.getByRole('heading', { name: 'Jobs in Research' })).toBeVisible();
  await expect(page.getByText('No jobs found.')).toBeVisible();
});