import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import LiveMatchViewer from './LiveMatchViewer';
import * as api from './api';
import type { Job } from './api';
const rendererState = vi.hoisted(() => ({fail: false}));

vi.mock('./api', async (original) => ({ ...await original<typeof import('./api')>(), loadJob: vi.fn(), stopJob: vi.fn() }));
vi.mock('./LocalPixiRenderer', () => ({ default: ({ view, mode, onAction, playbackControls }: { view: { sessionId: string; revision:number; decision: unknown }; mode: string; onAction: (id: string) => void; playbackControls?: import('react').ReactNode }) => {
  if (rendererState.fail) throw new Error('Fixture projection failure');
  return <div>{playbackControls}<span>Table {view.sessionId}</span><span>Frame {view.revision}</span><span>Decision {view.decision === null ? 'removed' : 'actionable'}</span><span>Mode {mode}</span><button type="button" onClick={() => onAction('not-allowed')}>Simulated action</button></div>;
} }));
const job = { id: 'live-batch', kind: 'playtest.batch', label: 'AI batch', status: 'running', details: { sessionId: 'game-session:1', sessionStatus: 'playing', currentMatchIndex: 1, completed: 0, total: 3, engineUrl: 'http://127.0.0.1:8787', deckNames: ['Tempo', 'Combo'] } } as Job;

beforeEach(() => {
  vi.clearAllMocks();
  rendererState.fail = false;
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', { configurable: true, value: function (this: HTMLDialogElement) { this.open = true; } });
  vi.mocked(api.loadJob).mockResolvedValue(job);
  vi.stubGlobal('fetch', vi.fn(async (url: string) => ({ ok: true, status: 200, json: async () => ({ sessionId: url.split('/').at(-1), revision: 1, decision: { id: 'ai-decision', options: [{ id: 'cast' }] }, state: { turnNumber: 1, players: [] } }) })));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); vi.restoreAllMocks(); });

test('a Pixi failure stays inside the live viewer; retry and back never restart gameplay', async () => {
  vi.spyOn(console, 'error').mockImplementation(() => {});
  rendererState.fail = true;
  const close = vi.fn();
  render(<LiveMatchViewer initialJob={job} onClose={close} />);
  expect(await screen.findByRole('button', {name:'Retry live display'})).toBeVisible();
  expect(screen.getByRole('button', {name:/Back to app/})).toBeVisible();
  rendererState.fail = false;
  fireEvent.click(screen.getByRole('button', {name:'Retry live display'}));
  expect(await screen.findByText('Table game-session:1')).toBeVisible();
  fireEvent.click(screen.getByRole('button', {name:/Back to app/}));
  expect(close).toHaveBeenCalledOnce();
  expect(api.stopJob).not.toHaveBeenCalled();
  expect(vi.mocked(fetch).mock.calls.every(call => !call[1]?.method || call[1].method === 'GET')).toBe(true);
});

test('live viewer is read-only and leaving never stops or deletes a match', async () => {
  const close = vi.fn();
  render(<LiveMatchViewer initialJob={job} onClose={close} />);
  await screen.findByText('Table game-session:1');
  expect(screen.getByText('Decision removed')).toBeVisible();
  expect(screen.getByText('Mode replay')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Simulated action' }));
  fireEvent.click(screen.getByRole('button', { name: /Back to app/ }));
  expect(close).toHaveBeenCalledOnce();
  expect(api.stopJob).not.toHaveBeenCalled();
  expect(vi.mocked(fetch).mock.calls.every((call) => !call[1]?.method || call[1].method === 'GET')).toBe(true);
});

test('an open viewer follows the next match in the same batch automatically', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  render(<LiveMatchViewer initialJob={job} onClose={vi.fn()} />);
  await screen.findByText('Table game-session:1');
  vi.mocked(api.loadJob).mockResolvedValue({ ...job, details: { ...job.details, sessionId: 'game-session:2', currentMatchIndex: 2, completed: 1 } });
  await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
  await screen.findByText('Table game-session:2');
  expect(screen.getByText('1/3 completed')).toBeVisible();
  expect(screen.queryByText('Table game-session:1')).toBeNull();
});

test('a deleted session between matches is waiting, not a fake failed batch', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => ({ status: 404, ok: false })));
  render(<LiveMatchViewer initialJob={job} onClose={vi.fn()} />);
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Waiting for the next one'));
  expect(screen.queryByRole('alert')).toBeNull();
  expect(api.stopJob).not.toHaveBeenCalled();
});

test('viewer refuses remote engines instead of reading an untrusted destination', async () => {
  vi.mocked(api.loadJob).mockResolvedValue({ ...job, details: { ...job.details, engineUrl: 'https://other.test' } });
  render(<LiveMatchViewer initialJob={job} onClose={vi.fn()} />);
  await screen.findByRole('alert');
  expect(fetch).not.toHaveBeenCalled();
});

test('joins at the latest frame then buffers, pauses and paces fast AI actions without changing play', async () => {
  vi.useFakeTimers({shouldAdvanceTime:true});
  const calls:string[]=[];
  vi.stubGlobal('fetch',vi.fn(async (url:string) => {
    calls.push(url);
    const sequences = url.endsWith('/spectator') ? [40] : url.endsWith('/40') ? [41,42,43] : [];
    return {ok:true,status:200,json:async()=>({frames:sequences.map(sequence=>({sequence,revision:sequence,state:{turnNumber:sequence,players:[]},decision:{id:'private-action'}})),hasMore:false})};
  }));
  render(<LiveMatchViewer initialJob={job} onClose={vi.fn()} />);
  await screen.findByText('Frame 40');
  expect(calls[0]).toMatch(/\/spectator$/);
  fireEvent.click(screen.getByRole('button',{name:'Pause viewing'}));
  await act(async()=>{await vi.advanceTimersByTimeAsync(850);});
  expect(screen.getByText('3 actions buffered')).toBeVisible();
  expect(screen.getByText('Frame 40')).toBeVisible();
  fireEvent.click(screen.getByRole('button',{name:'Resume viewing'}));
  await act(async()=>{await vi.advanceTimersByTimeAsync(710);});
  expect(screen.getByText('Frame 41')).toBeVisible();
  expect(screen.getByText('2 actions buffered')).toBeVisible();
  vi.mocked(api.loadJob).mockResolvedValue({...job,status:'completed'});
  await act(async()=>{await vi.advanceTimersByTimeAsync(1500);});
  expect(screen.getByText('Frame 43')).toBeVisible();
  expect(screen.getByText('0 actions buffered')).toBeVisible();
  expect(screen.getByText('Decision removed')).toBeVisible();
  expect(api.stopJob).not.toHaveBeenCalled();
});
