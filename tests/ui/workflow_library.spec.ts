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

test('save preserves the selected same-label new transition across reload', async ({ page }, testInfo) => {
  await page.goto('/admin/workflow-library/blueprints/delivery');
  await page.getByRole('tab', { name: 'Transitions', exact: true }).click();

  const addTransition = testInfo.project.name.startsWith('mobile')
    ? page.locator('[data-mobile-transition]').getByLabel('Add transition')
    : page.locator('[data-editor-transition-nav]').getByLabel('Add transition');
  await addTransition.click();
  await page.getByLabel('Transition name').fill('Shared label');
  await page.getByLabel('From state').selectOption({ label: 'Backlog' });
  await page.getByLabel('To state').selectOption({ label: 'Review' });
  await page.getByLabel('Add agent criterion').click();
  await page.locator('textarea[aria-label="Agent criterion 1"]').fill('first transition identity');

  await addTransition.click();
  await page.getByLabel('Transition name').fill('Shared label');
  await page.getByLabel('From state').selectOption({ label: 'Review' });
  await page.getByLabel('To state').selectOption({ label: 'Done' });
  await page.getByLabel('Add agent criterion').click();
  await page.locator('textarea[aria-label="Agent criterion 1"]').fill('second transition identity');

  const saveResponse = page.waitForResponse((response) =>
    response.url().includes('/admin/workflow-library/blueprints/delivery') &&
    response.request().method() === 'POST',
  );
  await page.getByRole('button', { name: 'Save', exact: true }).click();
  await saveResponse;

  await expect(page).toHaveURL('/admin/workflow-library/blueprints/delivery');

  const savedSelection = await page.evaluate(() => {
    const node = document.getElementById('workflow-editor-data');
    if (!node?.textContent) throw new Error('Missing workflow editor payload');
    const state = JSON.parse(node.textContent);
    const second = state.draft.transitions.find(
      (row: {
        existing_transition_id: string | null;
        criteria: Array<{ description: string }>;
      }) => row.criteria.some((criterion) => criterion.description === 'second transition identity'),
    );
    return {
      activeTransitionRef: window.sessionStorage.getItem('flowgency.workflow-editor.ui'),
      secondTransitionId: second?.existing_transition_id ?? null,
    };
  });

  const persistedState = JSON.parse(savedSelection.activeTransitionRef ?? '{}');
  expect(savedSelection.secondTransitionId).not.toBeNull();
  expect(persistedState.activeTransitionRef).toBe(savedSelection.secondTransitionId);
  await expect(page.getByLabel('From state')).toHaveValue('review');
  await expect(page.getByLabel('To state')).toHaveValue('done');
  await expect(page.locator('textarea[aria-label="Agent criterion 1"]')).toHaveValue('second transition identity');
  await assertNoConsoleErrors(page);
});

test('successful save redirect keeps newer local edits made while the save is pending', async ({ page }) => {
  await page.goto('/admin/workflow-library/blueprints/delivery');
  await page.evaluate(() => {
    const originalFetch = window.fetch.bind(window);
    let releaseSave: (() => void) | null = null;
    let saveCount = 0;
    Object.assign(window, {
      __workflowSaveCount: 0,
      __workflowSaveReady: false,
      __workflowSaveResolved: false,
      __releaseWorkflowSave: () => releaseSave?.(),
    });
    window.fetch = async (input, init) => {
      const request = input instanceof Request ? input : new Request(String(input), init);
      if (request.method === 'POST' && request.url.endsWith('/admin/workflow-library/blueprints/delivery')) {
        saveCount += 1;
        Object.assign(window, { __workflowSaveCount: saveCount });
        const response = await originalFetch(input, init);
        await new Promise<void>((resolve) => {
          releaseSave = resolve;
          Object.assign(window, { __workflowSaveReady: true });
        });
        Object.assign(window, { __workflowSaveResolved: true });
        return response;
      }
      return originalFetch(input, init);
    };
  });

  const description = page.locator('[data-editor-bind="description"]');
  await description.fill('Saved while pending');

  await page.getByRole('button', { name: 'Save', exact: true }).click();
  await expect.poll(() => page.evaluate(() => (window as typeof window & { __workflowSaveCount: number }).__workflowSaveCount)).toBe(1);
  await expect.poll(() => page.evaluate(() => (window as typeof window & { __workflowSaveReady: boolean }).__workflowSaveReady)).toBe(true);

  await description.fill('Newer local draft after save click');
  await page.evaluate(() => (window as typeof window & { __releaseWorkflowSave: () => void }).__releaseWorkflowSave());
  await expect.poll(() => page.evaluate(() => (window as typeof window & { __workflowSaveResolved: boolean }).__workflowSaveResolved)).toBe(true);
  await page.waitForLoadState('networkidle');

  await expect(page).toHaveURL('/admin/workflow-library/blueprints/delivery');
  await expect(page.locator('[data-editor-bind="description"]')).toHaveValue('Newer local draft after save click');
  await expect(page.getByRole('button', { name: 'Save', exact: true })).toBeEnabled();
  await expect(page.locator('[data-editor-status]')).toHaveText('Unsaved changes');
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

  const saveResponse = page.waitForResponse((response) =>
    response.url().includes('/admin/workflow-library/blueprints/delivery') &&
    response.request().method() === 'POST',
  );
  await page.getByRole('button', { name: 'Save', exact: true }).click();
  await saveResponse;
  await expect(page.getByRole('button', { name: 'Save', exact: true })).toBeDisabled();

  const savedPayload = await page.evaluate(() => {
    const node = document.getElementById('workflow-editor-data');
    if (!node?.textContent) throw new Error('Missing workflow editor payload');
    return JSON.parse(node.textContent);
  });
  const completeReview = savedPayload.draft.transitions.find((row: { name: string }) => row.name === 'Complete review');
  const requestChanges = savedPayload.draft.transitions.find((row: { name: string }) => row.name === 'Request changes');
  const sharedField = savedPayload.draft.fields.find((row: { label: string }) => row.label === 'Shared review note');

  expect(sharedField?.existing_field_id).toBeTruthy();
  expect(completeReview?.outputs[2]?.existing_field_id).toBe(sharedField.existing_field_id);
  expect(completeReview?.preconditions[0]?.existing_field_id).toBe(sharedField.existing_field_id);
  expect(requestChanges?.inputs[0]?.existing_field_id).toBe(sharedField.existing_field_id);
  await assertNoConsoleErrors(page);
});

test('typed preconditions, required flags, and duplicate labels keep their real identities after save', async ({ page }) => {
  await page.goto('/admin/workflow-library/blueprints/delivery');
  await page.getByRole('tab', { name: 'Transitions', exact: true }).click();

  await page.getByLabel('Add input').click();
  await page.getByLabel('New field label').fill('Flag');
  await page.getByLabel('New field type').selectOption('boolean');
  await page.getByRole('button', { name: 'Create and add', exact: true }).click();

  await page.getByLabel('Add input').click();
  await page.getByLabel('New field label').fill('Score');
  await page.getByLabel('New field type').selectOption('number');
  await page.getByRole('button', { name: 'Create and add', exact: true }).click();

  await page.getByLabel('Add output').click();
  await page.getByLabel('New field label').fill('Duplicate label');
  await page.getByLabel('New field type').selectOption('text');
  await page.getByRole('button', { name: 'Create and add', exact: true }).click();

  await page.getByLabel('Add output').click();
  await page.getByLabel('New field label').fill('Duplicate label');
  await page.getByLabel('New field type').selectOption('text');
  await page.getByRole('button', { name: 'Create and add', exact: true }).click();

  await page.getByLabel('Input required 2').uncheck();
  await page.getByLabel('Add precondition').click();
  await page.getByLabel('Precondition input 1').selectOption({ label: 'Flag' });
  await expect(page.getByLabel('Precondition value 1')).not.toBeChecked();

  await page.getByLabel('Add precondition').click();
  await page.getByLabel('Precondition input 2').selectOption({ label: 'Score' });
  await page.getByLabel('Precondition value 2').fill('0');

  const saveResponse = page.waitForResponse((response) =>
    response.url().includes('/admin/workflow-library/blueprints/delivery') &&
    response.request().method() === 'POST',
  );
  await page.getByRole('button', { name: 'Save', exact: true }).click();
  await saveResponse;
  await expect(page.getByRole('button', { name: 'Save', exact: true })).toBeDisabled();

  const savedPayload = await page.evaluate(() => {
    const node = document.getElementById('workflow-editor-data');
    if (!node?.textContent) throw new Error('Missing workflow editor payload');
    return JSON.parse(node.textContent);
  });
  const transition = savedPayload.draft.transitions.find((row: { name: string }) => row.name === 'Complete review');
  const flagField = savedPayload.draft.fields.find((row: { label: string; type: string }) => row.label === 'Flag' && row.type === 'boolean');
  const scoreField = savedPayload.draft.fields.find((row: { label: string; type: string }) => row.label === 'Score' && row.type === 'number');
  const duplicateFields = savedPayload.draft.fields.filter((row: { label: string }) => row.label === 'Duplicate label');

  expect(flagField?.existing_field_id).toBeTruthy();
  expect(scoreField?.existing_field_id).toBeTruthy();
  expect(transition?.inputs[1]?.required).toBe(false);
  expect(transition?.preconditions[0]?.existing_field_id).toBe(flagField.existing_field_id);
  expect(transition?.preconditions[0]?.value).toBe(false);
  expect(transition?.preconditions[1]?.existing_field_id).toBe(scoreField.existing_field_id);
  expect(transition?.preconditions[1]?.value).toBe(0);
  expect(duplicateFields).toHaveLength(2);
  expect(new Set(duplicateFields.map((row: { existing_field_id: string }) => row.existing_field_id)).size).toBe(2);
  await assertNoConsoleErrors(page);
});

test('malicious preview issue text is rendered as text and never executes', async ({ page, context }) => {
  await page.addInitScript(() => {
    Object.assign(window, { __workflowEditorXss: 0 });
  });
  await context.route('**/admin/workflow-library/blueprints/delivery/preview', async (route) => {
    await route.fulfill({
      status: 422,
      contentType: 'application/json',
      body: JSON.stringify({
        draft_version: 99,
        draft: {},
        issues: [
          {
            code: 'invalid-draft',
            field: 'draft.name',
            message: '<img src=x onerror="window.__workflowEditorXss=1">',
            hint: '<script>window.__workflowEditorXss=2</script>',
          },
        ],
      }),
    });
  });

  await page.goto('/admin/workflow-library/blueprints/delivery');
  await page.locator('[data-editor-bind="name"]').fill('Trigger preview safely');
  await expect(page.locator('[data-editor-issues]')).toContainText('<img src=x onerror="window.__workflowEditorXss=1">');
  await expect(page.locator('[data-editor-issues]')).toContainText('<script>window.__workflowEditorXss=2</script>');
  await expect.poll(() => page.evaluate(() => (window as typeof window & { __workflowEditorXss: number }).__workflowEditorXss)).toBe(0);
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