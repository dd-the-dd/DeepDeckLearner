import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';

import { LeagueHub } from './PlayerApp';
import * as api from './api';
import type { DeckSummary, Job, ProgrammaticAgent, LeagueConnection } from './api';

vi.mock('./api', async (importOriginal) => ({
  ...await importOriginal<typeof import('./api')>(),
  syncDeckWorkspace: vi.fn(), prepareLeagueDecks: vi.fn(), startJob: vi.fn(), stopJob:vi.fn(),
  loadLeagueQueue:vi.fn(),saveLeagueQueue:vi.fn(),appendLeagueQueueEntry:vi.fn(),controlLeagueQueue:vi.fn(),
}));

const localDeck: DeckSummary = { id: 'localdeck-123456789abc', name: 'Tempo draft', format: 'legacy', version: 1, colors: ['U'], playableCardCount: 60 };
const agent: ProgrammaticAgent = {
  schemaVersion: 'deepdeck-programmatic-agent/v1', id: 'programmatic-123456789abc', name: 'Tempo pilot', description: '', format: 'legacy', profile: 'balanced',
  deckVersionIds: [localDeck.id, 'localdeck-23456789abcd'], decks: [localDeck, { ...localDeck, id: 'localdeck-23456789abcd', name: 'Second draft' }], heuristics: {},
  strategyGraph: { version: 'v1', archetypeMix: { aggro: 0, tempo: 1, midrange: 0, control: 0, combo: 0 }, deckAdaptation: 1, weights: {}, temperature: 0, seed: 0, patterns: [] },
  cardPreferences: { prefer: [], avoid: [], holdForInteraction: [] }, strategyNotes: '', revisionReason: '', revision: 1, createdAt: '', updatedAt: '',
};
const account = { configured: true, valid: true, reason: 'Connected' };
const competition = { versionId: 'league-v1', name: 'Legacy League', status: 'active', format: 'legacy', timeControl: '1s' };
const production: LeagueConnection = { environment: 'production', reachable: true, apiUrl: 'https://www.deepdeckleague.com/api/v1', siteUrl: 'https://www.deepdeckleague.com', releaseProfile: 'production-proven', capabilities: ['league', 'mcp', 'decks.edit'], naturalCardSearchEnabled: false, reason: 'Production server reachable.' };

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.startJob).mockResolvedValue({ id: 'deployment-1' } as Job);
  vi.mocked(api.prepareLeagueDecks).mockResolvedValue({ deckVersionIds: ['published-v1'] });
  vi.mocked(api.loadLeagueQueue).mockResolvedValue({id:'queue',revision:0,status:'paused',strategy:'round_robin',entries:[],history:[],capacity:null,message:null});
  vi.mocked(api.saveLeagueQueue).mockResolvedValue({id:'queue',revision:1,status:'paused',strategy:'round_robin',entries:[],history:[],capacity:null,message:null});
  vi.mocked(api.appendLeagueQueueEntry).mockResolvedValue({id:'queue',revision:1,status:'paused',strategy:'round_robin',entries:[],history:[],capacity:null,message:null});
});
afterEach(cleanup);

test('a full account can queue an agent without starting it, with an independent overall lease',async()=>{
  const published={...agent,deckVersionIds:['published-v1'],decks:[{...localDeck,id:'published-v1'}]};
  render(<LeagueHub account={account} siteAccount={null} agents={[published]} competitions={[competition]} capacity={{status:'ready',limit:1,used:1,available:0,reason:'Server'}} jobs={[]} refresh={vi.fn()} openSettings={vi.fn()}/>);
  fireEvent.change(screen.getByLabelText('League'),{target:{value:competition.versionId}});
  fireEvent.change(screen.getByLabelText('Deployment duration'),{target:{value:'matches'}});
  fireEvent.change(screen.getByLabelText('Completed matches'),{target:{value:'7'}});
  expect(screen.getByRole('button',{name:'Send to League'})).toBeDisabled();
  fireEvent.click(screen.getByRole('button',{name:'Add to rotation'}));
  await waitFor(()=>expect(api.appendLeagueQueueEntry).toHaveBeenCalledWith(0,{agentId:agent.id,competitionVersionId:competition.versionId,deckVersionIds:['published-v1'],lease:{mode:'matches',matches:7}}));
  expect(api.saveLeagueQueue).not.toHaveBeenCalled();
  expect(api.startJob).not.toHaveBeenCalled();
  expect(screen.getByRole('status',{name:'League submission accepted'})).toHaveTextContent('No worker was started');
});

test('a running rotation accepts a new waiting agent without replacing existing turns',async()=>{
  vi.mocked(api.loadLeagueQueue).mockResolvedValue({id:'queue',revision:1,status:'running',strategy:'round_robin',entries:[],history:[],capacity:null,message:null});
  vi.mocked(api.appendLeagueQueueEntry).mockResolvedValue({id:'queue',revision:2,status:'running',strategy:'round_robin',entries:[],history:[],capacity:null,message:null});
  render(<LeagueHub account={account} siteAccount={account} agents={[agent]} competitions={[competition]} jobs={[]} refresh={vi.fn()} openSettings={vi.fn()}/>);
  fireEvent.change(screen.getByLabelText('League'),{target:{value:competition.versionId}});
  fireEvent.click(screen.getByRole('button',{name:'Publish & add to rotation'}));
  await waitFor(()=>expect(api.appendLeagueQueueEntry).toHaveBeenCalledWith(1,expect.objectContaining({agentId:agent.id})));
  expect(api.syncDeckWorkspace).toHaveBeenCalledTimes(2);
  expect(api.saveLeagueQueue).not.toHaveBeenCalled();expect(api.startJob).not.toHaveBeenCalled();
  expect(screen.getByRole('status',{name:'League submission accepted'})).toHaveTextContent('Existing matches keep playing');
  expect(screen.getByRole('status',{name:'League submission accepted'})).not.toHaveTextContent('No worker was started');
});

test.each([
  ['matches','Completed matches','3',{mode:'matches',matches:3}],
  ['duration','Duration in minutes','45',{mode:'duration',minutes:45}],
] as const)('the %s policy reaches the worker request without changing server capacity',async(mode,field,value,policy)=>{
  const published={...agent,deckVersionIds:['published-v1'],decks:[{...localDeck,id:'published-v1'}]};
  render(<LeagueHub account={account} siteAccount={null} agents={[published]} competitions={[competition]} capacity={{status:'ready',limit:37,used:7,available:30,reason:'Server'}} jobs={[]} refresh={vi.fn()} openSettings={vi.fn()}/>);
  fireEvent.change(screen.getByLabelText('League'),{target:{value:competition.versionId}});
  fireEvent.change(screen.getByLabelText('Deployment duration'),{target:{value:mode}});
  fireEvent.change(screen.getByLabelText(field),{target:{value}});
  fireEvent.click(screen.getByRole('button',{name:'Send to League'}));
  await waitFor(()=>expect(api.startJob).toHaveBeenCalledWith(expect.objectContaining({league_run:policy,connections:1,continuous:true})));
  expect(screen.getByRole('status',{name:'League places'})).toHaveTextContent('7 / 37 used');
});

test.each([['matches','Completed matches','1.5'],['duration','Duration in minutes','0'],['duration','Duration in minutes','10081']])('invalid %s limits block publishing and deployment', (mode,field,value)=>{
  render(<LeagueHub account={account} siteAccount={account} agents={[agent]} competitions={[competition]} jobs={[]} refresh={vi.fn()} openSettings={vi.fn()}/>);
  fireEvent.change(screen.getByLabelText('League'),{target:{value:competition.versionId}});
  fireEvent.change(screen.getByLabelText('Deployment duration'),{target:{value:mode}});
  fireEvent.change(screen.getByLabelText(field),{target:{value}});
  expect(screen.getByRole('alert')).toHaveTextContent('Choose a whole number');
  expect(screen.getByRole('button',{name:'Publish & send to League'})).toBeDisabled();
  expect(api.syncDeckWorkspace).not.toHaveBeenCalled();expect(api.startJob).not.toHaveBeenCalled();
});

test('finished bounded workers retain their reported count without claiming a freed server place',()=>{
  const finished={id:'finite',kind:'matchmaking.agent',status:'completed',label:'Finished pilot',logs:[],created_at:'2026-10-19T00:00:00Z',finished_at:'2026-10-19T01:00:00Z',details:{leagueRun:{completedEntries:2,activeEntries:0,maxMatches:3,durationSeconds:null}}} as unknown as Job;
  render(<LeagueHub account={account} siteAccount={null} agents={[agent]} competitions={[competition]} capacity={{status:'ready',limit:37,used:7,available:30,reason:'Server'}} jobs={[finished]} refresh={vi.fn()} openSettings={vi.fn()}/>);
  expect(screen.getByText('Recent League runs')).toBeInTheDocument();
  expect(screen.getByText('Worker finished')).toBeInTheDocument();
  expect(screen.getByText('Completed: 2 / 3')).toBeInTheDocument();
  expect(screen.getByRole('status',{name:'League places'})).toHaveTextContent('7 / 37 used');
  expect(api.startJob).not.toHaveBeenCalled();
});

test('a failed continuous run remains visible with its server reason and connection action',()=>{
  const failed={id:'denied',kind:'matchmaking.agent',status:'failed',label:'Denied pilot',logs:[],created_at:'2026-10-19T00:00:00Z',details:{leagueRun:{completedEntries:0,activeEntries:0,maxMatches:null,durationSeconds:null,lastError:'League request failed (HTTP 400). Deck is not in this competition.'}}} as unknown as Job;
  const connect=vi.fn();
  window.addEventListener('deepdeck:open-league-settings',connect);
  try {
    render(<LeagueHub account={account} siteAccount={null} agents={[agent]} competitions={[competition]} jobs={[failed]} refresh={vi.fn()} openSettings={vi.fn()}/>);
    expect(screen.getByRole('alert')).toHaveTextContent('HTTP 400');
    fireEvent.click(screen.getByRole('button',{name:'Open League connection settings'}));
    expect(connect).toHaveBeenCalledOnce();
    expect(api.startJob).not.toHaveBeenCalled();
  } finally {window.removeEventListener('deepdeck:open-league-settings',connect);}
});

describe('No-code League deployment', () => {
  test('shows Patreon information beside the server place count without treating it as a free membership', () => {
    render(<LeagueHub account={account} siteAccount={null} connection={production} agents={[agent]} competitions={[competition]} capacity={{status:'ready',limit:1,used:0,available:1,reason:'Server quota'}} jobs={[]} refresh={vi.fn()} openSettings={vi.fn()}/>);
    const places=screen.getByRole('status',{name:'League places'});
    expect(places).toHaveTextContent('0 / 1 used');
    expect(places).toHaveTextContent('Higher Patreon tiers include more simultaneous League agent places');
    expect(places).toHaveTextContent('Already supporting?');
    expect(places).not.toHaveTextContent(/free plan|pay again/i);
    expect(places.querySelector('a')).toHaveAttribute('href','https://www.deepdeckleague.com/support');
    expect(api.startJob).not.toHaveBeenCalled();
    expect(api.prepareLeagueDecks).not.toHaveBeenCalled();
  });

  test('does not present a missing capacity response as a reason to upgrade', () => {
    render(<LeagueHub account={account} siteAccount={null} connection={production} agents={[agent]} competitions={[competition]} capacity={{status:'unsupported',limit:null,used:null,available:null,reason:'Capacity not reported'}} jobs={[]} refresh={vi.fn()} openSettings={vi.fn()}/>);
    expect(screen.getByRole('status',{name:'League places'})).not.toHaveTextContent('Compare Patreon plans');
    expect(screen.getByRole('status',{name:'League places'})).toHaveTextContent('Not reported yet');
  });

  test('displays server capacity without an editable seat count or local tier rule', async () => {
    const published = { ...agent, deckVersionIds: ['published-v1'], decks: [{ ...localDeck, id: 'published-v1' }] };
    render(<LeagueHub account={account} siteAccount={null} agents={[published]} competitions={[competition]} capacity={{ status: 'ready', limit: 37, used: 7, available: 30, reason: 'Server quota' }} jobs={[]} refresh={vi.fn()} openSettings={vi.fn()} />);
    expect(screen.getByRole('status', { name: 'League places' })).toHaveTextContent('7 / 37 used');
    expect(screen.getByRole('status', { name: 'League places' })).toHaveTextContent('30 available');
    expect(screen.queryByRole('spinbutton')).toBeNull();
    expect(screen.queryByLabelText('Parallel seats')).toBeNull();
    fireEvent.change(screen.getByLabelText('League'), { target: { value: competition.versionId } });
    fireEvent.click(screen.getByRole('button', { name: 'Send to League' }));
    await waitFor(() => expect(api.startJob).toHaveBeenCalledOnce());
    expect(api.startJob).toHaveBeenCalledWith(expect.objectContaining({ connections: 1 }));
  });

  test('a full server account cannot launch another seat or publish unnecessary decks', () => {
    render(<LeagueHub account={account} siteAccount={account} agents={[agent]} competitions={[competition]} capacity={{ status: 'ready', limit: 4, used: 4, available: 0, reason: 'Server quota' }} jobs={[]} refresh={vi.fn()} openSettings={vi.fn()} />);
    fireEvent.change(screen.getByLabelText('League'), { target: { value: competition.versionId } });
    expect(screen.getByRole('button', { name: 'Publish & send to League' })).toBeDisabled();
    expect(screen.getByText(/All account places are in use/)).toBeVisible();
    expect(api.syncDeckWorkspace).not.toHaveBeenCalled();
  });

  test('explains which decks the agent may use and makes each selection explicit', () => {
    render(<LeagueHub account={account} siteAccount={account} agents={[agent]} competitions={[competition]} jobs={[]} refresh={vi.fn()} openSettings={vi.fn()} />);
    expect(screen.getByRole('group', { name: 'Decks for League matches' })).toBeVisible();
    expect(screen.getByText(/Your agent will use only the decks checked below/)).toBeVisible();
    expect(screen.queryByText('Eligible deck pool')).toBeNull();
    expect(screen.getByRole('checkbox', { name: 'Allow Tempo draft in League matches' })).toBeChecked();
    expect(screen.getByText('2 / 2 selected')).toBeVisible();
    fireEvent.click(screen.getByRole('checkbox', { name: 'Allow Second draft in League matches' }));
    expect(screen.getByText('Your agent will play this deck for this League run.')).toBeVisible();
    fireEvent.click(screen.getByRole('checkbox', { name: 'Allow Tempo draft in League matches' }));
    expect(screen.getByText('Select at least one deck before sending your agent.')).toBeVisible();
    expect(screen.getByRole('button', { name: 'Publish & send to League' })).toBeDisabled();
  });

  test('shows production format labels without offering inactive or incompatible leagues', async () => {
    const published = { ...agent, deckVersionIds: ['published-v1'], decks: [{ ...localDeck, id: 'published-v1' }] };
    render(<LeagueHub account={account} siteAccount={null} agents={[published]} competitions={[
      { ...competition, format: 'Legacy', name: 'Recorded Legacy Games' },
      { ...competition, versionId: 'commander-v1', format: 'Commander', name: 'Recorded Commander Games' },
      { ...competition, versionId: 'closed-v1', format: 'Legacy', name: 'Closed League', status: 'completed' },
    ]} jobs={[]} refresh={vi.fn()} openSettings={vi.fn()} />);
    expect(screen.getByRole('option', { name: 'Recorded Legacy Games · 1s' })).toBeVisible();
    expect(screen.queryByRole('option', { name: /Recorded Commander Games/ })).toBeNull();
    expect(screen.queryByRole('option', { name: /Closed League/ })).toBeNull();
    fireEvent.change(screen.getByLabelText('League'), { target: { value: competition.versionId } });
    fireEvent.click(screen.getByRole('button', { name: 'Send to League' }));
    await waitFor(() => expect(api.startJob).toHaveBeenCalledOnce());
    expect(api.startJob).toHaveBeenCalledWith(expect.objectContaining({ competition_version_id: 'league-v1', format: 'legacy' }));
  });

  test('matches Commander labels independently from Legacy', () => {
    render(<LeagueHub account={account} siteAccount={null} agents={[{ ...agent, format: 'commander' }]} competitions={[
      { ...competition, name: 'Legacy pool', format: 'Legacy' },
      { ...competition, versionId: 'commander-v1', name: 'Commander pool', format: ' Commander ' },
    ]} jobs={[]} refresh={vi.fn()} openSettings={vi.fn()} />);
    expect(screen.getByRole('option', { name: 'Commander pool · 1s' })).toBeVisible();
    expect(screen.queryByRole('option', { name: /Legacy pool/ })).toBeNull();
  });

  test('production messaging reflects the competition pool rather than staging-only AI slots', () => {
    render(<LeagueHub account={account} siteAccount={account} connection={production} agents={[agent]} competitions={[competition]} jobs={[]} refresh={vi.fn()} openSettings={vi.fn()} />);
    expect(screen.getByText(/This server validates eligible decks through its competition pool/)).toBeVisible();
    expect(screen.queryByText(/Selected decks use your account’s AI slots/)).toBeNull();
  });

  test('an invalid production key shows the actual server rejection instead of a network error', () => {
    render(<LeagueHub account={{ ...account, valid: false, reason: 'The saved key was rejected. Connect your production account.' }} siteAccount={null} connection={production} agents={[agent]} competitions={[competition]} jobs={[]} refresh={vi.fn()} openSettings={vi.fn()} />);
    expect(screen.getByText('Production League')).toBeVisible();
    expect(screen.getByText(/Connect your production account/)).toBeVisible();
    expect(screen.getByText('Agent key rejected')).toBeVisible();
    expect(screen.queryByText('Offline')).toBeNull();
  });
  test('publishes all attached local decks but only activates the selected pool', async () => {
    const refresh = vi.fn();
    render(<LeagueHub account={account} siteAccount={account} agents={[agent]} competitions={[competition]} jobs={[]} refresh={refresh} openSettings={vi.fn()} />);
    fireEvent.change(screen.getByLabelText('League'), { target: { value: competition.versionId } });
    fireEvent.click(screen.getAllByRole('checkbox')[1]);
    fireEvent.click(screen.getByRole('button', { name: 'Publish & send to League' }));
    await waitFor(() => expect(api.startJob).toHaveBeenCalledOnce());
    expect(api.syncDeckWorkspace).toHaveBeenNthCalledWith(1, localDeck.id, 'publish', false, localDeck.version);
    expect(api.syncDeckWorkspace).toHaveBeenNthCalledWith(2, agent.deckVersionIds[1], 'publish', false, agent.decks[1].version);
    expect(api.prepareLeagueDecks).toHaveBeenCalledWith([localDeck.id]);
    expect(api.startJob).toHaveBeenCalledWith(expect.objectContaining({ model_id: agent.id, deck_version_ids: [localDeck.id], continuous: true }));
    expect(vi.mocked(api.prepareLeagueDecks).mock.invocationCallOrder[0]).toBeLessThan(vi.mocked(api.startJob).mock.invocationCallOrder[0]);
    expect(refresh).toHaveBeenCalled();
  });

  test('requires account sign-in before publishing local decks', () => {
    const openSettings = vi.fn();
    render(<LeagueHub account={account} siteAccount={null} agents={[agent]} competitions={[competition]} jobs={[]} refresh={vi.fn()} openSettings={openSettings} />);
    fireEvent.change(screen.getByLabelText('League'), { target: { value: competition.versionId } });
    expect(screen.getByRole('button', { name: 'Publish & send to League' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Sign in to publish decks' }));
    expect(openSettings).toHaveBeenCalledOnce();
    expect(api.syncDeckWorkspace).not.toHaveBeenCalled();
  });

  test('a publication conflict leaves the worker stopped and shows an actionable error', async () => {
    vi.mocked(api.syncDeckWorkspace).mockRejectedValueOnce(new api.ApiError('The site draft changed. Download its changes in Decks.', 409));
    render(<LeagueHub account={account} siteAccount={account} agents={[agent]} competitions={[competition]} jobs={[]} refresh={vi.fn()} openSettings={vi.fn()} />);
    fireEvent.change(screen.getByLabelText('League'), { target: { value: competition.versionId } });
    fireEvent.click(screen.getByRole('button', { name: 'Publish & send to League' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Download its changes');
    expect(api.startJob).not.toHaveBeenCalled();
    expect(api.prepareLeagueDecks).not.toHaveBeenCalled();
  });

  test('a manually connected agent key can still use an already published public deck', async () => {
    const published = { ...agent, deckVersionIds: ['published-v1'], decks: [{ ...localDeck, id: 'published-v1' }] };
    render(<LeagueHub account={account} siteAccount={null} agents={[published]} competitions={[competition]} jobs={[]} refresh={vi.fn()} openSettings={vi.fn()} />);
    fireEvent.change(screen.getByLabelText('League'), { target: { value: competition.versionId } });
    fireEvent.click(screen.getByRole('button', { name: 'Send to League' }));
    await waitFor(() => expect(api.startJob).toHaveBeenCalledOnce());
    expect(api.syncDeckWorkspace).not.toHaveBeenCalled();
    expect(api.prepareLeagueDecks).not.toHaveBeenCalled();
  });

  test('accepted worker submission remains a success when refreshing the roster fails',async()=>{
    const published={...agent,deckVersionIds:['public'],decks:[{...localDeck,id:'public'}]};
    render(<LeagueHub account={account} siteAccount={null} agents={[published]} competitions={[competition]} jobs={[]} refresh={()=>Promise.reject(new Error('Offline refresh'))} openSettings={vi.fn()}/>);
    fireEvent.change(screen.getByLabelText('League'),{target:{value:competition.versionId}});
    fireEvent.click(screen.getByRole('button',{name:'Send to League'}));
    expect(await screen.findByRole('status',{name:'League submission accepted'})).toHaveTextContent('launch request accepted on this computer');
    expect(await screen.findByRole('alert')).toHaveTextContent('Do not resend');
    expect(api.startJob).toHaveBeenCalledOnce();
  });
  test('accepted launch shows roster refresh rather than still claiming it is joining the League',async()=>{
    let done!:()=>void;
    const published={...agent,deckVersionIds:['public'],decks:[{...localDeck,id:'public'}]};
    render(<LeagueHub account={account} siteAccount={null} agents={[published]} competitions={[competition]} jobs={[]} refresh={()=>new Promise<void>(resolve=>{done=resolve;})} openSettings={vi.fn()}/>);
    fireEvent.change(screen.getByLabelText('League'),{target:{value:competition.versionId}});
    fireEvent.click(screen.getByRole('button',{name:'Send to League'}));
    await screen.findByRole('status',{name:'League submission accepted'});
    expect(screen.getByRole('button',{name:'Refreshing roster…'})).toBeDisabled();
    done();
    await waitFor(()=>expect(screen.getByRole('button',{name:'Send to League'})).toBeEnabled());
  });

  test('a later publication conflict preserves confirmed publications and never starts a worker',async()=>{
    vi.mocked(api.syncDeckWorkspace).mockResolvedValueOnce({} as api.DeckWorkspace).mockRejectedValueOnce(new api.ApiError('Local revision changed; reopen Decks.',409));
    render(<LeagueHub account={account} siteAccount={account} agents={[agent]} competitions={[competition]} jobs={[]} refresh={vi.fn()} openSettings={vi.fn()}/>);
    fireEvent.change(screen.getByLabelText('League'),{target:{value:competition.versionId}});
    fireEvent.click(screen.getByRole('button',{name:'Publish & send to League'}));
    expect(await screen.findByRole('alert')).toHaveTextContent('1 deck was already published and remains on the site');
    expect(api.startJob).not.toHaveBeenCalled();expect(api.prepareLeagueDecks).not.toHaveBeenCalled();
    expect(api.syncDeckWorkspace).toHaveBeenCalledTimes(2);
  });

  test('deployment controls stay fixed while publishing and no duplicate submission is made',async()=>{
    let finish!:(value:api.DeckWorkspace)=>void;
    vi.mocked(api.syncDeckWorkspace).mockReturnValueOnce(new Promise(resolve=>{finish=resolve;}));
    render(<LeagueHub account={account} siteAccount={account} agents={[agent]} competitions={[competition]} jobs={[]} refresh={vi.fn()} openSettings={vi.fn()}/>);
    fireEvent.change(screen.getByLabelText('League'),{target:{value:competition.versionId}});
    fireEvent.click(screen.getByRole('button',{name:'Publish & send to League'}));
    expect(screen.getByLabelText('Agent')).toBeDisabled();expect(screen.getByLabelText('League')).toBeDisabled();
    for(const choice of screen.getAllByRole('checkbox'))expect(choice).toBeDisabled();
    finish({} as api.DeckWorkspace);
    await screen.findByRole('status',{name:'League submission accepted'});
    expect(api.startJob).toHaveBeenCalledOnce();
  });

  test('uncertain worker response asks to inspect the roster instead of assuming no worker exists',async()=>{
    vi.mocked(api.startJob).mockRejectedValueOnce(new TypeError('Connection lost'));
    const published={...agent,deckVersionIds:['public'],decks:[{...localDeck,id:'public'}]};
    render(<LeagueHub account={account} siteAccount={null} agents={[published]} competitions={[competition]} jobs={[]} refresh={vi.fn()} openSettings={vi.fn()}/>);
    fireEvent.change(screen.getByLabelText('League'),{target:{value:competition.versionId}});
    fireEvent.click(screen.getByRole('button',{name:'Send to League'}));
    expect(await screen.findByRole('alert')).toHaveTextContent("Could not confirm your agent's League launch");
    expect(api.startJob).toHaveBeenCalledOnce();
    expect(screen.queryByRole('status',{name:'League submission accepted'})).toBeNull();
  });

  test('unsupported capacity is explained without manufacturing an account limit',()=>{
    render(<LeagueHub account={account} siteAccount={account} agents={[agent]} competitions={[competition]} jobs={[]} refresh={vi.fn()} openSettings={vi.fn()} capacity={{status:'unsupported',limit:null,used:null,available:null,reason:'This production server does not report capacity yet.'}}/>);
    expect(screen.getByRole('status',{name:'League places'})).toHaveTextContent('This production server does not report capacity yet.');
    expect(screen.getByRole('status',{name:'League places'})).toHaveTextContent('Not reported yet');
  });

  test('missing mastered decks block publishing and worker creation',()=>{
    render(<LeagueHub account={account} siteAccount={account} agents={[{...agent,decks:[{...localDeck,unavailable:true}]}]} competitions={[competition]} jobs={[]} refresh={vi.fn()} openSettings={vi.fn()}/>);
    fireEvent.change(screen.getByLabelText('League'),{target:{value:competition.versionId}});
    expect(screen.getByRole('button',{name:'Publish & send to League'})).toBeDisabled();
    expect(screen.getByText(/A mastered deck is unavailable/)).toBeVisible();
    expect(api.syncDeckWorkspace).not.toHaveBeenCalled();expect(api.startJob).not.toHaveBeenCalled();
  });

  test('a confirmed local stop survives a failed refresh without pretending server capacity was freed',async()=>{
    const running={id:'worker',kind:'matchmaking.agent',label:'Tempo worker',status:'running',logs:[]} as unknown as Job;
    vi.mocked(api.stopJob).mockResolvedValueOnce({...running,status:'stopped'});
    render(<LeagueHub account={account} siteAccount={account} agents={[agent]} competitions={[competition]} jobs={[running]} refresh={()=>Promise.reject(new Error('Offline'))} openSettings={vi.fn()} capacity={{status:'ready',limit:1,used:1,available:0,reason:'Server'}}/>);
    fireEvent.click(screen.getByRole('button',{name:'Bring home'}));
    expect(await screen.findByText(/Tempo worker stopped locally/)).toBeVisible();
    expect(await screen.findByRole('alert')).toHaveTextContent('Do not stop it again');
    expect(screen.queryByRole('button',{name:'Bring home'})).toBeNull();
    expect(screen.getByRole('status',{name:'League places'})).toHaveTextContent('1 / 1 used');
    expect(api.stopJob).toHaveBeenCalledOnce();
  });

  test('a failed stop keeps the worker visible and offers a safe diagnostic instead of an unhandled rejection',async()=>{
    const running={id:'worker',kind:'matchmaking.agent',label:'Tempo worker',status:'running',logs:[]} as unknown as Job;
    vi.mocked(api.stopJob).mockRejectedValueOnce(new Error('Controller unavailable'));
    render(<LeagueHub account={account} siteAccount={account} agents={[agent]} competitions={[competition]} jobs={[running]} refresh={vi.fn()} openSettings={vi.fn()}/>);
    fireEvent.click(screen.getByRole('button',{name:'Bring home'}));
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not confirm that Tempo worker stopped');
    expect(screen.getByRole('button',{name:'Bring home'})).toBeEnabled();
    expect(api.stopJob).toHaveBeenCalledOnce();
  });
  test('a nonterminal stop response is not displayed as a confirmed stop',async()=>{
    const running={id:'worker',kind:'matchmaking.agent',label:'Tempo worker',status:'running',logs:[]} as unknown as Job;
    vi.mocked(api.stopJob).mockResolvedValueOnce(running);
    render(<LeagueHub account={account} siteAccount={account} agents={[agent]} competitions={[competition]} jobs={[running]} refresh={vi.fn()} openSettings={vi.fn()}/>);
    fireEvent.click(screen.getByRole('button',{name:'Bring home'}));
    expect(await screen.findByRole('alert')).toHaveTextContent('has not confirmed that the worker stopped');
    expect(screen.getByRole('button',{name:'Bring home'})).toBeEnabled();
    expect(screen.queryByText(/Tempo worker stopped locally/)).toBeNull();
  });
});
