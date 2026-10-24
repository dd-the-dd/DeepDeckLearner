import {
  FormEvent,
  Suspense,
  lazy,
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";

import AgentStudio, {type AgentCreationDeck} from "./AgentWorkspace";
import AgentLeaguePool, {type AgentLeaguePoolProps} from './AgentLeaguePool';
import PlaySettings, { DEFAULT_PLAY_SETTINGS, playSettingsPayload, type PlaySettingsDraft } from './PlaySettings';
import ReplayHub from './ReplayLibrary';
import AccountConnection from "./AccountConnection";
import AsyncActionButton from "./AsyncActionButton";
import DeckWorkshop from "./DeckWorkshop";
import DeckPicker from "./DeckPicker";
import SelectionFilters, { labelMatch } from './SelectionFilters';
import { supportsEntityLabels } from './EntityLabels';
import LocalInsights from "./LocalInsights";
import AssistantToolEvents from "./AssistantToolEvents";
import AssistantMessage from './AssistantMessage';
import { publishCardSearch } from './card-search-bridge';
import CardResearch from './CardResearch';
import ChatGPTConnection from './ChatGPTConnection';
import ResizableChatPanel from './ResizableChatPanel';
import { submitChatOnEnter } from './chat-keyboard';
import './global-copilot.css';
import LeagueAccessHelp from './LeagueAccessHelp';
import LeagueKeyConnection from './LeagueKeyConnection';
import LocalDiagnostics from './LocalDiagnostics';
import LiveMatchViewer from './LiveMatchViewer';
import CompetitionHistory, { CompetitionBuilder } from './LocalCompetitions';
import { ParticipantKindPicker } from './ParticipantPicker';
import type { LocalCompetition } from './api';
import { loadProviderModels, type LlmProvider } from './api';
import TableLaunch, { mergeObservedJob } from './TableLaunch';
import { agentPlayDraft, initialPreparations, type PlaySeat as Seat, type PlayPreparation, type PlayPreparations, type PlayHubDraft } from './play-preparation';
import {playPool} from './play-pool';
import PlayPoolPreview from './PlayPoolPreview';
import {
  ApiError,
  chatWithAgentStudio,
  loadAccountStatus,
  loadActiveGames,
  loadCompetitions,
  loadDownloadedDecks,
  loadDeckWorkspace,
  loadExperienceSettings,
  loadJobs,
  loadLeagueAccount,
  loadLeagueConnection,
  loadLeagueCapacity,
  loadLlmSettings,
  loadProgrammaticAgents,
  loadStatus,
  saveLlmSettings,
  saveDeckWorkspace,
  testLeagueMcp,
  startJob,
  stopJob,
  type AccountStatus,
  type AssistantToolEvent,
  type ActiveGame,
  type CapabilityStatus,
  type CompetitionSummary,
  type DeckSummary,
  type ExperienceSettings,
  type Job,
  type LeagueAccount,
  type LeagueConnection,
  type LeagueCapacity,
  type LlmSettings,
  type ProgrammaticAgent,
} from "./api";

const LocalPixiTable = lazy(() => import("./LocalPixiTable"));
const DECK_SAVE_TOOLS = new Set(['create_deck', 'import_decklist', 'import_league_deck', 'update_deck_cards', 'add_card', 'edit_card', 'move_card', 'set_card_relationship', 'update_deck_plan']);

type Page = "play" | "decks" | "agents" | "league" | "replays" | "insights" | "competitions";
type Format = "legacy" | "commander";
type ChatMessage = { role: "user" | "assistant"; content: string; toolEvents?: AssistantToolEvent[] };

const NAV: Array<{ id: Page; label: string; shortLabel?: string; hint: string; icon: string }> = [
  { id: "play", label: "Play", hint: "Local games", icon: "play" },
  { id: "decks", label: "Decks", hint: "Build & sync", icon: "cards" },
  { id: "agents", label: "Agents", hint: "Teach your AI", icon: "spark" },
  { id: "league", label: "League", hint: "Compete online", icon: "trophy" },
  { id: "competitions", label: "Competitions", shortLabel: "Events", hint: "Local tournaments", icon: "trophy" },
  { id: "replays", label: "Replays", hint: "Watch every game", icon: "replay" },
  { id: "insights", label: "Insights", hint: "Track progress", icon: "chart" },
];

const TITLES: Record<Page, { eyebrow: string; title: string; copy: string }> = {
  competitions: { eyebrow: 'Local competitions', title: 'Follow the field. Find your champion.', copy: 'Your tournaments, brackets and standings — saved on this computer.' },
  play: {
    eyebrow: "Local play",
    title: "Set the table. Play your way.",
    copy: "Choose the format, who occupies each seat, and the exact decks your agents know.",
  },
  decks: {
    eyebrow: "Deck workshop",
    title: "Your deck library, in one place.",
    copy: "Find League decks, bring them home, and prepare the lists your agents will master.",
  },
  agents: {
    eyebrow: "Agent workshop",
    title: "Teach an AI how your deck thinks.",
    copy: "Start from a shared strategic graph, then personalize matchups, cards, tags, and timing.",
  },
  league: {
    eyebrow: "Deep Deck League",
    title: "Send your agent into competition.",
    copy: "Choose an agent and a League. Your pool connects agents as account places become available.",
  },
  replays: {
    eyebrow: "Game library",
    title: "Every game becomes evidence.",
    copy: "League and local games land here automatically, ready to review turn by turn.",
  },
  insights: {
    eyebrow: "Performance",
    title: "See what is working—and what is not.",
    copy: "Compare agents, decks, and recent sessions without digging through training logs.",
  },
};

function Icon({ name }: { name: string }) {
  const paths: Record<string, ReactNode> = {
    play: <><path d="M8 5v14l11-7z" /></>,
    cards: <><rect x="5" y="3" width="13" height="18" rx="2" /><path d="m8 8 7 8M15 8l-7 8" /></>,
    spark: <><path d="m12 2 1.8 5.2L19 9l-5.2 1.8L12 16l-1.8-5.2L5 9l5.2-1.8z" /><path d="m18.5 15 .8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8z" /></>,
    trophy: <><path d="M8 4h8v5a4 4 0 0 1-8 0z" /><path d="M8 6H4v2a4 4 0 0 0 4 4M16 6h4v2a4 4 0 0 1-4 4M12 13v5M8 21h8M9 18h6" /></>,
    replay: <><path d="M4 12a8 8 0 1 0 2.3-5.7L4 8.6" /><path d="M4 4v4.6h4.6" /><path d="M12 8v4l3 2" /></>,
    chart: <><path d="M4 20V10M10 20V4M16 20v-7M22 20H2" /></>,
    settings: <><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.8 2.8-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6v.2h-4V21a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1L4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9A1.7 1.7 0 0 0 3 14H2.8v-4H3a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9L4.2 7 7 4.2l.1.1a1.7 1.7 0 0 0 1.9.3A1.7 1.7 0 0 0 10 3V2.8h4V3a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1L19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.6 1h.2v4H21a1.7 1.7 0 0 0-1.6 1z" /></>,
    search: <><circle cx="11" cy="11" r="7" /><path d="m16 16 5 5" /></>,
    plus: <><path d="M12 5v14M5 12h14" /></>,
    close: <><path d="m6 6 12 12M18 6 6 18" /></>,
    cloud: <><path d="M7 18h10a4 4 0 0 0 .7-7.9A6 6 0 0 0 6.3 8.6 4.7 4.7 0 0 0 7 18Z" /><path d="m9 13 3-3 3 3M12 10v6" /></>,
    bot: <><rect x="4" y="7" width="16" height="12" rx="3" /><path d="M12 3v4M8 12h.01M16 12h.01M8 16h8" /></>,
  };
  return <svg className="dd-icon" viewBox="0 0 24 24" aria-hidden="true">{paths[name] ?? paths.spark}</svg>;
}

function Pill({ state, children }: { state?: "ready" | "busy" | "quiet" | "warn"; children: ReactNode }) {
  return <span className={`dd-pill ${state ?? "quiet"}`}><i />{children}</span>;
}


export function PlayHub({
  status,
  agents,
  decks,
  jobs,
  onStarted,
  initialBatchSize = 0,
  refresh,
  preferredAgentId = "",
  openAgents,
  refreshDecks,
  onWatch,
  initialCompetition = false,
  onCompetitionCreated,
  initialDraft,
  onDraftChange,
}: {
  status: CapabilityStatus | null;
  agents: ProgrammaticAgent[];
  decks: DeckSummary[];
  jobs: Job[];
  onStarted: (job: Job) => boolean | void;
  initialBatchSize?: number;
  refresh: () => void | Promise<void>;
  preferredAgentId?: string;
  openAgents: () => void;
  refreshDecks?: () => void | Promise<void>;
  onWatch?: (job: Job) => void;
  initialCompetition?: boolean;
  onCompetitionCreated?: (competition: LocalCompetition) => void;
  initialDraft?: PlayHubDraft | null;
  onDraftChange?: (draft: PlayHubDraft) => void;
}) {
  const preferredAgent = agents.find((agent) => agent.id === preferredAgentId);
  const [mode, setMode] = useState<PlayHubDraft['mode']>(initialDraft?.mode ?? (initialCompetition ? 'competition' : initialBatchSize ? "batch" : "table"));
  const [games, setGames] = useState(initialDraft?.games ?? (initialBatchSize || 10));
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState("");
  const [deckPickerSeat, setDeckPickerSeat] = useState<number | null>(null);
  const [preparations, setPreparations] = useState<PlayPreparations>(() => initialDraft?.preparations ?? initialPreparations(agents, preferredAgent));
  useEffect(() => {onDraftChange?.({mode, games, preparations});}, [mode, games, preparations, onDraftChange]);
  const batchFromTable = (table: PlayPreparation): PlayPreparation => ({...table, settings: {...DEFAULT_PLAY_SETTINGS, matchLimit: '600'}});
  const preparation = mode === 'batch' ? preparations.batch ?? batchFromTable(preparations.table) : preparations.table;
  const {format, seats, settings: playSettings} = preparation;
  function updatePreparation(update: (current: PlayPreparation) => PlayPreparation) {
    const key = mode === 'batch' ? 'batch' : 'table';
    setPreparations(current => ({...current, [key]: update(current[key] ?? batchFromTable(current.table))}));
  }
  const setFormat = (format: Format) => updatePreparation(current => ({...current, format}));
  const setSeats = (update: (current: Seat[]) => Seat[]) => updatePreparation(current => ({...current, seats: update(current.seats)}));
  const setPlaySettings = (update: PlaySettingsDraft | ((current: PlaySettingsDraft) => PlaySettingsDraft)) => updatePreparation(current => ({...current, settings: typeof update === 'function' ? update(current.settings) : update}));
  const eligibleAgents = agents.filter((agent) => agent.format === format);
  const selectionEnabled = eligibleAgents.length > 0 && eligibleAgents.every(supportsEntityLabels);
  const activeJobs = jobs.filter((job) => ["playtest.agent", "playtest.batch", "playtest.table"].includes(job.kind) && ["queued", "running"].includes(job.status));
  const latestLocal = jobs.find((job) => ["playtest.agent", "playtest.batch", "playtest.table"].includes(job.kind));
  const engineJob = jobs.find((job) => ["dependency.engine.start", "dependency.stack.prepare"].includes(job.kind) && ["queued", "running"].includes(job.status));
  const latestEngineJob = jobs.find(job => ['dependency.engine.start', 'dependency.stack.prepare'].includes(job.kind));

  useEffect(() => {setError(''); setDeckPickerSeat(null);}, [mode, format]);

  useEffect(() => {
    const count = format === "commander" ? 4 : 2;
    setSeats((current) => {
      const displacedHumans = mode === 'table' ? current.slice(count).filter(seat=>seat.kind==='human') : [];
      return Array.from({ length: count }, (_, index) => {
      // Shrinking a Commander table must not silently remove its human player.
      const seat = (current[index]?.kind !== 'human' && displacedHumans.length ? displacedHumans.shift() : current[index]) ?? { kind: index === 0 && mode === "table" ? "human" : "agent", agentId: "", deckId: "" };
      const kind = mode === "batch" ? "agent" : seat.kind;
      const agent = agents.find((candidate) => candidate.id === seat.agentId && candidate.format === format) ?? agents.find((candidate) => candidate.format === format);
      return { ...seat, kind, agentId: kind === "agent" ? seat.agentId === 'random' ? 'random' : agent?.id ?? "" : "", deckId: kind === "agent" ? (seat.agentId === 'random' || agent?.deckVersionIds.includes(seat.deckId) ? seat.deckId : "random") : seat.deckId === 'random' ? 'random' : decks.some((deck) => deck.id === seat.deckId && deck.format === format) ? seat.deckId : '' } as Seat;
    });});
  }, [agents, decks, format, mode]);

  const hasHuman = seats.some((seat) => seat.kind === 'human');
  const seatPools = seats.map(seat => playPool(seat, format, agents, decks));
  const emptyRandomSeat = seats.findIndex((seat, index) => (seat.deckId === 'random' || seat.kind === 'agent' && seat.agentId === 'random') && Boolean(seatPools[index].problem));
  useEffect(() => {
    setPlaySettings((current) => ({ ...current, matchLimit: !hasHuman && !current.matchLimit ? '600' : current.matchLimit,
      startingSeat: !['random', 'rotate'].includes(current.startingSeat) && Number(current.startingSeat) > seats.length ? 'rotate' : current.startingSeat }));
  }, [hasHuman, seats.length, mode]);

  function updateSeat(index: number, patch: Partial<Seat>) {
    setSeats((current) => current.map((seat, seatIndex) => {
      if (seatIndex === index) return { ...seat, ...patch, ...(patch.kind === 'human' && !decks.some((deck) => deck.id === seat.deckId && deck.format === format) ? { deckId: '' } : {}) };
      return seat;
    }));
  }

  async function launch() {
    const agentSeats = seats.filter((seat) => seat.kind === "agent");
    const humanSeats = seats.filter((seat) => seat.kind === "human");
    if (agentSeats.some((seat) => seat.agentId !== 'random' && !agents.some((agent) => agent.id === seat.agentId))) {
      setError("Choose an agent for every AI seat.");
      return;
    }
    if (agentSeats.some((seat) => !seat.deckId)) {
      setError("Choose a deck or Random for every AI seat.");
      return;
    }
    if (agentSeats.some((seat) => {
      if (seat.agentId === 'random') return false;
      const agent = agents.find((item) => item.id === seat.agentId);
      return !agent || agent.format !== format || (seat.deckId === 'random' ? !agent.decks.some((deck) => !deck.unavailable) : !agent.decks.some((deck) => deck.id === seat.deckId && !deck.unavailable));
    })) { setError('A mastered deck is unavailable. Re-import it in Decks or update the agent’s pool in Agents.'); return; }
    const invalidPool = seats.findIndex((seat, index) => seat.deckId && Boolean(seatPools[index].problem));
    if (invalidPool >= 0) {setError(`Seat ${invalidPool + 1}: ${seatPools[invalidPool].problem}`); return;}
    if (mode === 'batch' && (!Number.isInteger(games) || games < 1 || games > 1000)) { setError('Choose a whole number of matches from 1 to 1000.'); return; }
    setStarting(true);
    setError("");
    try {
      if (humanSeats.some((seat) => seat.deckId !== 'random' && !decks.some((deck) => deck.id === seat.deckId && deck.format === format))) {
        setError("Choose or import a deck for the human seat.");
        return;
      }
      const settings = playSettingsPayload(playSettings, humanSeats.length > 0, seats.length);
      const job = await startJob({
          kind: mode === "batch" ? "playtest.batch" : "playtest.table",
          ...(agentSeats[0] ? {model_id: agentSeats[0].agentId} : {}),
          format,
          games: mode === "batch" ? games : 1,
          seats: seats.map((seat) => ({ kind: seat.kind, agent_id: seat.agentId, deck_id: seat.deckId || "random", ...(seat.agentTag ? {agent_tags: [seat.agentTag]} : {}), ...(seat.agentCollection ? {agent_collections: [seat.agentCollection]} : {}), ...(seat.deckTag ? {deck_tags: [seat.deckTag]} : {}), ...(seat.deckCollection ? {deck_collections: [seat.deckCollection]} : {}) })),
          engine_url: status?.engine.url ?? "http://127.0.0.1:8787",
          ...settings,
      });
      onStarted(job);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The table could not be started.");
    } finally {
      setStarting(false);
    }
  }

  if (mode === 'competition') return <div className="play-hub"><section className="setup-card competition-mode-bar"><div className="competition-actions"><button type="button" onClick={() => setMode('table')}>← Play a game</button><button type="button" onClick={() => setMode('batch')}>Simple AI batch</button><strong>Competition batch</strong></div></section><CompetitionBuilder agents={agents} decks={decks} refreshDecks={refreshDecks ?? refresh} engineUrl={status?.engine.url ?? 'http://127.0.0.1:8787'} ready={Boolean(status?.engine.healthy)} enginePreparing={Boolean(engineJob)} engineError={latestEngineJob?.status === 'failed' ? latestEngineJob.logs?.at(-1) ?? 'The local worker failed.' : undefined} onStartEngine={async () => {onStarted(await startJob({kind: 'dependency.engine.start'}));}} onCreated={competition => onCompetitionCreated?.(competition)} /></div>;
  return <div className="play-hub">
    <button type="button" className="competition-entry" onClick={() => setMode('competition')}><Icon name="trophy" /><span><strong>Organize a competition</strong><small>Elimination · Round robin · Swiss · Custom loss limit</small></span><span>Configure →</span></button>
    <section className="setup-card">
      <div className="setup-strip">
        <div><span>1</span><strong>Game</strong></div><i />
        <div><span>2</span><strong>Seats</strong></div><i />
        <div><span>3</span><strong>Play</strong></div>
      </div>
      <div className="game-options">
        <div><label>Game mode</label><div className="segmented"><button className={mode === "table" ? "active" : ""} type="button" onClick={() => setMode("table")}><Icon name="play" /><span><strong>Play a game</strong><small>You or your agents take the seats</small></span></button><button className={mode === "batch" ? "active" : ""} type="button" onClick={() => setMode("batch")}><Icon name="replay" /><span><strong>AI batch</strong><small>Run matches and collect stats</small></span></button></div></div>
        <label>Format<select value={format} onChange={(event) => setFormat(event.target.value as Format)}><option value="legacy">Legacy · 2 seats</option><option value="commander">Commander · 4 seats</option></select></label>
        <div className="format-match-rule"><span>Match rules</span><strong>{format === 'legacy' ? 'Best of three' : 'Single game'}</strong><small>{format === 'legacy' ? 'Sideboarding between games' : 'Four-player Commander'}</small></div>
        {mode === "batch" && <label>Matches<input type="number" min="1" max="1000" value={games} onChange={(event) => setGames(Number(event.target.value))} /></label>}
      </div>
      <p className="play-preparation-note">Game and batch setups are kept separately during this app session, including after visiting another tab or returning from a table.</p>
    </section>
    <PlaySettings value={playSettings} onChange={setPlaySettings} hasHuman={hasHuman} seats={seats.length} />
    {!eligibleAgents.length && seats.some(seat=>seat.kind==='agent') && <section className="runtime-setup feature-card"><div><strong>No {format === 'legacy' ? 'Legacy' : 'Commander'} agent yet</strong><p>Create a deck specialist in Agents, or choose Player for local shared-screen play.</p></div><button className="primary-action" type="button" onClick={openAgents}>Open Agents</button></section>}

    {!status && <section className="runtime-setup feature-card" role="status"><div><strong>Checking your local game engine…</strong><p>You can prepare decks and agents while DeepDeck checks the connection.</p></div></section>}
    {status && !status.engine.healthy && <section className="runtime-setup feature-card"><div><strong>{engineJob ? "Preparing your local game engine…" : "Start your local game engine"}</strong><p>{engineJob ? "The first build can take a few minutes. You can keep preparing decks and agents." : "DeepDeck runs your games on this computer. Start it once before playing."}</p></div><AsyncActionButton loading={starting || Boolean(engineJob)} loadingLabel="Starting…" onClick={async () => { setStarting(true); setError(""); try { onStarted(await startJob({ kind: "dependency.engine.start" })); } catch (reason) { setError(reason instanceof Error ? reason.message : "The engine could not start."); } finally { setStarting(false); } }}>Start game engine</AsyncActionButton></section>}

    <section className="seat-section">
      <header className="feature-heading"><div><span className="section-kicker">Seats</span><h2>Who is playing?</h2><p>AI seats only show decks that agent has learned. “Random” lets it choose from that pool.</p></div><Pill state={status?.engine.healthy ? "ready" : "warn"}>{!status ? "Checking Engine…" : status.engine.healthy ? "Engine ready" : "Engine setup needed"}</Pill></header>
      <div className={`seat-grid seats-${seats.length}`}>{seats.map((seat, index) => {
        const selectedAgent = eligibleAgents.find((agent) => agent.id === seat.agentId);
        const possibleAgents = eligibleAgents.filter((agent) => labelMatch(agent, seat.agentTag, seat.agentCollection));
        const deckPool = seat.kind === "agent" ? seat.agentId === 'random' ? decks.filter((deck) => deck.format === format && possibleAgents.some((agent) => agent.deckVersionIds.includes(deck.id))) : selectedAgent?.decks ?? [] : decks.filter((deck) => deck.format === format);
        const eligibleDecks = deckPool.filter((deck) => labelMatch(deck, seat.deckTag, seat.deckCollection));
        return <article className={`seat-card ${seat.kind}`} key={index}>
          <header><span>Seat {index + 1}</span><ParticipantKindPicker kind={seat.kind} humanDisabled={mode === 'batch'} onChange={kind => {const first = eligibleAgents[0]; updateSeat(index, kind === 'human' ? {kind, agentId: ''} : {kind, agentId: first?.id ?? '', deckId: 'random'});}} /></header>
          <div className="seat-avatar">{seat.kind === "human" ? <span>{seats.filter(item=>item.kind==='human').length>1 ? `PLAYER ${index+1}` : 'YOU'}</span> : <Icon name="bot" />}</div>
          {seat.kind === 'agent' && selectionEnabled && <SelectionFilters kind="agent" items={eligibleAgents} value={seat} onChange={(patch) => updateSeat(index, {...patch, agentId:'random',deckId:'random'})} />}
          {seat.kind === "agent" && <label>Agent<select value={seat.agentId} onChange={(event) => updateSeat(index, { agentId: event.target.value, deckId: "random" })}><option value="">Choose an agent</option>{selectionEnabled && <option value="random">{seat.agentTag || seat.agentCollection ? 'Random agent from filtered pool' : 'Random agent · all eligible agents'}</option>}{possibleAgents.map((agent) => <option value={agent.id} key={agent.id}>{agent.name} · r{agent.revision}</option>)}</select></label>}
          {selectionEnabled && <SelectionFilters kind="deck" items={deckPool} value={seat} onChange={(patch) => updateSeat(index, {...patch,deckId:'random'})} />}
          <label>Deck<select value={seat.deckId} onChange={(event) => updateSeat(index, { deckId: event.target.value })}><option value="">Choose a deck</option>{(seat.kind === 'agent' || selectionEnabled) && <option value="random">{seat.deckTag || seat.deckCollection ? 'Random deck from filtered pool' : seat.kind === 'agent' ? 'Random from mastered decks' : 'Random · all eligible local decks'}</option>}{eligibleDecks.map((deck) => <option value={deck.id} key={deck.id} disabled={Boolean(deck.unavailable)}>{deck.name}{deck.unavailable ? ' · unavailable' : ''}</option>)}</select></label>
          {(seat.deckId === 'random' || seat.kind === 'agent' && seat.agentId === 'random') && <PlayPoolPreview pool={seatPools[index]} seat={index + 1} human={seat.kind === 'human'} disabled={starting} onClearFilters={seat.agentTag || seat.agentCollection || seat.deckTag || seat.deckCollection ? () => updateSeat(index, {agentTag:'',agentCollection:'',deckTag:'',deckCollection:''}) : undefined} />}
          {seat.kind === "human" && <button className="browse-seat-decks" type="button" onClick={() => setDeckPickerSeat(index)}>Browse local & League decks</button>}
          {seat.kind === "agent" && selectedAgent && <small>{selectedAgent.decks.length} mastered deck{selectedAgent.decks.length === 1 ? "" : "s"} · {selectedAgent.profile} plan</small>}
        </article>;
      })}</div>
      {error && <p className="inline-error" role="alert">{error}</p>}
      {!error && latestLocal?.status === "failed" && <p className="inline-error" role="alert">The last session could not finish. {latestLocal.logs.at(-1) ?? "Check the decks and try again."}</p>}
      {seats.filter(seat=>seat.kind==='human').length > 1 && <p className="muted" role="note">Shared-screen play: pass the screen at each player decision. Hands stay hidden until that player is ready. This is local play, not an online invitation.</p>}
      <div className="launch-bar"><div><strong>{emptyRandomSeat >= 0 ? `Seat ${emptyRandomSeat + 1} needs a playable pool` : mode === "batch" ? `${games} matches ready to queue` : `${format === "legacy" ? "1 vs 1" : "Four-player"} table`}</strong><small>{format === "legacy" ? "Best of 3" : "Single game"} · replays saved automatically</small></div><AsyncActionButton className="launch-button" loading={starting} loadingLabel="Preparing the table…" disabled={!status?.engine.healthy || seats.some(seat=>seat.kind==='agent') && !eligibleAgents.length || emptyRandomSeat >= 0} onClick={() => void launch()}><Icon name="play" />{mode === "batch" ? "Run batch" : "Start game"}</AsyncActionButton></div>
    </section>
    {activeJobs.map((job) => <section className="active-strip" key={job.id}><span className="live-dot" /><div><strong>{job.kind === 'playtest.batch' ? 'AI batch in progress' : 'Local table active'}</strong><small>{job.details?.matchup || job.label}{job.details?.total ? ` · ${job.details.completed ?? 0}/${job.details.total} completed` : ''}</small></div>{job.kind === 'playtest.batch' && onWatch && <button type="button" onClick={() => onWatch(job)}>Watch live</button>}{job.kind === 'playtest.table' && job.details?.sessionId && <button type="button" onClick={() => onStarted(job)}>Open table</button>}<button type="button" onClick={() => void stopJob(job.id).then(refresh)}>Stop</button></section>)}
    {deckPickerSeat !== null && <DeckPicker decks={decks} format={format} onImported={refreshDecks ?? refresh} onClose={() => setDeckPickerSeat(null)} onPick={(deckId) => { updateSeat(deckPickerSeat, { deckId }); setDeckPickerSeat(null); }} />}
  </div>;
}

export function AssistantDrawer({
  context,
  open,
  onClose,
  onChanged,
  onStarted,
  onConnectLeague,
  onNavigate,
  onOpenDeck,
}: {
  context: "deck" | "agent";
  open: boolean;
  onClose: () => void;
  onChanged: (deckId?: string) => void | Promise<void>;
  onStarted: (job: Job) => boolean | void;
  onConnectLeague?: () => void;
  onNavigate?: (page: string) => void;
  onOpenDeck?: (deckId: string) => void;
}) {
  const [settings, setSettings] = useState<LlmSettings | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [prompt, setPrompt] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [configuring, setConfiguring] = useState(false);
  const [streamStatus, setStreamStatus] = useState('');
  const [streaming, setStreaming] = useState(false);
  const [searchFormat,setSearchFormat]=useState<'legacy'|'commander'|null>(null);
  function prepareSearch(result:unknown) {
    if(!publishCardSearch(result,window.localStorage.getItem('deepdeck.activeDeckId')??''))return;
    const format=(result as {filters:{format:'legacy'|'commander'}}).filters.format;
    setSearchFormat(format);
  }
  const messageViewport=useRef<HTMLDivElement>(null);
  const followStream=useRef(true);
  useEffect(()=>{if(followStream.current && messageViewport.current) messageViewport.current.scrollTop=messageViewport.current.scrollHeight;},[messages,streamStatus]);
  const previousTarget = useRef("");
  const isOpen = useRef(open);
  isOpen.current = open;
  const inFlight = useRef(false);
  useEffect(() => {
    if (!open) return;
    const target = `${context}:${window.localStorage.getItem(context === "deck" ? "deepdeck.activeDeckId" : "deepdeck.activeAgentId") ?? ""}`;
    if (previousTarget.current !== target) { setMessages([]); setPrompt(""); setError(""); setSearchFormat(null); previousTarget.current = target; }
  }, [context, open]);
  useEffect(() => { if (open) void loadLlmSettings().then(setSettings).catch(() => setError("Assistant settings could not be loaded. Close and reopen to retry.")); }, [open]);
  async function addSuggestedCard(name: string) {
    if (inFlight.current) throw new Error('Wait for the current assistant action to finish before adding a card.');
    const deckId = window.localStorage.getItem("deepdeck.activeDeckId");
    if (!deckId) throw new Error("Open or create a local deck first.");
    const target = previousTarget.current;
    inFlight.current = true;
    setBusy(true); setError('');
    try {
      const deck = await loadDeckWorkspace(deckId);
      if (previousTarget.current !== target || window.localStorage.getItem('deepdeck.activeDeckId') !== deckId) {
        throw new Error('The active deck changed. No card was added; reopen the assistant for the intended deck.');
      }
      const cards = deck.cards.map((card) => ({ ...card, tags: [...card.tags] }));
      const existing = cards.find((card) => card.name.toLocaleLowerCase() === name.toLocaleLowerCase() && card.section === "main");
      if (existing) existing.quantity += 1;
      else cards.push({ name, quantity: 1, section: "main", tags: [] });
      let saved;
      try { saved = await saveDeckWorkspace(deckId, { cards, expectedVersion: deck.version }); }
      catch (reason) {
        if (reason instanceof ApiError && reason.status === 409) {
          throw new Error('The deck changed before this card could be added. Nothing was overwritten. Click + Add again to add it to the latest saved deck.');
        }
        if (!(reason instanceof ApiError) || reason.status >= 500) {
          throw new Error(`Could not confirm whether ${name} was saved. Reopen the deck and check its quantity before adding it again.`);
        }
        throw reason;
      }
      if (previousTarget.current === target) setMessages((current) => [...current, { role: "assistant", content: `${name} added to the main deck.`, toolEvents: [{ tool: "add_card", ok: true, result: { name: saved.name, revision: saved.version } }] }]);
      // A failed refresh must never turn a confirmed write into an "add failed"
      // retry: that would encourage the user to add a second copy accidentally.
      try { await onChanged(); }
      catch { if (previousTarget.current === target) setError(`${name} was saved, but the library could not refresh. Do not add it again; reopen the deck to refresh.`); }
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }
  async function send(event: FormEvent) {
    event.preventDefault();
    const content = prompt.trim();
    if (!content || inFlight.current) return;
    inFlight.current = true;
    const scoped = context === "deck" ? `[Deck workshop] ${content}` : content;
    const conversation = [...messages, { role: "user" as const, content: scoped }];
    setMessages(conversation);
    setPrompt("");
    setBusy(true);
    setError("");
    const requestTarget = previousTarget.current;
    let partialText='';const partialEvents:AssistantToolEvent[]=[];
    setStreamStatus('Connecting to your assistant…');setStreaming(true);followStream.current=true;
    try {
      const result = await chatWithAgentStudio(
        conversation,
        context,
        window.localStorage.getItem("deepdeck.activeDeckId") ?? "",
        context === "agent" ? window.localStorage.getItem("deepdeck.activeAgentId") ?? "" : "",
        event=>{
          if(previousTarget.current!==requestTarget) return;
          if(event.type==='status') setStreamStatus(event.message);
          if(event.type==='delta') {partialText+=event.text;setStreamStatus('Writing…');}
          if(event.type==='tool') {
            partialEvents.push(event.event);setStreamStatus(event.event.ok?'Action completed':'Action needs attention');
            if(event.event.ok && ['search_cards','set_card_search_filters'].includes(event.event.tool)) prepareSearch(event.event.result);
          }
          if(event.type==='delta' || event.type==='tool') setMessages([...conversation,{role:'assistant',content:partialText,toolEvents:[...partialEvents]}]);
        },
      );
      const sameTarget = previousTarget.current === requestTarget;
      let responseTarget = requestTarget;
      let changedDeckId: string | undefined;
      if (sameTarget) setMessages([...conversation, { role: "assistant", content: result.message, toolEvents: result.toolEvents }]);
      for (const event of result.toolEvents) {
        if (sameTarget && event.ok && ['search_cards','set_card_search_filters'].includes(event.tool)) {
          prepareSearch(event.result);
        }
        if (sameTarget && event.ok && event.result && typeof event.result === "object" && "id" in event.result && typeof event.result.id === "string") {
          if (event.tool === "create_agent") { window.localStorage.setItem("deepdeck.activeAgentId", event.result.id); if (context === 'agent') responseTarget = `agent:${event.result.id}`; }
          if (DECK_SAVE_TOOLS.has(event.tool) && /^localdeck-[a-f0-9]{12}$/.test(event.result.id)) {
            changedDeckId = event.result.id;
            window.localStorage.setItem("deepdeck.activeDeckId", changedDeckId);
            if (context === 'deck') responseTarget = `deck:${changedDeckId}`;
          }
        }
      }
      if (sameTarget) previousTarget.current = responseTarget;
      const refreshWork = (async () => {
        if (!result.toolEvents.some((event) => event.ok && (DECK_SAVE_TOOLS.has(event.tool) || ['create_agent', 'update_agent', 'start_playtest'].includes(event.tool)))) return;
        try { await onChanged(changedDeckId); }
        catch { if (previousTarget.current === responseTarget) setError('Your actions completed, but the library could not refresh. Do not repeat the request; reopen this page to refresh.'); }
      })();
      const started = result.toolEvents.find((event) => event.tool === "start_playtest" && event.ok && event.result && typeof event.result === "object" && "id" in event.result);
      if (started && sameTarget && previousTarget.current === responseTarget && isOpen.current) {
        if (onStarted(started.result as Job) !== false) onClose();
      }
      await refreshWork;
    } catch (reason) {
      if(previousTarget.current===requestTarget && partialEvents.some(event=>event.ok && (DECK_SAVE_TOOLS.has(event.tool) || ['create_agent','update_agent','start_playtest'].includes(event.tool)))) {
        try {await onChanged();} catch { /* The completed actions remain visible; never retry a mutation. */ }
      }
      setError(reason instanceof Error ? reason.message : "The assistant could not answer.");
    } finally {
      inFlight.current = false;
      setBusy(false);
      setStreaming(false);
    }
  }
  return <div className={`assistant-layer${open ? " open" : ""}`} aria-hidden={!open}>
    <button className="assistant-scrim" type="button" onClick={onClose} tabIndex={open ? 0 : -1} aria-label="Close assistant" />
    <ResizableChatPanel label={`${context} assistant`} open={open}>
      <header><div className="assistant-orb"><Icon name="spark" /></div><div><span>DeepDeck copilot</span><strong>{context === "deck" ? "Build this deck with me" : "Shape this agent with me"}</strong></div><button type="button" onClick={onClose} aria-label="Close"><Icon name="close" /></button></header>
      {onNavigate && <nav className="assistant-page-nav" aria-label="Assistant page navigation">{NAV.map(item => <button type="button" key={item.id} onClick={() => onNavigate(item.id)}>{item.label}</button>)}</nav>}
      {!settings ? <p className="assistant-loading" role="status">{error || "Loading assistant settings…"}</p> : !settings.configured || configuring ? <AssistantSetup initial={settings} onConnectLeague={onConnectLeague} onSaved={(saved) => { setSettings(saved); setConfiguring(false); }} onCancel={settings.configured ? () => setConfiguring(false) : undefined} /> : <>
        <div className="assistant-context"><Pill state="ready">{settings.providerName}</Pill><span>{settings.leagueMcpEnabled ? "League MCP requested · verify in settings" : "Local tools · public search"}</span><button type="button" disabled={busy} onClick={() => setConfiguring(true)} aria-label="Assistant settings"><Icon name="settings" /></button></div>
        <div className="assistant-messages" ref={messageViewport} onScroll={()=>{const view=messageViewport.current;if(view)followStream.current=view.scrollHeight-view.scrollTop-view.clientHeight<80;}}>
          {busy && <p className="assistant-stream-status" role="status">{streamStatus || 'Working…'}</p>}
          {messages.length === 0 && <div className="assistant-welcome"><Icon name="spark" /><h3>{context === "deck" ? "Tell me what you want to build." : "Describe how this agent should play."}</h3><p>{context === "deck" ? "I can search the League, compare similar decks, and help organize cards, tags, combos, and opening hands." : "I can inspect mastered decks, tune graph weights, add card and tag patterns, and preserve every revision."}</p><div>{(context === "deck" ? ["Find similar decks", "Improve the mana curve", "Tag my combo pieces"] : ["Make it play more like tempo", "Protect this combo", "Improve the mulligan plan"]).map((suggestion) => <button type="button" key={suggestion} onClick={() => setPrompt(suggestion)}>{suggestion}</button>)}</div></div>}
          {messages.map((message, index) => <article className={message.role} key={`${message.role}-${index}`}><strong>{message.role === "user" ? "You" : "DeepDeck"}</strong>{message.role==='assistant' ? <AssistantMessage content={message.content} events={message.toolEvents} streaming={streaming && index===messages.length-1}/> : <p>{message.content.replace(/^\[Deck workshop\]\s*/, "")}</p>}{message.toolEvents?.length ? <AssistantToolEvents events={message.toolEvents} disabled={busy} onNavigate={onNavigate} onOpenDeck={onOpenDeck} onAddCard={context === "deck" ? addSuggestedCard : undefined} onOpenPlaytest={(job) => { if (onStarted(job) !== false) onClose(); }} /> : null}</article>)}
          {searchFormat && <details className="assistant-refine-search"><summary>Refine the assistant’s card search</summary><CardResearch format={searchFormat} deckId={window.localStorage.getItem('deepdeck.activeDeckId')??''} zone="Main deck" disabled={busy} onAdd={context==='deck' && window.localStorage.getItem('deepdeck.activeDeckId') ? addSuggestedCard : undefined}/></details>}
          {busy && !(streaming && messages.at(-1)?.role==='assistant' && messages.at(-1)?.content) && <article className="assistant-thinking"><i /><i /><i /><span>{streamStatus || 'Using DeepDeck tools…'}</span></article>}
        </div>
        {settings.authMode === 'chatgpt' && <div className="assistant-plan-status"><span>Using ChatGPT plan</span><a href="https://chatgpt.com/settings/usage" target="_blank" rel="noreferrer">Manage usage ↗</a></div>}
        <form className="assistant-composer" onSubmit={send}><textarea rows={2} value={prompt} onChange={(event) => setPrompt(event.target.value)} onKeyDown={(event) => submitChatOnEnter(event, !busy && Boolean(prompt.trim()))} title="Enter to send · Shift+Enter for a new line" placeholder={context === "deck" ? "Ask for cards, a deck plan, tags…" : "Describe a strategy change…"} /><button type="submit" disabled={busy || !prompt.trim()}><span>Send</span><Icon name="play" /></button></form>
        {error && <p className="inline-error" role="alert">{error}</p>}
      </>}
    </ResizableChatPanel>
  </div>;
}

export function AssistantSetup({ initial, onSaved, onCancel, onConnectLeague }: { initial?: LlmSettings; onSaved: (settings: LlmSettings) => void; onCancel?: () => void; onConnectLeague?: () => void }) {
  const [providers, setProviders] = useState<LlmProvider[]>(initial?.providers ?? []);
  const [providerId, setProviderId] = useState(initial?.providerId ?? 'custom');
  const [authMode, setAuthMode] = useState(initial?.authMode ?? 'apiKey');
  const [chatgptReady, setChatgptReady] = useState(false);
  const [availableModels, setAvailableModels] = useState<Array<{ id: string; name: string }>>([]);
  const [loadingModels, setLoadingModels] = useState(false);
  const [providerName, setProviderName] = useState(initial?.providerName || "My LLM");
  const [baseUrl, setBaseUrl] = useState(initial?.baseUrl || "");
  const [model, setModel] = useState(initial?.model || "");
  const [apiKey, setApiKey] = useState("");
  const [clearKey, setClearKey] = useState(false);
  const [leagueMcpEnabled, setLeagueMcpEnabled] = useState(initial?.leagueMcpEnabled ?? false);
  const [testing, setTesting] = useState(false);
  const [mcpEvents, setMcpEvents] = useState<AssistantToolEvent[]>([]);
  const [mcpConnected, setMcpConnected] = useState<boolean|null>(null);
  const mcpCheck = useRef(0);
  useEffect(()=>{
    const reset=()=>{++mcpCheck.current;setMcpConnected(null);setMcpEvents([]);setTesting(false);};
    window.addEventListener('deepdeck:league-connection-changed',reset);
    return()=>{++mcpCheck.current;window.removeEventListener('deepdeck:league-connection-changed',reset);};
  },[]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const selectedProvider = providers.find((item) => item.id === providerId);
  const sameEndpoint = baseUrl.replace(/\/$/, '') === initial?.baseUrl.replace(/\/$/, '');
  useEffect(() => {
    if (initial?.providers) return;
    let mounted = true;
    loadLlmSettings().then((value) => { if (mounted) setProviders(value.providers ?? []); }).catch(() => {});
    return () => { mounted = false; };
  }, [initial?.providers]);
  function chooseProvider(id: string) {
    const provider = providers.find((item) => item.id === id);
    setProviderId(id); setApiKey(''); setClearKey(false); setAvailableModels([]); setError('');
    setAuthMode(provider?.auth === 'chatgpt' ? 'chatgpt' : 'apiKey');
    if (provider) { setProviderName(provider.name); setBaseUrl(provider.baseUrl); setModel(''); }
  }
  async function discoverModels() {
    setLoadingModels(true); setError('');
    try { setAvailableModels((await loadProviderModels({ baseUrl, ...(apiKey.trim() || clearKey ? { apiKey: clearKey ? '' : apiKey } : {}) })).items); }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Models could not be loaded.'); }
    finally { setLoadingModels(false); }
  }
  async function save(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError("");
    try { onSaved(await saveLlmSettings({ providerName, providerId, authMode, baseUrl, model, leagueMcpEnabled, ...(authMode !== 'chatgpt' && (apiKey.trim() || clearKey) ? { apiKey: clearKey ? "" : apiKey } : {}) })); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Connection could not be saved."); }
    finally { setBusy(false); }
  }
  async function testMcp() {
    const request=++mcpCheck.current;
    setTesting(true); setError(""); setMcpEvents([]); setMcpConnected(null);
    try { const result=await testLeagueMcp(); if(request===mcpCheck.current){setMcpEvents(result.toolEvents); setMcpConnected(result.connected);} }
    catch (reason) { if(request===mcpCheck.current){setMcpConnected(false); setError(reason instanceof Error ? reason.message : "The League MCP connection could not be tested.");} }
    finally { if(request===mcpCheck.current)setTesting(false); }
  }
  return <form className="assistant-setup" onSubmit={save}>
    <span className="section-kicker">{initial?.configured ? 'Assistant settings' : 'One-time setup'}</span>
    <h3>Connect the LLM you choose.</h3>
    <p>Use your ChatGPT plan, a provider API key, or a local model. Your provider receives your chat and tool results. Your Python agents play without an LLM.</p>
    <label>LLM provider<select value={providerId} onChange={(event) => chooseProvider(event.target.value)}>{providers.length ? providers.map((provider) => <option key={provider.id} value={provider.id}>{provider.name}</option>) : <option value="custom">Custom OpenAI-compatible provider</option>}</select></label>
    {selectedProvider && <div className="llm-provider-note"><p>{selectedProvider.note}</p>{selectedProvider.docs && <a href={selectedProvider.docs} target="_blank" rel="noreferrer">Provider documentation ↗</a>}</div>}
    {authMode === 'chatgpt' ? <ChatGPTConnection initial={initial?.chatgpt} model={model} onModel={setModel} onReady={setChatgptReady} /> : <>
      <label>Provider name<input value={providerName} onChange={(event) => setProviderName(event.target.value)} /></label>
      <label>OpenAI-compatible URL<input type="url" value={baseUrl} onChange={(event) => { setBaseUrl(event.target.value); setProviderId('custom'); setApiKey(''); setAvailableModels([]); }} placeholder="http://localhost:11434/v1" /></label>
      <label>API key<input type="password" autoComplete="off" value={apiKey} disabled={clearKey} onChange={(event) => setApiKey(event.target.value)} placeholder={initial?.hasApiKey && sameEndpoint ? 'Saved key kept unless replaced' : 'Provider API key; optional for local models'} /></label>
      {initial?.hasApiKey && sameEndpoint && <label className="assistant-checkbox"><input type="checkbox" checked={clearKey} onChange={(event) => setClearKey(event.target.checked)} />Remove saved model key</label>}
      <AsyncActionButton type="button" loading={loadingModels} loadingLabel="Loading model catalog…" disabled={!baseUrl} onClick={() => void discoverModels()}>Load available models · no generation</AsyncActionButton>
      {!!availableModels.length && <label>Available models<select value={model} onChange={(event) => setModel(event.target.value)}><option value="">Choose a model</option>{availableModels.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>}
      <label>Model<input value={model} onChange={(event) => setModel(event.target.value)} placeholder="Choose a tool-capable model ID" /></label>
      <small>Keys are kept separately for each endpoint. Model access and tool support depend on your account. Saving does not generate text.</small>
    </>}
    <fieldset className="assistant-research-settings"><legend>League research</legend><label className="assistant-checkbox"><input type="checkbox" checked={leagueMcpEnabled} onChange={(event) => {setLeagueMcpEnabled(event.target.checked);setMcpConnected(null);setMcpEvents([]);}} />Use my League MCP connection</label><p>Optional, read-only access to permitted site decks, tags and card research. It never publishes changes. Public search works without it.</p><LeagueAccessHelp onConnect={onConnectLeague}/><AsyncActionButton type="button" loading={testing} loadingLabel="Testing League…" disabled={!leagueMcpEnabled} onClick={() => void testMcp()}>Test League tools · no LLM call</AsyncActionButton><p className={mcpConnected===true?'inline-success':'inline-notice'} role="status">{testing?'Checking MCP permissions and available tools…':mcpConnected===true?'MCP connected. Your permitted League research tools are available.':mcpConnected===false?'MCP connection failed. Check the explanation below, your website sign-in and MCP permission, then test again.':leagueMcpEnabled?'MCP requested, not yet verified. Test the connection to confirm access.':'MCP is off. Local editing and public search remain available.'}</p><AssistantToolEvents events={mcpEvents} /></fieldset>
    <AsyncActionButton className="primary-action" loading={busy} loadingLabel="Saving…" disabled={!baseUrl || !model || (authMode === 'chatgpt' && !chatgptReady)}>{initial?.configured ? 'Save assistant settings' : 'Connect assistant'}</AsyncActionButton>
    {onCancel && <button className="text-action" type="button" onClick={onCancel}>Back to chat</button>}{error && <p className="inline-error" role="alert">{error}</p>}
  </form>;
}

export function LeagueHub(props:AgentLeaguePoolProps) {
  return <AgentLeaguePool {...props}/>;
}



function SettingsDrawer({
  open,
  onClose,
  account,
  siteAccount,
  connection,
  refresh,
}: {
  open: boolean;
  onClose: () => void;
  account: AccountStatus | null;
  siteAccount: LeagueAccount | null;
  connection: LeagueConnection | null;
  refresh: () => void | Promise<void>;
}) {
  const [message, setMessage] = useState("");
  const [checkingLeague,setCheckingLeague] = useState(false);
  async function checkLeague() {setCheckingLeague(true);setMessage('');try{await refresh();setMessage('Connection checks refreshed. Review the server, website account and agent key status.');}catch{setMessage('Connection checks could not refresh. Your saved credentials were kept.');}finally{setCheckingLeague(false);}}
  return <div className={`settings-layer${open ? " open" : ""}`} aria-hidden={!open}>
    <button type="button" className="assistant-scrim" onClick={onClose} aria-label="Close settings" />
    <aside className="settings-drawer"><header><div><span className="section-kicker">DeepDeck</span><h2>Settings</h2></div><button type="button" onClick={onClose}><Icon name="close" /></button></header>
      <section className="league-server-settings"><div className="settings-title"><Icon name="cloud" /><div><strong>League server</strong><small>{connection?.environment === 'production' ? 'Production · your live account and decks' : connection?.environment === 'staging' ? 'Staging · test environment' : 'Configured server'}</small></div><Pill state={connection?.reachable ? 'ready' : 'warn'}>{connection ? connection.reachable ? 'Reachable' : 'Unavailable' : 'Checking…'}</Pill></div>{connection && <><a href={connection.siteUrl} target="_blank" rel="noreferrer">{new URL(connection.siteUrl).host} ↗</a><p className="inline-notice">{connection.reason} No automatic switch to staging.</p></>}</section>
      <section><div className="settings-title"><Icon name="cloud" /><div><strong>Connect to League</strong><small>One account API key for MCP and agents</small></div></div><LeagueKeyConnection account={account} siteUrl={connection?.siteUrl||'https://deepdeckleague.com'} onChanged={refresh}/><p className="league-agent-status">Agent matchmaking credential: <strong>{account?.valid?'Ready':account?.configured?'Needs attention':'Not configured'}</strong>. Test MCP separately above.</p><AsyncActionButton type="button" loading={checkingLeague} loadingLabel="Checking League…" onClick={()=>void checkLeague()}>Check League connection</AsyncActionButton></section>
      <section><details><summary>Website session · advanced deck synchronization</summary><p>This optional session is only needed for website operations that do not yet accept the API key. It is not required to configure MCP.</p><AccountConnection account={siteAccount} agentAccount={account} connection={connection} onChanged={refresh}/></details></section>
      <LocalDiagnostics />
      <details><summary>Advanced laboratory</summary><p>GPU allocation, neural training, tensor diagnostics, and raw job logs are intentionally hidden from the player experience. The underlying APIs remain available for a future expert mode.</p></details>{message && <p className={message.includes("updated") || message.includes("connected") ? "settings-message success" : "settings-message"}>{message}</p>}
    </aside>
  </div>;
}

export default function PlayerApp() {
  const [page, setPage] = useState<Page>("play");
  const playDraft = useRef<PlayHubDraft | null>(null);
  const rememberPlayDraft = useCallback((draft: PlayHubDraft) => {playDraft.current = draft;}, []);
  const [status, setStatus] = useState<CapabilityStatus | null>(null);
  const [account, setAccount] = useState<AccountStatus | null>(null);
  const [siteAccount, setSiteAccount] = useState<LeagueAccount | null>(null);
  const [connection, setConnection] = useState<LeagueConnection | null>(null);
  const [agents, setAgents] = useState<ProgrammaticAgent[]>([]);
  const [agentsLoaded,setAgentsLoaded]=useState(false);
  const [decks, setDecks] = useState<DeckSummary[]>([]);
  const [competitions, setCompetitions] = useState<CompetitionSummary[]>([]);
  const [leagueCapacity, setLeagueCapacity] = useState<LeagueCapacity | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [games, setGames] = useState<ActiveGame[]>([]);
  const [batchPreset, setBatchPreset] = useState(0);
  const [competitionPreset, setCompetitionPreset] = useState(false);
  const [preferredCompetitionId, setPreferredCompetitionId] = useState('');
  const [preferredReplayId, setPreferredReplayId] = useState('');
  const [preferredAgentId, setPreferredAgentId] = useState("");
  const [preferredLeagueAgentId, setPreferredLeagueAgentId] = useState("");
  const [agentCreationDeck,setAgentCreationDeck] = useState<AgentCreationDeck | null>(null);
  const consumeAgentCreationDeck = useCallback(()=>setAgentCreationDeck(null),[]);
  const [agentDraftDirty, setAgentDraftDirty] = useState(false);
  const [deckDraftDirty, setDeckDraftDirty] = useState(false);
  const [deckRevisionKey, setDeckRevisionKey] = useState(0);
  const [experience, setExperience] = useState<ExperienceSettings | null>(null);
  const [assistant, setAssistant] = useState<"deck" | "agent">("agent");
  const [assistantOpen, setAssistantOpen] = useState(false);
  function openAssistant(context: "deck" | "agent") { setAssistant(context); setAssistantOpen(true); }
  const [settingsOpen, setSettingsOpen] = useState(false);
  useEffect(() => {
    const openLeagueSettings = () => setSettingsOpen(true);
    window.addEventListener('deepdeck:open-league-settings', openLeagueSettings);
    return () => window.removeEventListener('deepdeck:open-league-settings', openLeagueSettings);
  }, []);
  const [activeJob, setActiveJob] = useState<Job | null>(null);
  const [watchJob, setWatchJob] = useState<Job | null>(null);
  const [loadError, setLoadError] = useState("");

  const jobsRead = useRef(0);
  const agentsRead=useRef(0);
  const refreshAgents=useCallback(async()=>{const request=++agentsRead.current;const value=await loadProgrammaticAgents();if(request===agentsRead.current){setAgents(value);setAgentsLoaded(true);}},[]);
  const experienceRead = useRef(0);
  const acceptExperience = useCallback((value: ExperienceSettings) => {++experienceRead.current; setExperience(value);}, []);
  const refreshExperience = useCallback(async () => {const request=++experienceRead.current;const value=await loadExperienceSettings();if(request===experienceRead.current)setExperience(value);}, []);
  const refreshJobs = useCallback(async () => {
    const request = ++jobsRead.current;
    const latest = await loadJobs('player');
    if (request === jobsRead.current) setJobs(latest);
  }, []);

  // Saving a local list/policy must not wait for remote account diagnostics.
  const refreshLocalLibrary = useCallback(async () => {
    await Promise.all([
      refreshAgents(),
      loadDownloadedDecks().then(setDecks),
      refreshJobs(),
    ]);
  }, [refreshJobs,refreshAgents]);

  const refresh = useCallback(async () => {
    const settled = await Promise.allSettled([
      loadStatus().then(setStatus),
      loadAccountStatus().then(setAccount),
      loadLeagueAccount().then(setSiteAccount),
      loadLeagueConnection().then(setConnection),
      refreshAgents(),
      loadDownloadedDecks().then(setDecks),
      refreshJobs(),
      loadActiveGames().then(setGames),
      refreshExperience(),
    ]);
    const failed = settled.find((item): item is PromiseRejectedResult => item.status === "rejected");
    setLoadError(failed ? failed.reason instanceof Error ? failed.reason.message : "Local controller unavailable." : "");
  }, [refreshJobs,refreshExperience,refreshAgents]);

  useEffect(() => { void refresh(); const timer = window.setInterval(() => void refresh(), 10_000); return () => window.clearInterval(timer); }, [refresh]);
  useEffect(() => { if (account?.valid) void loadCompetitions().then(setCompetitions).catch(() => undefined); }, [account?.valid]);
  useEffect(() => {
    if (!account?.valid) { setLeagueCapacity(null); return; }
    if (page !== 'league') return;
    let active = true;
    let pending = false;
    async function update() {
      if (pending) return;
      pending = true;
      try { const value = await loadLeagueCapacity(); if (active) setLeagueCapacity(value); }
      catch { if (active) setLeagueCapacity(null); }
      finally { pending = false; }
    }
    void update();
    const timer = window.setInterval(() => void update(), 10_000);
    return () => { active = false; window.clearInterval(timer); };
  }, [account?.valid, page]);
  useEffect(() => { setActiveJob((current) => { const observed = current && jobs.find((job) => job.id === current.id); return current && observed ? mergeObservedJob(current, observed) : current; }); }, [jobs]);
  const observeActiveJob = useCallback((observed: Job) => setActiveJob((current) => current?.id === observed.id ? mergeObservedJob(current, observed) : current), []);
  const activeSession = activeJob && !["stopped", "failed"].includes(activeJob.status) && ["playtest.agent", "playtest.table"].includes(activeJob.kind) ? activeJob.details?.sessionId : null;
  const heading = TITLES[page];
  function navigate(next: Page) {
    if (next !== page && deckDraftDirty && !window.confirm("Discard the unsaved deck draft before leaving?")) return false;
    if (next !== page && agentDraftDirty && !window.confirm("Discard the unsaved agent draft before leaving?")) return false;
    setPage(next);
    return true;
  }

  function openJob(job: Job) {
    if (job.kind === 'playtest.batch') { setWatchJob(job); void refresh(); return true; }
    const opensTable = ['playtest.agent', 'playtest.table'].includes(job.kind);
    if (opensTable && deckDraftDirty && !window.confirm('This table uses the saved deck. Discard your unsaved deck draft before opening it?')) return false;
    if (opensTable && agentDraftDirty && !window.confirm('This table uses the saved strategy. Discard your unsaved agent draft before opening it?')) return false;
    setActiveJob(job); void refresh();
    return true;
  }

  let tableView: ReactNode = null;
  if (activeJob && ['playtest.agent', 'playtest.table'].includes(activeJob.kind) && !activeSession) {
    tableView = <TableLaunch key={activeJob.id} job={activeJob} onUpdate={observeActiveJob} onClose={() => { setActiveJob(null); void refresh(); }} />;
  }

  if (activeSession && activeJob) tableView = <div className="full-game"><Suspense fallback={<div className="stage-loading">Opening your table…</div>}><LocalPixiTable humanPlayerIds={activeJob.details?.humanPlayerIds} deckVersionIds={activeJob.details?.deckVersionIds ?? [activeJob.details?.playerDeck?.id ?? "", activeJob.details?.opponentDeck?.id ?? ""].filter(Boolean)} engineUrl={activeJob.details?.engineUrl ?? status?.engine.url ?? "http://127.0.0.1:8787"} sessionId={activeSession} matchup={activeJob.details?.matchup ?? `${activeJob.details?.playerDeck?.name ?? "Player"} vs ${activeJob.details?.opponentDeck?.name ?? "Agent"}`} onClose={async () => { await stopJob(activeJob.id); setActiveJob(null); void refresh(); }} /></Suspense></div>;

  // Keep the conversation mounted, but never overlay a table or live viewer.
  // Closing its effective open state also prevents a late assistant reply from
  // navigating away from a match. Background batches alone do not hide chat.
  const assistantAllowed = !tableView && !watchJob;

  return <>{tableView || <div className="player-app">
    <aside className="player-nav">
      <button className="product-mark" type="button" onClick={() => navigate("play")} aria-label="DeepDeck home"><span className="mark-emblem">DD</span><span><strong>DeepDeck</strong><small>PLAYER LAB</small></span></button>
      <nav aria-label="Main navigation">{NAV.map((item) => <button type="button" key={item.id} className={page === item.id ? "active" : ""} aria-label={`${item.label} ${item.hint}${item.shortLabel ? ` (${item.shortLabel})` : ''}`} aria-current={page === item.id ? 'page' : undefined} title={`${item.label} — ${item.hint}`} onClick={() => navigate(item.id)}><Icon name={item.icon} /><span aria-hidden="true"><strong>{item.shortLabel ? <><span className="nav-full-label">{item.label}</span><span className="nav-short-label">{item.shortLabel}</span></> : item.label}</strong><small>{item.hint}</small></span>{page === item.id && <i />}</button>)}</nav>
      <div className="nav-bottom"><button type="button" aria-label="Settings Accounts & storage" title="Settings — Accounts & storage" onClick={() => setSettingsOpen(true)}><Icon name="settings" /><span><strong>Settings</strong><small>Accounts & storage</small></span></button><div className="workspace-health"><span className={status?.engine.healthy ? "online" : ""}><i />{!status ? "Checking Engine…" : status.engine.healthy ? "Ready to play" : "Setup needed"}</span><small>Local · private by default</small></div></div>
    </aside>
    <main className="player-main">
      <header className="player-header"><div><span className="page-eyebrow">{heading.eyebrow}</span><h1>{heading.title}</h1><p>{heading.copy}</p></div><div className="header-actions"><Pill state={account?.valid ? "ready" : "quiet"}>{account?.valid ? "League connected" : connection?.reachable ? "League sign-in needed" : "League offline"}</Pill>{jobs.some((job) => ["running", "queued"].includes(job.status)) && <Pill state="busy">{jobs.filter((job) => ["running", "queued"].includes(job.status)).length} active</Pill>}<button type="button" className="avatar-button" onClick={() => setSettingsOpen(true)}>YOU</button></div></header>
      {loadError && <p className="controller-banner" role="alert">{loadError}</p>}
      <div className="player-content">
        {page === "play" && <PlayHub status={status} agents={agents} decks={decks} jobs={jobs} initialDraft={playDraft.current} onDraftChange={rememberPlayDraft} refresh={refresh} refreshDecks={refreshLocalLibrary} openAgents={() => navigate('agents')} preferredAgentId={preferredAgentId} initialBatchSize={batchPreset} onStarted={openJob} onWatch={setWatchJob} initialCompetition={competitionPreset} onCompetitionCreated={competition => { setPreferredCompetitionId(competition.id); setCompetitionPreset(false); navigate('competitions'); }} />}
        {page === 'competitions' && <CompetitionHistory initialId={preferredCompetitionId} onCreate={() => { if (playDraft.current) playDraft.current = {...playDraft.current, mode: 'competition'}; setCompetitionPreset(true); navigate('play'); }} onWatch={job => {if (job.kind === 'playtest.table') openJob(job); else setWatchJob(job);}} onReplays={id => { setPreferredReplayId(id ?? ''); navigate('replays'); }} />}
        {page === "decks" && <DeckWorkshop downloaded={decks} revisionKey={deckRevisionKey} onImported={refreshLocalLibrary} openAssistant={() => openAssistant("deck")} account={siteAccount} openSettings={() => setSettingsOpen(true)} onDirtyChange={setDeckDraftDirty} onCreateAgent={deck=>{if(navigate('agents')) setAgentCreationDeck({id:deck.id,name:deck.name,format:deck.format});}} />}
        {page === "agents" && <div className="agent-workshop-shell"><div className="agent-value-strip"><div><Icon name="spark" /><span><strong>A shared strategy</strong><small>Reusable weighted decisions</small></span></div><div><Icon name="cards" /><span><strong>Deck specialists</strong><small>Card and tag rules</small></span></div><div><Icon name="replay" /><span><strong>Saved versions</strong><small>Review and restore strategies</small></span></div><button type="button" onClick={() => openAssistant("agent")}><Icon name="spark" />Open strategy copilot</button></div><AgentStudio initialDeck={agentCreationDeck} onInitialDeckUsed={consumeAgentCreationDeck} refresh={refreshLocalLibrary} revisionKey={agents.map((agent) => `${agent.id}:${agent.revision}`).join("|")} openAssistant={() => openAssistant("agent")} onDirtyChange={setAgentDraftDirty} openLeague={(agent) => { setPreferredLeagueAgentId(agent.id); setPage("league"); }} openPlaytest={(agent) => { playDraft.current = agentPlayDraft(playDraft.current, agent); setPreferredAgentId(agent?.id ?? ""); setBatchPreset(0); setPage("play"); }} /></div>}
        {page === "league" && <LeagueHub account={account} siteAccount={siteAccount} connection={connection} agents={agents} competitions={competitions} jobs={jobs} refresh={refreshLocalLibrary} openSettings={() => setSettingsOpen(true)} preferredAgentId={preferredLeagueAgentId} capacity={leagueCapacity} />}
        {page === "replays" && <ReplayHub agents={agents} localDecks={decks} agentLibraryReady={agentsLoaded} onRefreshAgents={refreshAgents} settings={experience} onSettingsSaved={acceptExperience} jobs={jobs} activeGames={games} onWatch={setWatchJob} initialReplayId={preferredReplayId} openSettings={() => setSettingsOpen(true)} />}
        {page === "insights" && <LocalInsights agents={agents} decks={decks} onStartBatch={() => { if (playDraft.current) playDraft.current = {...playDraft.current, mode: 'batch', games: 20}; setBatchPreset(20); setPage("play"); }} onViewReplays={() => setPage("replays")} />}
      </div>
    </main>
    {games.length > 0 && <div className="game-count" title="Active game sessions"><span className="live-dot" />{games.length}</div>}
  </div>}
    {assistantAllowed && !assistantOpen && <button className="assistant-fab global-assistant-fab" type="button" onClick={() => setAssistantOpen(true)} aria-label="Open DeepDeck copilot"><Icon name="spark" /><span>DeepDeck copilot</span></button>}
    <AssistantDrawer context={assistant} open={assistantAllowed && assistantOpen} onNavigate={value => {if (!NAV.some(item => item.id === value)) return; if (tableView) {window.alert('Return to the lab using the table controls before navigating. Your current match was not stopped.'); return;} navigate(value as Page);}} onOpenDeck={id => {
      if (tableView || watchJob) {window.alert('Return to the lab before opening the deck editor. Your match was not stopped.'); return;}
      if (deckDraftDirty) {window.alert('Save or cancel your unsaved deck edits before opening another deck. The imported deck is safely saved in your library.'); return;}
      if (!navigate('decks')) return;
      window.localStorage.setItem('deepdeck.activeDeckId', id);
      setDeckRevisionKey(key => key + 1);
    }} onConnectLeague={() => setSettingsOpen(true)} onClose={() => setAssistantOpen(false)} onChanged={async (deckId) => { if (deckId || assistant === "deck") setDeckRevisionKey((key) => key + 1); await refreshLocalLibrary(); }} onStarted={openJob} />
    <SettingsDrawer open={settingsOpen} onClose={() => setSettingsOpen(false)} account={account} siteAccount={siteAccount} connection={connection} refresh={refresh} />
    {watchJob && <LiveMatchViewer key={watchJob.id} initialJob={watchJob} onClose={() => { setWatchJob(null); void refresh(); }} />}
  </>;
}
