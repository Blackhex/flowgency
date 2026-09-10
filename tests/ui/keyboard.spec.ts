import { expect, test } from '@playwright/test';

import { expectBodyFocus, tabTo } from './keyboard';
import { installBasePageSetup } from './layout';

test.beforeEach(async ({ page }, testInfo) => {
  await installBasePageSetup(page, testInfo.project.name.endsWith('dark') ? 'dark' : 'light');
});

test('tabTo waits for a committed page to expose keyboard targets', async ({ page }) => {
  const delayedPage = [
    '<!doctype html>',
    '<html lang="en">',
    '<body>',
    '<script>',
    'setTimeout(() => {',
    '  const runtime = document.createElement("a");',
    '  runtime.href = "/runtime";',
    '  runtime.setAttribute("role", "tab");',
    '  runtime.textContent = "Runtime";',
    '  document.body.appendChild(runtime);',
    '}, 50);',
    '</script>',
    '</body>',
    '</html>',
  ].join('');

  await page.goto(`data:text/html,${encodeURIComponent(delayedPage)}`, { waitUntil: 'commit' });

  await expectBodyFocus(page);
  const runtime = await tabTo(page, { role: 'tab', name: 'Runtime', href: '/runtime' }, { maxTabs: 4 });
  await expect(runtime).toBeFocused();
});
