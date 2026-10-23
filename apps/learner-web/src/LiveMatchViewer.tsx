import { Component, lazy, Suspense, useEffect, useRef, useState, type ReactNode } from 'react';
import { loadJob, loadSavedReplay, type DeckPresentation, type Job } from './api';
import { latestEngineView, type EngineView } from './LocalPixiTable';
import { replayFrameView } from './LocalPixiReplay';

const LocalPixiRenderer = lazy(() => import('./LocalPixiRenderer'));
const terminal = (job: Job) => ['completed', 'failed', 'stopped'].includes(job.status);

class LiveTableBoundary extends Component<{children: ReactNode}, {failed: boolean}> {
  state = {failed: false};
  static getDerivedStateFromError() { return {failed: true}; }
  render() {
    if (this.state.failed) return <div role="alert" className="pixi-replay-error">
      <p>The live table could not be displayed. Viewing does not stop or restart the match. You can return to the app or retry this display.</p>
      <button type="button" onClick={() => this.setState({failed: false})}>Retry live display</button>
    </div>;
    return this.props.children;
  }
}

export default function LiveMatchViewer({ initialJob, onClose }: { initialJob: Job; onClose: () => void }) {
  const [job, setJob] = useState(initialJob);
  const [view, setView] = useState<EngineView | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('Connecting to the match…');
  const [decks, setDecks] = useState<DeckPresentation[]>([]);
  const [perspective, setPerspective] = useState('global');
  const [soundEpoch, setSoundEpoch] = useState(0);
  const [paused, setPaused] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [buffered, setBuffered] = useState(0);
  const [bufferWarning, setBufferWarning] = useState('');
  const queue = useRef<EngineView[]>([]);
  const newest = useRef<EngineView | null>(null);
  const cursors = useRef(new Map<string, number>());
  const snapshotCursors = useRef(new Map<string, number>());
  const recoveryAttempts = useRef(new Map<string, number>());
  const recovered = useRef(new Set<string>());
  const sessions = useRef(new Map<number, string>());
  useEffect(() => {
    if (paused) return;
    const timer = setInterval(() => {
      const next = queue.current.shift();
      if (next) {setView(next); setBuffered(queue.current.length);}
    }, 700 / speed);
    return () => clearInterval(timer);
  }, [paused, speed]);
  const dialog = useRef<HTMLDialogElement>(null);
  const deckKey = JSON.stringify(job.details?.deckVersionIds ?? []);
  useEffect(() => {
    dialog.current?.showModal();
    const previous = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => { document.body.style.overflow = previous; };
  }, []);
  useEffect(() => {
    let active = true;
    const abort = new AbortController();
    const ids = [...new Set(JSON.parse(deckKey) as string[])];
    void Promise.all(ids.map(async (id) => {
      const response = await fetch(`/api/v1/catalog/decks/${encodeURIComponent(id)}/presentation`, { signal: abort.signal });
      if (!response.ok) return null;
      return response.json() as Promise<DeckPresentation>;
    })).then((results) => { if (active) setDecks(results.filter((item): item is DeckPresentation => Boolean(item))); }).catch(() => {});
    return () => { active = false; abort.abort(); };
  }, [deckKey]);
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const abort = new AbortController();
    function receive(incoming: EngineView, streamed = true) {
      const positions = streamed ? cursors.current : snapshotCursors.current;
      const prior = positions.get(incoming.sessionId);
      if (prior !== undefined && incoming.revision <= prior) return;
      const safe = {...incoming, schemaVersion: 'mtg-game-session/v1', decision: null};
      positions.set(safe.sessionId, safe.revision);
      if (!newest.current) setView(safe);
      else queue.current.push(safe);
      newest.current = safe;
      if (queue.current.length > 2000) {
        queue.current.splice(0, queue.current.length - 2000);
        setBufferWarning('The viewing buffer reached its memory limit. Older pending frames were skipped; the saved replay is the complete source.');
      }
      setBuffered(queue.current.length);
    }
    async function recoverCompleted(observed: Job) {
      const details = observed.details;
      const completed = details?.completed ?? 0;
      const models = [...(details?.agents ?? []), ...(details?.seats ?? []).map(s => s.agentId), observed.model_id ?? ''];
      const model = models.find(id => id.startsWith('programmatic-'));
      if (!details?.batchId || !model) return true;
      for (const [index, session] of sessions.current) {
        if (index > completed || recovered.current.has(session)) continue;
        try {
          const id = `batch-${details.batchId}-${String(index).padStart(4, '0')}`;
          const replay = await loadSavedReplay(model, id);
          if (!active) return false;
          replay.frames.forEach((frame, i) => {
            const sequence = frame.spectatorSequence;
            const restored = replayFrameView(replay, i);
            if (typeof sequence === 'number' && restored) receive({...restored, sessionId: session, revision: sequence});
          });
          recovered.current.add(session);
        } catch {
          const attempts = (recoveryAttempts.current.get(session) ?? 0) + 1;
          recoveryAttempts.current.set(session, attempts);
          if (active) setBufferWarning('The saved match tail is temporarily unavailable. Available buffered actions continue playing.');
          // Keep match order while retrying. Never append an older tail after a newer match.
          if (attempts < 3) return false;
          recovered.current.add(session);
        }
      }
      return true;
    }
    async function poll() {
      let finished = false;
      try {
        const observed = await loadJob(initialJob.id);
        if (!active) return;
        setJob(observed); finished = terminal(observed);
        const details = observed.details;
        const recoveredTail = await recoverCompleted(observed);
        if (!active) return;
        if (!recoveredTail) { finished = false; return; }
        if (finished) { setNotice(observed.status === 'failed' ? 'The match stopped with an error. Check its diagnostic recording in Replays.' : observed.status === 'stopped' ? 'The session was stopped. Any saved recording is in Replays.' : 'Session finished. The recording is available in Replays.'); setError(''); return; }
        if (!details?.sessionId || details.sessionStatus === 'finished') { setNotice('Waiting for the next match…'); setError(''); return; }
        const base = new URL(details.engineUrl || 'http://127.0.0.1:8787');
        if (!['http:', 'https:'].includes(base.protocol) || !['127.0.0.1', 'localhost', '[::1]'].includes(base.hostname) || base.username) throw new Error('Only your local Engine can be watched here.');
        sessions.current.set(details.currentMatchIndex ?? 1, details.sessionId);
        const sessionUrl = `${base.href.replace(/\/$/, '')}/game/sessions/${encodeURIComponent(details.sessionId).replace(/%3A/gi, ':')}`;
        for (let page = 0; page < 4; page++) {
          const cursor = cursors.current.get(details.sessionId);
          const feed = await fetch(`${sessionUrl}/spectator${cursor === undefined ? newest.current ? '/0' : '' : `/${cursor}`}`, {signal: abort.signal});
          if (!active) return;
          if (!feed.ok) break;
          const packet = await feed.json();
          if (Array.isArray(packet.frames) && packet.frames.length) {
            if (packet.truncated) setBufferWarning('Some live frames expired before they could be received. The saved replay may contain the missing actions.');
            for (const frame of packet.frames) receive({...frame, sessionId: details.sessionId, revision: frame.sequence});
            setNotice('Live · buffered, read-only spectator'); setError('');
            if (packet.hasMore && page < 3) continue;
            return;
          }
          if (Array.isArray(packet.frames) && newest.current) return;
          break;
        }
        const response = await fetch(sessionUrl, { signal: abort.signal });
        if (!active) return;
        if (response.status === 404) { setNotice('The match ended. Waiting for the next one…'); setError(''); return; }
        if (!response.ok) throw new Error(`The live table could not be read (${response.status}).`);
        const incoming = await response.json() as EngineView;
        if (!active) return;
        // Discard all actionable decisions before handing this state to Pixi.
        const accepted = latestEngineView(newest.current?.sessionId === incoming.sessionId ? newest.current : null, incoming);
        if (accepted) receive(accepted, false);
        setNotice('Live · read-only spectator · basic snapshot stream'); setError('');
      } catch (reason) { if (active) setError(reason instanceof Error ? reason.message : 'The live connection was interrupted.'); }
      finally { if (active && !finished) timer = setTimeout(() => void poll(), 750); }
    }
    void poll();
    return () => { active = false; clearTimeout(timer); abort.abort(); };
  }, [initialJob.id]);
  const matchup = job.details?.matchup || job.details?.deckNames?.join(' vs ') || job.label;
  const currentView = view;
  const playbackControls = <div className="pixi-playback-controls" role="group" aria-label="Live playback">
    <button type="button" onClick={() => setPaused(value => !value)}>{paused ? 'Resume viewing' : 'Pause viewing'}</button>
    <label>Speed<select aria-label="Live playback speed" value={speed} onChange={e => setSpeed(Number(e.target.value))}><option value={.5}>0.5×</option><option value={1}>1×</option><option value={2}>2×</option><option value={4}>4×</option></select></label>
    <span>{buffered} actions buffered</span>
    <button type="button" disabled={!buffered} onClick={() => {queue.current = []; setBuffered(0); setSoundEpoch(value => value + 1); setView(newest.current);}}>Jump to live</button>
  </div>;
  return <dialog className="replay-theater live-match-theater" ref={dialog} aria-labelledby="live-match-title" onCancel={(event) => { event.preventDefault(); onClose(); }}>
    <header className="replay-theater-header"><button type="button" className="secondary-action" onClick={onClose}>← Back to app</button><div><small>{terminal(job) ? 'Finished' : 'Live on this computer'} · {job.details?.currentMatchIndex ? `Match ${job.details.currentMatchIndex} / ${job.details.total ?? '?'}` : 'Preparing match'}</small><h2 id="live-match-title">{matchup}</h2></div><span className="live-view-progress">{job.details?.completed ?? 0}/{job.details?.total ?? 1} completed</span></header>
    <div className="live-view-status"><span role="status">{notice}</span><small>Leaving this viewer does not stop the batch. New matches appear automatically.</small></div>
    {error && <p className="inline-error" role="alert">{error} Retrying without affecting play.</p>}
    {bufferWarning && <p className="muted" role="status">{bufferWarning}</p>}
    <div className="replay-stage"><LiveTableBoundary>{currentView ? <Suspense fallback={<p className="stage-loading">Loading the visual table…</p>}><LocalPixiRenderer deckSelections={decks} matchup={matchup} mode="replay" spectatorPerspective={perspective} onSpectatorPerspectiveChange={setPerspective} soundEpoch={soundEpoch} playbackControls={playbackControls} view={currentView} onAction={() => {}} onExit={onClose} /></Suspense> : <p className="stage-loading">{notice}</p>}</LiveTableBoundary></div>
  </dialog>;
}
