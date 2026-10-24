import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import PlayerApp from './PlayerApp';
import * as api from './api';
import type { CapabilityStatus, DeckSummary, ExperienceSettings, Job, ProgrammaticAgent } from './api';

vi.mock('./AgentWorkspace', () => ({ default: ({ openAssistant, onDirtyChange, openPlaytest, refresh }: { openAssistant: () => void; onDirtyChange: (value: boolean) => void; openPlaytest: (agent: ProgrammaticAgent) => void; refresh:()=>Promise<void> }) => <section><input aria-label="Unsaved editor" defaultValue="My unsaved strategy" /><button onClick={() => { onDirtyChange(true); openAssistant(); }}>Edit draft and chat</button><button onClick={() => openPlaytest(flowCommander)}>Playtest chosen pilot</button><button onClick={()=>void refresh()}>Refresh local library</button></section> }));
vi.mock('./LocalPixiTable', () => ({ default: ({onClose}: {onClose:()=>Promise<void>}) => <section><h1>Your actual table</h1><button onClick={()=>void onClose()}>Leave this table</button></section> }));
vi.mock('./LiveMatchViewer', () => ({ default: ({onClose}: {onClose:()=>void}) => <section><h1>Watching live</h1><button onClick={onClose}>Leave live viewer</button></section> }));
vi.mock('./DeckWorkshop',()=>({default:({onDirtyChange}:{onDirtyChange:(value:boolean)=>void})=><section><input aria-label="Unsent deck name" defaultValue="My draft deck"/><button onClick={()=>onDirtyChange(true)}>Make deck draft dirty</button></section>}));
vi.mock('./LocalInsights',()=>({default:({onStartBatch}:{onStartBatch:()=>void})=><button onClick={onStartBatch}>Set up a 20-match batch</button>}));
vi.mock('./LocalCompetitions',async original=>({...await original<typeof import('./LocalCompetitions')>(), default:({onCreate}:{onCreate:()=>void})=><button onClick={onCreate}>New competition</button>}));
vi.mock('./api', async (original) => ({ ...await original<typeof import('./api')>(),
  loadStatus: vi.fn(), loadAccountStatus: vi.fn(), loadLeagueAccount: vi.fn(), loadLeagueConnection: vi.fn(),
  loadProgrammaticAgents: vi.fn(), loadDownloadedDecks: vi.fn(), loadJobs: vi.fn(), loadJob: vi.fn(),
  loadActiveGames: vi.fn(), loadExperienceSettings: vi.fn(), loadLlmSettings: vi.fn(), chatWithAgentStudio: vi.fn(),
  startJob: vi.fn(), stopJob: vi.fn(),
  saveExperienceSettings:vi.fn(),
  loadLeagueQueue:vi.fn(),loadLocalTableReplays:vi.fn(),loadSavedReplays:vi.fn(),loadReplayInbox:vi.fn(),
}));
const job: Job = { id: 'chat-table', kind: 'playtest.table', status: 'queued', label: 'Your Legacy table', details: {}, logs: [], argv: [], created_at: '', started_at: null, finished_at: null, exit_code: null, artifact_path: null };
const flowDeck = {id:'flow-deck',name:'Flow Legacy deck',format:'legacy',version:1,colors:['U'],playableCardCount:60} as DeckSummary;
const flowCommanderDeck = {...flowDeck,id:'flow-commander',name:'Flow Commander deck',format:'commander'} as DeckSummary;
const flowAgent = {id:'flow-pilot',name:'Flow Legacy pilot',format:'legacy',profile:'balanced',revision:1,deckVersionIds:[flowDeck.id],decks:[flowDeck]} as ProgrammaticAgent;
const flowCommander = {...flowAgent,id:'flow-commander-pilot',name:'Flow Commander pilot',format:'commander',deckVersionIds:[flowCommanderDeck.id],decks:[flowCommanderDeck]} as ProgrammaticAgent;

test('global copilot remains mounted with its draft while navigating and minimizing', async () => {
  render(<PlayerApp />);
  fireEvent.click(screen.getByRole('button', {name: 'Open DeepDeck copilot'}));
  const prompt = await screen.findByPlaceholderText('Describe a strategy change…');
  fireEvent.change(prompt, {target: {value: 'Help me explore my decks'}});
  const navigation = screen.getByRole('navigation', {name: 'Assistant page navigation'});
  fireEvent.click(within(navigation).getByRole('button', {name: 'Decks'}));
  expect(screen.getByLabelText('Unsent deck name')).toBeInTheDocument();
  expect(prompt).toHaveValue('Help me explore my decks');
  fireEvent.click(screen.getByRole('button', {name: 'Close'}));
  fireEvent.click(screen.getByRole('button', {name: 'Open DeepDeck copilot'}));
  // The global launcher resumes the same conversation, even from another page.
  const retainedPrompt = await screen.findByPlaceholderText('Describe a strategy change…');
  expect(retainedPrompt).toHaveValue('Help me explore my decks');
  fireEvent.change(retainedPrompt, {target: {value: 'Keep this draft'}});
  fireEvent.click(within(screen.getByRole('navigation', {name: 'Assistant page navigation'})).getByRole('button', {name: 'Agents'}));
  fireEvent.click(screen.getByRole('button', {name: 'Close'}));
  fireEvent.click(screen.getByRole('button', {name: 'Open DeepDeck copilot'}));
  expect(screen.getByPlaceholderText('Describe a strategy change…')).toHaveValue('Keep this draft');
  expect(api.chatWithAgentStudio).not.toHaveBeenCalled();
});
test('the global copilot keeps the conversation after navigating to Decks and reopening', async () => {
  vi.mocked(api.chatWithAgentStudio).mockResolvedValue({message:'We can review your library together.',toolEvents:[],agents:[]});
  render(<PlayerApp />);
  fireEvent.click(screen.getByRole('button',{name:'Open DeepDeck copilot'}));
  const input=await screen.findByPlaceholderText('Describe a strategy change…');
  fireEvent.change(input,{target:{value:'Help me navigate'}});
  fireEvent.keyDown(input,{key:'Enter'});
  await screen.findByText('We can review your library together.');
  fireEvent.click(within(screen.getByRole('navigation',{name:'Assistant page navigation'})).getByRole('button',{name:'Decks'}));
  fireEvent.click(screen.getByRole('button',{name:'Close'}));
  fireEvent.click(screen.getByRole('button',{name:'Open DeepDeck copilot'}));
  expect(screen.getByText('Help me navigate')).toBeVisible();
  expect(screen.getByText('We can review your library together.')).toBeVisible();
  expect(api.chatWithAgentStudio).toHaveBeenCalledOnce();
});

beforeEach(() => {
  vi.clearAllMocks(); window.localStorage.clear();
  vi.mocked(api.loadStatus).mockResolvedValue({ engine: { healthy: true, url: 'http://127.0.0.1:8897' } } as CapabilityStatus);
  vi.mocked(api.loadAccountStatus).mockResolvedValue({ configured: false, valid: false, reason: 'Sign in' });
  vi.mocked(api.loadLeagueAccount).mockResolvedValue({ configured: false, valid: false, reason: 'Sign in' });
  vi.mocked(api.loadLeagueConnection).mockResolvedValue({ environment: 'production', reachable: false, apiUrl: 'https://www.deepdeckleague.com/api/v1', siteUrl: 'https://www.deepdeckleague.com', releaseProfile: 'unknown', capabilities: [], naturalCardSearchEnabled: false, reason: 'Offline' });
  vi.mocked(api.loadProgrammaticAgents).mockResolvedValue([]); vi.mocked(api.loadDownloadedDecks).mockResolvedValue([]);
  vi.mocked(api.loadJobs).mockResolvedValue([]); vi.mocked(api.loadActiveGames).mockResolvedValue([]);
  vi.mocked(api.loadExperienceSettings).mockResolvedValue({ replayStorageLimitBytes: 2 * 1024 ** 3, replayStorageUsedBytes: 0, replayPollSeconds: 600, autoCollectLeagueReplays: true } as ExperienceSettings);
  vi.mocked(api.loadLlmSettings).mockResolvedValue({ configured: true, providerName: 'Local fixture', model: 'fixture', baseUrl: 'http://127.0.0.1:11434/v1', hasApiKey: false, leagueMcpEnabled: false });
  vi.mocked(api.loadLeagueQueue).mockResolvedValue({id:'queue',revision:0,status:'paused',strategy:'round_robin',entries:[],history:[],capacity:null,message:null});
  vi.mocked(api.loadLocalTableReplays).mockResolvedValue([]);
  vi.mocked(api.loadSavedReplays).mockResolvedValue([]);
  vi.mocked(api.loadReplayInbox).mockResolvedValue({items:[],observedAtUnixMs:0});
  HTMLDialogElement.prototype.showModal=function(){this.setAttribute('open','');};
  vi.mocked(api.loadJob).mockResolvedValue(job);
  vi.mocked(api.startJob).mockResolvedValue(job);vi.mocked(api.stopJob).mockResolvedValue({...job,status:'stopped'});
  vi.mocked(api.chatWithAgentStudio).mockResolvedValue({ message: 'Table started.', toolEvents: [{ tool: 'start_playtest', ok: true, result: job }], agents: [] });
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); });

test('navigation keeps complete accessible names and marks only the current page', () => {
  render(<PlayerApp />);
  const navigation = within(screen.getByRole('navigation', {name: 'Main navigation'}));
  const buttons = navigation.getAllByRole('button');
  expect(buttons).toHaveLength(7);
  for (const button of buttons) {
    expect(button.getAttribute('aria-label')).toBeTruthy();
    expect(button.getAttribute('title')).toBeTruthy();
  }
  expect(navigation.getByRole('button', {name: 'Play Local games'})).toHaveAttribute('aria-current', 'page');
  const events = navigation.getByRole('button', {name: 'Competitions Local tournaments (Events)'});
  expect(within(events).getByText('Events')).toBeInTheDocument();
  fireEvent.click(events);
  expect(events).toHaveAttribute('aria-current', 'page');
  expect(navigation.getByRole('button', {name: 'Play Local games'})).not.toHaveAttribute('aria-current');
  expect(buttons.filter(button => button.hasAttribute('aria-current'))).toHaveLength(1);
  expect(screen.getByRole('button', {name: 'Settings Accounts & storage'})).toHaveAttribute('title', 'Settings — Accounts & storage');
});

test('returning to League shows saved agent rows without starting a worker',async()=>{
  const entry={id:'e1',agentId:'pilot',name:'Waiting pilot',format:'legacy',competitionVersionId:'league',deckVersionIds:['deck'],lease:{mode:'continuous' as const},completedMatches:2,elapsedSeconds:100,state:'queued' as const,turn:null,error:null};
  vi.mocked(api.loadLeagueQueue).mockResolvedValue({id:'queue',revision:1,status:'running',strategy:'round_robin',entries:[entry],history:[],capacity:null,message:null});
  render(<PlayerApp/>);
  fireEvent.click(screen.getByRole('button',{name:/^League /}));
  expect(await screen.findByText('Waiting pilot')).toBeInTheDocument();
  expect(screen.getByText('Waiting for a place')).toBeInTheDocument();
  expect(screen.queryByRole('button',{name:'Show rotation details'})).toBeNull();
  fireEvent.click(screen.getByRole('button',{name:/^Play /}));
  fireEvent.click(screen.getByRole('button',{name:/^League /}));
  expect(await screen.findByText('Waiting pilot')).toBeInTheDocument();
  expect(api.startJob).not.toHaveBeenCalled();
});

test('switching tabs cannot silently discard a deck draft',async()=>{
  const confirm=vi.spyOn(window,'confirm').mockReturnValue(false);
  render(<PlayerApp/>);
  fireEvent.click(screen.getByRole('button',{name:/^Decks /}));
  fireEvent.click(screen.getByRole('button',{name:'Make deck draft dirty'}));
  fireEvent.click(screen.getByRole('button',{name:/^Play /}));
  expect(confirm).toHaveBeenCalledWith(expect.stringContaining('unsaved deck draft'));
  expect(screen.getByLabelText('Unsent deck name')).toHaveValue('My draft deck');
  confirm.mockReturnValue(true);fireEvent.click(screen.getByRole('button',{name:/^Play /}));
  expect(screen.queryByLabelText('Unsent deck name')).toBeNull();
});
async function askToPlay(dirty = false) {
  render(<PlayerApp />);
  fireEvent.click(screen.getByRole('button', { name: /^Agents / }));
  fireEvent.click(screen.getByRole('button', { name: dirty ? 'Edit draft and chat' : 'Open strategy copilot' }));
  await screen.findByText('Describe how this agent should play.');
  fireEvent.change(screen.getByPlaceholderText('Describe a strategy change…'), { target: { value: 'Let me play now.' } });
  fireEvent.click(screen.getByRole('button', { name: 'Send' }));
}

test('the real player shell opens preparation and then its chat-created table without another request', async () => {
  vi.mocked(api.loadJob).mockResolvedValue({ ...job, status: 'running', details: { sessionId: 'ready', engineUrl: 'http://127.0.0.1:8897' } });
  await askToPlay();
  expect(await screen.findByRole('heading', { name: 'Your actual table' })).toBeVisible();
  expect(api.loadJob).toHaveBeenCalledWith(job.id);
  expect(api.chatWithAgentStudio).toHaveBeenCalledOnce();
});

test('a chat-created game cannot silently discard the agent editor draft', async () => {
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
  await askToPlay(true);
  await waitFor(() => expect(confirm).toHaveBeenCalledWith(expect.stringContaining('unsaved agent draft')));
  expect(screen.getByLabelText('Unsaved editor')).toHaveValue('My unsaved strategy');
  expect(screen.queryByRole('heading', { name: /Opening your table/ })).toBeNull();
  const open = await screen.findByRole('button', { name: 'Open table' });
  await waitFor(() => expect(open).toBeEnabled());
  confirm.mockReturnValue(true); fireEvent.click(open);
  expect(await screen.findByRole('heading', { name: /Opening your table/ })).toBeVisible();
  expect(screen.queryByLabelText('Unsaved editor')).toBeNull();
  expect(api.chatWithAgentStudio).toHaveBeenCalledOnce();
});

test('a failed launch gives a return route instead of silently disappearing or launching again', async () => {
  vi.mocked(api.loadJob).mockResolvedValue({ ...job, status: 'failed', logs: ['Engine unavailable'] });
  await askToPlay();
  expect(await screen.findByRole('heading', { name: 'Your table could not start.' })).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Return to app' }));
  expect(await screen.findByLabelText('Unsaved editor')).toBeVisible();
  expect(api.chatWithAgentStudio).toHaveBeenCalledOnce();
});

async function prepareLocalTable() {
  vi.mocked(api.loadProgrammaticAgents).mockResolvedValue([flowAgent,flowCommander]);
  vi.mocked(api.loadDownloadedDecks).mockResolvedValue([flowDeck,flowCommanderDeck]);
  render(<PlayerApp/>);
  await screen.findByRole('option',{name:'Flow Legacy pilot · r1'});
  fireEvent.change(within(document.querySelector('.seat-card.human') as HTMLElement).getByRole('combobox',{name:'Deck'}),{target:{value:flowDeck.id}});
  fireEvent.change(screen.getByLabelText(/^Shuffle seed/),{target:{value:'42'}});
}
const enterBatch=()=>fireEvent.click(screen.getByRole('button',{name:/AI batch Run matches/}));
const enterTable=()=>fireEvent.click(screen.getByRole('button',{name:/Play a game You or your agents/}));

test('table and batch preparations survive a real shell tab round trip without relaunching anything',async()=>{
  await prepareLocalTable();
  enterBatch();fireEvent.change(screen.getByLabelText('Matches'),{target:{value:'7'}});
  fireEvent.change(screen.getByLabelText(/^Shuffle seed/),{target:{value:'99'}});
  fireEvent.click(screen.getByRole('button',{name:/^Decks /}));
  fireEvent.click(screen.getByRole('button',{name:/^Play /}));
  expect(screen.getByLabelText('Matches')).toHaveValue(7);
  expect(screen.getByLabelText(/^Shuffle seed/)).toHaveValue('99');
  enterTable();
  expect(screen.getByLabelText(/^Shuffle seed/)).toHaveValue('42');
  expect(screen.getByLabelText(/^Match safety limit/)).toHaveValue('');
  expect(within(document.querySelector('.seat-card.human') as HTMLElement).getByRole('combobox',{name:'Deck'})).toHaveValue(flowDeck.id);
  expect(api.startJob).not.toHaveBeenCalled();
});

test('leaving the actual table restores the exact launch preparation and does not create a second match',async()=>{
  vi.mocked(api.startJob).mockResolvedValue({...job,status:'running',details:{sessionId:'real-session'}});
  await prepareLocalTable();
  fireEvent.click(screen.getByRole('button',{name:'Start game'}));
  await screen.findByRole('heading',{name:'Your actual table'});
  expect(screen.queryByRole('button',{name:'Open DeepDeck copilot'})).toBeNull();
  expect(document.querySelector('.assistant-layer')).toHaveAttribute('aria-hidden','true');
  fireEvent.click(screen.getByRole('button',{name:'Leave this table'}));
  await screen.findByRole('button',{name:'Start game'});
  expect(screen.getByRole('button',{name:'Open DeepDeck copilot'})).toBeVisible();
  expect(screen.getByLabelText(/^Shuffle seed/)).toHaveValue('42');
  expect(within(document.querySelector('.seat-card.human') as HTMLElement).getByRole('combobox',{name:'Deck'})).toHaveValue(flowDeck.id);
  expect(api.startJob).toHaveBeenCalledOnce();expect(api.stopJob).toHaveBeenCalledWith(job.id);
});

test('an open chat is hidden during a table and its draft survives returning to the app', async()=>{
  vi.mocked(api.startJob).mockResolvedValue({...job,status:'running',details:{sessionId:'real-session'}});
  await prepareLocalTable();
  fireEvent.click(screen.getByRole('button',{name:'Open DeepDeck copilot'}));
  const input=await screen.findByPlaceholderText('Describe a strategy change…');
  fireEvent.change(input,{target:{value:'Keep my strategy idea for after the match'}});
  fireEvent.click(screen.getByRole('button',{name:'Start game'}));
  await screen.findByRole('heading',{name:'Your actual table'});
  expect(document.querySelector('.assistant-layer')).toHaveAttribute('aria-hidden','true');
  expect(document.querySelector('.assistant-layer')).not.toHaveClass('open');
  expect(screen.queryByRole('button',{name:'Open DeepDeck copilot'})).toBeNull();
  expect(screen.queryByRole('navigation',{name:'Assistant page navigation'})).toBeNull();
  fireEvent.click(screen.getByRole('button',{name:'Leave this table'}));
  await screen.findByRole('button',{name:'Start game'});
  expect(document.querySelector('.assistant-layer')).toHaveClass('open');
  expect(screen.getByPlaceholderText('Describe a strategy change…')).toHaveValue('Keep my strategy idea for after the match');
  expect(api.chatWithAgentStudio).not.toHaveBeenCalled();
});

test('background batches keep chat available but opening their live viewer hides it',async()=>{
  vi.mocked(api.loadJobs).mockResolvedValue([{...job,kind:'playtest.batch',status:'running'}]);
  render(<PlayerApp/>);
  const watch=await screen.findByRole('button',{name:'Watch live'});
  expect(screen.getByRole('button',{name:'Open DeepDeck copilot'})).toBeVisible();
  fireEvent.click(watch);
  await screen.findByRole('heading',{name:'Watching live'});
  expect(screen.queryByRole('button',{name:'Open DeepDeck copilot'})).toBeNull();
  expect(document.querySelector('.assistant-layer')).toHaveAttribute('aria-hidden','true');
  fireEvent.click(screen.getByRole('button',{name:'Leave live viewer'}));
  expect(screen.getByRole('button',{name:'Open DeepDeck copilot'})).toBeVisible();
  expect(api.startJob).not.toHaveBeenCalled();
  expect(api.stopJob).not.toHaveBeenCalled();
});

test('a failed table launch returns to its saved seats and settings',async()=>{
  vi.mocked(api.loadJob).mockResolvedValue({...job,status:'failed',logs:['Engine unavailable']});
  await prepareLocalTable();
  fireEvent.click(screen.getByRole('button',{name:'Start game'}));
  await screen.findByRole('heading',{name:'Your table could not start.'});
  fireEvent.click(screen.getByRole('button',{name:'Return to app'}));
  expect(screen.getByLabelText(/^Shuffle seed/)).toHaveValue('42');
  expect(within(document.querySelector('.seat-card.human') as HTMLElement).getByRole('combobox',{name:'Deck'})).toHaveValue(flowDeck.id);
  expect(api.startJob).toHaveBeenCalledOnce();
});

test('Insights explicitly sets twenty batch matches without resetting the table or the batch settings',async()=>{
  await prepareLocalTable();enterBatch();
  fireEvent.change(screen.getByLabelText('Matches'),{target:{value:'7'}});
  fireEvent.change(screen.getByLabelText(/^Shuffle seed/),{target:{value:'99'}});
  fireEvent.click(screen.getByRole('button',{name:/^Insights /}));
  fireEvent.click(screen.getByRole('button',{name:'Set up a 20-match batch'}));
  expect(screen.getByLabelText('Matches')).toHaveValue(20);
  expect(screen.getByLabelText(/^Shuffle seed/)).toHaveValue('99');
  enterTable();expect(screen.getByLabelText(/^Shuffle seed/)).toHaveValue('42');
  expect(api.startJob).not.toHaveBeenCalled();
});

test('the explicit agent shortcut selects its Commander pilot while keeping the separate Legacy batch',async()=>{
  await prepareLocalTable();enterBatch();
  fireEvent.change(screen.getByLabelText('Matches'),{target:{value:'7'}});
  fireEvent.click(screen.getByRole('button',{name:/^Agents /}));
  fireEvent.click(screen.getByRole('button',{name:'Playtest chosen pilot'}));
  expect(screen.getByLabelText('Format')).toHaveValue('commander');
  expect(document.querySelectorAll('.seat-card.agent')).toHaveLength(3);
  for(const seat of document.querySelectorAll('.seat-card.agent')) expect(within(seat as HTMLElement).getByRole('combobox',{name:'Agent'})).toHaveValue(flowCommander.id);
  enterBatch();
  expect(screen.getByLabelText('Format')).toHaveValue('legacy');
  expect(screen.getByLabelText('Matches')).toHaveValue(7);
  expect(api.startJob).not.toHaveBeenCalled();
});

test('the competition history shortcut opens the builder even when a game preparation is remembered',async()=>{
  await prepareLocalTable();
  fireEvent.click(screen.getByRole('button',{name:/^Competitions /}));
  fireEvent.click(screen.getByRole('button',{name:'New competition'}));
  expect(screen.getByRole('heading',{name:'Your players. Your tournament.'})).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:'← Play a game'}));
  expect(screen.getByLabelText(/^Shuffle seed/)).toHaveValue('42');
  expect(api.startJob).not.toHaveBeenCalled();
});

test('an older background jobs response cannot erase the newer League roster',async()=>{
  let oldRead!:(jobs:Job[])=>void;
  const running={...job,id:'league-worker',kind:'matchmaking.agent',label:'Current League worker',status:'running' as const};
  vi.mocked(api.loadJobs).mockReturnValueOnce(new Promise(resolve=>{oldRead=resolve;})).mockResolvedValue([running]);
  render(<PlayerApp/>);
  fireEvent.click(screen.getByRole('button',{name:/^Agents /}));
  fireEvent.click(screen.getByRole('button',{name:'Refresh local library'}));
  await waitFor(()=>expect(api.loadJobs).toHaveBeenCalledTimes(2));
  fireEvent.click(screen.getByRole('button',{name:/^League /}));
  await screen.findByText('Current League worker');
  await act(async()=>{oldRead([]);});
  expect(within(screen.getByRole('table',{name:'Agents and Leagues'})).getByText('Current League worker')).toBeInTheDocument();
});

test('a stale initial agent read cannot erase a library refreshed explicitly by the user',async()=>{
  let oldRead!:(agents:ProgrammaticAgent[])=>void;
  vi.mocked(api.loadProgrammaticAgents).mockReturnValueOnce(new Promise(resolve=>{oldRead=resolve;})).mockResolvedValue([flowAgent]);
  render(<PlayerApp/>);
  fireEvent.click(screen.getByRole('button',{name:/^Agents /}));
  fireEvent.click(screen.getByRole('button',{name:'Refresh local library'}));
  await waitFor(()=>expect(api.loadProgrammaticAgents).toHaveBeenCalledTimes(2));
  fireEvent.click(screen.getByRole('button',{name:/^Play /}));
  await screen.findByRole('option',{name:/Flow Legacy pilot/});
  await act(async()=>{oldRead([]);});
  expect(screen.getByRole('option',{name:/Flow Legacy pilot/})).toBeInTheDocument();
});

test('a settings read started before a save cannot re-enable the newly paused collection',async()=>{
  vi.useFakeTimers({shouldAdvanceTime:true});
  const initial={replayStorageLimitBytes:2*1024**3,replayStorageUsedBytes:0,replayPollSeconds:600,autoCollectLeagueReplays:true};
  let stale!:(value:ExperienceSettings)=>void;
  vi.mocked(api.loadExperienceSettings).mockResolvedValueOnce(initial).mockReturnValueOnce(new Promise(resolve=>{stale=resolve;}));
  vi.mocked(api.saveExperienceSettings).mockResolvedValue({...initial,autoCollectLeagueReplays:false});
  await prepareLocalTable();
  await act(async()=>{await vi.advanceTimersByTimeAsync(10_000);});
  expect(api.loadExperienceSettings).toHaveBeenCalledTimes(2);
  fireEvent.click(screen.getByRole('button',{name:/^Replays /}));
  fireEvent.click(screen.getByRole('button',{name:'Limit replay storage'}));
  fireEvent.click(screen.getByLabelText('Automatically collect League replays'));
  fireEvent.click(screen.getByRole('button',{name:'Save replay settings'}));
  await screen.findByText(/Replay settings saved/);
  await act(async()=>{stale(initial);});
  expect(screen.getByLabelText('Automatically collect League replays')).not.toBeChecked();
  expect(api.saveExperienceSettings).toHaveBeenCalledWith({autoCollectLeagueReplays:false});
});
