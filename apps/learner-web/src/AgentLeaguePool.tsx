import {useEffect,useRef,useState} from 'react';
import LeagueCapacityHelp from './LeagueCapacityHelp';
import LeagueNotice from './LeagueNotice';
import {
  ApiError,appendLeagueQueueEntry,controlLeagueQueue,loadLeagueQueue,prepareLeagueDecks,
  removeLeagueQueueEntry,saveLeagueQueue,syncDeckWorkspace,
  type AccountStatus,type CompetitionSummary,type Job,type LeagueAccount,type LeagueCapacity,
  type LeagueConnection,type LeagueQueue,type LeagueQueueEntry,type ProgrammaticAgent,
} from './api';
import './league-agent-grid.css';

export type AgentLeaguePoolProps={account:AccountStatus|null;siteAccount:LeagueAccount|null;connection?:LeagueConnection|null;agents:ProgrammaticAgent[];competitions:CompetitionSummary[];jobs:Job[];refresh:()=>void|Promise<void>;openSettings:()=>void;preferredAgentId?:string;capacity?:LeagueCapacity|null};
const activeJob=(job:Job)=>job.kind==='matchmaking.agent'&&['queued','running'].includes(job.status);
const sameFormat=(a:string,b:string)=>a.trim().toLowerCase()===b.trim().toLowerCase();
export function poolAgentStatus(entry:LeagueQueueEntry,jobs:Job[],queue?:LeagueQueue|null):string {
  if(entry.state==='blocked'||entry.error)return 'Needs attention';
  if(entry.state==='done')return 'Finished';
  if(!entry.turn){
    if(queue?.status==='paused')return 'Paused';
    if(queue?.capacity?.status==='ready'&&queue.capacity.available===0)return 'Waiting for an account place';
    return 'Waiting for a local worker';
  }
  const job=jobs.find(job=>job.id===entry.turn?.jobId);
  if(!job)return 'Checking local worker';
  if(job.status==='queued')return 'Waiting for a local worker';
  if(!activeJob(job))return 'Checking result';
  const run=job.details?.leagueRun;
  if(run?.lastError)return 'Needs attention';
  if(run?.serverRegistered===false)return run.eventRegistered?'Reconnecting to League':'Connecting to League';
  if(run?.interventionRequired)return run.statusReason?.startsWith('Engine maintenance:')?'League maintenance — place reserved':'Tournament needs intervention';
  if(run?.eventRegistered&&run.phase==='registration')return 'Queued for tournament';
  if(run?.eventRegistered&&(run.phase==='season'||run.phase==='playoffs')){
    if(run.matchStatus==='running')return run.phase==='season'?'Playing — season':'Playing — playoffs';
    if(run.matchStatus==='scheduled')return 'Waiting for a match worker';
    if(run.matchStatus===undefined)return 'Checking match status';
    return 'Waiting for the next match';
  }
  if(run?.phase==='complete'||run?.phase==='cancelled')return 'Tournament finished';
  if(run?.serverRegistered&&run.waitingForRegistration)return `Waiting for the next ${entry.format} tournament`;
  if(run?.serverRegistered)return 'Connected — checking tournament';
  if(run&&run.activeEntries>0)return 'Competition status unavailable';
  return 'Connecting to League';
}

function AgentStatus({entry,jobs,queue}:{entry:LeagueQueueEntry;jobs:Job[];queue:LeagueQueue|null}){
  const run=jobs.find(job=>job.id===entry.turn?.jobId)?.details?.leagueRun;
  const [clock,setClock]=useState<{remaining:number;observed:number}|null>(null);
  const [now,setNow]=useState(()=>Date.now());
  useEffect(()=>{
    const remaining=Date.parse(run?.registrationClosesAt??'')-Date.parse(run?.serverNow??'');
    setClock(Number.isFinite(remaining)?{remaining,observed:Date.now()}:null);
  },[run?.registrationClosesAt,run?.serverNow]);
  useEffect(()=>{if(run?.phase!=='registration'||!clock)return;const timer=setInterval(()=>setNow(Date.now()),1000);return()=>clearInterval(timer);},[run?.phase,clock]);
  const seconds=clock&&!run?.interventionRequired?Math.max(0,Math.ceil((clock.remaining-Math.max(0,now-clock.observed))/1000)):null;
  return <><span className={`agent-pool-status ${entry.state}`} role="status">{poolAgentStatus(entry,jobs,queue)}</span>{run?.eventRegistered&&run.phase==='registration'&&seconds!==null&&<small role="timer" aria-label={`Tournament registration closes in ${Math.floor(seconds/60)} minutes ${seconds%60} seconds`}>{seconds>0?`${Math.floor(seconds/60)}:${String(seconds%60).padStart(2,'0')} until registration closes`:'Registration closed — checking tournament start'}</small>}{entry.error&&<small className="inline-error">{entry.error}</small>}{run?.lastError&&<small className="inline-error">{run.lastError}</small>}{run?.statusReason&&run.interventionRequired&&<small>{run.statusReason}</small>}</>;
}

export default function AgentLeaguePool({account,siteAccount,connection,agents,competitions,jobs,refresh,openSettings,preferredAgentId='',capacity=null}:AgentLeaguePoolProps){
  const [queue,setQueue]=useState<LeagueQueue|null>(null);
  const [agentId,setAgentId]=useState(preferredAgentId);
  const [leagueId,setLeagueId]=useState('');
  const [busy,setBusy]=useState(false);
  const [readError,setReadError]=useState('');
  const [error,setError]=useState('');
  const [notice,setNotice]=useState('');
  const [progress,setProgress]=useState('');
  const [reconcile,setReconcile]=useState(false);
  const [reload,setReload]=useState(0);
  const pending=useRef(false),epoch=useRef(0),mounted=useRef(false);
  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;};},[]);
  useEffect(()=>{
    let active=true;let timer:ReturnType<typeof setTimeout>;
    async function poll(){const version=epoch.current;try{const value=await loadLeagueQueue();if(active&&version===epoch.current&&!pending.current){setQueue(value);setReadError('');}}catch(reason){if(active&&version===epoch.current&&!pending.current)setReadError(reason instanceof Error?reason.message:'League pool unavailable.');}finally{if(active)timer=setTimeout(()=>void poll(),5000);}}
    void poll();return()=>{active=false;clearTimeout(timer);};
  },[reload]);
  const agent=agents.find(item=>item.id===agentId);
  const eligible=competitions.filter(item=>item.status.trim().toLowerCase()==='active'&&(!agent||sameFormat(item.format,agent.format)));
  const occupied=new Set([...(queue?.entries.map(entry=>entry.agentId)??[]),...jobs.filter(activeJob).map(job=>job.model_id)]);
  const localDrafts=agent?.decks.filter(deck=>deck.id.startsWith('localdeck-')&&deck.syncStatus?.needsPublishing!==false)??[];
  const deckProblem=agent&&(!agent.deckVersionIds.length||agent.decks.some(deck=>deck.unavailable))?'Update this agent’s mastered decks in Agents before joining.':'';
  const reported=queue?.capacity??capacity;
  const editable=!!queue&&queue.status==='paused'&&!queue.entries.some(entry=>entry.turn);
  const locked=busy||!queue||!!readError||reconcile||!!queue.controllerError;
  const supportsFormats=queue?.formatSeats!==undefined;

  async function act(operation:()=>Promise<void>){
    if(pending.current)return;
    pending.current=true;epoch.current+=1;setBusy(true);setError('');setNotice('');
    try{await operation();}catch(reason){if(mounted.current){setError(reason instanceof Error?reason.message:'The change could not be confirmed.');setReconcile(true);}}
    finally{epoch.current+=1;pending.current=false;if(mounted.current){setBusy(false);setProgress('');}}
  }
  async function reloadPool(){if(pending.current)return;await act(async()=>{const value=await loadLeagueQueue();setQueue(value);setReadError('');setReconcile(false);});setReload(value=>value+1);}
  async function add(id:string,competitionId:string,publish=false){
    const selected=agents.find(item=>item.id===id);
    const league=competitions.find(item=>item.versionId===competitionId&&item.status.trim().toLowerCase()==='active');
    if(locked||!account?.valid||!selected||!league||!sameFormat(selected.format,league.format)||!selected.deckVersionIds.length||selected.decks.some(deck=>deck.unavailable)||occupied.has(id))return;
    const drafts=selected.decks.filter(deck=>deck.id.startsWith('localdeck-')&&deck.syncStatus?.needsPublishing!==false);
    // Selecting a league never silently publishes private local deck drafts.
    if(drafts.length&&(!publish||!siteAccount?.valid))return;
    await act(async()=>{
      let accepted=false,writing=false,published=0;
      try{
        let current=await loadLeagueQueue();
        if(current.entries.some(entry=>entry.agentId===id))throw new Error('This agent is already in the pool. Refresh to see its status.');
        for(const deck of drafts){setProgress(`Publishing ${deck.name}…`);await syncDeckWorkspace(deck.id,'publish',false,deck.version);published+=1;}
        if(siteAccount?.valid){setProgress('Checking League decks…');await prepareLeagueDecks(selected.deckVersionIds);}
        current=await loadLeagueQueue();
        if(current.entries.some(entry=>entry.agentId===id))throw new Error('This agent was added elsewhere. Refresh the pool.');
        setProgress('Adding agent…');writing=true;
        const saved=await appendLeagueQueueEntry(current.revision,{agentId:id,competitionVersionId:competitionId,deckVersionIds:[...selected.deckVersionIds],lease:{mode:'continuous'}});
        accepted=true;setQueue(saved);setAgentId('');setLeagueId('');
        if(saved.status==='paused'&&!saved.entries.some(entry=>entry.turn)&&!saved.controllerError){
          setProgress('Connecting pool…');setQueue(await controlLeagueQueue('resume',saved.revision));
        }
        setNotice(`${selected.name} added. ${saved.status==='pausing'?'The pool is pausing; resume it when ready.':'Server places are assigned as capacity becomes available.'}`);
      }catch(reason){
        const uncertain=writing&&(!(reason instanceof ApiError)||reason.status>=500);
        throw new Error((accepted?'Agent added; pool activation could not be confirmed.':uncertain?'The pool update could not be confirmed.':reason instanceof Error?reason.message:'The agent could not be added.')+' Refresh before retrying.'+(published?` ${published} deck publication(s) remain saved on the site.`:''));
      }finally{try{await refresh();}catch{if(accepted)setNotice('Agent added. The library could not refresh; do not add it again. Refresh the pool to check.');}}
    });
  }
  function chooseAgent(id:string){setAgentId(id);const next=agents.find(a=>a.id===id),league=competitions.find(c=>c.versionId===leagueId);if(league&&next&&sameFormat(league.format,next.format))void add(id,leagueId);else setLeagueId('');}
  function chooseLeague(id:string){setLeagueId(id);if(agentId&&id)void add(agentId,id);}
  function changeRotation(strategy:LeagueQueue['strategy']){
    if(!queue||locked||!editable)return;
    // A reported allocation is the controller capability: never send this mode to older releases.
    if(strategy==='respect_format'&&!supportsFormats)return;
    const seats=queue.formatSeats;
    if(strategy==='respect_format'&&seats&&!seats.legacy&&!seats.commander){setError('Set format places before enabling format rotation.');return;}
    void act(async()=>{setQueue(await saveLeagueQueue({revision:queue.revision,strategy,entries:queue.entries,...(seats?{formatSeats:seats}:{})}));});
  }
  function changeFormatSeats(format:'legacy'|'commander',value:string){
    if(!queue?.formatSeats||locked||!editable||!/^\d+$/.test(value))return;
    const seats={...queue.formatSeats,[format]:Number(value)};
    if(!Number.isSafeInteger(seats[format]))return;
    if(reported?.status==='ready'&&reported.limit!==null&&seats.legacy+seats.commander>reported.limit){setError('Format places cannot exceed the account limit reported by the server.');return;}
    if(queue.strategy==='respect_format'&&!seats.legacy&&!seats.commander){setError('Keep at least one format place.');return;}
    void act(async()=>{setQueue(await saveLeagueQueue({revision:queue.revision,strategy:queue.strategy,formatSeats:seats,entries:queue.entries}));});
  }
  const independent=jobs.filter(job=>activeJob(job)&&!queue?.entries.some(entry=>entry.turn?.jobId===job.id));
  return <section className="feature-card agent-league-pool" aria-label="League agent pool">
    <header className="agent-pool-toolbar"><label>Rotation<select aria-label="Rotation" disabled={locked||!editable} value={queue?.strategy??'round_robin'} title="Pause the pool and let current competitions finish before changing rotation." onChange={event=>changeRotation(event.target.value as LeagueQueue['strategy'])}><option value="round_robin">Round robin</option><option value="random">Random</option>{supportsFormats&&<option value="respect_format">Respect format</option>}</select></label>
      <div className="agent-pool-capacity" role="status" aria-label="League places"><span>Account places</span><strong>{reported?.status==='ready'?`${reported.used} / ${reported.limit} used`:'Not verified'}</strong><small>{reported?.status==='ready'?`${reported.available} available`:reported?.reason??'Checking with the server…'}</small>{reported?.status==='ready'&&<details className="agent-pool-plan"><summary>More places?</summary><LeagueCapacityHelp siteUrl={connection?.siteUrl}/></details>}</div>
      <div className="agent-pool-actions"><button type="button" disabled={busy} onClick={()=>void reloadPool()} aria-label="Refresh League" title="Refresh League">↻</button>{queue&&<button type="button" disabled={locked||queue.status==='pausing'||(queue.status==='paused'&&!queue.entries.some(entry=>entry.state==='queued'))} onClick={()=>void act(async()=>{setQueue(await controlLeagueQueue(queue.status==='paused'?'resume':'pause',queue.revision));})}>{queue.status==='pausing'?'Finishing competitions…':queue.status==='paused'?'Resume pool':'Pause pool'}</button>}</div>
    </header>
    <p className="agent-pool-help">{queue?.strategy==='respect_format'?'Separate round-robin rotations keep the configured places for each format.':queue?.strategy==='random'?'Random selects an available agent for each turn; repeats are possible.':'Round robin gives each available agent a turn in list order.'} Keep the app open. Pausing lets current competitions finish.</p>
    {supportsFormats&&<details className="agent-format-places"><summary>Places by format{queue?.strategy==='respect_format'?' (active)':''}</summary>{(['legacy','commander'] as const).map(format=><label key={`${format}-${queue?.revision}`}>{format==='legacy'?'Legacy':'Commander'}<input aria-label={`${format} places`} type="number" min="0" step="1" max={reported?.status==='ready'?reported.limit??undefined:undefined} disabled={locked||!editable} defaultValue={queue!.formatSeats![format]} onKeyDown={event=>{if(event.key==='Enter')event.currentTarget.blur();}} onBlur={event=>{const value=event.currentTarget.value;if(value!==String(queue!.formatSeats![format]))changeFormatSeats(format,value);event.currentTarget.value=String(queue!.formatSeats![format]);}}/></label>)}</details>}
    {!account?.valid&&<p className="inline-notice">{account?.reason??'Connect your League API key to add agents.'} <button type="button" onClick={openSettings}>Connect League</button></p>}
    {readError&&<p role="alert" className="inline-error">{readError} Saved agents are not assumed removed.</p>}
    {error&&<LeagueNotice error label="League pool error" onDismiss={()=>setError('')}>{error}</LeagueNotice>}
    {notice&&<LeagueNotice label="League pool updated" onDismiss={()=>setNotice('')}>{notice}</LeagueNotice>}
    {queue?.controllerError&&<p role="alert" className="inline-error">{queue.controllerError}</p>}
    <div className="agent-pool-grid" role="table" aria-label="Agents and Leagues">
      <div className="agent-pool-row agent-pool-columns" role="row"><span role="columnheader">Agent</span><span role="columnheader">League</span><span role="columnheader">Status</span><span role="columnheader" className="sr-only">Actions</span></div>
      <div className="agent-pool-row agent-pool-add" role="row">
        <label role="cell"><span className="sr-only">Agent</span><select aria-label="Agent" disabled={locked||!account?.valid} value={agentId} onChange={event=>chooseAgent(event.target.value)}><option value="">+ Add an agent</option>{agents.filter(item=>!occupied.has(item.id)).map(item=><option value={item.id} key={item.id}>{item.name} · {item.format}</option>)}</select></label>
        <label role="cell"><span className="sr-only">League</span><select aria-label="League" disabled={locked||!account?.valid||!agent} value={leagueId} onChange={event=>chooseLeague(event.target.value)}><option value="">Choose a League</option>{eligible.map(item=><option key={item.versionId} value={item.versionId}>{item.name} · {item.timeControl}</option>)}</select></label>
        <span role="cell" className="agent-pool-add-status">{busy?progress||'Saving…':reconcile?'Refresh to reconcile':agent&&leagueId&&localDrafts.length?'Deck publication needed':'Adds automatically'}</span><span role="cell"/>
      </div>
      {queue?.entries.map(entry=><div className="agent-pool-row" role="row" key={entry.id}><span role="cell"><strong>{entry.name}</strong><small>{entry.format}</small></span><span role="cell">{competitions.find(item=>item.versionId===entry.competitionVersionId)?.name??'Saved League'}</span><span role="cell"><AgentStatus entry={entry} jobs={jobs} queue={queue}/></span><span role="cell" className="agent-pool-row-actions">{entry.state==='blocked'&&editable&&<button type="button" disabled={locked} onClick={()=>void act(async()=>{setQueue(await controlLeagueQueue('retry',queue!.revision,entry.id));})}>Review & retry</button>}<button type="button" aria-label={`Remove ${entry.name}`} title={entry.turn?'Pause and let this competition finish before removal.':'Remove from the pool'} disabled={locked||!!entry.turn} onClick={()=>void act(async()=>{setQueue(await removeLeagueQueueEntry(entry.id,queue!.revision));})}>Remove</button></span></div>)}
      {independent.map(job=><div className="agent-pool-row" role="row" key={job.id}><span role="cell"><strong>{agents.find(agent=>agent.id===job.model_id)?.name??job.label}</strong></span><span role="cell">Independent League connection</span><span role="cell"><span className="agent-pool-status">{job.details?.leagueRun?.activeEntries?'In competition':job.details?.leagueRun?.serverRegistered?'Connected to server':'Connecting'}</span></span><span role="cell"/></div>)}
    </div>
    {!queue&&!readError&&<p role="status">Loading your agents…</p>}
    {agent&&!eligible.length&&<p className="inline-notice">No active {agent.format} League is available.</p>}
    {deckProblem&&<p role="alert" className="inline-error">{deckProblem}</p>}
    {agent&&leagueId&&localDrafts.length>0&&<p className="inline-notice">{localDrafts.length} local deck(s) need publishing. {siteAccount?.valid?<button type="button" disabled={locked||!!deckProblem} onClick={()=>void add(agent.id,leagueId,true)}>Publish decks & join</button>:<button type="button" onClick={openSettings}>Sign in to publish decks</button>}</p>}
  </section>;
}
