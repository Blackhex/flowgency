import { expect, test } from '@playwright/test';

import { assertNoLayoutIssues, assertNoConsoleErrors, installConsoleErrorGate } from './layout';

test.beforeEach(async ({ page, request }, testInfo) => {
  const reset = await request.post('/__ui/reset');
  expect(reset.status()).toBe(204);
  installConsoleErrorGate(page);
  await page.addInitScript((theme) => {
    if (!localStorage.getItem('theme')) localStorage.setItem('theme', theme);
  }, testInfo.project.name.endsWith('dark') ? 'dark' : 'light');
});

test.afterEach(async ({ page, request }) => {
  await page.close();
  const reset = await request.post('/__ui/reset');
  expect(reset.status()).toBe(204);
});

test('field labels and state names do not expose technical identifiers', async ({ page }) => {
  await page.goto('/admin/workflow-library/blueprints/delivery');

  await expect(page.getByRole('tab')).toHaveText(['Overview', 'States', 'Transitions']);
  for (const name of ['Overview', 'States', 'Transitions']) {
    await page.getByRole('tab', { name, exact: true }).click();
    await expect(page.getByLabel(/identifier/i)).toHaveCount(0);
    await expect(page.getByRole('checkbox', { name: /Evidence required/i })).toHaveCount(0);
    await assertNoLayoutIssues(page);
  }

  await assertNoConsoleErrors(page);
});

test('create mode saves through the structured route', async ({ page }) => {
  await page.goto('/admin/workflow-library/blueprints/new');

  await page.locator('[data-editor-bind="name"]').fill('Delivery workflow copy');
  await expect(page.getByRole('button', { name: 'Save', exact: true })).toBeEnabled();

  const saveResponse = page.waitForResponse((response) =>
    response.url().includes('/admin/workflow-library/blueprints/new') &&
    response.request().method() === 'POST',
  );
  await page.getByRole('button', { name: 'Save', exact: true }).click();
  await saveResponse;

  await expect
    .poll(() => new URL(page.url()).pathname)
    .toMatch(/^\/admin\/workflow-library\/blueprints\/wf-[0-9a-f]{32}$/);
  await expect(page.getByRole('heading', { name: 'Delivery workflow copy', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Save', exact: true })).toBeDisabled();
  await assertNoConsoleErrors(page);
});

test('existing blueprint save follows the redirected canonical editor url', async ({ page }) => {
  await page.goto('/admin/workflow-library/blueprints/delivery');

  const name = page.locator('[data-editor-bind="name"]');
  await name.fill('Delivery workflow revised');
  await expect(page.getByRole('button', { name: 'Save', exact: true })).toBeEnabled();

  const saveResponse = page.waitForResponse((response) =>
    response.url().includes('/admin/workflow-library/blueprints/delivery') &&
    response.request().method() === 'POST',
  );
  await page.getByRole('button', { name: 'Save', exact: true }).click();
  await saveResponse;

  await expect(page).toHaveURL('/admin/workflow-library/blueprints/delivery');
  await expect(page.getByRole('heading', { name: 'Delivery workflow revised', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Save', exact: true })).toBeDisabled();
  await assertNoConsoleErrors(page);
});

test('revert restores the saved draft', async ({ page }) => {
  await page.goto('/admin/workflow-library/blueprints/delivery');

  const description = page.locator('[data-editor-bind="description"]');
  await description.fill('Local unsaved text');
  await expect(page.getByRole('button', { name: 'Revert', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: 'Revert', exact: true }).click();

  await page.waitForLoadState('networkidle');
  await expect(description).toHaveValue('Deliver verified work.');
  await expect(page.getByRole('button', { name: 'Save', exact: true })).toBeDisabled();
  await assertNoConsoleErrors(page);
});

test('reused fields stay linked across transitions and preconditions', async ({ page }, testInfo) => {
  await page.goto('/admin/workflow-library/blueprints/delivery');
  await page.getByRole('tab', { name: 'Transitions', exact: true }).click();

  await page.getByLabel('Add output').click();
  await page.getByLabel('New field label').fill('Shared note');
  await page.getByLabel('New field type').selectOption('text');
  await page.getByRole('button', { name: 'Create and add', exact: true }).click();

  await page.getByLabel('Add precondition').click();
  await page.getByLabel('Precondition input 1').selectOption({ label: 'Shared note' });
  await page.getByLabel('Precondition value 1').fill('ready');

  if (testInfo.project.name.startsWith('mobile')) {
    await page.locator('[data-mobile-transition]').getByLabel('Add transition').click();
  } else {
    await page.locator('[data-editor-transition-nav]').getByLabel('Add transition').click();
  }
  await page.getByLabel('Transition name').fill('Request changes');
  await page.getByLabel('Add input').click();
  await page.getByRole('button', { name: 'Shared note (Text)', exact: true }).click();
  await page.getByLabel('Input label 1').fill('Shared review note');

  if (testInfo.project.name.startsWith('mobile')) {
    await page.getByLabel('Transition picker').selectOption({ label: 'Complete review' });
  } else {
    await page.getByRole('button', { name: 'Complete review Review Done' }).click();
  }
  await expect(page.getByLabel('Output label 3')).toHaveValue('Shared review note');
  await expect(page.getByLabel('Precondition input 1')).toHaveText(/Shared review note/);
  await assertNoConsoleErrors(page);
});

test('transition navigation uses the sidebar on desktop and the picker on mobile', async ({ page }, testInfo) => {
  await page.goto('/admin/workflow-library/blueprints/delivery');
  await page.getByRole('tab', { name: 'Transitions', exact: true }).click();

  if (testInfo.project.name.startsWith('mobile')) {
    const picker = page.getByLabel('Transition picker');
    await expect(picker).toBeVisible();
    await expect(page.locator('[data-editor-transition-nav]')).toBeHidden();
    await picker.selectOption({ label: 'Complete review' });
  } else {
    const nav = page.locator('[data-editor-transition-nav]');
    await expect(nav).toBeVisible();
    await expect(page.getByLabel('Transition picker')).toBeHidden();
    await expect(nav.getByText('Complete review', { exact: true })).toBeVisible();
  }

  await expect(page.getByLabel('Transition name')).toHaveValue('Complete review');
  await assertNoConsoleErrors(page);
});

test('workflow editor tabs match the approved overview, states, and transitions layouts', async ({ page }, testInfo) => {
  await page.goto('/admin/workflow-library/blueprints/delivery');

  const suffix = testInfo.project.name.startsWith('mobile') ? 'mobile' : 'desktop';

  await page.getByRole('tab', { name: 'Overview', exact: true }).click();
  await assertNoLayoutIssues(page);
  await expect(page).toHaveScreenshot(`workflow-overview-${suffix}.png`, { fullPage: true });

  await page.getByRole('tab', { name: 'States', exact: true }).click();
  await assertNoLayoutIssues(page);
  await expect(page).toHaveScreenshot(`workflow-states-${suffix}.png`, { fullPage: true });

  await page.getByRole('tab', { name: 'Transitions', exact: true }).click();
  await assertNoLayoutIssues(page);
  await expect(page).toHaveScreenshot(`workflow-transitions-${suffix}.png`, { fullPage: true });
  await assertNoConsoleErrors(page);
});