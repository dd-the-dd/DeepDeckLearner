import { useRef, useState, type FormEvent } from 'react';
import AsyncActionButton from './AsyncActionButton';
import AssistantToolEvents from './AssistantToolEvents';
import { saveApiKey, testLeagueMcp, type AccountStatus, type AssistantToolEvent } from './api';

export default function LeagueKeyConnection({account,siteUrl,onChanged}: {account:AccountStatus|null;siteUrl:string;onChanged:()=>void|Promise<void>}) {
  const [key,setKey]=useState('');
  const [busy,setBusy]=useState(false);
  const [saved,setSaved]=useState(false);
  const [connected,setConnected]=useState<boolean|null>(null);
  const [error,setError]=useState('');
  const [warning,setWarning]=useState('');
  const [events,setEvents]=useState<AssistantToolEvent[]>([]);
  const checkVersion=useRef(0);
  async function check(save:boolean) {
    if(busy)return;
    const version=++checkVersion.current;
    setBusy(true);setError('');setWarning('');setConnected(null);setEvents([]);
    try {
      if(save){await saveApiKey(key.trim());setKey('');setSaved(true);window.dispatchEvent(new Event('deepdeck:league-connection-changed'));}
      const result=await testLeagueMcp();
      setConnected(result.connected);setEvents(result.toolEvents);
    }catch(reason){setError(reason instanceof Error?reason.message:'The League connection could not be checked.');}
    finally{
      setBusy(false);
      try{await onChanged();}catch{if(checkVersion.current===version)setWarning('The connection result above is retained, but the account overview could not refresh.');}
    }
  }
  function submit(event:FormEvent){event.preventDefault();void check(true);}
  const validInput=key.trim().startsWith('ddl_agent_')&&key.trim().length>=24;
  return <div className="league-key-connection">
    <p>Generate an account API key on the League website, paste it here, then test. No email or password is needed for MCP research or agent matchmaking.</p>
    <a className="league-key-link" href={new URL('/account',siteUrl).href} target="_blank" rel="noreferrer">Generate or manage my League API key ↗</a>
    <form onSubmit={submit}><label>League account API key<input type="password" autoComplete="off" spellCheck={false} value={key} disabled={busy} onChange={event=>setKey(event.target.value)} placeholder={account?.configured||saved?'Saved key kept unless replaced':'ddl_agent_…'}/></label><AsyncActionButton loading={busy} loadingLabel="Checking key and MCP…" disabled={!validInput}>Save & test connection</AsyncActionButton></form>
    <button type="button" disabled={busy||!(account?.configured||saved)} onClick={()=>void check(false)}>Test saved key · no LLM call</button>
    <p className={connected===true?'inline-success':'inline-notice'} role="status">{busy?'Checking the credential and MCP access on the server…':connected===true?'Connected to League MCP with the configured credential.':connected===false?'MCP access was not confirmed. Saving a key does not grant extra permissions; check the server response below.':saved?'Key saved locally. MCP access is not yet confirmed.':'A saved key is not proof of MCP access. Test it to confirm.'}</p>
    <small>The server checks the key’s scopes and the account’s MCP entitlement. Local play and your LLM connection are independent.</small>
    <AssistantToolEvents events={events}/>
    {error&&<p className="inline-error" role="alert">{error}</p>}
    {warning&&<p className="inline-notice" role="status">{warning}</p>}
  </div>;
}
