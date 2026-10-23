import type {ExperienceSettings,Job} from './api';
import ReplayInbox from './ReplayInbox';

export function replayInterval(seconds:number) {return seconds%60===0?`${seconds/60} minute${seconds===60?'':'s'}`:`${seconds} seconds`;}

export default function ReplayCollectionStatus({settings,jobs,onSettings,onRecovered}:{settings:ExperienceSettings|null;jobs:Job[];onSettings?:()=>void;onRecovered?:()=>void}) {
  const active=jobs.filter(job=>job.kind==='matchmaking.agent' && ['running','queued'].includes(job.status));
  const enabled=settings?.autoCollectLeagueReplays;
  const reports=new Map<string,Job>();
  for(const job of jobs.filter(job=>['matchmaking.agent','replays.collect'].includes(job.kind) && job.details?.replaySync)) {
    const key=job.model_id??job.id;const previous=reports.get(key);
    if(!previous || job.details!.replaySync!.checkedAtUnixMs>previous.details!.replaySync!.checkedAtUnixMs)reports.set(key,job);
  }
  const idle=[...reports.values()].filter(job=>job.details!.replaySync!.pending>0 && !active.some(running=>(running.model_id??running.id)===(job.model_id??job.id)));
  return <section className="replay-collection-status" aria-label="League replay collection">
    <header><div><strong>{!settings?'Checking replay collection…':!enabled?'Automatic collection is paused':active.length?'Automatic collection is enabled':'Ready to collect when an agent runs'}</strong><p>{!settings?'The saved collection settings have not loaded yet.':!enabled?'Existing recordings are kept. Local games and agents continue normally.':active.length?`Checks every ${replayInterval(settings.replayPollSeconds)} while these agents run on this computer.`:'Send an agent to the League from this computer to collect its finalized games.'}</p></div>{onSettings && <button type="button" onClick={onSettings}>Replay settings</button>}</header>
    {(active.length>0||idle.length>0) && <ul>{[...active,...idle].slice(0,12).map(job=>{const sync=job.details?.replaySync;const running=active.some(item=>item.id===job.id);return <li key={job.id}><strong>{job.label}</strong>{sync?<><span>{sync.collected} collected in total · {sync.pending} pending in the last report</span><small>Last reported check: {Number.isFinite(sync.checkedAtUnixMs)&&sync.checkedAtUnixMs>0?new Date(sync.checkedAtUnixMs).toLocaleString():'Not recorded'}{sync.enabled?'':' · agent reported collection paused'}</small>{!running && <p>This agent is not running on this computer. Use the pending inbox below to retry its downloads without deploying it again.</p>}{sync.lastError && <p className="inline-error">{sync.lastError} Pending games can be retried from the inbox when collection is enabled.</p>}</>:<span>No collection report received yet. A running agent is not proof that a replay was saved.</span>}</li>;})}</ul>}
    {active.length+idle.length>12 && <p>Showing 12 of {active.length+idle.length} agent reports. All active agents remain listed in the League roster.</p>}
    {[...active,...idle].some(job=>job.details?.replaySync) && <p>Collected totals are historical; the storage quota may already have retired older non-favorites.</p>}
    {settings?.replayStorageError && <p className="inline-error">Storage needs attention: {settings.replayStorageError}</p>}
    <ReplayInbox settings={settings} onRecovered={onRecovered}/>
  </section>;
}
