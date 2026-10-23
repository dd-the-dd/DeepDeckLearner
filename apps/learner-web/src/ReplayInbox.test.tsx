import {act,cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,beforeEach,expect,test,vi} from 'vitest';
import ReplayInbox from './ReplayInbox';
import * as api from './api';
import type {Job,ExperienceSettings,ReplayInbox as Inbox} from './api';

vi.mock('./api',async original=>({...await original<typeof import('./api')>(),loadReplayInbox:vi.fn(),loadJobs:vi.fn(),loadJob:vi.fn(),recoverReplayInbox:vi.fn()}));
const settings={autoCollectLeagueReplays:true} as ExperienceSettings;
const item={modelId:'pilot',name:'Tempo pilot',available:true,collected:4,pending:2,currentServerPending:2,otherServerPending:0,unknownServerPending:0,lastError:null,lastAttemptAtUnixMs:null};
const inbox:Inbox={items:[item],observedAtUnixMs:1};
const job:Job={id:'check',kind:'replays.collect',status:'queued',model_id:'pilot',label:'Recover replays',argv:[],logs:[],created_at:'now',worker_slots:0,started_at:null,finished_at:null,exit_code:null,artifact_path:null};
beforeEach(()=>{vi.resetAllMocks();vi.mocked(api.loadReplayInbox).mockResolvedValue(inbox);vi.mocked(api.loadJobs).mockResolvedValue([]);vi.mocked(api.loadJob).mockResolvedValue(job);vi.mocked(api.recoverReplayInbox).mockResolvedValue(job);});
afterEach(()=>{cleanup();vi.useRealTimers();});
function deferred<T>(){let resolve!:(value:T)=>void;const promise=new Promise<T>(done=>{resolve=done;});return{promise,resolve};}

test('durable pending games can be recovered without agent deployment and duplicate clicks stay disabled',async()=>{
  const request=deferred<Job>();vi.mocked(api.recoverReplayInbox).mockReturnValueOnce(request.promise);
  render(<ReplayInbox settings={settings}/>);
  const button=await screen.findByRole('button',{name:'Recover pending replays for Tempo pilot'});
  expect(screen.getByText('2 pending · 2 on this server · 4 historically collected')).toBeInTheDocument();
  fireEvent.click(button);fireEvent.click(button);
  expect(api.recoverReplayInbox).toHaveBeenCalledOnce();
  await act(async()=>request.resolve(job));
  expect(await screen.findByText(/No agent was sent/)).toBeInTheDocument();
  expect(screen.getByRole('button',{name:'Checking replays…'})).toBeDisabled();
});

test('unknown inbox is unavailable rather than empty and reload is explicit',async()=>{
  vi.mocked(api.loadReplayInbox).mockRejectedValueOnce(new Error('Database unavailable'));
  render(<ReplayInbox settings={settings}/>);
  await waitFor(()=>expect(screen.getByRole('status',{name:'Replay inbox status'})).toHaveTextContent('not assumed empty'));
  expect(screen.queryByText('No pending downloads are recorded.')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:'Reload pending downloads'}));
  await screen.findByRole('button',{name:'Recover pending replays for Tempo pilot'});
});

test('paused collection, deleted agent and different or unknown server origins cannot trigger recovery',async()=>{
  vi.mocked(api.loadReplayInbox).mockResolvedValue({items:[{...item,modelId:'missing',name:'Missing pilot',available:false},
    {...item,modelId:'other',name:'Other server',currentServerPending:0,otherServerPending:2},
    {...item,modelId:'unknown',name:'Unknown server',currentServerPending:0,unknownServerPending:2}],observedAtUnixMs:1});
  render(<ReplayInbox settings={{...settings,autoCollectLeagueReplays:false}}/>);
  await screen.findByText(/no confirmed server origin/);
  expect(screen.getByText(/Return to the original server/)).toBeInTheDocument();
  for(const button of screen.getAllByRole('button',{name:/Recover pending replays/}))expect(button).toBeDisabled();
  expect(api.recoverReplayInbox).not.toHaveBeenCalled();
});

test('accepted recovery completion refreshes library without claiming pending games downloaded',async()=>{
  vi.useFakeTimers();const recovered=vi.fn();
  vi.mocked(api.loadJob).mockResolvedValue({...job,status:'completed'});
  render(<ReplayInbox settings={settings} onRecovered={recovered}/>);
  await act(async()=>{});
  fireEvent.click(screen.getByRole('button',{name:'Recover pending replays for Tempo pilot'}));
  await act(async()=>{});
  expect(recovered).toHaveBeenCalledOnce();
  expect(screen.getByText(/unavailable games were not marked downloaded/)).toBeInTheDocument();
  expect(screen.getByText('2 pending · 2 on this server · 4 historically collected')).toBeInTheDocument();
  await act(async()=>{vi.advanceTimersByTime(5000);});
  expect(recovered).toHaveBeenCalledOnce();
});

test('uncertain response never automatically resubmits a recovery request',async()=>{
  vi.mocked(api.recoverReplayInbox).mockRejectedValue(new TypeError('Network response lost'));
  render(<ReplayInbox settings={settings}/>);
  fireEvent.click(await screen.findByRole('button',{name:'Recover pending replays for Tempo pilot'}));
  expect(await screen.findByRole('alert')).toHaveTextContent('Reload the inbox before retrying');
  expect(api.recoverReplayInbox).toHaveBeenCalledOnce();
});

test('terminal recovery re-reads the inbox so a pre-completion count cannot enable a duplicate retry',async()=>{
  vi.mocked(api.loadReplayInbox).mockResolvedValueOnce(inbox).mockResolvedValueOnce(inbox).mockResolvedValue({items:[{...item,pending:0,currentServerPending:0,collected:6}],observedAtUnixMs:2});
  vi.mocked(api.loadJob).mockResolvedValue({...job,status:'completed'});
  render(<ReplayInbox settings={settings}/>);
  fireEvent.click(await screen.findByRole('button',{name:'Recover pending replays for Tempo pilot'}));
  await screen.findByText('No pending downloads are recorded.');
  expect(screen.queryByRole('button',{name:'Recover pending replays for Tempo pilot'})).not.toBeInTheDocument();
  expect(api.loadReplayInbox).toHaveBeenCalledTimes(3);
  expect(api.recoverReplayInbox).toHaveBeenCalledOnce();
});
