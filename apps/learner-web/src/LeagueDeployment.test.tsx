import {cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {afterEach, beforeEach, expect, test, vi} from 'vitest';
import {LeagueHub} from './PlayerApp';
import {poolAgentStatus} from './AgentLeaguePool';
import * as api from './api';

vi.mock('./api', async original => ({
  ...await original<typeof import('./api')>(),
  syncDeckWorkspace:vi.fn(), prepareLeagueDecks:vi.fn(), startJob:vi.fn(),
  loadLeagueQueue:vi.fn(), saveLeagueQueue:vi.fn(), appendLeagueQueueEntry:vi.fn(),
  controlLeagueQueue:vi.fn(), removeLeagueQueueEntry:vi.fn(),
}));
const account={configured:true,valid:true,reason:'Connected'};
const competition={versionId:'legacy-v1',name:'Legacy League',status:'active',format:'Legacy',timeControl:'1s'};
const deck:api.DeckSummary={id:'published-v1',name:'Tempo',format:'legacy',version:1,colors:['U'],playableCardCount:60};
const agent:api.ProgrammaticAgent={
  schemaVersion:'deepdeck-programmatic-agent/v1',id:'pilot-1',name:'Tempo pilot',description:'',format:'legacy',profile:'balanced',
  deckVersionIds:[deck.id],decks:[deck],heuristics:{},
  strategyGraph:{version:'v1',archetypeMix:{aggro:0,tempo:1,midrange:0,control:0,combo:0},deckAdaptation:1,weights:{},temperature:0,seed:0,patterns:[]},
  cardPreferences:{prefer:[],avoid:[],holdForInteraction:[]},strategyNotes:'',revisionReason:'',revision:1,createdAt:'',updatedAt:'',
};
let queue:api.LeagueQueue;
const entry=(id='pilot-1'):api.LeagueQueueEntry=>({id:`entry-${id}`,agentId:id,name:id==='pilot-1'?'Tempo pilot':'Second pilot',format:'legacy',competitionVersionId:competition.versionId,deckVersionIds:[deck.id],lease:{mode:'continuous'},completedMatches:0,elapsedSeconds:0,state:'queued',error:null,turn:null});
function show(overrides:Partial<React.ComponentProps<typeof LeagueHub>>={}) {
  return render(<LeagueHub account={account} siteAccount={null} agents={[agent]} competitions={[competition]} jobs={[]} refresh={vi.fn()} openSettings={vi.fn()} {...overrides}/>);
}
async function choose(id=agent.id,league=competition.versionId) {
  await waitFor(()=>expect(screen.getByLabelText('Agent')).toBeEnabled());
  fireEvent.change(screen.getByLabelText('Agent'),{target:{value:id}});
  fireEvent.change(screen.getByLabelText('League'),{target:{value:league}});
}
beforeEach(()=>{
  vi.resetAllMocks();
  queue={id:'queue',revision:0,status:'paused',strategy:'round_robin',entries:[],history:[],capacity:null,message:null};
  vi.mocked(api.loadLeagueQueue).mockImplementation(async()=>queue);
  vi.mocked(api.prepareLeagueDecks).mockResolvedValue({deckVersionIds:[deck.id]});
  vi.mocked(api.appendLeagueQueueEntry).mockImplementation(async(revision,draft)=>{
    expect(revision).toBe(queue.revision);
    queue={...queue,revision:revision+1,entries:[...queue.entries,{...entry(draft.agentId),...draft}]};return queue;
  });
  vi.mocked(api.controlLeagueQueue).mockImplementation(async(action,revision)=>{
    expect(revision).toBe(queue.revision);
    queue={...queue,revision:revision+1,status:action==='resume'?'running':'paused'};return queue;
  });
});
afterEach(cleanup);

test('first row is blank and mounting never publishes, appends or launches',async()=>{
  show();await waitFor(()=>expect(screen.getByLabelText('Agent')).toBeEnabled());
  expect(screen.getByLabelText('Agent')).toHaveValue('');
  expect(screen.getByLabelText('League')).toBeDisabled();
  expect(screen.getByRole('button',{name:'Resume pool'})).toBeDisabled();
  expect(api.appendLeagueQueueEntry).not.toHaveBeenCalled();expect(api.syncDeckWorkspace).not.toHaveBeenCalled();expect(api.startJob).not.toHaveBeenCalled();
  expect(screen.queryByText('Limit replay storage')).toBeNull();expect(screen.queryByLabelText('Deployment duration')).toBeNull();
});
test('selecting agent and League appends once, then resumes using acknowledged revision',async()=>{
  show();await choose();
  await waitFor(()=>expect(api.controlLeagueQueue).toHaveBeenCalledWith('resume',1));
  expect(api.appendLeagueQueueEntry).toHaveBeenCalledExactlyOnceWith(0,{agentId:agent.id,competitionVersionId:competition.versionId,deckVersionIds:[deck.id],lease:{mode:'continuous'}});
  expect(await screen.findByText('Waiting for a local worker')).toBeInTheDocument();
  expect(screen.getByLabelText('Agent')).toHaveValue('');expect(api.startJob).not.toHaveBeenCalled();expect(api.saveLeagueQueue).not.toHaveBeenCalled();
});
test('full account can append another agent without replacing its existing pool',async()=>{
  queue={...queue,status:'running',revision:8,entries:[entry()]};
  const second={...agent,id:'pilot-2',name:'Second pilot'};
  show({agents:[agent,second],capacity:{status:'ready',limit:1,used:1,available:0,reason:'Server'}});
  await choose(second.id);
  await waitFor(()=>expect(api.appendLeagueQueueEntry).toHaveBeenCalledOnce());
  expect(queue.entries.map(e=>e.agentId)).toEqual(['pilot-1','pilot-2']);
  expect(screen.getAllByText('Waiting for a local worker')).toHaveLength(2);
  expect(api.controlLeagueQueue).not.toHaveBeenCalled();expect(api.startJob).not.toHaveBeenCalled();
});
test('League dropdown uses the selected agent format, case-insensitively',async()=>{
  show({competitions:[competition,{...competition,versionId:'cmd',name:'Commander League',format:'Commander'},{...competition,versionId:'closed',name:'Closed League',status:'closed'}]});
  await waitFor(()=>expect(screen.getByLabelText('Agent')).toBeEnabled());
  fireEvent.change(screen.getByLabelText('Agent'),{target:{value:agent.id}});
  expect(screen.getByRole('option',{name:/Legacy League/})).toBeInTheDocument();
  expect(screen.queryByRole('option',{name:/Commander League|Closed League/})).toBeNull();
});
test('local drafts require explicit publication consent before joining',async()=>{
  const draft={...agent,deckVersionIds:['localdeck-abc'],decks:[{...deck,id:'localdeck-abc'}]};
  show({agents:[draft],siteAccount:account});await choose();
  expect(api.syncDeckWorkspace).not.toHaveBeenCalled();expect(api.appendLeagueQueueEntry).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button',{name:'Publish decks & join'}));
  await waitFor(()=>expect(api.appendLeagueQueueEntry).toHaveBeenCalledOnce());
  expect(api.syncDeckWorkspace).toHaveBeenCalledWith('localdeck-abc','publish',false,1);
  expect(api.prepareLeagueDecks).toHaveBeenCalledWith(['localdeck-abc']);
});
test('API key alone does not authorize publication of private drafts',async()=>{
  const openSettings=vi.fn();show({agents:[{...agent,deckVersionIds:['localdeck-abc'],decks:[{...deck,id:'localdeck-abc'}]}],openSettings});await choose();
  fireEvent.click(screen.getByRole('button',{name:'Sign in to publish decks'}));
  expect(openSettings).toHaveBeenCalledOnce();expect(api.appendLeagueQueueEntry).not.toHaveBeenCalled();
});
test('missing mastered deck blocks append and publication',async()=>{
  show({agents:[{...agent,decks:[{...deck,unavailable:true}]}]});await choose();
  expect(screen.getByRole('alert')).toHaveTextContent('mastered decks');
  expect(api.appendLeagueQueueEntry).not.toHaveBeenCalled();expect(api.syncDeckWorkspace).not.toHaveBeenCalled();
});
test('deck preparation failure does not append and requires explicit reconciliation',async()=>{
  vi.mocked(api.prepareLeagueDecks).mockRejectedValueOnce(new api.ApiError('Deck changed on site',409));
  show({siteAccount:account});await choose();
  expect(await screen.findByRole('alert')).toHaveTextContent('Deck changed on site');
  expect(api.appendLeagueQueueEntry).not.toHaveBeenCalled();expect(screen.getByLabelText('Agent')).toBeDisabled();
});
test('uncertain append never retries automatically, even after error dismissal',async()=>{
  vi.mocked(api.appendLeagueQueueEntry).mockRejectedValueOnce(new TypeError('Connection lost'));
  show();await choose();
  expect(await screen.findByRole('alert')).toHaveTextContent('could not be confirmed');
  expect(screen.getByLabelText('Agent')).toBeDisabled();
  fireEvent.click(screen.getByRole('button',{name:'Dismiss League pool error'}));
  expect(screen.getByLabelText('Agent')).toBeDisabled();expect(api.appendLeagueQueueEntry).toHaveBeenCalledOnce();
  fireEvent.click(screen.getByRole('button',{name:'Refresh League'}));
  await waitFor(()=>expect(screen.getByLabelText('Agent')).toBeEnabled());
  expect(api.appendLeagueQueueEntry).toHaveBeenCalledOnce();
});
test('accepted append stays visible if resume cannot be confirmed',async()=>{
  vi.mocked(api.controlLeagueQueue).mockRejectedValueOnce(new TypeError('Lost response'));
  show();await choose();
  expect(await screen.findByRole('alert')).toHaveTextContent('Agent added; pool activation could not be confirmed');
  expect(screen.getByText('Paused')).toBeInTheDocument();expect(api.appendLeagueQueueEntry).toHaveBeenCalledOnce();
});
test('library refresh failure never turns acknowledged addition into a resubmission',async()=>{
  show({refresh:()=>Promise.reject(new Error('Library unavailable'))});await choose();
  expect(await screen.findByText(/The library could not refresh/)).toBeInTheDocument();
  expect(api.appendLeagueQueueEntry).toHaveBeenCalledOnce();expect(screen.getByText('Waiting for a local worker')).toBeInTheDocument();
});
test('server alone defines places and no editable local cap exists',async()=>{
  show({capacity:{status:'ready',limit:37,used:7,available:30,reason:'Server quota'}});
  expect(screen.getByRole('status',{name:'League places'})).toHaveTextContent('7 / 37 used');
  expect(screen.queryByRole('spinbutton')).toBeNull();
});
test('unverified capacity is never advertised as a plan limit',()=>{
  show({capacity:{status:'unsupported',limit:null,used:null,available:null,reason:'Capacity not reported'}});
  expect(screen.getByRole('status',{name:'League places'})).toHaveTextContent('Not verified');
  expect(screen.getByRole('status',{name:'League places'})).not.toHaveTextContent('Compare Patreon plans');
});
test('active agent cannot be removed or offered again',async()=>{
  queue={...queue,status:'running',entries:[{...entry(),state:'playing',turn:{dispatchId:'d',sequence:1,jobId:'job',createdAt:''}}]};show();
  expect(await screen.findByRole('button',{name:'Remove Tempo pilot'})).toBeDisabled();
  expect(screen.queryByRole('option',{name:/Tempo pilot/})).toBeNull();
  expect(api.removeLeagueQueueEntry).not.toHaveBeenCalled();
});
test('removal is a scoped revision-checked entry operation, not pool replacement',async()=>{
  queue={...queue,entries:[entry()]};vi.mocked(api.removeLeagueQueueEntry).mockResolvedValue({...queue,entries:[],revision:1});show();
  fireEvent.click(await screen.findByRole('button',{name:'Remove Tempo pilot'}));
  await waitFor(()=>expect(api.removeLeagueQueueEntry).toHaveBeenCalledWith('entry-pilot-1',0));
  expect(api.saveLeagueQueue).not.toHaveBeenCalled();
});
test('a pausing pool does not get automatically resumed by an addition',async()=>{
  queue={...queue,status:'pausing'};show();await choose();
  await waitFor(()=>expect(api.appendLeagueQueueEntry).toHaveBeenCalledOnce());
  expect(api.controlLeagueQueue).not.toHaveBeenCalled();
});
test('local turn is not confused with server admission or competition',()=>{
  const playing={...entry(),state:'playing' as const,turn:{dispatchId:'d',sequence:1,jobId:'job',createdAt:''}};
  const worker={id:'job',kind:'matchmaking.agent',status:'running',details:{leagueRun:{activeEntries:0,completedEntries:0,maxMatches:null,durationSeconds:null}}} as api.Job;
  expect(poolAgentStatus(playing,[])).toBe('Checking local worker');
  expect(poolAgentStatus(playing,[worker])).toBe('Connecting to League');
  expect(poolAgentStatus(playing,[{...worker,details:{leagueRun:{...worker.details!.leagueRun!,serverRegistered:true}}}])).toBe('Connected — checking tournament');
  expect(poolAgentStatus(playing,[{...worker,details:{leagueRun:{...worker.details!.leagueRun!,activeEntries:1}}}])).toBe('Competition status unavailable');
});

test('one grid distinguishes paused, local worker, registration, running match and between-round waits',async()=>{
  const playing={...entry(),state:'playing' as const,turn:{dispatchId:'d',sequence:1,jobId:'job',createdAt:''}};
  const report={completedEntries:0,activeEntries:1,maxMatches:null,durationSeconds:null,serverRegistered:true,eventRegistered:true,phase:'registration' as const};
  const worker={id:'job',kind:'matchmaking.agent',status:'running',details:{leagueRun:report}} as api.Job;
  expect(poolAgentStatus(entry(),[],queue)).toBe('Paused');
  expect(poolAgentStatus(playing,[{...worker,status:'queued'}])).toBe('Waiting for a local worker');
  expect(poolAgentStatus(playing,[worker])).toBe('Queued for tournament');
  for(const [patch,label] of [
    [{phase:'season',matchStatus:'running'},'Playing — season'],
    [{phase:'playoffs',matchStatus:'running'},'Playing — playoffs'],
    [{phase:'season',matchStatus:'scheduled'},'Waiting for a match worker'],
    [{phase:'season',matchStatus:null},'Waiting for the next match'],
    [{eventRegistered:false,phase:null,waitingForRegistration:true},'Waiting for the next legacy tournament'],
    [{interventionRequired:true},'Tournament needs intervention'],
    [{serverRegistered:false},'Reconnecting to League'],
  ] as const)expect(poolAgentStatus(playing,[{...worker,details:{leagueRun:{...report,...patch}}}])).toBe(label);
  queue={...queue,status:'running',entries:[playing]};show({jobs:[worker]});
  expect(await screen.findByText('Queued for tournament')).toBeInTheDocument();
  expect(screen.getAllByRole('table')).toHaveLength(1);
  expect(screen.queryByText('Waiting pool')).toBeNull();expect(screen.queryByText('Connected to League')).toBeNull();
});

test('registration countdown uses server clock rather than the viewer clock',async()=>{
  queue={...queue,status:'running',entries:[{...entry(),state:'playing',turn:{dispatchId:'d',sequence:1,jobId:'job',createdAt:''}}]};
  const worker={id:'job',kind:'matchmaking.agent',status:'running',details:{leagueRun:{completedEntries:0,activeEntries:1,maxMatches:null,durationSeconds:null,serverRegistered:true,eventRegistered:true,phase:'registration',serverNow:'2000-01-01T12:00:00Z',registrationClosesAt:'2000-01-01T12:30:00Z'}}} as api.Job;
  show({jobs:[worker]});
  expect(await screen.findByRole('timer')).toHaveTextContent('30:00 until registration closes');
});
test('older controllers are never offered unsupported format rotation',async()=>{
  show();await waitFor(()=>expect(screen.getByLabelText('Rotation')).toBeEnabled());
  expect(screen.queryByRole('option',{name:'Respect format'})).toBeNull();
});
test('format-capable controllers preserve allocation when changing rotation',async()=>{
  queue={...queue,formatSeats:{legacy:2,commander:2}};
  vi.mocked(api.saveLeagueQueue).mockImplementation(async value=>({...queue,...value} as api.LeagueQueue));
  show();await waitFor(()=>expect(screen.getByLabelText('Rotation')).toBeEnabled());
  fireEvent.change(screen.getByLabelText('Rotation'),{target:{value:'respect_format'}});
  await waitFor(()=>expect(api.saveLeagueQueue).toHaveBeenCalledWith({revision:0,strategy:'respect_format',entries:[],formatSeats:{legacy:2,commander:2}}));
});
test('format allocation is checked against authoritative account capacity',async()=>{
  queue={...queue,formatSeats:{legacy:1,commander:1}};
  show({capacity:{status:'ready',limit:2,used:0,available:2,reason:'Server'}});
  await waitFor(()=>expect(screen.getByLabelText('Rotation')).toBeEnabled());
  fireEvent.click(screen.getByText('Places by format'));
  fireEvent.change(screen.getByLabelText('legacy places'),{target:{value:'2'}});
  fireEvent.blur(screen.getByLabelText('legacy places'));
  expect(screen.getByRole('alert')).toHaveTextContent('cannot exceed');
  expect(api.saveLeagueQueue).not.toHaveBeenCalled();
});
