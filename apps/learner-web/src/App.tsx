import { FormEvent, useEffect, useMemo, useState } from 'react';

import {
  loadCompetitions,
  loadJobs,
  loadLocalDecks,
  loadStatus,
  searchDecks,
  startJob,
  stopJob,
  type CapabilityStatus,
  type CompetitionSummary,
  type DeckSummary,
  type Job,
  type LocalDeck,
} from './api';
import { workflowBlockers, type Workflow } from './readiness';

type Page = 'overview' | 'train' | 'playtest' | 'compete' | 'representation' | 'models';

const pages: Array<{ id: Page; label: string; glyph: string }> = [
  { id: 'overview', label: 'Overview', glyph: '⌂' },
  { id: 'train', label: 'Train', glyph: '↗' },
  { id: 'playtest', label: 'Playtest', glyph: '▶' },
  { id: 'compete', label: 'Matchmaking', glyph: '◎' },
  { id: 'representation', label: 'Representation', glyph: '◇' },
  { id: 'models', label: 'Models', glyph: '◎' },
];

const leagueUrl = 'https://staging.deepdeckleague.com';
const leagueLogoUrl = `${leagueUrl}/deep-deck-league-logo.png`;
const patreonUrl = 'https://www.patreon.com/DeepDeckLeague';

const workflowCopy: Record<Workflow, { title: string; kicker: string; description: string }> = {
  'local-training': {
    title: 'Train locally',
    kicker: 'Best place to start',
    description: 'Pick a model and launch. DeepDeckLearner chooses the safe starter settings for you.',
  },
  'online-training': {
    title: 'Train online',
    kicker: 'Hosted opponents',
    description: 'Connect your account when the versioned hosted trajectory contract is available.',
  },
  'local-playtest': {
    title: 'Test locally',
    kicker: 'Watch the behavior',
    description: 'Connect an example policy to DeepDeckEngine and inspect how it plays.',
  },
  matchmaking: {
    title: 'Join matchmaking',
    kicker: 'Take it to the League',
    description: 'Connect your account, find a deck by name, and queue an inference agent.',
  },
};

function StatusDot({ ready, label }: { ready: boolean; label: string }) {
  return <span className={`status-chip ${ready ? 'ready' : 'missing'}`}><i />{label}</span>;
}

function PatreonMark() {
  return <svg viewBox="0 0 24 24" aria-hidden="true">
    <path d="M14.82 2.4a7.18 7.18 0 1 0 0 14.36 7.18 7.18 0 0 0 0-14.36ZM2.4 21.6h3.5V2.4H2.4v19.2Z" />
  </svg>;
}

function WorkflowCard({
  workflow,
  status,
  onSelect,
}: {
  workflow: Workflow;
  status: CapabilityStatus | null;
  onSelect: (workflow: Workflow) => void;
}) {
  const blockers = workflowBlockers(status, workflow);
  const copy = workflowCopy[workflow];
  return (
    <button className="workflow-card" type="button" onClick={() => onSelect(workflow)}>
      <span className="card-kicker">{copy.kicker}</span>
      <span className="card-title">{copy.title}<b aria-hidden="true">→</b></span>
      <span className="card-copy">{copy.description}</span>
      <span className={`card-state ${blockers.length ? 'blocked' : 'ready'}`}>
        {blockers.length ? `${blockers.length} setup step${blockers.length > 1 ? 's' : ''}` : 'Ready'}
      </span>
    </button>
  );
}

function shortRevision(revision: string | null) {
  return revision ? revision.slice(0, 8) : 'not installed';
}

function DependencyPanel({ status, refresh }: { status: CapabilityStatus | null; refresh: () => void }) {
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const engine = status?.engine;
  const pixi = status?.pixi;
  const stackReady = Boolean(engine?.healthy && pixi?.built);

  async function run(action: string, payload: Record<string, unknown>) {
    setBusy(action); setError('');
    try { await startJob(payload); refresh(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Unable to start dependency task.'); }
    finally { setBusy(''); }
  }

  async function startStack() {
    setBusy('stack'); setError('');
    try {
      if (!engine?.source_available || !engine.synced || !pixi?.source_available || !pixi.synced) {
        throw new Error('Synchronize every missing or outdated dependency before starting the local stack.');
      }
      if (!engine.healthy) await startJob({ kind: 'dependency.engine.start' });
      if (!pixi.built) await startJob({ kind: 'dependency.pixi.prepare' });
      refresh();
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Unable to start the local stack.'); }
    finally { setBusy(''); }
  }

  return <section className="panel dependency-panel">
    <div className="section-heading dependency-heading"><div><span className="eyebrow">Local runtime</span><h2>Engine and visual client</h2><p>Use the compatible versions pinned by this workbench.</p></div><button className="primary" type="button" disabled={!status || stackReady || Boolean(busy)} onClick={() => void startStack()}>{stackReady ? 'Local stack ready' : busy === 'stack' ? 'Starting…' : 'Start local stack'}</button></div>
    <div className="dependency-list">
      <article>
        <div className="dependency-icon engine">E</div>
        <div className="dependency-copy"><span>DeepDeckEngine</span><strong>{engine?.healthy ? 'Running' : engine?.synced ? engine.built ? 'Ready to start' : 'Build required' : engine?.source_available ? 'Update available' : 'Not installed'}</strong><small>Current {shortRevision(engine?.revision ?? null)} · compatible {shortRevision(engine?.pinned_revision ?? null)}</small>{engine?.dirty && <em>Local changes prevent automatic synchronization.</em>}</div>
        <div className="dependency-actions"><button type="button" disabled={!engine?.source_available || !engine.synced || engine.healthy || Boolean(busy)} onClick={() => void run('engine', { kind: 'dependency.engine.start' })}>{engine?.healthy ? 'Running' : engine?.built ? 'Start' : 'Build & start'}</button><button type="button" disabled={engine?.synced || engine?.dirty || Boolean(busy)} onClick={() => void run('engine-sync', { kind: 'dependency.sync', dependency: 'engine' })}>{engine?.synced ? 'Up to date' : 'Sync version'}</button></div>
      </article>
      <article>
        <div className="dependency-icon pixi">P</div>
        <div className="dependency-copy"><span>DeepDeckPixi</span><strong>{pixi?.built ? 'Ready' : pixi?.synced ? 'Build required' : pixi?.source_available ? 'Update available' : 'Not installed'}</strong><small>Current {shortRevision(pixi?.revision ?? null)} · compatible {shortRevision(pixi?.pinned_revision ?? null)}</small>{pixi?.dirty && <em>Local changes prevent automatic synchronization.</em>}</div>
        <div className="dependency-actions"><button type="button" disabled={!pixi?.source_available || !pixi.synced || pixi.built || Boolean(busy)} onClick={() => void run('pixi', { kind: 'dependency.pixi.prepare' })}>{pixi?.built ? 'Ready' : 'Prepare'}</button><button type="button" disabled={pixi?.synced || pixi?.dirty || Boolean(busy)} onClick={() => void run('pixi-sync', { kind: 'dependency.sync', dependency: 'pixi' })}>{pixi?.synced ? 'Up to date' : 'Sync version'}</button></div>
      </article>
    </div>
    <p className="dependency-note">Sync uses the reviewed revisions pinned by DeepDeckLearner. It never follows a floating upstream branch.</p>
    {error && <p className="form-error" role="alert">{error}</p>}
  </section>;
}

function TrainingForm({ status, refresh }: { status: CapabilityStatus | null; refresh: () => void }) {
  const [source, setSource] = useState<'smoke' | 'dataset'>('smoke');
  const [model, setModel] = useState('v12');
  const [dataset, setDataset] = useState('');
  const [advanced, setAdvanced] = useState(false);
  const [epochs, setEpochs] = useState(3);
  const [learningRate, setLearningRate] = useState(0.0003);
  const [device, setDevice] = useState('cuda');
  const [error, setError] = useState('');
  const blockers = workflowBlockers(status, 'local-training');

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError('');
    try {
      await startJob({
        kind: source === 'smoke' ? 'training.smoke' : 'training.dataset',
        model,
        dataset,
        epochs,
        learning_rate: learningRate,
        device,
      });
      refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to start training.');
    }
  }

  return (
    <form className="panel configure" onSubmit={submit}>
      <div className="section-heading"><div><span className="eyebrow">Configure</span><h2>Local training</h2></div><span className="step">01</span></div>
      <div className="field-grid beginner-fields">
        <label>Model<select value={model} onChange={(event) => setModel(event.target.value)}><option value="v12">V12 · two-player</option><option value="v11">V11 · multiplayer</option></select></label>
        <div className="automatic-setting"><span>Compute</span><strong>GPU preferred</strong><small>Automatically uses CPU when CUDA is unavailable.</small></div>
      </div>
      <button className="advanced-toggle" type="button" aria-expanded={advanced} onClick={() => setAdvanced(!advanced)}><span>{advanced ? '−' : '+'}</span> Advanced settings</button>
      {advanced && <div className="advanced-fields">
        <label>Training input<select value={source} onChange={(event) => { const next = event.target.value as 'smoke' | 'dataset'; setSource(next); if (next === 'dataset' && !dataset) setDataset(status?.paths.trajectory ?? '.deepdeck/trajectories/decisions.jsonl'); }}><option value="smoke">Built-in smoke trajectory</option><option value="dataset">Trajectory JSONL file</option></select></label>
        {source === 'dataset' && <label className="wide-field">Trajectory file<input value={dataset} onChange={(event) => setDataset(event.target.value)} placeholder={status?.paths.trajectory ?? '.deepdeck/trajectories/decisions.jsonl'} required /><small>Created automatically inside this project when the workbench starts.</small></label>}
        <label>Epochs<input type="number" min="1" max="1000" value={epochs} onChange={(event) => setEpochs(Number(event.target.value))} /></label>
        <label>Learning rate<input type="number" min="0.00000001" max="1" step="0.0001" value={learningRate} onChange={(event) => setLearningRate(Number(event.target.value))} /></label>
        <label>Device<select value={device} onChange={(event) => setDevice(event.target.value)}><option value="cuda">CUDA · fallback to CPU</option><option value="cpu">CPU</option></select></label>
      </div>}
      {blockers.length > 0 && <div className="notice warning"><strong>Before you start</strong>{blockers.map((item) => <span key={item}>{item}</span>)}</div>}
      {error && <p className="form-error" role="alert">{error}</p>}
      <div className="form-actions"><button className="primary" disabled={blockers.length > 0}>Start training <span>→</span></button><small>One training job at a time protects your GPU.</small></div>
    </form>
  );
}

function OnlinePanel({ status }: { status: CapabilityStatus | null }) {
  const blockers = workflowBlockers(status, 'online-training');
  return <section className="panel configure"><div className="section-heading"><div><span className="eyebrow">Hosted training</span><h2>Connect without pretending</h2></div><span className="step">02</span></div><p className="lead">Online inference already uses your account API key. Weight updates need a complete, versioned observation/action/reward trajectory; a replay alone is not declared sufficient yet.</p><div className="notice warning"><strong>Not ready to launch</strong>{blockers.map((item) => <span key={item}>{item}</span>)}</div><button className="primary" disabled>Start online training</button></section>;
}

function PlaytestForm({ status, refresh }: { status: CapabilityStatus | null; refresh: () => void }) {
  const [agent, setAgent] = useState('random');
  const [format, setFormat] = useState('legacy');
  const [ownDeck, setOwnDeck] = useState('');
  const [opponentDeck, setOpponentDeck] = useState('');
  const [deckSearch, setDeckSearch] = useState('');
  const [decks, setDecks] = useState<LocalDeck[]>([]);
  const [catalogError, setCatalogError] = useState('');
  const [error, setError] = useState('');
  const blockers = workflowBlockers(status, 'local-playtest');
  useEffect(() => {
    if (!status?.engine.healthy) return;
    let active = true;
    void loadLocalDecks(format, status.engine.url).then((items) => {
      if (!active) return;
      setDecks(items);
      setOwnDeck(items[0]?.deckSessionId ?? '');
      setOpponentDeck(items[1]?.deckSessionId ?? items[0]?.deckSessionId ?? '');
      setCatalogError('');
    }).catch((reason) => {
      if (active) setCatalogError(reason instanceof Error ? reason.message : 'Unable to load local decks.');
    });
    return () => { active = false; };
  }, [format, status?.engine.healthy, status?.engine.url]);
  const visibleDecks = decks.filter((deck) => deck.deckName.toLowerCase().includes(deckSearch.toLowerCase()));
  async function submit(event: FormEvent) {
    event.preventDefault(); setError('');
    try { await startJob({ kind: 'playtest.agent', agent, format, deck_session_id: ownDeck, opponent_deck_session_id: opponentDeck, engine_url: status?.engine.url }); refresh(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Unable to launch playtest.'); }
  }
  return <form className="panel configure" onSubmit={submit}>
    <div className="section-heading"><div><span className="eyebrow">Local behavior test</span><h2>Play against your agent</h2></div><span className="step">03</span></div>
    <div className="field-grid">
      <label>Agent<select value={agent} onChange={(event) => setAgent(event.target.value)}><option value="random">Random baseline</option><option value="alexios">Alexios rules</option><option value="v12">V12 example</option><option value="v11">V11 example</option></select></label>
      <label>Format<select value={format} onChange={(event) => setFormat(event.target.value)}><option value="legacy">Legacy</option><option value="commander">Commander</option></select></label>
    </div>
    <label>Search available local decks<input type="search" value={deckSearch} onChange={(event) => setDeckSearch(event.target.value)} placeholder="Reanimator, Alexios, Atraxa…" /></label>
    <div className="field-grid">
      <label>Your deck<select required value={ownDeck} onChange={(event) => setOwnDeck(event.target.value)}><option value="">Choose a deck</option>{visibleDecks.map((deck) => <option key={deck.deckSessionId} value={deck.deckSessionId}>{deck.deckName}</option>)}</select></label>
      <label>Opponent deck<select required value={opponentDeck} onChange={(event) => setOpponentDeck(event.target.value)}><option value="">Choose a deck</option>{visibleDecks.map((deck) => <option key={deck.deckSessionId} value={deck.deckSessionId}>{deck.deckName}</option>)}</select></label>
    </div>
    {catalogError && <p className="form-error" role="alert">{catalogError}</p>}
    {blockers.length > 0 && <div className="notice warning"><strong>Before you start</strong>{blockers.map((item) => <span key={item}>{item}</span>)}</div>}
    {error && <p className="form-error" role="alert">{error}</p>}
    <button className="primary" disabled={blockers.length > 0 || !ownDeck || !opponentDeck}>Launch local game <span>▶</span></button>
  </form>;
}

function MatchmakingForm({ status, jobs, refresh }: { status: CapabilityStatus | null; jobs: Job[]; refresh: () => void }) {
  const [agent, setAgent] = useState('random');
  const [format, setFormat] = useState('legacy');
  const [query, setQuery] = useState('');
  const [decks, setDecks] = useState<DeckSummary[]>([]);
  const [selectedDeck, setSelectedDeck] = useState<DeckSummary | null>(null);
  const [competitions, setCompetitions] = useState<CompetitionSummary[]>([]);
  const [speed, setSpeed] = useState('1s');
  const [continuous, setContinuous] = useState(false);
  const [checkpoint, setCheckpoint] = useState('');
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState('');
  const blockers = workflowBlockers(status, 'matchmaking');
  const competition = competitions.find((item) => item.format.toLowerCase() === format);
  const checkpoints = jobs.filter((job) => job.status === 'completed' && job.artifact_path);
  const needsCheckpoint = agent === 'v11' || agent === 'v12';

  useEffect(() => {
    if (!status?.hosted.api_key_configured) {
      setCompetitions([]);
      return;
    }
    void loadCompetitions().then(setCompetitions).catch((reason) => {
      setError(reason instanceof Error ? reason.message : 'Unable to load active competitions.');
    });
  }, [status?.hosted.api_key_configured]);

  async function findDecks(event: FormEvent) {
    event.preventDefault();
    if (!status?.hosted.api_key_configured) {
      setError('Add your account API key and restart DeepDeckLearner before searching decks.');
      return;
    }
    setSearching(true); setError(''); setSelectedDeck(null);
    try { setDecks(await searchDecks(query, format)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Unable to search decks.'); }
    finally { setSearching(false); }
  }

  async function submit(event: FormEvent) {
    event.preventDefault(); setError('');
    if (!selectedDeck || !competition) return;
    try {
      await startJob({
        kind: 'matchmaking.agent', agent, speed, continuous,
        checkpoint: needsCheckpoint ? checkpoint : undefined,
        competition_version_id: competition.versionId,
        deck_version_id: selectedDeck.id,
      });
      refresh();
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Unable to join matchmaking.'); }
  }

  return <section className="panel configure matchmaking-setup">
    <div className="section-heading"><div><span className="eyebrow">Guided League setup</span><h2>Connect, choose a deck, and queue</h2></div><span className="step">04</span></div>
    <ol className="setup-steps">
      <li className={status?.hosted.api_key_configured ? 'complete' : ''}><span>1</span><div><strong>Create your account key</strong><p>Sign in, open Account → Autonomous agents, and press Generate API key.</p><a href="https://staging.deepdeckleague.com/account#autonomous-agents" target="_blank" rel="noreferrer">Open the exact account section ↗</a></div></li>
      <li className={status?.hosted.api_key_configured ? 'complete' : ''}><span>2</span><div><strong>Add the copied line to <code>.env</code></strong><p>Save <code>DEEPDECK_API_KEY=ddl_agent_…</code> in the DeepDeckLearner project root, then restart the workbench. The browser never receives the secret.</p></div></li>
      <li><span>3</span><div><strong>Find your deck by name</strong><p>DeepDeckLearner keeps the version identifier underneath this search.</p></div></li>
    </ol>
    <form className="deck-finder" onSubmit={findDecks}>
      <label>Format<select value={format} onChange={(event) => { setFormat(event.target.value); setDecks([]); setSelectedDeck(null); }}><option value="legacy">Legacy</option><option value="commander">Commander</option></select></label>
      <label>Deck name or creator<input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Reanimator, Alexios, Andrea…" /></label>
      <button className="primary" type="submit" disabled={searching || !status?.hosted.api_key_configured}>{searching ? 'Searching…' : 'Search decks'}</button>
    </form>
    {decks.length > 0 && <div className="deck-search-results" role="listbox" aria-label="Deck search results">{decks.map((deck) => <button type="button" role="option" aria-selected={selectedDeck?.id === deck.id} className={selectedDeck?.id === deck.id ? 'selected' : ''} key={deck.id} onClick={() => setSelectedDeck(deck)}><strong>{deck.name}</strong><span>{deck.creator ? `by ${deck.creator}` : 'Community deck'} · {deck.format ?? format} · v{deck.version}</span><small>{deck.playableCardCount} playable cards</small></button>)}</div>}
    <form onSubmit={submit}>
      <div className="field-grid">
        <label>Agent<select value={agent} onChange={(event) => setAgent(event.target.value)}><option value="random">Random baseline</option><option value="alexios">Alexios rules</option><option value="v12">V12 example</option><option value="v11">V11 example</option></select></label>
        <label>Decision pace<select value={speed} onChange={(event) => setSpeed(event.target.value)}><option value="100ms">100 ms</option><option value="1s">1 second</option><option value="10s">10 seconds</option></select></label>
      </div>
      {needsCheckpoint && <label>Trained checkpoint<select required value={checkpoint} onChange={(event) => setCheckpoint(event.target.value)}><option value="">Choose a completed training run</option>{checkpoints.map((job) => <option key={job.id} value={job.artifact_path ?? ''}>{job.label} · {job.artifact_path}</option>)}</select></label>}
      <label className="checkbox-label"><input type="checkbox" checked={continuous} onChange={(event) => setContinuous(event.target.checked)} />Keep rejoining after each match</label>
      {selectedDeck && <div className="selected-deck"><span>Selected deck</span><strong>{selectedDeck.name}</strong><small>{competition ? `${competition.name} · ${competition.timeControl}` : `No active ${format} competition`}</small></div>}
      {blockers.length > 0 && <div className="notice warning"><strong>Before you queue</strong>{blockers.map((item) => <span key={item}>{item}</span>)}</div>}
      {error && <p className="form-error" role="alert">{error}</p>}
      <button className="primary" disabled={blockers.length > 0 || !selectedDeck || !competition || (needsCheckpoint && !checkpoint)}>Join matchmaking <span>→</span></button>
    </form>
  </section>;
}

function JobsPanel({ jobs, refresh }: { jobs: Job[]; refresh: () => void }) {
  return <section className="panel jobs"><div className="section-heading"><div><span className="eyebrow">Controller-owned</span><h2>Recent jobs</h2></div><button className="text-button" type="button" onClick={refresh}>Refresh</button></div>{jobs.length === 0 ? <div className="empty"><span>◇</span><p>No jobs yet. A V12 smoke run is a safe first step.</p></div> : <div className="job-list">{jobs.map((job) => <article className="job" key={job.id}><div><StatusDot ready={job.status === 'completed' || job.status === 'running'} label={job.status} /><h3>{job.label}</h3><p>{job.logs.at(-1) ?? job.argv.join(' ')}</p>{job.artifact_path && <code>{job.artifact_path}</code>}</div>{job.status === 'running' && <button className="danger" type="button" onClick={() => void stopJob(job.id).then(refresh)}>Stop</button>}</article>)}</div>}</section>;
}

export default function App() {
  const [page, setPage] = useState<Page>('overview');
  const [workflow, setWorkflow] = useState<Workflow>('local-training');
  const [status, setStatus] = useState<CapabilityStatus | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loadError, setLoadError] = useState('');

  async function refresh() {
    try { const [nextStatus, nextJobs] = await Promise.all([loadStatus(), loadJobs()]); setStatus(nextStatus); setJobs(nextJobs); setLoadError(''); }
    catch (reason) { setLoadError(reason instanceof Error ? reason.message : 'Controller unavailable.'); }
  }
  useEffect(() => { void refresh(); const timer = window.setInterval(() => void refresh(), 2500); return () => window.clearInterval(timer); }, []);
  const running = useMemo(() => jobs.filter((job) => job.status === 'running').length, [jobs]);
  function selectWorkflow(next: Workflow) {
    setWorkflow(next);
    setPage(next === 'local-playtest' ? 'playtest' : next === 'matchmaking' ? 'compete' : 'train');
  }

  return <div className="app-shell"><aside><a className="brand" href={leagueUrl} target="_blank" rel="noreferrer" aria-label="Open Deep Deck League"><img src={leagueLogoUrl} alt="Deep Deck League" /><span>Learner</span></a><span className="local-badge">● Local workbench</span><nav aria-label="Main navigation">{pages.map((item) => <button className={page === item.id ? 'active' : ''} type="button" key={item.id} onClick={() => setPage(item.id)}><span>{item.glyph}</span>{item.label}</button>)}</nav><div className="aside-foot"><small>Public AI laboratory</small><a className="patreon-link" href={patreonUrl} target="_blank" rel="noreferrer"><PatreonMark /><span>Support on Patreon</span><b aria-hidden="true">↗</b></a><a className="source-link" href="https://github.com/dd-the-dd/DeepDeckLearner" target="_blank" rel="noreferrer">View source <span aria-hidden="true">↗</span></a></div></aside><main><header><div><span className="eyebrow">Deep Deck AI laboratory</span><h1>{page === 'overview' ? 'Start from what you know.' : pages.find((item) => item.id === page)?.label}</h1></div><div className="health"><StatusDot ready={Boolean(status?.engine.healthy)} label="Engine" /><StatusDot ready={Boolean(status?.pixi.built)} label="Pixi" /><span className="running-count">{running} running</span></div></header>{loadError && <p className="controller-error" role="alert">Local controller: {loadError}</p>}{page === 'overview' && <><section className="intro"><p>Train first, test the behavior, then choose a League deck by name. Open the ML controls only when you need them.</p><div className="workflow-grid">{(['local-training', 'local-playtest', 'matchmaking'] as Workflow[]).map((item) => <WorkflowCard key={item} workflow={item} status={status} onSelect={selectWorkflow} />)}</div></section><DependencyPanel status={status} refresh={refresh} /><TrainingForm status={status} refresh={refresh} /><JobsPanel jobs={jobs} refresh={refresh} /></>}{page === 'train' && <>{workflow === 'online-training' ? <OnlinePanel status={status} /> : <TrainingForm status={status} refresh={refresh} />}<div className="segmented"><button className={workflow === 'local-training' ? 'selected' : ''} onClick={() => setWorkflow('local-training')}>Local</button><button className={workflow === 'online-training' ? 'selected' : ''} onClick={() => setWorkflow('online-training')}>Online</button></div><JobsPanel jobs={jobs} refresh={refresh} /></>}{page === 'playtest' && <><DependencyPanel status={status} refresh={refresh} /><PlaytestForm status={status} refresh={refresh} /><JobsPanel jobs={jobs} refresh={refresh} /></>}{page === 'compete' && <><MatchmakingForm status={status} jobs={jobs} refresh={refresh} /><JobsPanel jobs={jobs} refresh={refresh} /></>}{page === 'representation' && <section className="panel prose"><span className="eyebrow">Magic → tensor</span><h2>A decision, not a screenshot</h2><p>The encoder combines the observable game state, each legal action, known deck context, and a previous-state delta. V11 keeps four multiplayer value slots; V12 specializes the value head for two-player Legacy.</p><div className="tensor-flow"><span>Game observation</span><b>+</b><span>Legal action</span><b>+</b><span>Known deck</span><b>→</b><span>Feature tensor</span></div><p>Feature indices and masks are versioned in <code>deepdeck_examples.deep_learning.encoding</code>. See the ML guide before changing them: a checkpoint is only compatible with the schema it learned.</p></section>}{page === 'models' && <section className="model-grid"><article className="panel model"><span>V12</span><h2>Two-player policy</h2><p>Legacy-oriented example with two value slots. Weights are intentionally not public.</p></article><article className="panel model"><span>V11</span><h2>Multiplayer policy</h2><p>Commander-oriented example with four value slots and the same public encoder family.</p></article><article className="panel model"><span>Baseline</span><h2>Readable behavior</h2><p>Random and Alexios rule-based agents make protocol and behavior testing approachable.</p></article></section>}</main></div>;
}
