import { expect, test, type APIRequestContext, type Locator, type Page } from '@playwright/test';

import { assertNoConsoleErrors, assertNoLayoutIssues, installBasePageSetup } from './layout';

const advisorPath = '/newsletter/agents/advisor/permissions';
const fixturePath = '/research/agents/permissions-editor/permissions';
const runtimeWorkspaceText = 'tests/ui/.runtime/current/workspaces/newsletter';
const runtimeEditorialSuffix = /tests\/ui\/\.runtime\/current\/teams\/newsletter\/editorial$/;
const longRulePath = 'workspace/teams/newsletter/' + [
  'editorial',
  'north-america',
  'q4-launch',
  'draft-reviews',
  'compliance-handoff',
  'final-approvals',
  'copyedits',
  'handoff',
  'archive',
  'allowed-zone',
].join('/');

type InitialPayload = {
  draft: {
    revision: string;
    catalog_id: string;
    draft_version: number;
    draft: {
      mode: 'inherit' | 'restricted' | 'unrestricted';
      rules: Array<{
        source_index: number | null;
        target: 'path' | 'no_path';
        path: string | null;
        selected: string[];
      }>;
    };
  };
};

function permissionFooter(page: Page) {
  return page.locator('.permission-footer-copy');
}

async function pinMaskedTextWidth(locator: Locator, width: string) {
  await locator.evaluateAll((elements, maskWidth) => {
    for (const element of elements as HTMLElement[]) {
      element.style.display = 'inline-block';
      element.style.width = maskWidth;
      element.style.whiteSpace = 'nowrap';
      element.style.overflow = 'hidden';
    }
  }, width);
}

function permissionVariableTextMasks(
  page: Page,
  options: { includeRulePathInputs?: boolean } = {},
) {
  const masks = [
    permissionFooter(page),
    page.locator('#permission-summary .permission-summary-scope > p').filter({ hasText: '.runtime/current/' }),
  ];
  if (options.includeRulePathInputs ?? true) {
    masks.push(page.locator('[data-rule-path]'));
  }
  return masks;
}

function parseInitialPayload(html: string): InitialPayload {
  const match = html.match(/<script id="permissions-initial" type="application\/json">([\s\S]*?)<\/script>/);
  if (!match) {
    throw new Error('permissions-initial payload not found');
  }
  return JSON.parse(match[1]) as InitialPayload;
}

async function pageInitialPayload(page: Page): Promise<InitialPayload> {
  return JSON.parse(await page.locator('#permissions-initial').textContent() ?? '{}') as InitialPayload;
}

async function restorePermissions(request: APIRequestContext, path: string, originalDraft: InitialPayload['draft']['draft']) {
  const response = await request.get(path);
  expect(response.ok()).toBe(true);
  const initial = parseInitialPayload(await response.text());
  const payload = {
    revision: initial.draft.revision,
    catalog_id: initial.draft.catalog_id,
    draft_version: initial.draft.draft_version + 100,
    draft: {
      mode: originalDraft.mode,
      rules: originalDraft.rules.map((rule) => ({
        ...rule,
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
  await installBasePageSetup(page, testInfo.project.name.endsWith('dark') ? 'dark' : 'light');
  await page.addStyleTag({ content: '* { animation: none !important; transition: none !important; caret-color: transparent !important; }' });
});

test('permissions editor layout remains stable', async ({ page }) => {
  await page.goto(advisorPath);
  await expect(page.getByRole('heading', { name: 'Permissions', exact: true })).toBeVisible();
  await expect(page.locator('[data-rule-path]').first()).toHaveValue(runtimeEditorialSuffix);
  await expect(page.locator('#permission-summary')).toContainText(runtimeWorkspaceText);
  await expect(page.locator('#permission-summary')).toContainText('tests/ui/.runtime/current/teams/newsletter/editorial');
  await expect(permissionFooter(page)).toHaveText(/Config revision: [0-9a-f]{64}/);
  await pinMaskedTextWidth(permissionFooter(page), '44rem');
  await assertNoLayoutIssues(page);
  await expect(page).toHaveScreenshot('agent-permissions.png', {
    fullPage: true,
    mask: permissionVariableTextMasks(page),
  });
  await assertNoConsoleErrors(page);
});

test('empty permissions state remains stable', async ({ page }) => {
  await page.goto(advisorPath);

  const firstRow = page.locator('[data-rule-row]').first();
  await firstRow.locator('[data-remove-rule]').click();

  await expect(page.getByText('No agent-specific rules yet.', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Save permissions', exact: true })).toBeEnabled();
  await expect(page.locator('#permission-summary')).toContainText(runtimeWorkspaceText);
  await expect(permissionFooter(page)).toHaveText(/Config revision: [0-9a-f]{64}/);
  await pinMaskedTextWidth(permissionFooter(page), '44rem');
  await assertNoLayoutIssues(page);
  await expect(page).toHaveScreenshot('agent-permissions-empty.png', {
    fullPage: true,
    mask: permissionVariableTextMasks(page, { includeRulePathInputs: false }),
  });
  await assertNoConsoleErrors(page);
});

test('structured rules discard without persisting', async ({ page }) => {
  await page.goto(advisorPath);
  const rows = page.locator('[data-rule-row]');
  const originalCount = await rows.count();

  await page.getByRole('button', { name: 'Add rule', exact: true }).click();
  await page.getByRole('menuitem', { name: 'No-path rule', exact: true }).click();

  await expect(rows).toHaveCount(originalCount + 1);
  const added = rows.last();
  await expect(added.locator('[data-rule-path]')).toHaveCount(0);
  await expect(added.getByText('Tools', { exact: true })).toHaveCount(0);

  await added.locator('[data-custom-tool]').fill('custom_test_tool');
  await added.getByRole('button', { name: 'Add custom tool', exact: true }).click();
  await expect(added.getByRole('checkbox', { name: 'custom_test_tool', exact: true })).toBeChecked();

  await page.getByRole('button', { name: 'Discard changes', exact: true }).click();
  await expect(rows).toHaveCount(originalCount);
  await expect(page.getByRole('button', { name: 'Save permissions', exact: true })).toBeDisabled();

  await assertNoLayoutIssues(page);
  await assertNoConsoleErrors(page);
});

test('add rule menu supports keyboard focus and enter adds a custom tool without submitting', async ({ page }) => {
  await page.goto(advisorPath);

  const addRule = page.getByRole('button', { name: 'Add rule', exact: true });
  await addRule.focus();
  await page.keyboard.press('Space');
  await expect(page.getByRole('menuitem', { name: 'Path rule', exact: true })).toBeFocused();
  await page.keyboard.press('ArrowDown');
  await expect(page.getByRole('menuitem', { name: 'No-path rule', exact: true })).toBeFocused();
  await page.keyboard.press('Enter');

  const added = page.locator('[data-rule-row]').last();
  await expect(added.locator('[data-custom-tool]')).toBeFocused();
  await added.locator('[data-custom-tool]').fill('keyboard_tool');
  await page.keyboard.press('Enter');

  await expect(page).toHaveURL(advisorPath);
  await expect(added.getByRole('checkbox', { name: 'keyboard_tool', exact: true })).toBeFocused();
  await expect(added.getByRole('checkbox', { name: 'keyboard_tool', exact: true })).toBeChecked();
  await expect(page.getByRole('button', { name: 'Save permissions', exact: true })).toBeEnabled();
  await assertNoConsoleErrors(page);
});

test('out-of-order preview responses never replace the newest draft summary', async ({ page }) => {
  await page.goto(advisorPath);

  await page.evaluate(() => {
    const realFetch = window.fetch.bind(window);
    let previewCount = 0;
    let releaseFirstPreview;
    window.__previewCount = 0;
    window.__releaseFirstPreview = () => releaseFirstPreview?.();
    window.fetch = async (input, init) => {
      const url = typeof input === 'string' ? input : input.url;
      if (!url.endsWith('/permissions/preview')) {
        return realFetch(input, init);
      }
      previewCount += 1;
      window.__previewCount = previewCount;
      const request = JSON.parse(String(init?.body ?? '{}'));
      const body = {
        draft_version: request.draft_version,
        revision: request.revision,
        catalog_id: request.catalog_id,
        summary_html: `<div class="text-sm">${request.draft.rules[0]?.path}</div>`,
        workspace_write: request.draft.rules[0]?.path === 'second-preview',
        issues: [],
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

  const path = page.locator('[data-rule-path]').first();
  await path.fill('first-preview');
  await expect.poll(() => page.evaluate(() => window.__previewCount)).toBe(1);
  await path.fill('second-preview');
  await expect.poll(() => page.evaluate(() => window.__previewCount)).toBe(2);

  await expect(page.locator('#permission-summary')).toContainText('second-preview');
  await page.evaluate(() => window.__releaseFirstPreview());
  await expect(page.getByText('first-preview', { exact: true })).toHaveCount(0);
  await expect(page.locator('[data-workspace-write]')).toHaveText('Workspace-root write: granted');
  await assertNoConsoleErrors(page);
});

test('preview failure retains a custom tool draft and retry preview recovers the summary', async ({ page }) => {
  await page.goto(advisorPath);

  await page.evaluate(() => {
    const realFetch = window.fetch.bind(window);
    let failNextPreview = true;
    window.fetch = async (input, init) => {
      const url = typeof input === 'string' ? input : input.url;
      if (!url.endsWith('/permissions/preview')) {
        return realFetch(input, init);
      }
      const request = JSON.parse(String(init?.body ?? '{}'));
      if (failNextPreview) {
        failNextPreview = false;
        return new Response(JSON.stringify({
          draft_version: request.draft_version,
          code: 'preview-unavailable',
          issues: [],
          summary_html: null,
        }), {
          status: 503,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      return new Response(JSON.stringify({
        draft_version: request.draft_version,
        revision: request.revision,
        catalog_id: request.catalog_id,
        summary_html: '<div class="text-sm">retry-preview-ok</div>',
        workspace_write: true,
        issues: [],
      }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    };
  });

  const row = page.locator('[data-rule-row]').first();
  await row.locator('[data-custom-tool]').fill('keep_this_tool');
  await row.getByRole('button', { name: 'Add custom tool', exact: true }).click();

  await expect(page.getByText('Preview unavailable', { exact: true })).toBeVisible();
  await expect(row.getByRole('checkbox', { name: 'keep_this_tool', exact: true })).toBeChecked();
  await expect(page.getByRole('button', { name: 'Save permissions', exact: true })).toBeDisabled();
  await expect(row.locator('[data-rule-path]')).toHaveValue(runtimeEditorialSuffix);
  await expect(permissionFooter(page)).toHaveText(/Config revision: [0-9a-f]{64}/);
  await pinMaskedTextWidth(permissionFooter(page), '44rem');
  await assertNoLayoutIssues(page);
  await expect(page).toHaveScreenshot('agent-permissions-preview-error.png', {
    fullPage: true,
    mask: permissionVariableTextMasks(page),
  });

  await page.getByRole('button', { name: 'Retry preview', exact: true }).click();
  await expect(page.locator('#permission-summary')).toContainText('retry-preview-ok');
  await expect(page.getByRole('button', { name: 'Save permissions', exact: true })).toBeEnabled();
  await assertNoConsoleErrors(page);
});

test('long path permissions state remains stable', async ({ page }) => {
  await page.goto(advisorPath);

  const pathField = page.locator('[data-rule-path]').first();
  await pathField.fill(longRulePath);

  await expect(pathField).toHaveValue(longRulePath);
  await expect(page.locator('#permission-summary')).toContainText('allowed-zone');
  await expect(page.locator('#permission-summary')).toContainText(runtimeWorkspaceText);
  await expect(permissionFooter(page)).toHaveText(/Config revision: [0-9a-f]{64}/);
  await expect(page.getByRole('button', { name: 'Save permissions', exact: true })).toBeEnabled();
  await pinMaskedTextWidth(permissionFooter(page), '44rem');
  await assertNoLayoutIssues(page);
  await expect(page).toHaveScreenshot('agent-permissions-long-path.png', {
    fullPage: true,
    mask: permissionVariableTextMasks(page, { includeRulePathInputs: false }),
  });
  await assertNoConsoleErrors(page);
});

test('pending custom tool text blocks save until it is added or cleared', async ({ page }) => {
  await page.goto(advisorPath);

  const row = page.locator('[data-rule-row]').first();
  const customInput = row.locator('[data-custom-tool]');
  await customInput.fill('pending_only_tool');

  await expect(page.getByRole('button', { name: 'Save permissions', exact: true })).toBeDisabled();
  await expect(row.getByRole('checkbox', { name: 'pending_only_tool', exact: true })).toHaveCount(0);
  await expect(row.getByText('Add or clear the pending custom tool before saving.', { exact: true })).toBeVisible();
  await expect(page.locator('[data-summary-status]')).toHaveText('Add or clear the pending custom tool before saving.');

  await page.getByRole('button', { name: 'Discard changes', exact: true }).click();
  await expect(customInput).toHaveValue('');
  await expect(row.getByText('Add or clear the pending custom tool before saving.', { exact: true })).toHaveCount(0);

  await customInput.fill('added_tool');
  await row.getByRole('button', { name: 'Add custom tool', exact: true }).click();
  await expect(row.getByRole('checkbox', { name: 'added_tool', exact: true })).toBeChecked();

  await customInput.fill('leave_me_pending');
  const closeDialog = page.waitForEvent('dialog');
  await page.close({ runBeforeUnload: true });
  const dialog = await closeDialog;
  expect(dialog.type()).toBe('beforeunload');
  await dialog.dismiss();
});

test('preview conflict retains the draft, disables save, and reload restores the live page', async ({ page }) => {
  await page.goto(advisorPath);

  await page.evaluate(() => {
    const realFetch = window.fetch.bind(window);
    window.fetch = async (input, init) => {
      const url = typeof input === 'string' ? input : input.url;
      if (!url.endsWith('/permissions/preview')) {
        return realFetch(input, init);
      }
      const request = JSON.parse(String(init?.body ?? '{}'));
      return new Response(JSON.stringify({
        draft_version: request.draft_version,
        code: 'catalog-conflict',
        issues: [
          {
            code: 'catalog-conflict',
            field: 'catalog_id',
            message: 'Tool metadata changed while this draft was open.',
            hint: 'Reload before saving permissions again.',
          },
        ],
        summary_html: null,
      }), {
        status: 409,
        headers: { 'Content-Type': 'application/json' },
      });
    };
  });

  const row = page.locator('[data-rule-row]').first();
  await row.locator('[data-custom-tool]').fill('conflict_tool');
  await row.getByRole('button', { name: 'Add custom tool', exact: true }).click();

  await expect(page.getByText('Preview unavailable', { exact: true })).toBeVisible();
  await expect(row.getByRole('checkbox', { name: 'conflict_tool', exact: true })).toBeChecked();
  await expect(page.getByRole('button', { name: 'Save permissions', exact: true })).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Reload page', exact: true })).toBeVisible();

  await Promise.all([
    page.waitForLoadState('domcontentloaded'),
    page.getByRole('button', { name: 'Reload page', exact: true }).click(),
  ]);

  await expect(page).toHaveURL(advisorPath);
  await expect(page.getByRole('button', { name: 'Reload page', exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Save permissions', exact: true })).toBeDisabled();
  await assertNoConsoleErrors(page);
});

test('save round trip uses the real endpoint and restores the original fixture policy', async ({ page, request }) => {
  await page.goto(fixturePath);
  const initial = await pageInitialPayload(page);
  const originalDraft = structuredClone(initial.draft.draft);

  try {
    const writeCheckbox = page.getByRole('checkbox', { name: 'write', exact: true });
    await writeCheckbox.check();
    await expect(page.getByRole('button', { name: 'Save permissions', exact: true })).toBeEnabled();
    await page.getByRole('button', { name: 'Save permissions', exact: true }).click();

    await expect(page).toHaveURL(fixturePath);
    await expect(page.getByRole('checkbox', { name: 'write', exact: true })).toBeChecked();
    await expect(page.getByText('Workspace-root write: granted', { exact: true })).toBeVisible();
  } finally {
    await restorePermissions(request, fixturePath, originalDraft);
  }

  await assertNoLayoutIssues(page);
  await assertNoConsoleErrors(page);
});