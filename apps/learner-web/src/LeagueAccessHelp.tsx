import { useEffect, useRef, useState } from 'react';
import { loadLeagueAccount, loadLeagueConnection, loadAccountStatus, type LeagueAccount, type LeagueConnection, type AccountStatus } from './api';

// Account, matchmaking and MCP grants are separate permissions; never infer one from another.
export default function LeagueAccessHelp({onConnect}: {onConnect?:()=>void}) {
  const [account,setAccount] = useState<LeagueAccount|null>(null);
  const [agent,setAgent] = useState<AccountStatus|null>(null);
  const [server,setServer] = useState<LeagueConnection|null>(null);
  const [checking,setChecking] = useState(true);
  const [failed,setFailed] = useState(false);
  const request = useRef(0);
  async function check() {
    const current = ++request.current;
    setChecking(true); setFailed(false);
    const results=await Promise.allSettled([loadLeagueAccount(),loadAccountStatus(),loadLeagueConnection()]);
    if (request.current!==current) return;
    if(results[0].status==='fulfilled')setAccount(results[0].value); else setAccount(null);
    if(results[1].status==='fulfilled')setAgent(results[1].value); else setAgent(null);
    if(results[2].status==='fulfilled')setServer(results[2].value); else setServer(null);
    setFailed(results.some(result=>result.status==='rejected')); setChecking(false);
  }
  useEffect(()=>{
    const refresh=()=>{void check();};
    refresh();
    window.addEventListener('focus',refresh);
    window.addEventListener('deepdeck:league-connection-changed',refresh);
    return()=>{++request.current;window.removeEventListener('focus',refresh);window.removeEventListener('deepdeck:league-connection-changed',refresh);};
  },[]);
  return <div className="league-access-help" aria-live="polite">
    <strong>{checking?'Checking League connection…':failed?'Some connection checks are unavailable':'League connection status'}</strong>
    <dl>
      <div><dt>Server</dt><dd>{checking?'Checking…':server?.reachable?'Reachable':server?'Unavailable':'Not verified'}</dd></div>
      <div><dt>League API key · matchmaking</dt><dd>{checking?'Checking…':agent?.valid?'Key accepted':agent?.configured?'Needs attention':agent?'Not configured':'Not verified'}</dd></div>
      <div><dt>Website session · optional deck sync</dt><dd>{checking?'Checking…':account?.valid?'Connected':account?.configured?'Session needs attention':account?'Not connected · optional':'Not verified'}</dd></div>
    </dl>
    {!checking && server && !server.reachable && <p>{server.reason}</p>}
    {!checking && account?.configured && !account.valid && <p>{account.reason}</p>}
    <p>Connect with your League API key in Settings; no email or password is needed for MCP. An accepted matchmaking key does not confirm MCP access: test MCP separately to check the server’s permissions. The website session is only for deck synchronization operations that still require it.</p>
    <div className="league-access-actions"><a href={server?.siteUrl || 'https://deepdeckleague.com'} target="_blank" rel="noreferrer">Open DeepDeck League ↗</a>{onConnect&&<button type="button" onClick={onConnect}>Connect League in Settings</button>}<button type="button" disabled={checking} onClick={()=>void check()}>{checking?'Checking…':'Check League connection'}</button></div>
  </div>;
}
