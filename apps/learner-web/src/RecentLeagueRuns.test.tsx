import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import type { Job } from './api';
import RecentLeagueRuns from './RecentLeagueRuns';

const storageKey = 'deepdeck.dismissedLeagueRuns.v1';
function run(id: string, status = 'failed'): Job {
  return { id, kind: 'matchmaking.agent', label: `${id} pilot`, status, logs: [],
    created_at: '2026-10-23T01:00:00Z',
    details: { leagueRun: { completedEntries: 2, activeEntries: 0, maxMatches: 3, durationSeconds: null,
      lastError: status === 'failed' ? `${id} server refusal` : null } },
  } as unknown as Job;
}

beforeEach(() => { window.localStorage.clear(); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

test('the close button hides only the selected finished run and keeps its job unchanged', () => {
  const failed = run('failed');
  const before = JSON.stringify(failed);
  render(<RecentLeagueRuns jobs={[failed, run('finished', 'completed')]} />);
  expect(screen.getByRole('alert')).toHaveTextContent('failed server refusal');
  fireEvent.click(screen.getByRole('button', { name: 'Dismiss League run for failed pilot' }));
  expect(screen.queryByText('failed pilot')).not.toBeInTheDocument();
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  expect(screen.getByText('finished pilot')).toBeVisible();
  expect(JSON.stringify(failed)).toBe(before);
  expect(JSON.parse(window.localStorage.getItem(storageKey)!)).toEqual(['failed']);
});

test('polling, page remounts and new runs do not bring a dismissed notification back', () => {
  const jobs = [run('old')];
  const page = render(<RecentLeagueRuns jobs={jobs} />);
  fireEvent.click(screen.getByRole('button', { name: 'Dismiss League run for old pilot' }));
  page.rerender(<RecentLeagueRuns jobs={[...jobs]} />);
  expect(screen.queryByText('old pilot')).not.toBeInTheDocument();
  page.unmount();
  render(<RecentLeagueRuns jobs={[run('new'), ...jobs]} />);
  expect(screen.queryByText('old pilot')).not.toBeInTheDocument();
  expect(screen.getByText('new pilot')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Show dismissed runs (1)' })).toBeVisible();
});

test('dismissed runs can be restored without losing diagnostics', () => {
  render(<RecentLeagueRuns jobs={[run('failed')]} />);
  fireEvent.click(screen.getByRole('button', { name: 'Dismiss League run for failed pilot' }));
  expect(screen.getByText(/Your history and replays are still saved/)).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Show dismissed runs (1)' }));
  expect(screen.getByText('failed pilot')).toBeVisible();
  expect(screen.getByRole('alert')).toHaveTextContent('failed server refusal');
  expect(JSON.parse(window.localStorage.getItem(storageKey)!)).toEqual([]);
});

test('the four-row limit applies after filtering dismissed notifications', () => {
  render(<RecentLeagueRuns jobs={['one', 'two', 'three', 'four', 'five'].map(id => run(id))} />);
  expect(screen.queryByText('five pilot')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Dismiss League run for one pilot' }));
  expect(screen.getByText('five pilot')).toBeVisible();
  expect(screen.getAllByRole('button', { name: /^Dismiss League run/ })).toHaveLength(4);
});

test('active workers and non-League jobs are never offered a notification close button', () => {
  const { container } = render(<RecentLeagueRuns jobs={[run('running', 'running'), run('queued', 'queued'), { ...run('batch'), kind: 'play.batch' }]} />);
  expect(container).toBeEmptyDOMElement();
});

test.each(['broken json', '{}', 'null', '[null,2,{},""]'])('invalid saved data is harmless: %s', value => {
  window.localStorage.setItem(storageKey, value);
  render(<RecentLeagueRuns jobs={[run('visible')]} />);
  expect(screen.getByText('visible pilot')).toBeVisible();
});

test('blocked browser storage still allows dismissal for the current visit', () => {
  vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('Storage blocked'); });
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('Storage full'); });
  render(<RecentLeagueRuns jobs={[run('failed')]} />);
  fireEvent.click(screen.getByRole('button', { name: 'Dismiss League run for failed pilot' }));
  expect(screen.queryByText('failed pilot')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Show dismissed runs (1)' }));
  expect(screen.getByText('failed pilot')).toBeVisible();
});

test('saved preferences are bounded and contain IDs, never error text or worker logs', () => {
  window.localStorage.setItem(storageKey, JSON.stringify(Array.from({ length: 200 }, (_, index) => `old-${index}`)));
  render(<RecentLeagueRuns jobs={[run('new')]} />);
  fireEvent.click(screen.getByRole('button', { name: 'Dismiss League run for new pilot' }));
  const saved = JSON.parse(window.localStorage.getItem(storageKey)!) as string[];
  expect(saved).toHaveLength(200);
  expect(saved.at(-1)).toBe('new');
  expect(saved).not.toContain('old-0');
  expect(JSON.stringify(saved)).not.toContain('server refusal');
});
