import { expect, test } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { assertNoLayoutIssues } from './layout';

test('logs preserve readable output without active log markup', async ({ page }, testInfo) => {
  const logs = path.resolve('tests/ui/.runtime/current/teams/newsletter/logs/2026-07-16');
  await mkdir(logs, { recursive: true });
  const unsafe = '<script>window.logExecuted=1</script><img src="/log-resource" onerror="window.logExecuted=1">';
  const cases = [
    { name: 'markdown.out', content: '# Readable\n\n**Restored**\n\n' + unsafe, truncated: false },
    { name: 'events.out', content: JSON.stringify({ type: 'tool.execution_start', data: { arguments: 'bad' } }) + '\n' + JSON.stringify({ type: 'assistant.message', data: { content: '# Readable\n\n**Restored**\n\n' + unsafe } }), truncated: false },
    { name: 'long.out', content: '# Readable\n\n' + 'Preview content\n'.repeat(10000), truncated: true },
    { name: 'stderr.err', content: unsafe, truncated: false },
  ];
  await page.addInitScript(() => { (window as Window & { logExecuted?: number }).logExecuted = 0; });
  const injectedRequests: string[] = [];
  page.on('request', request => {
    if (request.url().includes('/log-resource')) injectedRequests.push(request.url());
  });
  for (const sample of cases) {
    const filePath = path.join(logs, `log-view-${testInfo.project.name}-${sample.name}`);
    await writeFile(filePath, sample.content, 'utf8');
    await page.goto('/newsletter/logs/view?path=' + encodeURIComponent(filePath));
    const content = page.locator('[data-log-content]');
    await expect(content).toBeVisible();
    await expect(content.locator('script, img, iframe, form, style')).toHaveCount(0);
    expect(await page.evaluate(() => (window as Window & { logExecuted?: number }).logExecuted)).toBe(0);
    expect(injectedRequests).toEqual([]);
    await expect(page.getByRole('status')).toHaveCount(sample.truncated ? 1 : 0);
    if (sample.name !== 'stderr.err') await expect(content.locator('h1')).toHaveText('Readable');
    if (sample.name === 'events.out' || sample.name === 'markdown.out') {
      await expect(content.locator('strong')).toHaveText('Restored');
    }
    await assertNoLayoutIssues(page);
    await page.screenshot({ path: testInfo.outputPath(sample.name + '.png'), fullPage: false });
  }
});
