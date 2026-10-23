import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,beforeEach,expect,test,vi} from 'vitest';
import ReplayStorageSettings from './ReplayStorageSettings';
import ReplayCollectionStatus from './ReplayCollectionStatus';
import * as api from './api';

vi.mock('./api',async original=>({...await original<typeof import('./api')>(),saveExperienceSettings:vi.fn()}));
const settings:api.ExperienceSettings={autoCollectLeagueReplays:false,replayPollSeconds:1200,replayStorageLimitBytes:2*1024**3,replayStorageUsedBytes:123};
beforeEach(()=>{vi.clearAllMocks();vi.mocked(api.saveExperienceSettings).mockResolvedValue(settings);});
afterEach(cleanup);

test('changing disk quota never re-enables collection or overwrites the polling interval',async()=>{
  const saved=vi.fn();render(<ReplayStorageSettings settings={settings} onSaved={saved}/>);
  expect(screen.getByLabelText('Automatically collect League replays')).not.toBeChecked();
  fireEvent.change(screen.getByRole('slider'),{target:{value:'3'}});
  fireEvent.click(screen.getByRole('button',{name:'Save replay settings'}));
  await waitFor(()=>expect(api.saveExperienceSettings).toHaveBeenCalledWith({replayStorageLimitBytes:3*1024**3}));
  expect(saved).toHaveBeenCalledWith(settings);
});

test('a settings refresh keeps the edited quota and adopts untouched collection changes',async()=>{
  const view=render(<ReplayStorageSettings settings={{...settings,autoCollectLeagueReplays:true}} onSaved={vi.fn()}/>);
  fireEvent.change(screen.getByRole('slider'),{target:{value:'4'}});
  view.rerender(<ReplayStorageSettings settings={{...settings,replayStorageLimitBytes:5*1024**3}} onSaved={vi.fn()}/>);
  expect(screen.getByRole('slider')).toHaveValue('4');
  expect(screen.getByLabelText('Automatically collect League replays')).not.toBeChecked();
  fireEvent.click(screen.getByRole('button',{name:'Save replay settings'}));
  await waitFor(()=>expect(api.saveExperienceSettings).toHaveBeenCalledWith({replayStorageLimitBytes:4*1024**3}));
});

test('explicit pause sends only that flag and does not modify storage',async()=>{
  render(<ReplayStorageSettings settings={{...settings,autoCollectLeagueReplays:true}} onSaved={vi.fn()}/>);
  fireEvent.click(screen.getByLabelText('Automatically collect League replays'));
  fireEvent.click(screen.getByRole('button',{name:'Save replay settings'}));
  await waitFor(()=>expect(api.saveExperienceSettings).toHaveBeenCalledWith({autoCollectLeagueReplays:false}));
});

test('failed save preserves editable choices and discard restores the latest settings',async()=>{
  vi.mocked(api.saveExperienceSettings).mockRejectedValueOnce(new Error('Controller unavailable'));
  render(<ReplayStorageSettings settings={settings} onSaved={vi.fn()}/>);
  fireEvent.change(screen.getByRole('slider'),{target:{value:'4'}});
  fireEvent.click(screen.getByRole('button',{name:'Save replay settings'}));
  expect(await screen.findByRole('alert')).toHaveTextContent('Controller unavailable');
  expect(screen.getByRole('slider')).toHaveValue('4');
  fireEvent.click(screen.getByRole('button',{name:'Discard replay changes'}));
  expect(screen.getByRole('slider')).toHaveValue('2');
  expect(screen.getByRole('button',{name:'Save replay settings'})).toBeDisabled();
});

test('missing settings are not shown as enabled collection or editable defaults',()=>{
  render(<ReplayStorageSettings settings={null} onSaved={vi.fn()}/>);
  expect(screen.getByRole('slider')).toBeDisabled();
  expect(screen.getByLabelText('Automatically collect League replays')).toBeDisabled();
});

const active={id:'run',model_id:'pilot',kind:'matchmaking.agent',status:'running',label:'Tempo agent',details:{replaySync:{enabled:true,collected:12,pending:2,lastError:'Replay storage is full.',checkedAtUnixMs:1000}}} as api.Job;
test('collection panel distinguishes configured pause, last report, pending games and quota failure',()=>{
  render(<ReplayCollectionStatus settings={settings} jobs={[active]}/>);
  expect(screen.getByText('Automatic collection is paused')).toBeInTheDocument();
  expect(screen.getByText('12 collected in total · 2 pending in the last report')).toBeInTheDocument();
  expect(screen.getByText(/Replay storage is full/)).toBeInTheDocument();
  expect(screen.getByText(/Collected totals are historical/)).toBeInTheDocument();
});

test('active agent without report does not imply recordings were collected',()=>{
  render(<ReplayCollectionStatus settings={{...settings,autoCollectLeagueReplays:true}} jobs={[{...active,details:{}}]}/>);
  expect(screen.getByText(/No collection report received yet/)).toBeInTheDocument();
  expect(screen.queryByText(/0 collected/)).toBeNull();
});

test('stopped agents with pending games remain actionable and stale old reports do not reappear',()=>{
  const stopped={...active,status:'stopped' as const};
  const view=render(<ReplayCollectionStatus settings={{...settings,autoCollectLeagueReplays:true}} jobs={[stopped]}/>);
  expect(screen.getByText(/Use the pending inbox below/)).toBeInTheDocument();
  view.rerender(<ReplayCollectionStatus settings={settings} jobs={[stopped,{...stopped,id:'new-run',details:{replaySync:{...active.details!.replaySync!,pending:0,checkedAtUnixMs:2000}}}]}/>);
  expect(screen.queryByText(/Use the pending inbox below/)).toBeNull();
});
