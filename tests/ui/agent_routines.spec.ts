import { expect, test, type APIRequestContext, type Page } from '@playwright/test';

import { assertNoConsoleErrors, assertNoLayoutIssues, installConsoleErrorGate } from './layout';

const pagePath = '/newsletter/agents/advisor/routines';
const fixturePath = '/research/agents/permissions-editor/routines';

type InitialPayload = {
  draft: {
    revision: string;
    draft_version: number;
    draft: {
      routines: Array<{
        key: string;
        source_index: number | null;
        id: string;
        prompt_scope: 'blueprint' | 'instance';
        prompt_name: string;
        enabled: boolean;
        arguments: string[];
        schedule: { mode: 'every' | 'at'; amount: string; unit: 'm' | 'h' | 'd'; time: string };
        recovery: { mode: 'default' | 'none' | 'today' | 'always' | 'duration'; amount: string; unit: 'm' | 'h' | 'd' };
        memory: { scope: 'inherit' | 'run' | 'routine' | 'agent' | 'team' | 'channel'; channel: string };
      }>;
    };
  };
};

async function pageInitialPayload(page: Page): Promise<InitialPayload> {
  return JSON.parse(await page.locator('#routines-initial').textContent() ?? '{}') as InitialPayload;
}

async function restoreRoutines(request: APIRequestContext, path: string, originalDraft: InitialPayload['draft']['draft']) {
  const response = await request.get(path);
  expect(response.ok()).toBe(true);
  const initial = JSON.parse((await response.text()).match(/<script id="routines-initial" type="application\/json">([\s\S]*?)<\/script>/)?.[1] ?? '{}') as InitialPayload;
  const payload = {
    revision: initial.draft.revision,
    draft_version: initial.draft.draft_version + 100,
    draft: {
      routines: originalDraft.routines.map((row, index) => ({
        ...row,
        key: `restore-${index}`,
        source_index: null,
      })),
    },
  };
  const save = await request.post(path, {
    form: { payload: JSON.stringify(payload) },
    failOnStatusCode: false,
    maxRedirects: 0,
  });
  expect(save.status()).toBe(303);
}

test.beforeEach(async ({ page }, testInfo) => {
  installConsoleErrorGate(page);
  await page.addInitScript((theme) => {
    localStorage.setItem('theme', theme);
  }, testInfo.project.name.endsWith('dark') ? 'dark' : 'light');
  await page.addStyleTag({ content: '* { animation: none !important; transition: none !important; caret-color: transparent !important; }' });
});

test('routines layout remains stable for enabled and disabled rows', async ({ page }) => {
  await page.goto(pagePath);

  await expect(page.getByRole('heading', { name: 'Routines', exact: true })).toBeVisible();
  await expect(page.locator('[data-routine-row]')).toHaveCount(2);
  await expect(page.locator('#routine-summary [data-summary-row]')).toHaveCount(2);
  await assertNoLayoutIssues(page);
  await expect(page).toHaveScreenshot('agent-routines.png', { fullPage: true });
  await assertNoConsoleErrors(page);
});

test('renaming and reordering preserve saved provenance', async ({ page }) => {
  await page.goto(pagePath);
  const rows = page.locator('[data-routine-row]');
  const first = rows.first();
  const originalId = await first.locator('[data-field="id"]').inputValue();
  const savedBefore = await page.locator('#routine-summary [data-summary-key="saved-0"] [data-saved-last]').innerText();

  await first.locator('[data-field="id"]').fill('renamed-review');
  await expect(first.locator('[data-rename-warning]')).toContainText(originalId);
  await first.locator('[data-move-down]').click();
  await expect(rows.last().locator('[data-field="id"]')).toHaveValue('renamed-review');

  const summary = page.locator('#routine-summary [data-summary-key="saved-0"]');
  await expect(summary.locator('[data-saved-last]')).toHaveText(savedBefore);
  await expect(summary.locator('[data-original-id-note]')).toContainText(originalId);

  await page.getByRole('button', { name: 'Discard changes', exact: true }).click();
  await expect(rows.first().locator('[data-field="id"]')).toHaveValue(originalId);
  await assertNoLayoutIssues(page);
  await assertNoConsoleErrors(page);
});

test('conditional controls remain row-local and blank arguments block save', async ({ page }) => {
  await page.goto(pagePath);

  const first = page.locator('[data-routine-row]').first();
  const second = page.locator('[data-routine-row]').nth(1);

  await first.locator('[data-field="schedule.mode"][value="at"]').check();
  await expect(first.locator('[data-schedule-at-fields]')).toBeVisible();
  await expect(first.locator('[data-schedule-every-fields]')).toBeHidden();
  await expect(second.locator('[data-schedule-every-fields]')).toBeVisible();

  await expect(second.locator('[data-memory-channel-wrap]')).toBeHidden();
  await second.locator('[data-field="memory.scope"]').selectOption('channel');
  await expect(second.locator('[data-memory-channel-wrap]')).toBeVisible();
  await expect(first.locator('[data-memory-channel-wrap]')).toBeVisible();
  await second.locator('[data-field="memory.channel"]').selectOption('brand-strategy');

  await first.locator('[data-field="recovery.mode"]').selectOption('duration');
  await expect(first.locator('[data-recovery-duration-wrap]')).toBeVisible();
  await expect(second.locator('[data-recovery-duration-wrap]')).toBeHidden();
  await first.locator('[data-field="recovery.amount"]').fill('48');

  await first.getByRole('button', { name: 'Add argument', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Save routines', exact: true })).toBeDisabled();
  await expect(first.getByText('Routine arguments must be non-empty strings.', { exact: true })).toBeVisible();

  await first.locator('[data-argument-row]').last().locator('[data-argument-value]').fill('--kept-as-typed  value');
  await expect.poll(async () => await page.getByRole('button', { name: 'Save routines', exact: true }).isEnabled()).toBe(true);
  await expect(first.locator('[data-argument-row]').last().locator('[data-argument-value]')).toHaveValue('--kept-as-typed  value');
  await assertNoConsoleErrors(page);
});

test('empty routines state renders without overflow', async ({ page }) => {
  await page.goto(pagePath);

  await page.locator('[data-routine-row]').first().locator('[data-remove-routine]').click();
  await page.locator('[data-routine-row]').first().locator('[data-remove-routine]').click();

  await expect(page.locator('[data-empty-routines]')).toBeVisible();
  await expect(page.locator('.routine-empty-summary')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Save routines', exact: true })).toBeEnabled();
  await assertNoLayoutIssues(page);
  await expect(page).toHaveScreenshot('agent-routines-empty.png', { fullPage: true });
  await assertNoConsoleErrors(page);
});

test('failed preview preserves the edited ID and retry recovers', async ({ page }) => {
  await page.goto(pagePath);
  await page.evaluate(() => {
    const realFetch = window.fetch.bind(window);
    let failNextPreview = true;
    window.fetch = async (input, init) => {
      const url = typeof input === 'string' ? input : input.url;
      if (!url.endsWith('/routines/preview')) {
        return realFetch(input, init);
      }
      const request = JSON.parse(String(init?.body ?? '{}'));
      if (failNextPreview) {
        failNextPreview = false;
        return new Response(JSON.stringify({
          draft_version: request.draft_version,
          code: 'preview-unavailable',
          issues: [],
        }), {
          status: 503,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      return realFetch(input, init);
    };
  });
  const first = page.locator('[data-routine-row]').first();
  await first.locator('[data-field="id"]').fill('keep-my-draft');

  await expect(page.getByText('Preview unavailable', { exact: true })).toBeVisible();
  await expect(first.locator('[data-field="id"]')).toHaveValue('keep-my-draft');
  await expect(page.getByRole('button', { name: 'Save routines', exact: true })).toBeDisabled();
  await expect(page).toHaveScreenshot('agent-routines-preview-error.png', { fullPage: true });

  await page.getByRole('button', { name: 'Retry preview', exact: true }).click();
  await expect.poll(async () => await page.getByRole('button', { name: 'Save routines', exact: true }).isEnabled()).toBe(true);
  await page.getByRole('button', { name: 'Discard changes', exact: true }).click();
  await assertNoConsoleErrors(page);
});

test('out-of-order preview responses never replace the newest summary', async ({ page }) => {
  await page.goto(pagePath);
  await page.evaluate(() => {
    const realFetch = window.fetch.bind(window);
    let previewCount = 0;
    let releaseFirstPreview;
    window.__routinePreviewCount = 0;
    window.__releaseRoutinePreview = () => releaseFirstPreview?.();
    window.fetch = async (input, init) => {
      const url = typeof input === 'string' ? input : input.url;
      if (!url.endsWith('/routines/preview')) {
        return realFetch(input, init);
      }
      previewCount += 1;
      window.__routinePreviewCount = previewCount;
      const request = JSON.parse(String(init?.body ?? '{}'));
      const body = {
        draft_version: request.draft_version,
        revision: request.revision,
        rows: request.draft.routines.map((row) => ({
          key: row.key,
          source_index: row.source_index,
          id: row.id,
          enabled: row.enabled,
          prompt_scope: row.prompt_scope,
          prompt_name: row.prompt_name,
          schedule: row.schedule.mode === 'at' ? `at ${row.schedule.time}` : `every ${row.schedule.amount}${row.schedule.unit}`,
          memory: row.memory.scope === 'channel' ? `Channel: ${row.memory.channel}` : 'Agent memory',
          arguments: row.arguments,
          recovery: row.recovery.mode,
        })),
        warnings: [],
      };
      if (previewCount === 1) {
        await new Promise((resolve) => {
          releaseFirstPreview = resolve;
        });
      }
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    };
  });
  const firstId = page.locator('[data-routine-row]').first().locator('[data-field="id"]');
  await firstId.fill('first-preview');
  await expect.poll(() => page.evaluate(() => window.__routinePreviewCount)).toBe(1);
  await firstId.fill('second-preview');
  await expect.poll(() => page.evaluate(() => window.__routinePreviewCount)).toBe(2);

  await expect(page.locator('#routine-summary [data-summary-row]').first()).toContainText('second-preview');
  await page.evaluate(() => window.__releaseRoutinePreview());
  await expect(page.getByText('first-preview', { exact: true })).toHaveCount(0);
  await assertNoConsoleErrors(page);
});

test('programmatic value changes cannot submit unnoticed', async ({ page }) => {
  let savePosts = 0;
  await page.route('**/newsletter/agents/advisor/routines', async (route) => {
    const request = route.request();
    if (request.method() === 'POST' && !request.url().endsWith('/preview')) {
      savePosts += 1;
    }
    await route.continue();
  });

  await page.goto(pagePath);
  const first = page.locator('[data-routine-row]').first().locator('[data-field="id"]');
  await first.fill('validated-first');
  await expect.poll(async () => await page.getByRole('button', { name: 'Save routines', exact: true }).isEnabled()).toBe(true);

  await page.evaluate(() => {
    const input = document.querySelector('[data-routine-row] [data-field="id"]');
    if (input instanceof HTMLInputElement) {
      input.value = 'sneaky-change';
    }
  });

  await page.getByRole('button', { name: 'Save routines', exact: true }).click();
  await expect(page).toHaveURL(pagePath);
  await expect.poll(() => savePosts).toBe(0);
  await expect(page.getByRole('button', { name: 'Save routines', exact: true })).toBeDisabled();
  await expect(page.locator('#routine-summary [data-summary-row]').first()).toContainText('sneaky-change');
  await assertNoConsoleErrors(page);
});

test('beforeunload can be canceled before discarding the draft', async ({ page }) => {
  await page.goto(pagePath);
  await page.locator('[data-routine-row]').first().locator('[data-field="id"]').fill('dirty-exit-check');

  const firstDialog = page.waitForEvent('dialog');
  const firstClose = page.close({ runBeforeUnload: true });
  const dialog = await firstDialog;
  expect(dialog.type()).toBe('beforeunload');
  await dialog.dismiss();
  await firstClose;
  expect(page.isClosed()).toBe(false);
});

test('long text state wraps without clipping', async ({ page }) => {
  await page.goto(pagePath);

  const longId = 'release-review-with-a-very-long-unbroken-routine-identifier-to-prove-the-summary-wraps-correctly';
  const longArgument = '--very-long-argument-token-without-breaks-and-with-preserved-spacing    second-segment';
  const first = page.locator('[data-routine-row]').first();
  const firstArgument = first.locator('[data-argument-list] [data-argument-value]').first();
  await first.locator('[data-field="id"]').fill(longId);
  await firstArgument.click();
  await firstArgument.fill(longArgument);

  await expect.poll(async () => await page.getByRole('button', { name: 'Save routines', exact: true }).isEnabled()).toBe(true);
  await assertNoLayoutIssues(page);
  await expect(page).toHaveScreenshot('agent-routines-long-text.png', { fullPage: true });
  await assertNoConsoleErrors(page);
});

test('real save round trip restores the research fixture routines', async ({ page, request }) => {
  await page.goto(fixturePath);
  const initial = await pageInitialPayload(page);
  const originalDraft = structuredClone(initial.draft.draft);

  try {
    const row = page.locator('[data-routine-row]').first();
    await row.locator('[data-field="id"]').fill('research-digest-updated');
    await row.locator('[data-field="enabled"]').check();
    await row.locator('[data-field="schedule.mode"][value="at"]').check();
    await expect(row.locator('[data-schedule-at-fields]')).toBeVisible();
    await row.locator('[data-field="schedule.time"]').fill('17:30');
    await row.getByRole('button', { name: 'Add argument', exact: true }).click();
    await row.locator('[data-argument-row]').last().locator('[data-argument-value]').fill('--saved');

    await expect.poll(async () => await page.getByRole('button', { name: 'Save routines', exact: true }).isEnabled()).toBe(true);
    await page.getByRole('button', { name: 'Save routines', exact: true }).click();

    await expect(page).toHaveURL(fixturePath);
    await expect(page.locator('[data-routine-row]').first().locator('[data-field="id"]')).toHaveValue('research-digest-updated');
    await expect(page.locator('#routine-summary [data-summary-row]').first()).toContainText('17:30');
  } finally {
    await restoreRoutines(request, fixturePath, originalDraft);
  }

  await assertNoConsoleErrors(page);
});