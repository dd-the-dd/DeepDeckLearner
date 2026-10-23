import {useEffect,useRef,useState} from 'react';
import AsyncActionButton from './AsyncActionButton';
import LeagueCapacityHelp from './LeagueCapacityHelp';
import {controlLeagueQueue,loadLeagueQueue,saveLeagueQueue,removeLeagueQueueEntry,type LeagueQueue,type LeagueQueueEntry,type LeagueTurnSummary} from './api';

export function leaseLabel(entry:LeagueQueueEntry):string {
  return entry.lease.mode==='matches'?`${entry.completedMatches} / ${entry.lease.matches} completed matches`
    :entry.lease.mode==='duration'?`${Math.floor(entry.elapsedSeconds/60)} / ${entry.lease.minutes} deployment minutes`
    :`${entry.completedMatches} completed matches · no end limit`;
}

export function rotationOverview(queue:LeagueQueue):string {
  const turns=queue.entries.filter(e=>e.turn).length;
  if(queue.controllerError)return 'Needs attention';
  if(queue.status==='pausing')return 'Pausing after current turns';
  if(queue.status==='paused')return turns?'Paused · previous turns awaiting confirmation':'Paused';
  if(turns)return 'Running';
  if(!queue.capacity || queue.capacity.status!=='ready')return 'Waiting for server capacity';
  if(queue.capacity.available===0)return 'Waiting for an account place';
  return 'Running';
}

function turnOutcome(turn:LeagueTurnSummary):string {
  if(!turn.result)return 'Result not confirmed';
  if(turn.result.outcome==='complete')return '1 completed match';
  if(turn.result.outcome==='failed')return 'Match failed · no completed match';
  if(turn.result.outcome==='cancelled')return 'Attempt cancelled · no completed match';
  return 'No completed match';
}

function RecentTurns({turns,onReplays}:{turns:LeagueTurnSummary[];onReplays?:()=>void}) {
  const [showAll,setShowAll]=useState(false);
  const retained=turns.slice(-30);
  const recent=retained.slice(showAll?0:-6).reverse();
  if(!recent.length)return <p className="muted">Completed rotation turns will appear here. A worker launch alone is not a completed match.</p>;
  return <section className="rotation-history" aria-label="Recent rotation turns"><header className="feature-heading"><div><h3>Recent rotation turns</h3><p>Last {recent.length} of {retained.length} retained turns. Match outcomes and replay availability are tracked separately.</p></div>{onReplays && <button type="button" onClick={onReplays}>Open replay library</button>}</header>
    <ol>{recent.map(turn=><li key={turn.dispatchId} className={turn.error?'turn-needs-review':''}><div><strong>{turn.name}</strong><span>{turnOutcome(turn)}</span>{turn.error && <p className="inline-error">Reported issue: {turn.error}</p>}</div><div className="rotation-turn-facts">
      {turn.result ? <span>{Math.floor(turn.result.elapsedSeconds/60)}m {Math.floor(turn.result.elapsedSeconds%60)}s deployed</span>:<span>Duration not confirmed</span>}
      {turn.finishedAt && !Number.isNaN(Date.parse(turn.finishedAt)) && <time dateTime={turn.finishedAt} title="When the controller recorded this turn, not the original match-end time">Recorded {new Date(turn.finishedAt).toLocaleString()}</time>}
    </div></li>)}</ol>
    {retained.length>6 && <button type="button" aria-expanded={showAll} onClick={()=>setShowAll(value=>!value)}>{showAll?'Show latest six turns':`Show all ${retained.length} retained turns`}</button>}
    <p className="muted">A completed match is not necessarily a win. Agent and deck results are available in Insights; replay collection keeps its own disk quota and favorites.</p>
  </section>;
}

export default function LeagueRotation({refreshKey=0,expanded=true,onExpandedChange,onReplays,siteUrl}:{refreshKey?:number;expanded?:boolean;onExpandedChange?:(value:boolean)=>void;onReplays?:()=>void;siteUrl?:string}) {
  const [queue,setQueue]=useState<LeagueQueue|null>(null);
  const [loading,setLoading]=useState(true);
  const [readError,setReadError]=useState('');
  const [actionError,setActionError]=useState('');
  const [busy,setBusy]=useState(false);
  const [reload,setReload]=useState(0);
  const pending=useRef(false);
  const epoch=useRef(0);
  const mounted=useRef(false);
  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;};},[]);
  useEffect(()=>{
    let active=true;let timer:ReturnType<typeof setTimeout>;
    async function poll() {
      const version=epoch.current;
      try {
        const value=await loadLeagueQueue();
        if(active && version===epoch.current && !pending.current){setQueue(value);setReadError('');}
      } catch(reason){if(active && version===epoch.current)setReadError(reason instanceof Error?reason.message:'Rotation could not be loaded.');}
      finally{if(active){setLoading(false);timer=setTimeout(()=>void poll(),5000);}}
    }
    void poll();return()=>{active=false;clearTimeout(timer);};
  },[refreshKey,reload]);
  async function mutate(operation:()=>Promise<LeagueQueue>) {
    if(pending.current)return;
    pending.current=true;epoch.current+=1;setBusy(true);setActionError('');
    try {const value=await operation();if(mounted.current)setQueue(value);}
    catch(reason){if(mounted.current)setActionError(`${reason instanceof Error?reason.message:'The request failed.'} The change is not confirmed. Reload the rotation before retrying.`);}
    finally{epoch.current+=1;pending.current=false;if(mounted.current)setBusy(false);}
  }
  const editable=!!queue && queue.status==='paused' && !queue.entries.some(entry=>entry.turn);
  const dispatched=queue?.entries.filter(entry=>entry.turn).length??0;
  const waiting=queue?.entries.filter(entry=>entry.state==='queued' && !entry.turn).length??0;
  const blocked=queue?.entries.filter(entry=>entry.state==='blocked').length??0;
  const exhausted=queue?.entries.filter(entry=>entry.state==='done').length??0;
  return <section className="feature-card league-rotation" aria-label="Agent rotation">
    <header className="feature-heading"><div><span className="section-kicker">Your waiting bench</span><h2>{expanded?'Let your agents take turns.':'Agent rotation'}</h2>{expanded && <p>Queue as many agents as you like. Only the places granted by the server can play at once.</p>}</div><div className="rotation-heading-actions">{queue && <span className={`competition-status ${queue.status}`}>{queue.status}</span>}{onExpandedChange && <button type="button" aria-expanded={expanded} onClick={()=>onExpandedChange(!expanded)}>{expanded?'Hide rotation details':'Show rotation details'}</button>}</div></header>
    <div role="status" aria-label="Rotation overview" className="rotation-overview"><strong>{queue?rotationOverview(queue):readError?'Rotation unavailable':'Checking your rotation…'}</strong>{queue && <span>{dispatched} turn{dispatched===1?'':'s'} dispatched · {waiting} waiting · {blocked} need{blocked===1?'s':''} review{exhausted?` · ${exhausted} budget${exhausted===1?'':'s'} finished`:''}</span>}{readError && queue && <small>Last loaded state · refresh unavailable</small>}</div>
    {!expanded && <>{readError && <p>{readError} Your saved rotation is not assumed empty. Open details to retry.</p>}{queue?.controllerError && <p className="inline-error">{queue.controllerError}</p>}{!!blocked && <p>Open rotation details to review agents that stopped without a confirmed result.</p>}</>}
    {expanded && <>
    {loading && !queue && <p role="status">Loading your rotation…</p>}
    {readError && <p role="alert" className="inline-error">{readError} {queue?'Previously loaded rotation remains visible.':'Rotation is unavailable, not empty.'}</p>}
    {actionError && <p role="alert" className="inline-error">{actionError}</p>}
    {queue?.controllerError && <p role="alert" className="inline-error">{queue.controllerError}</p>}
    <div className="rotation-controls"><button type="button" disabled={busy} onClick={()=>setReload(value=>value+1)}>Reload rotation</button>
      {queue && <><label>Rotation order<select disabled={busy || !editable} value={queue.strategy} aria-describedby="rotation-order-help" onChange={event=>void mutate(()=>saveLeagueQueue({revision:queue.revision,strategy:event.target.value as LeagueQueue['strategy'],entries:queue.entries}))}>
        <option value="round_robin">Round robin</option><option value="random">Random</option></select></label>
        {queue.status==='running' || queue.status==='pausing' || queue.entries.some(e=>e.turn)
          ? <AsyncActionButton loading={busy} loadingLabel="Pausing…" disabled={queue.status==='pausing'} onClick={()=>void mutate(()=>controlLeagueQueue('pause',queue.revision))}>Pause rotation</AsyncActionButton>
          : <AsyncActionButton className="primary-action" loading={busy} loadingLabel="Starting…" disabled={!queue.entries.some(e=>e.state==='queued') || !!readError} onClick={()=>void mutate(()=>controlLeagueQueue('resume',queue.revision))}>Start rotation</AsyncActionButton>}
      </>}
    </div>
    <p id="rotation-order-help" className="muted">{queue?.strategy==='random'?'Random chooses an available agent afresh each turn; repeats are possible.':'Round robin gives each available agent one turn in list order, then starts again.'} A turn is one matchmaking attempt. An assigned match finishes before the place rotates.</p>
    {queue?.capacity && <p role="status" className="rotation-capacity">{queue.capacity.status==='ready'?`${queue.capacity.used} / ${queue.capacity.limit} account places used · ${queue.capacity.available} reported available`:`Automatic dispatch waiting: ${queue.capacity.reason}`} <small>Last checked {new Date(queue.capacity.checkedAt).toLocaleTimeString()}.</small>{queue.capacity.status==='ready' && <LeagueCapacityHelp siteUrl={siteUrl}/>}</p>}
    {queue?.message && <p role="status">{queue.message}</p>}
    {queue && !queue.entries.length && <p>Add an agent using your choices above. Adding to the bench does not start a League worker.</p>}
    {!!queue?.entries.length && <ol className="rotation-entries">{queue.entries.map((entry,index)=><li key={entry.id}>
      <span className="rotation-seat">{index+1}</span><div><strong>{entry.name}</strong><p>{entry.deckVersionIds.length} eligible deck{entry.deckVersionIds.length===1?'':'s'} · {entry.format} · {leaseLabel(entry)}</p><small>{entry.state==='playing'?'Turn dispatched · connection follows in the live roster':entry.state==='done'?'Deployment budget finished':entry.state==='blocked'?'Needs your review':'Waiting for a turn'}</small>{entry.error && <p className="inline-error">{entry.error}</p>}</div>
      {editable && entry.state==='blocked' && <AsyncActionButton loading={busy} loadingLabel="Saving review…" onClick={()=>void mutate(()=>controlLeagueQueue('retry',queue.revision,entry.id))}>Reviewed — retry {entry.name}</AsyncActionButton>}
      {!entry.turn && <button type="button" disabled={busy || !!readError || !!queue.controllerError} aria-label={`Remove ${entry.name} from rotation`} onClick={()=>void mutate(()=>removeLeagueQueueEntry(entry.id,queue.revision))}>Remove</button>}
      {entry.turn && <small className="rotation-turn-protected">Active turn protected · pause and let it finish before removal.</small>}
    </li>)}</ol>}
    {queue && <RecentTurns turns={queue.history} onReplays={onReplays}/>}
    <details><summary>How rotation limits and storage work</summary><p>Keep this app open. Each agent keeps its own completed-match or deployment-time budget across turns. Local waiting time does not consume deployment minutes; connecting, matchmaking and finishing a game do. Failed matches do not consume the completed-match limit.</p><p>Add agents or remove waiting entries while your rotation keeps playing. Active turns cannot be removed. Pause and let them finish before changing decks, budgets or rotation order. After an app restart, resume explicitly. Missing results require review; they are never counted as wins or losses.</p><p>The server supplies account capacity, including use on other computers. If it does not report capacity, automatic rotation stays inactive. The last 30 turn summaries and 100 finished worker diagnostics are retained. Replay quotas and starred games are managed separately in Settings.</p></details>
    </>}
  </section>;
}
