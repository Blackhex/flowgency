import { expect, test, type APIRequestContext, type Locator, type Page } from '@playwright/test';

import { assertNoConsoleErrors, assertNoLayoutIssues, assertNoTailwindCdnRequests, installBasePageSetup } from './layout';

// Absolute paths vary in length per checkout; pin them to one line so they cannot reflow the page.
async function pinToSingleLine(locator: Locator) {
  await locator.evaluateAll((elements) => {
    for (const element of elements as HTMLElement[]) {
      element.style.whiteSpace = 'nowrap';
      element.style.overflow = 'hidden';
      element.style.textOverflow = 'clip';
    }
  });
}

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
  await installBasePageSetup(page, testInfo.project.name.endsWith('dark') ? 'dark' : 'light');
});

test('dashboard reports selected group pipeline and durable job semantics', async ({ page }) => {
  await page.goto('/newsletter/');
  await expect(page.getByText('4 agents')).toBeVisible();
  await expect(page.getByText('advisor', { exact: true }).first()).toBeVisible();
  await expect(page.getByText('copilot')).toBeVisible();
  await expect(page.getByRole('link', { name: 'waiting for memory' })).toBeVisible();
  await expect(page.getByRole('link', { name: /Advisor/ }).first()).toHaveAttribute('href', '/newsletter/agents/advisor/profile');
  await expect(page.locator('body')).not.toContainText('Add Instance');
  await assertNoLayoutIssues(page);
  await expect(page).toHaveScreenshot('dashboard.png', {
    fullPage: true,
    mask: dashboardScreenshotMasks(page),
  });
  await assertNoTailwindCdnRequests(page);
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
  await pinToSingleLine(stdoutLog);
  await pinToSingleLine(stderrLog);
  await assertNoLayoutIssues(page);
  // Log paths are absolute and vary per checkout, so compare them as text only.
  await expect(page).toHaveScreenshot('failed-job.png', { fullPage: true, mask: [stdoutLog, stderrLog] });

  await page.goto('/research/jobs');
  await expect(page.getByRole('heading', { name: 'Jobs in Research' })).toBeVisible();
  await expect(page.getByText('No jobs found.')).toBeVisible();
  await assertNoLayoutIssues(page);
  await assertNoConsoleErrors(page);
});

test('fleet cards expose run timing and the routine link', async ({ page }) => {
  await page.goto('/newsletter/');
  await expect(page.locator('a[href="/newsletter/agents/advisor/profile"]')).toBeVisible();
  await expect(page.locator('a[href="/newsletter/agents/builder/routines"]')).toBeVisible();
  await expect(page.getByText('last job failed').first()).toBeVisible();
  await assertNoConsoleErrors(page);
});

test.describe('inbox live regions', () => {
  const PENDING_SENTENCE = 'Routine daily-review was due at 09:00 and has not run \u2014 3h late.';
  const FLEET_KEYS = ['agent:advisor', 'agent:builder', 'agent:reviewer', 'agent:researcher'];

  async function resetRuntime(request: APIRequestContext) {
    expect((await request.post('/__ui/reset')).status()).toBe(204);
  }

  async function change(request: APIRequestContext, name: string) {
    expect((await request.post('/__ui/live/change', { data: { case: name } })).status()).toBe(204);
  }

  const fleetKeys = (page: Page) => page.locator('[data-live-region="fleet"] [data-live-key^="agent:"]')
    .evaluateAll((elements) => elements.map((element) => element.getAttribute('data-live-key')));

  async function attentionCounts(page: Page) {
    const attention = page.locator('[data-live-region="attention"]');
    const header = (await attention.locator('span.font-mono').first().textContent()) ?? '';
    const listed = await attention.locator(
      '[data-live-key^="health:"], [data-live-key^="unassigned:"], [data-live-key^="attention-issue:"]',
    ).count();
    return { header: Number(/(\d+) item/.exec(header)?.[1] ?? 0), listed };
  }

  async function queueCounts(page: Page) {
    const strip = page.locator('[data-live-region="work-queue"]');
    const text = (await strip.textContent()) ?? '';
    return { header: Number(/(\d+) queued/.exec(text)?.[1] ?? 0), listed: await strip.locator('[data-live-key^="job:"]').count() };
  }

  const healthyCount = async (page: Page) => Number(
    /(\d+) healthy/.exec((await page.locator('[data-live-key="fleet:summary"]').textContent()) ?? '')?.[1] ?? -1,
  );

  test.beforeEach(async ({ request }) => resetRuntime(request));
  test.afterEach(async ({ request }) => resetRuntime(request));

  test('a completed scheduled job clears the pending card in place without a reload', async ({ page, request }) => {
    await change(request, 'inbox-routine-pending');
    await page.goto('/newsletter/');
    await page.evaluate(() => { (window as unknown as { __kept: boolean }).__kept = true; });
    const main = await page.locator('main').elementHandle();
    const card = page.locator('[data-live-key="agent:advisor"]');
    const attention = page.locator('[data-live-region="attention"]');

    await expect(card).toHaveAttribute('data-health', 'red');
    await expect(card).toHaveAttribute('data-health-kind', 'overdue');
    await expect(card).toContainText('daily-review due 09:00');
    await expect(attention).toContainText(PENDING_SENTENCE);
    const healthyBefore = await healthyCount(page);

    await change(request, 'inbox-job-completes');

    await expect(card).toHaveAttribute('data-health', 'green');
    await expect(card).toHaveAttribute('data-health-kind', 'healthy');
    await expect(card.locator('a[aria-label="Advisor"] span[title="Healthy"]')).toHaveCount(1);
    await expect(card).not.toContainText('daily-review due 09:00');
    await expect(attention).not.toContainText(PENDING_SENTENCE);
    await expect(attention.locator('[data-live-key="health:advisor"]')).toHaveCount(0);
    await expect.poll(() => healthyCount(page)).toBe(healthyBefore + 1);
    expect(await main!.evaluate((node) => node.isConnected)).toBe(true);
    expect(await page.evaluate(() => (window as unknown as { __kept?: boolean }).__kept)).toBe(true);
    const counts = await attentionCounts(page);
    expect(counts.header).toBe(counts.listed);
  });

  test('membership create, move and remove reconcile the fleet by identity', async ({ page, request }) => {
    await page.goto('/newsletter/');
    await expect.poll(() => fleetKeys(page)).toEqual(FLEET_KEYS);
    await page.locator('[data-live-key="agent:builder"]').evaluate((element) => {
      (element as unknown as { __kept: boolean }).__kept = true;
    });

    await change(request, 'inbox-agent-added');
    await expect.poll(() => fleetKeys(page)).toEqual([...FLEET_KEYS, 'agent:scribe']);
    await expect(page.locator('[data-live-key="fleet:summary"]')).toContainText('5 agents');

    await change(request, 'inbox-agent-moved');
    await expect.poll(async () => (await fleetKeys(page))[0]).toBe('agent:builder');
    expect(await page.locator('[data-live-key="agent:builder"]').evaluate(
      (element) => (element as unknown as { __kept?: boolean }).__kept,
    )).toBe(true);

    await change(request, 'inbox-agent-removed');
    await expect.poll(() => fleetKeys(page)).toEqual(['agent:builder', 'agent:advisor', 'agent:reviewer', 'agent:scribe']);
    await expect(page.locator('[data-live-key="fleet:summary"]')).toContainText('4 agents');
  });

  test('activity and attention counts stay coherent while a focused link keeps focus', async ({ page, request }) => {
    await page.goto('/newsletter/');
    const link = page.locator('[data-live-key="agent:builder"] a').first();
    await link.focus();
    await expect(link).toBeFocused();
    const before = await attentionCounts(page);
    expect(before.header).toBe(before.listed);

    await change(request, 'inbox-ticket-activity');

    const created = page.locator('[data-live-region="activity"] [data-live-key="activity:delivery:fixture-live-activity"]');
    await expect(created).toBeVisible();
    await expect(page.locator('[data-live-region="attention"] [data-live-key="unassigned:delivery:fixture-live-activity"]')).toBeVisible();
    await expect(link).toBeFocused();
    const after = await attentionCounts(page);
    expect(after.listed).toBe(before.listed + 1);
    expect(after.header).toBe(after.listed);
  });

  test('queue header counts always equal the listed waiting jobs', async ({ page, request }) => {
    await page.goto('/newsletter/');
    const before = await queueCounts(page);
    expect(before.header).toBe(before.listed);

    await change(request, 'inbox-queue-grows');

    await expect.poll(async () => (await queueCounts(page)).listed).toBe(before.listed + 2);
    const after = await queueCounts(page);
    expect(after.header).toBe(after.listed);
    await expect(page.locator('[data-live-key="job:inbox-queued-1"]')).toContainText('builder / suite-health');
  });

  test('relative labels advance with the clock without a reload', async ({ page, request }) => {
    await page.goto('/newsletter/');
    const activity = page.locator('[data-live-region="activity"] a[data-live-key^="activity:"]').first();
    const label = activity.locator('span.font-mono');
    await expect(label).toHaveText('Just now');

    await change(request, 'inbox-clock-advances');

    await expect(label).toHaveText('30m ago');
  });
});

test.describe('jobs live regions', () => {
  async function resetRuntime(request: APIRequestContext) {
    expect((await request.post('/__ui/reset')).status()).toBe(204);
  }

  async function change(request: APIRequestContext, name: string) {
    expect((await request.post('/__ui/live/change', { data: { case: name } })).status()).toBe(204);
  }

  const jobStatus = (page: Page) => page.locator('[data-live-region="job-status"]');
  const cancelForms = (page: Page) => page.locator('main form[action$="/cancel"]');
  const pageStatus = (page: Page) => page.locator('[data-live-status][role="status"]');

  test.beforeEach(async ({ request }) => resetRuntime(request));
  test.afterEach(async ({ request }) => resetRuntime(request));

  test('a finished job updates in place and keeps the open diagnostics node', async ({ page, request }) => {
    await page.goto('/newsletter/jobs/job-waiting');
    await page.getByText('Diagnostics', { exact: true }).click();
    const diagnostics = await page.locator('main details').elementHandle();
    const main = await page.locator('main').elementHandle();
    await expect(jobStatus(page)).toContainText('Waiting for memory');
    await expect(cancelForms(page)).toHaveCount(1);

    await change(request, 'job-finishes');

    await expect(jobStatus(page)).toContainText('Complete');
    await expect(jobStatus(page)).not.toContainText('Waiting for memory');
    await expect(cancelForms(page)).toHaveCount(0);
    expect(await diagnostics!.evaluate(
      (node) => node.isConnected && node instanceof HTMLDetailsElement && node.open,
    )).toBe(true);
    expect(await main!.evaluate((node) => node.isConnected)).toBe(true);
    await expect(page.getByText(/Memory hash: 2222/)).toBeVisible();
  });

  test('a held cancel button survives the finish until it is released', async ({ page, request }) => {
    await page.goto('/newsletter/jobs/job-waiting');
    const cancel = page.getByRole('button', { name: 'Cancel', exact: true });
    await cancel.focus();
    await expect(cancel).toBeFocused();

    await change(request, 'job-finishes');

    await expect(jobStatus(page)).toContainText('Complete');
    await expect(cancel).toBeFocused();
    await expect(cancelForms(page)).toHaveCount(1);

    await cancel.evaluate((element) => (element as HTMLElement).blur());

    await expect(cancelForms(page)).toHaveCount(0);
  });

  test('a job that finishes with a session swaps the cancel action for resume in one snapshot', async ({ page, request }) => {
    await page.goto('/newsletter/jobs/job-waiting');
    const resumeForms = page.locator('main form[action$="/resume"]');
    await expect(cancelForms(page)).toHaveCount(1);
    await expect(resumeForms).toHaveCount(0);

    await change(request, 'job-finishes-with-session');

    await expect(jobStatus(page)).toContainText('Complete');
    await expect(cancelForms(page)).toHaveCount(0);
    await expect(resumeForms).toHaveCount(1);
    await expect(page.getByRole('button', { name: /^Resume in / })).toBeVisible();
    await expect(page.locator('#resume-command')).toHaveValue(/job-waiting-session/);
    await expect(pageStatus(page)).toBeHidden();
  });

  test('a held cancel button defers the resume swap until it is released', async ({ page, request }) => {
    await page.goto('/newsletter/jobs/job-waiting');
    const resumeForms = page.locator('main form[action$="/resume"]');
    const cancel = page.getByRole('button', { name: 'Cancel', exact: true });
    await cancel.focus();

    await change(request, 'job-finishes-with-session');

    await expect(jobStatus(page)).toContainText('Complete');
    await expect(cancel).toBeFocused();
    await expect(cancelForms(page)).toHaveCount(1);
    await expect(resumeForms).toHaveCount(0);

    await cancel.evaluate((element) => (element as HTMLElement).blur());

    await expect(cancelForms(page)).toHaveCount(0);
    await expect(resumeForms).toHaveCount(1);
  });

  test('retained artifacts and the memory publication state follow the job', async ({ page, request }) => {
    await page.goto('/newsletter/jobs/job-failed');
    const artifacts = page.locator('[data-live-region="job-artifacts"]');
    const publication = page.locator('[data-live-region="job-publication"]');
    await expect(artifacts).toContainText('Failed memory snapshot');
    await expect(publication).toContainText('1 retained artifact');

    await change(request, 'job-failure-artifacts');

    await expect(artifacts).toContainText('Second Draft');
    await expect(publication).toContainText('2 retained artifacts');

    await change(request, 'job-memory-published');

    await expect(artifacts).not.toContainText('Failed memory snapshot');
    await expect(publication).toHaveText('');
  });

  test('new and removed jobs reconcile the list by identity and keep untouched rows', async ({ page, request }) => {
    await page.goto('/newsletter/jobs');
    const count = page.locator('[data-live-region="jobs-count"]');
    await expect(page.locator('[data-live-key="job:job-waiting"]')).toBeVisible();
    await expect(page.locator('[data-live-key="job:job-failed"]')).toBeVisible();
    await page.locator('[data-live-key="job:job-waiting"]').evaluate((element) => {
      (element as unknown as { __kept: boolean }).__kept = true;
    });
    const before = Number(/(\d+)/.exec((await count.textContent()) ?? '')?.[1]);

    await change(request, 'job-added');
    await expect(page.locator('[data-live-key="job:job-live-added"]')).toBeVisible();
    await expect(count).toHaveText(`${before + 1} jobs`);

    await change(request, 'job-removed');
    await expect(page.locator('[data-live-key="job:job-failed"]')).toHaveCount(0);
    await expect(count).toHaveText(`${before} jobs`);
    expect(await page.locator('[data-live-key="job:job-waiting"]').evaluate(
      (element) => (element as unknown as { __kept?: boolean }).__kept,
    )).toBe(true);
  });

  test('a row status updates while its focused cancel button is held', async ({ page, request }) => {
    await page.goto('/newsletter/jobs');
    const row = page.locator('[data-live-key="job:job-waiting"]');
    const cancel = row.getByRole('button', { name: 'Cancel', exact: true });
    await cancel.focus();

    await change(request, 'job-finishes');

    await expect(row.locator('span.rounded-full').first()).toHaveText('Complete');
    await expect(cancel).toBeFocused();

    await cancel.evaluate((element) => (element as HTMLElement).blur());

    await expect(row.locator('form')).toHaveCount(0);
  });

  test('a removed job reports the page as unavailable without replacing it', async ({ page, request }) => {
    await page.goto('/newsletter/jobs/job-failed');
    await expect(jobStatus(page)).toContainText('Failed');

    await change(request, 'job-removed');

    await expect(pageStatus(page)).toBeVisible();
    await expect(pageStatus(page)).toContainText('This page is no longer available');
    await expect(jobStatus(page)).toContainText('Failed');
  });
});