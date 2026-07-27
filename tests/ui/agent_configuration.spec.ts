import { expect, test } from '@playwright/test';

import { expectBodyFocus, tabTo } from './keyboard';
import { assertNoConsoleErrors, assertNoLayoutIssues, installConsoleErrorGate } from './layout';

const tabs = ['Profile', 'Blueprint', 'Runtime', 'Routines', 'Prompts', 'Memory', 'Activity'];

test.beforeEach(async ({ page }, testInfo) => {
  installConsoleErrorGate(page);
  const dark = testInfo.project.name.endsWith('dark');
  await page.addInitScript((theme) => {
    if (!localStorage.getItem('theme')) localStorage.setItem('theme', theme);
  }, dark ? 'dark' : 'light');
  await page.addStyleTag({ content: '* { animation: none !important; transition: none !important; caret-color: transparent !important; }' });
});

test('group settings leads to the sole roster and inherited runtime', async ({ page }) => {
  await page.goto('/admin/orgs/newsletter/edit');
  await expect(page.getByRole('heading', { name: 'Edit: Newsletter' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Create Instance' })).toHaveCount(0);
  await expect(page.getByText('Advisor')).toHaveCount(0);
  const workspacePath = page.locator('#workspace_path');
  const groupPath = page.locator('#path');
  const sandboxRoots = page.locator('#sandbox_roots');
  await expect(workspacePath).toHaveValue(/tests[\\/]ui[\\/]\.runtime[\\/]current[\\/]workspaces[\\/]newsletter$/);
  await expect(groupPath).toHaveValue(/tests[\\/]ui[\\/]\.runtime[\\/]current[\\/]groups[\\/]newsletter$/);
  await expect(sandboxRoots).toHaveValue(/tests[\\/]ui[\\/]\.runtime[\\/]current[\\/]workspaces[\\/]newsletter$/);
  await assertNoLayoutIssues(page);
  // These fields hold absolute paths that vary per checkout, so compare them as text only.
  await expect(page).toHaveScreenshot('group-settings.png', {
    fullPage: true,
    mask: [workspacePath, groupPath, sandboxRoots],
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
  await expect(page.getByRole('heading', { name: 'Group default' })).toBeVisible();
  const inheritedRoot = page.getByText(/Group default: .*tests\/ui\/\.runtime\/current\/workspaces\/newsletter$/);
  await expect(inheritedRoot).toHaveCount(2);
  await expect(inheritedRoot.first()).toBeVisible();
  const groupRoot = page.getByText(/Agent addition: .*tests\/ui\/\.runtime\/current\/groups\/newsletter$/);
  await expect(groupRoot).toHaveCount(1);
  await expect(groupRoot).toBeVisible();
  const additionalRoot = page.getByText(/Agent addition: .*tests\/ui\/\.runtime\/current\/groups\/newsletter\/editorial$/);
  await expect(additionalRoot).toHaveCount(1);
  await expect(additionalRoot).toBeVisible();
  await expect(page.locator('body')).not.toContainText(/\.runtime\/run-\d+/);
  await expect(page.getByText('Timeout: 2400s', { exact: true })).toBeVisible();
  await expect(page.getByText('Timeout: 1200s', { exact: true })).toBeVisible();
  await expect(page.getByText('Tools: allowlist (shell, write)', { exact: true })).toBeVisible();
  await expect(page.getByText('Tools: allowlist (shell)', { exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Pinned integration' }).locator('..')).toContainText('Copilot');
  await expect(page.getByRole('heading', { name: 'Pinned integration' }).locator('..')).toContainText('copilot');
  await expect(page.getByRole('heading', { name: 'Effective preview' })).toBeVisible();
  await assertNoLayoutIssues(page);
  // Sandbox roots are absolute and vary per checkout, so compare them as text only.
  await expect(page).toHaveScreenshot('agent-runtime.png', {
    fullPage: true,
    mask: [inheritedRoot, groupRoot, additionalRoot],
  });
  await assertNoConsoleErrors(page);
});

test('all agent detail tabs have stable selected semantics and keyboard focus', async ({ page }) => {
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
  await tabTo(page, { role: 'textbox', name: 'Target group' });
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