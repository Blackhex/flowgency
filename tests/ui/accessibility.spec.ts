import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';

const pages = [
  { name: 'Group Settings', path: '/admin/orgs/newsletter/edit', identity: ['heading', 'Edit: Newsletter'] },
  { name: 'Agent roster', path: '/newsletter/agents', identity: ['heading', 'Instances assigned to Newsletter'] },
  { name: 'Agent Profile', path: '/newsletter/agents/advisor/profile', identity: ['tab', 'Profile'] },
  { name: 'Agent Blueprint', path: '/newsletter/agents/advisor/blueprint', identity: ['tab', 'Blueprint'] },
  { name: 'Agent Runtime', path: '/newsletter/agents/advisor/runtime', identity: ['tab', 'Runtime'] },
  { name: 'Agent Routines', path: '/newsletter/agents/advisor/routines', identity: ['tab', 'Routines'] },
  { name: 'Agent Memory', path: '/newsletter/agents/advisor/memory', identity: ['tab', 'Memory'] },
  { name: 'Agent Activity', path: '/newsletter/agents/advisor/activity', identity: ['tab', 'Activity'] },
  { name: 'Dashboard', path: '/newsletter/', identity: ['text', 'How the pipeline works'] },
  { name: 'Agent Library', path: '/admin/agent-library', identity: ['heading', 'Agent Library'] },
  { name: 'Memory Channels', path: '/admin/memory-channels', identity: ['heading', 'Memory Channels'] },
  { name: 'Memory Channel detail', path: '/admin/memory-channels/brand-strategy', identity: ['heading', 'Brand Strategy'] },
  { name: 'Jobs', path: '/newsletter/jobs', identity: ['heading', 'Jobs in Newsletter'] },
  { name: 'Waiting job', path: '/newsletter/jobs/job-waiting', identity: ['text', 'Memory: Channel: Brand Strategy'] },
  { name: 'Failed job', path: '/newsletter/jobs/job-failed', identity: ['link', 'Failed memory snapshot'] },
] as const;

function identityLocator(page: Page, identity: (typeof pages)[number]['identity']) {
  const [kind, name] = identity;
  if (kind === 'heading') return page.getByRole('heading', { name, exact: true });
  if (kind === 'tab') return page.getByRole('tab', { name, exact: true });
  if (kind === 'link') return page.getByRole('link', { name, exact: true });
  return page.getByText(name, { exact: true });
}

test.beforeEach(async ({ page }, testInfo) => {
  await page.addInitScript((theme) => {
    if (!localStorage.getItem('theme')) localStorage.setItem('theme', theme);
  }, testInfo.project.name.endsWith('dark') ? 'dark' : 'light');
});

for (const { name, path, identity } of pages) {
  test(`${name} has no WCAG A or AA violations`, async ({ page }) => {
    await page.goto(path);
    await expect(page).toHaveURL(path);
    const landmark = identityLocator(page, identity);
    await expect(landmark).toBeVisible();
    if (identity[0] === 'tab') await expect(landmark).toHaveAttribute('aria-current', 'page');
    const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze();
    expect(results.violations, JSON.stringify(results.violations, null, 2)).toEqual([]);
  });
}