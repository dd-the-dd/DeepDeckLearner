import { lazy, Suspense, useEffect, useRef, useState } from 'react';

import AsyncActionButton from './AsyncActionButton';
import { DeckArtwork, ManaColors } from './DeckVisuals';
import { ReplayPlaySettings } from './PlaySettings';
import ReplayMatchFacts from './ReplayMatchFacts';
import ReplayCollectionStatus from './ReplayCollectionStatus';
import {
  acquireSavedReplay, loadSavedReplays, loadLocalTableReplays, releaseSavedReplayLease,
  renewSavedReplayLease, setReplayFavorite,
  type DeckSummary, type ExperienceSettings, type ProgrammaticAgent,
  type SavedReplay, type SavedReplaySummary,
  type Job, type ActiveGame,
} from './api';

const LocalPixiReplay = lazy(() => import('./LocalPixiReplay'));
type ReplayItem = SavedReplaySummary & { modelId: string; modelName: string };
const keyOf = (item: ReplayItem) => `${item.modelId}:${item.id}`;
const sharedReplay = (item: ReplayItem) => item.source === 'league' || item.id.startsWith('batch-');
const matchTitle = (item: ReplayItem) => item.deckNames?.filter(Boolean).join(' vs ') || 'Magic match';
const dateLabel = (value: number | null) => value ? new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }).format(value) : 'Unknown date';
const bytesLabel = (value: number) => `${(value / 1024 ** 3).toFixed(2)} GB`;
const complete = (item: ReplayItem) => ['complete', 'completed', 'gameOver'].includes(item.gameStatus ?? '');

export default function ReplayHub({ agents, localDecks = [], settings, jobs = [], activeGames = [], onWatch, initialReplayId = '',openSettings,agentLibraryReady=true,onRefreshAgents }: { agents: ProgrammaticAgent[]; localDecks?: DeckSummary[]; settings: ExperienceSettings | null; jobs?: Job[]; activeGames?: ActiveGame[]; onWatch?: (job: Job) => void; initialReplayId?: string;openSettings?:()=>void;agentLibraryReady?:boolean;onRefreshAgents?:()=>Promise<void> }) {
  const [items, setItems] = useState<ReplayItem[]>([]);
  // Selection is explicit: new recordings must not replace an open match.
  const [selected, setSelected] = useState<ReplayItem | null>(null);
  const [replay, setReplay] = useState<SavedReplay | null>(null);
  const [frame, setFrame] = useState(0);
  const [source, setSource] = useState<'all' | 'league' | 'local'>('league');
  const [favoritesOnly, setFavoritesOnly] = useState(false);
  const [loading, setLoading] = useState(true);
  const [hasSnapshot,setHasSnapshot]=useState(false);
  const [libraryError,setLibraryError]=useState('');
  const [refreshingAgents,setRefreshingAgents]=useState(false);
  const [error, setError] = useState('');
  const [openError, setOpenError] = useState('');
  const [pinning, setPinning] = useState('');
  const [refreshKey, setRefreshKey] = useState(0);
  const dialog = useRef<HTMLDialogElement>(null);
  const openedInitial = useRef('');
  const libraries=useRef(new Map<string,ReplayItem[]>());
  const libraryEpoch=useRef(0);
  const agentsKey = JSON.stringify(agents.map(({ id, name }) => ({ id, name })));
  const decks = new Map([...agents.flatMap(agent=>agent.decks), ...localDecks].map(deck=>[deck.id,deck] as const));

  useEffect(() => {
    if(!agentLibraryReady){setLoading(true);return;}
    let active = true;
    let timer:ReturnType<typeof setTimeout>;
    let controller:AbortController|null=null;
    let deadline:ReturnType<typeof setTimeout>;
    async function refreshLibrary() {
      if(!active)return;
      setLoading(true);
      const version=libraryEpoch.current;
      controller=new AbortController();
      const signal=controller.signal;
      deadline=setTimeout(()=>controller?.abort(),30_000);
      const pilots = [...JSON.parse(agentsKey) as Array<{ id: string; name: string }>, {id:'local-tables',name:'Local players'}];
      const groups = await Promise.allSettled(pilots.map(async (agent) => (await (agent.id==='local-tables' ? loadLocalTableReplays(signal) : loadSavedReplays(agent.id,signal))).map((item) => ({ ...item, modelId: agent.id, modelName: agent.name }))));
      clearTimeout(deadline);
      if (!active) return;
      if(version!==libraryEpoch.current){setLoading(false);timer=setTimeout(()=>void refreshLibrary(),30_000);return;}
      const ids=new Set(pilots.map(p=>p.id));
      for(const id of libraries.current.keys())if(!ids.has(id))libraries.current.delete(id);
      groups.forEach((group,index)=>{if(group.status==='fulfilled')libraries.current.set(pilots[index].id,group.value);});
      const unique = new Map<string, ReplayItem>();
      for (const group of libraries.current.values()) {
        for (const item of group) {
          const key = sharedReplay(item) ? item.id : keyOf(item);
          const existing = unique.get(key);
          if (!existing) unique.set(key, {...item});
          else { existing.saved ||= item.saved; if (!existing.modelName.split(' · ').includes(item.modelName)) existing.modelName += ` · ${item.modelName}`; }
        }
      }
      setItems([...unique.values()].sort((a, b) => (b.createdAtUnixMs ?? 0) - (a.createdAtUnixMs ?? 0)));
      setLoading(false);
      const failed=groups.filter(group=>group.status==='rejected').length;
      setHasSnapshot(current=>current || failed===0 || [...libraries.current.values()].some(group=>group.length>0));
      setLibraryError(failed?`${failed} game librar${failed===1?'y is':'ies are'} unavailable. Last loaded recordings are kept where available; counts may be incomplete. Your open replay is unchanged.`:'');
      timer=setTimeout(()=>void refreshLibrary(),30_000);
    }
    void refreshLibrary();
    return () => { active = false;clearTimeout(timer);clearTimeout(deadline);controller?.abort(); };
  }, [agentsKey, refreshKey,agentLibraryReady]);

  useEffect(() => {
    if (!agentLibraryReady || loading || !hasSnapshot || !initialReplayId || openedInitial.current === initialReplayId) return;
    const item = items.find(candidate => candidate.id === initialReplayId);
    if(!item && libraryError)return;
    openedInitial.current = initialReplayId;
    setSource('local');
    if (item) setSelected(item);
    else setError('This competition recording is no longer in local storage. It may have been removed by your replay quota. Competition results remain available.');
  }, [loading,hasSnapshot,libraryError,items,initialReplayId,agentLibraryReady]);

  const selectedId = selected?.id;
  const selectedModelId = selected?.modelId;
  useEffect(() => {
    let active = true;
    let leaseId = '';
    let timer: number | undefined;
    setReplay(null); setFrame(0); setOpenError('');
    if (!selectedId || !selectedModelId) return;
    dialog.current?.showModal();
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    void acquireSavedReplay(selectedModelId, selectedId).then((result) => {
      leaseId = result.leaseId;
      if (!active) { if (leaseId) void releaseSavedReplayLease(selectedModelId, selectedId, leaseId).catch(() => undefined); return; }
      setReplay(result.replay);
      if (leaseId) timer = window.setInterval(() => {
        void renewSavedReplayLease(selectedModelId, selectedId, leaseId).catch(() => {
          if (active) setOpenError('Replay protection could not be renewed. The loaded recording remains available; reopen it to reconnect.');
        });
      }, 30_000);
    }).catch((reason: unknown) => { if (active) setOpenError(reason instanceof Error ? reason.message : 'This replay could not be opened.'); });
    return () => {
      active = false;
      document.body.style.overflow = previousOverflow;
      if (timer) window.clearInterval(timer);
      if (leaseId) void releaseSavedReplayLease(selectedModelId, selectedId, leaseId).catch(() => undefined);
    };
  }, [selectedId, selectedModelId]);

  async function favorite(item: ReplayItem) {
    if (pinning) return;
    libraryEpoch.current+=1;
    setPinning(keyOf(item));
    try {
      const result = await setReplayFavorite(item.modelId, item.id, !item.saved);
      const matches = (candidate: ReplayItem) => sharedReplay(item) ? candidate.id === item.id : keyOf(candidate) === keyOf(item);
      for(const [id,group] of libraries.current)libraries.current.set(id,group.map(candidate=>matches(candidate)?{...candidate,saved:result.saved}:candidate));
      setItems((current) => current.map((candidate) => matches(candidate) ? { ...candidate, saved: result.saved } : candidate));
      setSelected((current) => current && matches(current) ? { ...current, saved: result.saved } : current);
    } catch (reason) { const message = reason instanceof Error ? reason.message : 'The favorite could not be saved.'; setError(message); if (selected) setOpenError(message); }
    finally { libraryEpoch.current+=1;setPinning(''); }
  }

  function star(item: ReplayItem) {
    return <AsyncActionButton className={`replay-star${item.saved ? ' is-favorite' : ''}`} aria-label={item.saved ? 'Remove from favorites' : 'Add to favorites'} aria-pressed={item.saved} title={item.saved ? 'Favorite · protected from automatic cleanup' : 'Keep this match as a favorite'} loading={pinning === keyOf(item)} disabled={Boolean(pinning)} loadingLabel="Saving favorite…" onClick={() => void favorite(item)}>{item.saved ? '★' : '☆'}</AsyncActionButton>;
  }

  async function retryLibrary(){
    if(!agentLibraryReady && onRefreshAgents){
      setRefreshingAgents(true);
      try{await onRefreshAgents();}
      catch{setLibraryError('The agent library is unavailable. Retry loading it; no recordings were assumed removed.');}
      finally{setRefreshingAgents(false);}
    }
    setRefreshKey(key=>key+1);
  }

  const visible = items.filter((item) => (!favoritesOnly || item.saved) && (source === 'all' || (source === 'league' ? item.source === 'league' : item.source !== 'league')));
  const localLive = jobs.filter((job) => ['playtest.batch', 'playtest.table', 'playtest.agent'].includes(job.kind) && ['running', 'queued'].includes(job.status));
  const leagueLive = activeGames.filter((game) => game.source === 'league' && game.watchUrl);
  return <div className="replay-hub replay-library">
    <ReplayCollectionStatus settings={settings} jobs={jobs} onSettings={openSettings} onRecovered={()=>setRefreshKey(value=>value+1)}/>
    {(localLive.length > 0 || leagueLive.length > 0) && <section className="feature-card live-now-card" aria-label="Live matches"><header><span className="live-dot" /><div><span className="section-kicker">Live now</span><h2>Watch your agents play</h2><p>Open a read-only table. Leaving the viewer keeps the match running.</p></div></header>{localLive.map((job) => <div className="live-now-row" key={job.id}><div><strong>{job.details?.deckNames?.join(' vs ') || job.label}</strong><small>Local Engine · {job.details?.completed ?? 0}/{job.details?.total ?? 1} completed{job.details?.participantNames?.length ? ` · ${job.details.participantNames.join(' vs ')}` : ''}</small></div><button className="primary-action" type="button" disabled={!onWatch} onClick={() => onWatch?.(job)}>Watch live</button></div>)}{leagueLive.map((game) => <div className="live-now-row" key={game.id}><div><strong>{game.decks.filter(Boolean).join(' vs ') || game.modelName}</strong><small>League · opens the server's live viewer</small></div><a className="primary-action" href={game.watchUrl!} target="_blank" rel="noreferrer">Watch on League ↗</a></div>)}</section>}
    <section className="replay-toolbar">
      <div className="segmented compact">{(['all', 'league', 'local'] as const).map((value) => <button key={value} type="button" className={source === value ? 'active' : ''} onClick={() => setSource(value)}>{value === 'all' ? 'All games' : value === 'league' ? 'League' : 'Local'}</button>)}</div>
      <label className="replay-favorite-filter"><input type="checkbox" checked={favoritesOnly} onChange={(event) => setFavoritesOnly(event.target.checked)} />★ Favorites only</label>
      <button type="button" disabled={refreshingAgents} onClick={() => void retryLibrary()}>{refreshingAgents?'Reloading agent libraries…':!agentLibraryReady?'Reload agent libraries':loading?'Restart library check':'Refresh library'}</button>
      <div className="storage-meter">{settings?<><span><strong>{bytesLabel(settings.replayStorageUsedBytes)}</strong> / {bytesLabel(settings.replayStorageLimitBytes)}</span><i><b style={{ width: `${Math.min(100,settings.replayStorageUsedBytes/Math.max(1,settings.replayStorageLimitBytes)*100)}%` }} /></i><small>Oldest non-favorites are removed first</small></>:<span>Checking replay storage…</span>}</div>
    </section>
    {settings?.replayStorageError && <p className="inline-error" role="alert">{settings.replayStorageError}</p>}
    {error && <p className="inline-error" role="alert">{error}</p>}
    {libraryError && <div className="inline-error replay-library-error" role="alert"><p>{libraryError}</p><button type="button" disabled={refreshingAgents} onClick={()=>void retryLibrary()}>Retry replay libraries</button></div>}
    <p className="replay-library-summary" role="status" aria-label="Replay library status">{!hasSnapshot?(loading?'Loading your recordings…':'Recording count unavailable.'):libraryError?`Last loaded view: ${visible.length} recording${visible.length===1?'':'s'} · refresh incomplete.`:`${visible.length} recording${visible.length===1?'':'s'} · Star a match to protect it from automatic cleanup.`}{hasSnapshot&&loading?' Checking for updates…':''}</p>
    {loading && !items.length ? <p className="list-loading" role="status">Loading replays…</p> : <section className="replay-match-grid" aria-label="Game library">{visible.map((item) => <article className="replay-match-card" key={keyOf(item)} data-status={item.gameStatus}>
      <header><span className="replay-origin">{item.source === 'league' ? 'League server' : 'Local Engine'}</span><span>{item.gameMode || 'Magic'}{item.roundNumber ? item.matchSummary?.scope === 'game' ? ` · Game ${item.roundNumber}` : ` · ${item.roundNumber} round${item.roundNumber > 1 ? 's' : ''}` : ''}</span>{star(item)}</header>
      <div className="replay-competitors">{(item.decks.length ? item.decks : ['']).map((id, index) => {
        const deck: DeckSummary = decks.get(id) ?? { id, name: item.deckNames?.[index] || `Deck ${index + 1}`, version: 1, colors: [], playableCardCount: 0, unavailable: !id };
        return <div className="replay-competitor" key={`${id}:${index}`}><DeckArtwork deck={deck} /><div><small>Seat {index + 1} · {item.participants?.[index]?.kind === 'human' ? 'Player' : 'Pilot'}</small><strong>{item.deckNames?.[index] || deck.name}</strong><span>{item.participants?.[index]?.name || item.modelName}</span>{decks.has(id) && <ManaColors colors={deck.colors} />}</div></div>;
      })}</div>
      <ReplayMatchFacts summary={item.matchSummary} names={item.participants?.map((participant) => participant.name)} />
      <footer><div><strong className={complete(item) ? 'recording-complete' : 'recording-partial'}>{complete(item) ? 'Completed match' : item.gameStatus === 'interrupted' ? 'Interrupted recording' : item.gameStatus === 'timeout' ? 'Time limit reached' : item.gameStatus || 'Completion not recorded'}</strong><small>{dateLabel(item.createdAtUnixMs)}{item.durationSeconds != null ? ` · ${Math.round(item.durationSeconds)}s recorded` : ''} · {item.frameCount} steps</small></div><button type="button" className="primary-action replay-watch" onClick={() => { setSelected(item); setOpenError(''); }}>Watch match <span aria-hidden="true">↗</span></button></footer>
    </article>)}</section>}
    {!loading && !visible.length && libraryError && <div className="dd-empty"><h3>Recordings could not be confirmed in this view</h3><p>Retry the unavailable libraries. A read failure does not mean your recordings were removed.</p></div>}
    {!loading && hasSnapshot && !libraryError && !visible.length && <div className="dd-empty"><h3>{favoritesOnly ? 'No favorite in this view' : 'No recording in this view'}</h3><p>{favoritesOnly ? 'Star a match to find it here and keep it safe.' : 'Choose All games or Local to see playtests. League recordings arrive after your agent plays online.'}</p></div>}
    {selected && <dialog className="replay-theater" ref={dialog} aria-labelledby="replay-theater-title" onCancel={(event) => { event.preventDefault(); setSelected(null); }}>
      <header className="replay-theater-header"><button type="button" className="secondary-action" onClick={() => setSelected(null)}>← Back to replays</button><div><small>{selected.source === 'league' ? 'League server' : 'Local Engine'} · {selected.gameMode || 'Magic'}</small><h2 id="replay-theater-title">{matchTitle(selected)}</h2></div>{star(selected)}<details><summary>Match details</summary><div><p>{selected.participants?.map((participant) => participant.name).join(' vs ') || selected.modelName}</p>{replay && <ReplayPlaySettings metadata={replay.metadata} />}<small>{dateLabel(selected.createdAtUnixMs)}</small></div></details></header>
      <ReplayMatchFacts summary={selected.matchSummary} names={selected.participants?.map((participant) => participant.name)} />
      {!complete(selected) && <p className="replay-partial-notice" role="status">This recording is {selected.gameStatus || 'not confirmed complete'}. You can watch every saved step, but the rest of the match was not recorded.</p>}
      {openError && <p className="inline-error" role="alert">{openError}</p>}
      <div className="replay-stage">{replay ? <Suspense fallback={<p className="stage-loading">Opening table…</p>}><LocalPixiReplay key={replay.id} replay={replay} deckVersionIds={selected.decks} frameIndex={frame} onFrameIndexChange={setFrame} onExit={() => setSelected(null)} /></Suspense> : <p className="stage-loading" role="status">{openError ? 'The recording could not be loaded.' : 'Opening recording…'}</p>}</div>
    </dialog>}
  </div>;
}
