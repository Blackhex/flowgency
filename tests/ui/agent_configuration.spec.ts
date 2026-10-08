import AxeBuilder from '@axe-core/playwright';
import { readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';

import { expect, test, type APIRequestContext, type Locator, type Page } from '@playwright/test';

import { expectBodyFocus, tabTo } from './keyboard';
import { assertNoConsoleErrors, assertNoLayoutIssues, installBasePageSetup } from './layout';
import { expectNoNotice, expectOnlyNotice, expectQuietPolls, notices } from './live_notice';

const runtimeConfigPath = path.resolve(__dirname, '.runtime', 'current', 'config.yaml');

async function resetUiRuntime(request: APIRequestContext): Promise<void> {
  const response = await request.post('/__ui/reset');
  expect(response.status()).toBe(204);
}

async function readRuntimeConfig(): Promise<string> {
  return await readFile(runtimeConfigPath, 'utf8');
}

async function writeRuntimeConfig(source: string): Promise<void> {
  await writeFile(runtimeConfigPath, source, 'utf8');
}

async function seedAdvisorCopilotModel(): Promise<void> {
  const source = await readRuntimeConfig();
  const seeded = source.replace(
    '      integration: copilot\n      identity:\n',
    '      integration: copilot\n      integration_config:\n        model: gpt-5.4\n      identity:\n',
  );
  if (seeded === source) {
    throw new Error('advisor Copilot fixture block not found');
  }
  await writeRuntimeConfig(seeded);
}

async function expectNoAxeViolations(page: Page): Promise<void> {
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze();
  expect(results.violations, JSON.stringify(results.violations, null, 2)).toEqual([]);
}

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

const tabs = ['Profile', 'Blueprint', 'Runtime', 'Permissions', 'Prompts', 'Routines', 'Memory', 'Activity', 'Logs'];

test.beforeEach(async ({ page, request }, testInfo) => {
  await resetUiRuntime(request);
  const dark = testInfo.project.name.endsWith('dark');
  await installBasePageSetup(page, dark ? 'dark' : 'light');
  await page.addStyleTag({ content: '* { animation: none !important; transition: none !important; caret-color: transparent !important; }' });
});

test.afterEach(async ({ page, request }) => {
  await page.close();
  await resetUiRuntime(request);
});

test('team settings leads to the sole roster and inherited runtime', async ({ page }) => {
  await page.goto('/admin/teams/newsletter/edit');
  await expect(page.getByRole('heading', { name: 'Edit: Newsletter' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Create Instance' })).toHaveCount(0);
  await expect(page.getByText('Advisor')).toHaveCount(0);
  const workspacePath = page.locator('#workspace_path');
  const teamPath = page.locator('#path');
  const permissionRulesField = page.locator('#permission_rules_yaml');
  await expect(workspacePath).toHaveValue(/tests[\\/]ui[\\/]\.runtime[\\/]current[\\/]workspaces[\\/]newsletter$/);
  await expect(teamPath).toHaveValue(/tests[\\/]ui[\\/]\.runtime[\\/]current[\\/]teams[\\/]newsletter$/);
  await expect(permissionRulesField).toHaveValue(/workspaces[\\/]newsletter/);
  await assertNoLayoutIssues(page);
  // These fields hold absolute paths that vary per checkout, so compare them as text only.
  await expect(page).toHaveScreenshot('group-settings.png', {
    fullPage: true,
    mask: [workspacePath, teamPath, permissionRulesField],
  });

  await expectBodyFocus(page);
  await tabTo(page, { role: 'link', name: /Manage agents/, href: '/newsletter/agents' });
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/\/newsletter\/agents$/);
  await expect(page.getByText('Blueprint: advisor')).toBeVisible();
  await expect(page.getByText('waiting for memory')).toBeVisible();
  await assertNoLayoutIssues(page);
  await expect(page).toHaveScreenshot('agent-roster.png', { fullPage: true });

  await expectBodyFocus(page);
  await tabTo(page, { role: 'link', name: 'Configure', href: '/newsletter/agents/advisor/profile' });
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL('/newsletter/agents/advisor/profile');
  await expectBodyFocus(page);
  await tabTo(page, { role: 'tab', name: 'Runtime', href: '/newsletter/agents/advisor/runtime' });
  await page.keyboard.press('Shift+Tab');
  await expect(page.getByRole('tab', { name: 'Blueprint' })).toBeFocused();
  await page.keyboard.press('Tab');
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/\/newsletter\/agents\/advisor\/runtime$/);
  await expect(page.getByRole('heading', { name: 'Team default' })).toBeVisible();
  await expect(page.locator('body')).not.toContainText(/\.runtime\/run-\d+/);
  await expect(page.getByText('Timeout: 2400s', { exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Pinned integration' }).locator('..')).toContainText('Copilot');
  await expect(page.getByRole('heading', { name: 'Pinned integration' }).locator('..')).toContainText('copilot');
  await expect(page.getByRole('link', { name: 'Open Permissions' }).first()).toHaveAttribute('href', '/newsletter/agents/advisor/permissions');
  const timeoutField = page.locator('input[name="timeout"]');
  await expect(timeoutField).toHaveValue('1200');
  await expect(page.locator('body')).not.toContainText('permission_rules_yaml');
  await expect(page.locator('body')).not.toContainText('Effective preview');
  await expect(page.locator('body')).not.toContainText(/^Mode$/m);
  await assertNoLayoutIssues(page);
  await expect(page).toHaveScreenshot('agent-runtime.png', {
    fullPage: true,
  });
  await assertNoConsoleErrors(page);
});

test('all agent detail tabs have stable selected semantics and keyboard focus', async ({ page }) => {
  await page.goto('/newsletter/agents/advisor/profile');
  const tabNav = page.locator('nav[aria-label="Agent detail tabs"]');
  await expect(tabNav).toBeVisible();
  await expect(tabNav.getByRole('tab')).toHaveText(tabs);

  for (const tab of tabs) {
    await page.goto('/newsletter/agents/advisor/profile');
    await expectBodyFocus(page);
    const link = await tabTo(page, { role: 'tab', name: tab, href: `/newsletter/agents/advisor/${tab.toLowerCase()}` });
    await expect(link).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(page).toHaveURL(new RegExp(`/newsletter/agents/advisor/${tab.toLowerCase()}$`));
    await expect(page.getByRole('tab', { name: tab })).toHaveAttribute('aria-current', 'page');
    await assertNoLayoutIssues(page);
  }
  await assertNoConsoleErrors(page);
});

test('copilot runtime consent saves true then false and preserves timeout and unrelated config', async ({ page }) => {
  await seedAdvisorCopilotModel();
  await page.goto('/newsletter/agents/advisor/runtime');

  const consent = page.locator('input[name="integration_config.allow_local_network"]');
  const timeoutField = page.locator('input[name="timeout"]');
  const saveButton = page.getByRole('button', { name: 'Save runtime', exact: true });

  await expect(consent).not.toBeChecked();
  await expect(timeoutField).toHaveValue('1200');
  await expect(page.getByText('Allows connections to local services and LAN hosts, not only Flowgency.', { exact: true })).toBeVisible();

  await consent.check();
  await timeoutField.fill('1801');
  const saveTrue = page.waitForResponse((response) => response.url().includes('/newsletter/agents/advisor/runtime') && response.request().method() === 'POST');
  await saveButton.click();
  expect((await saveTrue).status()).toBe(303);
  await page.reload();

  await expect(consent).toBeChecked();
  await expect(timeoutField).toHaveValue('1801');
  const enabledConfig = await readRuntimeConfig();
  expect(enabledConfig).toContain('model: gpt-5.4');
  expect(enabledConfig).toContain('allow_local_network: true');
  expect(enabledConfig).toContain('timeout: 1801');

  await consent.uncheck();
  const saveFalse = page.waitForResponse((response) => response.url().includes('/newsletter/agents/advisor/runtime') && response.request().method() === 'POST');
  await saveButton.click();
  expect((await saveFalse).status()).toBe(303);
  await page.reload();

  await expect(consent).not.toBeChecked();
  await expect(timeoutField).toHaveValue('1801');
  const disabledConfig = await readRuntimeConfig();
  expect(disabledConfig).toContain('model: gpt-5.4');
  expect(disabledConfig).toContain('allow_local_network: false');
  expect(disabledConfig).toContain('timeout: 1801');
  await assertNoLayoutIssues(page);
  await expectNoAxeViolations(page);
  await assertNoConsoleErrors(page);
});

test('stale runtime save keeps the submitted timeout and consent draft visible', async ({ page }) => {
  await seedAdvisorCopilotModel();
  await page.goto('/newsletter/agents/advisor/runtime');

  const consent = page.locator('input[name="integration_config.allow_local_network"]');
  const timeoutField = page.locator('input[name="timeout"]');
  const saveButton = page.getByRole('button', { name: 'Save runtime', exact: true });

  await consent.check();
  await timeoutField.fill('1801');
  await writeRuntimeConfig((await readRuntimeConfig()).replace('title: Flowgency UI Gate', 'title: Changed elsewhere'));

  const staleSave = page.waitForResponse((response) => response.url().includes('/newsletter/agents/advisor/runtime') && response.request().method() === 'POST');
  await saveButton.click();
  expect((await staleSave).status()).toBe(409);

  await expect(page.getByText('config.yaml changed; reload before saving', { exact: true })).toBeVisible();
  await expect(timeoutField).toHaveValue('1801');
  await expect(consent).toBeChecked();
  await assertNoLayoutIssues(page);
});

test('non-copilot runtime hides the local-network consent control', async ({ page }) => {
  await page.goto('/newsletter/agents/builder/runtime');

  await expect(page.getByRole('heading', { name: 'Runtime', exact: true })).toBeVisible();
  await expect(page.locator('input[name="integration_config.allow_local_network"]')).toHaveCount(0);
  await expect(page.getByText('Allows connections to local services and LAN hosts, not only Flowgency.', { exact: true })).toHaveCount(0);
  await assertNoLayoutIssues(page);
  await assertNoConsoleErrors(page);
});

test('roster launcher keeps dialog focus, supports grouped prompt selection, and toggles one-off validation', async ({ page }) => {
  await page.goto('/newsletter/agents');
  await expectBodyFocus(page);

  const addAgentButton = await tabTo(page, { role: 'button', name: 'Add agent' });
  await expect(addAgentButton).toBeFocused();
  await page.keyboard.press('Enter');

  const dialog = page.locator('#add-agent-dialog');
  await expect(dialog).toBeVisible();
  await expect(page.getByRole('textbox', { name: 'Instance name' })).toBeFocused();

  const cancelButton = await tabTo(page, { role: 'button', name: 'Cancel' });
  await expect(cancelButton).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(dialog).not.toBeVisible();
  await expect(addAgentButton).toBeFocused();

  const launcher = page.locator('[data-launch-form]').first();
  const promptSelect = launcher.locator('[data-prompt-select]');
  await expect(promptSelect.locator('optgroup[label="Shared from blueprint"] option')).toHaveCount(2);
  await expect(promptSelect.locator('optgroup[label="Private to this instance"] option')).toHaveCount(1);
  await expect(launcher.locator('[data-prompt-description]')).toContainText('Shared daily review.');
  await expect(launcher.locator('[data-prompt-hint]')).toContainText('Mention the release window if relevant.');

  await promptSelect.selectOption('instance:local-triage');
  await expect(launcher.locator('[data-prompt-description]')).toContainText('Private local triage.');
  await expect(launcher.locator('[data-prompt-hint]')).toContainText('Escalate blockers if the draft is stale.');

  const savedRadio = await tabTo(page, { role: 'radio', name: 'Saved prompt' });
  await expect(savedRadio).toBeFocused();
  await page.keyboard.press('ArrowRight');
  const oneOffRadio = launcher.getByRole('radio', { name: 'One-off' });
  await expect(oneOffRadio).toBeFocused();
  await expect(oneOffRadio).toBeChecked();
  await expect(launcher.locator('[data-one-off-panel]')).toBeVisible();
  await expect(launcher.locator('[data-saved-panel]')).toBeHidden();
  await expect.poll(() => launcher.locator('[data-task-input]').evaluate((node) => (node as HTMLTextAreaElement).required)).toBe(true);

  await page.keyboard.press('ArrowLeft');
  await expect(savedRadio).toBeFocused();
  await expect(savedRadio).toBeChecked();
  await expect(launcher.locator('[data-saved-panel]')).toBeVisible();
  await expect(launcher.locator('[data-one-off-panel]')).toBeHidden();
  await expect.poll(() => launcher.locator('[data-task-input]').evaluate((node) => (node as HTMLTextAreaElement).required)).toBe(false);

  await assertNoLayoutIssues(page);
  await assertNoConsoleErrors(page);
});

test('library instructions and skill targets are canonical', async ({ page }) => {
  await page.goto('/admin/agent-library');
  await expect(page.getByRole('heading', { name: 'Agent Library' })).toBeVisible();
  await assertNoLayoutIssues(page);
  await expect(page).toHaveScreenshot('agent-library.png', { fullPage: true });
  await page.getByRole('link', { name: /Advisor/ }).press('Enter');
  await expect(page.getByRole('heading', { name: 'AGENTS.md' })).toBeVisible();
  await page.getByRole('link', { name: /daily-review/ }).first().press('Enter');
  await expect(page.getByRole('heading', { name: 'SKILL.md' })).toBeVisible();
  await assertNoConsoleErrors(page);
});

test('agent prompts and shared prompt library views have stable visuals', async ({ page }) => {
  await page.goto('/newsletter/agents/advisor/prompts');
  await expect(page.getByRole('heading', { name: 'Prompts', exact: true })).toBeVisible();
  await expect(page.getByText('Shared from blueprint')).toBeVisible();
  await expect(page.getByText('Private to this instance')).toBeVisible();
  await assertNoLayoutIssues(page);
  await expect(page).toHaveScreenshot('agent-prompts.png', { fullPage: true });

  await page.goto('/admin/agent-library/blueprints/advisor/prompts');
  await expect(page.getByRole('heading', { name: 'Shared prompt source editor' })).toBeVisible();
  await page.getByRole('link', { name: 'release-window' }).press('Enter');
  await expect(page.getByRole('heading', { name: 'release-window' })).toBeVisible();
  await assertNoLayoutIssues(page);
  await expect(page).toHaveScreenshot('prompt-library.png', { fullPage: true });
  await assertNoConsoleErrors(page);
});

test('memory channel uses a friendly label without normal hash disclosure', async ({ page }) => {
  await page.goto('/admin/memory-channels');
  await assertNoLayoutIssues(page);
  await page.getByRole('link', { name: 'brand-strategy' }).press('Enter');
  await expect(page.getByRole('heading', { name: 'Brand Strategy' })).toBeVisible();
  await expect(page.getByText('Internal hash')).toHaveCount(0);
  await expect(page.locator('body')).not.toContainText('22222222222222222222222222222222');
  await assertNoLayoutIssues(page);
  await expect(page).toHaveScreenshot('memory-channel.png', { fullPage: true });
  await assertNoConsoleErrors(page);
});

test('destructive controls require confirmation or a review page and are keyboard reachable', async ({ page }) => {
  await page.goto('/newsletter/agents');
  await expectBodyFocus(page);
  await tabTo(page, { role: 'textbox', name: 'Target team' });
  await page.keyboard.type('research');
  await page.keyboard.press('Tab');
  await expect(page.getByRole('combobox', { name: 'Memory move mode' }).first()).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(page.getByRole('button', { name: 'Move' }).first()).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('heading', { name: /Move advisor/ })).toBeVisible();
  await expect(page).toHaveURL('/newsletter/agents/advisor/move');

  await page.goto('/newsletter/agents');
  await expectBodyFocus(page);
  await tabTo(page, { role: 'button', name: 'Remove' });
  let removeMessage = '';
  page.once('dialog', async (dialog) => {
    expect(dialog.type()).toBe('confirm');
    removeMessage = dialog.message();
    await dialog.dismiss();
  });
  await page.keyboard.press('Enter');
  expect(removeMessage).toContain('Remove Advisor');
  await expect(page).toHaveURL('/newsletter/agents');
  await expect(page.getByText('Advisor', { exact: true })).toBeVisible();

  await page.goto('/admin/memory-channels/brand-strategy');
  await expectBodyFocus(page);
  await tabTo(page, { role: 'button', name: 'Delete channel' });
  let deleteMessage = '';
  page.once('dialog', async (dialog) => {
    expect(dialog.type()).toBe('confirm');
    deleteMessage = dialog.message();
    await dialog.dismiss();
  });
  await page.keyboard.press('Enter');
  expect(deleteMessage).toBe('Delete this memory channel?');
  await expect(page.getByRole('heading', { name: 'Brand Strategy' })).toBeVisible();
  await assertNoConsoleErrors(page);
});

test('mobile navigation preserves theme and keyboard focus', async ({ page }, testInfo) => {
  test.skip(!testInfo.project.name.startsWith('mobile-'));
  const startingTheme = testInfo.project.name.endsWith('dark') ? 'dark' : 'light';
  await page.goto('/newsletter/');
  await expectBodyFocus(page);
  const menu = await tabTo(page, { role: 'button', name: 'Open navigation' });
  await expect(menu).toHaveAttribute('aria-expanded', 'false');
  await page.keyboard.press('Space');
  await expect(menu).toHaveAttribute('aria-expanded', 'true');
  await expect(page.getByRole('navigation')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Close navigation' })).toBeFocused();

  await tabTo(page, { role: 'link', name: 'Agent Library', href: '/admin/agent-library' });
  await tabTo(page, { role: 'link', name: 'Memory Channels', href: '/admin/memory-channels' });
  await tabTo(page, { role: 'link', name: 'Jobs', href: '/newsletter/jobs' });
  const themeToggle = await tabTo(page, { role: 'button', name: /mode/ });
  await page.keyboard.press('Space');
  const changedTheme = startingTheme === 'dark' ? 'light' : 'dark';
  await expect(page.locator('html')).toHaveClass(new RegExp(changedTheme === 'dark' ? '\\bdark\\b' : '^(?!.*\\bdark\\b)'));
  await expect.poll(() => page.evaluate(() => localStorage.getItem('theme'))).toBe(changedTheme);
  await page.reload();
  await expect.poll(() => page.evaluate(() => localStorage.getItem('theme'))).toBe(changedTheme);
  await expect(page.locator('html')).toHaveClass(new RegExp(changedTheme === 'dark' ? '\\bdark\\b' : '^(?!.*\\bdark\\b)'));

  await expectBodyFocus(page);
  await tabTo(page, { role: 'button', name: 'Open navigation' });
  await page.keyboard.press('Space');
  await tabTo(page, { role: 'button', name: /mode/ });
  await page.keyboard.press('Space');
  await expect.poll(() => page.evaluate(() => localStorage.getItem('theme'))).toBe(startingTheme);
  await page.keyboard.press('Escape');
  await expect(page.getByRole('button', { name: 'Open navigation' })).toHaveAttribute('aria-expanded', 'false');
  await expect(page.getByRole('button', { name: 'Open navigation' })).toBeFocused();
  await page.keyboard.press('Space');
  await expect(page.getByRole('button', { name: 'Close navigation' })).toBeFocused();
  await expect(page).toHaveScreenshot('mobile-navigation.png', { fullPage: true });
  await assertNoConsoleErrors(page);
});

async function changeLiveFixture(request: APIRequestContext, name: string): Promise<void> {
  expect((await request.post('/__ui/live/change', { data: { case: name } })).status()).toBe(204);
}

test.describe('agent roster live refresh', () => {
  test('refreshes identity and active jobs in place without touching held controls or loaded revisions', async ({ page, request }) => {
    await page.goto('/newsletter/agents');
    const row = page.locator('[data-live-key="agent:advisor"]');
    const move = row.locator('form[action$="/advisor/move"]');
    const loadedRevision = await move.locator('input[name="revision"]').inputValue();
    const target = move.locator('input[name="target_team"]');
    await target.fill('research');
    const memoryScope = row.locator('select[name="memory_scope"]');
    await memoryScope.selectOption('team');
    await memoryScope.focus();
    const optionCount = await memoryScope.locator('option').count();
    await row.evaluate((node) => { (node as unknown as { __kept: boolean }).__kept = true; });
    await memoryScope.evaluate((node) => { (node as unknown as { __kept: boolean }).__kept = true; });

    await changeLiveFixture(request, 'agent-source-and-status');

    await expect(row).toContainText('Principal Strategist');
    await expect(row.locator('[data-live-key="job:agent-live-running"]')).toHaveText('Running');
    await expect(row.locator('[data-active-job-count]')).toHaveText('2');
    await expect(target).toHaveValue('research');
    await expect(memoryScope).toHaveValue('team');
    await expect(memoryScope).toBeFocused();
    expect(await memoryScope.locator('option').count()).toBe(optionCount);
    expect(await memoryScope.evaluate((node) => (node as unknown as { __kept?: boolean }).__kept)).toBe(true);
    expect(await row.evaluate((node) => (node as unknown as { __kept?: boolean }).__kept)).toBe(true);
    await expect(move.locator('input[name="revision"]')).toHaveValue(loadedRevision);

    await move.getByRole('button', { name: 'Move' }).click();
    await expect(page.getByText('config.yaml changed; reload before previewing move')).toBeVisible();
  });
});


test.describe('agent detail live refresh', () => {
  const status = (page: Page) => page.locator('[data-live-region="agent-status"]');
  const header = (page: Page) => page.locator('[data-live-region="agent-header"]');

  test('prompts keep the draft and loaded digest while the catalog digest and status update', async ({ page, request }) => {
    await page.goto('/newsletter/agents/advisor/prompts');
    const card = page.locator('[data-live-key="prompt:local-triage"]');
    const digest = card.locator('form[action$="/save"] input[name="digest"]');
    const loadedDigest = await digest.inputValue();
    const source = card.locator('textarea[name="source"]');
    const draft = '---\nname: local-triage\ndescription: Private local triage.\n---\n\nLocal working draft\n';
    await source.fill(draft);
    await card.evaluate((node) => { (node as unknown as { __kept: boolean }).__kept = true; });

    await changeLiveFixture(request, 'agent-source-and-status');

    await expect(card.locator('p', { hasText: 'Digest:' })).not.toContainText(loadedDigest);
    await expect(header(page)).toContainText('Principal Strategist');
    await expect(status(page)).toContainText('Running job agent-live-running');
    await expect(source).toHaveValue(draft);
    await expect(digest).toHaveValue(loadedDigest);
    expect(await card.evaluate((node) => (node as unknown as { __kept?: boolean }).__kept)).toBe(true);

    await card.getByRole('button', { name: 'Save source' }).click();
    await expect(page.getByText('Reload the latest prompt source before saving.')).toBeVisible();
    await expect(page.locator('textarea[name="source"]').nth(1)).toHaveValue(draft);
  });

  test('memory keeps the draft, loaded content revision and held file selector', async ({ page, request }) => {
    await page.goto('/newsletter/agents/advisor/memory');
    const memoryStatus = page.locator('[data-live-region="agent-memory-status"]');
    const initialStatus = await memoryStatus.textContent();
    const revision = page.locator('input[name="content_revision"]');
    const loadedRevision = await revision.inputValue();
    const content = page.locator('textarea[name="content"]');
    await content.fill('Local memory draft');
    const file = page.locator('select[name="filename"]');
    await file.focus();
    await file.evaluate((node) => { (node as unknown as { __kept: boolean }).__kept = true; });

    await changeLiveFixture(request, 'agent-memory-revision');

    await expect.poll(async () => await memoryStatus.textContent()).not.toBe(initialStatus);
    await expect(content).toHaveValue('Local memory draft');
    await expect(revision).toHaveValue(loadedRevision);
    await expect(file).toBeFocused();
    expect(await file.evaluate((node) => (node as unknown as { __kept?: boolean }).__kept)).toBe(true);

    await page.getByRole('button', { name: 'Save memory' }).click();
    await expect(page.getByText('Attempted content')).toBeVisible();
    await expect(page.locator('pre').filter({ hasText: 'Local memory draft' })).toBeVisible();
  });

  test('runtime keeps the timeout draft and loaded revision while the team default updates', async ({ page, request }) => {
    await page.goto('/newsletter/agents/advisor/runtime');
    const timeout = page.locator('input[name="timeout"]');
    const revision = page.locator('input[name="revision"]');
    const loadedRevision = await revision.inputValue();
    await timeout.fill('900');

    await changeLiveFixture(request, 'agent-team-runtime');

    await expect(page.locator('[data-live-region="agent-runtime-summary"]')).toContainText('Timeout: 3000s');
    await expect(timeout).toHaveValue('900');
    await expect(revision).toHaveValue(loadedRevision);
    await page.getByRole('button', { name: 'Save runtime' }).click();
    await expect(page.locator('input[name="timeout"]')).toHaveValue('900');
    await expect(page.locator('.border-amber-200').filter({ hasText: /config\.yaml changed/i })).toBeVisible();
  });

  test('a rejected runtime save keeps its loaded revision, so a resubmit conflicts again and the change stays announced', async ({ page, request }) => {
    await page.goto('/newsletter/agents/advisor/runtime');
    const revision = page.locator('input[name="revision"]');
    const loadedRevision = await revision.inputValue();
    await page.locator('input[name="timeout"]').fill('900');

    await changeLiveFixture(request, 'agent-team-runtime');
    await page.getByRole('button', { name: 'Save runtime' }).click();

    for (let attempt = 0; attempt < 2; attempt += 1) {
      await expect(page.locator('.border-amber-200').filter({ hasText: /config\.yaml changed/i })).toBeVisible();
      await expect(revision).toHaveValue(loadedRevision);
      await expect(page.locator('input[name="timeout"]')).toHaveValue('900');
      await expectOnlyNotice(page, 'Agent configuration changed since this form loaded. Reload to see the latest.');
      if (attempt === 0) await page.getByRole('button', { name: 'Save runtime' }).click();
    }
  });

  test('profile identity refreshes in the header while a name draft and its revision stay', async ({ page, request }) => {
    await page.goto('/newsletter/agents/advisor/profile');
    const name = page.locator('input[name="display_name"]');
    const revision = page.locator('input[name="revision"]');
    const loadedRevision = await revision.inputValue();
    await name.fill('Draft Advisor');

    await changeLiveFixture(request, 'agent-identity');

    await expect(header(page)).toContainText('Principal Strategist');
    await expect(status(page)).toContainText('Configuration revision');
    await expect(name).toHaveValue('Draft Advisor');
    await expect(revision).toHaveValue(loadedRevision);
    await page.getByRole('button', { name: 'Save profile' }).click();
    await expect(page.locator('.border-amber-200').filter({ hasText: /changed/i })).toBeVisible();
  });

  test('profile announces a changed configuration without touching the draft or the loaded revision', async ({ page, request }) => {
    await page.goto('/newsletter/agents/advisor/profile');
    await expectNoNotice(page);
    const name = page.locator('input[name="display_name"]');
    const revision = page.locator('input[name="revision"]');
    const loadedRevision = await revision.inputValue();
    await name.fill('Draft Advisor');
    await name.evaluate((node) => { (node as unknown as { __kept: boolean }).__kept = true; });

    await changeLiveFixture(request, 'agent-identity');

    await expectOnlyNotice(page, 'Agent configuration changed since this form loaded. Reload to see the latest.');
    await expect(revision).toHaveValue(loadedRevision);
    await expect(name).toHaveValue('Draft Advisor');
    expect(await name.evaluate((node) => (node as unknown as { __kept?: boolean }).__kept)).toBe(true);
    await expect(status(page)).toHaveAttribute('role', 'status');
    await expect(status(page)).toHaveAttribute('aria-live', 'polite');
    await expect(status(page)).toHaveAttribute('aria-atomic', 'true');
    await expectQuietPolls(page, [status(page), notices(page)]);

    expect((await request.post('/__ui/reset')).status()).toBe(204);
    await expectNoNotice(page);
  });

  test('memory announces changed content without advancing the loaded content revision', async ({ page, request }) => {
    await page.goto('/newsletter/agents/advisor/memory');
    await expectNoNotice(page);
    const memoryStatus = page.locator('[data-live-region="agent-memory-status"]');
    const revision = page.locator('input[name="content_revision"]');
    const loadedRevision = await revision.inputValue();
    const content = page.locator('textarea[name="content"]');
    await content.fill('Local memory draft');

    await changeLiveFixture(request, 'agent-memory-revision');

    await expectOnlyNotice(page, 'Memory content changed since this form loaded. Reload to see the latest.');
    await expect(revision).toHaveValue(loadedRevision);
    await expect(content).toHaveValue('Local memory draft');
    await expect(memoryStatus.locator('[data-live-key="memory:revision"]')).toHaveAttribute('role', 'status');
    await expectQuietPolls(page, [memoryStatus, status(page), notices(page)]);
  });

  test('a source-only prompt change is announced for the prompt and never advances its loaded digest', async ({ page, request }) => {
    await page.goto('/newsletter/agents/advisor/prompts');
    await expectNoNotice(page);
    const card = page.locator('[data-live-key="prompt:local-triage"]');
    const digest = card.locator('form[action$="/save"] input[name="digest"]');
    const loadedDigest = await digest.inputValue();
    const source = card.locator('textarea[name="source"]');
    const draft = '---\nname: local-triage\ndescription: Private local triage.\n---\n\nLocal working draft\n';
    await source.fill(draft);
    const loadedJobs = await status(page).textContent();

    await changeLiveFixture(request, 'agent-catalog-digest');

    await expectOnlyNotice(page, 'Prompt local-triage changed since this form loaded. Reload to see the latest.');
    await expect(digest).toHaveValue(loadedDigest);
    await expect(source).toHaveValue(draft);
    await expect(status(page)).toHaveText(loadedJobs!);
    await expectQuietPolls(page, [status(page), notices(page)]);

    expect((await request.post('/__ui/reset')).status()).toBe(204);
    await expectNoNotice(page);
  });

  test('a changed active-job set is announced and the visible default layout is untouched', async ({ page, request }) => {
    await page.goto('/newsletter/agents/advisor/blueprint');
    await expectNoNotice(page);
    const loadedCount = Number(/Active jobs: (\d+)/.exec(await status(page).textContent())?.[1]);

    await changeLiveFixture(request, 'agent-active-job');

    await expectOnlyNotice(page, `Active jobs changed since this page loaded. Now ${loadedCount + 1} active.`);
    await expect(status(page)).toContainText(`Active jobs: ${loadedCount + 1}`);
    await expect(header(page)).not.toContainText('active job');
    await expectQuietPolls(page, [status(page), notices(page)]);
  });

  test('blueprint, logs and activity show remote digest, log and report changes in place', async ({ page, request }) => {
    await page.goto('/newsletter/agents/advisor/blueprint');
    const blueprint = page.locator('[data-live-region="agent-blueprint"]');
    const initialBlueprint = await blueprint.textContent();
    await changeLiveFixture(request, 'agent-catalog-digest');
    await expect.poll(async () => await blueprint.textContent()).not.toBe(initialBlueprint);

    await page.goto('/newsletter/agents/advisor/logs');
    const list = page.locator('[data-live-region="agent-logs-list"]');
    await expect(list).not.toContainText('advisor-live-refresh.out');
    await changeLiveFixture(request, 'agent-log-membership');
    await expect(list).toContainText('advisor-live-refresh.out');
    await expect(page.locator('[data-live-region="agent-logs-count"]')).toContainText('file');

    await page.goto('/newsletter/agents/advisor/activity');
    const entries = page.locator('[data-live-region="agent-activity-entries"]');
    await expect(entries).not.toContainText('Published the live refresh handoff report.');
    await changeLiveFixture(request, 'agent-report-history');
    await expect(entries).toContainText('Published the live refresh handoff report.');
  });

  test('an open add-agent dialog and its draft survive a refresh of the roster behind it', async ({ page, request }) => {
    await page.goto('/newsletter/agents');
    await page.getByRole('button', { name: 'Add agent' }).first().click();
    const dialog = page.locator('#add-agent-dialog');
    await expect(dialog).toBeVisible();
    await dialog.locator('input[name="name"]').fill('draft-agent');
    await dialog.evaluate((node) => { (node as unknown as { __kept: boolean }).__kept = true; });

    await changeLiveFixture(request, 'agent-source-and-status');

    await expect(page.locator('[data-live-key="agent:advisor"]')).toContainText('Principal Strategist');
    await expect(dialog).toBeVisible();
    await expect(dialog.locator('input[name="name"]')).toHaveValue('draft-agent');
    expect(await dialog.evaluate((node) => (node as unknown as { __kept?: boolean }).__kept)).toBe(true);
  });
});


test.describe('activity reports live refresh', () => {
  test('inserted reports initialize, discarded reports release their observers and expanded reports stay open', async ({ page, request }) => {
    await page.addInitScript(() => {
      const Native = window.ResizeObserver;
      const stats = { observed: 0, disconnected: 0 };
      (window as unknown as { __roStats: typeof stats }).__roStats = stats;
      window.ResizeObserver = class extends Native {
        observe(target: Element, options?: ResizeObserverOptions) {
          stats.observed += 1;
          return super.observe(target, options);
        }

        disconnect() {
          stats.disconnected += 1;
          return super.disconnect();
        }
      };
    });
    expect((await request.post('/__ui/reset', { data: { fixture: 'agent-activity-logs' } })).status()).toBe(204);
    await page.goto('/newsletter/agents/advisor/activity');
    const longReport = page.locator('section ol > li').filter({ hasText: 'Triage identified and documented the violated invariant' }).first();
    const toggle = longReport.locator('[data-report-toggle]');
    const text = longReport.locator('[data-report-text]');
    await expect(toggle).toHaveText('Show more');
    await toggle.click();
    await expect(toggle).toHaveAttribute('aria-expanded', 'true');
    await expect(toggle).toHaveText('Show less');
    // A focused control holds its ancestors, so a structural change would wait for the blur.
    await toggle.evaluate((node) => (node as HTMLElement).blur());
    await longReport.evaluate((node) => { (node as unknown as { __kept: boolean }).__kept = true; });
    const stats = () => page.evaluate(() => (window as unknown as { __roStats: { observed: number; disconnected: number } }).__roStats);
    const before = await stats();
    expect(await page.evaluate(() => typeof (window as unknown as { initActivityReports: unknown }).initActivityReports)).toBe('function');
    expect(await page.evaluate(() => typeof (window as unknown as { disposeActivityReports: unknown }).disposeActivityReports)).toBe('function');

    await changeLiveFixture(request, 'agent-report-history');

    const entries = page.locator('[data-live-region="agent-activity-entries"]');
    await expect(entries).toContainText('Published the live refresh handoff report.');
    await expect.poll(async () => (await stats()).observed).toBeGreaterThan(before.observed);
    await expect(toggle).toHaveAttribute('aria-expanded', 'true');
    await expect(toggle).toHaveText('Show less');
    expect(await text.evaluate((node) => getComputedStyle(node).overflow)).not.toBe('hidden');
    expect(await longReport.evaluate((node) => (node as unknown as { __kept?: boolean }).__kept)).toBe(true);

    const observed = (await stats()).observed;
    await page.evaluate(() => {
      const probe = document.createElement('div');
      probe.id = 'report-probe';
      probe.innerHTML = '<div data-activity-report><div data-report-text>probe</div><button type="button" data-report-toggle hidden aria-expanded="false">Show more</button></div>';
      document.body.append(probe);
    });
    await expect.poll(async () => (await stats()).observed).toBe(observed + 1);
    const disconnected = (await stats()).disconnected;
    await page.evaluate(() => document.getElementById('report-probe')?.remove());
    await expect.poll(async () => (await stats()).disconnected).toBe(disconnected + 1);
  });
});
