import { expect, test, type APIRequestContext } from '@playwright/test';

import { expectBodyFocus, tabTo } from './keyboard';
import { assertNoLayoutIssues, assertNoConsoleErrors, installConsoleErrorGate } from './layout';

type DetailSnapshot = {
  ticket: {
    version: unknown;
    ref: { ticket_id: string };
    assignee: string | null;
  };
  fields: Array<{ id: string; value: unknown }>;
};

function operationId(label: string): string {
  return `${label}-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

async function resetUiRuntime(request: APIRequestContext): Promise<void> {
  const response = await request.post('/__ui/reset');
  expect(response.status()).toBe(204);
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
  await page.getByLabel('Title', { exact: true }).fill('Capture typed workflow values');
  await page.getByLabel('Description', { exact: true }).fill('Keep boolean false and numeric zero visible without coercion.');
  await page.getByRole('button', { name: 'Create ticket', exact: true }).click();

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