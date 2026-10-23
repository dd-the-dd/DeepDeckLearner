import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import ReplayHub from './ReplayLibrary';
import * as api from './api';
import type { ProgrammaticAgent, SavedReplaySummary } from './api';

vi.mock('./api', async (original) => ({ ...await original<typeof import('./api')>(), loadLocalTableReplays: vi.fn(), loadSavedReplays: vi.fn(), acquireSavedReplay: vi.fn(), renewSavedReplayLease: vi.fn(), releaseSavedReplayLease: vi.fn(), setReplayFavorite: vi.fn() }));
vi.mock('./DeckVisuals', () => ({ DeckArtwork: ({deck}:{deck:api.DeckSummary}) => <span data-testid={`art-${deck.id}`} data-cover={deck.coverCard?.name}>Card artwork</span>, ManaColors: () => <span>Mana symbols</span> }));
vi.mock('./LocalPixiReplay', () => ({ default: ({ replay, frameIndex, onFrameIndexChange }: { replay: api.SavedReplay; frameIndex: number; onFrameIndexChange: (frame: number) => void }) => <div aria-label="Playback"><span>{replay.id} · frame {frameIndex}</span><button onClick={() => onFrameIndexChange(frameIndex + 1)}>Next</button></div> }));
const agent = { id: 'pilot', name: 'Sneak pilot', decks: [] } as unknown as ProgrammaticAgent;
const row: SavedReplaySummary = { id: 'batch-one', source: 'local-table', createdAtUnixMs: 1000, episode: 1, seed: 42, matchupId: 'raw-uuid-not-a-label', decks: ['delver', 'sneak'], deckNames: ['Delver', 'Sneak and Show'], participants: [{ name: 'You', kind: 'human' }, { name: 'Sneak pilot', kind: 'agent' }], gameMode: 'legacy', gameStatus: 'completed', roundNumber: 2, winner: 'p1', durationSeconds: 60, frameCount: 100, bytes: 100, saved: false, viewing: false };

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.loadLocalTableReplays).mockResolvedValue([]);
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', { configurable: true, value: function (this: HTMLDialogElement) { this.open = true; } });
  vi.mocked(api.loadSavedReplays).mockImplementation(async () => [structuredClone(row)]);
  vi.mocked(api.acquireSavedReplay).mockResolvedValue({ replay: { schemaVersion: 'deepdeck-replay/v1', id: row.id, metadata: {}, frames: [{}], createdAtUnixMs: 1000 }, leaseId: 'watcher', expiresAtUnixMs: Date.now() + 90_000, saved: false });
  vi.mocked(api.releaseSavedReplayLease).mockResolvedValue({ released: true });
  vi.mocked(api.renewSavedReplayLease).mockResolvedValue({ expiresAtUnixMs: Date.now() + 90_000 });
  vi.mocked(api.setReplayFavorite).mockImplementation(async (_, __, favorite) => ({ saved: favorite }));
});
afterEach(() => { cleanup(); vi.useRealTimers(); });

test('human-only recordings can be read and protected as favorites without any agent',async()=>{
  vi.mocked(api.loadLocalTableReplays).mockResolvedValue([{...row,source:'local-human-table',participants:[{name:'Player 1',kind:'human'},{name:'Player 2',kind:'human'}]}]);
  render(<ReplayHub agents={[]} settings={null}/>);
  fireEvent.click(screen.getByRole('button',{name:'Local'}));
  await screen.findByRole('button',{name:/Watch match/});
  expect(api.loadSavedReplays).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button',{name:'Add to favorites'}));
  await waitFor(()=>expect(api.setReplayFavorite).toHaveBeenCalledWith('local-tables',row.id,true));
  fireEvent.click(screen.getByRole('button',{name:/Watch match/}));
  await screen.findByLabelText('Playback');
  expect(api.acquireSavedReplay).toHaveBeenCalledWith('local-tables',row.id);
});

test('human replay cards use local deck artwork and mana metadata even without an agent',async()=>{
  vi.mocked(api.loadLocalTableReplays).mockResolvedValue([row]);
  const deck:api.DeckSummary={id:'delver',name:'Delver',version:1,colors:['U'],playableCardCount:60,coverCard:{name:'Delver of Secrets',imageUrl:'https://example.invalid/delver.jpg'}};
  render(<ReplayHub agents={[]} localDecks={[deck]} settings={null}/>);
  fireEvent.click(screen.getByRole('button',{name:'Local'}));
  expect(await screen.findByTestId('art-delver')).toHaveAttribute('data-cover','Delver of Secrets');
  expect(screen.getByText('Mana symbols')).toBeInTheDocument();
});

function deferred<T>(){let resolve!:(value:T)=>void;const promise=new Promise<T>(done=>{resolve=done;});return{promise,resolve};}

async function library() {
  render(<ReplayHub agents={[agent]} settings={null} />);
  fireEvent.click(screen.getByRole('button', { name: 'Local' }));
  await screen.findByRole('button', { name: /Watch match/ });
}

test('cards present origin, format, decks and pilots instead of match IDs, without auto-opening', async () => {
  await library();
  expect(screen.getByText('Local Engine')).toBeVisible();
  expect(screen.getByText('legacy · 2 rounds')).toBeVisible();
  expect(screen.getByText('You')).toBeVisible();
  expect(screen.getByText('Sneak and Show')).toBeVisible();
  expect(screen.queryByText('raw-uuid-not-a-label')).toBeNull();
  expect(api.acquireSavedReplay).not.toHaveBeenCalled();
});

test('a competition replay link opens exactly that recording once and may be closed normally', async () => {
  render(<ReplayHub agents={[agent]} settings={null} initialReplayId={row.id} />);
  const dialog = await screen.findByRole('dialog', {name: 'Delver vs Sneak and Show'});
  await within(dialog).findByLabelText('Playback');
  expect(api.acquireSavedReplay).toHaveBeenCalledWith('pilot', row.id);
  fireEvent.click(within(dialog).getByRole('button', {name: /Back to replays/}));
  await waitFor(() => expect(api.releaseSavedReplayLease).toHaveBeenCalled());
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(api.acquireSavedReplay).toHaveBeenCalledTimes(1);
});

test('an evicted competition recording leaves results intact and reports its absence', async () => {
  render(<ReplayHub agents={[agent]} settings={null} initialReplayId="batch-evicted" />);
  expect(await screen.findByRole('alert')).toHaveTextContent('Competition results remain available');
  expect(api.acquireSavedReplay).not.toHaveBeenCalled();
});

test('cards show wins per game and total turns without confusing steps with turns', async () => {
  vi.mocked(api.loadSavedReplays).mockResolvedValue([{ ...row, matchSummary: { version: 'deepdeck-replay-match-summary/v1', scope: 'match', winsByPlayerId: { p1: 2, p2: 1 }, playerIds: ['p1', 'p2'], gamesToWin: 2, roundTurns: [{ round: 1, turns: 13, complete: true }, { round: 2, turns: 24, complete: true }, { round: 3, turns: 34, complete: true }], totalTurns: 71, turnsExact: true } }]);
  await library();
  expect(screen.getByLabelText('Games won')).toHaveTextContent('2 – 1');
  expect(screen.getByText('Turns played')).toBeVisible();
  expect(screen.getByText('71')).toBeVisible();
  expect(screen.getByText(/100 steps/)).toBeVisible();
});

test('Replays also opens a local running batch without changing its job', async () => {
  const live = { id: 'running', kind: 'playtest.batch', status: 'running', label: 'AI batch', details: { completed: 1, total: 4, deckNames: ['Tempo', 'Combo'] } } as api.Job;
  const watch = vi.fn();
  render(<ReplayHub agents={[agent]} settings={null} jobs={[live]} onWatch={watch} />);
  fireEvent.click(screen.getByRole('button', { name: 'Watch live' }));
  expect(watch).toHaveBeenCalledWith(live);
  expect(api.acquireSavedReplay).not.toHaveBeenCalled();
});

test('opens the full-window reader and closes with lease release and scroll restored', async () => {
  await library();
  fireEvent.click(screen.getByRole('button', { name: /Watch match/ }));
  const dialog = await screen.findByRole('dialog', { name: 'Delver vs Sneak and Show' });
  expect(dialog).toHaveClass('replay-theater');
  await within(dialog).findByLabelText('Playback');
  expect(document.body.style.overflow).toBe('hidden');
  fireEvent.click(within(dialog).getByRole('button', { name: /Back to replays/ }));
  await waitFor(() => expect(api.releaseSavedReplayLease).toHaveBeenCalledWith('pilot', row.id, 'watcher'));
  expect(document.body.style.overflow).toBe('');
});

test('a newly collected match and automatic refresh never reset the open playback', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  await library();
  fireEvent.click(screen.getByRole('button', { name: /Watch match/ }));
  await screen.findByLabelText('Playback');
  fireEvent.click(screen.getByRole('button', { name: 'Next' }));
  expect(screen.getByText(`${row.id} · frame 1`)).toBeVisible();
  vi.mocked(api.loadSavedReplays).mockResolvedValue([{ ...row, id: 'batch-newer', createdAtUnixMs: 5000 }, row]);
  await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
  vi.useRealTimers();
  expect(screen.getByText(`${row.id} · frame 1`)).toBeVisible();
  expect(api.acquireSavedReplay).toHaveBeenCalledTimes(1);
  expect(api.renewSavedReplayLease).toHaveBeenCalledWith('pilot', row.id, 'watcher');
});

test('stars toggle favorites in both directions without launching another replay', async () => {
  await library();
  fireEvent.click(screen.getByRole('button', { name: 'Add to favorites' }));
  await screen.findByRole('button', { name: 'Remove from favorites', pressed: true });
  expect(api.setReplayFavorite).toHaveBeenLastCalledWith('pilot', row.id, true);
  fireEvent.click(screen.getByLabelText('★ Favorites only'));
  fireEvent.click(screen.getByRole('button', { name: 'Remove from favorites' }));
  await screen.findByText('No favorite in this view');
  expect(api.setReplayFavorite).toHaveBeenLastCalledWith('pilot', row.id, false);
  expect(api.acquireSavedReplay).not.toHaveBeenCalled();
});

test('interrupted captures and full-storage errors are explicit, not fake completed matches', async () => {
  vi.mocked(api.loadSavedReplays).mockResolvedValue([{ ...row, gameStatus: 'interrupted', frameCount: 13 }]);
  render(<ReplayHub agents={[agent]} settings={{ replayStorageLimitBytes: 100, replayStorageUsedBytes: 200, replayPollSeconds: 600, autoCollectLeagueReplays: true, replayStorageError: 'Storage full; favorites kept.' }} />);
  fireEvent.click(screen.getByRole('button', { name: 'Local' }));
  await screen.findByText('Interrupted recording');
  expect(screen.getByRole('alert')).toHaveTextContent('Storage full; favorites kept.');
  fireEvent.click(screen.getByRole('button', { name: /Watch match/ }));
  expect(await screen.findByText(/rest of the match was not recorded/)).toHaveAttribute('role','status');
});

test('initial pending or failed library reads never pretend zero recordings and retry stays explicit',async()=>{
  const first=deferred<SavedReplaySummary[]>();
  vi.mocked(api.loadSavedReplays).mockReturnValueOnce(first.promise).mockRejectedValueOnce(new Error('Unavailable'));
  render(<ReplayHub agents={[agent]} settings={null}/>);
  const summary=screen.getByRole('status',{name:'Replay library status'});
  expect(summary).toHaveTextContent('Loading your recordings');expect(summary).not.toHaveTextContent('0 recordings');
  expect(screen.getByText('Checking replay storage…')).toBeInTheDocument();
  await act(async()=>first.resolve([]));
  fireEvent.click(screen.getByRole('button',{name:'Refresh library'}));
  await screen.findByRole('button',{name:'Retry replay libraries'});
  expect(screen.queryByText('No recording in this view')).not.toBeInTheDocument();
  expect(summary).toHaveTextContent('refresh incomplete');
  vi.mocked(api.loadSavedReplays).mockResolvedValue([{...row,source:'league'}]);
  fireEvent.click(screen.getByRole('button',{name:'Retry replay libraries'}));
  await screen.findByRole('button',{name:/Watch match/});
  expect(summary).toHaveTextContent('1 recording');
});

test('failure before the first snapshot is unavailable and never consumes a competition replay link',async()=>{
  vi.mocked(api.loadSavedReplays).mockRejectedValueOnce(new Error('Unavailable')).mockResolvedValue([row]);
  render(<ReplayHub agents={[agent]} settings={null} initialReplayId={row.id}/>);
  await screen.findByRole('button',{name:'Retry replay libraries'});
  expect(screen.getByRole('status',{name:'Replay library status'})).toHaveTextContent('count unavailable');
  expect(screen.queryByText(/Competition results remain available/)).not.toBeInTheDocument();
  expect(api.acquireSavedReplay).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button',{name:'Retry replay libraries'}));
  await screen.findByLabelText('Playback');
  expect(api.acquireSavedReplay).toHaveBeenCalledOnce();
});

test('one unavailable agent library keeps its earlier recordings while another adds a new match',async()=>{
  const second={...agent,id:'second',name:'Second pilot'};
  vi.mocked(api.loadSavedReplays).mockImplementation(async id=>[{...row,id:id==='pilot'?'local-first':'local-second',source:'local-table'}]);
  render(<ReplayHub agents={[agent,second]} settings={null}/>);
  fireEvent.click(screen.getByRole('button',{name:'Local'}));
  await waitFor(()=>expect(screen.getAllByRole('button',{name:/Watch match/})).toHaveLength(2));
  vi.mocked(api.loadSavedReplays).mockImplementation(async id=>{
    if(id==='pilot')throw new Error('Unavailable');
    return [{...row,id:'local-second'},{...row,id:'local-new',createdAtUnixMs:5000}];
  });
  fireEvent.click(screen.getByRole('button',{name:'Refresh library'}));
  await screen.findByRole('button',{name:'Retry replay libraries'});
  expect(screen.getAllByRole('button',{name:/Watch match/})).toHaveLength(3);
  expect(screen.getByRole('status',{name:'Replay library status'})).toHaveTextContent('Last loaded view: 3 recordings');
});

test('refreshes are sequential and failure never resets an open reader or its step',async()=>{
  vi.useFakeTimers({shouldAdvanceTime:true});
  await library();
  fireEvent.click(screen.getByRole('button',{name:/Watch match/}));
  await screen.findByLabelText('Playback');
  fireEvent.click(screen.getByRole('button',{name:'Next'}));
  const waiting=deferred<SavedReplaySummary[]>();
  vi.mocked(api.loadSavedReplays).mockReturnValueOnce(waiting.promise);
  await act(async()=>{vi.advanceTimersByTime(30_000);});
  expect(api.loadSavedReplays).toHaveBeenCalledTimes(2);
  await act(async()=>{vi.advanceTimersByTime(90_000);});
  expect(api.loadSavedReplays).toHaveBeenCalledTimes(2);
  await act(async()=>waiting.resolve([row]));
  vi.mocked(api.loadSavedReplays).mockRejectedValue(new Error('Offline'));
  await act(async()=>{vi.advanceTimersByTime(30_000);});
  expect(screen.getByText(`${row.id} · frame 1`)).toBeVisible();
  expect(api.acquireSavedReplay).toHaveBeenCalledOnce();
  expect(screen.getByRole('status',{name:'Replay library status'})).toHaveTextContent('Last loaded view: 1 recording');
});

test('a stale pre-favorite refresh cannot undo a confirmed star or hide it from favorites',async()=>{
  await library();
  const old=deferred<SavedReplaySummary[]>();
  vi.mocked(api.loadSavedReplays).mockReturnValueOnce(old.promise);
  fireEvent.click(screen.getByRole('button',{name:'Refresh library'}));
  fireEvent.click(screen.getByRole('button',{name:'Add to favorites'}));
  await screen.findByRole('button',{name:'Remove from favorites'});
  await act(async()=>old.resolve([{...row,saved:false}]));
  fireEvent.click(screen.getByLabelText('★ Favorites only'));
  expect(screen.getByRole('button',{name:'Remove from favorites'})).toBeInTheDocument();
  expect(screen.getByRole('button',{name:/Watch match/})).toBeInTheDocument();
});

test('a cancelled read cannot overwrite a newer manual refresh',async()=>{
  const old=deferred<SavedReplaySummary[]>();
  vi.mocked(api.loadSavedReplays).mockReturnValueOnce(old.promise).mockResolvedValueOnce([{...row,source:'league'}]);
  render(<ReplayHub agents={[agent]} settings={null}/>);
  const signal=vi.mocked(api.loadSavedReplays).mock.calls[0][1];
  fireEvent.click(screen.getByRole('button',{name:'Restart library check'}));
  expect(signal?.aborted).toBe(true);
  await screen.findByRole('button',{name:/Watch match/});
  await act(async()=>old.resolve([]));
  expect(screen.getByRole('button',{name:/Watch match/})).toBeInTheDocument();
});

test('a competition link waits for the parent agent library rather than reporting an evicted game',async()=>{
  const view=render(<ReplayHub agents={[]} agentLibraryReady={false} settings={null} initialReplayId={row.id}/>);
  expect(screen.queryByText('No recording in this view')).not.toBeInTheDocument();
  expect(api.loadSavedReplays).not.toHaveBeenCalled();expect(api.acquireSavedReplay).not.toHaveBeenCalled();
  view.rerender(<ReplayHub agents={[agent]} agentLibraryReady settings={null} initialReplayId={row.id}/>);
  await screen.findByLabelText('Playback');
  expect(api.acquireSavedReplay).toHaveBeenCalledOnce();
});

test('the first agent-library read can be retried without assuming the replay library is empty',async()=>{
  const retry=vi.fn().mockRejectedValue(new Error('Offline'));
  render(<ReplayHub agents={[]} agentLibraryReady={false} onRefreshAgents={retry} settings={null}/>);
  fireEvent.click(screen.getByRole('button',{name:'Reload agent libraries'}));
  await screen.findByRole('button',{name:'Retry replay libraries'});
  expect(retry).toHaveBeenCalledOnce();
  expect(screen.queryByText('No recording in this view')).not.toBeInTheDocument();
});
