import { expect, type Locator, type Page } from '@playwright/test';

export const notices = (page: Page): Locator => page.locator('[data-live-notices]');

// The host must cost no layout while nothing has changed elsewhere.
export async function expectNoNotice(page: Page): Promise<void> {
  await expect(notices(page)).toBeEmpty();
  expect((await notices(page).boundingBox())?.height).toBe(0);
}

// One announced notice with exactly this copy, and no other.
export async function expectOnlyNotice(page: Page, copy: string): Promise<void> {
  const host = notices(page);
  await expect(host).toHaveAttribute('role', 'status');
  await expect(host).toHaveAttribute('aria-live', 'polite');
  await expect(host.locator('[data-live-notice-scope]')).toHaveCount(1);
  await expect(host.locator('[data-live-notice-scope]')).toHaveText(copy);
  await expect(host.locator('[data-live-notice-scope]')).toBeVisible();
  await expect(page.getByRole('status').filter({ hasText: copy })).toHaveCount(1);
}

// An unchanged poll answers 304 and must leave the watched DOM, including the notice host, untouched.
export async function expectQuietPolls(page: Page, watched: Locator[]): Promise<void> {
  const handles = await Promise.all(watched.map((locator) => locator.elementHandle()));
  await page.evaluate((nodes) => {
    const holder = window as unknown as { __quietMutations: number };
    holder.__quietMutations = 0;
    const observer = new MutationObserver((records) => {
      holder.__quietMutations += records.length;
    });
    for (const node of nodes) {
      observer.observe(node as Node, { subtree: true, childList: true, attributes: true, characterData: true });
    }
  }, handles);
  for (let poll = 0; poll < 2; poll += 1) {
    await page.waitForResponse((response) => response.url().includes('__live=1'));
  }
  expect(await page.evaluate(() => (window as unknown as { __quietMutations: number }).__quietMutations)).toBe(0);
}
