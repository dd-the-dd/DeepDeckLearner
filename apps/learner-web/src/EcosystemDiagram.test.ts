import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, expect, test, vi } from 'vitest';

const html = readFileSync(resolve(process.cwd(), 'public/assets/deepdeck-ecosystem.html'), 'utf8');

test('the standalone French diagram links every component and separates privacy boundaries', () => {
  const page = new DOMParser().parseFromString(html, 'text/html');
  expect(page.documentElement.lang).toBe('fr');
  expect(page.querySelectorAll('.component')).toHaveLength(6);
  for (const id of ['website', 'engine', 'parser', 'learner', 'pixi', 'sdk', 'privacy']) {
    expect(page.getElementById(id)).not.toBeNull();
  }
  for (const anchor of page.querySelectorAll<HTMLAnchorElement>('a[href^="#"]')) {
    expect(page.getElementById(anchor.getAttribute('href')!.slice(1))).not.toBeNull();
  }
  for (const repository of ['DeepDeckEngine', 'DeepDeckLearner', 'DeepDeckPixi', 'DeepDeckAgent']) {
    expect(page.querySelector(`a[href="https://github.com/dd-the-dd/${repository}"]`)).not.toBeNull();
  }
  expect(page.querySelector('.privacy')?.textContent).toContain('Ignoré par Git');
  expect(page.querySelector('.privacy')?.textContent).toContain('poids de modèles privés');
  expect(page.querySelector('#parser')?.textContent).toContain('partie du dépôt Engine');
});

test('the flow graph has directed, bidirectional and optional connections, not just component cards', () => {
  const page = new DOMParser().parseFromString(html, 'text/html');
  expect(page.querySelectorAll('.ecosystem-flow')).toHaveLength(1);
  expect(page.querySelector('.ecosystem-flow desc')?.textContent).toContain('Le parser, inclus dans l’Engine');
  for (const [from, to] of [['learner','agent'], ['agent','sdk'], ['sdk','engine'], ['parser','engine'], ['engine','pixi'], ['league','engine']]) {
    expect(page.querySelector(`path[data-from="${from}"][data-to="${to}"]`)).not.toBeNull();
  }
  expect(page.querySelectorAll('path.wire.both')).toHaveLength(3);
  expect(page.querySelector('path.wire.sync')?.getAttribute('data-to')).toBe('website');
  expect(page.querySelector('path[data-from="sdk"][data-to="league"]')).not.toBeNull();
  expect(page.querySelector('details#explanations')?.hasAttribute('open')).toBe(false);
  expect(page.querySelector('.diagram-scroll')?.getAttribute('tabindex')).toBe('0');
});

test('English diagram explains the same direct SDK to League connection',()=>{
  const english=readFileSync(resolve(process.cwd(),'public/assets/deepdeck-ecosystem-en.html'),'utf8');
  const page=new DOMParser().parseFromString(english,'text/html');
  expect(page.documentElement.lang).toBe('en');
  expect(page.querySelector('path[data-from="sdk"][data-to="league"]')).not.toBeNull();
  expect(page.querySelector('.sdk-callout')?.textContent).toContain('your connection to our League service');
  for(const a of page.querySelectorAll('a[href^="#"]')) expect(page.getElementById(a.getAttribute('href')!.slice(1))).not.toBeNull();
});

afterEach(() => {document.body.replaceChildren(); vi.restoreAllMocks();});

test('zoom, fit, print and component navigation work entirely offline', () => {
  const parsed = new DOMParser().parseFromString(html, 'text/html');
  document.body.innerHTML = parsed.body.innerHTML;
  const diagram = document.querySelector<SVGSVGElement>('.ecosystem-flow')!;
  vi.spyOn(diagram, 'getBoundingClientRect').mockReturnValue({width:1200} as DOMRect);
  const print = vi.spyOn(window, 'print').mockImplementation(()=>undefined);
  new Function(parsed.querySelector('script')!.textContent!)();
  document.querySelector<HTMLButtonElement>('#zoom-in')!.click();
  expect(diagram.style.width).toBe('1440px');
  document.querySelector<HTMLButtonElement>('#zoom-out')!.click();
  expect(diagram.style.width).toBe('1000px');
  document.querySelector<HTMLButtonElement>('#zoom-reset')!.click();
  expect(diagram.style.width).toBe('100%');
  expect(diagram.style.minWidth).toBe('0');
  document.querySelector<SVGElement>('.ecosystem-flow a[href="#sdk"]')!.dispatchEvent(new MouseEvent('click', {bubbles:true, cancelable:true}));
  expect(document.querySelector<HTMLDetailsElement>('#explanations')!.open).toBe(true);
  document.querySelector<HTMLButtonElement>('#print')!.click();
  expect(print).toHaveBeenCalledOnce();
});

test('the diagram needs no external assets, requests, framework or LLM', () => {
  const page = new DOMParser().parseFromString(html, 'text/html');
  expect(page.querySelectorAll('script[src],link[rel="stylesheet"],iframe,img,video,audio')).toHaveLength(0);
  expect(html).not.toMatch(/\bfetch\s*\(|XMLHttpRequest|@import|url\(https?:/);
  expect(page.querySelectorAll('script')).toHaveLength(1);
  expect(page.querySelector('script')?.textContent).toContain('window.print()');
});
