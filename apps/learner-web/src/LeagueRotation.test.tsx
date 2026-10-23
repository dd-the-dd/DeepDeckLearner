import {act,cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,beforeEach,expect,test,vi} from 'vitest';
import LeagueRotation, {rotationOverview} from './LeagueRotation';
import * as api from './api';
import type {LeagueQueue} from './api';

vi.mock('./api',async original=>({...await original<typeof import('./api')>(),loadLeagueQueue:vi.fn(),saveLeagueQueue:vi.fn(),removeLeagueQueueEntry:vi.fn(),controlLeagueQueue:vi.fn()}));
const queue:LeagueQueue={id:'queue',revision:4,status:'paused',strategy:'round_robin',message:null,capacity:null,history:[],entries:[
  {id:'entry-a',agentId:'tempo',name:'Tempo pilot',format:'legacy',competitionVersionId:'league',deckVersionIds:['deck-a'],lease:{mode:'matches',matches:10},completedMatches:3,elapsedSeconds:140,state:'queued',turn:null,error:null},
  {id:'entry-b',agentId:'combo',name:'Combo pilot',format:'legacy',competitionVersionId:'league',deckVersionIds:['deck-b'],lease:{mode:'duration',minutes:60},completedMatches:2,elapsedSeconds:360,state:'queued',turn:null,error:null},
]};
beforeEach(()=>{vi.resetAllMocks();vi.mocked(api.loadLeagueQueue).mockResolvedValue(queue);});
afterEach(()=>{cleanup();vi.useRealTimers();});
function deferred<T>(){let resolve!:(value:T)=>void;const promise=new Promise<T>(done=>{resolve=done;});return{promise,resolve};}

test('explains rotation and each independent lease; changing strategy preserves all agents',async()=>{
  vi.mocked(api.saveLeagueQueue).mockResolvedValue({...queue,revision:5,strategy:'random'});
  render(<LeagueRotation/>);
  await screen.findByText('Tempo pilot');
  expect(screen.getByText('1 eligible deck · legacy · 3 / 10 completed matches')).toBeInTheDocument();
  expect(screen.getByText('1 eligible deck · legacy · 6 / 60 deployment minutes')).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Rotation order'),{target:{value:'random'}});
  await waitFor(()=>expect(api.saveLeagueQueue).toHaveBeenCalledWith({revision:4,strategy:'random',entries:queue.entries}));
  expect(screen.getByText(/repeats are possible/)).toBeInTheDocument();
});

test('start is explicit; pause drains instead of killing the assigned match',async()=>{
  vi.mocked(api.controlLeagueQueue).mockResolvedValue({...queue,revision:5,status:'running'});
  render(<LeagueRotation/>);
  fireEvent.click(await screen.findByRole('button',{name:'Start rotation'}));
  await waitFor(()=>expect(api.controlLeagueQueue).toHaveBeenCalledWith('resume',4));
  expect(screen.getByRole('button',{name:'Pause rotation'})).toBeInTheDocument();
  expect(screen.getByLabelText('Rotation order')).toBeDisabled();
  expect(screen.getByRole('button',{name:'Remove Tempo pilot from rotation'})).toBeEnabled();
  vi.mocked(api.controlLeagueQueue).mockResolvedValue({...queue,revision:6,status:'pausing'});
  fireEvent.click(screen.getByRole('button',{name:'Pause rotation'}));
  await waitFor(()=>expect(api.controlLeagueQueue).toHaveBeenLastCalledWith('pause',5));
});

test('remove sends only the waiting entry identity while leaving the active turn protected',async()=>{
  const active={...queue.entries[0],state:'playing' as const,turn:{dispatchId:'turn',sequence:3,jobId:'job',createdAt:'now'}};
  vi.mocked(api.loadLeagueQueue).mockResolvedValue({...queue,status:'running',entries:[active,queue.entries[1]]});
  vi.mocked(api.removeLeagueQueueEntry).mockResolvedValue({...queue,revision:5,status:'running',entries:[active]});
  render(<LeagueRotation/>);
  await screen.findByText('Tempo pilot');
  expect(screen.queryByRole('button',{name:'Remove Tempo pilot from rotation'})).not.toBeInTheDocument();
  expect(screen.getByText(/Active turn protected/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:'Remove Combo pilot from rotation'}));
  await waitFor(()=>expect(api.removeLeagueQueueEntry).toHaveBeenCalledWith('entry-b',4));
  expect(api.saveLeagueQueue).not.toHaveBeenCalled();expect(api.controlLeagueQueue).not.toHaveBeenCalled();
  expect(screen.getByText('Tempo pilot')).toBeInTheDocument();
});

test('a dispatch racing removal leaves the agent visible and explains the conflict',async()=>{
  vi.mocked(api.removeLeagueQueueEntry).mockRejectedValue(new api.ApiError('This agent now has an active turn. It was not removed.',409));
  render(<LeagueRotation/>);
  fireEvent.click(await screen.findByRole('button',{name:'Remove Tempo pilot from rotation'}));
  await waitFor(()=>expect(screen.getByRole('alert')).toHaveTextContent('active turn'));
  expect(screen.getByText('Tempo pilot')).toBeInTheDocument();
  expect(api.controlLeagueQueue).not.toHaveBeenCalled();
});

test('load failure is unavailable rather than empty and offers explicit reload',async()=>{
  vi.mocked(api.loadLeagueQueue).mockRejectedValueOnce(new Error('Offline'));
  render(<LeagueRotation/>);
  expect(await screen.findByRole('alert')).toHaveTextContent('Rotation is unavailable, not empty');
  expect(screen.queryByText(/Add an agent using your choices/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:'Reload rotation'}));
  await screen.findByText('Tempo pilot');
});

test('a pre-action poll cannot undo a confirmed pause',async()=>{
  vi.useFakeTimers();
  const old=deferred<LeagueQueue>();
  vi.mocked(api.loadLeagueQueue).mockImplementationOnce(async()=>({...queue,status:'running'})).mockImplementationOnce(()=>old.promise);
  vi.mocked(api.controlLeagueQueue).mockResolvedValue({...queue,status:'paused',revision:5});
  render(<LeagueRotation/>);
  await act(async()=>{});
  await act(async()=>{vi.advanceTimersByTime(5000);});
  fireEvent.click(screen.getByRole('button',{name:'Pause rotation'}));
  await act(async()=>{});
  await act(async()=>{old.resolve({...queue,status:'running'});});
  expect(screen.getByRole('button',{name:'Start rotation'})).toBeInTheDocument();
});

test('uncertain control errors remain visible while polling and do not automatically retry',async()=>{
  vi.useFakeTimers();
  vi.mocked(api.controlLeagueQueue).mockRejectedValue(new Error('Request timed out'));
  render(<LeagueRotation/>);
  await act(async()=>{});
  fireEvent.click(screen.getByRole('button',{name:'Start rotation'}));
  await act(async()=>{});
  await act(async()=>{vi.advanceTimersByTime(5000);});
  expect(screen.getByRole('alert')).toHaveTextContent('change is not confirmed');
  expect(api.controlLeagueQueue).toHaveBeenCalledOnce();
});

test('server capacity is never guessed; missing receipts are reviewed before retry',async()=>{
  vi.mocked(api.loadLeagueQueue).mockResolvedValue({...queue,capacity:{status:'unsupported',limit:null,used:null,available:null,reason:'Server does not report capacity',checkedAt:'2026-10-19T12:00:00Z'},entries:[{...queue.entries[0],state:'blocked',error:'No confirmed result'}]});
  vi.mocked(api.controlLeagueQueue).mockResolvedValue(queue);
  render(<LeagueRotation/>);
  await screen.findByText('Tempo pilot');
  expect(screen.getByText(/Automatic dispatch waiting/)).toHaveTextContent('Server does not report capacity');
  expect(screen.getByRole('button',{name:'Start rotation'})).toBeDisabled();
  fireEvent.click(screen.getByRole('button',{name:'Reviewed — retry Tempo pilot'}));
  await waitFor(()=>expect(api.controlLeagueQueue).toHaveBeenCalledWith('retry',4,'entry-a'));
});

test.each([
  [{status:'running',capacity:null},'Waiting for server capacity'],
  [{status:'running',capacity:{status:'ready',limit:10,used:10,available:0}},'Waiting for an account place'],
  [{status:'pausing'},'Pausing after current turns'],
  [{status:'paused',entries:[{...queue.entries[0],turn:{dispatchId:'old'}}]},'Paused · previous turns awaiting confirmation'],
  [{status:'running',controllerError:'Storage unavailable'},'Needs attention'],
] as const)('overview distinguishes waiting, draining and uncertain controller state %#',(change,expected)=>{
  expect(rotationOverview({...queue,...change} as LeagueQueue)).toBe(expected);
});

test('collapsed overview never invents an empty bench while a read is pending or unavailable',async()=>{
  const pending=deferred<LeagueQueue>();
  vi.mocked(api.loadLeagueQueue).mockReturnValueOnce(pending.promise);
  const {rerender}=render(<LeagueRotation expanded={false}/>);
  expect(screen.getByRole('status',{name:'Rotation overview'})).toHaveTextContent('Checking your rotation');
  expect(screen.getByRole('status',{name:'Rotation overview'})).not.toHaveTextContent('0 waiting');
  await act(async()=>pending.resolve({...queue,status:'running'}));
  expect(screen.getByRole('status',{name:'Rotation overview'})).toHaveTextContent('2 waiting');
  vi.mocked(api.loadLeagueQueue).mockRejectedValueOnce(new Error('Unavailable'));
  rerender(<LeagueRotation expanded={false} refreshKey={1}/>);
  await screen.findByText(/saved rotation is not assumed empty/);
  expect(screen.getByRole('status',{name:'Rotation overview'})).toHaveTextContent('2 waiting');
  expect(screen.getByRole('status',{name:'Rotation overview'})).toHaveTextContent('Last loaded state');
  expect(api.controlLeagueQueue).not.toHaveBeenCalled();
});

test('recent receipts distinguish completed, failed, cancelled and missing outcomes without inventing wins or time',async()=>{
  vi.mocked(api.loadLeagueQueue).mockResolvedValue({...queue,history:[
    {dispatchId:'1',name:'Successful pilot',finishedAt:'2026-10-19T12:00:00Z',error:null,result:{completedEntries:1,elapsedSeconds:72,outcome:'complete',matchId:'m1'}},
    {dispatchId:'2',name:'Failed pilot',error:'Server failure',result:{completedEntries:0,elapsedSeconds:15,outcome:'failed',matchId:'m2'}},
    {dispatchId:'3',name:'Cancelled pilot',error:null,result:{completedEntries:0,elapsedSeconds:3,outcome:'cancelled',matchId:null}},
    {dispatchId:'4',name:'Unknown pilot',finishedAt:'bad date',error:'Receipt missing',result:null},
  ]});
  const replays=vi.fn();render(<LeagueRotation onReplays={replays}/>);
  const history=await screen.findByRole('region',{name:'Recent rotation turns'});
  expect(history).toHaveTextContent('1 completed match');expect(history).toHaveTextContent('1m 12s deployed');
  expect(history).toHaveTextContent('Match failed · no completed match');
  expect(history).toHaveTextContent('Attempt cancelled · no completed match');
  expect(history).toHaveTextContent('Result not confirmed');expect(history).toHaveTextContent('Duration not confirmed');
  expect(history).not.toHaveTextContent('Invalid Date');expect(history).not.toHaveTextContent('Winner');
  expect(history.querySelector('li')?.textContent).toContain('Unknown pilot');
  fireEvent.click(screen.getByRole('button',{name:'Open replay library'}));expect(replays).toHaveBeenCalledOnce();
});

test('receipt display is bounded to the six most recent records',async()=>{
  vi.mocked(api.loadLeagueQueue).mockResolvedValue({...queue,history:Array.from({length:30},(_,i)=>({dispatchId:String(i),name:`Past pilot ${i}`,error:null,result:null}))});
  render(<LeagueRotation/>);
  const history=await screen.findByRole('region',{name:'Recent rotation turns'});
  expect(history.querySelectorAll('li')).toHaveLength(6);
  expect(history).toHaveTextContent('Last 6 of 30 retained turns');
  expect(history.querySelector('li')?.textContent).toContain('Past pilot 29');
  fireEvent.click(screen.getByRole('button',{name:'Show all 30 retained turns'}));
  expect(history.querySelectorAll('li')).toHaveLength(30);
  fireEvent.click(screen.getByRole('button',{name:'Show latest six turns'}));
  expect(history.querySelectorAll('li')).toHaveLength(6);
});
