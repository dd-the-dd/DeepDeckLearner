import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import LocalDiagnostics from './LocalDiagnostics';
import * as api from './api';
vi.mock('./api', () => ({ loadLocalDiagnostics: vi.fn() }));

const report: api.LocalDiagnosticReport = { localOnly: true, storedEntries: 1, usedBytes: 12288,
  limits: { maxEntries: 2000, databaseBytes: 8388608, retentionDays: 14 },
  items: [{ reference: '14f3d891-538b-455e-8183-97e82a85e4a5', createdAtUnix: 1000000000, method: 'POST', route: '/api/v1/jobs', status: 500, durationMs: 30, category: 'service_error' }] };
beforeEach(() => { vi.clearAllMocks(); vi.mocked(api.loadLocalDiagnostics).mockResolvedValue(report); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
function open() { fireEvent.click(screen.getByText('Local diagnostics · private support journal')); }

test('the optional journal does not fetch or upload when Settings renders or the section opens', () => {
  render(<LocalDiagnostics />); open();
  expect(api.loadLocalDiagnostics).not.toHaveBeenCalled();
  expect(screen.getByText(/No API keys/)).toBeVisible();
});
test('an explicit read shows references, and copying happens only on explicit request', async () => {
  const writeText = vi.fn().mockResolvedValue(undefined); vi.stubGlobal('navigator', { clipboard: { writeText } });
  render(<LocalDiagnostics />); open();
  fireEvent.click(screen.getByRole('button', { name: 'Read local journal' }));
  expect(await screen.findByText(/1 stored/)).toBeVisible();
  expect(api.loadLocalDiagnostics).toHaveBeenCalledWith(true);
  expect(writeText).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Copy diagnostic metadata' }));
  expect(await screen.findByRole('status')).toHaveTextContent(/nothing was uploaded/);
  expect(JSON.parse(writeText.mock.calls[0][0])).toEqual(report);
});
test('changing the filter does not send a request and clears the old report until explicitly read', async () => {
  render(<LocalDiagnostics />); open();
  fireEvent.click(screen.getByRole('button', { name: 'Read local journal' }));
  await screen.findByText(/1 stored/);
  fireEvent.click(screen.getByRole('checkbox'));
  expect(screen.queryByText(/1 stored/)).toBeNull();
  expect(api.loadLocalDiagnostics).toHaveBeenCalledOnce();
  fireEvent.click(screen.getByRole('button', { name: 'Read local journal' }));
  await screen.findByText(/1 stored/);
  expect(api.loadLocalDiagnostics).toHaveBeenLastCalledWith(false);
});
test('a private storage failure stays generic and does not leave stale diagnostic contents', async () => {
  vi.mocked(api.loadLocalDiagnostics).mockRejectedValue(new Error('PRIVATE_KEY_DETAILS'));
  render(<LocalDiagnostics />); open();
  fireEvent.click(screen.getByRole('button', { name: 'Read local journal' }));
  expect(await screen.findByRole('status')).toHaveTextContent(/could not be loaded/);
  expect(document.body.textContent).not.toContain('PRIVATE_KEY');
});
