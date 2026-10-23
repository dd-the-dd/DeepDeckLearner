import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, expect, test, vi } from 'vitest';

const html = readFileSync(resolve(process.cwd(), 'public/assets/strategy-graphs.html'), 'utf8');
function open() {
  const parsed = new DOMParser().parseFromString(html, 'text/html');
  document.body.innerHTML = parsed.body.innerHTML;
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => { callback(0); return 0; });
  vi.spyOn(window, 'addEventListener').mockImplementation(() => undefined);
  const script = parsed.querySelector('script')?.textContent;
  expect(script).toBeTruthy();
  // Execute only our checked-in standalone document's script in the test DOM.
  new Function(script!)();
  return document;
}
afterEach(() => { document.body.replaceChildren(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

test('the standalone HTML starts and each graph node opens its explanation', () => {
  const page = open();
  expect(page.querySelectorAll('[data-node]')).toHaveLength(8);
  for (const node of page.querySelectorAll<HTMLButtonElement>('[data-node]')) {
    node.click(); expect(node.getAttribute('aria-pressed')).toBe('true');
    expect(page.querySelector('#detail-description')?.textContent?.length).toBeGreaterThan(20);
  }
});

test('all five decision graphs and all four tabs work without a backend', () => {
  const page = open();
  for (const button of page.querySelectorAll<HTMLButtonElement>('[data-tab]')) {
    button.click(); expect(page.getElementById(button.dataset.tab!)?.hasAttribute('hidden')).toBe(false);
  }
  for (const button of page.querySelectorAll<HTMLButtonElement>('[data-model]')) {
    button.click(); expect(button.getAttribute('aria-pressed')).toBe('true');
    expect(page.querySelectorAll('.decision-step')).toHaveLength(4);
  }
});

test('weight examples compute the selected archetype multiplier without modifying an agent', () => {
  const page = open();
  expect(page.querySelectorAll('#weight-rows tr')).toHaveLength(16);
  const multiplier = page.querySelector<HTMLInputElement>('#multiplier')!;
  multiplier.value = '2'; multiplier.dispatchEvent(new Event('input', { bubbles: true }));
  expect(page.querySelector('[data-node-id="combo_progress"] [data-effective]')?.textContent).toBe('3,8');
  expect(page.querySelector('#multiplier-output')?.textContent).toBe('2 ×');
});

test('the three stack scenarios distinguish our spell, a cantrip and an actual opposing counter', () => {
  const page = open();
  const expected = { own: 'Laisser notre sort résoudre.', cantrip: 'Conserver le dernier contre.', counterwar: 'Contrer la réponse adverse.' };
  for (const [scenario, text] of Object.entries(expected)) {
    page.querySelector<HTMLButtonElement>(`[data-scenario="${scenario}"]`)!.click();
    expect(page.querySelector('.scenario-result strong')?.textContent).toBe(text);
  }
  expect(page.querySelectorAll('#scenario-stack li')).toHaveLength(2);
});
