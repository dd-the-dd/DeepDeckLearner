import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import LocalInsights from './LocalInsights';
import * as api from './api';
import type { LocalStatistics } from './api';

vi.mock('./api',async original=>({...await original<typeof import('./api')>(),loadLocalStatistics:vi.fn()}));
vi.mock('./AdvancedInsights',()=>({default:({onSaved}:{onSaved:()=>void})=><button onClick={onSaved}>Advanced tracking saved</button>}));
function stats(name='Saved pilot',days=0):LocalStatistics {
  return {days,summary:{matches:3,seatResults:6,agents:1,decks:2,players:0},
    agents:[{id:'pilot',name,matches:3,wins:2,losses:1,draws:0,winRate:2/3,confidence95:[.2,.9],decks:2}],decks:[],players:[],recentMatches:[]};
}
function deferred<T>(){let resolve!:(value:T)=>void;let reject!:(reason:Error)=>void;const promise=new Promise<T>((done,fail)=>{resolve=done;reject=fail;});return {promise,resolve,reject};}
beforeEach(()=>{vi.resetAllMocks();vi.mocked(api.loadLocalStatistics).mockResolvedValue(stats());});
afterEach(()=>{cleanup();vi.useRealTimers();});
const props=()=>({onStartBatch:vi.fn(),onViewReplays:vi.fn()});

test('initial loading is not an empty library or an invitation to create replacement results',()=>{
  vi.mocked(api.loadLocalStatistics).mockReturnValue(new Promise(()=>{}));
  const callbacks=props();
  render(<LocalInsights {...callbacks}/>);
  expect(screen.getByRole('heading',{name:'Loading local results…'})).toBeInTheDocument();
  expect(screen.getByText('Reading the saved results for this period. No match needs to be started.')).toBeInTheDocument();
  expect(screen.queryByRole('button',{name:'Set up an AI batch'})).not.toBeInTheDocument();
  expect(callbacks.onStartBatch).not.toHaveBeenCalled();
});

test('a failed first load leaves counts unknown and offers an explicit retry',async()=>{
  vi.mocked(api.loadLocalStatistics).mockRejectedValueOnce(new Error('Results unavailable'));
  render(<LocalInsights {...props()}/>);
  expect(await screen.findByRole('alert')).toHaveTextContent('no zero results are assumed');
  expect(screen.getByRole('heading',{name:'Local results unavailable'})).toBeInTheDocument();
  expect([...document.querySelectorAll('.insight-kpis strong')].map(e=>e.textContent)).toEqual(['—','—','—','—']);
  expect(screen.queryByRole('heading',{name:'Loading local results…'})).not.toBeInTheDocument();
  expect(screen.queryByText('No results in this view yet')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:'Retry results'}));
  await screen.findByText('Saved pilot');
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});

test('manual refresh and failure retain the current period data; retry can replace it',async()=>{
  render(<LocalInsights {...props()}/>);
  await screen.findByText('Saved pilot');
  const pending=deferred<LocalStatistics>();
  vi.mocked(api.loadLocalStatistics).mockReturnValueOnce(pending.promise);
  fireEvent.click(screen.getByRole('button',{name:'Refresh results'}));
  expect(screen.getByText('Saved pilot')).toBeInTheDocument();
  expect(screen.getByRole('button',{name:'Refreshing results…'})).toBeDisabled();
  await act(async()=>{pending.reject(new Error('Refresh failed'));});
  expect(screen.getByRole('alert')).toHaveTextContent('Previously loaded results remain visible');
  expect(screen.getByText('Saved pilot')).toBeInTheDocument();
  vi.mocked(api.loadLocalStatistics).mockResolvedValueOnce(stats('Updated pilot'));
  fireEvent.click(screen.getByRole('button',{name:'Retry results'}));
  await screen.findByText('Updated pilot');
  expect(screen.queryByText('Saved pilot')).not.toBeInTheDocument();
});

test('changing period hides mismatched numbers and ignores late prior-period reads',async()=>{
  render(<LocalInsights {...props()}/>);
  await screen.findByText('Saved pilot');
  const sevenDays=deferred<LocalStatistics>();
  vi.mocked(api.loadLocalStatistics).mockReturnValueOnce(sevenDays.promise);
  fireEvent.change(screen.getByLabelText('Period'),{target:{value:'7'}});
  expect(screen.queryByText('Saved pilot')).not.toBeInTheDocument();
  expect(document.querySelector('.insight-kpis strong')).toHaveTextContent('—');
  vi.mocked(api.loadLocalStatistics).mockResolvedValueOnce(stats('Thirty day pilot',30));
  fireEvent.change(screen.getByLabelText('Period'),{target:{value:'30'}});
  await screen.findByText('Thirty day pilot');
  await act(async()=>{sevenDays.resolve(stats('Stale seven day pilot',7));});
  expect(screen.getByText('Thirty day pilot')).toBeInTheDocument();
  expect(screen.queryByText('Stale seven day pilot')).not.toBeInTheDocument();
});

test('automatic polling waits for each read to finish and stops after unmount',async()=>{
  vi.useFakeTimers();
  const first=deferred<LocalStatistics>();const second=deferred<LocalStatistics>();
  vi.mocked(api.loadLocalStatistics).mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
  const view=render(<LocalInsights {...props()}/>);
  await act(async()=>{await vi.advanceTimersByTimeAsync(90_000);});
  expect(api.loadLocalStatistics).toHaveBeenCalledTimes(1);
  await act(async()=>{first.resolve(stats());});
  await act(async()=>{await vi.advanceTimersByTimeAsync(29_999);});
  expect(api.loadLocalStatistics).toHaveBeenCalledTimes(1);
  await act(async()=>{await vi.advanceTimersByTimeAsync(1);});
  expect(api.loadLocalStatistics).toHaveBeenCalledTimes(2);
  await act(async()=>{await vi.advanceTimersByTimeAsync(90_000);});
  expect(api.loadLocalStatistics).toHaveBeenCalledTimes(2);
  view.unmount();
  await act(async()=>{second.resolve(stats());await vi.advanceTimersByTimeAsync(90_000);});
  expect(api.loadLocalStatistics).toHaveBeenCalledTimes(2);
});

test('saving advanced tracking refreshes without clearing the loaded comparison',async()=>{
  render(<LocalInsights {...props()}/>);
  await screen.findByText('Saved pilot');
  const pending=deferred<LocalStatistics>();
  vi.mocked(api.loadLocalStatistics).mockReturnValueOnce(pending.promise);
  fireEvent.click(screen.getByRole('button',{name:'Advanced tracking saved'}));
  expect(screen.getByText('Saved pilot')).toBeInTheDocument();
  await act(async()=>{pending.resolve(stats('Refreshed pilot'));});
  await screen.findByText('Refreshed pilot');
});

test('a successfully loaded empty view offers setup, but never launches it automatically',async()=>{
  vi.mocked(api.loadLocalStatistics).mockResolvedValue({...stats(),agents:[],summary:{matches:0,seatResults:0,agents:0,decks:0,players:0}});
  const callbacks=props();render(<LocalInsights {...callbacks}/>);
  await screen.findByText('No results in this view yet');
  expect(document.querySelector('.insight-kpis strong')).toHaveTextContent('0');
  expect(callbacks.onStartBatch).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button',{name:'Set up an AI batch'}));
  await waitFor(()=>expect(callbacks.onStartBatch).toHaveBeenCalledTimes(1));
});
