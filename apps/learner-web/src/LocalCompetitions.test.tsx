import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import CompetitionHistory, { CompetitionBuilder, RoundRobinMatrix } from './LocalCompetitions';
import * as api from './api';
import type { DeckSummary, LocalCompetition, ProgrammaticAgent } from './api';

vi.mock('./api', async original => ({...await original<typeof import('./api')>(), createLocalCompetition: vi.fn(), loadLocalCompetitions: vi.fn(), loadLocalCompetition: vi.fn(), controlLocalCompetition: vi.fn(), loadJob: vi.fn(), loadStatus: vi.fn()}));
const agents = [{id: 'pilot-a', name: 'Tempo pilot', format: 'legacy', deckVersionIds: ['deck-a'], decks: [{id: 'deck-a', name: 'Delver', format: 'legacy', version: 1}]}, {id: 'pilot-b', name: 'Combo pilot', format: 'legacy', deckVersionIds: ['deck-b'], decks: [{id: 'deck-b', name: 'Sneak', format: 'legacy', version: 1}]}] as ProgrammaticAgent[];
const competition = {id: 'c1', name: 'My tournament', type: 'double', status: 'running', createdAt: '2026-10-19T00:00:00Z', entrants: [{id: 'p1', name: 'Tempo pilot', seed: 1}, {id: 'p2', name: 'Combo pilot', seed: 2}], matches: [{id: 'm1', stage: 'winners', round: 1, players: ['p1', 'p2'], status: 'playing', winner: null, score: null, jobId: 'j1', attempts: []}], activeJobId: 'j1', playSettings: {firstSeed: 0}, standings: [], parameters: {}, error: null} as unknown as LocalCompetition;
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.createLocalCompetition).mockResolvedValue(competition);
  vi.mocked(api.loadLocalCompetitions).mockResolvedValue([competition]);
  vi.mocked(api.loadLocalCompetition).mockResolvedValue(competition);
});

test('group playoffs explain first-to-four and submit reseeding and opening advantage', async () => {
  render(<CompetitionBuilder agents={agents} engineUrl="http://127.0.0.1:8787" ready onCreated={vi.fn()} />);
  fireEvent.click(screen.getByRole('button', {name: 'Add all agents'}));
  fireEvent.click(screen.getByRole('button', {name: 'Add all agents'}));
  fireEvent.change(screen.getByLabelText('Competition format'), {target: {value: 'groups_cut'}});
  fireEvent.change(screen.getByLabelText('Playoff match wins to advance'), {target: {value: '4'}});
  expect(screen.getByText('Best of 7 matches')).toBeInTheDocument();
  const help = screen.getByRole('button', {name: 'Help: Playoff match wins to advance'});
  fireEvent.keyDown(help, {key: 'Enter'});
  expect(screen.getByRole('tooltip')).toHaveTextContent('four wins means a best-of-seven');
  fireEvent.keyDown(help, {key: 'Escape'});
  expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', {name: 'Start competition'}));
  await waitFor(() => expect(api.createLocalCompetition).toHaveBeenCalled());
  expect(vi.mocked(api.createLocalCompetition).mock.calls[0][0]).toMatchObject({type: 'groups_cut', groups: 2, qualifiers_per_group: 1, playoff_wins: 4, reseed: true, seed_advantage: true});
});

test('player entries use local decks, allow another human, and preserve AI mastered pools', async () => {
  const decks = agents.flatMap(a => a.decks) as DeckSummary[];
  render(<CompetitionBuilder agents={agents} decks={decks} engineUrl="http://127.0.0.1:8787" ready onCreated={vi.fn()} />);
  fireEvent.click(screen.getByRole('button', {name: 'Add all agents'}));
  fireEvent.click(screen.getAllByRole('button', {name: 'Player'})[0]);
  expect(screen.getAllByRole('button', {name: 'Player'})[1]).toBeEnabled();
  expect(screen.getByRole('button', {name: 'Browse local & League decks'})).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Entrant 1 deck'), {target: {value: 'deck-a'}});
  fireEvent.click(screen.getByRole('button', {name: 'Start competition'}));
  await waitFor(() => expect(api.createLocalCompetition).toHaveBeenCalled());
  expect(vi.mocked(api.createLocalCompetition).mock.calls[0][0].entrants).toEqual([
    {kind: 'human', agent_id: '', deck_id: 'deck-a', name: 'You'},
    {agent_id: 'pilot-b', deck_id: 'random', name: 'Combo pilot'},
  ]);
});

test('an all-human competition needs no agent and retains each player name and deck',async()=>{
  const decks=agents.flatMap(agent=>agent.decks) as DeckSummary[];
  render(<CompetitionBuilder agents={[]} decks={decks} engineUrl="http://127.0.0.1:8787" ready onCreated={vi.fn()}/>);
  for(let index=1;index<=2;index++){
    fireEvent.change(screen.getByLabelText('Add an entrant'),{target:{value:'human'}});
    fireEvent.change(screen.getByLabelText(`Entrant ${index} name`),{target:{value:index===1?'Dérick':'Alex'}});
    fireEvent.change(screen.getByLabelText(`Entrant ${index} deck`),{target:{value:index===1?'deck-a':'deck-b'}});
  }
  expect(screen.getAllByRole('button',{name:'Player'})).toHaveLength(2);
  fireEvent.click(screen.getByRole('button',{name:'Start competition'}));
  await waitFor(()=>expect(api.createLocalCompetition).toHaveBeenCalledOnce());
  expect(vi.mocked(api.createLocalCompetition).mock.calls[0][0].entrants).toEqual([
    {kind:'human',agent_id:'',deck_id:'deck-a',name:'Dérick'},
    {kind:'human',agent_id:'',deck_id:'deck-b',name:'Alex'},
  ]);
  expect(vi.mocked(api.createLocalCompetition).mock.calls[0][0].match_timeout_seconds).toBeNull();
});

test('human competition defaults are untimed while explicit limits survive participant changes',()=>{
  render(<CompetitionBuilder agents={agents} decks={agents.flatMap(a=>a.decks) as DeckSummary[]} engineUrl="http://127.0.0.1:8787" ready onCreated={vi.fn()}/>);
  fireEvent.click(screen.getByRole('button',{name:'Add all agents'}));
  const limit=()=>screen.getByLabelText('Match safety limit');
  expect(limit()).toHaveValue('600');
  fireEvent.click(screen.getAllByRole('button',{name:'Player'})[0]);
  expect(limit()).toHaveValue('');
  fireEvent.change(limit(),{target:{value:'180'}});
  fireEvent.click(screen.getAllByRole('button',{name:'AI'})[0]);
  expect(limit()).toHaveValue('180');
  fireEvent.click(screen.getAllByRole('button',{name:'Player'})[0]);
  expect(limit()).toHaveValue('180');
  fireEvent.change(limit(),{target:{value:''}});
  fireEvent.click(screen.getAllByRole('button',{name:'AI'})[0]);
  expect(limit()).toHaveValue('600');
  fireEvent.click(screen.getAllByRole('button',{name:'Player'})[0]);
  expect(limit()).toHaveValue('');
});

test('preparing a match explains the cold start without offering another launch',async()=>{
  vi.mocked(api.loadLocalCompetition).mockResolvedValue({...competition,matches:[{...competition.matches[0],status:'dispatching'}],activeJobId:null});
  render(<CompetitionHistory initialId="c1" onCreate={vi.fn()} onWatch={vi.fn()} onReplays={vi.fn()}/>);
  expect(await screen.findByText('Preparing table…')).toBeInTheDocument();
  expect(screen.getByText(/Preparing decks and Oracle rules/)).toHaveAttribute('role','status');
  expect(screen.queryByRole('button',{name:'Play current match'})).toBeNull();
});

test('interrupted attempts retain a direct replay link after the competition pauses',async()=>{
  const replay=vi.fn();
  vi.mocked(api.loadLocalCompetition).mockResolvedValue({...competition,status:'paused',activeJobId:null,matches:[{...competition.matches[0],status:'error',attempts:[{replayId:'partial-game',error:'The table was closed.'}]}]});
  render(<CompetitionHistory initialId="c1" onCreate={vi.fn()} onWatch={vi.fn()} onReplays={replay}/>);
  fireEvent.click(await screen.findByText('Interrupted attempts & recordings'));
  fireEvent.click(screen.getByRole('button',{name:'Replay interrupted attempt 1'}));
  expect(replay).toHaveBeenCalledWith('partial-game');
  expect(api.controlLocalCompetition).not.toHaveBeenCalled();
});

test('Commander uses four-player compatible formats and a real Commander agent pool', async () => {
  const commander = agents.map(agent => ({...agent, format: 'commander', decks: agent.decks.map(deck => ({...deck, format: 'commander'}))})) as ProgrammaticAgent[];
  render(<CompetitionBuilder agents={commander} engineUrl="http://127.0.0.1:8787" ready onCreated={vi.fn()} />);
  fireEvent.change(screen.getByLabelText('Game format'), {target: {value: 'commander'}});
  expect(screen.getByLabelText('Competition format').querySelectorAll('option')).toHaveLength(4);
  fireEvent.click(screen.getByRole('button', {name: 'Add all agents'}));
  fireEvent.click(screen.getByRole('button', {name: 'Start competition'}));
  expect(screen.getByRole('alert')).toHaveTextContent('Commander needs at least four');
  expect(api.createLocalCompetition).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', {name: 'Add all agents'}));
  fireEvent.click(screen.getByRole('button', {name: 'Start competition'}));
  await waitFor(() => expect(api.createLocalCompetition).toHaveBeenCalled());
  expect(vi.mocked(api.createLocalCompetition).mock.calls[0][0]).toMatchObject({format: 'commander', type: 'single'});
});

test.each([false, true])('five-player Commander season supports equal real matches or optional three-point byes: %s', async flexible => {
  const commander = Array.from({length:5}, (_, index) => ({...agents[0], id:`commander-${index}`, name:`Commander ${index}`, format:'commander', decks:agents[0].decks.map(deck=>({...deck,format:'commander'}))})) as ProgrammaticAgent[];
  render(<CompetitionBuilder agents={commander} engineUrl="http://127.0.0.1:8787" ready onCreated={vi.fn()} />);
  fireEvent.change(screen.getByLabelText('Game format'), {target:{value:'commander'}});
  fireEvent.change(screen.getByLabelText('Competition format'), {target:{value:'random_season'}});
  fireEvent.click(screen.getByRole('button', {name:'Add all agents'}));
  expect(screen.getByLabelText('Season matches per player')).toHaveValue(4);
  expect(screen.getByLabelText('Commander season plan')).toHaveTextContent('5 four-player matches · 0 awarded byes');
  if (flexible) {
    fireEvent.change(screen.getByLabelText('Season matches per player'), {target:{value:'3'}});
    fireEvent.click(screen.getByRole('button', {name:'Start competition'}));
    expect(screen.getByRole('alert')).toHaveTextContent('choose a multiple of 4');
    expect(api.createLocalCompetition).not.toHaveBeenCalled();
    fireEvent.click(screen.getByLabelText('Allow season byes'));
    expect(screen.queryByRole('alert')).toBeNull();
    expect(screen.getByLabelText('Commander season plan')).toHaveTextContent('3 four-player matches · 3 awarded byes');
  }
  fireEvent.click(screen.getByRole('button', {name:'Start competition'}));
  await waitFor(()=>expect(api.createLocalCompetition).toHaveBeenCalledOnce());
  expect(vi.mocked(api.createLocalCompetition).mock.calls[0][0]).toMatchObject({format:'commander', type:'random_season', rounds:flexible?3:4, allow_season_byes:flexible});
});

test('competition setup offers Engine start without launching a competition', async () => {
  const start = vi.fn().mockResolvedValue(undefined);
  render(<CompetitionBuilder agents={agents} engineUrl="http://127.0.0.1:8787" ready={false} onStartEngine={start} onCreated={vi.fn()} />);
  fireEvent.click(screen.getByRole('button', {name: 'Start game engine'}));
  await waitFor(() => expect(start).toHaveBeenCalledOnce());
  expect(api.createLocalCompetition).not.toHaveBeenCalled();
  expect(screen.getByRole('button', {name: 'Start competition'})).toBeEnabled();
});

test.each([true,false])('a pending Engine status is checked before creating a competition: healthy=%s',async healthy=>{
  vi.mocked(api.loadStatus).mockResolvedValue({engine:{healthy,url:'http://127.0.0.1:8787'}} as api.CapabilityStatus);
  render(<CompetitionBuilder agents={agents} engineUrl="http://127.0.0.1:8787" ready={false} onCreated={vi.fn()}/>);
  fireEvent.click(screen.getByRole('button',{name:'Add all agents'}));
  fireEvent.click(screen.getByRole('button',{name:'Start competition'}));
  await waitFor(()=>expect(api.loadStatus).toHaveBeenCalledOnce());
  if(healthy) await waitFor(()=>expect(api.createLocalCompetition).toHaveBeenCalledOnce());
  else {
    expect(await screen.findByRole('alert')).toHaveTextContent('No competition was created.');
    expect(api.createLocalCompetition).not.toHaveBeenCalled();
  }
});
afterEach(() => {cleanup(); vi.useRealTimers();});

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(done => {resolve = done;});
  return {promise, resolve};
}

test('round-robin matrix reverses scores by row, separates legs and excludes playoffs and byes', () => {
  const replay = vi.fn();
  const match = {...competition.matches[0], stage: 'round_robin', status: 'done' as const, winner: 'p1', score: [2, 1], replayId: 'replay-one'};
  const draw = {...match, id: 'm2', round: 2, players: ['p2', 'p1'], winner: null, score: [1, 1], replayId: null};
  const pending = {...match, id: 'm3', round: 3, status: 'ready' as const, score: null, winner: null, replayId: null};
  render(<RoundRobinMatrix competition={{...competition, matches: [match, draw, pending, {...match, id: 'playoff', stage: 'winners', score: [9, 0]}, {...match, id: 'bye', players: ['p1', null], status: 'bye'}]}} onReplays={replay} />);
  const win = screen.getByRole('button', {name: 'Tempo pilot against Combo pilot, round 1: Win 2–1. Watch replay'});
  expect(screen.getByRole('button', {name: 'Combo pilot against Tempo pilot, round 1: Loss 1–2. Watch replay'})).toBeInTheDocument();
  expect(screen.getAllByText('Draw 1–1')).toHaveLength(2);
  expect(screen.getAllByText('Not played')).toHaveLength(2);
  expect(screen.queryByText('Win 9–0')).not.toBeInTheDocument();
  expect(screen.getAllByLabelText('Same entrant')).toHaveLength(2);
  fireEvent.click(win);
  expect(replay).toHaveBeenCalledWith('replay-one');
  expect(screen.getByRole('region', {name: 'Round-robin results'})).toHaveAttribute('tabindex', '0');
});

test('matrix never invents game scores and identifies live and interrupted matches', () => {
  render(<RoundRobinMatrix competition={{...competition, matches: [
    {...competition.matches[0], stage: 'round_robin', status: 'done', winner: 'p2', score: null},
    {...competition.matches[0], id: 'live', stage: 'round_robin'},
    {...competition.matches[0], id: 'error', stage: 'round_robin', status: 'error'},
  ]}} onReplays={vi.fn()} />);
  expect(screen.getByText('Win')).toBeInTheDocument();
  expect(screen.getByText('Loss')).toBeInTheDocument();
  expect(screen.getAllByText('Live')).toHaveLength(2);
  expect(screen.getAllByText('Needs attention')).toHaveLength(2);
});

test('nine real formats, any number of entrants, only mastered decks, and no implicit fixed seed', async () => {
  const created = vi.fn(); render(<CompetitionBuilder agents={agents} engineUrl="http://127.0.0.1:8787" ready onCreated={created} />);
  expect(screen.getByLabelText('Competition format').querySelectorAll('option')).toHaveLength(9);
  fireEvent.click(screen.getByRole('button', {name: 'Add all agents'}));
  expect(screen.getByLabelText('Entrant 1 deck').textContent).toContain('Delver');
  expect(screen.getByLabelText('Entrant 1 deck').textContent).not.toContain('Sneak');
  fireEvent.click(screen.getByRole('button', {name: 'Start competition'}));
  await waitFor(() => expect(created).toHaveBeenCalledWith(competition));
  const payload = vi.mocked(api.createLocalCompetition).mock.calls[0][0];
  expect(payload.type).toBe('single'); expect(payload.seed).toBeUndefined();
  expect(payload.entrants).toEqual([{agent_id: 'pilot-a', deck_id: 'random', name: 'Tempo pilot'}, {agent_id: 'pilot-b', deck_id: 'random', name: 'Combo pilot'}]);
});

test('round-robin qualification parameters and removal are sent to organizer', async () => {
  render(<CompetitionBuilder agents={agents} engineUrl="http://127.0.0.1:8787" ready onCreated={vi.fn()} />);
  fireEvent.click(screen.getByRole('button', {name: 'Add all agents'}));
  fireEvent.click(screen.getByRole('button', {name: 'Add all agents'}));
  fireEvent.change(screen.getByLabelText('Competition format'), {target: {value: 'round_robin_cut'}});
  fireEvent.change(screen.getByLabelText('Round-robin legs'), {target: {value: '2'}});
  fireEvent.change(screen.getByLabelText('Playoff qualifiers'), {target: {value: '3'}});
  fireEvent.click(screen.getByRole('button', {name: 'Remove entrant 4'}));
  fireEvent.click(screen.getByRole('button', {name: 'Start competition'}));
  await waitFor(() => expect(api.createLocalCompetition).toHaveBeenCalled());
  expect(vi.mocked(api.createLocalCompetition).mock.calls[0][0]).toMatchObject({type: 'round_robin_cut', legs: 2, cut: 3});
  expect(vi.mocked(api.createLocalCompetition).mock.calls[0][0].entrants).toHaveLength(3);
});

test('history opens classic bracket and live viewer, pausing is a separate action', async () => {
  const watch = vi.fn(); const job = {id: 'j1', status: 'running'} as api.Job;
  vi.mocked(api.loadJob).mockResolvedValue(job);
  vi.mocked(api.controlLocalCompetition).mockResolvedValue({...competition, status: 'pausing'});
  render(<CompetitionHistory onCreate={vi.fn()} onWatch={watch} onReplays={vi.fn()} />);
  fireEvent.click(await screen.findByRole('button', {name: /My tournament/}));
  expect(await screen.findByText('Winners bracket')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', {name: 'Watch current match'}));
  await waitFor(() => expect(watch).toHaveBeenCalledWith(job));
  expect(api.controlLocalCompetition).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', {name: 'Pause after this match'}));
  await waitFor(() => expect(api.controlLocalCompetition).toHaveBeenCalledWith('c1', 'pause'));
  expect(await screen.findByText('Finishing the current match before pausing…')).toBeInTheDocument();
});

test.each(['playing', 'done'] as const)('history counts individual series matches while a series is %s', async status => {
  const seriesGames = [1, 2, 3].map(index => ({winner: 'p1', score: null, replayId: `series-${index}`}));
  vi.mocked(api.loadLocalCompetitions).mockResolvedValue([{
    ...competition,
    matches: [
      {...competition.matches[0], id: 'group', stage: 'groups', status: 'done'},
      {...competition.matches[0], id: 'final', stage: 'winners', status, seriesGames},
      {...competition.matches[0], id: 'bye', status: 'bye'},
      {...competition.matches[0], id: 'skipped', status: 'skipped'},
    ],
  }]);
  render(<CompetitionHistory onCreate={vi.fn()} onWatch={vi.fn()} onReplays={vi.fn()} />);
  const card = await screen.findByRole('button', {name: /My tournament/});
  expect(card).toHaveTextContent(`${status === 'done' ? 2 : 1} encounter${status === 'done' ? 's' : ''} completed`);
  expect(card).toHaveTextContent('4 matches played');
});

test('paused error is visible, completed results remain and resume is explicit', async () => {
  vi.mocked(api.loadLocalCompetition).mockResolvedValue({...competition, status: 'paused', error: 'Engine action failed', activeJobId: null});
  render(<CompetitionHistory initialId="c1" onCreate={vi.fn()} onWatch={vi.fn()} onReplays={vi.fn()} />);
  expect(await screen.findByRole('alert')).toHaveTextContent('Engine action failed');
  expect(screen.getByRole('button', {name: 'Resume competition'})).toBeInTheDocument();
  expect(api.controlLocalCompetition).not.toHaveBeenCalled();
});

test('a poll started before pause cannot overwrite its confirmed response', async () => {
  vi.useFakeTimers();
  render(<CompetitionHistory initialId="c1" onCreate={vi.fn()} onWatch={vi.fn()} onReplays={vi.fn()} />);
  await act(async () => {});
  expect(screen.getByText('Winners bracket')).toBeInTheDocument();
  const oldRead = deferred<LocalCompetition>();
  vi.mocked(api.loadLocalCompetition).mockReturnValueOnce(oldRead.promise);
  await act(async () => {await vi.advanceTimersByTimeAsync(2000);});
  expect(api.loadLocalCompetition).toHaveBeenCalledTimes(2);
  vi.mocked(api.controlLocalCompetition).mockResolvedValue({...competition, status: 'pausing'});
  await act(async () => {fireEvent.click(screen.getByRole('button', {name: 'Pause after this match'}));});
  expect(screen.getByText('Finishing the current match before pausing…')).toBeInTheDocument();
  await act(async () => {oldRead.resolve(competition);});
  expect(screen.getByText('Finishing the current match before pausing…')).toBeInTheDocument();
  expect(screen.queryByRole('button', {name: 'Pause after this match'})).not.toBeInTheDocument();
});

test('polling during a pending control does not replace the last confirmed status', async () => {
  vi.useFakeTimers();
  render(<CompetitionHistory initialId="c1" onCreate={vi.fn()} onWatch={vi.fn()} onReplays={vi.fn()} />);
  await act(async () => {});
  const pause = deferred<LocalCompetition>();
  vi.mocked(api.controlLocalCompetition).mockReturnValueOnce(pause.promise);
  vi.mocked(api.loadLocalCompetition).mockResolvedValue({...competition, status: 'paused', activeJobId: null});
  fireEvent.click(screen.getByRole('button', {name: 'Pause after this match'}));
  await act(async () => {await vi.advanceTimersByTimeAsync(2000);});
  expect(api.loadLocalCompetition).toHaveBeenCalledTimes(2);
  expect(screen.queryByRole('button', {name: 'Resume competition'})).not.toBeInTheDocument();
  await act(async () => {pause.resolve({...competition, status: 'pausing'});});
  expect(screen.getByText('Finishing the current match before pausing…')).toBeInTheDocument();
});

test('successful background reads do not erase an unconfirmed control error', async () => {
  vi.useFakeTimers();
  render(<CompetitionHistory initialId="c1" onCreate={vi.fn()} onWatch={vi.fn()} onReplays={vi.fn()} />);
  await act(async () => {});
  vi.mocked(api.controlLocalCompetition).mockRejectedValueOnce(new Error('Connection lost'));
  await act(async () => {fireEvent.click(screen.getByRole('button', {name: 'Pause after this match'}));});
  expect(screen.getByRole('alert')).toHaveTextContent('Connection lost');
  expect(screen.getByRole('alert')).toHaveTextContent('The change is not confirmed');
  await act(async () => {await vi.advanceTimersByTimeAsync(2000);});
  expect(api.loadLocalCompetition).toHaveBeenCalledTimes(2);
  expect(screen.getByRole('alert')).toHaveTextContent('Connection lost');
  vi.mocked(api.controlLocalCompetition).mockResolvedValueOnce({...competition, status: 'pausing'});
  await act(async () => {fireEvent.click(screen.getByRole('button', {name: 'Pause after this match'}));});
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});

test('leaving a pending control cannot reopen its old detail or leave controls busy', async () => {
  render(<CompetitionHistory initialId="c1" onCreate={vi.fn()} onWatch={vi.fn()} onReplays={vi.fn()} />);
  await screen.findByText('Winners bracket');
  const pause = deferred<LocalCompetition>();
  vi.mocked(api.controlLocalCompetition).mockReturnValueOnce(pause.promise);
  fireEvent.click(screen.getByRole('button', {name: 'Pause after this match'}));
  fireEvent.click(screen.getByRole('button', {name: /All competitions/}));
  await screen.findByRole('heading', {name: 'Competition history'});
  await act(async () => {pause.resolve({...competition, status: 'pausing'});});
  expect(screen.queryByText('Winners bracket')).not.toBeInTheDocument();
  fireEvent.click(await screen.findByRole('button', {name: /My tournament/}));
  expect(await screen.findByRole('button', {name: 'Pause after this match'})).toBeEnabled();
});

test('a failed history load is not presented as an empty library and can be retried', async () => {
  vi.mocked(api.loadLocalCompetitions).mockRejectedValueOnce(new Error('History unavailable'));
  render(<CompetitionHistory onCreate={vi.fn()} onWatch={vi.fn()} onReplays={vi.fn()} />);
  expect(await screen.findByRole('alert')).toHaveTextContent('Saved competitions could not be loaded');
  expect(screen.queryByRole('button', {name: 'Create your first competition'})).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', {name: 'Retry loading'}));
  expect(await screen.findByRole('button', {name: /My tournament/})).toBeInTheDocument();
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});

test('failed detail refresh retains standings and recovers on the next read', async () => {
  vi.useFakeTimers();
  render(<CompetitionHistory initialId="c1" onCreate={vi.fn()} onWatch={vi.fn()} onReplays={vi.fn()} />);
  await act(async () => {});
  vi.mocked(api.loadLocalCompetition).mockRejectedValueOnce(new Error('Refresh unavailable'));
  await act(async () => {await vi.advanceTimersByTimeAsync(2000);});
  expect(screen.getByRole('alert')).toHaveTextContent('Previously loaded results remain visible');
  expect(screen.getByText('Winners bracket')).toBeInTheDocument();
  await act(async () => {await vi.advanceTimersByTimeAsync(2000);});
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});

test.each(['swiss', 'round_robin_cut'])('%s defaults adapt to a two-entrant field', async kind => {
  render(<CompetitionBuilder agents={agents} engineUrl="http://127.0.0.1:8787" ready onCreated={vi.fn()} />);
  fireEvent.click(screen.getByRole('button', {name: 'Add all agents'}));
  fireEvent.change(screen.getByLabelText('Competition format'), {target: {value: kind}});
  expect(screen.getByLabelText(kind === 'swiss' ? 'Swiss rounds' : 'Playoff qualifiers')).toHaveValue(kind === 'swiss' ? 1 : 2);
  fireEvent.click(screen.getByRole('button', {name: 'Start competition'}));
  await waitFor(() => expect(api.createLocalCompetition).toHaveBeenCalled());
  expect(vi.mocked(api.createLocalCompetition).mock.calls[0][0]).toMatchObject(kind === 'swiss' ? {rounds: 1} : {cut: 2});
});

test('removing entrants preserves an explicit cut and requires fixing it before launch', () => {
  render(<CompetitionBuilder agents={agents} engineUrl="http://127.0.0.1:8787" ready onCreated={vi.fn()} />);
  fireEvent.click(screen.getByRole('button', {name: 'Add all agents'}));
  fireEvent.click(screen.getByRole('button', {name: 'Add all agents'}));
  fireEvent.change(screen.getByLabelText('Competition format'), {target: {value: 'round_robin_cut'}});
  fireEvent.change(screen.getByLabelText('Playoff qualifiers'), {target: {value: '3'}});
  fireEvent.change(screen.getByLabelText('Playoff qualifiers'), {target: {value: '4'}});
  fireEvent.click(screen.getByRole('button', {name: 'Remove entrant 4'}));
  expect(screen.getByLabelText('Playoff qualifiers')).toHaveValue(4);
  expect(screen.getByRole('status')).toHaveTextContent('Choose between 2 and 3 playoff qualifiers.');
  fireEvent.click(screen.getByRole('button', {name: 'Start competition'}));
  expect(screen.getByRole('alert')).toHaveTextContent('Choose between 2 and 3 playoff qualifiers.');
  expect(api.createLocalCompetition).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText('Playoff qualifiers'), {target: {value: '3'}});
  expect(screen.getByRole('button', {name: 'Start competition'})).toBeEnabled();
});

test('season rounds and playoff qualifiers can be cleared, replaced and submitted as numbers', async()=>{
  render(<CompetitionBuilder agents={agents} engineUrl="http://127.0.0.1:8787" ready onCreated={vi.fn()}/>);
  fireEvent.click(screen.getByRole('button',{name:'Add all agents'}));
  fireEvent.click(screen.getByRole('button',{name:'Add all agents'}));
  fireEvent.change(screen.getByLabelText('Competition format'),{target:{value:'random_season_cut'}});
  const cut=screen.getByLabelText('Playoff qualifiers');
  const rounds=screen.getByLabelText('Season rounds');
  expect(cut).toHaveAttribute('step','1');
  expect(rounds).toHaveAttribute('step','1');
  fireEvent.change(cut,{target:{value:''}});
  expect(cut).toHaveValue(null);
  fireEvent.click(screen.getByRole('button',{name:'Start competition'}));
  expect(screen.getByRole('alert')).toHaveTextContent('Choose between 2 and 4 playoff qualifiers.');
  expect(screen.getByRole('button',{name:'Start competition'})).toHaveAccessibleDescription('Choose between 2 and 4 playoff qualifiers.');
  fireEvent.change(cut,{target:{value:'3'}});
  fireEvent.change(rounds,{target:{value:''}});
  expect(rounds).toHaveValue(null);
  fireEvent.click(screen.getByRole('button',{name:'Start competition'}));
  expect(screen.getByRole('alert')).toHaveTextContent('Choose 1 to 100 season rounds.');
  fireEvent.change(rounds,{target:{value:'12'}});
  expect(rounds).toHaveValue(12);
  fireEvent.click(screen.getByRole('button',{name:'Start competition'}));
  await waitFor(()=>expect(api.createLocalCompetition).toHaveBeenCalledOnce());
  expect(vi.mocked(api.createLocalCompetition).mock.calls[0][0]).toMatchObject({
    type:'random_season_cut',cut:3,rounds:12,ai_speed:'1ms',max_turns:60,match_timeout_seconds:600,
  });
});

test.each([
  ['round_robin', 'Round-robin legs', '1.5'],
  ['round_robin', 'Round-robin legs', '0'],
  ['swiss', 'Swiss rounds', '3'],
  ['loss_limit', 'Losses before elimination', '101'],
])('invalid %s parameter %s=%s cannot launch', (kind, field, value) => {
  render(<CompetitionBuilder agents={agents} engineUrl="http://127.0.0.1:8787" ready onCreated={vi.fn()} />);
  fireEvent.click(screen.getByRole('button', {name: 'Add all agents'}));
  fireEvent.change(screen.getByLabelText('Competition format'), {target: {value: kind}});
  fireEvent.change(screen.getByLabelText(field), {target: {value}});
  expect(screen.getByRole('button', {name: 'Start competition'})).toBeEnabled();
  fireEvent.click(screen.getByRole('button', {name: 'Start competition'}));
  expect(screen.getByRole('alert')).toBeVisible();
  expect(screen.getByRole('alert')).toHaveFocus();
  expect(api.createLocalCompetition).not.toHaveBeenCalled();
});
