import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';

const pages = [
  ['Group Settings', '/admin/orgs/newsletter/edit'],
  ['Agent roster', '/newsletter/agents'],
  ['Agent Profile', '/newsletter/agents/advisor/profile'],
  ['Agent Blueprint', '/newsletter/agents/advisor/blueprint'],
  ['Agent Runtime', '/newsletter/agents/advisor/runtime'],
  ['Agent Routines', '/newsletter/agents/advisor/routines'],
  ['Agent Memory', '/newsletter/agents/advisor/memory'],
  ['Agent Activity', '/newsletter/agents/advisor/activity'],
  ['Dashboard', '/newsletter/'],
  ['Agent Library', '/admin/agent-library'],
  ['Memory Channels', '/admin/memory-channels'],
  ['Memory Channel detail', '/admin/memory-channels/brand-strategy'],
  ['Jobs', '/newsletter/jobs'],
  ['Waiting job', '/newsletter/jobs/job-waiting'],
  ['Failed job', '/newsletter/jobs/job-failed'],
] as const;

test.beforeEach(async ({ page }, testInfo) => {
  await page.addInitScript((theme) => localStorage.setItem('theme', theme), testInfo.project.name.endsWith('dark') ? 'dark' : 'light');
});

for (const [name, path] of pages) {
  test(`${name} has no WCAG A or AA violations`, async ({ page }) => {
    await page.goto(path);
    await expect(page.locator('main')).not.toBeEmpty();
    const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze();
    expect(results.violations, JSON.stringify(results.violations, null, 2)).toEqual([]);
  });
}