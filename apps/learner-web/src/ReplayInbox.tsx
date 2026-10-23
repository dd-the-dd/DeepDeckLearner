import {useEffect,useRef,useState} from 'react';
import {loadReplayInbox,loadJobs,loadJob,recoverReplayInbox,type ReplayInbox as Inbox,type Job,type ExperienceSettings} from './api';
import AsyncActionButton from './AsyncActionButton';

const active=(job:Job)=>['queued','running'].includes(job.status);

export default function ReplayInbox({settings,onRecovered}:{settings:ExperienceSettings|null;onRecovered?:()=>void}) {
  const [inbox,setInbox]=useState<Inbox|null>(null);
  const [recoveries,setRecoveries]=useState<Job[]>([]);
  const [readError,setReadError]=useState('');
  const [actionError,setActionError]=useState('');
  const [notice,setNotice]=useState('');
  const [submitting,setSubmitting]=useState('');
  const [reload,setReload]=useState(0);
  const accepted=useRef<Job|null>(null);
  const completed=useRef(new Set<string>());
  const observedActive=useRef(new Set<string>());
  const epoch=useRef(0);
  const pending=useRef(false);
  const mounted=useRef(false);
  const changed=useRef(onRecovered);changed.current=onRecovered;
  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;};},[]);
  useEffect(()=>{
    let live=true;let timer:ReturnType<typeof setTimeout>;
    async function poll(){
      const version=epoch.current;
      try{
        const known=accepted.current;
        const [value,jobs,exact]=await Promise.all([loadReplayInbox(),loadJobs(),known&&active(known)?loadJob(known.id):Promise.resolve(null)]);
        if(live && version===epoch.current && !pending.current){
          const byId=new Map(jobs.filter(j=>j.kind==='replays.collect').map(j=>[j.id,j]));
          if(exact)byId.set(exact.id,exact);
          if(known && !byId.has(known.id))byId.set(known.id,known);
          const receipts=[...byId.values()];
          const finished=receipts.some(current=>!active(current) && (observedActive.current.has(current.id)||current.id===known?.id) && !completed.current.has(current.id));
          // The first inbox read can race the final file commit. Re-read after
          // observing a terminal job before enabling another recovery click.
          const confirmed=finished?await loadReplayInbox():value;
          if(!live || version!==epoch.current || pending.current)return;
          setInbox(confirmed);setRecoveries(receipts);setReadError('');
          if(known)accepted.current=byId.get(known.id)!;
          for(const current of receipts){
            if(active(current))observedActive.current.add(current.id);
            if(!active(current) && (observedActive.current.has(current.id)||current.id===known?.id) && !completed.current.has(current.id)){
              completed.current.add(current.id);
              setNotice(current.status==='completed'?'Replay check finished. Read the pending inbox below for what remains; unavailable games were not marked downloaded.':'Replay recovery stopped without a successful check. Pending games remain in the inbox.');
              changed.current?.();
            }
          }
        }
      }catch(reason){if(live && version===epoch.current)setReadError(reason instanceof Error?reason.message:'Pending downloads could not be read.');}
      finally{if(live)timer=setTimeout(()=>void poll(),5000);}
    }
    void poll();return()=>{live=false;clearTimeout(timer);};
  },[reload]);
  async function recover(modelId:string){
    if(pending.current)return;
    pending.current=true;epoch.current+=1;setSubmitting(modelId);setActionError('');setNotice('');
    try{
      const job=await recoverReplayInbox(modelId);accepted.current=job;
      if(mounted.current){setRecoveries(current=>[job,...current.filter(j=>j.id!==job.id)]);setNotice('Replay recovery accepted on this computer. No agent was sent to the League.');}
    }catch(reason){if(mounted.current)setActionError(`${reason instanceof Error?reason.message:'Recovery was not confirmed.'} Reload the inbox before retrying; do not redeploy your agent to recover a recording.`);}
    finally{epoch.current+=1;pending.current=false;if(mounted.current){setSubmitting('');setReload(value=>value+1);}}
  }
  const items=inbox?.items.filter(item=>item.pending>0)??[];
  const running=recoveries.filter(active);
  return <section className="replay-inbox" aria-label="Pending replay inbox"><header><div><strong>Pending replay downloads</strong><p>Recover finalized games without sending an agent back to play. Each check attempts up to 10 eligible games and uses your existing replay quota.</p></div><button type="button" disabled={!!submitting} onClick={()=>setReload(value=>value+1)}>Reload pending downloads</button></header>
    <p role="status" aria-label="Replay inbox status">{readError?`Inbox unavailable. ${inbox?'Last loaded counts remain visible.':'Pending downloads are not assumed empty.'} ${readError}`:!inbox?'Checking the saved replay inbox…':!items.length?'No pending downloads are recorded.':`${items.reduce((sum,item)=>sum+item.pending,0)} replay downloads pending in the saved inbox.`}</p>
    {notice && <p role="status">{notice}</p>}{actionError && <p role="alert" className="inline-error">{actionError}</p>}
    {!!running.length && <p role="status">{running.length} replay recovery check{running.length===1?'':'s'} running on this computer. This does not use a League place.</p>}
    {!!items.length && <ul>{items.slice(0,12).map(item=>{const busy=running.some(j=>j.model_id===item.modelId);return <li key={item.modelId}><div><strong>{item.name}</strong><span>{item.pending} pending · {item.currentServerPending} on this server · {item.collected} historically collected</span>{item.otherServerPending>0 && <p>{item.otherServerPending} belong to another League server. Return to the original server to recover them.</p>}{item.unknownServerPending>0 && <p>{item.unknownServerPending} have no confirmed server origin and will not be requested automatically.</p>}{!item.available && <p>The local agent library is unavailable. Restore it before recovering these games.</p>}{item.lastError && <p className="inline-error">Last download issue: {item.lastError}</p>}{item.lastAttemptAtUnixMs && <small>Last attempt started {new Date(item.lastAttemptAtUnixMs).toLocaleString()}</small>}</div><AsyncActionButton loading={submitting===item.modelId||busy} loadingLabel="Checking replays…" disabled={!!submitting||busy||!!readError||!settings?.autoCollectLeagueReplays||!item.available||item.currentServerPending<1} onClick={()=>void recover(item.modelId)}>Recover pending replays for {item.name}</AsyncActionButton></li>;})}</ul>}
    {items.length>12 && <p>Showing the first 12 agents with pending downloads. Recovered entries leave this list; no inbox entries are discarded.</p>}
    {settings?.autoCollectLeagueReplays===false && !!items.length && <p>Collection is paused. Resume it in Replay settings before retrying downloads.</p>}
  </section>;
}
