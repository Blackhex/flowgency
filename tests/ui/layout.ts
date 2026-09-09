import { readFileSync } from 'fs';
import { resolve } from 'path';
import { expect, type Page } from '@playwright/test';

type LayoutIssue = {
  type: 'clipped' | 'overlap' | 'viewport';
  first: string;
  second?: string;
};

const pageErrors = new WeakMap<Page, string[]>();

const FONT_DIR = resolve(__dirname, '../../node_modules');
const FONT_BUFFERS: Record<string, Buffer> = {
  '/test/dm-sans/normal.woff2': readFileSync(resolve(FONT_DIR, '@fontsource-variable/dm-sans/files/dm-sans-latin-wght-normal.woff2')),
  '/test/dm-sans/italic.woff2': readFileSync(resolve(FONT_DIR, '@fontsource-variable/dm-sans/files/dm-sans-latin-wght-italic.woff2')),
  '/test/jetbrains-mono/normal.woff2': readFileSync(resolve(FONT_DIR, '@fontsource-variable/jetbrains-mono/files/jetbrains-mono-latin-wght-normal.woff2')),
};

const deterministicFontStylesheet = `
@font-face {
  font-family: 'DM Sans';
  font-style: normal;
  font-weight: 100 900;
  font-display: swap;
  src: url(https://fonts.gstatic.com/test/dm-sans/normal.woff2) format('woff2');
  unicode-range: U+0000-00FF;
}
@font-face {
  font-family: 'DM Sans';
  font-style: italic;
  font-weight: 100 900;
  font-display: swap;
  src: url(https://fonts.gstatic.com/test/dm-sans/italic.woff2) format('woff2');
  unicode-range: U+0000-00FF;
}
@font-face {
  font-family: 'JetBrains Mono';
  font-style: normal;
  font-weight: 100 800;
  font-display: swap;
  src: url(https://fonts.gstatic.com/test/jetbrains-mono/normal.woff2) format('woff2');
  unicode-range: U+0000-00FF;
}
`;

export function installConsoleErrorGate(page: Page): void {
  const errors: string[] = [];
  pageErrors.set(page, errors);
  page.on('console', (message) => {
    if (message.type() === 'error') {
      errors.push(`console: ${message.text()}`);
    }
  });
  page.on('pageerror', (error) => {
    errors.push(`pageerror: ${error.message}`);
  });
}

export async function installDeterministicFontResponses(page: Page): Promise<void> {
  await page.route('https://fonts.googleapis.com/**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'text/css; charset=utf-8',
      body: deterministicFontStylesheet,
    });
  });
  await page.route('https://fonts.gstatic.com/**', async (route) => {
    const pathname = new URL(route.request().url()).pathname;
    const buffer = FONT_BUFFERS[pathname];
    if (buffer) {
      await route.fulfill({ status: 200, contentType: 'font/woff2', body: buffer });
    } else {
      await route.fulfill({ status: 204, body: '' });
    }
  });
}

export async function installBasePageSetup(page: Page, theme: 'light' | 'dark'): Promise<void> {
  installConsoleErrorGate(page);
  await installDeterministicFontResponses(page);
  await page.addInitScript((initialTheme) => {
    if (!localStorage.getItem('theme')) localStorage.setItem('theme', initialTheme);
  }, theme);
}

export async function assertNoConsoleErrors(page: Page): Promise<void> {
  expect(pageErrors.get(page) ?? []).toEqual([]);
}

export async function assertNoLayoutIssues(page: Page): Promise<void> {
  const issues = await page.evaluate<LayoutIssue[]>(() => {
    const results: LayoutIssue[] = [];
    const root = document.documentElement;
    if (root.scrollWidth > root.clientWidth + 1) {
      results.push({ type: 'viewport', first: `${root.scrollWidth}px content in ${root.clientWidth}px viewport` });
    }

    const selector = 'h1, h2, h3, a, button, [role="tab"], input, select, textarea';
    const visible = (element: HTMLElement) => {
      const style = getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
    };
    const label = (element: HTMLElement) => (
      element.getAttribute('aria-label') || element.textContent || element.getAttribute('name') || element.tagName
    ).replace(/\s+/g, ' ').trim().slice(0, 100);

    const elements = Array.from(document.querySelectorAll<HTMLElement>(`main :is(${selector})`)).filter(visible);
    for (const element of elements) {
      if (element.closest('.overflow-x-auto, .overflow-y-auto, [data-allow-scroll]')) continue;
      const rect = element.getBoundingClientRect();
      const fixedControl = element.matches('button, input, select, textarea, [role="tab"]');
      const horizontallyClipped = element.scrollWidth > element.clientWidth + 1;
      const verticallyClipped = fixedControl && element.scrollHeight > element.clientHeight + 1;
      const outsideViewport = rect.left < -1 || rect.right > root.clientWidth + 1;
      if (horizontallyClipped || verticallyClipped || outsideViewport) {
        results.push({ type: 'clipped', first: label(element) });
      }
    }

    const regions = Array.from(document.querySelectorAll<HTMLElement>(
      'main > div, main section, main [role="tablist"], main .rounded-xl, main .rounded-lg',
    )).filter(visible);
    for (const region of regions) {
      const peers = Array.from(region.querySelectorAll<HTMLElement>('a, button, [role="tab"], h1, h2, h3')).filter(visible);
      for (let index = 0; index < peers.length; index += 1) {
        for (let other = index + 1; other < peers.length; other += 1) {
          const first = peers[index];
          const second = peers[other];
          if (first.contains(second) || second.contains(first)) continue;
          const a = first.getBoundingClientRect();
          const b = second.getBoundingClientRect();
          const overlapWidth = Math.min(a.right, b.right) - Math.max(a.left, b.left);
          const overlapHeight = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
          if (overlapWidth > 1 && overlapHeight > 1) {
            results.push({ type: 'overlap', first: label(first), second: label(second) });
          }
        }
      }
    }
    return results;
  });
  expect(issues).toEqual([]);
}

export async function assertFontFacesLoaded(page: Page): Promise<void> {
  const loaded = await page.evaluate(async () => {
    await document.fonts.ready;
    const dmSans = await document.fonts.load('400 1em "DM Sans"');
    const jbMono = await document.fonts.load('400 1em "JetBrains Mono"');
    return [
      { family: 'DM Sans', count: dmSans.length },
      { family: 'JetBrains Mono', count: jbMono.length },
    ];
  });
  for (const { family, count } of loaded) {
    expect(count, `Expected loaded FontFace for "${family}"`).toBeGreaterThan(0);
  }
}

export const expectLayoutIntegrity = assertNoLayoutIssues;