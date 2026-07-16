import { expect, test, type Locator, type Page } from '@playwright/test';

const tabs = ['Profile', 'Blueprint', 'Runtime', 'Routines', 'Memory', 'Activity'];

test.beforeEach(async ({ page }, testInfo) => {
  const dark = testInfo.project.name.endsWith('dark');
  await page.addInitScript((theme) => localStorage.setItem('theme', theme), dark ? 'dark' : 'light');
  await page.addStyleTag({ content: '* { animation: none !important; transition: none !important; caret-color: transparent !important; }' });
});

async function expectNoViewportOverflow(page: Page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(1);
}

async function expectNoOverlap(items: Locator) {
  const boxes = (await items.evaluateAll((nodes) => nodes.map((node) => {
    const box = node.getBoundingClientRect();
    return { left: box.left, right: box.right, top: box.top, bottom: box.bottom, text: node.textContent?.trim() };
  }))).filter((box) => box.right > box.left && box.bottom > box.top);
  for (let index = 0; index < boxes.length; index += 1) {
    for (let other = index + 1; other < boxes.length; other += 1) {
      const intersects = boxes[index].left < boxes[other].right && boxes[index].right > boxes[other].left
        && boxes[index].top < boxes[other].bottom && boxes[index].bottom > boxes[other].top;
      expect(intersects, `${boxes[index].text} overlaps ${boxes[other].text}`).toBeFalsy();
    }
  }
}

test('group settings leads to the sole roster and inherited runtime', async ({ page }) => {
  await page.goto('/admin/orgs/newsletter/edit');
  await expect(page.getByRole('heading', { name: 'Edit: Newsletter' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Create Instance' })).toHaveCount(0);
  await expect(page.getByText('Advisor')).toHaveCount(0);
  await expect(page).toHaveScreenshot('group-settings.png', { fullPage: true });

  await page.getByRole('link', { name: /Manage agents/ }).press('Enter');
  await expect(page).toHaveURL(/\/newsletter\/agents$/);
  await expect(page.getByText('Blueprint: advisor')).toBeVisible();
  await expect(page.getByText('waiting for memory')).toBeVisible();
  await expectNoOverlap(page.locator('main a, main button').filter({ visible: true }));
  await expectNoViewportOverflow(page);
  await expect(page).toHaveScreenshot('agent-roster.png', { fullPage: true });

  await page.getByRole('link', { name: 'Configure', exact: true }).first().press('Enter');
  await page.getByRole('tab', { name: 'Runtime' }).press('Enter');
  await expect(page).toHaveURL(/\/newsletter\/agents\/advisor\/runtime$/);
  await expect(page.getByRole('heading', { name: 'Group default' })).toBeVisible();
  await expect(page.getByText('Agent addition:', { exact: false })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Effective preview' })).toBeVisible();
  await expectNoViewportOverflow(page);
  await expect(page).toHaveScreenshot('agent-runtime.png', { fullPage: true });
});

test('all agent detail tabs have stable selected semantics and keyboard focus', async ({ page }) => {
  await page.goto('/newsletter/agents/advisor/profile');
  for (const tab of tabs) {
    const link = page.getByRole('tab', { name: tab });
    await link.focus();
    await expect(link).toBeFocused();
    await link.press('Enter');
    await expect(page).toHaveURL(new RegExp(`/newsletter/agents/advisor/${tab.toLowerCase()}$`));
    await expect(page.getByRole('tab', { name: tab })).toHaveAttribute('aria-current', 'page');
    await expectNoOverlap(page.getByRole('tab'));
    await expectNoViewportOverflow(page);
  }
});

test('library instructions and skill targets are canonical', async ({ page }) => {
  await page.goto('/admin/agent-library');
  await expect(page.getByRole('heading', { name: 'Agent Library' })).toBeVisible();
  await expect(page).toHaveScreenshot('agent-library.png', { fullPage: true });
  await page.getByRole('link', { name: /Advisor/ }).press('Enter');
  await expect(page.getByRole('heading', { name: 'AGENTS.md' })).toBeVisible();
  await page.getByRole('link', { name: /daily-review/ }).first().press('Enter');
  await expect(page.getByRole('heading', { name: 'SKILL.md' })).toBeVisible();
});

test('memory channel uses a friendly label without normal hash disclosure', async ({ page }) => {
  await page.goto('/admin/memory-channels');
  await page.getByRole('link', { name: 'brand-strategy' }).press('Enter');
  await expect(page.getByRole('heading', { name: 'Brand Strategy' })).toBeVisible();
  await expect(page.getByText('Internal hash')).toHaveCount(0);
  await expect(page.locator('body')).not.toContainText('22222222222222222222222222222222');
  await expectNoViewportOverflow(page);
  await expect(page).toHaveScreenshot('memory-channel.png', { fullPage: true });
});

test('destructive controls require confirmation or a review page and are keyboard reachable', async ({ page }) => {
  await page.goto('/newsletter/agents');
  const move = page.getByRole('button', { name: 'Move' }).first();
  await move.focus();
  await expect(move).toBeFocused();
  await page.getByPlaceholder('target group').first().fill('newsletter');
  await move.press('Enter');
  await expect(page.getByRole('heading', { name: /Move advisor/ })).toBeVisible();

  await page.goto('/newsletter/agents');
  const remove = page.getByRole('button', { name: 'Remove' }).first();
  await expect(remove.locator('xpath=ancestor::form//input[@name="confirm"]')).toHaveValue('true');
  await remove.focus();
  await expect(remove).toBeFocused();

  await page.goto('/admin/memory-channels');
  await page.getByLabel('Key').focus();
  await expect(page.getByLabel('Key')).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(page.getByLabel('Display name')).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(page.getByRole('button', { name: 'Create channel' })).toBeFocused();
});