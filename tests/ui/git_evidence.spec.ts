import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import path from 'node:path';

import { expect, test, type Page } from '@playwright/test';

import { expectBodyFocus, tabTo } from './keyboard';
import { assertNoConsoleErrors, assertNoLayoutIssues, installBasePageSetup } from './layout';

const fixtureIndexPath = path.join(__dirname, '.runtime', 'current', 'fixture-index', 'git-evidence.json');

// Seeding this fixture runs the real capture service against a real repository
// three times, which is far slower than an ordinary UI action but bounded.
const RESET_TIMEOUT_MS = 120_000;

// Measured on this worktree: the seeding reset costs 12-14s in beforeEach and
// the same again in afterEach, which both draw on the test's own budget. This
// spec therefore gets its own bounded deadline; the global 30s stays untouched.
test.describe.configure({ timeout: 90_000 });

type EvidenceEntry = {
  artifact_id: string;
  patch_sha256: string;
  patch_bytes: number;
  base_commit: string;
  end_commit: string;
  job_id: string;
};

type FixtureIndex = {
  ticket_id: string;
  ticket_href: string;
  current: EvidenceEntry;
  bulk: EvidenceEntry;
  empty: EvidenceEntry;
  reviewer_job_id: string;
  renamed_path: string;
  original_path: string;
  binary_path: string;
  bulk_path: string;
  added_bulk_path: string;
};

async function fixtureIndex(): Promise<FixtureIndex> {
  return JSON.parse(await readFile(fixtureIndexPath, 'utf8')) as FixtureIndex;
}

function diffHref(index: FixtureIndex, entry: EvidenceEntry, source: 'ticket' | 'job'): string {
  return `${index.ticket_href}/artifacts/${entry.artifact_id}/diff?source=${source}`;
}

function patchHref(index: FixtureIndex, entry: EvidenceEntry): string {
  return `${index.ticket_href}/artifacts/${entry.artifact_id}/patch`;
}

function shortId(objectId: string): string {
  return objectId.slice(0, 12);
}

// The repository identity is derived from the runtime directory's own inode, so
// it is the one value on the page that a fresh runtime legitimately changes.
function repositoryValue(page: Page) {
  return page.locator('.git-evidence-meta > div:first-child > dd');
}

function baseValue(page: Page) {
  return page.locator('.git-evidence-meta > div:nth-child(2) > dd');
}

function endValue(page: Page) {
  return page.locator('.git-evidence-meta > div:nth-child(3) > dd');
}

test.beforeEach(async ({ page, request }, testInfo) => {
  const reset = await request.post('/__ui/reset', {
    data: { fixture: 'git-evidence' },
    timeout: RESET_TIMEOUT_MS,
  });
  expect(reset.status()).toBe(204);
  await installBasePageSetup(page, testInfo.project.name.endsWith('dark') ? 'dark' : 'light');
});

test.afterEach(async ({ page, request }) => {
  await page.close();
  expect((await request.post('/__ui/reset', { timeout: RESET_TIMEOUT_MS })).status()).toBe(204);
});

test('closed ticket opens its retained diff and returns', async ({ page, request }) => {
  const index = await fixtureIndex();
  const ticketUrl = '/newsletter/workflows/delivery/tickets/fixture-git-evidence';
  await page.goto(ticketUrl);
  const overview = page.locator('[data-ticket-panel="overview"]');
  await overview.getByRole('link', { name: 'View diff', exact: true }).click();
  await expect(page.getByRole('navigation', { name: 'Changed files', exact: true })).toBeVisible();
  await expect(page.getByText('Local commits', { exact: true })).toBeVisible();
  const downloadUrl = await page.getByRole('link', { name: 'Download patch', exact: true }).getAttribute('href');
  expect(downloadUrl).toBeTruthy();
  const patch = await request.get(downloadUrl!);
  expect(patch.ok()).toBeTruthy();
  expect(patch.headers()['x-content-type-options']).toBe('nosniff');
  const body = await patch.body();
  expect(body.toString('utf8')).toContain('diff --git');
  // The expected digest comes from the retained manifest's own decoded bytes,
  // read from disk, so the route is never compared against itself.
  expect(createHash('sha256').update(body).digest('hex')).toBe(index.current.patch_sha256);
  expect(body.length).toBe(index.current.patch_bytes);
  await assertNoLayoutIssues(page);
  await assertNoConsoleErrors(page);
  await page.getByRole('link', { name: 'Back to ticket', exact: true }).click();
  await expect(page).toHaveURL(ticketUrl);
});

test('history opens an earlier result and only its producing job links it', async ({ page, request }) => {
  const index = await fixtureIndex();
  await page.goto(index.ticket_href);
  await page.getByRole('tab', { name: 'History' }).click();

  const history = page.locator('[data-ticket-panel="history"]');
  // The later reviewer reused this artifact, so History lists it twice; open
  // the entry that first accepted it.
  const accepted = history.locator('article', { hasText: 'Transition Submit bulk update accepted' });
  await accepted.locator(`a[href="${diffHref(index, index.bulk, 'ticket')}"]`).click();

  const meta = page.locator('.git-evidence-meta');
  await expect(baseValue(page)).toHaveText(shortId(index.bulk.base_commit));
  await expect(endValue(page)).toHaveText(shortId(index.bulk.end_commit));
  await expect(meta).toContainText('refs/heads/main');
  expect(index.bulk.base_commit).not.toBe(index.current.base_commit);
  expect(index.bulk.end_commit).not.toBe(index.current.end_commit);
  const earlier = await (await request.get(patchHref(index, index.bulk))).body();
  expect(createHash('sha256').update(earlier).digest('hex')).toBe(index.bulk.patch_sha256);
  expect(index.bulk.patch_sha256).not.toBe(index.current.patch_sha256);

  await page.goto(`/newsletter/jobs/${index.bulk.job_id}`);
  await expect(page.getByText('Git evidence', { exact: true })).toBeVisible();
  await page.locator(`a[href="${diffHref(index, index.bulk, 'job')}"]`).click();

  const back = page.getByRole('link', { name: 'Back to job', exact: true });
  await expect(back).toHaveAttribute('href', `/newsletter/jobs/${index.bulk.job_id}`);
  await expect(endValue(page)).toHaveText(shortId(index.bulk.end_commit));
  await back.click();
  await expect(page).toHaveURL(`/newsletter/jobs/${index.bulk.job_id}`);

  // The later reviewer only reused the artifact, so it never claims it.
  await page.goto(`/newsletter/jobs/${index.reviewer_job_id}`);
  await expect(page.getByText('Changed files', { exact: true })).toBeVisible();
  await expect(page.getByText('Git evidence', { exact: true })).toHaveCount(0);
  await expect(page.locator(`a[href*="${index.bulk.artifact_id}"]`)).toHaveCount(0);
  await expect(page.locator(`a[href*="${index.current.artifact_id}"]`)).toHaveCount(0);
  await assertNoConsoleErrors(page);
});

test('binary, renamed, oversized and empty evidence stay truthful', async ({ page, request }) => {
  const index = await fixtureIndex();

  await page.goto(diffHref(index, index.current, 'ticket'));
  const binary = page.locator(`section[aria-label="${index.binary_path}"]`);
  await expect(binary).toContainText('Binary file change. Download the patch to read it.');
  await expect(binary.locator('table')).toHaveCount(0);
  const renamed = page.locator(`section[aria-label="${index.renamed_path}"]`);
  await expect(renamed).toContainText(`from ${index.original_path}`);
  // A rename that changed no bytes is its own state, not a mode change and not
  // a file whose rows were dropped by the display budget.
  await expect(renamed).toContainText('Renamed with no content change.');
  await expect(renamed).not.toContainText('File mode changed');
  await expect(renamed).not.toContainText('No lines are shown for this file');
  const hostile = page.locator('section[aria-label="src/inert_sample.py"]');
  await expect(hostile).toContainText('<script>window.__evidenceXssFired = true</script>');
  expect(await page.evaluate(() => (window as unknown as Record<string, unknown>).__evidenceXssFired)).toBeUndefined();
  await expect(page.getByText('No net changes between these commits.', { exact: true })).toHaveCount(0);
  await expect(page.getByText('Part of this patch is not shown here.')).toHaveCount(0);

  await page.goto(diffHref(index, index.empty, 'ticket'));
  await expect(page.getByText('No net changes between these commits.', { exact: true })).toBeVisible();
  await expect(page.getByRole('navigation', { name: 'Changed files', exact: true })).toHaveCount(0);
  await expect(page.getByText('not in a form the viewer can lay out')).toHaveCount(0);
  await expect(page.getByText('is not UTF-8 text')).toHaveCount(0);
  const emptyPatch = await (await request.get(patchHref(index, index.empty))).body();
  expect(emptyPatch.length).toBe(0);
  expect(createHash('sha256').update(emptyPatch).digest('hex')).toBe(index.empty.patch_sha256);

  await page.goto(diffHref(index, index.bulk, 'ticket'));
  await expect(
    page.getByText('Part of this patch is not shown here. The download contains the complete patch.', { exact: true }),
  ).toBeVisible();
  const oversized = page.locator(`section[aria-label="${index.bulk_path}"]`);
  await expect(oversized).toContainText('No lines are shown for this file. Download the patch to read it.');
  await expect(oversized.locator('tbody tr')).toHaveCount(0);
  // An added file has a 000000 old mode, which is not a mode change.
  const oversizedAddition = page.locator(`section[aria-label="${index.added_bulk_path}"]`);
  await expect(oversizedAddition).toContainText('No lines are shown for this file. Download the patch to read it.');
  await expect(oversizedAddition).not.toContainText('File mode changed');
  const complete = await (await request.get(patchHref(index, index.bulk))).body();
  expect(complete.length).toBe(index.bulk.patch_bytes);
  expect(createHash('sha256').update(complete).digest('hex')).toBe(index.bulk.patch_sha256);
  await assertNoConsoleErrors(page);
});

test('keyboard and a 320px viewport reach the diff controls', async ({ page }) => {
  const index = await fixtureIndex();
  await page.setViewportSize({ width: 320, height: 900 });
  await page.goto(diffHref(index, index.current, 'ticket'));
  await expect(page.getByRole('navigation', { name: 'Changed files', exact: true })).toBeVisible();

  await expectBodyFocus(page);
  await tabTo(page, { role: 'link', name: 'Skip to changes' });
  const back = await tabTo(page, { role: 'link', name: 'Back to ticket' });
  await expect(back).toBeFocused();
  const download = await tabTo(page, { role: 'link', name: 'Download patch' });
  await expect(download).toBeFocused();
  const fileLink = await tabTo(page, { role: 'link', href: '#change-1' });
  await expect(fileLink).toBeFocused();
  await fileLink.press('Enter');
  await expect(page).toHaveURL(/#change-1$/);

  const scrolling = await page.evaluate(() => ({
    documentOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    scrollingPanes: Array.from(document.querySelectorAll('.git-diff-scroll'))
      .filter((pane) => pane.scrollWidth > pane.clientWidth + 1).length,
    widestHeadingOverflow: Math.max(
      ...Array.from(document.querySelectorAll<HTMLElement>('.git-evidence-file h2'))
        .map((heading) => heading.scrollWidth - heading.clientWidth),
    ),
  }));
  expect(scrolling.documentOverflow).toBeLessThanOrEqual(1);
  expect(scrolling.scrollingPanes).toBeGreaterThan(0);
  expect(scrolling.widestHeadingOverflow).toBeLessThanOrEqual(1);
  await assertNoLayoutIssues(page);
  await assertNoConsoleErrors(page);
});

async function regionFit(page: Page, selector: string) {
  return page.evaluate((target) => {
    const region = document.querySelector(target) as HTMLElement | null;
    if (region === null) {
      return null;
    }
    const bounds = region.getBoundingClientRect();
    const links = Array.from(region.querySelectorAll('a'));
    return {
      links: links.length,
      escaping: links.filter((link) => {
        const rect = link.getBoundingClientRect();
        return rect.right > bounds.right + 1 || rect.left < bounds.left - 1 || rect.width === 0;
      }).length,
      documentOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    };
  }, selector);
}

test('verified evidence links fit the ticket outputs column and the producing job', async ({ page }) => {
  const index = await fixtureIndex();
  const outputs = page.locator('[data-ticket-panel="overview"] [data-git-evidence-links]');
  const jobLinks = page.locator('[data-git-evidence-links]');

  for (const width of [1440, 320]) {
    await page.setViewportSize({ width, height: 900 });

    await page.goto(index.ticket_href);
    await expect(outputs.getByRole('link', { name: 'View diff', exact: true })).toBeVisible();
    await expect(outputs.getByRole('link', { name: 'Download patch', exact: true })).toBeVisible();
    const ticketFit = await regionFit(page, '[data-ticket-panel="overview"] [data-git-evidence-links]');
    expect(ticketFit?.links).toBe(2);
    expect(ticketFit?.escaping).toBe(0);
    expect(ticketFit?.documentOverflow).toBeLessThanOrEqual(1);
    await assertNoLayoutIssues(page);
    if (width === 320) {
      await expect(outputs).toHaveScreenshot('git-evidence-ticket-outputs-narrow.png');
    }

    await page.goto(`/newsletter/jobs/${index.current.job_id}`);
    await expect(jobLinks).toBeVisible();
    const jobFit = await regionFit(page, '[data-git-evidence-links]');
    expect(jobFit?.links).toBe(2);
    expect(jobFit?.escaping).toBe(0);
    expect(jobFit?.documentOverflow).toBeLessThanOrEqual(1);
    await assertNoLayoutIssues(page);
    if (width === 1440) {
      await expect(jobLinks).toHaveScreenshot('git-evidence-job-links.png');
    }
  }

  // Both links are reachable from the narrow column, not merely visible.
  await page.goto(index.ticket_href);
  await outputs.getByRole('link', { name: 'View diff', exact: true }).click();
  await expect(page.getByText('Local commits', { exact: true })).toBeVisible();
  await assertNoConsoleErrors(page);
});

test.describe('javascript-disabled evidence viewer', () => {
  test.use({ javaScriptEnabled: false });

  test('the retained diff opens and downloads without javascript', async ({ page, request }) => {
    const index = await fixtureIndex();
    await page.goto(index.ticket_href);
    const overview = page.locator('[data-ticket-panel="overview"]');
    await overview.locator(`a[href="${diffHref(index, index.current, 'ticket')}"]`).click();

    await expect(page.getByRole('navigation', { name: 'Changed files', exact: true })).toBeVisible();
    await expect(page.getByText('Local commits', { exact: true })).toBeVisible();
    // The viewer is what this suite owns; the ticket page's own no-script
    // layout is covered by the workflow board suite.
    await assertNoLayoutIssues(page);
    const patch = await request.get(patchHref(index, index.current));
    expect(patch.ok()).toBeTruthy();
    expect(patch.headers()['content-disposition']).toContain('committed-changes.patch');
    expect(createHash('sha256').update(await patch.body()).digest('hex')).toBe(index.current.patch_sha256);

    await page.getByRole('link', { name: 'Back to ticket', exact: true }).click();
    await expect(page).toHaveURL(index.ticket_href);
    await expect(page.locator('[data-ticket-panel="overview"]')).toContainText('Committed changes');

    // The ticket page's unrelated no-script input overflow still trips the
    // global helper here, so this measures the feature's own region instead.
    const stacking = await page.evaluate(() => {
      const region = document.querySelector('[data-ticket-panel="overview"] [data-git-evidence-links]');
      if (region === null) {
        return null;
      }
      const cell = region.closest('dd') as HTMLElement;
      const cellBounds = cell.getBoundingClientRect();
      const boxes = Array.from(region.querySelectorAll('a')).map((link) => link.getBoundingClientRect());
      return {
        links: boxes.length,
        escapingCell: boxes.filter((box) => box.width === 0 || box.right > cellBounds.right + 1 || box.left < cellBounds.left - 1).length,
        sharingALine: boxes.slice(1).filter((box, before) => box.top < boxes[before].bottom - 1).length,
      };
    });
    expect(stacking?.links).toBe(2);
    expect(stacking?.escapingCell).toBe(0);
    expect(stacking?.sharingALine).toBe(0);
  });
});

test('evidence viewer keeps the approved shell and a stable layout', async ({ page }, testInfo) => {
  const index = await fixtureIndex();
  await page.goto(diffHref(index, index.current, 'ticket'));
  await expect(page.getByRole('navigation', { name: 'Changed files', exact: true })).toBeVisible();
  await expect(page.locator('.git-evidence-meta')).toContainText('refs/heads/main');
  await page.evaluate(() => {
    if (document.activeElement instanceof HTMLElement) {
      document.activeElement.blur();
    }
  });
  await assertNoLayoutIssues(page);
  const suffix = testInfo.project.name.startsWith('mobile') ? '-mobile' : '';
  await expect(page).toHaveScreenshot(`git-evidence-diff${suffix}.png`, {
    fullPage: true,
    mask: [repositoryValue(page)],
  });
  await assertNoConsoleErrors(page);
});
