import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import { PlayHub } from './PlayerApp';
import { ReplayPlaySettings } from './PlaySettings';
import * as api from './api';
import type { CapabilityStatus, DeckSummary, Job, ProgrammaticAgent } from './api';

vi.mock('./api', async (original) => ({ ...await original<typeof import('./api')>(), startJob: vi.fn() }));
const human: DeckSummary = { id: 'human', name: 'My local deck', format: 'legacy', version: 1, colors: ['U'], playableCardCount: 60 };
const mastered: DeckSummary = { ...human, id: 'mastered', name: 'Mastered combo' };
const commander: DeckSummary = { ...human, id: 'commander', name: 'Commander deck', format: 'commander', playableCardCount: 100 };
const agent = { id: 'programmatic-123456789abc', name: 'Legacy pilot', format: 'legacy', profile: 'balanced', revision: 2, deckVersionIds: [mastered.id], decks: [mastered] } as ProgrammaticAgent;
const commanderAgent = { ...agent, id: 'programmatic-23456789abcd', name: 'Commander pilot', format: 'commander', deckVersionIds: [commander.id], decks: [commander] } as ProgrammaticAgent;
const status = { engine: { healthy: true, url: 'http://127.0.0.1:8897' } } as CapabilityStatus;
function props() { return { status, agents: [agent, commanderAgent], decks: [human, mastered, commander], jobs: [], onStarted: vi.fn(), refresh: vi.fn(), openAgents: vi.fn() }; }
function pickHumanDeck() { fireEvent.change(within(document.querySelector('.seat-card.human') as HTMLElement).getByRole('combobox', { name: 'Deck' }), { target: { value: human.id } }); }

beforeEach(() => { vi.clearAllMocks(); vi.mocked(api.startJob).mockResolvedValue({ id: 'new-table', kind: 'playtest.table', status: 'running' } as Job); });
afterEach(cleanup);

test('a casual human table sends untimed defaults and preserves the human deck outside the AI pool', async () => {
  const callbacks = props(); render(<PlayHub {...callbacks} />); pickHumanDeck();
  const aiSeat = document.querySelector('.seat-card.agent') as HTMLElement;
  expect(within(aiSeat).queryByRole('option', { name: human.name })).toBeNull();
  expect(within(aiSeat).getByRole('option', { name: mastered.name })).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Start game' }));
  await waitFor(() => expect(api.startJob).toHaveBeenCalledWith(expect.objectContaining({ kind: 'playtest.table', engine_url: status.engine.url, ai_speed: '1ms', max_turns: 60, starting_seat: 'rotate', human_decision_timeout_seconds: null, match_timeout_seconds: null, seats: [{ kind: 'human', agent_id: '', deck_id: human.id }, { kind: 'agent', agent_id: agent.id, deck_id: mastered.id }] })));
  expect(callbacks.onStarted).toHaveBeenCalledOnce();
});

test('local play exposes the requested fast rhythms, match durations and turn presets',()=>{
  render(<PlayHub {...props()} />);
  const rhythm=screen.getByLabelText('Local AI rhythm');
  expect(rhythm).toHaveValue('1ms');
  expect(within(rhythm).getAllByRole('option').map(option=>option.getAttribute('value'))).toEqual(['1ms','10ms','100ms']);
  const turns=screen.getByLabelText('Turn limit per game');
  expect(turns).toHaveValue('60');
  expect(within(turns).getAllByRole('option').map(option=>option.getAttribute('value'))).toEqual(['20','30','40','60','100']);
  const durations=within(screen.getByLabelText('Match safety limit')).getAllByRole('option').map(option=>option.getAttribute('value'));
  expect(durations.filter(Boolean)).toEqual(['60','180','600']);
});

test('advanced setup sends the exact seed, opening seat and explicit safety limits', async () => {
  render(<PlayHub {...props()} />); pickHumanDeck(); fireEvent.click(screen.getByText('Playtest settings'));
  fireEvent.change(screen.getByLabelText(/^Shuffle seed/), { target: { value: '42' } });
  fireEvent.change(screen.getByLabelText(/^Opening seat/), { target: { value: '2' } });
  fireEvent.change(screen.getByLabelText(/^Turn limit per game/), { target: { value: '100' } });
  fireEvent.change(screen.getByLabelText(/^Human inactivity limit/), { target: { value: '180' } });
  fireEvent.change(screen.getByLabelText(/^Match safety limit/), { target: { value: '180' } });
  fireEvent.click(screen.getByRole('button', { name: 'Start game' }));
  await waitFor(() => expect(api.startJob).toHaveBeenCalledWith(expect.objectContaining({ seed: 42, starting_seat: 2, max_turns: 100, human_decision_timeout_seconds: 180, match_timeout_seconds: 180 })));
});

test('Commander batches remain all AI, bounded and limited to mastered decks', async () => {
  render(<PlayHub {...props()} initialBatchSize={20} />);
  fireEvent.change(screen.getByLabelText('Format'), { target: { value: 'commander' } });
  await waitFor(() => expect(document.querySelectorAll('.seat-card.agent')).toHaveLength(4));
  expect(document.querySelectorAll('.seat-card.human')).toHaveLength(0);
  expect(screen.queryByLabelText(/^Human inactivity limit/)).toBeNull();
  fireEvent.change(screen.getByLabelText('Matches'), { target: { value: '7' } });
  fireEvent.click(screen.getByRole('button', { name: 'Run batch' }));
  await waitFor(() => expect(api.startJob).toHaveBeenCalledWith(expect.objectContaining({ kind: 'playtest.batch', format: 'commander', games: 7, match_timeout_seconds: 600, seats: Array(4).fill({ kind: 'agent', agent_id: commanderAgent.id, deck_id: 'random' }) })));
});

test('each seat can independently become human without replacing another player', async () => {
  render(<PlayHub {...props()} />); pickHumanDeck();
  const second = document.querySelectorAll('.seat-card')[1] as HTMLElement;
  fireEvent.click(within(second).getByRole('button', { name: 'Player' }));
  expect(document.querySelectorAll('.seat-card.human')).toHaveLength(2);
  fireEvent.click(screen.getByRole('button', { name: 'Start game' }));
  await waitFor(() => expect(api.startJob).toHaveBeenCalledWith(expect.objectContaining({ seats: [{ kind: 'human', agent_id: '', deck_id: human.id }, { kind: 'human', agent_id: '', deck_id: mastered.id }] })));
  expect(vi.mocked(api.startJob).mock.calls[0][0]).not.toHaveProperty('model_id');
});

test('changing format clears a human deck that cannot play that format', async () => {
  render(<PlayHub {...props()} />); pickHumanDeck();
  fireEvent.change(screen.getByLabelText('Format'), { target: { value: 'commander' } });
  await waitFor(() => expect(document.querySelectorAll('.seat-card')).toHaveLength(4));
  expect(within(document.querySelector('.seat-card.human') as HTMLElement).getByRole('combobox', { name: 'Deck' })).toHaveValue('');
  fireEvent.click(screen.getByRole('button', { name: 'Start game' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('Choose or import a deck');
  expect(api.startJob).not.toHaveBeenCalled();
});

test('reducing a four-seat table preserves displaced humans in available AI seats', async () => {
  render(<PlayHub {...props()} preferredAgentId={commanderAgent.id} />);
  await waitFor(() => expect(document.querySelectorAll('.seat-card')).toHaveLength(4));
  const last = document.querySelectorAll('.seat-card')[3] as HTMLElement;
  fireEvent.click(within(last).getByRole('button', { name: 'Player' }));
  fireEvent.change(within(last).getByRole('combobox', { name: 'Deck' }), { target: { value: commander.id } });
  fireEvent.change(screen.getByLabelText('Format'), { target: { value: 'legacy' } });
  await waitFor(() => expect(document.querySelectorAll('.seat-card')).toHaveLength(2));
  expect(document.querySelectorAll('.seat-card.human')).toHaveLength(2);
  expect(document.querySelectorAll('.seat-card')[0]).toHaveClass('human');
  expect(within(document.querySelector('.seat-card.human') as HTMLElement).getByRole('combobox', { name: 'Deck' })).toHaveValue('');
});

test('a missing agent offers an explicit route to the workshop', () => {
  const callbacks = props(); render(<PlayHub {...callbacks} agents={[commanderAgent]} />);
  expect(screen.getByText('No Legacy agent yet')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Start game' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Open Agents' }));
  expect(callbacks.openAgents).toHaveBeenCalledOnce();
});

test('an all-human table can start from the selector with no agent installed',async()=>{
  render(<PlayHub {...props()} agents={[]}/>); pickHumanDeck();
  const second=document.querySelectorAll('.seat-card')[1] as HTMLElement;
  fireEvent.click(within(second).getByRole('button',{name:'Player'}));
  fireEvent.change(within(second).getByRole('combobox',{name:'Deck'}),{target:{value:mastered.id}});
  expect(screen.getByRole('button',{name:'Start game'})).toBeEnabled();
  fireEvent.click(screen.getByRole('button',{name:'Start game'}));
  await waitFor(()=>expect(api.startJob).toHaveBeenCalledOnce());
  expect(vi.mocked(api.startJob).mock.calls[0][0].seats).toEqual([
    {kind:'human',agent_id:'',deck_id:human.id},
    {kind:'human',agent_id:'',deck_id:mastered.id},
  ]);
});

test('an unavailable mastered deck is explained before a game starts', async () => {
  render(<PlayHub {...props()} agents={[{ ...agent, decks: [{ ...mastered, unavailable: true }] }]} />); pickHumanDeck();
  fireEvent.click(screen.getByRole('button', { name: 'Start game' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('Re-import it in Decks');
  expect(api.startJob).not.toHaveBeenCalled();
});

test('invalid seeds and fractional batch sizes are rejected without launching', async () => {
  const view = render(<PlayHub {...props()} />); pickHumanDeck();
  fireEvent.change(screen.getByLabelText(/^Shuffle seed/), { target: { value: 'not-a-seed' } });
  fireEvent.click(screen.getByRole('button', { name: 'Start game' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('whole-number seed');
  view.unmount(); render(<PlayHub {...props()} initialBatchSize={10} />);
  fireEvent.change(screen.getByLabelText('Matches'), { target: { value: '1.5' } });
  fireEvent.click(screen.getByRole('button', { name: 'Run batch' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('whole number of matches');
  expect(api.startJob).not.toHaveBeenCalled();
});

test('replays expose actual setup and saved agent versions, without inventing settings for older games', () => {
  const view = render(<ReplayPlaySettings metadata={{ seed: 42, startingSeat: 2, agentVersions: [{ name: 'Combo pilot', revision: 7 }], playSettings: { maxTurns: 80, humanDecisionTimeoutSeconds: null, matchTimeoutSeconds: 1800 } }} />);
  fireEvent.click(screen.getByText('Match setup & limits'));
  expect(screen.getByText('42')).toBeVisible(); expect(screen.getByText('Seat 2')).toBeVisible();
  expect(screen.getByText('Combo pilot · v7')).toBeVisible(); expect(screen.getByText('None')).toBeVisible();
  view.rerender(<ReplayPlaySettings metadata={{ source: 'league' }} />);
  expect(screen.queryByText('Match setup & limits')).toBeNull();
});

const openBatch = () => fireEvent.click(screen.getByRole('button', {name: /AI batch Run matches/}));
const openTable = () => fireEvent.click(screen.getByRole('button', {name: /Play a game You or your agents/}));

test('returning from a Commander batch restores the human Legacy table and its untimed setup', async () => {
  render(<PlayHub {...props()} />); pickHumanDeck();
  fireEvent.change(screen.getByLabelText(/^Shuffle seed/), {target: {value: '42'}});
  fireEvent.change(screen.getByLabelText(/^Human inactivity limit/), {target: {value: '180'}});
  fireEvent.change(screen.getByLabelText(/^Turn limit per game/), {target: {value: '100'}});
  openBatch();
  expect(document.querySelectorAll('.seat-card.human')).toHaveLength(0);
  fireEvent.change(screen.getByLabelText('Format'), {target: {value: 'commander'}});
  fireEvent.change(screen.getByLabelText(/^Shuffle seed/), {target: {value: '99'}});
  fireEvent.change(screen.getByLabelText(/^Match safety limit/), {target: {value: '180'}});
  openTable();
  expect(screen.getByLabelText('Format')).toHaveValue('legacy');
  expect(document.querySelectorAll('.seat-card')).toHaveLength(2);
  expect(document.querySelectorAll('.seat-card.human')).toHaveLength(1);
  expect(within(document.querySelector('.seat-card.human') as HTMLElement).getByRole('combobox', {name: 'Deck'})).toHaveValue(human.id);
  expect(screen.getByLabelText(/^Shuffle seed/)).toHaveValue('42');
  expect(screen.getByLabelText(/^Human inactivity limit/)).toHaveValue('180');
  expect(screen.getByLabelText(/^Match safety limit/)).toHaveValue('');
  expect(screen.getByLabelText(/^Turn limit per game/)).toHaveValue('100');
  fireEvent.click(screen.getByRole('button', {name: 'Start game'}));
  await waitFor(() => expect(api.startJob).toHaveBeenCalledWith(expect.objectContaining({
    format: 'legacy', seed: 42, max_turns: 100, human_decision_timeout_seconds: 180, match_timeout_seconds: null,
    seats: [{kind: 'human', agent_id: '', deck_id: human.id}, {kind: 'agent', agent_id: agent.id, deck_id: mastered.id}],
  })));
});

test('batch remembers its own seats, format, match count and limits through table and competition menus', async () => {
  render(<PlayHub {...props()} initialBatchSize={10} />);
  fireEvent.change(screen.getByLabelText('Format'), {target: {value: 'commander'}});
  fireEvent.change(screen.getByLabelText('Matches'), {target: {value: '7'}});
  fireEvent.change(screen.getByLabelText(/^Shuffle seed/), {target: {value: '99'}});
  fireEvent.change(screen.getByLabelText(/^Match safety limit/), {target: {value: '180'}});
  const fourth = document.querySelectorAll('.seat-card')[3] as HTMLElement;
  fireEvent.change(within(fourth).getByRole('combobox', {name: 'Deck'}), {target: {value: commander.id}});
  openTable();
  expect(screen.getByLabelText('Format')).toHaveValue('legacy');
  expect(document.querySelectorAll('.seat-card.human')).toHaveLength(1);
  fireEvent.click(screen.getByRole('button', {name: /Organize a competition/}));
  fireEvent.click(screen.getByRole('button', {name: 'Simple AI batch'}));
  expect(screen.getByLabelText('Format')).toHaveValue('commander');
  expect(document.querySelectorAll('.seat-card.agent')).toHaveLength(4);
  expect(screen.getByLabelText('Matches')).toHaveValue(7);
  expect(screen.getByLabelText(/^Shuffle seed/)).toHaveValue('99');
  expect(screen.getByLabelText(/^Match safety limit/)).toHaveValue('180');
  expect(within(document.querySelectorAll('.seat-card')[3] as HTMLElement).getByRole('combobox', {name: 'Deck'})).toHaveValue(commander.id);
  fireEvent.click(screen.getByRole('button', {name: 'Run batch'}));
  await waitFor(() => expect(api.startJob).toHaveBeenCalledWith(expect.objectContaining({format: 'commander', games: 7, seed: 99, match_timeout_seconds: 180,
    seats: [0,1,2,3].map(i => ({kind: 'agent', agent_id: commanderAgent.id, deck_id: i === 3 ? commander.id : 'random'})),
  })));
});

test('remembered seats are revalidated after their decks and agents disappear in the background', () => {
  const callbacks = props();
  const view = render(<PlayHub {...callbacks} />); pickHumanDeck();
  openBatch();
  view.rerender(<PlayHub {...callbacks} agents={[commanderAgent]} decks={[commander]} />);
  openTable();
  expect(within(document.querySelector('.seat-card.human') as HTMLElement).getByRole('combobox', {name: 'Deck'})).toHaveValue('');
  expect(screen.getByRole('button', {name: 'Start game'})).toBeDisabled();
  expect(api.startJob).not.toHaveBeenCalled();
});

test('a mode switch clears stale launch errors without overwriting the other mode settings', async () => {
  render(<PlayHub {...props()} />); pickHumanDeck();
  fireEvent.change(screen.getByLabelText(/^Shuffle seed/), {target: {value: 'invalid'}});
  fireEvent.click(screen.getByRole('button', {name: 'Start game'}));
  expect(await screen.findByRole('alert')).toHaveTextContent('whole-number seed');
  openBatch();
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  expect(screen.getByLabelText(/^Shuffle seed/)).toHaveValue('');
  openTable();
  expect(screen.getByLabelText(/^Shuffle seed/)).toHaveValue('invalid');
});

function labelledPool() {
  const tempo={...mastered,tags:['tempo'],collections:['test']};
  const combo={...mastered,id:'combo-deck',name:'Combo deck',tags:['combo'],collections:['test']};
  const blue={...agent,tags:['blue'],collections:['club'],decks:[tempo]};
  const red={...agent,id:'red-pilot',name:'Red combo pilot',tags:['red'],collections:['club'],deckVersionIds:[combo.id],decks:[combo]};
  return {agents:[blue,red],decks:[{...human,tags:['human-only'],collections:[]},tempo,combo],blue,red};
}

test('random selection preview counts only agents who can play a deck matching the current filters', async()=>{
  const pool=labelledPool();render(<PlayHub {...props()} agents={pool.agents} decks={pool.decks}/>);pickHumanDeck();
  const seat=document.querySelector('.seat-card.agent') as HTMLElement;
  fireEvent.change(within(seat).getByRole('combobox',{name:'Agent'}),{target:{value:'random'}});
  const preview=within(seat).getByLabelText('Seat 2 random selection');
  expect(preview).toHaveTextContent('2 playable agents · 2 available decks');
  fireEvent.change(within(seat).getByRole('combobox',{name:'Deck tag'}),{target:{value:'combo'}});
  expect(preview).toHaveTextContent('1 playable agent · 1 available deck');
  expect(within(preview).getByText('Red combo pilot')).toBeInTheDocument();
  expect(within(preview).queryByText(agent.name)).toBeNull();
  fireEvent.click(screen.getByRole('button',{name:'Start game'}));
  await waitFor(()=>expect(api.startJob).toHaveBeenCalledWith(expect.objectContaining({seats:[
    {kind:'human',agent_id:'',deck_id:human.id},
    {kind:'agent',agent_id:'random',deck_id:'random',deck_tags:['combo']},
  ]})));
});

test('an empty joint pool is explained before launch and filters are cleared only on explicit request',()=>{
  const pool=labelledPool();render(<PlayHub {...props()} agents={pool.agents} decks={pool.decks}/>);pickHumanDeck();
  const seat=document.querySelector('.seat-card.agent') as HTMLElement;
  fireEvent.change(within(seat).getByRole('combobox',{name:'Agent'}),{target:{value:'random'}});
  fireEvent.change(within(seat).getByRole('combobox',{name:'Deck tag'}),{target:{value:'combo'}});
  fireEvent.change(within(seat).getByRole('combobox',{name:'Agent tag'}),{target:{value:'blue'}});
  const preview=within(seat).getByLabelText('Seat 2 random selection');
  expect(preview).toHaveAttribute('open');
  expect(within(preview).getByRole('status')).toHaveTextContent('No available mastered deck matches this pool');
  expect(within(preview).getByRole('status')).toHaveTextContent('not widened automatically');
  expect(screen.getByRole('button',{name:'Start game'})).toBeDisabled();
  expect(screen.getByText('Seat 2 needs a playable pool')).toBeInTheDocument();
  expect(within(seat).getByRole('combobox',{name:'Agent tag'})).toHaveValue('blue');
  expect(within(seat).getByRole('combobox',{name:'Deck tag'})).toHaveValue('combo');
  expect(within(seat).getByRole('option',{name:'combo · not in current pool'})).toBeInTheDocument();
  expect(api.startJob).not.toHaveBeenCalled();
  fireEvent.click(within(preview).getByRole('button',{name:'Clear selection filters'}));
  expect(within(seat).getByRole('combobox',{name:'Agent tag'})).toHaveValue('');
  expect(within(seat).getByRole('combobox',{name:'Deck tag'})).toHaveValue('');
  expect(preview).toHaveTextContent('2 playable agents · 2 available decks');
  expect(screen.getByRole('button',{name:'Start game'})).toBeEnabled();
});

test('human random preview can include a local deck no agent masters and preserves its filter in the request',async()=>{
  const pool=labelledPool();render(<PlayHub {...props()} agents={pool.agents} decks={pool.decks}/>);
  const humanSeat=document.querySelector('.seat-card.human') as HTMLElement;
  fireEvent.change(within(humanSeat).getByRole('combobox',{name:'Deck tag'}),{target:{value:'human-only'}});
  const preview=within(humanSeat).getByLabelText('Seat 1 random selection');
  expect(preview).toHaveTextContent('Random deck · 1 available deck');
  expect(within(preview).getByText(human.name)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:'Start game'}));
  await waitFor(()=>expect(api.startJob).toHaveBeenCalledWith(expect.objectContaining({seats:[
    {kind:'human',agent_id:'',deck_id:'random',deck_tags:['human-only']},
    {kind:'agent',agent_id:agent.id,deck_id:mastered.id},
  ]})));
});
