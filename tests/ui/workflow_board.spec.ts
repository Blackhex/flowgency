import { expect, test, type APIRequestContext, type Page } from '@playwright/test';
import { readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';

import { expectBodyFocus, tabTo } from './keyboard';
import { assertNoLayoutIssues, assertNoConsoleErrors, installBasePageSetup } from './layout';

type DetailSnapshot = {
  ticket: {
    version: unknown;
    ref: { ticket_id: string };
    assignee: string | null;
  };
  fields: Array<{ id: string; value: unknown; is_output: boolean }>;
};

type PollProbe = typeof window & { __pollTimer?: number };

const WORKFLOW_HANDLE_KEY = 'workflow';

const runtimeConfigPath = path.join(__dirname, '.runtime', 'current', 'config.yaml');

function operationId(label: string): string {
  return `${label}-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

async function resetUiRuntime(request: APIRequestContext): Promise<void> {
  const response = await request.post('/__ui/reset');
  expect(response.status()).toBe(204);
}

async function detailSnapshot(request: APIRequestContext, ticketId = 'fixture-review'): Promise<DetailSnapshot> {
  const response = await request.get(`/newsletter/workflows/delivery/tickets/${ticketId}/snapshot`);
  expect(response.ok()).toBeTruthy();
  return await response.json() as DetailSnapshot;
}

async function replaceRuntimeConfigAgent(fromName: string, toName: string): Promise<void> {
  const config = await readFile(runtimeConfigPath, 'utf8');
  const updated = config.replace(`- name: ${fromName}`, `- name: ${toName}`);
  expect(updated).not.toBe(config);
  await writeFile(runtimeConfigPath, updated, 'utf8');
}

async function waitForWorkflowController(page: Parameters<typeof test.beforeEach>[0]['page']): Promise<void> {
  await page.waitForFunction(() => Boolean((window as typeof window & { workflowBoardController?: unknown }).workflowBoardController));
}

// The coordinator owns the cadence, so the probe records its latest 2000 ms timer for tests to clear.
async function installPollTimerProbe(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const nativeSetTimeout = window.setTimeout.bind(window);
    window.setTimeout = ((handler: TimerHandler, delay?: number, ...args: unknown[]) => {
      const id = nativeSetTimeout(handler, delay, ...args);
      if (delay === 2000) (window as PollProbe).__pollTimer = id;
      return id;
    }) as typeof window.setTimeout;
  });
}

async function stopPollTimer(page: Page): Promise<void> {
  await waitForWorkflowController(page);
  await page.evaluate(() => clearTimeout((window as PollProbe).__pollTimer));
}

async function forcePoll(page: Page): Promise<void> {
  await page.evaluate(async (key) => {
    clearTimeout((window as PollProbe).__pollTimer);
    await window.FlowgencyLive.handles.get(key)!.refresh();
    clearTimeout((window as PollProbe).__pollTimer);
  }, WORKFLOW_HANDLE_KEY);
}

async function workflowHandleEtag(page: Page): Promise<string | null> {
  return page.evaluate((key) => window.FlowgencyLive.handles.get(key)!.etag, WORKFLOW_HANDLE_KEY);
}

async function startPoll(page: Page): Promise<void> {
  await page.evaluate((key) => void window.FlowgencyLive.handles.get(key)!.refresh(), WORKFLOW_HANDLE_KEY);
}

async function installHeldPollBody(page: Page): Promise<void> {
  await page.evaluate(() => {
    const probeWindow = window as typeof window & {
      releasePollBody?: () => void;
      pollBodyHeld?: boolean;
    };
    const originalFetch = window.fetch.bind(window);
    const held = new Promise<void>((resolve) => {
      probeWindow.releasePollBody = resolve;
    });
    let heldOnce = false;
    window.fetch = async (input, init) => {
      const response = await originalFetch(input, init);
      if (!heldOnce && String(input).includes('/workflows/delivery/snapshot') && response.status === 200) {
        heldOnce = true;
        // The shared transport reads the body as text; hold both readers.
        const readers = response as unknown as Record<'json' | 'text', () => Promise<unknown>>;
        for (const reader of ['json', 'text'] as const) {
          const original = readers[reader].bind(response);
          readers[reader] = async () => {
            probeWindow.pollBodyHeld = true;
            await held;
            return original();
          };
        }
      }
      return response;
    };
  });
}

test.beforeEach(async ({ page, request }, testInfo) => {
  await resetUiRuntime(request);
  await installBasePageSetup(page, testInfo.project.name.endsWith('dark') ? 'dark' : 'light');
  await installPollTimerProbe(page);
});

test.afterEach(async ({ page, request }) => {
  await page.close();
  await resetUiRuntime(request);
});

test('board page exposes approved toolbar and inspector controls', async ({ page }) => {
  await page.goto('/newsletter/workflows/delivery?ticket=fixture-review');

  await expect(page.getByRole('heading', { name: 'Delivery' })).toBeVisible();
  await expect(page.getByText('8 tickets', { exact: true })).toBeVisible();
  await expect(page.getByText('2 working', { exact: true })).toBeVisible();
  await expect(page.getByText('FG-101', { exact: true })).toBeVisible();
  await expect(page.getByText('FG-108', { exact: true })).toBeVisible();
  await expect(page.getByLabel('Search tickets', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'New ticket', exact: true })).toBeVisible();
  await expect(page.getByLabel('Assigned agent', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Run', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Assign', exact: true })).toHaveCount(0);
  await expect(page.locator('[draggable="true"]')).toHaveCount(0);
  await assertNoLayoutIssues(page);
  await assertNoConsoleErrors(page);
});

test('team sidebar exposes workflow library and a live route', async ({ page }, testInfo) => {
  await page.goto('/newsletter/workflows/delivery?ticket=fixture-review');

  if (testInfo.project.name.startsWith('mobile')) {
    await page.getByRole('button', { name: 'Open navigation', exact: true }).click();
  }

  await expect(page.getByRole('link', { name: 'Workflow Library', exact: true })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Observations', exact: true })).toHaveCount(0);
  await expect(page.getByRole('link', { name: 'Proposals', exact: true })).toHaveCount(0);
  await expect(page.getByRole('link', { name: 'Decisions', exact: true })).toHaveCount(0);

  await page.getByRole('link', { name: 'Workflow Library', exact: true }).click();
  await expect(page).toHaveURL('/admin/workflow-library');
  await expect(page.getByRole('heading', { name: 'Workflow Library' })).toBeVisible();
  await assertNoConsoleErrors(page);
});

test('assignee controls use the configured team agent set in html and js', async ({ page }) => {
  await replaceRuntimeConfigAgent('researcher', 'qa-lead');

  await page.goto('/newsletter/workflows/delivery?ticket=fixture-review');

  await expect(page.locator('#ticket-assignee option[value="qa-lead"]')).toHaveText('qa-lead');
  await expect(page.locator('#ticket-assignee option[value="researcher"]')).toHaveCount(0);
  await expect(page.locator('#workflow-assignee option[value="qa-lead"]')).toHaveText('qa-lead');
  await expect(page.locator('#workflow-assignee option[value="researcher"]')).toHaveCount(0);

  const saveResponse = page.waitForResponse((response) => response.url().includes('/tickets/fixture-review/assignee') && response.request().method() === 'POST');
  await page.getByLabel('Assigned agent', { exact: true }).selectOption('qa-lead');
  await saveResponse;

  await expect(page.getByLabel('Assigned agent', { exact: true })).toHaveValue('qa-lead');
  const saved = await detailSnapshot(page.request);
  expect(saved.ticket.assignee).toBe('qa-lead');
  await assertNoConsoleErrors(page);
});

test('board, ticket detail, and settings avoid clipping at a 320px viewport', async ({ page }) => {
  // The narrowest supported width with the longest fixture labels. The Kanban
  // track may scroll (it is .overflow-x-auto), but no control, form, or heading
  // may clip and the document must not overflow horizontally.
  await page.setViewportSize({ width: 320, height: 900 });

  await page.goto('/newsletter/workflows/delivery?ticket=fixture-review');
  await expect(page.getByRole('heading', { name: 'Delivery' })).toBeVisible();
  await expect(page.getByLabel('Search tickets', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Run', exact: true })).toBeVisible();
  await expect(
    page
      .getByLabel('Ticket details')
      .getByRole('heading', { name: 'Validate stale transition handling', exact: true }),
  ).toBeVisible();
  await assertNoLayoutIssues(page);

  await page.goto('/newsletter/workflows/delivery/tickets/fixture-review');
  await expect(page.getByRole('button', { name: 'Back to board', exact: true })).toBeVisible();
  await assertNoLayoutIssues(page);

  await page.goto('/newsletter/workflows/delivery/settings');
  await expect(page.locator('h1')).toContainText('Delivery settings');
  await expect(page.getByLabel('Storage root', { exact: true })).toBeVisible();
  await assertNoLayoutIssues(page);
  await assertNoConsoleErrors(page);
});

test('assignee selection saves without moving the ticket or losing a draft', async ({ page }) => {
  await page.goto('/newsletter/workflows/delivery?ticket=fixture-review');

  await page.getByLabel('Acceptance criteria', { exact: true }).fill('Keep this unsaved note');
  const saveResponse = page.waitForResponse((response) => response.url().includes('/tickets/fixture-review/assignee') && response.request().method() === 'POST');
  await page.getByLabel('Assigned agent', { exact: true }).selectOption('builder');
  await saveResponse;

  await expect(page.getByRole('button', { name: 'Assign', exact: true })).toHaveCount(0);
  await expect(page.getByLabel('Acceptance criteria', { exact: true })).toHaveValue('Keep this unsaved note');
  await expect(page.locator('[data-ticket-state]')).toHaveText('Review');

  const saved = await page.request.get('/newsletter/workflows/delivery/tickets/fixture-review/snapshot');
  expect((await saved.json()).ticket.assignee).toBe('builder');

  await assertNoConsoleErrors(page);
});

test('focused assignment uses its original version and exposes a remote conflict', async ({ page, request }) => {
  await page.goto('/newsletter/workflows/delivery?ticket=fixture-review');
  await stopPollTimer(page);
  await page.getByLabel('Acceptance criteria', { exact: true }).fill('Keep my local draft');
  const original = await detailSnapshot(request);
  await page.locator('#ticket-assignee').focus();
  const heldAssignee = await page.locator('#ticket-assignee').elementHandle();
  const remote = await request.post('/newsletter/workflows/delivery/tickets/fixture-review/assignee', {
    headers: { Accept: 'application/json' },
    form: { payload: JSON.stringify({
      version: original.ticket.version,
      operation_id: operationId('remote-assignment'),
      assignee: 'researcher',
    }) },
  });
  expect(remote.ok()).toBeTruthy();
  await forcePoll(page);
  expect(await heldAssignee!.evaluate((node) => node.isConnected && node === document.activeElement)).toBe(true);
  await expect(page.locator('#ticket-assignee')).toHaveValue(original.ticket.assignee || '');
  const posted = page.waitForRequest((requestEvent) => requestEvent.method() === 'POST' && requestEvent.url().endsWith('/fixture-review/assignee'));
  const conflicted = page.waitForResponse((response) => response.request().method() === 'POST' && response.url().endsWith('/fixture-review/assignee'));
  await page.locator('#ticket-assignee').selectOption('builder');
  const form = new URLSearchParams((await posted).postData() || '');
  expect(JSON.parse(form.get('payload') || '{}').version).toEqual(original.ticket.version);
  expect((await conflicted).status()).toBe(409);
  expect((await detailSnapshot(request)).ticket.assignee).toBe('researcher');
  await expect(page.locator('#ticket-assignee')).toHaveValue('researcher');
  await expect(page.locator('#workflow-action-errors')).toBeVisible();
  await expect(page.getByLabel('Acceptance criteria', { exact: true })).toHaveValue('Keep my local draft');

  const refreshed = await detailSnapshot(request);
  const retryPosted = page.waitForRequest((requestEvent) => requestEvent.method() === 'POST' && requestEvent.url().endsWith('/fixture-review/assignee'));
  const retried = page.waitForResponse((response) => response.request().method() === 'POST' && response.url().endsWith('/fixture-review/assignee'));
  await page.locator('#ticket-assignee').selectOption('builder');
  const retryForm = new URLSearchParams((await retryPosted).postData() || '');
  expect(JSON.parse(retryForm.get('payload') || '{}').version).toEqual(refreshed.ticket.version);
  expect((await retried).status()).toBe(303);
  expect((await detailSnapshot(request)).ticket.assignee).toBe('builder');
  await expect(page.locator('#ticket-assignee')).toHaveValue('builder');
  await expect(page.locator('#workflow-action-errors')).toBeHidden();
  await expect(page.getByLabel('Acceptance criteria', { exact: true })).toHaveValue('Keep my local draft');
});

test('passive queued run status does not disable a held assignee select', async ({ page, request }) => {
  await page.goto('/newsletter/workflows/delivery?ticket=fixture-review');
  await stopPollTimer(page);
  const assigneeSelect = page.locator('#ticket-assignee');
  const originalNode = await assigneeSelect.elementHandle();
  expect(originalNode).not.toBeNull();

  await assigneeSelect.focus();
  const original = await detailSnapshot(request);
  const remoteRun = await request.post('/newsletter/workflows/delivery/tickets/fixture-review/run', {
    headers: { Accept: 'application/json' },
    form: { payload: JSON.stringify({
      version: original.ticket.version,
      operation_id: operationId('remote-run-held-assignee'),
    }) },
  });
  expect(remoteRun.ok()).toBeTruthy();
  await forcePoll(page);

  expect(await originalNode!.evaluate((node) => node.isConnected && node === document.getElementById('ticket-assignee'))).toBe(true);
  await expect(assigneeSelect).toBeFocused();
  await expect(assigneeSelect).toBeEnabled();
  await expect(page.locator('[data-ticket-run-status]')).toContainText('Queued');
  await expect(page.getByRole('button', { name: 'Run', exact: true })).toBeDisabled();

  await page.getByRole('button', { name: 'Save inputs', exact: true }).focus();
  await expect(assigneeSelect).toBeDisabled();
});

test('unrelated held input reply does not disable a held assignee select', async ({ page, context, request }) => {
  let releaseInputResponse: () => void = () => undefined;
  const heldInputResponse = new Promise<void>((resolve) => {
    releaseInputResponse = resolve;
  });
  await context.route('**/tickets/fixture-review/update', async (route) => {
    const upstream = await route.fetch();
    const updated = await detailSnapshot(request);
    const remoteRun = await request.post('/newsletter/workflows/delivery/tickets/fixture-review/run', {
      headers: { Accept: 'application/json' },
      form: { payload: JSON.stringify({
        version: updated.ticket.version,
        operation_id: operationId('held-input-remote-run'),
      }) },
    });
    expect(remoteRun.ok()).toBeTruthy();
    const queued = await detailSnapshot(request);
    await releaseInputResponse;
    await route.fulfill({
      status: upstream.status(),
      headers: { ...upstream.headers(), 'content-type': 'application/json' },
      body: JSON.stringify(queued),
    });
  }, { times: 1 });

  await page.goto('/newsletter/workflows/delivery?ticket=fixture-review');
  const assigneeSelect = page.locator('#ticket-assignee');
  const originalNode = await assigneeSelect.elementHandle();
  expect(originalNode).not.toBeNull();
  await page.getByLabel('Acceptance criteria', { exact: true }).fill('Submitted while assignee select is held');
  await assigneeSelect.focus();
  await assigneeSelect.evaluate((node) => {
    (node as HTMLSelectElement).value = 'builder';
  });
  const inputResponse = page.waitForResponse((response) => response.url().includes('/tickets/fixture-review/update') && response.request().method() === 'POST');
  const savePromise = page.evaluate(() => (window as typeof window & {
    workflowBoardController: { saveInputs: () => Promise<void> };
  }).workflowBoardController.saveInputs());
  releaseInputResponse();
  await inputResponse;
  await savePromise;

  expect(await originalNode!.evaluate((node) => node.isConnected && node === document.getElementById('ticket-assignee'))).toBe(true);
  await expect(assigneeSelect).toBeFocused();
  await expect(assigneeSelect).toHaveValue('builder');
  await expect(assigneeSelect).toBeEnabled();
  await expect(page.locator('[data-ticket-run-status]')).toContainText('Queued');
  await expect(page.getByRole('button', { name: 'Run', exact: true })).toBeDisabled();

  await page.getByRole('button', { name: 'Save inputs', exact: true }).focus();
  await expect(assigneeSelect).toBeDisabled();
  expect((await detailSnapshot(request)).fields.find((field) => field.id === 'acceptance-criteria')?.value).toBe('Submitted while assignee select is held');
});

test('remote input change before typing in a clean focused field remains a conflict', async ({ page, request }) => {
  await page.goto('/newsletter/workflows/delivery?ticket=fixture-review');
  await stopPollTimer(page);
  const field = page.getByLabel('Acceptance criteria', { exact: true });
  const originalValue = await field.inputValue();
  const original = await detailSnapshot(request);

  await field.focus();
  const remoteUpdate = await request.post('/newsletter/workflows/delivery/tickets/fixture-review/update', {
    headers: { Accept: 'application/json' },
    form: { payload: JSON.stringify({
      version: original.ticket.version,
      operation_id: operationId('remote-before-clean-typing'),
      patch: { field_values: { 'acceptance-criteria': 'Remote clean focused change' } },
    }) },
  });
  expect(remoteUpdate.ok()).toBeTruthy();
  await forcePoll(page);

  await expect(field).toHaveValue(originalValue);
  await field.fill('Local typing after remote clean change');
  const conflictResponse = page.waitForResponse((response) => response.url().includes('/tickets/fixture-review/update') && response.request().method() === 'POST');
  await page.getByRole('button', { name: 'Save inputs', exact: true }).click();
  expect((await conflictResponse).status()).toBe(409);
  expect((await detailSnapshot(request)).fields.find((remoteField) => remoteField.id === 'acceptance-criteria')?.value).toBe('Remote clean focused change');
  await expect(field).toHaveValue('Local typing after remote clean change');
});

test('deferred clean focused input release applies the latest server value', async ({ page, request }) => {
  await page.goto('/newsletter/workflows/delivery?ticket=fixture-review');
  await stopPollTimer(page);
  const field = page.getByLabel('Acceptance criteria', { exact: true });
  const originalValue = await field.inputValue();
  const original = await detailSnapshot(request);

  await field.focus();
  const remoteUpdate = await request.post('/newsletter/workflows/delivery/tickets/fixture-review/update', {
    headers: { Accept: 'application/json' },
    form: { payload: JSON.stringify({
      version: original.ticket.version,
      operation_id: operationId('deferred-clean-release'),
      patch: { field_values: { 'acceptance-criteria': 'Released clean server value' } },
    }) },
  });
  expect(remoteUpdate.ok()).toBeTruthy();
  await forcePoll(page);

  await expect(field).toHaveValue(originalValue);
  await page.getByRole('button', { name: 'Run', exact: true }).focus();
  await expect(field).toHaveValue('Released clean server value');
});

test('in-flight draft typing survives assignment and input replies', async ({ page, context, request }) => {
  let releaseAssigneeResponse: () => void = () => undefined;
  let releaseInputResponse: () => void = () => undefined;
  const heldAssigneeResponse = new Promise<void>((resolve) => {
    releaseAssigneeResponse = resolve;
  });
  const heldInputResponse = new Promise<void>((resolve) => {
    releaseInputResponse = resolve;
  });
  await context.route('**/tickets/fixture-review/assignee', async (route) => {
    const upstream = await route.fetch();
    await heldAssigneeResponse;
    await route.fulfill({ response: upstream });
  }, { times: 1 });
  await context.route('**/tickets/fixture-review/update', async (route) => {
    const upstream = await route.fetch();
    await heldInputResponse;
    await route.fulfill({ response: upstream });
  }, { times: 1 });

  await page.goto('/newsletter/workflows/delivery?ticket=fixture-review');
  const assignmentResponse = page.waitForResponse((response) => response.url().includes('/tickets/fixture-review/assignee') && response.request().method() === 'POST');
  await page.locator('#ticket-assignee').selectOption('builder');
  await page.getByLabel('Acceptance criteria', { exact: true }).fill('Typed while assignment response is held');
  releaseAssigneeResponse();
  await assignmentResponse;
  await expect(page.getByLabel('Acceptance criteria', { exact: true })).toHaveValue('Typed while assignment response is held');

  const inputResponse = page.waitForResponse((response) => response.url().includes('/tickets/fixture-review/update') && response.request().method() === 'POST');
  await page.getByRole('button', { name: 'Save inputs', exact: true }).click();
  await page.getByLabel('Acceptance criteria', { exact: true }).fill('Typed while input response is held');
  releaseInputResponse();
  await inputResponse;

  await expect(page.getByLabel('Acceptance criteria', { exact: true })).toHaveValue('Typed while input response is held');
  expect((await detailSnapshot(request)).fields.find((field) => field.id === 'acceptance-criteria')?.value).toBe('Typed while assignment response is held');
  const followUpResponse = page.waitForResponse((response) => response.url().includes('/tickets/fixture-review/update') && response.request().method() === 'POST');
  await page.getByRole('button', { name: 'Save inputs', exact: true }).click();
  expect((await followUpResponse).status()).toBe(303);
  expect((await detailSnapshot(request)).fields.find((field) => field.id === 'acceptance-criteria')?.value).toBe('Typed while input response is held');
});

test('unrelated input reply leaves the held assignee selection alone', async ({ page, context, request }) => {
  let releaseInputResponse: () => void = () => undefined;
  const heldInputResponse = new Promise<void>((resolve) => {
    releaseInputResponse = resolve;
  });
  await context.route('**/tickets/fixture-review/update', async (route) => {
    const upstream = await route.fetch();
    await heldInputResponse;
    await route.fulfill({ response: upstream });
  }, { times: 1 });

  await page.goto('/newsletter/workflows/delivery?ticket=fixture-review');
  await page.getByLabel('Acceptance criteria', { exact: true }).fill('Submitted while assignee select is held');
  await page.locator('#ticket-assignee').focus();
  await page.locator('#ticket-assignee').evaluate((node) => {
    (node as HTMLSelectElement).value = 'builder';
  });
  const inputResponse = page.waitForResponse((response) => response.url().includes('/tickets/fixture-review/update') && response.request().method() === 'POST');
  const savePromise = page.evaluate(() => (window as typeof window & {
    workflowBoardController: { saveInputs: () => Promise<void> };
  }).workflowBoardController.saveInputs());
  releaseInputResponse();
  await inputResponse;
  await savePromise;

  await expect(page.locator('#ticket-assignee')).toHaveValue('builder');
  expect((await detailSnapshot(request)).fields.find((field) => field.id === 'acceptance-criteria')?.value).toBe('Submitted while assignee select is held');
});

test('dirty inputs save after assignee save when the server field is unchanged', async ({ page }) => {
  await page.goto('/newsletter/workflows/delivery?ticket=fixture-review');

  const assigneeSaved = page.waitForResponse((response) => response.url().includes('/tickets/fixture-review/assignee') && response.request().method() === 'POST');
  await page.getByLabel('Acceptance criteria', { exact: true }).fill('Keep this unsaved note');
  await page.getByLabel('Assigned agent', { exact: true }).selectOption('builder');
  await assigneeSaved;

  const inputsSaved = page.waitForResponse((response) => response.url().includes('/tickets/fixture-review/update') && response.request().method() === 'POST');
  await page.getByRole('button', { name: 'Save inputs', exact: true }).click();
  await inputsSaved;

  const saved = await page.request.get('/newsletter/workflows/delivery/tickets/fixture-review/snapshot');
  const payload = (await saved.json()) as DetailSnapshot;
  expect(payload.ticket.assignee).toBe('builder');
  expect(payload.fields.find((field) => field.id === 'acceptance-criteria')?.value).toBe('Keep this unsaved note');

  await assertNoConsoleErrors(page);
});

test('concurrent dirty input keeps the remote value and the unsaved local draft', async ({ page, request }) => {
  await page.goto('/newsletter/workflows/delivery?ticket=fixture-review');

  const initial = (await (await request.get('/newsletter/workflows/delivery/tickets/fixture-review/snapshot')).json()) as DetailSnapshot;
  await page.getByLabel('Acceptance criteria', { exact: true }).fill('My unsaved local edit');

  const assigneeSaved = page.waitForResponse((response) => response.url().includes('/tickets/fixture-review/assignee') && response.request().method() === 'POST');
  await page.getByLabel('Assigned agent', { exact: true }).selectOption('builder');
  await assigneeSaved;

  const remoteVersionResponse = await request.get('/newsletter/workflows/delivery/tickets/fixture-review/snapshot');
  const remoteVersion = (await remoteVersionResponse.json()) as DetailSnapshot;
  const remoteUpdate = await request.post('/newsletter/workflows/delivery/tickets/fixture-review/update', {
    headers: { Accept: 'application/json' },
    form: {
      payload: JSON.stringify({
        version: remoteVersion.ticket.version,
        operation_id: operationId('remote-edit'),
        patch: {
          field_values: {
            'acceptance-criteria': 'Remote server edit',
          },
        },
      }),
    },
  });
  expect(remoteUpdate.ok()).toBeTruthy();

  const conflictResponse = page.waitForResponse((response) => response.url().includes('/tickets/fixture-review/update') && response.request().method() === 'POST');
  await page.getByRole('button', { name: 'Save inputs', exact: true }).click();
  const conflict = await conflictResponse;
  expect(conflict.status()).toBe(409);

  const persisted = (await (await request.get('/newsletter/workflows/delivery/tickets/fixture-review/snapshot')).json()) as DetailSnapshot;
  expect(persisted.fields.find((field) => field.id === 'acceptance-criteria')?.value).toBe('Remote server edit');
  await expect(page.getByLabel('Acceptance criteria', { exact: true })).toHaveValue('My unsaved local edit');
  expect(initial.ticket.ref.ticket_id).toBe('fixture-review');
});

test('remote input change before the assignee response reaches the browser keeps the stale draft local', async ({ page, request, context }) => {
  let releaseAssigneeResponse: (() => void) | null = null;
  const assigneeResponseReleased = new Promise<void>((resolve) => {
    releaseAssigneeResponse = resolve;
  });

  await context.route('**/tickets/fixture-review/assignee', async (route) => {
    const upstream = await route.fetch();
    const detail = await upstream.json() as DetailSnapshot;
    const remoteUpdate = await request.post('/newsletter/workflows/delivery/tickets/fixture-review/update', {
      headers: { Accept: 'application/json' },
      form: {
        payload: JSON.stringify({
          version: detail.ticket.version,
          operation_id: operationId('remote-before-assignee-release'),
          patch: {
            field_values: {
              'acceptance-criteria': 'Remote server edit before assignee response',
            },
          },
        }),
      },
    });
    expect(remoteUpdate.ok()).toBeTruthy();
    await assigneeResponseReleased;
    await route.fulfill({ response: upstream });
  });

  await page.goto('/newsletter/workflows/delivery/tickets/fixture-review');
  await waitForWorkflowController(page);
  await page.getByLabel('Acceptance criteria', { exact: true }).fill('My unsaved local edit');

  const assigneeSaved = page.waitForResponse((response) => response.url().includes('/tickets/fixture-review/assignee') && response.request().method() === 'POST');
  await page.getByLabel('Assigned agent', { exact: true }).selectOption('builder');
  releaseAssigneeResponse?.();
  await assigneeSaved;

  const conflictResponse = page.waitForResponse((response) => response.url().includes('/tickets/fixture-review/update') && response.request().method() === 'POST');
  await page.getByRole('button', { name: 'Save inputs', exact: true }).click();
  const conflict = await conflictResponse;
  expect(conflict.status()).toBe(409);

  const persisted = await detailSnapshot(request);
  expect(persisted.ticket.assignee).toBe('builder');
  expect(persisted.fields.find((field) => field.id === 'acceptance-criteria')?.value).toBe('Remote server edit before assignee response');
  await expect(page.getByLabel('Acceptance criteria', { exact: true })).toHaveValue('My unsaved local edit');
  await expect(page.locator('#workflow-action-errors')).toContainText('Refresh the ticket');
});

test('run queues durable work without changing the ticket state', async ({ page, request }) => {
  await page.goto('/newsletter/workflows/delivery?ticket=fixture-review');

  const runResponse = page.waitForResponse(
    (response) =>
      response.url().includes('/tickets/fixture-review/run') &&
      response.request().method() === 'POST',
  );
  await page.getByRole('button', { name: 'Run', exact: true }).click();
  await runResponse;

  const detail = (await (
    await request.get('/newsletter/workflows/delivery/tickets/fixture-review/snapshot')
  ).json()) as DetailSnapshot & {
    ticket: DetailSnapshot['ticket'] & {
      pending_run_job_id: string | null;
      active_run_job_id: string | null;
      state_id: string;
    };
  };

  expect(detail.ticket.pending_run_job_id).toBeTruthy();
  expect(detail.ticket.active_run_job_id).toBeNull();
  expect(detail.ticket.state_id).toBe('review');
  await expect(page.getByText(/Queued /)).toBeVisible();
});

test('assignee filter applies without a manual form submit', async ({ page }) => {
  await page.goto('/newsletter/workflows/delivery');

  await page.getByLabel('All assignees', { exact: true }).selectOption('unassigned');

  await expect(page).toHaveURL(/assignee=unassigned/);
  await expect(page.getByText('Review the local storage contract', { exact: true })).toBeVisible();
  await expect(page.getByText('Validate stale transition handling', { exact: true })).toHaveCount(0);
});

test('new ticket dialog creates a backlog ticket from the live board', async ({ page, request }) => {
  await page.goto('/newsletter/workflows/delivery');

  await page.getByRole('button', { name: 'New ticket', exact: true }).click();
  const dialog = page.locator('#workflow-ticket-dialog');
  await expect(dialog).toBeVisible();
  await dialog.getByLabel('Title', { exact: true }).fill('Capture typed workflow values');
  await dialog.getByLabel('Description', { exact: true }).fill('Keep boolean false and numeric zero visible without coercion.');
  await dialog.getByRole('button', { name: 'Create ticket', exact: true }).click();

  await expect(page.getByRole('heading', { name: 'Capture typed workflow values', exact: true })).toBeVisible();
  await expect(page.getByLabel('Assigned agent', { exact: true })).toHaveValue('');
  await expect(page.getByRole('button', { name: 'Run', exact: true })).toBeDisabled();

  const createdUrl = new URL(page.url());
  const ticketId = createdUrl.searchParams.get('ticket') ?? createdUrl.pathname.split('/').at(-1) ?? '';
  expect(ticketId).toBeTruthy();

  const detail = await request.get(`/newsletter/workflows/delivery/tickets/${ticketId}/snapshot`);
  expect(detail.ok()).toBeTruthy();
  const payload = await detail.json() as DetailSnapshot & {
    ticket: DetailSnapshot['ticket'] & {
      state_id: string;
      title: string;
      description: string;
      pending_run_job_id: string | null;
    };
  };
  expect(payload.ticket.title).toBe('Capture typed workflow values');
  expect(payload.ticket.description).toBe('Keep boolean false and numeric zero visible without coercion.');
  expect(payload.ticket.state_id).toBe('backlog');
  expect(payload.ticket.pending_run_job_id).toBeNull();
});

test('save failures stay visible without dropping the dirty draft', async ({ context, page }) => {
  await context.route('**/tickets/fixture-review/update', async (route) => {
    await route.fulfill({
      status: 503,
      contentType: 'application/json',
      body: JSON.stringify({
        code: 'storage-unavailable',
        issues: [
          {
            code: 'storage-unavailable',
            field: 'payload',
            message: 'Ticket service unavailable.',
            hint: 'Retry after restoring the ticket storage provider.',
          },
        ],
      }),
    });
  });

  await page.goto('/newsletter/workflows/delivery?ticket=fixture-review');

  await page.getByLabel('Acceptance criteria', { exact: true }).fill('Keep this visible after the failed save');
  await page.getByRole('button', { name: 'Save inputs', exact: true }).click();

  await expect(page.locator('#workflow-action-errors')).toContainText('Ticket service unavailable.');
  await expect(page.getByLabel('Acceptance criteria', { exact: true })).toHaveValue('Keep this visible after the failed save');
});

test('overview opens in read mode with one run status and rendered description', async ({ page }) => {
  await page.goto('/newsletter/workflows/delivery?ticket=fixture-review');

  await expect(page.locator('[data-ticket-run-status]')).toHaveText('No active run');
  await expect(page.locator('[data-ticket-description-read]')).toBeVisible();
  await expect(page.locator('[data-ticket-description-read] p')).toContainText('Reject transitions when the ticket revision');
  await expect(page.locator('[data-ticket-edit]')).toBeHidden();
  await expect(page.getByRole('button', { name: 'Edit ticket', exact: true })).toBeVisible();

  await assertNoConsoleErrors(page);
});

test('description edits save through the overview controls', async ({ page, request }) => {
  await page.goto('/newsletter/workflows/delivery?ticket=fixture-review');

  await page.getByRole('button', { name: 'Edit ticket', exact: true }).click();
  const saved = page.waitForResponse(
    (response) =>
      response.url().includes('/tickets/fixture-review/update') &&
      response.request().method() === 'POST',
  );
  await page.locator('#ticket-description').fill('Updated description from the browser');
  await page.getByRole('button', { name: 'Save', exact: true }).click();
  await saved;

  const detail = (await (
    await request.get('/newsletter/workflows/delivery/tickets/fixture-review/snapshot')
  ).json()) as DetailSnapshot & { ticket: DetailSnapshot['ticket'] & { description: string } };
  expect(detail.ticket.description).toBe('Updated description from the browser');
  await expect(page.locator('[data-ticket-edit]')).toBeHidden();
  await expect(page.locator('[data-ticket-description-read]')).toContainText('Updated description from the browser');
});

test('edit save updates read description without page html', async ({ page, request }) => {
  await page.goto('/newsletter/workflows/delivery?ticket=fixture-review');
  const htmlRequests: string[] = [];
  page.on('request', (requestEvent) => {
    const accepted = requestEvent.headers().accept || '';
    if (requestEvent.method() === 'GET' && accepted.includes('text/html')) {
      htmlRequests.push(requestEvent.url());
    }
  });

  await page.getByRole('button', { name: 'Edit ticket', exact: true }).click();
  const saved = page.waitForResponse(
    (response) =>
      response.url().includes('/tickets/fixture-review/update') &&
      response.request().method() === 'POST',
  );
  await page.locator('#ticket-description').fill('JSON-only description save');
  await page.getByRole('button', { name: 'Save', exact: true }).click();
  expect((await saved).status()).toBe(303);

  expect(htmlRequests).toEqual([]);
  const detail = await detailSnapshot(request);
  expect(detail.ticket.description).toBe('JSON-only description save');
  await expect(page.locator('[data-ticket-edit]')).toBeHidden();
  await expect(page.locator('[data-ticket-description-read]')).toContainText('JSON-only description save');
});

test('cancelling the edit form restores confirmed title and description', async ({ page }) => {
  await page.goto('/newsletter/workflows/delivery?ticket=fixture-review');

  await page.getByRole('button', { name: 'Edit ticket', exact: true }).click();
  await page.locator('#ticket-title').fill('Temporary title that must not persist');
  await page.locator('#ticket-description').fill('Temporary description that must not persist');
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();

  await expect(page.locator('[data-ticket-edit]')).toBeHidden();
  await expect(page.getByRole('heading', { name: 'Validate stale transition handling' })).toBeVisible();

  await page.getByRole('button', { name: 'Edit ticket', exact: true }).click();
  await expect(page.locator('#ticket-title')).toHaveValue('Validate stale transition handling');
  await expect(page.locator('#ticket-description')).toHaveValue(/Reject transitions when the ticket revision/);
});

test('board selection keeps ticket drafts across card navigation and browser history', async ({ page }) => {
  await page.goto('/newsletter/workflows/delivery?ticket=fixture-review');

  const navigationRequests: string[] = [];
  page.on('request', (request) => {
    if (request.isNavigationRequest() && request.url().includes('/newsletter/workflows/delivery')) {
      navigationRequests.push(request.url());
    }
  });

  await page.getByLabel('Acceptance criteria', { exact: true }).fill('Unsaved local workflow note');
  await page.getByRole('link', { name: 'Review the local storage contract' }).click();

  await expect(page).toHaveURL(/ticket=fixture-review-2/);
  await expect(page.getByRole('heading', { name: 'Review the local storage contract' })).toBeVisible();
  expect(navigationRequests).toEqual([]);

  await page.goBack();

  await expect(page).toHaveURL(/ticket=fixture-review/);
  await expect(page.locator('#field-acceptance-criteria')).toHaveValue('Unsaved local workflow note');
});

test('keyboard close clears the selection and reopening restores the ticket draft', async ({ page }) => {
  await page.goto('/newsletter/workflows/delivery?ticket=fixture-review&query=Validate&assignee=reviewer');

  await page.getByLabel('Acceptance criteria', { exact: true }).fill('Unsaved close-and-reopen note');
  const closeLink = await tabTo(page, { role: 'link', name: 'Close' });
  await expect(closeLink).toBeFocused();
  await closeLink.press('Enter');

  await expect(page).toHaveURL(/\/newsletter\/workflows\/delivery\?query=Validate&assignee=reviewer$/);
  await expect(page.getByLabel('Ticket details')).toHaveCount(0);

  await page.getByRole('link', { name: /Validate stale transition handling/ }).click();

  await expect(page).toHaveURL(/ticket=fixture-review/);
  await expect(page.locator('#field-acceptance-criteria')).toHaveValue('Unsaved close-and-reopen note');
  await assertNoConsoleErrors(page);
});

test('visible polling refresh updates server content without wiping a dirty draft', async ({ page, request }) => {
  await page.goto('/newsletter/workflows/delivery?ticket=fixture-review');

  const localField = page.locator('#field-acceptance-criteria');
  await localField.fill('Locally edited and not yet saved');
  await expect(localField).toBeFocused();

  const detail = (await (
    await request.get('/newsletter/workflows/delivery/tickets/fixture-review/snapshot')
  ).json()) as DetailSnapshot & { ticket: DetailSnapshot['ticket'] & { description: string } };

  const remoteUpdate = await request.post('/newsletter/workflows/delivery/tickets/fixture-review/update', {
    headers: { Accept: 'application/json' },
    form: {
      payload: JSON.stringify({
        version: detail.ticket.version,
        operation_id: operationId('poll-refresh-description'),
        patch: {
          description: 'Remote description refreshed through polling',
        },
      }),
    },
  });
  expect(remoteUpdate.ok()).toBeTruthy();

  await expect(page.locator('#ticket-description')).toHaveValue('Remote description refreshed through polling');
  await expect(localField).toHaveValue('Locally edited and not yet saved');
  await expect(localField).toBeFocused();
});

test('refresh failure preserves controls and recovers without clearing an action error', async ({ page, request }) => {
  await page.goto('/newsletter/workflows/delivery?ticket=fixture-review');
  await stopPollTimer(page);
  const field = page.getByLabel('Acceptance criteria', { exact: true });
  const fieldNode = await page.locator('#field-acceptance-criteria').elementHandle();
  const inspectorNode = await page.getByLabel('Ticket details').elementHandle();
  expect(fieldNode).not.toBeNull();
  expect(inspectorNode).not.toBeNull();

  await field.fill('Draft survives refresh failure');
  const original = await detailSnapshot(request);
  const remoteUpdate = await request.post('/newsletter/workflows/delivery/tickets/fixture-review/update', {
    headers: { Accept: 'application/json' },
    form: { payload: JSON.stringify({
      version: original.ticket.version,
      operation_id: operationId('refresh-failure-conflict'),
      patch: { field_values: { 'acceptance-criteria': 'Remote value before stale action' } },
    }) },
  });
  expect(remoteUpdate.ok()).toBeTruthy();
  const conflictResponse = page.waitForResponse((response) => response.url().includes('/tickets/fixture-review/update') && response.request().method() === 'POST');
  await page.getByRole('button', { name: 'Save inputs', exact: true }).click();
  expect((await conflictResponse).status()).toBe(409);
  await expect(page.locator('#workflow-action-errors')).toContainText('Refresh the ticket');

  await page.route('**/newsletter/workflows/delivery/snapshot?*', async (route) => {
    await route.abort('failed');
  }, { times: 1 });
  await forcePoll(page);

  await expect(page.locator('#workflow-refresh-status')).toContainText('Unable to refresh');
  await expect(page.locator('#workflow-action-errors')).toContainText('Refresh the ticket');
  expect(await fieldNode!.evaluate((node) => node.isConnected && node === document.getElementById('field-acceptance-criteria'))).toBe(true);
  expect(await inspectorNode!.evaluate((node) => node.isConnected && node === document.querySelector('[aria-label="Ticket details"]'))).toBe(true);
  await expect(field).toHaveValue('Draft survives refresh failure');

  await forcePoll(page);

  await expect(page.locator('#workflow-refresh-status')).toBeHidden();
  await expect(page.locator('#workflow-action-errors')).toContainText('Refresh the ticket');
  await expect(field).toHaveValue('Draft survives refresh failure');

  await page.route('**/newsletter/workflows/delivery/snapshot?*', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      headers: { ETag: 'W/"task4-invalid-json"', 'Cache-Control': 'no-cache' },
      body: '{',
    });
  }, { times: 1 });
  await forcePoll(page);

  await expect(page.locator('#workflow-refresh-status')).toContainText('Unable to refresh');
  await expect(page.locator('#workflow-action-errors')).toContainText('Refresh the ticket');
  await expect(field).toHaveValue('Draft survives refresh failure');

  await forcePoll(page);

  await expect(page.locator('#workflow-refresh-status')).toBeHidden();
  await expect(page.locator('#workflow-action-errors')).toContainText('Refresh the ticket');
  await expect(field).toHaveValue('Draft survives refresh failure');
});

test('incompatible snapshot requires explicit refresh without partial application', async ({ page, request }) => {
  const response = await request.get('/newsletter/workflows/delivery/snapshot?ticket=fixture-review');
  expect(response.ok()).toBeTruthy();
  const board = await response.json();
  const nextBoard = JSON.parse(JSON.stringify(board));
  const editableField = nextBoard.selected_ticket.fields.find((field: { id: string; is_output: boolean; type: string }) => field.id === 'acceptance-criteria' && !field.is_output);
  expect(editableField).toBeTruthy();
  editableField.type = editableField.type === 'text' ? 'number' : 'text';
  nextBoard.name = 'Rejected incompatible board title';
  nextBoard.selected_ticket.ticket.title = 'Rejected incompatible ticket title';
  const selectedCard = nextBoard.columns.flatMap((column: { tickets: Array<{ ref: { ticket_id: string }; title: string }> }) => column.tickets).find((ticket: { ref: { ticket_id: string }; title: string }) => ticket.ref.ticket_id === 'fixture-review');
  expect(selectedCard).toBeTruthy();
  selectedCard.title = 'Rejected incompatible card title';

  const htmlRequests: string[] = [];
  page.on('request', (requestEvent) => {
    const accepted = requestEvent.headers().accept || '';
    if (requestEvent.method() === 'GET' && accepted.includes('text/html')) {
      htmlRequests.push(requestEvent.url());
    }
  });
  await page.route('**/newsletter/workflows/delivery/snapshot?*', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      headers: { ETag: 'W/"task4-incompatible"', 'Cache-Control': 'no-cache' },
      body: JSON.stringify(nextBoard),
    });
  }, { times: 1 });

  await page.goto('/newsletter/workflows/delivery?ticket=fixture-review');
  await stopPollTimer(page);
  htmlRequests.length = 0;
  const previousState = await page.evaluate((key) => {
    const controller = (window as typeof window & {
      workflowBoardController: { board: unknown; ticket: { ticket: { version: unknown } } };
      __task4BoardBefore?: unknown;
    }).workflowBoardController;
    (window as typeof window & { __task4BoardBefore?: unknown }).__task4BoardBefore = controller.board;
    return { version: controller.ticket.ticket.version, etag: window.FlowgencyLive.handles.get(key)!.etag };
  }, WORKFLOW_HANDLE_KEY);

  await forcePoll(page);
  await page.getByRole('button', { name: 'Run', exact: true }).focus();

  const currentState = await page.evaluate((key) => {
    const controller = (window as typeof window & {
      workflowBoardController: { board: unknown; ticket: { ticket: { version: unknown } } };
      __task4BoardBefore?: unknown;
    }).workflowBoardController;
    return {
      sameBoard: controller.board === (window as typeof window & { __task4BoardBefore?: unknown }).__task4BoardBefore,
      version: controller.ticket.ticket.version,
      etag: window.FlowgencyLive.handles.get(key)!.etag,
    };
  }, WORKFLOW_HANDLE_KEY);
  expect(currentState.sameBoard).toBe(true);
  expect(currentState.version).toEqual(previousState.version);
  expect(currentState.etag).toBeNull();
  await expect(page.locator('#workflow-refresh-status')).toContainText('Refresh required');
  await expect(page.getByRole('heading', { name: 'Rejected incompatible board title' })).toHaveCount(0);
  await expect(page.getByRole('heading', { name: 'Rejected incompatible ticket title' })).toHaveCount(0);
  await expect(page.getByRole('link', { name: /Rejected incompatible card title/ })).toHaveCount(0);
  expect(htmlRequests).toEqual([]);
});

test('missing selected ticket keeps its inspector and draft', async ({ page, request }) => {
  const response = await request.get('/newsletter/workflows/delivery/snapshot?ticket=fixture-review');
  expect(response.ok()).toBeTruthy();
  const board = await response.json();
  const unavailableBoard = JSON.parse(JSON.stringify(board));
  unavailableBoard.selected_ticket = { ...unavailableBoard.selected_ticket, ticket: null, fields: [] };
  unavailableBoard.name = 'Board structure may still refresh';

  await page.route('**/newsletter/workflows/delivery/snapshot?*', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      headers: { ETag: 'W/"task4-selected-unavailable"', 'Cache-Control': 'no-cache' },
      body: JSON.stringify(unavailableBoard),
    });
  }, { times: 1 });
  await page.goto('/newsletter/workflows/delivery?ticket=fixture-review');
  await stopPollTimer(page);
  await page.getByLabel('Acceptance criteria', { exact: true }).fill('Draft survives missing selection');
  const inspectorNode = await page.getByLabel('Ticket details').elementHandle();
  const fieldNode = await page.locator('#field-acceptance-criteria').elementHandle();
  expect(inspectorNode).not.toBeNull();
  expect(fieldNode).not.toBeNull();

  await forcePoll(page);

  await expect(page.locator('#workflow-refresh-status')).toContainText('Ticket unavailable');
  expect(await inspectorNode!.evaluate((node) => node.isConnected && node === document.querySelector('[aria-label="Ticket details"]'))).toBe(true);
  expect(await fieldNode!.evaluate((node) => node.isConnected && node === document.getElementById('field-acceptance-criteria'))).toBe(true);
  await expect(page.getByLabel('Acceptance criteria', { exact: true })).toHaveValue('Draft survives missing selection');

  await forcePoll(page);

  await expect(page.locator('#workflow-refresh-status')).toBeHidden();
  await expect(page.getByLabel('Acceptance criteria', { exact: true })).toHaveValue('Draft survives missing selection');
});

test('a ticket that vanishes and returns unchanged converges instead of sticking on a 304', async ({ page, request }) => {
  const response = await request.get('/newsletter/workflows/delivery/snapshot?ticket=fixture-review');
  expect(response.ok()).toBeTruthy();
  const board = await response.json();
  const unavailableBoard = JSON.parse(JSON.stringify(board));
  unavailableBoard.selected_ticket = { ...unavailableBoard.selected_ticket, ticket: null, fields: [] };
  unavailableBoard.name = 'Delivery while the ticket is missing';

  await page.goto(WORKFLOW_BOARD_URL);
  await stopPollTimer(page);
  await forcePoll(page);
  const acceptedEtag = await workflowHandleEtag(page);
  expect(acceptedEtag).not.toBeNull();
  await expect(page.getByRole('heading', { name: 'Delivery', exact: true })).toBeVisible();

  await page.route('**/newsletter/workflows/delivery/snapshot?*', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      headers: { ETag: 'W/"vanished-ticket"', 'Cache-Control': 'no-cache' },
      body: JSON.stringify(unavailableBoard),
    });
  }, { times: 1 });
  await forcePoll(page);
  await expect(page.locator('#workflow-refresh-status')).toContainText('Ticket unavailable');
  await expect(page.getByRole('heading', { name: 'Delivery while the ticket is missing' })).toBeVisible();
  expect(await workflowHandleEtag(page)).toBeNull();

  const conditional: (string | undefined)[] = [];
  await page.route('**/newsletter/workflows/delivery/snapshot?*', async (route) => {
    conditional.push(route.request().headers()['if-none-match']);
    await route.fallback();
  });
  await forcePoll(page);

  expect(conditional).toEqual([undefined]);
  await expect(page.getByRole('heading', { name: 'Delivery', exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Delivery while the ticket is missing' })).toHaveCount(0);
  await expect(page.locator('#workflow-refresh-status')).toBeHidden();
  expect(await workflowHandleEtag(page)).toBe(acceptedEtag);
});

test('healthy board can show and recover from polled workflow issues', async ({ page, request }) => {
  const response = await request.get('/newsletter/workflows/delivery/snapshot?ticket=fixture-review');
  expect(response.ok()).toBeTruthy();
  const board = await response.json();
  expect(board.issues).toEqual([]);
  const issueBoard = JSON.parse(JSON.stringify(board));
  issueBoard.issues = [{
    code: 'workflow-definition-unavailable',
    field: 'workflow',
    message: 'Definition <strong>markup</strong> unavailable',
    hint: '<script>nope</script>Use the configured workflow source.',
  }];
  issueBoard.presentation.issues_html = '<p><strong>workflow-definition-unavailable</strong>: Definition &lt;strong&gt;markup&lt;/strong&gt; unavailable <span>Use the configured workflow source.</span></p>';
  let servedIssue = false;
  await page.route('**/newsletter/workflows/delivery/snapshot?*', async (route) => {
    if (servedIssue) {
      await route.fallback();
      return;
    }
    servedIssue = true;
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      headers: { ETag: 'W/"issue-projection"', 'Cache-Control': 'no-cache' },
      body: JSON.stringify(issueBoard),
    });
  });

  await page.goto('/newsletter/workflows/delivery?ticket=fixture-review');
  await stopPollTimer(page);
  const htmlRequests: string[] = [];
  page.on('request', (requestEvent) => {
    const accepted = requestEvent.headers().accept || '';
    if (requestEvent.method() === 'GET' && accepted.includes('text/html')) {
      htmlRequests.push(requestEvent.url());
    }
  });
  const inspectorNode = await page.getByLabel('Ticket details').elementHandle();
  const fieldNode = await page.locator('#field-acceptance-criteria').elementHandle();
  expect(inspectorNode).not.toBeNull();
  expect(fieldNode).not.toBeNull();
  await page.getByLabel('Acceptance criteria', { exact: true }).fill('Draft survives issue banner');

  await forcePoll(page);

  const boardIssues = page.locator('[data-board-issues]');
  await expect(boardIssues).toBeVisible();
  await expect(boardIssues).toContainText('workflow-definition-unavailable');
  await expect(boardIssues).toContainText('Definition <strong>markup</strong> unavailable');
  await expect(boardIssues.locator('script')).toHaveCount(0);
  expect(await inspectorNode!.evaluate((node) => node.isConnected && node === document.querySelector('[aria-label="Ticket details"]'))).toBe(true);
  expect(await fieldNode!.evaluate((node) => node.isConnected && node === document.getElementById('field-acceptance-criteria'))).toBe(true);
  await expect(page.getByLabel('Acceptance criteria', { exact: true })).toHaveValue('Draft survives issue banner');

  await forcePoll(page);

  await expect(boardIssues).toBeHidden();
  await expect(page.getByLabel('Acceptance criteria', { exact: true })).toHaveValue('Draft survives issue banner');
  expect(htmlRequests).toEqual([]);
});

test('poll body parsed after an action cannot roll back its accepted reply', async ({ page, request }) => {
  await page.goto('/newsletter/workflows/delivery?ticket=fixture-review');
  await stopPollTimer(page);
  const response = await request.get('/newsletter/workflows/delivery/snapshot?ticket=fixture-review');
  expect(response.ok()).toBeTruthy();
  const staleBoard = await response.json();
  await installHeldPollBody(page);
  await page.route('**/newsletter/workflows/delivery/snapshot?*', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      headers: { ETag: 'W/"task4-late-action"', 'Cache-Control': 'no-cache' },
      body: JSON.stringify(staleBoard),
    });
  }, { times: 1 });

  await startPoll(page);
  await page.waitForFunction(() => Boolean((window as typeof window & { pollBodyHeld?: boolean }).pollBodyHeld));
  const assignmentResponse = page.waitForResponse((eventResponse) => eventResponse.url().includes('/tickets/fixture-review/assignee') && eventResponse.request().method() === 'POST');
  await page.getByLabel('Assigned agent', { exact: true }).selectOption('builder');
  expect((await assignmentResponse).status()).toBe(303);
  await expect(page.getByLabel('Assigned agent', { exact: true })).toHaveValue('builder');
  await page.evaluate(() => (window as typeof window & { releasePollBody?: () => void }).releasePollBody?.());

  await expect(page.getByLabel('Assigned agent', { exact: true })).toHaveValue('builder');
  expect((await detailSnapshot(request)).ticket.assignee).toBe('builder');
});

test('pending mutation completion resumes the next board snapshot application', async ({ page, request }) => {
  const response = await request.get('/newsletter/workflows/delivery/snapshot?ticket=fixture-review');
  expect(response.ok()).toBeTruthy();
  const board = await response.json();
  const heldBoard = JSON.parse(JSON.stringify(board));
  heldBoard.name = 'Delivery while mutation pending';
  const resumedBoard = JSON.parse(JSON.stringify(board));
  resumedBoard.name = 'Delivery after pending mutation';
  let snapshotCount = 0;
  await page.route('**/newsletter/workflows/delivery/snapshot?*', async (route) => {
    snapshotCount += 1;
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      headers: { ETag: `W/"post-mutation-${snapshotCount}"`, 'Cache-Control': 'no-cache' },
      body: JSON.stringify(snapshotCount === 1 ? heldBoard : resumedBoard),
    });
  }, { times: 2 });

  await page.goto('/newsletter/workflows/delivery?ticket=fixture-review');
  await stopPollTimer(page);
  await page.evaluate(() => {
    const appWindow = window as typeof window & { __releaseAssigneeMutation?: () => void; __assignmentPending?: boolean };
    const originalFetch = window.fetch.bind(window);
    const held = new Promise<void>((resolve) => {
      appWindow.__releaseAssigneeMutation = resolve;
    });
    window.fetch = async (input, init) => {
      if (String(input).includes('/tickets/fixture-review/assignee')) {
        appWindow.__assignmentPending = true;
        await held;
      }
      return originalFetch(input, init);
    };
  });
  const mutationResponse = page.waitForResponse((eventResponse) => eventResponse.url().includes('/tickets/fixture-review/assignee') && eventResponse.request().method() === 'POST');
  const heldSnapshotResponse = page.waitForResponse((eventResponse) => eventResponse.url().includes('/newsletter/workflows/delivery/snapshot') && eventResponse.request().method() === 'GET');
  await page.getByLabel('Assigned agent', { exact: true }).selectOption('builder');
  await page.waitForFunction(() => Boolean((window as typeof window & { __assignmentPending?: boolean }).__assignmentPending));
  await startPoll(page);
  expect((await heldSnapshotResponse).status()).toBe(200);
  await expect(page.getByRole('heading', { name: 'Delivery while mutation pending' })).toHaveCount(0);
  const resumedSnapshotResponse = page.waitForResponse((eventResponse) => eventResponse.url().includes('/newsletter/workflows/delivery/snapshot') && eventResponse.request().method() === 'GET');
  await page.evaluate(() => (window as typeof window & { __releaseAssigneeMutation?: () => void }).__releaseAssigneeMutation?.());

  expect((await mutationResponse).status()).toBe(303);
  expect((await resumedSnapshotResponse).status()).toBe(200);
  await expect(page.getByRole('heading', { name: 'Delivery after pending mutation' })).toBeVisible();
  await expect(page.getByLabel('Assigned agent', { exact: true })).toHaveValue('builder');
});

test('hidden document rejects a snapshot whose body parsing finishes late', async ({ page, request }) => {
  await page.addInitScript(() => {
    let hidden = false;
    Object.defineProperty(document, 'hidden', {
      configurable: true,
      get() {
        return hidden;
      },
      set(value) {
        hidden = Boolean(value);
      },
    });
    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      get() {
        return hidden ? 'hidden' : 'visible';
      },
    });
    (window as typeof window & { __setTestHidden?: (value: boolean) => void }).__setTestHidden = (value: boolean) => {
      hidden = value;
      document.dispatchEvent(new Event('visibilitychange'));
    };
  });
  const response = await request.get('/newsletter/workflows/delivery/snapshot?ticket=fixture-review');
  expect(response.ok()).toBeTruthy();
  const staleBoard = await response.json();
  staleBoard.name = 'Hidden late body must not apply';
  staleBoard.selected_ticket.ticket.description = 'Hidden late body stale description';
  await page.goto('/newsletter/workflows/delivery?ticket=fixture-review');
  await stopPollTimer(page);
  await installHeldPollBody(page);
  await page.route('**/newsletter/workflows/delivery/snapshot?*', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      headers: { ETag: 'W/"task4-late-hidden"', 'Cache-Control': 'no-cache' },
      body: JSON.stringify(staleBoard),
    });
  }, { times: 1 });

  await startPoll(page);
  await page.waitForFunction(() => Boolean((window as typeof window & { pollBodyHeld?: boolean }).pollBodyHeld));
  await page.evaluate(() => (window as typeof window & { __setTestHidden: (value: boolean) => void }).__setTestHidden(true));
  await page.evaluate(() => (window as typeof window & { releasePollBody?: () => void }).releasePollBody?.());

  await expect(page.getByRole('heading', { name: 'Hidden late body must not apply' })).toHaveCount(0);
  await expect(page.locator('#ticket-description')).not.toHaveValue('Hidden late body stale description');
  expect(await workflowHandleEtag(page)).not.toBe('W/"task4-late-hidden"');
});

test('polling keeps the assignee node and never fetches page html', async ({ page, request }) => {
  await page.goto('/newsletter/workflows/delivery?ticket=fixture-review');
  await stopPollTimer(page);
  const htmlRequests: string[] = [];
  page.on('request', (requestEvent) => {
    const accepted = requestEvent.headers().accept || '';
    if (requestEvent.method() === 'GET' && accepted.includes('text/html')) {
      htmlRequests.push(requestEvent.url());
    }
  });
  const original = await page.locator('#ticket-assignee').elementHandle();
  const unaffectedCard = await page.getByRole('link', { name: /Review the local storage contract/ }).elementHandle();
  expect(original).not.toBeNull();
  expect(unaffectedCard).not.toBeNull();
  await page.locator('#ticket-assignee').focus();
  const other = await detailSnapshot(request, 'fixture-backlog-1');
  const changed = await request.post('/newsletter/workflows/delivery/tickets/fixture-backlog-1/update', {
    headers: { Accept: 'application/json' },
    form: { payload: JSON.stringify({
      version: other.ticket.version,
      operation_id: operationId('poll-other-card'),
      patch: { title: 'Remote card label' },
    }) },
  });
  expect(changed.ok()).toBeTruthy();
  await forcePoll(page);
  expect(await original!.evaluate((node) => node.isConnected && node === document.getElementById('ticket-assignee'))).toBe(true);
  expect(await unaffectedCard!.evaluate((node) => node.isConnected)).toBe(true);
  await expect(page.locator('#ticket-assignee')).toBeFocused();
  await expect(page.getByRole('link', { name: /Remote card label/ })).toBeVisible();
  expect(htmlRequests).toEqual([]);
  const saved = page.waitForResponse((response) => response.url().includes('/tickets/fixture-review/assignee') && response.request().method() === 'POST');
  await page.locator('#ticket-assignee').selectOption('builder');
  expect((await saved).status()).toBe(303);
  expect((await detailSnapshot(request)).ticket.assignee).toBe('builder');
  await assertNoConsoleErrors(page);
});

test('expanded polling updates header totals without replacing held ticket chrome', async ({ page, request }) => {
  await page.goto('/newsletter/workflows/delivery/tickets/fixture-review');
  await stopPollTimer(page);
  const htmlRequests: string[] = [];
  page.on('request', (requestEvent) => {
    const accepted = requestEvent.headers().accept || '';
    if (requestEvent.method() === 'GET' && accepted.includes('text/html')) {
      htmlRequests.push(requestEvent.url());
    }
  });
  const pageHandle = await page.locator('.workflow-board-page').elementHandle();
  const inspector = await page.getByLabel('Ticket details').elementHandle();
  const dialog = await page.locator('#workflow-ticket-dialog').elementHandle();
  const assigneeInput = await page.locator('#ticket-assignee').elementHandle();
  expect(pageHandle).not.toBeNull();
  expect(inspector).not.toBeNull();
  expect(dialog).not.toBeNull();
  expect(assigneeInput).not.toBeNull();
  await page.locator('#ticket-assignee').focus();

  const created = await request.post('/newsletter/workflows/delivery/tickets', {
    headers: { Accept: 'application/json' },
    form: { payload: JSON.stringify({
      operation_id: operationId('expanded-header-total'),
      title: 'Created while expanded stays stable',
      description: 'Header totals should update from the board snapshot.',
      field_values: {},
    }) },
  });
  expect(created.ok()).toBeTruthy();
  await forcePoll(page);

  expect(await pageHandle!.evaluate((node) => node.isConnected && node === document.querySelector('.workflow-board-page'))).toBe(true);
  expect(await inspector!.evaluate((node) => node.isConnected && node === document.querySelector('[aria-label="Ticket details"]'))).toBe(true);
  expect(await dialog!.evaluate((node) => node.isConnected && node === document.getElementById('workflow-ticket-dialog'))).toBe(true);
  expect(await assigneeInput!.evaluate((node) => node.isConnected && node === document.getElementById('ticket-assignee'))).toBe(true);
  await expect(page.locator('#ticket-assignee')).toBeFocused();
  await expect(page.getByText('9 tickets', { exact: true })).toBeVisible();
  expect(htmlRequests).toEqual([]);
  await assertNoConsoleErrors(page);
});

test('polling reconciles moved and removed cards with stable keyed nodes', async ({ page, request }) => {
  const response = await request.get('/newsletter/workflows/delivery/snapshot?ticket=fixture-review');
  expect(response.ok()).toBeTruthy();
  const board = await response.json();
  const nextBoard = JSON.parse(JSON.stringify(board));
  const backlog = nextBoard.columns[0];
  const active = nextBoard.columns[1];
  const movedIndex = backlog.tickets.findIndex((ticket) => ticket.ref.ticket_id === 'fixture-backlog-1');
  const removedIndex = backlog.tickets.findIndex((ticket) => ticket.ref.ticket_id === 'fixture-backlog-2');
  expect(movedIndex).toBeGreaterThanOrEqual(0);
  expect(removedIndex).toBeGreaterThanOrEqual(0);
  const moved = backlog.tickets.splice(movedIndex, 1)[0];
  const adjustedRemovedIndex = backlog.tickets.findIndex((ticket) => ticket.ref.ticket_id === 'fixture-backlog-2');
  const removed = backlog.tickets.splice(adjustedRemovedIndex, 1)[0];
  expect(removed.ref.ticket_id).toBe('fixture-backlog-2');
  active.tickets.unshift(moved);
  for (const column of nextBoard.columns) {
    column.count = column.tickets.length;
  }
  nextBoard.ticket_count = nextBoard.columns.reduce((total, column) => total + column.tickets.length, 0);

  let served = false;
  await page.route('**/newsletter/workflows/delivery/snapshot?*', async (route) => {
    if (served) {
      await route.fallback();
      return;
    }
    served = true;
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      headers: { ETag: 'W/"structural-test"', 'Cache-Control': 'no-cache' },
      body: JSON.stringify(nextBoard),
    });
  });

  await page.goto('/newsletter/workflows/delivery?ticket=fixture-review');
  await stopPollTimer(page);
  const movedCard = await page.locator('[data-ticket-id="fixture-backlog-1"]').elementHandle();
  const removedCard = await page.locator('[data-ticket-id="fixture-backlog-2"]').elementHandle();
  const unaffectedCard = await page.locator('[data-ticket-id="fixture-review"]').elementHandle();
  expect(movedCard).not.toBeNull();
  expect(removedCard).not.toBeNull();
  expect(unaffectedCard).not.toBeNull();

  await forcePoll(page);

  expect(await movedCard!.evaluate((node) => node.isConnected)).toBe(true);
  expect(await removedCard!.evaluate((node) => node.isConnected)).toBe(false);
  expect(await unaffectedCard!.evaluate((node) => node.isConnected && node === document.querySelector('[data-ticket-id="fixture-review"]'))).toBe(true);
  const backlogIds = await page.locator(`[data-column-key="${backlog.key}"] [data-ticket-id]`).evaluateAll((nodes) => nodes.map((node) => node.getAttribute('data-ticket-id')));
  const activeIds = await page.locator(`[data-column-key="${active.key}"] [data-ticket-id]`).evaluateAll((nodes) => nodes.map((node) => node.getAttribute('data-ticket-id')));
  expect(backlogIds).toEqual([]);
  expect(activeIds).toEqual(['fixture-backlog-1', 'fixture-active-1', 'fixture-active-2']);
  await expect(page.getByText('7 tickets', { exact: true })).toBeVisible();
  await assertNoConsoleErrors(page);
});

test('polling rejects invalid scoped refs before adopting model or DOM changes', async ({ page, request }) => {
  const response = await request.get('/newsletter/workflows/delivery/snapshot?ticket=fixture-review');
  expect(response.ok()).toBeTruthy();
  const board = await response.json();
  const cases = [
    ['missing card ref', (nextBoard: typeof board) => {
      delete nextBoard.columns[0].tickets[0].ref;
    }, 'invalid-card-ref'],
    ['missing card scope field', (nextBoard: typeof board) => {
      delete nextBoard.columns[0].tickets[0].ref.team_id;
    }, 'invalid-card-ref'],
    ['wrong-type card scope field', (nextBoard: typeof board) => {
      nextBoard.columns[0].tickets[0].ref.workflow_id = 42;
    }, 'invalid-card-ref'],
    ['mismatched card binding', (nextBoard: typeof board) => {
      nextBoard.columns[0].tickets[0].ref.binding_id = 'other-binding';
    }, 'invalid-card-ref'],
    ['mismatched card team', (nextBoard: typeof board) => {
      nextBoard.columns[0].tickets[0].ref.team_id = 'other-team';
    }, 'invalid-card-ref'],
    ['mismatched card workflow', (nextBoard: typeof board) => {
      nextBoard.columns[0].tickets[0].ref.workflow_id = 'other-workflow';
    }, 'invalid-card-ref'],
    ['missing selected ref', (nextBoard: typeof board) => {
      delete nextBoard.selected_ticket.ticket.ref;
    }, 'invalid-selected-ref'],
    ['mismatched selected binding', (nextBoard: typeof board) => {
      nextBoard.selected_ticket.ticket.ref.binding_id = 'other-binding';
    }, 'invalid-selected-ref'],
  ] as const;

  for (const [label, alter, expectedReason] of cases) {
    const nextBoard = JSON.parse(JSON.stringify(board));
    nextBoard.name = `Rejected ${label} board`;
    nextBoard.columns[0].tickets[0].title = `Rejected ${label}`;
    alter(nextBoard);
    let served = false;
    await page.route('**/newsletter/workflows/delivery/snapshot?*', async (route) => {
      if (served) {
        await route.fallback();
        return;
      }
      served = true;
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        headers: { ETag: `W/"invalid-ref-${label.replaceAll(' ', '-')}"`, 'Cache-Control': 'no-cache' },
        body: JSON.stringify(nextBoard),
      });
    });

    await page.goto('/newsletter/workflows/delivery?ticket=fixture-review');
    await stopPollTimer(page);
    await page.locator('#field-acceptance-criteria').fill(`Unsaved draft for ${label}`);
    const card = await page.locator('[data-ticket-id="fixture-review"]').elementHandle();
    const inspector = await page.getByLabel('Ticket details').elementHandle();
    const input = await page.locator('#field-acceptance-criteria').elementHandle();
    const controllerBefore = await page.evaluate((key) => {
      const controller = (window as typeof window & {
        workflowBoardController: { board: unknown; ticket: { ticket: { version: unknown } } };
        __task2BoardBefore?: unknown;
      }).workflowBoardController;
      (window as typeof window & { __task2BoardBefore?: unknown }).__task2BoardBefore = controller.board;
      return {
        version: controller.ticket.ticket.version,
        etag: window.FlowgencyLive.handles.get(key)!.etag,
      };
    }, WORKFLOW_HANDLE_KEY);
    expect(card).not.toBeNull();
    expect(inspector).not.toBeNull();
    expect(input).not.toBeNull();
    const inspection = await page.evaluate((candidate) => {
      const controller = (window as typeof window & {
        workflowBoardController: { currentTicketId: () => string | null; view: { inspectBoard: (board: unknown, selectedTicketId: string | null) => { ok: boolean; reason: string } } };
      }).workflowBoardController;
      return {
        base: controller.view.inspectBoard(candidate.validBoard, controller.currentTicketId()),
        invalid: controller.view.inspectBoard(candidate.invalidBoard, controller.currentTicketId()),
      };
    }, { validBoard: board, invalidBoard: nextBoard });
    expect(inspection.base).toMatchObject({ ok: true });
    expect(inspection.invalid).toMatchObject({ ok: false, reason: expectedReason });

    await forcePoll(page);

    const controllerAfter = await page.evaluate((key) => {
      const controller = (window as typeof window & {
        workflowBoardController: { board: unknown; ticket: { ticket: { version: unknown } } };
        __task2BoardBefore?: unknown;
      }).workflowBoardController;
      return {
        sameBoard: controller.board === (window as typeof window & { __task2BoardBefore?: unknown }).__task2BoardBefore,
        version: controller.ticket.ticket.version,
        etag: window.FlowgencyLive.handles.get(key)!.etag,
      };
    }, WORKFLOW_HANDLE_KEY);
    expect(controllerAfter.sameBoard).toBe(true);
    expect(controllerAfter.version).toEqual(controllerBefore.version);
    expect(controllerAfter.etag).not.toBe(`W/"invalid-ref-${label.replaceAll(' ', '-')}"`);
    expect(await card!.evaluate((node) => node.isConnected && node === document.querySelector('[data-ticket-id="fixture-review"]'))).toBe(true);
    expect(await inspector!.evaluate((node) => node.isConnected && node === document.querySelector('[aria-label="Ticket details"]'))).toBe(true);
    expect(await input!.evaluate((node) => node.isConnected && node === document.getElementById('field-acceptance-criteria'))).toBe(true);
    await expect(page.locator('#field-acceptance-criteria')).toHaveValue(`Unsaved draft for ${label}`);
    await expect(page.getByRole('link', { name: new RegExp(`Rejected ${label}`) })).toHaveCount(0);
    await expect(page.getByRole('heading', { name: `Rejected ${label} board` })).toHaveCount(0);
    await page.unroute('**/newsletter/workflows/delivery/snapshot?*');
  }

  await assertNoConsoleErrors(page);
});

test('hidden pages pause polling and abort an in-flight refresh without extra snapshot requests', async ({ page, request }) => {
  await page.addInitScript(() => {
    let hidden = false;
    Object.defineProperty(document, 'hidden', {
      configurable: true,
      get() {
        return hidden;
      },
      set(value) {
        hidden = Boolean(value);
      },
    });
    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      get() {
        return hidden ? 'hidden' : 'visible';
      },
    });
    (window as typeof window & { __setTestHidden?: (value: boolean) => void }).__setTestHidden = (value: boolean) => {
      hidden = value;
      document.dispatchEvent(new Event('visibilitychange'));
    };
  });
  await page.goto('/newsletter/workflows/delivery/tickets/fixture-review');
  await stopPollTimer(page);

  const paused = await page.evaluate(async (key) => {
    const win = window as typeof window & { __setTestHidden: (value: boolean) => void };
    const handle = window.FlowgencyLive.handles.get(key)!;
    let releaseSnapshot: (() => void) | null = null;
    const held = new Promise<void>((resolve) => {
      releaseSnapshot = resolve;
    });
    const originalFetch = window.fetch.bind(window);
    let snapshotRequests = 0;
    let firstSignal: AbortSignal | null | undefined;
    window.fetch = async (input, init) => {
      const url = String(input);
      if (url.includes('/newsletter/workflows/delivery/snapshot')) {
        snapshotRequests += 1;
        if (snapshotRequests === 1) {
          firstSignal = init?.signal;
          await held;
        }
      }
      return originalFetch(input, init);
    };
    void handle.refresh();
    await Promise.resolve();
    win.__setTestHidden(true);
    const aborted = firstSignal?.aborted ?? false;
    const hiddenRead = await handle.refresh();
    releaseSnapshot?.();
    window.fetch = originalFetch;
    return { aborted, hiddenRead, snapshotRequests };
  }, WORKFLOW_HANDLE_KEY);

  expect(paused.snapshotRequests).toBe(1);
  expect(paused.aborted).toBe(true);
  expect(paused.hiddenRead).toBe('hidden');

  const remoteUpdate = await request.post('/newsletter/workflows/delivery/tickets/fixture-review/update', {
    headers: { Accept: 'application/json' },
    form: {
      payload: JSON.stringify({
        version: (await detailSnapshot(request)).ticket.version,
        operation_id: operationId('resume-hidden-poll'),
        patch: { description: 'Visibility refresh after hidden pause' },
      }),
    },
  });
  expect(remoteUpdate.ok()).toBeTruthy();
  await page.evaluate(() => (window as typeof window & { __setTestHidden: (value: boolean) => void }).__setTestHidden(false));
  await expect(page.locator('#ticket-description')).toHaveValue('Visibility refresh after hidden pause');
});

test('older selection and refresh responses cannot replace a newer ticket selection or draft', async ({ page, context }) => {
  const delayedHtml: Array<() => void> = [];
  const delayedSnapshot: Array<() => void> = [];
  await context.route('**/newsletter/workflows/delivery?ticket=fixture-review-2', async (route) => {
    const upstream = await route.fetch();
    await new Promise<void>((resolve) => delayedHtml.push(resolve));
    await route.fulfill({ response: upstream });
  });
  await context.route('**/newsletter/workflows/delivery/snapshot?*ticket=fixture-review*', async (route) => {
    const upstream = await route.fetch();
    await new Promise<void>((resolve) => delayedSnapshot.push(resolve));
    await route.fulfill({ response: upstream });
  });

  await page.goto('/newsletter/workflows/delivery?ticket=fixture-review');
  await stopPollTimer(page);
  await startPoll(page);
  const delayedSelectionRequested = page.waitForRequest((request) => request.url().includes('ticket=fixture-review-2'));
  await page.getByRole('link', { name: 'Review the local storage contract' }).click();
  await delayedSelectionRequested;
  await page.evaluate(() => void (window as typeof window & { workflowBoardController: { openTicket: (ticketId: string) => Promise<void> } }).workflowBoardController.openTicket('fixture-active-1'));
  await expect(page).toHaveURL(/ticket=fixture-active-1/);
  await page.getByLabel('Acceptance criteria', { exact: true }).fill('Draft on the newer selection');

  delayedSnapshot.splice(0).forEach((release) => release());
  delayedHtml.splice(0).forEach((release) => release());

  await expect(page).toHaveURL(/ticket=fixture-active-1/);
  await expect(page.getByRole('heading', { name: 'Implement atomic ticket assignment' })).toBeVisible();
  await expect(page.getByLabel('Acceptance criteria', { exact: true })).toHaveValue('Draft on the newer selection');
});

test.describe('javascript-disabled workflow forms', () => {
  test.use({ javaScriptEnabled: false });

  test('create, edit, assign, save inputs, and run submit through HTML forms', async ({ page, request }) => {
    await page.goto('/newsletter/workflows/delivery');

    const createForm = page.locator('[data-noscript-create]');
    await createForm.getByLabel('Title', { exact: true }).fill('Create ticket without JavaScript');
    await createForm.getByLabel('Description', { exact: true }).fill('Use the same strict routes with ordinary HTML form fields.');
    await createForm.getByRole('button', { name: 'Create ticket', exact: true }).click();

    await expect(page.getByRole('heading', { name: 'Create ticket without JavaScript', exact: true })).toBeVisible();
    await page.locator('#ticket-assignee').selectOption('builder');
    await page.getByRole('button', { name: 'Assign', exact: true }).click();
    await expect(page.locator('#ticket-assignee')).toHaveValue('builder');

    await page.getByRole('link', { name: 'Edit ticket', exact: true }).click();
    const editForm = page.locator('[data-noscript-edit-form]');
    await editForm.getByLabel('Title', { exact: true }).fill('Edited without JavaScript');
    await editForm.getByLabel('Description', { exact: true }).fill('The HTML fallback keeps the approved route semantics.');
    await editForm.locator('[data-noscript-edit-save]').click();
    await expect(page.getByRole('heading', { name: 'Edited without JavaScript', exact: true })).toBeVisible();

    const inputsForm = page.locator('[data-noscript-inputs-form]');
    await inputsForm.getByLabel('Acceptance criteria', { exact: true }).fill('Preserve strict version checking and HTML drafts.');
    await inputsForm.locator('button[type="submit"]').click();
    await expect(page.getByLabel('Acceptance criteria', { exact: true })).toHaveValue('Preserve strict version checking and HTML drafts.');

    await page.getByRole('button', { name: 'Run', exact: true }).click();
    await expect(page.locator('[data-ticket-run-status]')).toContainText('Queued');

    const createdUrl = new URL(page.url());
    const ticketId = createdUrl.pathname.split('/').at(-1) ?? '';
    const detail = await detailSnapshot(request, ticketId);
    expect(detail.ticket.assignee).toBe('builder');
  });

  test('stale HTML edit errors keep the local draft visible', async ({ page, request }) => {
    await page.goto('/newsletter/workflows/delivery/tickets/fixture-review?edit=1');

    const stale = await detailSnapshot(request);
    const remoteUpdate = await request.post('/newsletter/workflows/delivery/tickets/fixture-review/update', {
      headers: { Accept: 'application/json' },
      form: {
        payload: JSON.stringify({
          version: stale.ticket.version,
          operation_id: operationId('remote-html-stale'),
          patch: { description: 'Remote change before HTML submit' },
        }),
      },
    });
    expect(remoteUpdate.ok()).toBeTruthy();

    const editForm = page.locator('[data-noscript-edit-form]');
    await editForm.getByLabel('Description', { exact: true }).fill('Local stale HTML draft');
    await editForm.locator('[data-noscript-edit-save]').click();

    await expect(page.locator('.workflow-issue-banner')).toContainText('Refresh the ticket');
    await expect(editForm.getByLabel('Description', { exact: true })).toHaveValue('Local stale HTML draft');
  });
});

test('requirements and history show retained labels, reasoning, and links', async ({ page }) => {
  await page.goto('/newsletter/workflows/delivery?ticket=fixture-review');

  await page.getByRole('tab', { name: 'Requirements' }).click();
  await expect(page.getByText('Required inputs', { exact: true })).toBeVisible();
  await expect(page.getByText('Required outputs', { exact: true })).toBeVisible();
  await expect(page.getByText('Qualitative criteria', { exact: true })).toBeVisible();
  await expect(page.getByText('The implementation satisfies the acceptance criteria.', { exact: true })).toBeVisible();

  await page.getByRole('tab', { name: 'History' }).click();
  await expect(page.getByText('Ticket created', { exact: true })).toBeVisible();
  await expect(page.getByText('Assigned to reviewer', { exact: true })).toBeVisible();
  await expect(page.getByText('{', { exact: true })).toHaveCount(0);

  await page.getByRole('tab', { name: 'Overview' }).click();
  await expect(page.getByText('Outputs', { exact: true })).toBeVisible();
  await expect(page.getByRole('term').filter({ hasText: 'Review verdict' })).toBeVisible();
});

test('desktop board and mobile ticket detail keep keyboard access and stable screenshots', async ({ page }, testInfo) => {
  const expectedCriteria = 'A stale ticket revision or workflow digest cannot change state. Repeating an accepted operation returns its original result without a second transition.';
  let detail = (await (await page.request.get('/newsletter/workflows/delivery/tickets/fixture-review/snapshot')).json()) as DetailSnapshot;
  if (detail.ticket.assignee !== 'reviewer') {
    const resetAssignee = await page.request.post('/newsletter/workflows/delivery/tickets/fixture-review/assignee', {
      headers: { Accept: 'application/json' },
      form: {
        payload: JSON.stringify({
          version: detail.ticket.version,
          operation_id: operationId('reset-screenshot-assignee'),
          assignee: 'reviewer',
        }),
      },
    });
    expect(resetAssignee.ok()).toBeTruthy();
    detail = (await (await page.request.get('/newsletter/workflows/delivery/tickets/fixture-review/snapshot')).json()) as DetailSnapshot;
  }
  if (detail.fields.find((field) => field.id === 'acceptance-criteria')?.value !== expectedCriteria) {
    const resetInputs = await page.request.post('/newsletter/workflows/delivery/tickets/fixture-review/update', {
      headers: { Accept: 'application/json' },
      form: {
        payload: JSON.stringify({
          version: detail.ticket.version,
          operation_id: operationId('reset-screenshot-inputs'),
          patch: {
            field_values: {
              'acceptance-criteria': expectedCriteria,
            },
          },
        }),
      },
    });
    expect(resetInputs.ok()).toBeTruthy();
  }
  await page.goto('/newsletter/workflows/delivery?ticket=fixture-review');
  await expectBodyFocus(page);
  await tabTo(page, { role: 'textbox', name: 'Search tickets' });
  await tabTo(page, { role: 'combobox', name: 'All assignees' });
  const overviewTab = await tabTo(page, { role: 'tab', name: 'Overview' });
  await expect(overviewTab).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(page.getByRole('tab', { name: 'Requirements' })).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('tab', { name: 'Requirements' })).toHaveAttribute('aria-selected', 'true');
  await page.getByRole('tab', { name: 'Overview' }).click();
  await expect(page.getByRole('tab', { name: 'Overview' })).toHaveAttribute('aria-selected', 'true');
  await page.evaluate(() => {
    if (document.activeElement instanceof HTMLElement) {
      document.activeElement.blur();
    }
  });
  await assertNoLayoutIssues(page);
  if (!testInfo.project.name.startsWith('mobile')) {
    await expect(page).toHaveScreenshot('workflow-board-overview.png', { fullPage: true });
  } else {
    await page.goto('/newsletter/workflows/delivery');
    await expect(page).toHaveURL(/\/newsletter\/workflows\/delivery$/);
    await page.evaluate(() => {
      if (document.activeElement instanceof HTMLElement) {
        document.activeElement.blur();
      }
    });
    await assertNoLayoutIssues(page);
    await expect(page).toHaveScreenshot('workflow-board-mobile.png', { fullPage: true });
    await page.goto('/newsletter/workflows/delivery/tickets/fixture-review');
    await expect(page.getByRole('button', { name: 'Back to board', exact: true })).toBeVisible();
  }
  if (!testInfo.project.name.startsWith('mobile')) {
    await page.getByRole('link', { name: 'Expand', exact: true }).click();
    await expect(page).toHaveURL(/\/newsletter\/workflows\/delivery\/tickets\/fixture-review$/);
    await expect(page.getByRole('button', { name: 'Back to board', exact: true })).toBeVisible();
  }
  await page.evaluate(() => {
    if (document.activeElement instanceof HTMLElement) {
      document.activeElement.blur();
    }
  });
  await assertNoLayoutIssues(page);
  if (!testInfo.project.name.startsWith('mobile')) {
    await expect(page).toHaveScreenshot('workflow-ticket-desktop.png', { fullPage: true });
  }
  if (testInfo.project.name.startsWith('mobile')) {
    await expect(page).toHaveScreenshot('workflow-ticket-mobile.png', { fullPage: true });
  }
  await page.getByRole('button', { name: 'Back to board', exact: true }).click();
  await expect(page).toHaveURL(/\/newsletter\/workflows\/delivery/);
  await assertNoLayoutIssues(page);
  await assertNoConsoleErrors(page);
});

const HOSTILE_OUTPUT_VALUE = '<b>bold</b> & "quoted" <script>window.__xssFired = true</script>';

async function setTerminalReviewVerdict(
  request: APIRequestContext,
  ticketId: string,
  value = 'Verified durable result',
): Promise<void> {
  const current = await detailSnapshot(request, ticketId);
  const updated = await request.post(`/newsletter/workflows/delivery/tickets/${ticketId}/update`, {
    headers: { Accept: 'application/json' },
    form: { payload: JSON.stringify({
      version: current.ticket.version,
      operation_id: operationId('terminal-result'),
      patch: { field_values: { 'review-verdict': value } },
    }) },
  });
  expect(updated.ok()).toBeTruthy();
}

async function assertTerminalOutputReadOnly(page: Page, value = 'Verified durable result'): Promise<void> {
  const overview = page.locator('[data-ticket-panel="overview"]');
  await expect(overview.getByRole('heading', { name: 'Outputs', exact: true })).toBeVisible();
  await expect(overview.getByText(value, { exact: true })).toBeVisible();
  await expect(overview.locator('[data-ticket-input="review-verdict"]')).toHaveCount(0);
  await expect(overview.getByText('Not submitted', { exact: true })).toBeVisible();
}

// Proves the value is decoded literal DOM text, not interpreted markup: an unescaped
// regression would render <b>/<script> as elements, and the exact-text match above would fail.
async function assertOutputValueEscaped(page: Page, value: string): Promise<void> {
  await assertTerminalOutputReadOnly(page, value);
  const overview = page.locator('[data-ticket-panel="overview"]');
  await expect(overview.locator('b')).toHaveCount(0);
  await expect(overview.locator('script')).toHaveCount(0);
}

test('terminal output remains read-only in inspector and expanded view', async ({ page, request }) => {
  const ticketId = 'fixture-done-1';
  await setTerminalReviewVerdict(request, ticketId);
  for (const url of [
    `/newsletter/workflows/delivery?ticket=${ticketId}`,
    `/newsletter/workflows/delivery/tickets/${ticketId}`,
  ]) {
    await page.goto(url);
    await assertTerminalOutputReadOnly(page);
    await assertNoLayoutIssues(page);
    await assertNoConsoleErrors(page);
  }

  await setTerminalReviewVerdict(request, ticketId, HOSTILE_OUTPUT_VALUE);
  for (const url of [
    `/newsletter/workflows/delivery?ticket=${ticketId}`,
    `/newsletter/workflows/delivery/tickets/${ticketId}`,
  ]) {
    await page.goto(url);
    await assertOutputValueEscaped(page, HOSTILE_OUTPUT_VALUE);
    await assertNoLayoutIssues(page);
    await assertNoConsoleErrors(page);
  }

  const detail = await detailSnapshot(page.request, ticketId);
  expect(detail.fields.find((field) => field.id === 'review-verdict')?.is_output).toBe(true);
});

test('terminal output stays read-only at a 320px viewport', async ({ page, request }) => {
  const ticketId = 'fixture-done-1';
  await setTerminalReviewVerdict(request, ticketId);
  await page.setViewportSize({ width: 320, height: 900 });

  await page.goto(`/newsletter/workflows/delivery/tickets/${ticketId}`);
  await assertTerminalOutputReadOnly(page);
  await assertNoLayoutIssues(page);
  await assertNoConsoleErrors(page);

  await setTerminalReviewVerdict(request, ticketId, HOSTILE_OUTPUT_VALUE);
  await page.reload();
  await assertOutputValueEscaped(page, HOSTILE_OUTPUT_VALUE);
  await assertNoLayoutIssues(page);
  await assertNoConsoleErrors(page);
});

test.describe('javascript-disabled terminal output', () => {
  test.use({ javaScriptEnabled: false });

  test('terminal output remains read-only in the server-rendered expanded view', async ({ page, request }) => {
    const ticketId = 'fixture-done-1';
    await setTerminalReviewVerdict(request, ticketId);

    await page.goto(`/newsletter/workflows/delivery/tickets/${ticketId}`);
    await assertTerminalOutputReadOnly(page);
    await assertNoConsoleErrors(page);

    await setTerminalReviewVerdict(request, ticketId, HOSTILE_OUTPUT_VALUE);
    await page.reload();
    await assertOutputValueEscaped(page, HOSTILE_OUTPUT_VALUE);
  });
});
// ── Shared refresh lifecycle: one workflow loop, shell regions ride the same snapshot ────────

const WORKFLOW_BOARD_URL = '/newsletter/workflows/delivery?ticket=fixture-review';

// Every passive read the page makes: the workflow snapshot and any generic `?__live=1` shell read.
function trackPassiveReads(page: Page): string[] {
  const reads: string[] = [];
  page.on('request', (requestEvent) => {
    const url = new URL(requestEvent.url());
    if (requestEvent.method() === 'GET' && (url.pathname.endsWith('/snapshot') || url.searchParams.has('__live'))) {
      reads.push(`${url.pathname}${url.search}`);
    }
  });
  return reads;
}

async function openWorkflowOnPausedClock(page: Page, url = WORKFLOW_BOARD_URL): Promise<void> {
  await page.clock.install({ time: 0 });
  await page.goto(url);
  await waitForWorkflowController(page);
  await page.clock.pauseAt(600_000);
  await page.waitForTimeout(500);
}

async function applyLiveChange(request: APIRequestContext, change: string): Promise<void> {
  const response = await request.post('/__ui/live/change', { data: { case: change } });
  expect(response.status()).toBe(204);
}

// Advances the 2000 ms cadence until the assertion holds.
async function refreshUntil(page: Page, assertion: () => Promise<void>): Promise<void> {
  await expect(async () => {
    await page.clock.runFor(2000);
    await assertion();
  }).toPass({ timeout: 15_000, intervals: [100] });
}

test('workflow data refreshes through one shared handle with one read per cycle', async ({ page }) => {
  const reads = trackPassiveReads(page);
  await openWorkflowOnPausedClock(page);

  expect(await page.evaluate(() => Array.from(window.FlowgencyLive.handles.keys()))).toEqual([WORKFLOW_HANDLE_KEY]);
  await expect(page.locator('#live-initial')).toHaveCount(0);

  const base = reads.length;
  for (let cycle = 1; cycle <= 3; cycle += 1) {
    await page.clock.runFor(2000);
    await expect.poll(() => reads.length).toBe(base + cycle);
    await page.waitForTimeout(250);
    // A second loop would have issued its own read on the same tick.
    expect(reads).toHaveLength(base + cycle);
  }
  expect(reads.every((read) => read.startsWith('/newsletter/workflows/delivery/snapshot?'))).toBe(true);
  expect(reads.some((read) => read.includes('__live'))).toBe(false);
});

test('navigation keeps one loop and retargets its reads to the new selection', async ({ page }) => {
  const reads = trackPassiveReads(page);
  await openWorkflowOnPausedClock(page);

  await page.getByRole('link', { name: 'Review the local storage contract' }).click();
  await expect(page).toHaveURL(/ticket=fixture-review-2/);
  await expect.poll(() => reads.some((read) => read.includes('ticket=fixture-review-2'))).toBe(true);
  await page.waitForTimeout(300);

  expect(await page.evaluate(() => Array.from(window.FlowgencyLive.handles.keys()))).toEqual([WORKFLOW_HANDLE_KEY]);
  const marker = reads.length;
  await page.clock.runFor(2000);
  await expect.poll(() => reads.length).toBe(marker + 1);
  await page.waitForTimeout(250);
  expect(reads).toHaveLength(marker + 1);
  expect(reads.at(-1)).toContain('ticket=fixture-review-2');
});

test('shared navigation refreshes from the workflow snapshot without another read', async ({ page, request }) => {
  const reads = trackPassiveReads(page);
  await openWorkflowOnPausedClock(page);
  const badge = page.locator('#sidebar [data-live-key="workflow:delivery"] [data-workflow-state="count"]');
  const before = Number(await badge.textContent());
  const sidebar = await page.locator('#sidebar').elementHandle();
  const row = await page.locator('#sidebar [data-live-key="workflow:delivery"]').elementHandle();
  const boardPage = await page.locator('.workflow-board-page').elementHandle();
  const inspector = await page.getByLabel('Ticket details').elementHandle();
  await page.getByLabel('Acceptance criteria', { exact: true }).fill('Draft survives the shell refresh');
  const readsBefore = reads.length;

  await applyLiveChange(request, 'navigation-workflow-count');
  await refreshUntil(page, () => expect(badge).toHaveText(String(before + 1), { timeout: 1500 }));

  await expect(page.getByText(`${before + 1} tickets`, { exact: true })).toBeVisible();
  expect(await sidebar!.evaluate((node) => node.isConnected)).toBe(true);
  expect(await row!.evaluate((node) => node.isConnected)).toBe(true);
  expect(await boardPage!.evaluate((node) => node.isConnected && node === document.querySelector('.workflow-board-page'))).toBe(true);
  expect(await inspector!.evaluate((node) => node.isConnected && node === document.querySelector('[aria-label="Ticket details"]'))).toBe(true);
  await expect(page.getByLabel('Acceptance criteria', { exact: true })).toHaveValue('Draft survives the shell refresh');
  const newReads = reads.slice(readsBefore);
  expect(newReads.length).toBeGreaterThan(0);
  expect(newReads.every((read) => read.startsWith('/newsletter/workflows/delivery/snapshot?'))).toBe(true);
  expect(reads.some((read) => read.includes('__live'))).toBe(false);
  await assertNoConsoleErrors(page);
});

test('a shell-only change reaches the navigation through the workflow snapshot only', async ({ page, request }) => {
  const reads = trackPassiveReads(page);
  await openWorkflowOnPausedClock(page);
  await expect(page.locator('#sidebar')).not.toContainText('Research updated');
  const field = page.getByLabel('Acceptance criteria', { exact: true });
  await field.fill('Draft kept while the team label changes');
  const fieldNode = await page.locator('#field-acceptance-criteria').elementHandle();

  await applyLiveChange(request, 'navigation-membership');
  await refreshUntil(page, () => expect(page.locator('#sidebar')).toContainText('Research updated', { timeout: 1500 }));

  await expect(page.locator('#team-switcher option[value="research"]')).toHaveText('Research updated');
  await expect(page.locator('#team-switcher')).toHaveValue('newsletter');
  await expect(page.locator('#sidebar a[href="/newsletter/workflows/delivery"]')).toHaveClass(/active/);
  expect(await fieldNode!.evaluate((node) => node.isConnected && node === document.getElementById('field-acceptance-criteria'))).toBe(true);
  await expect(field).toHaveValue('Draft kept while the team label changes');
  expect(reads.some((read) => read.includes('__live'))).toBe(false);
  // Only the declared navigation regions are ever reconciled; nothing of the board is a live region.
  await expect(page.locator('[data-live-region]:not(#sidebar [data-live-region])')).toHaveCount(0);
  await assertNoConsoleErrors(page);
});

test('a snapshot that reports the workflow gone shows a workflow-unavailable status and recovers', async ({ page }) => {
  await page.route('**/newsletter/workflows/delivery/snapshot?*', async (route) => {
    await route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ detail: 'Unknown workflow' }) });
  }, { times: 1 });
  await page.goto(WORKFLOW_BOARD_URL);
  await stopPollTimer(page);
  await page.getByLabel('Acceptance criteria', { exact: true }).fill('Draft survives an unavailable workflow');

  await forcePoll(page);

  await expect(page.locator('#workflow-refresh-status')).toContainText('Workflow unavailable');
  await expect(page.locator('#workflow-refresh-status')).toHaveAttribute('data-refresh-kind', 'unavailable');
  await expect(page.getByLabel('Acceptance criteria', { exact: true })).toHaveValue('Draft survives an unavailable workflow');

  await forcePoll(page);

  await expect(page.locator('#workflow-refresh-status')).toBeHidden();
});
