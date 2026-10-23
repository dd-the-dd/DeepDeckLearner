import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import TableLaunch, { mergeObservedJob } from './TableLaunch';
import * as api from './api';
import type { Job } from './api';

vi.mock('./api', async (original) => ({ ...await original<typeof import('./api')>(), loadJob: vi.fn(), stopJob: vi.fn() }));
const job: Job = { id: 'owned-job', kind: 'playtest.table', status: 'running', label: 'Your Legacy table', details: {}, logs: [], argv: [], created_at: '', started_at: null, finished_at: null, exit_code: null, artifact_path: null };
beforeEach(() => { vi.clearAllMocks(); vi.mocked(api.loadJob).mockResolvedValue(job); vi.mocked(api.stopJob).mockResolvedValue({ ...job, status: 'stopped' }); });
afterEach(() => { cleanup(); vi.useRealTimers(); });

test('preparation observes the existing job and delivers its real session as soon as ready', async () => {
  const ready = { ...job, details: { engineUrl: 'http://127.0.0.1:8897', sessionId: 'game-session:1' } };
  vi.mocked(api.loadJob).mockResolvedValue(ready);
  const update = vi.fn(); render(<TableLaunch job={job} onUpdate={update} onClose={vi.fn()} />);
  expect(screen.getByRole('heading')).toHaveTextContent('Opening your table');
  await waitFor(() => expect(update).toHaveBeenCalledWith(ready));
  expect(api.loadJob).toHaveBeenCalledWith(job.id);
  expect(api.stopJob).not.toHaveBeenCalled();
});

test('an observation failure reconnects to the same job instead of launching another table', async () => {
  vi.useFakeTimers(); vi.mocked(api.loadJob).mockRejectedValueOnce(new Error('Connection lost')).mockResolvedValueOnce(job);
  const update = vi.fn(); render(<TableLaunch job={job} onUpdate={update} onClose={vi.fn()} />);
  await act(async () => {});
  expect(screen.getByRole('alert')).toHaveTextContent('Reconnecting to your existing table');
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  expect(api.loadJob).toHaveBeenNthCalledWith(2, job.id);
  expect(update).toHaveBeenCalledWith(job);
  expect(screen.queryByRole('alert')).toBeNull();
});

test('cancelling waits for the owned job and ignores a late ready response', async () => {
  let ready!: (value: Job) => void; let stopped!: (value: Job) => void;
  vi.mocked(api.loadJob).mockReturnValue(new Promise((resolve) => { ready = resolve; }));
  vi.mocked(api.stopJob).mockReturnValue(new Promise((resolve) => { stopped = resolve; }));
  const update = vi.fn(); const close = vi.fn(); render(<TableLaunch job={job} onUpdate={update} onClose={close} />);
  fireEvent.click(screen.getByRole('button', { name: 'Cancel launch' }));
  expect(screen.getByRole('button', { name: 'Stopping & saving trace…' })).toBeDisabled();
  await act(async () => { ready({ ...job, details: { sessionId: 'late' } }); });
  expect(update).not.toHaveBeenCalled(); expect(close).not.toHaveBeenCalled();
  await act(async () => { stopped({ ...job, status: 'stopped' }); });
  expect(api.stopJob).toHaveBeenCalledWith(job.id); expect(close).toHaveBeenCalledOnce();
});

test('keeping preparation in the background does not stop a table', () => {
  const close = vi.fn(); render(<TableLaunch job={job} onUpdate={vi.fn()} onClose={close} />);
  fireEvent.click(screen.getByRole('button', { name: 'Keep preparing in background' }));
  expect(close).toHaveBeenCalledOnce(); expect(api.stopJob).not.toHaveBeenCalled();
});

test('failure is actionable, keeps technical details collapsed, and never restarts automatically', () => {
  const close = vi.fn(); render(<TableLaunch job={{ ...job, status: 'failed', logs: ['The Engine is unavailable.'] }} onUpdate={vi.fn()} onClose={close} />);
  expect(screen.getByRole('heading')).toHaveTextContent('could not start');
  expect(document.querySelector('details')).not.toHaveAttribute('open');
  expect(api.loadJob).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Return to app' })); expect(close).toHaveBeenCalledOnce();
});

test('stale job observations do not remove an acquired session or roll a finished job back', () => {
  const ready = { ...job, details: { sessionId: 'ready', engineUrl: 'http://127.0.0.1:8897' } };
  expect(mergeObservedJob(ready, job).details).toEqual(ready.details);
  expect(mergeObservedJob(ready, { ...job, status: 'queued' })).toBe(ready);
  const failed = { ...ready, status: 'failed' as const };
  expect(mergeObservedJob(failed, job)).toBe(failed);
  expect(mergeObservedJob(ready, failed).status).toBe('failed');
});

test('deck setup errors are readable without opening a traceback', () => {
  render(<TableLaunch job={{ ...job, status: 'failed', logs: ['Traceback (most recent call last):', 'ValueError: The Engine could not start this table: illegal game setup: Death and Taxes [legacy-sideboard-count]: Legacy permits at most 15 sideboard cards; found 16'] }} onUpdate={vi.fn()} onClose={vi.fn()} />);
  expect(screen.getByRole('alert')).toHaveTextContent('Death and Taxes · Legacy permits at most 15 sideboard cards; found 16');
  expect(document.querySelector('details')).not.toHaveAttribute('open');
});
