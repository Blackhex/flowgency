import { mkdir, readFile, rm } from 'node:fs/promises';
import path from 'node:path';

import AxeBuilder from '@axe-core/playwright';
import { expect, test, type APIRequestContext, type Page } from '@playwright/test';

import { expectBodyFocus, tabTo } from './keyboard';
import { assertNoConsoleErrors, assertNoLayoutIssues, installBasePageSetup } from './layout';

const runtimeRoot = path.resolve(__dirname, '.runtime', 'current');
const configPath = path.join(runtimeRoot, 'config.yaml');

async function resetUiRuntime(request: APIRequestContext): Promise<void> {
  const response = await request.post('/__ui/reset');
  expect(response.status()).toBe(204);
}

async function workflowSnapshot(request: APIRequestContext, workflowId = 'delivery') {
  const response = await request.get(`/newsletter/workflows/${workflowId}/snapshot`);
  expect(response.ok()).toBeTruthy();
  return await response.json() as { ticket_count: number };
}

async function fillStorageRoot(page: Page, value: string): Promise<void> {
  await page.getByLabel('Storage root', { exact: true }).fill(value);
}

async function readExpectedRevision(page: Page): Promise<string> {
  return await page.locator('input[name="expected_revision"]').inputValue();
}

async function currentRoot(page: Page): Promise<string> {
  return await page.getByLabel('Storage root', { exact: true }).inputValue();
}

async function openNewWorkflow(page: Page, projectName: string): Promise<void> {
  if (projectName.startsWith('mobile')) {
    await page.getByRole('button', { name: 'Open navigation', exact: true }).click();
  }
  await page.getByRole('link', { name: 'New workflow', exact: true }).click();
}

async function expectNoAxeViolations(page: Page): Promise<void> {
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze();
  expect(results.violations, JSON.stringify(results.violations, null, 2)).toEqual([]);
}

test.beforeEach(async ({ page, request }, testInfo) => {
  await resetUiRuntime(request);
  await installBasePageSetup(page, testInfo.project.name.endsWith('dark') ? 'dark' : 'light');
});

test.afterEach(async ({ page, request }) => {
  await page.close();
  await resetUiRuntime(request);
});

test('existing workflow settings stay editable and preserve compact approved layout', async ({ page }, testInfo) => {
  await page.goto('/newsletter/workflows/delivery/settings');

  await expect(page.locator('h1')).toContainText('Delivery settings');
  await expect(page.getByLabel('Name', { exact: true })).toBeEnabled();
  await expect(page.getByLabel('Blueprint', { exact: true })).toBeEnabled();
  await expect(page.getByLabel('Integration', { exact: true })).toBeEnabled();
  await expect(page.getByLabel('Storage root', { exact: true })).toBeEnabled();
  await expect(page.getByText(/Blueprint and storage in use/)).toHaveCount(0);
  await expect(page.getByLabel(/identifier/i)).toHaveCount(0);
  await expect(page.getByRole('link', { name: 'Workflow settings', exact: true })).toHaveCount(0);
  await expect(page.getByRole('link', { name: 'Open blueprint', exact: true })).toHaveAttribute('href', '/admin/workflow-library/blueprints/delivery');

  if (testInfo.project.name.startsWith('mobile')) {
    await expect(page.getByRole('button', { name: 'Open navigation', exact: true })).toBeVisible();
  } else {
    await expect(page.getByRole('link', { name: 'New workflow', exact: true })).toBeVisible();
  }

  await expectBodyFocus(page);
  await tabTo(page, { role: 'textbox', name: 'Name' });
  await tabTo(page, { role: 'combobox', name: 'Blueprint' });
  await tabTo(page, { role: 'combobox', name: 'Integration' });
  await tabTo(page, { role: 'textbox', name: 'Storage root' });
  await tabTo(page, { role: 'button', name: 'Check storage' });
  await assertNoLayoutIssues(page);
  if (testInfo.project.name.startsWith('mobile')) {
    await expect(page).toHaveScreenshot('workflow-settings-mobile.png', { fullPage: true });
  } else {
    await expect(page).toHaveScreenshot('workflow-settings.png', { fullPage: true });
  }
  await assertNoConsoleErrors(page);
});

test('board toolbar exposes workflow settings navigation', async ({ page }) => {
  await page.goto('/newsletter/workflows/delivery');

  await expect(page.getByRole('link', { name: 'Workflow settings', exact: true })).toBeVisible();
  await page.getByRole('link', { name: 'Workflow settings', exact: true }).click();
  await expect(page).toHaveURL('/newsletter/workflows/delivery/settings');
  await expect(page.locator('h1')).toContainText('Delivery settings');
  await assertNoConsoleErrors(page);
});

test('settings toolbar tracks dirty state and revert restores the saved draft', async ({ page }) => {
  await page.goto('/newsletter/workflows/delivery/settings');

  const name = page.getByLabel('Name', { exact: true });
  const root = page.getByLabel('Storage root', { exact: true });
  const savedStatus = page.locator('[data-workflow-settings-status]');
  const saveButton = page.getByRole('button', { name: 'Save', exact: true });
  const revertButton = page.getByRole('button', { name: 'Revert', exact: true });
  const originalName = await name.inputValue();
  const originalRoot = await root.inputValue();

  await expect(savedStatus).toHaveText('Saved');
  await expect(saveButton).toBeDisabled();
  await expect(revertButton).toBeDisabled();

  await name.fill('Draft delivery');
  await root.fill(path.join(path.dirname(originalRoot), 'draft-root'));

  await expect(savedStatus).toHaveText('Unsaved changes');
  await expect(saveButton).toBeEnabled();
  await expect(revertButton).toBeEnabled();

  await revertButton.click();

  await expect(name).toHaveValue(originalName);
  await expect(root).toHaveValue(originalRoot);
  await expect(savedStatus).toHaveText('Saved');
  await expect(saveButton).toBeDisabled();
  await expect(revertButton).toBeDisabled();
  await expect(page.locator('#workflow-storage-health')).toHaveText('Not checked');
  await assertNoConsoleErrors(page);
});

test('changing the storage target clears stale health and older responses do not overwrite newer results', async ({ page }) => {
  await page.goto('/newsletter/workflows/delivery/settings');
  await page.evaluate(() => {
    const realFetch = window.fetch.bind(window);
    let requestCount = 0;
    let releaseFirstResponse: (() => void) | null = null;
    Object.assign(window, {
      __releaseWorkflowStorageCheck: () => releaseFirstResponse?.(),
    });
    window.fetch = async (input, init) => {
      const url = typeof input === 'string' ? input : input.url;
      if (!url.endsWith('/newsletter/workflows/delivery/settings/check-storage')) {
        return realFetch(input, init);
      }
      requestCount += 1;
      const body = init?.body;
      const form = body instanceof FormData ? body : new FormData();
      const root = String(form.get('integration_config.root') ?? '');
      if (requestCount === 1) {
        await new Promise<void>((resolve) => {
          releaseFirstResponse = resolve;
        });
      }
      const payload = root.endsWith('delivery-stale-next')
        ? {
            status: 'unavailable',
            label: 'Storage unavailable',
            detail: 'Newest result',
            ticket_count: 0,
            issues: [],
          }
        : {
            status: 'ok',
            label: 'Available',
            detail: '',
            ticket_count: 0,
            issues: [],
          };
      return new Response(JSON.stringify(payload), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    };
  });

  const root = page.getByLabel('Storage root', { exact: true });
  const health = page.locator('#workflow-storage-health');
  const originalRoot = await root.inputValue();
  const nextRoot = path.join(path.dirname(originalRoot), 'delivery-stale-next');

  await page.getByRole('button', { name: 'Check storage', exact: true }).click();
  await root.fill(nextRoot);
  await expect(health).toHaveText('Not checked');
  await page.getByRole('button', { name: 'Check storage', exact: true }).click();
  await expect(health).toHaveText('Storage unavailable');
  await page.evaluate(() => (window as typeof window & { __releaseWorkflowStorageCheck: () => void }).__releaseWorkflowStorageCheck());
  await expect(health).toHaveText('Storage unavailable');
  await assertNoConsoleErrors(page);
});

test('storage root switch hides old namespace and switch-back restores it', async ({ page, request }, testInfo) => {
  await page.goto('/newsletter/workflows/delivery/settings');

  const originalRoot = await currentRoot(page);
  const switchedRoot = path.join(path.dirname(originalRoot), 'delivery-empty');
  await rm(switchedRoot, { recursive: true, force: true });
  await mkdir(switchedRoot, { recursive: true });
  const saveSwitched = page.waitForResponse((response) => response.url().includes('/newsletter/workflows/delivery/settings') && response.request().method() === 'POST');
  await fillStorageRoot(page, switchedRoot);
  await page.getByRole('button', { name: 'Save', exact: true }).click();
  await saveSwitched;

  await expect((await workflowSnapshot(request)).ticket_count).toBe(0);

  const restoreRevision = await readExpectedRevision(page);
  const restored = await request.post('/newsletter/workflows/delivery/settings', {
    form: {
      name: 'Delivery',
      blueprint: 'delivery',
      integration: 'local',
      'integration_config.root': originalRoot,
      expected_revision: restoreRevision,
    },
    headers: { Accept: 'text/html' },
    maxRedirects: 0,
  });
  expect(restored.status()).toBe(303);

  await expect((await workflowSnapshot(request)).ticket_count).toBe(8);
  await assertNoConsoleErrors(page);
});

test('check storage renders text-only issues and recovers after a network failure', async ({ page }) => {
  await page.goto('/newsletter/workflows/delivery/settings');
  await page.evaluate(() => {
    const realFetch = window.fetch.bind(window);
    let requestCount = 0;
    Object.assign(window, { __workflowStorageAttack: 0 });
    window.fetch = async (input, init) => {
      const url = typeof input === 'string' ? input : input.url;
      if (!url.endsWith('/newsletter/workflows/delivery/settings/check-storage')) {
        return realFetch(input, init);
      }
      requestCount += 1;
      if (requestCount === 1) {
        throw new TypeError('network failed');
      }
      return new Response(JSON.stringify({
        status: 'incompatible',
        label: 'Incompatible',
        detail: 'Compatibility mismatch <script>window.__workflowStorageAttack = 1</script>',
        ticket_count: 2,
        issues: [
          {
            code: 'incompatible-blueprint',
            field: 'definition',
            message: 'Unsafe <img src=x onerror="window.__workflowStorageAttack = 1"> issue',
            hint: 'Repair <svg onload="window.__workflowStorageAttack = 1"></svg> text only.',
          },
        ],
      }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    };
  });

  const checkButton = page.getByRole('button', { name: 'Check storage', exact: true });
  await checkButton.click();
  await expect(page.locator('#workflow-storage-health')).toHaveText('Check failed');
  await expect(checkButton).toBeEnabled();

  await checkButton.click();
  await expect(page.locator('#workflow-storage-health')).toHaveText('Incompatible');
  await expect(page.locator('#workflow-storage-health-details')).toContainText('Compatibility mismatch <script>window.__workflowStorageAttack = 1</script>');
  await expect(page.locator('#workflow-storage-health-details')).toContainText('Unsafe <img src=x onerror="window.__workflowStorageAttack = 1"> issue');
  await expect(page.locator('#workflow-storage-health-details')).toContainText('Repair <svg onload="window.__workflowStorageAttack = 1"></svg> text only.');
  await expect(page.locator('#workflow-storage-health-details img')).toHaveCount(0);
  await expect(page.locator('#workflow-storage-health-details script')).toHaveCount(0);
  await expect(page.getByLabel('Name', { exact: true })).toBeEnabled();
  await expect(page.getByLabel('Blueprint', { exact: true })).toBeEnabled();
  await expect(page.getByLabel('Storage root', { exact: true })).toBeEnabled();
  await expect.poll(() => page.evaluate(() => (window as typeof window & { __workflowStorageAttack: number }).__workflowStorageAttack)).toBe(0);
  await assertNoConsoleErrors(page);
});

test('check storage distinguishes unavailable from empty and create follows selected blueprint', async ({ page, request }, testInfo) => {
  await page.goto('/newsletter/workflows/delivery/settings');

  const originalRoot = await currentRoot(page);
  const missingRoot = path.join(path.dirname(originalRoot), 'delivery-missing');
  const emptyRoot = path.join(path.dirname(originalRoot), 'delivery-empty');
  await rm(missingRoot, { recursive: true, force: true });
  await rm(emptyRoot, { recursive: true, force: true });
  await mkdir(emptyRoot, { recursive: true });

  await fillStorageRoot(page, missingRoot);
  await page.getByRole('button', { name: 'Check storage', exact: true }).click();
  await expect(page.locator('#workflow-storage-health')).toHaveText('Storage unavailable');

  await fillStorageRoot(page, emptyRoot);
  await page.getByRole('button', { name: 'Check storage', exact: true }).click();
  await expect(page.locator('#workflow-storage-health')).toHaveText('Available');

  await openNewWorkflow(page, testInfo.project.name);
  await expect(page).toHaveURL('/newsletter/workflows/new');
  await expect(page.getByRole('heading', { name: 'New workflow', exact: true })).toBeVisible();
  await expect(page.getByLabel(/identifier/i)).toHaveCount(0);
  await page.getByLabel('Blueprint', { exact: true }).selectOption('research-workflow');
  await expect(page.getByRole('link', { name: 'Open blueprint', exact: true })).toHaveAttribute('href', '/admin/workflow-library/blueprints/research-workflow');
  await page.getByLabel('Name', { exact: true }).fill('Investigation');
  await fillStorageRoot(page, path.join(path.dirname(originalRoot), 'investigation-root'));

  const saveCreated = page.waitForResponse((response) => response.url().includes('/newsletter/workflows/new') && response.request().method() === 'POST');
  await page.getByRole('button', { name: 'Create workflow', exact: true }).click();
  await saveCreated;

  await expect(page).toHaveURL(/\/newsletter\/workflows\/wf-[0-9a-f]{32}\/settings$/);
  await expect(page.getByRole('heading', { name: 'Investigation settings', exact: true })).toBeVisible();
  await expect(page.getByText('Investigation', { exact: true }).first()).toBeVisible();
  await assertNoLayoutIssues(page);
  if (testInfo.project.name.startsWith('mobile')) {
    await expect(page).toHaveScreenshot('workflow-create-mobile.png', { fullPage: true });
  } else {
    await expect(page).toHaveScreenshot('workflow-create.png', { fullPage: true });
  }
  await assertNoConsoleErrors(page);
});

test('validation errors keep the submitted draft visible on desktop and mobile', async ({ page }, testInfo) => {
  await page.goto('/newsletter/workflows/delivery/settings');

  await page.getByLabel('Name', { exact: true }).fill('');
  await fillStorageRoot(page, '');
  const failedSave = page.waitForResponse((response) => response.url().includes('/newsletter/workflows/delivery/settings') && response.request().method() === 'POST');
  await page.getByRole('button', { name: 'Save', exact: true }).click();
  await failedSave;

  await expect(page.getByRole('alert')).toContainText('Workflow name is required.');
  await expect(page.getByLabel('Name', { exact: true })).toHaveValue('');
  await expect(page.getByLabel('Storage root', { exact: true })).toHaveValue('');
  await expect(page.locator('#workflow-storage-health')).toHaveText('Invalid settings');
  await assertNoLayoutIssues(page);
  if (testInfo.project.name.startsWith('mobile')) {
    await expect(page).toHaveScreenshot('workflow-storage-error-mobile.png', { fullPage: true });
  } else {
    await expect(page).toHaveScreenshot('workflow-storage-error.png', { fullPage: true });
  }
});

test('stale save preserves the submitted draft after an external settings change', async ({ page, request }, testInfo) => {
  await page.goto('/newsletter/workflows/delivery/settings');

  const originalRoot = await currentRoot(page);
  const externalRoot = path.join(path.dirname(originalRoot), 'delivery-external');
  await rm(externalRoot, { recursive: true, force: true });
  await mkdir(externalRoot, { recursive: true });
  const staleRevision = await readExpectedRevision(page);

  const externalSave = await request.post('/newsletter/workflows/delivery/settings', {
    form: {
      name: 'Delivery',
      blueprint: 'delivery',
      integration: 'local',
      'integration_config.root': externalRoot,
      expected_revision: staleRevision,
    },
    headers: { Accept: 'text/html' },
    maxRedirects: 0,
  });
  expect(externalSave.status()).toBe(303);

  await page.getByLabel('Name', { exact: true }).fill('Local stale draft');
  await fillStorageRoot(page, originalRoot);
  const staleSave = page.waitForResponse((response) => response.url().includes('/newsletter/workflows/delivery/settings') && response.request().method() === 'POST');
  await page.getByRole('button', { name: 'Save', exact: true }).click();
  await staleSave;
  await expect(page.getByLabel('Name', { exact: true })).toHaveValue('Local stale draft');
  await expect(page.getByLabel('Storage root', { exact: true })).toHaveValue(originalRoot);
  await expect(page.locator('body')).toContainText('reload before saving');
  const config = await readFile(configPath, 'utf8');
  expect(config).toContain('delivery-external');
});

test('320px workflow settings keep toolbar controls and storage row usable', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 900 });
  await page.goto('/newsletter/workflows/delivery/settings');

  await expect(page.getByRole('button', { name: 'Open navigation', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Revert', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Save', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Check storage', exact: true })).toBeVisible();
  await assertNoLayoutIssues(page);
  await assertNoConsoleErrors(page);
});

test('320px new workflow form and validation error remain visible without clipping', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 900 });
  await page.goto('/newsletter/workflows/new');

  await expect(page.getByRole('heading', { name: 'New workflow', exact: true })).toBeVisible();
  // All form fields must fit at 320px — "Storage root" is the longest label in the form
  await expect(page.getByLabel('Name', { exact: true })).toBeVisible();
  await expect(page.getByLabel('Storage root', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Create workflow', exact: true })).toBeVisible();
  await assertNoLayoutIssues(page);
  await assertNoConsoleErrors(page);

  // Type a meaningful name so the form is dirty and the Create button enables
  await page.getByLabel('Name', { exact: true }).fill('Long workflow name for layout validation');
  // Submit with empty storage root -> server returns 422 with validation error
  const failedSave = page.waitForResponse((response) =>
    response.url().includes('/newsletter/workflows/new') &&
    response.request().method() === 'POST',
  );
  await page.getByRole('button', { name: 'Create workflow', exact: true }).click();
  await failedSave;

  // Error alert is visible and name draft is retained at 320px
  await expect(page.getByRole('alert')).toBeVisible();
  await expect(page.getByLabel('Name', { exact: true })).toHaveValue('Long workflow name for layout validation');
  await assertNoLayoutIssues(page);
});

test('workflow settings views have no WCAG A or AA violations', async ({ page, request }, testInfo) => {
  await page.goto('/newsletter/workflows/delivery/settings');
  await expect(page.getByRole('heading', { name: 'Delivery settings', exact: true })).toBeVisible();
  await expectNoAxeViolations(page);

  await openNewWorkflow(page, testInfo.project.name);
  await expect(page.getByRole('heading', { name: 'New workflow', exact: true })).toBeVisible();
  await expectNoAxeViolations(page);

  const invalidSave = await request.post('/newsletter/workflows/delivery/settings', {
    form: {
      name: '',
      blueprint: 'delivery',
      integration: 'local',
      'integration_config.root': '',
      expected_revision: await readExpectedRevision(page),
    },
    headers: { Accept: 'text/html' },
    maxRedirects: 0,
  });
  expect(invalidSave.status()).toBe(422);
  await page.goto('/newsletter/workflows/delivery/settings');
  await page.getByLabel('Name', { exact: true }).fill('');
  await page.getByLabel('Storage root', { exact: true }).fill('');
  const failedSave = page.waitForResponse((response) => response.url().includes('/newsletter/workflows/delivery/settings') && response.request().method() === 'POST');
  await page.getByRole('button', { name: 'Save', exact: true }).click();
  await failedSave;
  await expect(page.getByRole('alert')).toBeVisible();
  await expectNoAxeViolations(page);
});