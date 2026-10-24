import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,beforeEach,expect,test,vi} from 'vitest';
import ReplayStorageControl from './ReplayStorageControl';
import * as api from './api';

vi.mock('./api',async original=>({...await original<typeof import('./api')>(),saveExperienceSettings:vi.fn(),loadReplayInbox:vi.fn(),loadJobs:vi.fn()}));
const settings:api.ExperienceSettings={autoCollectLeagueReplays:false,replayPollSeconds:1200,replayStorageLimitBytes:2*1024**3,replayStorageUsedBytes:123};
beforeEach(()=>{
  vi.clearAllMocks();
  vi.mocked(api.saveExperienceSettings).mockResolvedValue(settings);
  vi.mocked(api.loadReplayInbox).mockResolvedValue({items:[],observedAtUnixMs:0});
  vi.mocked(api.loadJobs).mockResolvedValue([]);
  HTMLDialogElement.prototype.showModal=function(){this.setAttribute('open','');};
  HTMLDialogElement.prototype.close=function(){this.removeAttribute('open');this.dispatchEvent(new Event('close'));};
});
afterEach(cleanup);
test('only a compact gear is visible and no inbox is queried before opening',()=>{
  render(<ReplayStorageControl settings={settings} jobs={[]}/>);
  expect(screen.getByRole('button',{name:'Limit replay storage'})).toHaveAttribute('aria-expanded','false');
  expect(screen.queryByRole('slider')).toBeNull();expect(screen.queryByRole('dialog')).toBeNull();
  expect(api.loadReplayInbox).not.toHaveBeenCalled();expect(api.saveExperienceSettings).not.toHaveBeenCalled();
});
test('gear opens quota settings; saving reports to parent without changing collection',async()=>{
  const saved=vi.fn();render(<ReplayStorageControl settings={settings} jobs={[]} onSaved={saved}/>);
  fireEvent.click(screen.getByRole('button',{name:'Limit replay storage'}));
  expect(screen.getByRole('dialog',{name:'Replay settings'})).toBeVisible();
  fireEvent.change(screen.getByRole('slider'),{target:{value:'3'}});
  fireEvent.click(screen.getByRole('button',{name:'Save replay settings'}));
  await waitFor(()=>expect(api.saveExperienceSettings).toHaveBeenCalledWith({replayStorageLimitBytes:3*1024**3}));
  expect(saved).toHaveBeenCalledWith(settings);
});
test('closing hides the panel without discarding an unsaved quota draft',()=>{
  render(<ReplayStorageControl settings={settings} jobs={[]}/>);
  fireEvent.click(screen.getByRole('button',{name:'Limit replay storage'}));
  fireEvent.change(screen.getByRole('slider'),{target:{value:'4'}});
  fireEvent.click(screen.getByRole('button',{name:'Close replay settings'}));
  expect(screen.queryByRole('dialog')).toBeNull();
  fireEvent.click(screen.getByRole('button',{name:'Limit replay storage'}));
  expect(screen.getByRole('slider')).toHaveValue('4');expect(api.saveExperienceSettings).not.toHaveBeenCalled();
});
test('unknown settings cannot be saved as invented defaults',()=>{
  render(<ReplayStorageControl settings={null} jobs={[]}/>);
  fireEvent.click(screen.getByRole('button',{name:'Limit replay storage'}));
  expect(screen.getByRole('slider')).toBeDisabled();
  expect(screen.getByRole('button',{name:'Save replay settings'})).toBeDisabled();
});
