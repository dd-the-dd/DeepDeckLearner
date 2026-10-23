import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import LeagueAccessHelp from './LeagueAccessHelp';
import * as api from './api';

vi.mock('./api',()=>({loadLeagueAccount:vi.fn(),loadAccountStatus:vi.fn(),loadLeagueConnection:vi.fn()}));
beforeEach(()=>{
  vi.clearAllMocks();
  vi.mocked(api.loadLeagueAccount).mockResolvedValue({configured:false,valid:false,reason:'Sign in here to synchronize decks.'});
  vi.mocked(api.loadAccountStatus).mockResolvedValue({configured:true,valid:true,reason:'Agent key accepted'});
  vi.mocked(api.loadLeagueConnection).mockResolvedValue({siteUrl:'https://deepdeckleague.com',apiUrl:'https://deepdeckleague.com/api/v1',environment:'production',reachable:true,releaseProfile:null,capabilities:[],naturalCardSearchEnabled:false,reason:'Server reachable'});
});
afterEach(cleanup);
test('separates server, account, agent-key and MCP permissions and links to the actual server',async()=>{
  const connect=vi.fn();
  render(<LeagueAccessHelp onConnect={connect}/>);
  expect(await screen.findByText('Reachable')).toBeVisible();
  expect(screen.getByText('Not connected · optional')).toBeVisible();
  expect(screen.getByText('Key accepted')).toBeVisible();
  expect(screen.getByText(/accepted matchmaking key does not confirm MCP access/)).toBeVisible();
  expect(screen.queryByText('Sign in here to synchronize decks.')).toBeNull();
  expect(screen.getByRole('link')).toHaveAttribute('href','https://deepdeckleague.com');
  fireEvent.click(screen.getByRole('button',{name:'Connect League in Settings'}));
  expect(connect).toHaveBeenCalledOnce();
  fireEvent.click(screen.getByRole('button',{name:'Check League connection'}));
  await waitFor(()=>expect(api.loadLeagueAccount).toHaveBeenCalledTimes(2));
});
test('failed reads show unknown rather than claiming an account was disconnected',async()=>{
  vi.mocked(api.loadLeagueAccount).mockRejectedValue(new Error('temporary failure'));
  render(<LeagueAccessHelp/>);
  expect(await screen.findByText('Some connection checks are unavailable')).toBeVisible();
  expect(screen.getByText('Not verified')).toBeVisible();
  expect(screen.queryByText('Sign-in needed')).toBeNull();
});
test('account sign-in from Settings refreshes the contextual helper without resending credentials',async()=>{
  render(<LeagueAccessHelp/>);
  await screen.findByText('Not connected · optional');
  vi.mocked(api.loadLeagueAccount).mockResolvedValue({configured:true,valid:true,reason:'Connected'});
  fireEvent(window,new Event('deepdeck:league-connection-changed'));
  expect(await screen.findByText('Connected')).toBeVisible();
  expect(api.loadLeagueAccount).toHaveBeenCalledTimes(2);
});
