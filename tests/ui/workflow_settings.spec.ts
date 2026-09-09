import { mkdir, readFile, rm } from 'node:fs/promises';
import path from 'node:path';

import { expect, test, type APIRequestContext, type Page } from '@playwright/test';

import { expectBodyFocus, tabTo } from './keyboard';
import { assertNoConsoleErrors, assertNoLayoutIssues, installConsoleErrorGate } from './layout';

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

test.beforeEach(async ({ page, request }, testInfo) => {
  await resetUiRuntime(request);
  installConsoleErrorGate(page);
  await page.addInitScript((theme) => {
    if (!localStorage.getItem('theme')) localStorage.setItem('theme', theme);
  }, testInfo.project.name.endsWith('dark') ? 'dark' : 'light');
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

test('storage root switch hides old namespace and switch-back restores it', async ({ page, request }, testInfo) => {
  test.skip(testInfo.project.name.startsWith('mobile'), 'desktop-only switch flow');
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

test('check storage distinguishes unavailable from empty and create follows selected blueprint', async ({ page, request }, testInfo) => {
  test.skip(testInfo.project.name.startsWith('mobile'), 'desktop-only create flow');
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

  await page.getByRole('link', { name: 'New workflow', exact: true }).click();
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

test('stale save preserves the submitted draft after an external settings change', async ({ page, request }, testInfo) => {
  test.skip(testInfo.project.name.startsWith('mobile'), 'desktop-only conflict flow');
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