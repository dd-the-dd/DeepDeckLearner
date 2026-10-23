import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import AdvancedInsights from './AdvancedInsights';
import * as api from './api';

vi.mock('./api', async original => ({...await original<typeof import('./api')>(), loadAdvancedStatistics:vi.fn(), loadStatisticsStorage:vi.fn(), saveStatisticsStorage:vi.fn()}));
const settings: api.StatisticsStorage = {storageLimitBytes:128 * 1024 ** 2, usedBytes:100, legacyBytes:0, matchFiles:2, rules:[], lastError:null, quotaScope:'Local result files only', metrics:{life:'Life', draws:'Observed cards drawn'}, groups:[{id:'board', name:'Board and life', description:'Observed board'}, {id:'resources', name:'Cards and mana', description:'Observed resources'}]};
const empty: api.AdvancedStatistics = {kind:'agent', id:'a', seatResults:5, trackedSeatResults:0, totals:{}, snapshots:{}, curve:[], droppedTurnSamples:0, note:'Missing fields are not zeroes.'};
beforeEach(() => {vi.clearAllMocks(); vi.mocked(api.loadStatisticsStorage).mockResolvedValue(settings); vi.mocked(api.loadAdvancedStatistics).mockResolvedValue(empty); vi.mocked(api.saveStatisticsStorage).mockImplementation(async payload => ({...settings, ...payload}));});
afterEach(cleanup);

test('advanced tracking is off by default and saving targets a specific entity with its quota', async () => {
  const saved = vi.fn(); render(<AdvancedInsights kind="agent" days={0} targets={[{id:'a', name:'Tempo pilot'}]} onSaved={saved} />);
  const board = await screen.findByRole('checkbox', {name:/Board and life/});
  expect(board).not.toBeChecked();
  expect(await screen.findByText('No advanced samples yet')).toBeInTheDocument();
  fireEvent.click(board);
  fireEvent.change(screen.getByLabelText('Maximum statistics space (MB)'), {target:{value:'64'}});
  fireEvent.click(screen.getByRole('button', {name:'Save tracking & storage'}));
  await waitFor(() => expect(saved).toHaveBeenCalled());
  expect(api.saveStatisticsStorage).toHaveBeenCalledWith({storageLimitBytes:64 * 1024 ** 2, rules:[{kind:'agent', id:'a', groups:['board']}]});
});

test('unsaved per-target groups survive switching targets, then save together', async () => {
  render(<AdvancedInsights kind="deck" days={30} targets={[{id:'a', name:'Tempo'}, {id:'b', name:'Combo'}]} onSaved={vi.fn()} />);
  fireEvent.click(await screen.findByRole('checkbox', {name:/Board and life/}));
  fireEvent.change(screen.getByLabelText('Track this deck'), {target:{value:'b'}});
  fireEvent.click(screen.getByRole('checkbox', {name:/Cards and mana/}));
  fireEvent.click(screen.getByRole('button', {name:'Save tracking & storage'}));
  await waitFor(() => expect(api.saveStatisticsStorage).toHaveBeenCalled());
  expect(vi.mocked(api.saveStatisticsStorage).mock.calls[0][0].rules).toEqual([{kind:'deck', id:'a', groups:['board']}, {kind:'deck', id:'b', groups:['resources']}]);
});

test('numeric summaries and chart show sample sizes rather than invented history', async () => {
  vi.mocked(api.loadAdvancedStatistics).mockResolvedValue({...empty, trackedSeatResults:2, totals:{draws:{sum:10, mean:5, samples:2}}, snapshots:{life:{mean:17, samples:4}}, curve:[{turn:1, values:{life:{mean:20, samples:2}}}, {turn:2, values:{life:{mean:14, samples:2}}}]});
  render(<AdvancedInsights kind="player" days={0} targets={[{id:'a', name:'You'}]} onSaved={vi.fn()} />);
  expect(await screen.findByText('Observed cards drawn')).toBeInTheDocument();
  expect(screen.getByText('Mean per tracked seat result · n=2')).toBeInTheDocument();
  expect(await screen.findByRole('img', {name:'Life: observed mean by game turn'})).toBeInTheDocument();
  expect(screen.getByText(/not a prediction/)).toBeInTheDocument();
});

test('storage errors do not silently clear the draft or promise data was saved', async () => {
  vi.mocked(api.saveStatisticsStorage).mockRejectedValue(new Error('Not enough disk space'));
  render(<AdvancedInsights kind="agent" days={0} targets={[{id:'a', name:'Tempo'}]} onSaved={vi.fn()} />);
  const board = await screen.findByRole('checkbox', {name:/Board and life/}); fireEvent.click(board);
  fireEvent.click(screen.getByRole('button', {name:'Save tracking & storage'}));
  expect(await screen.findByRole('alert')).toHaveTextContent('Not enough disk space');
  expect(board).toBeChecked();
});

test('an older Engine cannot silently appear to enforce the new storage budget', async () => {
  vi.mocked(api.loadStatisticsStorage).mockResolvedValue({...settings, legacyEngineBytes:1024 ** 2, engineAnalyticsDisabled:false});
  render(<AdvancedInsights kind="agent" days={0} targets={[{id:'a', name:'Tempo'}]} onSaved={vi.fn()} />);
  expect(await screen.findByRole('alert')).toHaveTextContent('did not confirm');
  expect(screen.getByText(/historical Engine statistics/)).toHaveTextContent('1.0 MB');
});
