import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import LeagueKeyConnection from './LeagueKeyConnection';
import * as api from './api';
vi.mock('./api',()=>({saveApiKey:vi.fn(),testLeagueMcp:vi.fn()}));
beforeEach(()=>{vi.clearAllMocks();localStorage.clear();vi.mocked(api.saveApiKey).mockResolvedValue({configured:true});vi.mocked(api.testLeagueMcp).mockResolvedValue({connected:true,toolEvents:[]});});
afterEach(cleanup);
const account={configured:true,valid:true,reason:'Agent key ready'};
const key='ddl_agent_fake_test_key_not_real_123456';
test('key-only setup saves and explicitly tests MCP, without username/password or storing the input in browser storage',async()=>{
  render(<LeagueKeyConnection account={null} siteUrl="https://deepdeckleague.com" onChanged={vi.fn()}/>);
  expect(screen.queryByLabelText('Email or username')).toBeNull();
  expect(screen.getByRole('link')).toHaveAttribute('href','https://deepdeckleague.com/account');
  fireEvent.change(screen.getByLabelText('League account API key'),{target:{value:key}});
  fireEvent.click(screen.getByRole('button',{name:'Save & test connection'}));
  expect(await screen.findByText('Connected to League MCP with the configured credential.')).toBeVisible();
  expect(api.saveApiKey).toHaveBeenCalledWith(key);
  expect(api.testLeagueMcp).toHaveBeenCalledOnce();
  expect(screen.getByLabelText('League account API key')).toHaveValue('');
  expect(JSON.stringify(localStorage)).not.toContain(key);
});
test('a saved key is not a claimed MCP connection; server permission denial remains visible',async()=>{
  vi.mocked(api.testLeagueMcp).mockResolvedValue({connected:false,toolEvents:[{tool:'league_mcp_connection',ok:false,transport:'mcp',error:'HTTP 403: Server-reported MCP entitlement required.'}]});
  render(<LeagueKeyConnection account={account} siteUrl="https://deepdeckleague.com" onChanged={vi.fn()}/>);
  expect(api.testLeagueMcp).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button',{name:'Test saved key · no LLM call'}));
  expect(await screen.findByText('HTTP 403: Server-reported MCP entitlement required.')).toBeVisible();
  expect(screen.queryByText('Connected to League MCP with the configured credential.')).toBeNull();
  expect(api.saveApiKey).not.toHaveBeenCalled();
});
test('refresh failure does not invalidate a successful MCP test or resend the key',async()=>{
  render(<LeagueKeyConnection account={account} siteUrl="https://deepdeckleague.com" onChanged={vi.fn().mockRejectedValue(new Error('offline overview'))}/>);
  fireEvent.click(screen.getByRole('button',{name:'Test saved key · no LLM call'}));
  await waitFor(()=>expect(screen.getByText(/overview could not refresh/)).toBeVisible());
  expect(screen.getByText('Connected to League MCP with the configured credential.')).toBeVisible();
  expect(api.saveApiKey).not.toHaveBeenCalled();
});

test('Patreon refusal offers membership and account connection, without claiming reconnect grants access',async()=>{
  vi.mocked(api.testLeagueMcp).mockResolvedValue({connected:false,toolEvents:[{tool:'league_mcp_connection',ok:false,transport:'mcp',errorCode:'membership_required',httpStatus:403,error:'HTTP 403: Active Patreon membership required.'}]});
  render(<LeagueKeyConnection account={account} siteUrl="https://www.deepdeckleague.com" onChanged={vi.fn()}/>);
  const connect=vi.fn();
  window.addEventListener('deepdeck:open-league-settings',connect);
  try {
    fireEvent.click(screen.getByRole('button',{name:/Test saved key/}));
    expect(await screen.findByRole('link',{name:/View Patreon membership options/})).toHaveAttribute('href','https://www.deepdeckleague.com/support');
    expect(screen.getByRole('link',{name:/Sign in \/ manage my League account/})).toHaveAttribute('href','https://www.deepdeckleague.com/account');
    fireEvent.click(screen.getByRole('button',{name:'Connect with a League API key'}));
    expect(connect).toHaveBeenCalledOnce();
    expect(screen.getByText(/Your LLM subscription is separate/)).toBeVisible();
  } finally {window.removeEventListener('deepdeck:open-league-settings',connect);}
});

test('ordinary key rejection does not advertise a paid membership as its fix',async()=>{
  vi.mocked(api.testLeagueMcp).mockResolvedValue({connected:false,toolEvents:[{tool:'league_mcp_connection',ok:false,transport:'mcp',errorCode:'credential_rejected',httpStatus:401,error:'HTTP 401: Key revoked.'}]});
  render(<LeagueKeyConnection account={account} siteUrl="https://www.deepdeckleague.com" onChanged={vi.fn()}/>);
  fireEvent.click(screen.getByRole('button',{name:/Test saved key/}));
  expect(await screen.findByText('HTTP 401: Key revoked.')).toBeVisible();
  expect(screen.queryByRole('link',{name:/View Patreon/})).toBeNull();
});

test('protocol incompatibility does not offer reconnecting or Patreon as its repair',async()=>{
  vi.mocked(api.testLeagueMcp).mockResolvedValue({connected:false,toolEvents:[{tool:'league_mcp_connection',ok:false,transport:'mcp',errorCode:'protocol_mismatch',error:'Missing resultType; Patreon status is not the cause.'}]});
  render(<LeagueKeyConnection account={account} siteUrl="https://www.deepdeckleague.com" onChanged={vi.fn()}/>);
  fireEvent.click(screen.getByRole('button',{name:/Test saved key/}));
  expect(await screen.findByRole('link',{name:/MCP compatibility information/})).toHaveAttribute('href','https://www.deepdeckleague.com/developers');
  expect(screen.queryByRole('link',{name:/View Patreon/})).toBeNull();
  expect(screen.queryByRole('link',{name:/Sign in \/ manage/})).toBeNull();
  expect(screen.queryByRole('button',{name:'Connect with a League API key'})).toBeNull();
  expect(screen.getByText(/Keep your current credential/)).toBeVisible();
});
