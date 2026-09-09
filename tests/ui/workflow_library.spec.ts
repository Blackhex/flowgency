import { readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';

import { expect, test, type Locator, type Page } from '@playwright/test';

import { expectBodyFocus, tabTo } from './keyboard';
import { assertNoLayoutIssues, assertNoConsoleErrors, installBasePageSetup } from './layout';

const runtimeRoot = path.resolve(__dirname, '.runtime', 'current');
const configPath = path.join(runtimeRoot, 'config.yaml');
const deliverySourcePath = path.join(runtimeRoot, 'workflow-library', 'delivery', 'workflow.yaml');

async function readEditorPayload(page: Page) {
  return page.evaluate(() => {
    const node = document.getElementById('workflow-editor-data');
    if (!node?.textContent) throw new Error('Missing workflow editor payload');
    return JSON.parse(node.textContent);
  });
}

async function mutateTextFile(filePath: string, mutate: (content: string) => string) {
  const before = await readFile(filePath, 'utf8');
  const after = mutate(before);
  if (after === before) throw new Error(`Mutation did not change ${filePath}`);
  await writeFile(filePath, after, 'utf8');
  return before;
}

function addTransitionButton(page: Page, projectName: string) {
  return projectName.startsWith('mobile')
    ? page.locator('[data-mobile-transition]').getByLabel('Add transition')
    : page.locator('[data-editor-transition-nav]').getByLabel('Add transition');
}

async function openTransitions(page: Page) {
  await page.getByRole('tab', { name: 'Transitions', exact: true }).click();
}

async function saveEditor(page: Page, blueprintId = 'delivery') {
  const saveResponse = page.waitForResponse((response) =>
    response.url().includes(`/admin/workflow-library/blueprints/${blueprintId}`) &&
    response.request().method() === 'POST',
  );
  await page.getByRole('button', { name: 'Save', exact: true }).click();
  await saveResponse;
}

async function setColor(locator: Locator, value: string) {
  await locator.evaluate((node, nextValue) => {
    const input = node as HTMLInputElement;
    input.value = String(nextValue);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  }, value);
}

test.beforeEach(async ({ page, request }, testInfo) => {
  const reset = await request.post('/__ui/reset');
  expect(reset.status()).toBe(204);
  await installBasePageSetup(page, testInfo.project.name.endsWith('dark') ? 'dark' : 'light');
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

  const addTransition = addTransitionButton(page, testInfo.project.name);
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

test('state editing preserves order, color, initial selection, and recreated IDs across save cycles', async ({ page }) => {
  await page.goto('/admin/workflow-library/blueprints/delivery');
  await page.getByRole('tab', { name: 'States', exact: true }).click();

  const stateNames = page.locator('input[aria-label^="State name"]');
  const stateColors = page.locator('input[type="color"]');

  await stateNames.nth(2).fill('Quality review');
  await setColor(stateColors.nth(2), '#2255aa');
  await page.getByLabel('Make Quality review initial').check();

  await page.getByRole('button', { name: 'Add state', exact: true }).click();
  await stateNames.nth(4).fill('Blocked');
  await setColor(stateColors.nth(4), '#445566');
  await page.getByLabel('Move Blocked up').click();
  await page.getByLabel('Move Blocked up').click();

  await saveEditor(page);
  await expect(page.getByRole('button', { name: 'Save', exact: true })).toBeDisabled();

  const firstSaved = await readEditorPayload(page);
  expect(firstSaved.draft.states.map((row: { name: string }) => row.name)).toEqual([
    'Backlog',
    'In progress',
    'Blocked',
    'Quality review',
    'Done',
  ]);

  const renamedReview = firstSaved.draft.states.find((row: { existing_state_id: string; name: string; color: string; initial: boolean }) => row.existing_state_id === 'review');
  const firstBlocked = firstSaved.draft.states.find((row: { existing_state_id: string | null; name: string; color: string }) => row.name === 'Blocked');

  expect(renamedReview).toMatchObject({
    name: 'Quality review',
    color: '#2255aa',
    initial: true,
  });
  expect(firstBlocked?.color).toBe('#445566');
  expect(firstBlocked?.existing_state_id).toBeTruthy();

  const firstBlockedId = firstBlocked?.existing_state_id;
  await expect(page.getByLabel('Delete Quality review')).toBeDisabled();
  await page.getByLabel('Delete Blocked').click();
  await page.getByRole('button', { name: 'Add state', exact: true }).click();
  await stateNames.nth(4).fill('Blocked');
  await setColor(stateColors.nth(4), '#778899');

  await saveEditor(page);
  const secondSaved = await readEditorPayload(page);
  const recreatedBlocked = secondSaved.draft.states.find((row: { existing_state_id: string | null; name: string; color: string }) => row.name === 'Blocked');

  expect(recreatedBlocked?.existing_state_id).toBeTruthy();
  expect(recreatedBlocked?.existing_state_id).not.toBe(firstBlockedId);
  expect(recreatedBlocked?.color).toBe('#778899');
  await assertNoConsoleErrors(page);
});

test('real save rejects incompatible state removal and leaves workflow source bytes unchanged', async ({ page, request }) => {
  await page.goto('/admin/workflow-library/blueprints/delivery');

  const payload = await readEditorPayload(page);
  payload.draft.states = payload.draft.states.filter(
    (row: { existing_state_id: string | null }) => row.existing_state_id === 'done',
  );
  payload.draft.states[0].initial = true;
  payload.draft.transitions = [];

  const before = await readFile(deliverySourcePath, 'utf8');
  const response = await request.post('/admin/workflow-library/blueprints/delivery', {
    form: {
      payload: JSON.stringify({
        expected_revision: payload.expected_revision,
        expected_digest: payload.expected_digest,
        draft_version: payload.draft_version + 1,
        draft: payload.draft,
      }),
    },
    headers: { Accept: 'application/json' },
  });

  expect(response.status()).toBe(409);
  expect((await response.json()).draft.states).toHaveLength(1);
  expect(await readFile(deliverySourcePath, 'utf8')).toBe(before);
});

test('criterion edits keep saved IDs and recreated criteria get fresh IDs after reload', async ({ page }) => {
  await page.goto('/admin/workflow-library/blueprints/delivery');
  await openTransitions(page);

  const initialPayload = await readEditorPayload(page);
  const originalCriterionId = initialPayload.draft.transitions[0].criteria[0].existing_criterion_id;

  await page.locator('textarea[aria-label="Agent criterion 1"]').fill('Edited criterion text');
  await saveEditor(page);
  await expect(page.getByRole('button', { name: 'Save', exact: true })).toBeDisabled();

  const afterEdit = await readEditorPayload(page);
  expect(afterEdit.draft.transitions[0].criteria[0].existing_criterion_id).toBe(originalCriterionId);
  expect(afterEdit.draft.transitions[0].criteria[0].description).toBe('Edited criterion text');

  await page.getByLabel('Remove agent criterion 1').click();
  await page.getByLabel('Add agent criterion').click();
  await page.locator('textarea[aria-label="Agent criterion 1"]').fill('Replacement criterion text');
  await saveEditor(page);
  await expect(page.getByRole('button', { name: 'Save', exact: true })).toBeDisabled();

  const afterRecreate = await readEditorPayload(page);
  expect(afterRecreate.draft.transitions[0].criteria[0].existing_criterion_id).toBeTruthy();
  expect(afterRecreate.draft.transitions[0].criteria[0].existing_criterion_id).not.toBe(originalCriterionId);
  expect(afterRecreate.draft.transitions[0].criteria[0].description).toBe('Replacement criterion text');
  await assertNoConsoleErrors(page);
});

test('real stale config save keeps the local draft visible', async ({ page }) => {
  await page.goto('/admin/workflow-library/blueprints/delivery');

  const description = page.locator('[data-editor-bind="description"]');
  await description.fill('Keep stale config draft');
  const originalConfig = await mutateTextFile(configPath, (content) =>
    content.replace('title: Flowgency UI Gate', 'title: Flowgency UI Gate drifted'),
  );

  try {
    await saveEditor(page);

    await expect(description).toHaveValue('Keep stale config draft');
    await expect(page.locator('[data-editor-issues]')).toContainText('Workflow source changed before save completed.');
    await expect(page.locator('[data-editor-warning]')).toHaveText('Reload before saving again.');
    await expect(page.locator('[data-editor-status]')).toHaveText('Unsaved changes');
    await expect(page.getByRole('button', { name: 'Save', exact: true })).toBeEnabled();
  } finally {
    await writeFile(configPath, originalConfig, 'utf8');
  }
});

test('real stale source save keeps the local draft visible', async ({ page }) => {
  await page.goto('/admin/workflow-library/blueprints/delivery');

  const description = page.locator('[data-editor-bind="description"]');
  await description.fill('Keep stale source draft');
  await mutateTextFile(deliverySourcePath, (content) =>
    content.replace('description: Deliver verified work.', 'description: External source changed on disk.'),
  );

  await saveEditor(page);

  await expect(description).toHaveValue('Keep stale source draft');
  await expect(page.locator('[data-editor-issues]')).toContainText('Workflow source changed before save completed.');
  await expect(page.locator('[data-editor-warning]')).toHaveText('Reload before saving again.');
  await expect(page.locator('[data-editor-status]')).toHaveText('Unsaved changes');
  await expect(page.getByRole('button', { name: 'Save', exact: true })).toBeEnabled();
});

test('malformed external workflow source keeps the library listing safe and renders the editor unavailable', async ({ page }) => {
  await mutateTextFile(deliverySourcePath, () => 'not: [valid');

  await page.goto('/admin/workflow-library');
  await expect(page.getByRole('heading', { name: 'Workflow Library', exact: true })).toBeVisible();
  await expect(page.locator('a[href="/admin/workflow-library/blueprints/delivery"]')).toContainText(/delivery/i);

  await page.goto('/admin/workflow-library/blueprints/delivery');
  await expect(page.getByRole('heading', { name: 'delivery', exact: true })).toBeVisible();
  await expect(page.locator('[data-editor-warning]')).toHaveText('Current workflow source is unavailable until the file is repaired.');
  await expect(page.locator('[data-editor-issues]')).toContainText('Current workflow source is unavailable.');
  await expect(page.getByRole('button', { name: 'Save', exact: true })).toBeDisabled();
  await expect(page.locator('input[aria-label^="State name"]')).toHaveCount(0);
});

test('delayed preview responses do not replace newer edits', async ({ page }) => {
  await page.goto('/admin/workflow-library/blueprints/delivery');
  await page.evaluate(() => {
    const realFetch = window.fetch.bind(window);
    let previewCount = 0;
    let releaseFirstPreview: (() => void) | null = null;
    Object.assign(window, {
      __workflowPreviewCount: 0,
      __releaseWorkflowPreview: () => releaseFirstPreview?.(),
    });
    window.fetch = async (input, init) => {
      const url = typeof input === 'string' ? input : input.url;
      if (!url.endsWith('/admin/workflow-library/blueprints/delivery/preview')) {
        return realFetch(input, init);
      }
      previewCount += 1;
      Object.assign(window, { __workflowPreviewCount: previewCount });
      const params = new URLSearchParams(String(init?.body ?? ''));
      const request = JSON.parse(params.get('payload') ?? '{}') as {
        draft_version: number;
        draft: { name: string };
      };
      if (previewCount === 1) {
        await new Promise<void>((resolve) => {
          releaseFirstPreview = resolve;
        });
      }
      return new Response(JSON.stringify({
        draft_version: request.draft_version,
        draft: request.draft,
        issues: [
          {
            code: 'invalid-draft',
            field: 'draft.name',
            message: `${request.draft.name} issue`,
            hint: 'Fix the latest value.',
          },
        ],
      }), {
        status: 422,
        headers: { 'Content-Type': 'application/json' },
      });
    };
  });
  const name = page.locator('[data-editor-bind="name"]');

  await name.fill('First preview');
  await expect.poll(() => page.evaluate(() => (window as typeof window & { __workflowPreviewCount: number }).__workflowPreviewCount)).toBe(1);
  await name.fill('Second preview');
  await expect.poll(() => page.evaluate(() => (window as typeof window & { __workflowPreviewCount: number }).__workflowPreviewCount)).toBe(2);

  await expect(page.locator('[data-editor-issues]')).toContainText('Second preview issue');
  await page.evaluate(() => (window as typeof window & { __releaseWorkflowPreview: () => void }).__releaseWorkflowPreview());
  await expect(page.locator('[data-editor-issues]')).not.toContainText('First preview issue');
  await expect(name).toHaveValue('Second preview');
  await assertNoConsoleErrors(page);
});

test('delayed preview responses do not replace a newer revert', async ({ page }) => {
  await page.goto('/admin/workflow-library/blueprints/delivery');
  await page.evaluate(() => {
    const realFetch = window.fetch.bind(window);
    let previewCount = 0;
    let releaseFirstPreview: (() => void) | null = null;
    Object.assign(window, {
      __workflowPreviewCount: 0,
      __releaseWorkflowPreview: () => releaseFirstPreview?.(),
    });
    window.fetch = async (input, init) => {
      const url = typeof input === 'string' ? input : input.url;
      if (!url.endsWith('/admin/workflow-library/blueprints/delivery/preview')) {
        return realFetch(input, init);
      }
      previewCount += 1;
      Object.assign(window, { __workflowPreviewCount: previewCount });
      const params = new URLSearchParams(String(init?.body ?? ''));
      const request = JSON.parse(params.get('payload') ?? '{}') as {
        draft_version: number;
        draft: { name: string };
      };
      if (previewCount === 1) {
        await new Promise<void>((resolve) => {
          releaseFirstPreview = resolve;
        });
      }
      return new Response(JSON.stringify({
        draft_version: request.draft_version,
        draft: request.draft,
        issues: [
          {
            code: 'invalid-draft',
            field: 'draft.name',
            message: 'Late preview issue',
            hint: 'This should not survive revert.',
          },
        ],
      }), {
        status: 422,
        headers: { 'Content-Type': 'application/json' },
      });
    };
  });
  const name = page.locator('[data-editor-bind="name"]');

  await name.fill('Pending preview before revert');
  await expect.poll(() => page.evaluate(() => (window as typeof window & { __workflowPreviewCount: number }).__workflowPreviewCount)).toBe(1);
  await page.getByRole('button', { name: 'Revert', exact: true }).click();
  await page.evaluate(() => (window as typeof window & { __releaseWorkflowPreview: () => void }).__releaseWorkflowPreview());

  await expect(name).toHaveValue('Delivery');
  await expect(page.locator('[data-editor-issues]')).toBeHidden();
  await expect(page.locator('[data-editor-status]')).toHaveText('Saved');
  await expect(page.getByRole('button', { name: 'Save', exact: true })).toBeDisabled();
  await assertNoConsoleErrors(page);
});

test('delayed preview responses do not overwrite a successful save', async ({ page }) => {
  await page.goto('/admin/workflow-library/blueprints/delivery');
  await page.evaluate(() => {
    const realFetch = window.fetch.bind(window);
    let previewCount = 0;
    let releaseFirstPreview: (() => void) | null = null;
    Object.assign(window, {
      __workflowPreviewCount: 0,
      __releaseWorkflowPreview: () => releaseFirstPreview?.(),
    });
    window.fetch = async (input, init) => {
      const url = typeof input === 'string' ? input : input.url;
      if (!url.endsWith('/admin/workflow-library/blueprints/delivery/preview')) {
        return realFetch(input, init);
      }
      previewCount += 1;
      Object.assign(window, { __workflowPreviewCount: previewCount });
      const params = new URLSearchParams(String(init?.body ?? ''));
      const request = JSON.parse(params.get('payload') ?? '{}') as {
        draft_version: number;
        draft: { name: string };
      };
      if (previewCount === 1) {
        await new Promise<void>((resolve) => {
          releaseFirstPreview = resolve;
        });
      }
      return new Response(JSON.stringify({
        draft_version: request.draft_version,
        draft: request.draft,
        issues: [
          {
            code: 'invalid-draft',
            field: 'draft.name',
            message: 'Late preview issue',
            hint: 'This should not overwrite save.',
          },
        ],
      }), {
        status: 422,
        headers: { 'Content-Type': 'application/json' },
      });
    };
  });
  await page.locator('[data-editor-bind="name"]').fill('Saved before preview returns');
  await expect.poll(() => page.evaluate(() => (window as typeof window & { __workflowPreviewCount: number }).__workflowPreviewCount)).toBe(1);

  await saveEditor(page);
  await expect(page.getByRole('heading', { name: 'Saved before preview returns', exact: true })).toBeVisible();
  await page.evaluate(() => (window as typeof window & { __releaseWorkflowPreview: () => void }).__releaseWorkflowPreview());

  await expect(page.locator('[data-editor-issues]')).toBeHidden();
  await expect(page.locator('[data-editor-warning]')).toBeHidden();
  await expect(page.locator('[data-editor-status]')).toHaveText('Saved');
  await expect(page.getByRole('button', { name: 'Save', exact: true })).toBeDisabled();
  await assertNoConsoleErrors(page);
});

test('workflow editor tabs stay keyboard reachable across overview, states, and transitions', async ({ page }, testInfo) => {
  await page.goto('/admin/workflow-library/blueprints/delivery');

  await expectBodyFocus(page);
  const overviewTab = await tabTo(page, { role: 'tab', name: 'Overview' });
  await expect(overviewTab).toBeFocused();

  const nameField = await tabTo(page, { role: 'textbox', name: 'Blueprint name' });
  await expect(nameField).toBeFocused();

  await page.getByRole('tab', { name: 'States', exact: true }).focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('tab', { name: 'States', exact: true })).toHaveAttribute('aria-selected', 'true');
  const addState = await tabTo(page, { role: 'button', name: 'Add state' }, { maxTabs: 4 });
  await expect(addState).toBeFocused();

  await page.getByRole('tab', { name: 'Transitions', exact: true }).focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('tab', { name: 'Transitions', exact: true })).toHaveAttribute('aria-selected', 'true');
  if (testInfo.project.name.startsWith('mobile')) {
    await expect(page.getByLabel('Transition picker')).toBeVisible();
    await page.getByLabel('Transition picker').focus();
    await expect(page.getByLabel('Transition picker')).toBeFocused();
  } else {
    const navButton = page.locator('[data-editor-transition-nav]').getByRole('button').nth(1);
    await navButton.focus();
    await expect(navButton).toBeFocused();
  }
  await assertNoConsoleErrors(page);
});

test('320px workflow editor uses the mobile transition picker without losing keyboard access', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 900 });
  await page.goto('/admin/workflow-library/blueprints/delivery');

  await page.getByRole('tab', { name: 'Transitions', exact: true }).focus();
  await page.keyboard.press('Enter');
  await expect(page.getByLabel('Transition picker')).toBeVisible();
  await expect(page.locator('[data-editor-transition-nav]')).toBeHidden();

  await page.getByLabel('Transition picker').focus();
  await expect(page.getByLabel('Transition picker')).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(page.locator('[data-mobile-transition]').getByLabel('Add transition')).toBeFocused();
  await assertNoLayoutIssues(page);
  await assertNoConsoleErrors(page);
});

test('reused fields stay linked across transitions and preconditions', async ({ page }, testInfo) => {
  await page.goto('/admin/workflow-library/blueprints/delivery');
  await page.getByRole('tab', { name: 'Transitions', exact: true }).click();

  await page.getByLabel('Add output').click();
  await page.getByLabel('New field label').fill('Shared note');
  await page.getByLabel('New field type').selectOption('text');
  await page.getByRole('button', { name: 'Create and add', exact: true }).click();

  await page.getByLabel('Add input').click();
  await page.getByRole('button', { name: 'Shared note (Text)', exact: true }).click();

  await page.getByLabel('Add precondition').click();
  await page.getByLabel('Precondition input 1').selectOption({ label: 'Shared note' });
  await page.getByLabel('Precondition value 1').fill('ready');

  await addTransitionButton(page, testInfo.project.name).click();
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
  expect(completeReview?.inputs[1]?.existing_field_id).toBe(sharedField.existing_field_id);
  expect(completeReview?.outputs[2]?.existing_field_id).toBe(sharedField.existing_field_id);
  expect(completeReview?.preconditions[0]?.existing_field_id).toBe(sharedField.existing_field_id);
  expect(requestChanges?.inputs[0]?.existing_field_id).toBe(sharedField.existing_field_id);

  // Reload: server must persist canonical field ID through all references
  const sharedFieldId = sharedField.existing_field_id as string;
  await page.reload();

  const freshPayload = await readEditorPayload(page);
  const freshCompleteReview = freshPayload.draft.transitions.find((row: { name: string }) => row.name === 'Complete review');
  const freshRequestChanges = freshPayload.draft.transitions.find((row: { name: string }) => row.name === 'Request changes');
  const freshSharedField = freshPayload.draft.fields.find((row: { existing_field_id: string }) => row.existing_field_id === sharedFieldId);

  expect(freshSharedField?.existing_field_id).toBe(sharedFieldId);
  expect(freshSharedField?.label).toBe('Shared review note');
  expect(freshCompleteReview?.inputs[1]?.existing_field_id).toBe(sharedFieldId);
  expect(freshCompleteReview?.outputs[2]?.existing_field_id).toBe(sharedFieldId);
  expect(freshCompleteReview?.preconditions[0]?.existing_field_id).toBe(sharedFieldId);
  expect(freshRequestChanges?.inputs[0]?.existing_field_id).toBe(sharedFieldId);

  // Verify the renamed label renders in the UI after reload
  await openTransitions(page);
  if (testInfo.project.name.startsWith('mobile')) {
    await page.getByLabel('Transition picker').selectOption({ label: 'Complete review' });
  } else {
    await page.getByRole('button', { name: 'Complete review Review Done' }).click();
  }
  await expect(page.getByLabel('Output label 3')).toHaveValue('Shared review note');
  await expect(page.getByLabel('Input label 2')).toHaveValue('Shared review note');
  await expect(page.getByLabel('Precondition input 1')).toHaveText(/Shared review note/);
  await assertNoConsoleErrors(page);
});

test('preconditions only offer declared inputs and preserve invalid refs for correction', async ({ page }) => {
  await page.goto('/admin/workflow-library/blueprints/delivery');
  await openTransitions(page);

  await page.getByLabel('Add output').click();
  await page.getByLabel('New field label').fill('Internal note');
  await page.getByLabel('New field type').selectOption('text');
  await page.getByRole('button', { name: 'Create and add', exact: true }).click();

  await page.getByLabel('Add precondition').click();

  await expect.poll(async () => page.getByLabel('Precondition input 1').locator('option').allTextContents()).toEqual([
    'Acceptance criteria',
  ]);

  await page.getByLabel('Add input').click();
  await page.getByRole('button', { name: 'Review verdict (Text)', exact: true }).click();

  await expect
    .poll(async () => page.getByLabel('Precondition input 1').locator('option').allTextContents())
    .toEqual(['Acceptance criteria', 'Review verdict']);

  await page.getByLabel('Precondition input 1').selectOption({ label: 'Review verdict' });
  await page.getByLabel('Precondition value 1').fill('approved');
  const selectedFieldId = await page.getByLabel('Precondition input 1').inputValue();

  await page.getByLabel('Remove input field 2').click();

  await expect(page.getByLabel('Precondition input 1')).toHaveValue(selectedFieldId);
  await expect(page.getByLabel('Precondition input 1').locator('option:checked')).toContainText('Review verdict');

  await saveEditor(page);

  await expect(page.locator('[data-editor-issues]')).toContainText('Workflow draft has invalid structure or references.');
  await expect(page.locator('[data-editor-warning]')).toHaveText('Correct the highlighted issues before saving.');
  await expect(page.locator('[data-editor-status]')).toHaveText('Unsaved changes');
  await expect(page.getByRole('button', { name: 'Save', exact: true })).toBeEnabled();
  await expect(page.getByLabel('Precondition input 1')).toHaveValue(selectedFieldId);
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