import { expect, test } from '@playwright/test';

import { assertNoConsoleErrors, assertNoTailwindCdnRequests, installBasePageSetup } from './layout';

// setup_complete.html is a standalone document (it does not extend base.html),
// so it needs its own regression: no Tailwind CDN dependency and a rendered,
// non-overflowing layout on both desktop and mobile viewports.
test.beforeEach(async ({ page }, testInfo) => {
  await installBasePageSetup(page, testInfo.project.name.endsWith('dark') ? 'dark' : 'light');
});

test('setup completion page renders its themed layout without the Tailwind CDN', async ({ page }) => {
  await page.goto('/setup/complete/newsletter');

  await expect(page.getByRole('heading', { name: 'Now go outside and touch grass for a while' })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Or, add another agent team →' })).toBeVisible();

  const overflowsViewport = await page.evaluate(() => {
    const root = document.documentElement;
    return root.scrollWidth > root.clientWidth + 1;
  });
  expect(overflowsViewport).toBe(false);

  await assertNoTailwindCdnRequests(page);
  await assertNoConsoleErrors(page);
});
