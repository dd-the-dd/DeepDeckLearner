import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import AppErrorBoundary from './AppErrorBoundary';
import * as api from './api';
vi.mock('./api', () => ({ reportLocalRenderFailure: vi.fn() }));
const reference = '14f3d891-538b-455e-8183-97e82a85e4a5';
beforeEach(() => { vi.clearAllMocks(); vi.mocked(api.reportLocalRenderFailure).mockResolvedValue({ reference, recorded: true }); });

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

test('normal content remains untouched', () => {
  render(<AppErrorBoundary><button>Play a match</button></AppErrorBoundary>);
  expect(screen.getByRole('button', { name: 'Play a match' })).toBeVisible();
  expect(screen.queryByRole('alert')).toBeNull();
});

test('a render failure records only a local marker, not private exception text or player actions', async () => {
  vi.spyOn(console, 'error').mockImplementation(() => {});
  const fetch = vi.spyOn(globalThis, 'fetch');
  function Broken(): never { throw new Error('PRIVATE_PROMPT_AND_API_KEY'); }
  render(<AppErrorBoundary><Broken /></AppErrorBoundary>);
  expect(screen.getByRole('heading', { name: /back to the table/ })).toBeVisible();
  expect(screen.getByRole('alert').textContent).not.toContain('PRIVATE_');
  expect(screen.getByText(/Unsaved edits/)).toBeVisible();
  expect(screen.getByRole('link', { name: 'DeepDeck support' })).toHaveAttribute('href', 'https://www.deepdeckleague.com/support');
  expect(await screen.findByText(reference)).toBeVisible();
  expect(api.reportLocalRenderFailure).toHaveBeenCalledExactlyOnceWith();
  expect(fetch).not.toHaveBeenCalled();
});

test('only an explicit retry remounts the display; it does not call game APIs', async () => {
  vi.spyOn(console, 'error').mockImplementation(() => {});
  const fetch = vi.spyOn(globalThis, 'fetch');
  let broken = true;
  function Screen() { if (broken) throw new Error('private detail'); return <p>App ready again</p>; }
  render(<AppErrorBoundary><Screen /></AppErrorBoundary>);
  await screen.findByText(reference);
  broken = false;
  fireEvent.click(screen.getByRole('button', { name: 'Try displaying the app again' }));
  expect(screen.getByText('App ready again')).toBeVisible();
  expect(fetch).not.toHaveBeenCalled();
});

test('a failed optional local marker cannot prevent recovery or expose its exception', async () => {
  vi.spyOn(console, 'error').mockImplementation(() => {});
  vi.mocked(api.reportLocalRenderFailure).mockRejectedValue(new Error('PRIVATE_STORAGE_DETAIL'));
  function Broken(): never { throw new Error('PRIVATE_RENDER_DETAIL'); }
  render(<AppErrorBoundary><Broken /></AppErrorBoundary>);
  expect(await screen.findByRole('heading', { name: /back to the table/ })).toBeVisible();
  expect(screen.getByRole('button', { name: 'Try displaying the app again' })).toBeVisible();
  expect(document.body.textContent).not.toContain('PRIVATE_');
});
