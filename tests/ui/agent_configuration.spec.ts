import { expect, test } from '@playwright/test';

import { expectBodyFocus, tabTo } from './keyboard';
import { expectLayoutIntegrity } from './layout';

const tabs = ['Profile', 'Blueprint', 'Runtime', 'Routines', 'Memory', 'Activity'];

test.beforeEach(async ({ page }, testInfo) => {
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
  await expectLayoutIntegrity(page);
  await expect(page).toHaveScreenshot('group-settings.png', { fullPage: true });

  await expectBodyFocus(page);
  await tabTo(page, { role: 'link', name: /Manage agents/, href: '/newsletter/agents' });
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/\/newsletter\/agents$/);
  await expect(page.getByText('Blueprint: advisor')).toBeVisible();
  await expect(page.getByText('waiting for memory')).toBeVisible();
  await expectLayoutIntegrity(page);
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
  const inheritedRoot = page.getByText(/Group default: .*tests\/ui\/\.runtime\/current\/groups\/newsletter\/shared$/);
  await expect(inheritedRoot).toHaveCount(2);
  await expect(inheritedRoot.first()).toBeVisible();
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
  await expectLayoutIntegrity(page);
  await expect(page).toHaveScreenshot('agent-runtime.png', { fullPage: true });
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
    await expectLayoutIntegrity(page);
  }
});

test('library instructions and skill targets are canonical', async ({ page }) => {
  await page.goto('/admin/agent-library');
  await expect(page.getByRole('heading', { name: 'Agent Library' })).toBeVisible();
  await expectLayoutIntegrity(page);
  await expect(page).toHaveScreenshot('agent-library.png', { fullPage: true });
  await page.getByRole('link', { name: /Advisor/ }).press('Enter');
  await expect(page.getByRole('heading', { name: 'AGENTS.md' })).toBeVisible();
  await page.getByRole('link', { name: /daily-review/ }).first().press('Enter');
  await expect(page.getByRole('heading', { name: 'SKILL.md' })).toBeVisible();
});

test('memory channel uses a friendly label without normal hash disclosure', async ({ page }) => {
  await page.goto('/admin/memory-channels');
  await expectLayoutIntegrity(page);
  await page.getByRole('link', { name: 'brand-strategy' }).press('Enter');
  await expect(page.getByRole('heading', { name: 'Brand Strategy' })).toBeVisible();
  await expect(page.getByText('Internal hash')).toHaveCount(0);
  await expect(page.locator('body')).not.toContainText('22222222222222222222222222222222');
  await expectLayoutIntegrity(page);
  await expect(page).toHaveScreenshot('memory-channel.png', { fullPage: true });
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
});