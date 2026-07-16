import { expect, type Page } from '@playwright/test';

type LayoutIssue = {
  type: 'clipped' | 'overlap' | 'viewport';
  first: string;
  second?: string;
};

export async function expectLayoutIntegrity(page: Page): Promise<void> {
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