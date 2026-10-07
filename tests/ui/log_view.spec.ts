import AxeBuilder from '@axe-core/playwright';
import { expect, test, type APIRequestContext, type Locator, type Page } from '@playwright/test';
import { access, mkdir, rm, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { assertNoConsoleErrors, assertNoLayoutIssues, installBasePageSetup } from './layout';

const runtimeRoot = path.resolve('tests/ui/.runtime/current');
const newsletterLogsRoot = path.join(runtimeRoot, 'teams', 'newsletter', 'logs');
const evidenceRoot = path.resolve('.superpowers', 'sdd', '2026-09-12-agent-activity-logs', 'evidence');
const activityFixture = 'agent-activity-logs';

async function resetUiRuntime(request: APIRequestContext, fixture = 'default'): Promise<void> {
  const response = await request.post('/__ui/reset', { data: { fixture } });
  expect(response.status()).toBe(204);
}

async function expectNoAxeViolations(page: Page): Promise<void> {
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze();
  expect(results.violations, JSON.stringify(results.violations, null, 2)).toEqual([]);
}

async function captureEvidence(page: Page, name: string): Promise<void> {
  await mkdir(evidenceRoot, { recursive: true });
  await page.screenshot({ path: path.join(evidenceRoot, name), fullPage: true });
}

async function fileExists(filePath: string): Promise<boolean> {
  try {
    await access(filePath);
    return true;
  } catch {
    return false;
  }
}

function activityRow(page: Page, text: string): Locator {
  return page.locator('section ol > li').filter({ hasText: text }).first();
}

function logRow(page: Page, text: string): Locator {
  return page.locator('li > a').filter({ hasText: text }).first();
}

async function openActivityFixture(page: Page, request: APIRequestContext): Promise<void> {
  await resetUiRuntime(request, activityFixture);
  await page.goto('/newsletter/agents/advisor/activity');
  await expect(page.getByRole('heading', { name: 'Activity', exact: true })).toBeVisible();
}

test.beforeEach(async ({ page, request }, testInfo) => {
  await resetUiRuntime(request);
  await installBasePageSetup(page, testInfo.project.name.endsWith('dark') ? 'dark' : 'light');
});

test.afterEach(async ({ page, request }) => {
  await page.close();
  await resetUiRuntime(request);
});

test('default reset clears opt-in reviewer logs and restores the default dashboard reviewer state', async ({ page, request }) => {
  const leakedLog = path.join(newsletterLogsRoot, '2026-09-11', 'reviewer-unrelated.out');

  await resetUiRuntime(request, activityFixture);
  await expect.poll(async () => fileExists(leakedLog)).toBe(true);

  await resetUiRuntime(request);
  await expect.poll(async () => fileExists(leakedLog)).toBe(false);

  await page.goto('/newsletter/');

  const reviewerLink = page.getByRole('link', { name: 'Reviewer', exact: true }).first();
  const reviewerCard = reviewerLink.locator('xpath=ancestor::div[contains(@class, "rounded-lg") and contains(@class, "border")][1]');
  await expect(reviewerCard).toContainText('never run');
  await expect(reviewerCard.getByRole('link', { name: 'Running', exact: true })).toBeVisible();
  await expect(reviewerCard.locator('a[href*="/logs/view?path="]')).toHaveCount(0);
  await expect(page.locator('main')).toContainText('4 agents · 0 healthy · 3 never run · 2 running');
  await assertNoLayoutIssues(page);
  await assertNoConsoleErrors(page);
});

test('ui reset rejects unsupported fixture names without mutating runtime files', async ({ request }) => {
  const preserved = path.join(newsletterLogsRoot, 'fixture-preserved.out');

  await mkdir(newsletterLogsRoot, { recursive: true });
  await writeFile(preserved, 'keep me\n', 'utf8');

  const response = await request.post('/__ui/reset', { data: { fixture: 'missing-fixture' } });

  expect(response.status()).toBe(400);
  await expect.poll(async () => fileExists(preserved)).toBe(true);
  expect(await response.json()).toEqual({ detail: 'Unsupported fixture' });
});

test('empty logs tabs use the approved copy', async ({ page, request }) => {
  await resetUiRuntime(request, activityFixture);
  await rm(newsletterLogsRoot, { recursive: true, force: true });
  await mkdir(newsletterLogsRoot, { recursive: true });

  await page.goto('/newsletter/agents/advisor/logs');

  await expect(page.getByRole('heading', { name: 'Execution Logs', exact: true })).toBeVisible();
  await expect(page.getByText('No logs found.', { exact: true })).toBeVisible();
  await expect(page.getByText('No logs yet', { exact: true })).toHaveCount(0);
  await assertNoLayoutIssues(page);
  await assertNoConsoleErrors(page);
});

test('logs keep the heading count aligned and dark dividers matched to the frame', async ({ page, request }, testInfo) => {
  await resetUiRuntime(request, activityFixture);
  await page.goto('/newsletter/agents/advisor/logs');

  const heading = page.getByRole('heading', { name: 'Execution Logs', exact: true });
  const count = page.getByText(/^\d+ files?$/).first();
  const headerRow = heading.locator('xpath=..');

  for (const viewport of [
    { width: 1440, height: 1000 },
    { width: 390, height: 844 },
  ]) {
    await page.setViewportSize(viewport);
    await expect(heading).toBeVisible();
    await expect(count).toBeVisible();

    const headingBox = await heading.boundingBox();
    const countBox = await count.boundingBox();
    const headerRowBox = await headerRow.boundingBox();

    expect(headingBox).not.toBeNull();
    expect(countBox).not.toBeNull();
    expect(headerRowBox).not.toBeNull();
    expect(Math.abs((headingBox!.y + headingBox!.height) - (countBox!.y + countBox!.height))).toBeLessThanOrEqual(4);
    expect(countBox!.x).toBeGreaterThanOrEqual(headingBox!.x + headingBox!.width);
    expect(Math.abs((countBox!.x + countBox!.width) - (headerRowBox!.x + headerRowBox!.width))).toBeLessThanOrEqual(4);
  }

  if (testInfo.project.name.endsWith('dark')) {
    const colors = await page.locator('ul[role="list"]').first().evaluate((list) => {
      const secondRow = list.querySelectorAll('li')[1] as HTMLElement | undefined;
      return {
        frame: getComputedStyle(list).borderTopColor,
        divider: secondRow ? getComputedStyle(secondRow).borderTopColor : '',
      };
    });
    expect(colors.divider).toBe(colors.frame);
  }

  await assertNoLayoutIssues(page);
  await assertNoConsoleErrors(page);
});

test('activity and logs preserve same-agent round trips, browser Back, and responsive disclosures', async ({ page, request }, testInfo) => {
  await openActivityFixture(page, request);

  const longReport = activityRow(page, 'Triage identified and documented the violated invariant');
  const toggle = longReport.locator('[data-report-toggle]');
  const reportText = longReport.locator('[data-report-text]');
  const outputLink = longReport.getByRole('link', { name: 'Output', exact: true });
  const errorLink = longReport.getByRole('link', { name: 'Error', exact: true });
  const fullLongReport = [
    'Triage identified and documented the violated invariant. Canonical path resolution runs before the reparse-ancestor check, so symlink and junction ancestors can become invisible to the validator. The proposed repair preserves lexical ancestry until safety validation is complete.',
    'Evidence: configuration.models._path_from_config resolves lexical paths before configuration.paths.validate_resolved_paths. The existing-directory validator also resolves before checking for reparse points.',
    'Record the original path before resolution, reject unsafe ancestors, and retain the current canonical overlap checks. Cover directory symlinks, Windows junctions, missing safe descendants, and ordinary relative paths with focused regressions.',
    'No implementation was made in this run. Keep the ticket in progress for the implementation handoff.',
  ].join('\n\n');
  await expect(toggle).toHaveText('Show more');
  await expect(toggle).toHaveAttribute('aria-expanded', 'false');
  await toggle.focus();
  await page.keyboard.press('Enter');
  await expect(toggle).toHaveAttribute('aria-expanded', 'true');
  await expect(toggle).toHaveText('Show less');
  await expect(reportText).toHaveText(fullLongReport);
  const outputHref = await outputLink.getAttribute('href');
  const errorHref = await errorLink.getAttribute('href');
  expect(outputHref).not.toBeNull();
  expect(errorHref).not.toBeNull();

  const viewports = testInfo.project.name.startsWith('mobile-')
    ? [{ width: 390, height: 844 }, { width: 320, height: 844 }]
    : [{ width: 1440, height: 1000 }, { width: 768, height: 1024 }, { width: 390, height: 844 }, { width: 320, height: 844 }];
  for (const viewport of viewports) {
    await page.setViewportSize(viewport);
    await expect(toggle).toHaveAttribute('aria-expanded', 'true');
    await expect(reportText).toHaveText(fullLongReport);
    await expect(outputLink).toBeVisible();
    await expect(errorLink).toBeVisible();
    await expect(outputLink).toHaveAttribute('href', outputHref!);
    await expect(errorLink).toHaveAttribute('href', errorHref!);
    const textMetrics = await reportText.evaluate((element) => {
      const target = element as HTMLElement;
      const style = getComputedStyle(target);
      return {
        overflow: style.overflow,
        lineClamp: style.getPropertyValue('-webkit-line-clamp'),
        clientHeight: target.clientHeight,
        scrollHeight: target.scrollHeight,
      };
    });
    expect(textMetrics.overflow).not.toBe('hidden');
    expect(textMetrics.lineClamp === '' || textMetrics.lineClamp === 'none').toBe(true);
    expect(textMetrics.scrollHeight - textMetrics.clientHeight).toBeLessThanOrEqual(1);
    await assertNoLayoutIssues(page);
    await expectNoAxeViolations(page);
  }
  await toggle.focus();
  await page.keyboard.press('Enter');
  await expect(toggle).toHaveAttribute('aria-expanded', 'false');
  await expect(outputLink).toBeVisible();
  await expect(errorLink).toBeVisible();
  await expect(outputLink).toHaveAttribute('href', outputHref!);
  await expect(errorLink).toHaveAttribute('href', errorHref!);
  await page.setViewportSize(
    testInfo.project.name.startsWith('mobile-')
      ? { width: 390, height: 844 }
      : { width: 1440, height: 1000 },
  );

  await outputLink.click();
  await expect(page).toHaveURL(/source=activity/);
  const backToActivity = page.getByRole('link', { name: /Back to Activity/ });
  await expect(backToActivity).toBeVisible();
  await expect(page.locator('[data-log-content]')).toContainText('Daily control-plane triage');
  await backToActivity.click();
  await expect(page).toHaveURL(/\/newsletter\/agents\/advisor\/activity$/);

  await longReport.getByRole('link', { name: 'Error', exact: true }).click();
  await expect(page.locator('[data-log-content]')).toContainText('Session completed.');
  await page.goBack();
  await expect(page).toHaveURL(/\/newsletter\/agents\/advisor\/activity$/);
  await expect(toggle).toHaveAttribute('aria-expanded', 'false');
  await toggle.click();
  await expect(toggle).toHaveAttribute('aria-expanded', 'true');

  const historicRow = activityRow(page, 'Historical note without provenance remains visible.');
  await expect(historicRow).toContainText('No logs available');
  await expect(historicRow.getByRole('link', { name: 'Output', exact: true })).toHaveCount(0);

  const runningRow = activityRow(page, 'Reviewing current configuration and runtime changes.');
  await expect(runningRow).toContainText('No logs yet');
  await expect(runningRow.getByRole('button', { name: 'Show more', exact: true })).toBeHidden();

  const logsTab = page.getByRole('tab', { name: 'Logs', exact: true });
  await logsTab.click();
  await expect(page).toHaveURL(/\/newsletter\/agents\/advisor\/logs$/);
  await expect(page.getByRole('heading', { name: 'Execution Logs', exact: true })).toBeVisible();

  const specialLog = logRow(page, 'advisor-demo & łog.out');
  await expect(specialLog).toBeVisible();
  await specialLog.click();
  await expect(page).toHaveURL(/source=logs/);
  await expect(page.getByRole('link', { name: /Back to Logs/ })).toBeVisible();
  await expect(page.locator('[data-log-content]')).toContainText('special encoded filename');
  await page.goBack();
  await expect(page).toHaveURL(/\/newsletter\/agents\/advisor\/logs$/);

  const longBasename = /ultralongunbrokenlogbasenamefornarrowlayoutverification/;
  const longLog = page.getByRole('link', { name: longBasename });
  await expect(longLog).toBeVisible();
  await longLog.click();
  await expect(page.locator('[data-log-content]')).toContainText('UNBROKENCONTENT_');
  await page.getByRole('link', { name: /Back to Logs/ }).click();
  await expect(page).toHaveURL(/\/newsletter\/agents\/advisor\/logs$/);

  await page.goto('/newsletter/logs');
  await expect(page.getByRole('heading', { name: 'Execution Logs', exact: true })).toBeVisible();
  await page.getByRole('link', { name: 'advisor-demo & łog.out', exact: true }).click();
  await expect(page.getByRole('link', { name: /Back to logs/ })).toBeVisible();
  await assertNoConsoleErrors(page);
});

test('short one-line summaries collapse only when rendered height actually overflows', async ({ page, request }) => {
  await openActivityFixture(page, request);

  const shortOverflowReport = activityRow(page, 'Lexical ancestry validation keeps junction visibility across nested workspace recovery boundaries while preserving ordinary relative path checks.');
  const toggle = shortOverflowReport.locator('[data-report-toggle]');
  const text = shortOverflowReport.locator('[data-report-text]');

  await page.setViewportSize({ width: 320, height: 844 });
  await expect(toggle).toBeVisible();
  await expect(toggle).toBeEnabled();
  await expect(toggle).toHaveAttribute('aria-expanded', 'false');
  await expect(toggle).toHaveText('Show more');
  await toggle.focus();
  await page.keyboard.press('Enter');
  await expect(toggle).toHaveAttribute('aria-expanded', 'true');
  await expect(toggle).toHaveText('Show less');
  await expect(text).toContainText('preserving ordinary relative path checks.');

  await page.setViewportSize({ width: 1440, height: 1000 });
  await expect(toggle).toBeHidden();
  await expect(text).toContainText('Lexical ancestry validation keeps junction visibility across nested workspace recovery boundaries while preserving ordinary relative path checks.');
  await assertNoLayoutIssues(page);
  await assertNoConsoleErrors(page);
});

test('activity and logs snapshots match the approved states without broad masking', async ({ page, request }, testInfo) => {
  await openActivityFixture(page, request);
  await expect(page).toHaveScreenshot('agent-activity.png', { fullPage: true });
  if (testInfo.project.name === 'desktop-dark') {
    await captureEvidence(page, 'activity-desktop-actual.png');
  }
  if (testInfo.project.name === 'mobile-dark') {
    await captureEvidence(page, 'activity-mobile-actual.png');
  }

  const toggle = activityRow(page, 'Triage identified and documented the violated invariant').locator('[data-report-toggle]');
  await toggle.click();
  await expect(page).toHaveScreenshot('agent-activity-expanded.png', { fullPage: true });
  if (testInfo.project.name === 'desktop-dark') {
    await captureEvidence(page, 'activity-expanded-desktop-actual.png');
  }
  if (testInfo.project.name === 'mobile-dark') {
    await captureEvidence(page, 'activity-expanded-mobile-actual.png');
  }

  await page.getByRole('tab', { name: 'Logs', exact: true }).click();
  await expect(page).toHaveScreenshot('agent-logs.png', { fullPage: true });
  if (testInfo.project.name === 'desktop-dark') {
    await captureEvidence(page, 'logs-desktop-actual.png');
  }
  if (testInfo.project.name === 'mobile-dark') {
    await captureEvidence(page, 'logs-mobile-actual.png');
  }
  await assertNoLayoutIssues(page);
  await assertNoConsoleErrors(page);
});

test('logs preserve readable output without active log markup', async ({ page }, testInfo) => {
  await installBasePageSetup(page, testInfo.project.name.endsWith('dark') ? 'dark' : 'light');
  const logs = path.resolve('tests/ui/.runtime/current/teams/newsletter/logs/2026-07-16');
  await mkdir(logs, { recursive: true });
  const unsafe = '<script>window.logExecuted=1</script><img src="/log-resource" onerror="window.logExecuted=1">';
  const cases = [
    { name: 'markdown.out', content: '# Readable\n\n**Restored**\n\n' + unsafe, truncated: false },
    { name: 'events.out', content: JSON.stringify({ type: 'tool.execution_start', data: { arguments: 'bad' } }) + '\n' + JSON.stringify({ type: 'assistant.message', data: { content: '# Readable\n\n**Restored**\n\n' + unsafe } }), truncated: false },
    { name: 'long.out', content: '# Readable\n\n' + 'Preview content\n'.repeat(10000), truncated: true },
    { name: 'stderr.err', content: unsafe, truncated: false },
  ];
  await page.addInitScript(() => { (window as Window & { logExecuted?: number }).logExecuted = 0; });
  const injectedRequests: string[] = [];
  page.on('request', request => {
    if (request.url().includes('/log-resource')) injectedRequests.push(request.url());
  });
  for (const sample of cases) {
    const filePath = path.join(logs, `log-view-${testInfo.project.name}-${sample.name}`);
    await writeFile(filePath, sample.content, 'utf8');
    await page.goto('/newsletter/logs/view?path=' + encodeURIComponent(filePath));
    const content = page.locator('[data-log-content]');
    await expect(content).toBeVisible();
    await expect(content.locator('script, img, iframe, form, style')).toHaveCount(0);
    expect(await page.evaluate(() => (window as Window & { logExecuted?: number }).logExecuted)).toBe(0);
    expect(injectedRequests).toEqual([]);
    await expect(page.getByRole('status')).toHaveCount(sample.truncated ? 1 : 0);
    if (sample.name !== 'stderr.err') await expect(content.locator('h1')).toHaveText('Readable');
    if (sample.name === 'events.out' || sample.name === 'markdown.out') {
      await expect(content.locator('strong')).toHaveText('Restored');
    }
    await assertNoLayoutIssues(page);
    await page.screenshot({ path: testInfo.outputPath(sample.name + '.png'), fullPage: false });
  }
});

test('log views handle empty, missing, and escaped paths without mutating return semantics', async ({ page, request }) => {
  await openActivityFixture(page, request);
  const logs = path.join(newsletterLogsRoot, '2026-09-11');
  const disappearing = path.join(logs, 'advisor-empty.out');
  const outside = path.resolve('tests/ui/outside-log-root.out');

  await writeFile(outside, 'outside root\n', 'utf8');
  try {
    await page.goto('/newsletter/logs/view?path=' + encodeURIComponent(path.join(logs, 'advisor-empty.out')) + '&agent=advisor&source=logs');
    await expect(page.getByRole('link', { name: /Back to Logs/ })).toBeVisible();
    const emptyContent = page.locator('[data-log-content]');
    await expect(emptyContent).toHaveCount(1);
    await expect(emptyContent).toHaveText('');
    await assertNoConsoleErrors(page);

    await page.goto('/newsletter/agents/advisor/logs');
    const disappearingLink = logRow(page, 'advisor-empty.out');
    await expect(disappearingLink).toBeVisible();
    await expect(disappearingLink).toHaveAttribute('href', /source=logs/);
    await rm(disappearing, { force: true });
    const missingNavigation = page.waitForNavigation();
    await disappearingLink.click();
    const missingResponse = await missingNavigation;
    expect(missingResponse?.status()).toBe(404);
    await expect(page.locator('body')).toContainText('{"detail":"Log not found"}');
    await page.goBack();
    await expect(page).toHaveURL(/\/newsletter\/agents\/advisor\/logs$/);
    await expect(page.getByRole('heading', { name: 'Execution Logs', exact: true })).toBeVisible();

    const outsideResponse = await page.goto('/newsletter/logs/view?path=' + encodeURIComponent(outside) + '&agent=advisor&source=logs');
    expect(outsideResponse?.status()).toBe(403);
    await expect(page.locator('body')).toContainText('{"detail":"Access denied"}');
    await expect(page.locator('body')).not.toContainText('outside root');
    if (outsideResponse) {
      expect(await outsideResponse.text()).not.toContain('outside root');
    }
    await page.goBack();
    await expect(page).toHaveURL(/\/newsletter\/agents\/advisor\/logs$/);
    await expect(page.getByRole('heading', { name: 'Execution Logs', exact: true })).toBeVisible();
  } finally {
    await rm(outside, { force: true });
  }
});

test.describe('javascript-disabled agent activity and log viewer', () => {
  test.use({ javaScriptEnabled: false });

  test('long reports stay expanded in HTML fallback and log returns stay scoped', async ({ page, request }) => {
    await openActivityFixture(page, request);
    const longReport = activityRow(page, 'Triage identified and documented the violated invariant');
    await expect(longReport.getByRole('button', { name: 'Show more', exact: true })).toBeHidden();
    await expect(longReport).toContainText('Evidence: configuration.models._path_from_config resolves lexical paths before configuration.paths.validate_resolved_paths.');

    await longReport.getByRole('link', { name: 'Output', exact: true }).click();
    const backToActivity = page.getByRole('link', { name: /Back to Activity/ });
    await expect(backToActivity).toBeVisible();
    await expect(backToActivity).toHaveAttribute('href', '/newsletter/agents/advisor/activity');
    await backToActivity.click();
    await expect(page).toHaveURL(/\/newsletter\/agents\/advisor\/activity$/);
    await expect(longReport).toContainText('Evidence: configuration.models._path_from_config resolves lexical paths before configuration.paths.validate_resolved_paths.');

    await longReport.getByRole('link', { name: 'Output', exact: true }).click();
    await expect(backToActivity).toBeVisible();
    await assertNoLayoutIssues(page);
    await assertNoConsoleErrors(page);
  });
});


test.describe('live log view', () => {
  const logDay = path.join(newsletterLogsRoot, '2026-07-16');
  const tailLog = path.join(logDay, 'advisor-live-tail.out');
  const viewUrl = (file: string, extra = '') => '/newsletter/logs/view?path=' + encodeURIComponent(file) + extra;

  async function change(request: APIRequestContext, name: string): Promise<void> {
    expect((await request.post('/__ui/live/change', { data: { case: name } })).status()).toBe(204);
  }

  const content = (page: Page) => page.locator('[data-live-region="log-content"]');
  const status = (page: Page) => page.locator('[data-live-region="log-status"]');
  const pageStatus = (page: Page) => page.locator('[data-live-status][role="status"]');
  const scrollState = (page: Page) => page.locator('main').evaluate((main) => ({
    top: main.scrollTop,
    gap: main.scrollHeight - main.scrollTop - main.clientHeight,
    scrolls: main.scrollHeight > main.clientHeight,
  }));

  test('growing output follows the bottom only for a reader already following it', async ({ page, request }) => {
    await change(request, 'log-tall');
    await page.goto(viewUrl(tailLog));
    await expect(content(page)).toContainText('tail line 120');
    await page.locator('main').evaluate((main) => { main.scrollTop = main.scrollHeight; });
    expect((await scrollState(page)).scrolls).toBe(true);

    await change(request, 'log-appended');

    await expect(content(page)).toContainText('appended line 10');
    await expect.poll(async () => (await scrollState(page)).gap).toBeLessThanOrEqual(2);
  });

  test('a reader scrolled away from the bottom keeps their position while output grows', async ({ page, request }) => {
    await change(request, 'log-tall');
    await page.goto(viewUrl(tailLog));
    await expect(content(page)).toContainText('tail line 120');
    await page.locator('main').evaluate((main) => { main.scrollTop = 200; });
    const before = await scrollState(page);
    expect(before.scrolls).toBe(true);
    expect(before.gap).toBeGreaterThan(50);
    const main = await page.locator('main').elementHandle();

    await change(request, 'log-appended');

    await expect(content(page)).toContainText('appended line 10');
    expect((await scrollState(page)).top).toBe(before.top);
    expect(await main!.evaluate((node) => node.isConnected)).toBe(true);
  });

  test('selected text and its node survive growth until the selection is released', async ({ page, request }) => {
    await change(request, 'log-tall');
    await page.goto(viewUrl(tailLog));
    await expect(content(page)).toContainText('tail line 120');
    const block = await page.locator('[data-log-content] > p').first().elementHandle();
    await page.evaluate(() => {
      const target = document.querySelector('[data-log-content] > p')!;
      const range = document.createRange();
      range.selectNodeContents(target);
      const selection = window.getSelection()!;
      selection.removeAllRanges();
      selection.addRange(range);
    });
    const selected = await page.evaluate(() => String(window.getSelection()));

    await change(request, 'log-appended');
    await page.waitForTimeout(2600);

    expect(await page.evaluate(() => String(window.getSelection()))).toBe(selected);
    expect(await block!.evaluate((node) => node.isConnected)).toBe(true);
    await expect(content(page)).not.toContainText('appended line 10');

    await page.evaluate(() => window.getSelection()!.removeAllRanges());

    await expect(content(page)).toContainText('appended line 10');
    expect(await block!.evaluate((node) => node.isConnected)).toBe(true);
  });

  test('horizontal preview scroll and node identity survive growth', async ({ page }) => {
    const wide = path.join(logDay, 'advisor-live-wide.out');
    const source = '```\n' + 'W'.repeat(600) + '\n```\n\nfirst paragraph\n';
    await mkdir(logDay, { recursive: true });
    await writeFile(wide, source, 'utf8');
    await page.goto(viewUrl(wide));
    const block = content(page).locator('pre').first();
    await expect(block).toBeVisible();
    const handle = await block.elementHandle();
    await block.evaluate((node) => { node.scrollLeft = 150; });
    const left = await block.evaluate((node) => node.scrollLeft);
    expect(left).toBeGreaterThan(0);

    await writeFile(wide, source + '\nappended paragraph\n', 'utf8');

    await expect(content(page)).toContainText('appended paragraph');
    expect(await handle!.evaluate((node) => node.isConnected)).toBe(true);
    expect(await handle!.evaluate((node) => node.scrollLeft)).toBe(left);
  });

  test('a rotated log replaces the preview and an oversized one reports truncation', async ({ page, request }) => {
    await change(request, 'log-tall');
    await page.goto(viewUrl(tailLog));
    await expect(content(page)).toContainText('tail line 120');
    await expect(status(page)).toHaveText('');

    await change(request, 'log-truncated');

    await expect(content(page)).toContainText('rotated line');
    await expect(content(page)).not.toContainText('tail line 120');

    await change(request, 'log-oversized');

    await expect(status(page)).toContainText('Preview truncated');
    await expect(page.getByRole('status').filter({ hasText: 'Preview truncated' })).toHaveCount(1);
  });

  test('a disappearing log reports the page as unavailable and keeps its last content', async ({ page, request }) => {
    await change(request, 'log-tall');
    await page.goto(viewUrl(tailLog));
    await expect(content(page)).toContainText('tail line 120');

    await change(request, 'log-removed');

    await expect(pageStatus(page)).toBeVisible();
    await expect(pageStatus(page)).toContainText('This page is no longer available');
    await expect(content(page)).toContainText('tail line 120');
  });

  test('snapshot requests that change the selectors outside the allowed scope are rejected', async ({ request }) => {
    const outside = path.resolve('tests/ui/outside-live-log.out');
    await writeFile(outside, 'outside secret\n', 'utf8');
    const owned = path.join(logDay, 'advisor-job-failed.out');
    try {
      const cases = [
        viewUrl(outside, '&__live=1'),
        viewUrl(outside, '&agent=advisor&source=logs&__live=1'),
        viewUrl(owned, '&agent=reviewer&source=logs&__live=1'),
        viewUrl(path.join(logDay, '..', '..', '..', '..', 'outside-live-log.out'), '&__live=1'),
      ];
      for (const url of cases) {
        const response = await request.get(url);
        expect(response.status(), url).toBe(403);
        expect(await response.text()).not.toContain('outside secret');
      }
      expect((await request.get(viewUrl(owned, '&agent=advisor&__live=1'))).status()).toBe(400);
      expect((await request.get(viewUrl(owned, '&agent=advisor&source=logs&__live=1'))).status()).toBe(200);
    } finally {
      await rm(outside, { force: true });
    }
  });

  test('the log listing gains and loses entries in place', async ({ page, request }) => {
    await page.goto('/newsletter/logs');
    const listing = page.locator('[data-live-region="logs-list"]');
    await expect(listing.getByRole('link', { name: 'advisor-job-failed.err', exact: true })).toBeVisible();
    await listing.evaluate((node) => { (node as unknown as { __kept: boolean }).__kept = true; });

    await change(request, 'log-listing-membership');

    await expect(listing.getByRole('link', { name: 'advisor-live-membership.out', exact: true })).toBeVisible();
    await expect(listing.getByRole('link', { name: 'advisor-job-failed.err', exact: true })).toHaveCount(0);
    expect(await listing.evaluate((node) => (node as unknown as { __kept?: boolean }).__kept)).toBe(true);
  });
});