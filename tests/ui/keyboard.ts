import { expect, type Locator, type Page } from '@playwright/test';

export type FocusTarget = {
  role?: string;
  name?: string | RegExp;
  href?: string | RegExp;
};

type FocusState = {
  role: string;
  name: string;
  href: string | null;
};

function matches(actual: string | null, expected?: string | RegExp): boolean {
  if (expected === undefined) return true;
  if (actual === null) return false;
  return typeof expected === 'string' ? actual === expected : expected.test(actual);
}

export async function expectBodyFocus(page: Page): Promise<void> {
  await expect.poll(() => page.evaluate(() => document.activeElement === document.body)).toBe(true);
}

export async function tabTo(
  page: Page,
  target: FocusTarget,
  options: { backwards?: boolean; maxTabs?: number } = {},
): Promise<Locator> {
  const key = options.backwards ? 'Shift+Tab' : 'Tab';
  const visited: FocusState[] = [];
  for (let step = 0; step < (options.maxTabs ?? 80); step += 1) {
    await page.keyboard.press(key);
    const state = await page.evaluate<FocusState>(() => {
      const element = document.activeElement as HTMLElement | null;
      if (!element) return { role: '', name: '', href: null };
      const tag = element.tagName.toLowerCase();
      const inputType = element instanceof HTMLInputElement ? element.type : '';
      const implicitRoles: Record<string, string> = {
        a: 'link',
        button: 'button',
        select: 'combobox',
        textarea: 'textbox',
      };
      const role = element.getAttribute('role')
        || (tag === 'input' && ['button', 'submit', 'reset', 'checkbox', 'radio'].includes(inputType) ? inputType : '')
        || implicitRoles[tag]
        || (tag === 'input' ? 'textbox' : '');
      const labelledBy = element.getAttribute('aria-labelledby');
      const labelledText = labelledBy
        ? labelledBy.split(/\s+/).map((id) => document.getElementById(id)?.textContent ?? '').join(' ')
        : '';
      const associatedLabel = 'labels' in element
        ? Array.from((element as HTMLInputElement).labels ?? []).map((label) => label.textContent ?? '').join(' ')
        : '';
      const name = element.getAttribute('aria-label')
        || labelledText.trim()
        || associatedLabel.trim()
        || element.textContent?.trim()
        || element.getAttribute('placeholder')
        || '';
      return {
        role,
        name: name.replace(/\s+/g, ' ').trim(),
        href: element instanceof HTMLAnchorElement ? element.getAttribute('href') : null,
      };
    });
    visited.push(state);
    if ((!target.role || state.role === target.role) && matches(state.name, target.name) && matches(state.href, target.href)) {
      const marker = `keyboard-target-${Date.now()}-${step}`;
      await page.evaluate((value) => document.activeElement?.setAttribute('data-keyboard-target', value), marker);
      return page.locator(`[data-keyboard-target="${marker}"]`);
    }
  }
  throw new Error(`Keyboard target not reached: ${JSON.stringify(target)}; visited ${JSON.stringify(visited)}`);
}