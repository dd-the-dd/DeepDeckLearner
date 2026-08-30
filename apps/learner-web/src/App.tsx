import { FormEvent, useEffect, useMemo, useRef, useState } from "react";

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
} from "./api";
import { workflowBlockers, type Workflow } from "./readiness";

type Page =
  | "overview"
  | "train"
  | "playtest"
  | "compete"
  | "representation"
  | "models";

const pages: Array<{ id: Page; label: string; glyph: string }> = [
  { id: "overview", label: "Home", glyph: "⌂" },
  { id: "train", label: "Train", glyph: "↗" },
  { id: "playtest", label: "Playtest", glyph: "▶" },
  { id: "compete", label: "Matchmaking", glyph: "◎" },
  { id: "representation", label: "Representation", glyph: "◇" },
  { id: "models", label: "Models", glyph: "◎" },
];

const pageHeadings: Record<Page, string> = {
  overview: "What do you want to do?",
  train: "Train an agent",
  playtest: "Test an agent locally",
  compete: "Send an agent to the League",
  representation: "Understand the tensor",
  models: "Choose a model family",
};

const leagueUrl = "https://staging.deepdeckleague.com";
const leagueLogoUrl = `${leagueUrl}/deep-deck-league-logo.png`;
const patreonUrl = "https://www.patreon.com/DeepDeckLeague";

const workflowCopy: Record<
  Workflow,
  { title: string; kicker: string; description: string }
> = {
  "local-training": {
    title: "Train an agent",
    kicker: "Recommended first step",
    description:
      "Choose a V11 or V12 model, format, and training deck pool before launching a run.",
  },
  "online-training": {
    title: "Train online",
    kicker: "Hosted opponents",
    description:
      "Connect your account when the versioned hosted trajectory contract is available.",
  },
  "local-playtest": {
    title: "Test an agent locally",
    kicker: "I want to test behavior",
    description:
      "Prepare Engine and Pixi, choose two decks, then launch a local behavior test.",
  },
  matchmaking: {
    title: "Send an agent to the League",
    kicker: "I am ready to compete",
    description:
      "Connect your account key, find a legal deck by name, and join matchmaking.",
  },
};

function StatusDot({ ready, label }: { ready: boolean; label: string }) {
  return (
    <span className={`status-chip ${ready ? "ready" : "missing"}`}>
      <i />
      {label}
    </span>
  );
}

function PatreonMark() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M14.82 2.4a7.18 7.18 0 1 0 0 14.36 7.18 7.18 0 0 0 0-14.36ZM2.4 21.6h3.5V2.4H2.4v19.2Z" />
    </svg>
  );
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
    <button
      className={`workflow-card${workflow === "local-training" ? " recommended" : ""}`}
      type="button"
      onClick={() => onSelect(workflow)}
    >
      <span className="card-kicker">{copy.kicker}</span>
      <span className="card-title">
        {copy.title}
        <b aria-hidden="true">→</b>
      </span>
      <span className="card-copy">{copy.description}</span>
      <span className={`card-state ${blockers.length ? "blocked" : "ready"}`}>
        {blockers.length ? "Guided setup included" : "Ready to continue"}
      </span>
    </button>
  );
}

function WorkflowJourney({
  active,
  steps,
}: {
  active: number;
  steps: Array<{ label: string; detail: string }>;
}) {
  return (
    <ol className="workflow-journey" aria-label="Workflow progress">
      {steps.map((step, index) => {
        const number = index + 1;
        const state =
          number < active
            ? "complete"
            : number === active
              ? "active"
              : "upcoming";
        return (
          <li className={state} key={step.label}>
            <span>{number < active ? "✓" : number}</span>
            <div>
              <strong>{step.label}</strong>
              <small>{step.detail}</small>
            </div>
          </li>
        );
      })}
    </ol>
  );
}

function WorkspaceSummary({ status }: { status: CapabilityStatus | null }) {
  return (
    <section className="workspace-summary" aria-label="Workspace readiness">
      <div>
        <span className="eyebrow">Your workspace</span>
        <strong>
          {status?.torch.ready
            ? "You can train an agent now."
            : "One training dependency is missing."}
        </strong>
        <small>
          Engine and Pixi are only needed when you run a local behavior test.
        </small>
      </div>
      <div className="workspace-status">
        <StatusDot ready={Boolean(status?.torch.ready)} label="Training" />
        <StatusDot
          ready={Boolean(status?.engine.healthy && status?.pixi.built)}
          label="Local play"
        />
        <StatusDot
          ready={Boolean(status?.hosted.api_key_configured)}
          label="League account"
        />
      </div>
    </section>
  );
}

function LockedNextStep() {
  return (
    <section className="panel locked-step" aria-disabled="true">
      <span>Next</span>
      <div>
        <h2>Choose the matchup</h2>
        <p>
          This step opens automatically as soon as Engine and Pixi are ready.
        </p>
      </div>
    </section>
  );
}

function shortRevision(revision: string | null) {
  return revision ? revision.slice(0, 8) : "not installed";
}

function DependencyPanel({
  status,
  jobs,
  refresh,
}: {
  status: CapabilityStatus | null;
  jobs: Job[];
  refresh: () => void;
}) {
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const engine = status?.engine;
  const pixi = status?.pixi;
  const stackReady = Boolean(engine?.healthy && pixi?.built);
  const stackJob = jobs.find(
    (job) =>
      job.kind === "dependency.stack.prepare" &&
      ["queued", "running"].includes(job.status),
  );
  const stackFailure = jobs.find(
    (job) => job.kind === "dependency.stack.prepare" && job.status === "failed",
  );
  const dirty = Boolean(engine?.dirty || pixi?.dirty);

  async function run(action: string, payload: Record<string, unknown>) {
    setBusy(action);
    setError("");
    try {
      await startJob(payload);
      refresh();
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Unable to start dependency task.",
      );
    } finally {
      setBusy("");
    }
  }

  async function startStack() {
    setBusy("stack");
    setError("");
    try {
      await startJob({ kind: "dependency.stack.prepare" });
      refresh();
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Unable to start the local stack.",
      );
    } finally {
      setBusy("");
    }
  }

  return (
    <section className={`panel dependency-panel${stackReady ? " ready" : ""}`}>
      <div className="section-heading dependency-heading">
        <div>
          <span className="eyebrow">Step 1 · automatic setup</span>
          <h2>Prepare the local game table</h2>
          <p>
            One action retrieves the pinned Engine and Pixi sources, builds what
            is needed, and starts Engine.
          </p>
        </div>
        <button
          className="primary"
          type="button"
          disabled={
            !status || stackReady || Boolean(busy) || Boolean(stackJob) || dirty
          }
          onClick={() => void startStack()}
        >
          {stackReady
            ? "Local tools ready"
            : stackJob || busy === "stack"
              ? "Setting up…"
              : "Set up Engine + Pixi"}
        </button>
      </div>
      <ol className="setup-progress">
        <li
          className={
            engine?.synced && pixi?.synced
              ? "complete"
              : stackJob
                ? "active"
                : ""
          }
        >
          <span>{engine?.synced && pixi?.synced ? "✓" : "1"}</span>
          <div>
            <strong>Get compatible sources</strong>
            <small>Uses the reviewed commits pinned by DeepDeckLearner.</small>
          </div>
        </li>
        <li
          className={
            pixi?.built
              ? "complete"
              : stackJob && engine?.synced && pixi?.synced
                ? "active"
                : ""
          }
        >
          <span>{pixi?.built ? "✓" : "2"}</span>
          <div>
            <strong>Build the visual client</strong>
            <small>Installs locked packages and prepares DeepDeckPixi.</small>
          </div>
        </li>
        <li
          className={
            engine?.healthy
              ? "complete"
              : stackJob && pixi?.built
                ? "active"
                : ""
          }
        >
          <span>{engine?.healthy ? "✓" : "3"}</span>
          <div>
            <strong>Start the rules engine</strong>
            <small>
              Builds once when necessary, then listens only on this computer.
            </small>
          </div>
        </li>
      </ol>
      {dirty && (
        <p className="form-error" role="alert">
          Engine or Pixi contains local changes. Commit or move those changes
          before automatic setup; nothing will be overwritten.
        </p>
      )}
      {(error || stackFailure?.logs.at(-1)) && (
        <p className="form-error" role="alert">
          {error || stackFailure?.logs.at(-1)}
        </p>
      )}
      <details className="runtime-details">
        <summary>Technical details and individual controls</summary>
        <div className="dependency-list">
          <article>
            <div className="dependency-icon engine">E</div>
            <div className="dependency-copy">
              <span>DeepDeckEngine</span>
              <strong>
                {engine?.healthy
                  ? "Running"
                  : engine?.synced
                    ? engine.built
                      ? "Ready to start"
                      : "Build required"
                    : engine?.source_available
                      ? "Update available"
                      : "Not installed"}
              </strong>
              <small>
                Current {shortRevision(engine?.revision ?? null)} · compatible{" "}
                {shortRevision(engine?.pinned_revision ?? null)}
              </small>
              {engine?.dirty && (
                <em>Local changes prevent automatic synchronization.</em>
              )}
            </div>
            <div className="dependency-actions">
              <button
                type="button"
                disabled={
                  !engine?.source_available ||
                  !engine.synced ||
                  engine.healthy ||
                  Boolean(busy)
                }
                onClick={() =>
                  void run("engine", { kind: "dependency.engine.start" })
                }
              >
                {engine?.healthy
                  ? "Running"
                  : engine?.built
                    ? "Start"
                    : "Build & start"}
              </button>
              <button
                type="button"
                disabled={engine?.synced || engine?.dirty || Boolean(busy)}
                onClick={() =>
                  void run("engine-sync", {
                    kind: "dependency.sync",
                    dependency: "engine",
                  })
                }
              >
                {engine?.synced ? "Up to date" : "Sync version"}
              </button>
            </div>
          </article>
          <article>
            <div className="dependency-icon pixi">P</div>
            <div className="dependency-copy">
              <span>DeepDeckPixi</span>
              <strong>
                {pixi?.built
                  ? "Ready"
                  : pixi?.synced
                    ? "Build required"
                    : pixi?.source_available
                      ? "Update available"
                      : "Not installed"}
              </strong>
              <small>
                Current {shortRevision(pixi?.revision ?? null)} · compatible{" "}
                {shortRevision(pixi?.pinned_revision ?? null)}
              </small>
              {pixi?.dirty && (
                <em>Local changes prevent automatic synchronization.</em>
              )}
            </div>
            <div className="dependency-actions">
              <button
                type="button"
                disabled={
                  !pixi?.source_available ||
                  !pixi.synced ||
                  pixi.built ||
                  Boolean(busy)
                }
                onClick={() =>
                  void run("pixi", { kind: "dependency.pixi.prepare" })
                }
              >
                {pixi?.built ? "Ready" : "Prepare"}
              </button>
              <button
                type="button"
                disabled={pixi?.synced || pixi?.dirty || Boolean(busy)}
                onClick={() =>
                  void run("pixi-sync", {
                    kind: "dependency.sync",
                    dependency: "pixi",
                  })
                }
              >
                {pixi?.synced ? "Up to date" : "Sync version"}
              </button>
            </div>
          </article>
        </div>
        <p className="dependency-note">
          Individual controls are recovery tools. Normal setup should only
          require the main button above.
        </p>
      </details>
    </section>
  );
}

function TrainingForm({
  status,
  refresh,
}: {
  status: CapabilityStatus | null;
  refresh: () => void;
}) {
  const [source, setSource] = useState<"smoke" | "dataset">("smoke");
  const [model, setModel] = useState("v12");
  const [dataset, setDataset] = useState("");
  const [advanced, setAdvanced] = useState(false);
  const [epochs, setEpochs] = useState(3);
  const [learningRate, setLearningRate] = useState(0.0003);
  const [device, setDevice] = useState("cuda");
  const [error, setError] = useState("");
  const [format, setFormat] = useState("legacy");
  const [decks, setDecks] = useState<DeckSummary[]>([]);
  const [selectedDecks, setSelectedDecks] = useState<string[]>([]);
  const [deckError, setDeckError] = useState("");
  const [loadingDecks, setLoadingDecks] = useState(false);
  const blockers = workflowBlockers(status, "local-training");
  const deckTrainingAvailable = Boolean(status?.workflows.training_decks);

  useEffect(() => {
    if (!status?.hosted.api_key_configured) {
      setDecks([]);
      return;
    }
    let active = true;
    setLoadingDecks(true);
    setDeckError("");
    void searchDecks("", format)
      .then((items) => {
        if (!active) return;
        setDecks(items);
        setSelectedDecks((current) =>
          current.filter((id) => items.some((deck) => deck.id === id)),
        );
      })
      .catch((reason) => {
        if (!active) return;
        setDecks([]);
        setDeckError(
          reason instanceof Error
            ? reason.message
            : "Unable to load training decks from Deep Deck League.",
        );
      })
      .finally(() => {
        if (active) setLoadingDecks(false);
      });
    return () => {
      active = false;
    };
  }, [format, status?.hosted.api_key_configured]);

  function toggleDeck(deckVersionId: string) {
    setSelectedDecks((current) =>
      current.includes(deckVersionId)
        ? current.filter((id) => id !== deckVersionId)
        : [...current, deckVersionId],
    );
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    try {
      await startJob({
        kind: source === "smoke" ? "training.smoke" : "training.dataset",
        model,
        dataset,
        epochs,
        learning_rate: learningRate,
        device,
      });
      refresh();
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Unable to start training.",
      );
    }
  }

  return (
    <form className="panel configure" onSubmit={submit}>
      <div className="section-heading">
        <div>
          <span className="eyebrow">Steps 1–2 · configure the training run</span>
          <h2>Choose a model and its training decks</h2>
          <p className="section-lead">
            Select the actual pool the agent should learn from before starting.
            V12 is the recommended two-player model.
          </p>
        </div>
        <span className="step">01</span>
      </div>
      <div className="field-grid beginner-fields">
        <label>
          Model
          <select
            value={model}
            onChange={(event) => setModel(event.target.value)}
          >
            <option value="v12">V12 · two-player</option>
            <option value="v11">V11 · multiplayer</option>
          </select>
        </label>
        <label>
          Format
          <select
            value={format}
            onChange={(event) => {
              setFormat(event.target.value);
              setSelectedDecks([]);
            }}
          >
            <option value="legacy">Legacy</option>
            <option value="commander">Commander</option>
          </select>
        </label>
        <div className="automatic-setting">
          <span>Compute</span>
          <strong>GPU preferred</strong>
          <small>Automatically uses CPU when CUDA is unavailable.</small>
        </div>
      </div>
      <fieldset className="training-pool">
        <legend>Training pool</legend>
        <div className="training-pool-heading">
          <div>
            <strong>Select one or more decks</strong>
            <small>
              {selectedDecks.length
                ? `${selectedDecks.length} deck${selectedDecks.length === 1 ? "" : "s"} selected`
                : "No deck selected — training cannot start"}
            </small>
          </div>
        </div>
        {!status?.hosted.api_key_configured ? (
          <div className="notice warning" role="status">
            <strong>Connect your Deep Deck League account</strong>
            <span>
              Add your account API key in Application setup, then return here
              to choose decks from your authenticated training pool.
            </span>
          </div>
        ) : loadingDecks ? (
          <p className="deck-pool-empty" role="status">Loading legal decks…</p>
        ) : deckError ? (
          <p className="form-error" role="alert">{deckError}</p>
        ) : decks.length === 0 ? (
          <p className="deck-pool-empty">
            Your Deep Deck League account has no available {format} deck.
          </p>
        ) : (
          <div className="training-deck-grid" aria-label="Training decks">
            {decks.map((deck) => {
              const selected = selectedDecks.includes(deck.id);
              return (
                <button
                  key={deck.id}
                  type="button"
                  aria-pressed={selected}
                  className={selected ? "selected" : ""}
                  onClick={() => toggleDeck(deck.id)}
                >
                  <span aria-hidden="true">{selected ? "✓" : "+"}</span>
                  <strong>{deck.name}</strong>
                  <small>v{deck.version} · {deck.playableCardCount} cards</small>
                </button>
              );
            })}
          </div>
        )}
      </fieldset>
      <div className="notice warning" role="status">
        <strong>Deck training is not available yet</strong>
        <span>
          Your pool can be configured here, but Engine does not publish the
          trajectory-v1 collector yet. DeepDeckLearner will not replace your
          chosen decks with sample data.
        </span>
      </div>
      <div className="form-actions">
        <button
          className="primary"
          type="button"
          disabled={
            blockers.length > 0 ||
            selectedDecks.length === 0 ||
            !deckTrainingAvailable
          }
        >
          Train {model.toUpperCase()} on selected decks <span>→</span>
        </button>
        <small>
          Select decks first. This action unlocks only when the real trajectory
          collector is available.
        </small>
      </div>
      <button
        className="advanced-toggle"
        type="button"
        aria-expanded={advanced}
        onClick={() => setAdvanced(!advanced)}
      >
        <span>{advanced ? "−" : "+"}</span> Advanced trainer validation
      </button>
      {advanced && (
        <div className="advanced-fields trainer-validation">
          <p className="advanced-explanation">
            This separately validates the encoder, optimizer and checkpoint
            pipeline. It uses sample or existing JSONL data—not the decks
            selected above.
          </p>
          <label>
            Training input
            <select
              value={source}
              onChange={(event) => {
                const next = event.target.value as "smoke" | "dataset";
                setSource(next);
                if (next === "dataset" && !dataset)
                  setDataset(
                    status?.paths.trajectory ??
                      ".deepdeck/trajectories/decisions.jsonl",
                  );
              }}
            >
              <option value="smoke">Built-in smoke trajectory</option>
              <option value="dataset">Trajectory JSONL file</option>
            </select>
          </label>
          {source === "dataset" && (
            <label className="wide-field">
              Trajectory file
              <input
                value={dataset}
                onChange={(event) => setDataset(event.target.value)}
                placeholder={
                  status?.paths.trajectory ??
                  ".deepdeck/trajectories/decisions.jsonl"
                }
                required
              />
              <small>
                Created automatically inside this project when the workbench
                starts.
              </small>
            </label>
          )}
          <label>
            Epochs
            <input
              type="number"
              min="1"
              max="1000"
              value={epochs}
              onChange={(event) => setEpochs(Number(event.target.value))}
            />
          </label>
          <label>
            Learning rate
            <input
              type="number"
              min="0.00000001"
              max="1"
              step="0.0001"
              value={learningRate}
              onChange={(event) => setLearningRate(Number(event.target.value))}
            />
          </label>
          <label>
            Device
            <select
              value={device}
              onChange={(event) => setDevice(event.target.value)}
            >
              <option value="cuda">CUDA · fallback to CPU</option>
              <option value="cpu">CPU</option>
            </select>
          </label>
        </div>
      )}
      {blockers.length > 0 && (
        <div className="notice warning">
          <strong>Before you start</strong>
          {blockers.map((item) => (
            <span key={item}>{item}</span>
          ))}
        </div>
      )}
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      {advanced && (
        <div className="form-actions validation-actions">
          <button className="secondary" disabled={blockers.length > 0}>
            Validate {model.toUpperCase()} trainer
          </button>
          <small>This does not train on the selected deck pool.</small>
        </div>
      )}
    </form>
  );
}

function OnlinePanel({ status }: { status: CapabilityStatus | null }) {
  const blockers = workflowBlockers(status, "online-training");
  return (
    <section className="panel configure">
      <div className="section-heading">
        <div>
          <span className="eyebrow">Hosted training</span>
          <h2>Connect without pretending</h2>
        </div>
        <span className="step">02</span>
      </div>
      <p className="lead">
        Online inference already uses your account API key. Weight updates need
        a complete, versioned observation/action/reward trajectory; a replay
        alone is not declared sufficient yet.
      </p>
      <div className="notice warning">
        <strong>Not ready to launch</strong>
        {blockers.map((item) => (
          <span key={item}>{item}</span>
        ))}
      </div>
      <button className="primary" disabled>
        Start online training
      </button>
    </section>
  );
}

function PlaytestForm({
  status,
  refresh,
}: {
  status: CapabilityStatus | null;
  refresh: () => void;
}) {
  const [agent, setAgent] = useState("random");
  const [format, setFormat] = useState("legacy");
  const [ownDeck, setOwnDeck] = useState("");
  const [opponentDeck, setOpponentDeck] = useState("");
  const [deckSearch, setDeckSearch] = useState("");
  const [decks, setDecks] = useState<LocalDeck[]>([]);
  const [catalogError, setCatalogError] = useState("");
  const [error, setError] = useState("");
  const blockers = workflowBlockers(status, "local-playtest");
  useEffect(() => {
    if (!status?.engine.healthy) return;
    let active = true;
    void loadLocalDecks(format, status.engine.url)
      .then((items) => {
        if (!active) return;
        setDecks(items);
        setOwnDeck(items[0]?.deckSessionId ?? "");
        setOpponentDeck(
          items[1]?.deckSessionId ?? items[0]?.deckSessionId ?? "",
        );
        setCatalogError("");
      })
      .catch((reason) => {
        if (active)
          setCatalogError(
            reason instanceof Error
              ? reason.message
              : "Unable to load local decks.",
          );
      });
    return () => {
      active = false;
    };
  }, [format, status?.engine.healthy, status?.engine.url]);
  const visibleDecks = decks.filter((deck) =>
    deck.deckName.toLowerCase().includes(deckSearch.toLowerCase()),
  );
  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    try {
      await startJob({
        kind: "playtest.agent",
        agent,
        format,
        deck_session_id: ownDeck,
        opponent_deck_session_id: opponentDeck,
        engine_url: status?.engine.url,
      });
      refresh();
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Unable to launch playtest.",
      );
    }
  }
  return (
    <form className="panel configure" onSubmit={submit}>
      <div className="section-heading">
        <div>
          <span className="eyebrow">Step 2 · choose the matchup</span>
          <h2>Play against your agent</h2>
        </div>
        <span className="step">02</span>
      </div>
      <div className="field-grid">
        <label>
          Agent
          <select
            value={agent}
            onChange={(event) => setAgent(event.target.value)}
          >
            <option value="random">Random baseline</option>
            <option value="alexios">Alexios rules</option>
            <option value="v12">V12 example</option>
            <option value="v11">V11 example</option>
          </select>
        </label>
        <label>
          Format
          <select
            value={format}
            onChange={(event) => setFormat(event.target.value)}
          >
            <option value="legacy">Legacy</option>
            <option value="commander">Commander</option>
          </select>
        </label>
      </div>
      <label>
        Search available local decks
        <input
          type="search"
          value={deckSearch}
          onChange={(event) => setDeckSearch(event.target.value)}
          placeholder="Reanimator, Alexios, Atraxa…"
        />
      </label>
      <div className="field-grid">
        <label>
          Your deck
          <select
            required
            value={ownDeck}
            onChange={(event) => setOwnDeck(event.target.value)}
          >
            <option value="">Choose a deck</option>
            {visibleDecks.map((deck) => (
              <option key={deck.deckSessionId} value={deck.deckSessionId}>
                {deck.deckName}
              </option>
            ))}
          </select>
        </label>
        <label>
          Opponent deck
          <select
            required
            value={opponentDeck}
            onChange={(event) => setOpponentDeck(event.target.value)}
          >
            <option value="">Choose a deck</option>
            {visibleDecks.map((deck) => (
              <option key={deck.deckSessionId} value={deck.deckSessionId}>
                {deck.deckName}
              </option>
            ))}
          </select>
        </label>
      </div>
      {catalogError && (
        <p className="form-error" role="alert">
          {catalogError}
        </p>
      )}
      {blockers.length > 0 && (
        <div className="notice warning">
          <strong>Before you start</strong>
          {blockers.map((item) => (
            <span key={item}>{item}</span>
          ))}
        </div>
      )}
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      <div className="notice">
        <strong>What this launches today</strong>
        <span>
          The agent and its opponent run in the local Rust session. Pixi is
          prepared; embedding its visual session host is the next integration.
        </span>
      </div>
      <button
        className="primary"
        disabled={blockers.length > 0 || !ownDeck || !opponentDeck}
      >
        Launch behavior test <span>▶</span>
      </button>
    </form>
  );
}

function MatchmakingForm({
  status,
  jobs,
  refresh,
}: {
  status: CapabilityStatus | null;
  jobs: Job[];
  refresh: () => void;
}) {
  const [agent, setAgent] = useState("random");
  const [format, setFormat] = useState("legacy");
  const [query, setQuery] = useState("");
  const [decks, setDecks] = useState<DeckSummary[]>([]);
  const [selectedDeck, setSelectedDeck] = useState<DeckSummary | null>(null);
  const [competitions, setCompetitions] = useState<CompetitionSummary[]>([]);
  const [speed, setSpeed] = useState("1s");
  const [continuous, setContinuous] = useState(false);
  const [checkpoint, setCheckpoint] = useState("");
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState("");
  const blockers = workflowBlockers(status, "matchmaking");
  const competition = competitions.find(
    (item) => item.format.toLowerCase() === format,
  );
  const checkpoints = jobs.filter(
    (job) => job.status === "completed" && job.artifact_path,
  );
  const needsCheckpoint = agent === "v11" || agent === "v12";

  useEffect(() => {
    if (!status?.hosted.api_key_configured) {
      setCompetitions([]);
      return;
    }
    void loadCompetitions()
      .then(setCompetitions)
      .catch((reason) => {
        setError(
          reason instanceof Error
            ? reason.message
            : "Unable to load active competitions.",
        );
      });
  }, [status?.hosted.api_key_configured]);

  async function findDecks(event: FormEvent) {
    event.preventDefault();
    if (!status?.hosted.api_key_configured) {
      setError(
        "Add your account API key and restart DeepDeckLearner before searching decks.",
      );
      return;
    }
    setSearching(true);
    setError("");
    setSelectedDeck(null);
    try {
      setDecks(await searchDecks(query, format));
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Unable to search decks.",
      );
    } finally {
      setSearching(false);
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    if (!selectedDeck || !competition) return;
    try {
      await startJob({
        kind: "matchmaking.agent",
        agent,
        speed,
        continuous,
        checkpoint: needsCheckpoint ? checkpoint : undefined,
        competition_version_id: competition.versionId,
        deck_version_id: selectedDeck.id,
      });
      refresh();
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Unable to join matchmaking.",
      );
    }
  }

  return (
    <section className="panel configure matchmaking-setup">
      <div className="section-heading">
        <div>
          <span className="eyebrow">Guided League setup</span>
          <h2>Connect, choose a deck, and queue</h2>
        </div>
        <span className="step">01—03</span>
      </div>
      <ol className="setup-steps">
        <li className={status?.hosted.api_key_configured ? "complete" : ""}>
          <span>1</span>
          <div>
            <strong>Create your account key</strong>
            <p>
              Sign in, open Account → Autonomous agents, and press Generate API
              key.
            </p>
            <a
              href="https://staging.deepdeckleague.com/account#autonomous-agents"
              target="_blank"
              rel="noreferrer"
            >
              Open the exact account section ↗
            </a>
          </div>
        </li>
        <li className={status?.hosted.api_key_configured ? "complete" : ""}>
          <span>2</span>
          <div>
            <strong>
              Add the copied line to <code>.env</code>
            </strong>
            <p>
              Save <code>DEEPDECK_API_KEY=ddl_agent_…</code> in the
              DeepDeckLearner project root, then restart the workbench. The
              browser never receives the secret.
            </p>
          </div>
        </li>
        <li>
          <span>3</span>
          <div>
            <strong>Find your deck by name</strong>
            <p>
              DeepDeckLearner keeps the version identifier underneath this
              search.
            </p>
          </div>
        </li>
      </ol>
      <form className="deck-finder" onSubmit={findDecks}>
        <label>
          Format
          <select
            value={format}
            onChange={(event) => {
              setFormat(event.target.value);
              setDecks([]);
              setSelectedDeck(null);
            }}
          >
            <option value="legacy">Legacy</option>
            <option value="commander">Commander</option>
          </select>
        </label>
        <label>
          Deck name or creator
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Reanimator, Alexios, Andrea…"
          />
        </label>
        <button
          className="primary"
          type="submit"
          disabled={searching || !status?.hosted.api_key_configured}
        >
          {searching ? "Searching…" : "Search decks"}
        </button>
      </form>
      {decks.length > 0 && (
        <div
          className="deck-search-results"
          role="listbox"
          aria-label="Deck search results"
        >
          {decks.map((deck) => (
            <button
              type="button"
              role="option"
              aria-selected={selectedDeck?.id === deck.id}
              className={selectedDeck?.id === deck.id ? "selected" : ""}
              key={deck.id}
              onClick={() => setSelectedDeck(deck)}
            >
              <strong>{deck.name}</strong>
              <span>
                {deck.creator ? `by ${deck.creator}` : "Community deck"} ·{" "}
                {deck.format ?? format} · v{deck.version}
              </span>
              <small>{deck.playableCardCount} playable cards</small>
            </button>
          ))}
        </div>
      )}
      <form onSubmit={submit}>
        <div className="field-grid">
          <label>
            Agent
            <select
              value={agent}
              onChange={(event) => setAgent(event.target.value)}
            >
              <option value="random">Random baseline</option>
              <option value="alexios">Alexios rules</option>
              <option value="v12">V12 example</option>
              <option value="v11">V11 example</option>
            </select>
          </label>
          <label>
            Decision pace
            <select
              value={speed}
              onChange={(event) => setSpeed(event.target.value)}
            >
              <option value="100ms">100 ms</option>
              <option value="1s">1 second</option>
              <option value="10s">10 seconds</option>
            </select>
          </label>
        </div>
        {needsCheckpoint && (
          <label>
            Trained checkpoint
            <select
              required
              value={checkpoint}
              onChange={(event) => setCheckpoint(event.target.value)}
            >
              <option value="">Choose a completed training run</option>
              {checkpoints.map((job) => (
                <option key={job.id} value={job.artifact_path ?? ""}>
                  {job.label} · {job.artifact_path}
                </option>
              ))}
            </select>
          </label>
        )}
        <label className="checkbox-label">
          <input
            type="checkbox"
            checked={continuous}
            onChange={(event) => setContinuous(event.target.checked)}
          />
          Keep rejoining after each match
        </label>
        {selectedDeck && (
          <div className="selected-deck">
            <span>Selected deck</span>
            <strong>{selectedDeck.name}</strong>
            <small>
              {competition
                ? `${competition.name} · ${competition.timeControl}`
                : `No active ${format} competition`}
            </small>
          </div>
        )}
        {blockers.length > 0 && (
          <div className="notice warning">
            <strong>Before you queue</strong>
            {blockers.map((item) => (
              <span key={item}>{item}</span>
            ))}
          </div>
        )}
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
        <button
          className="primary"
          disabled={
            blockers.length > 0 ||
            !selectedDeck ||
            !competition ||
            (needsCheckpoint && !checkpoint)
          }
        >
          Join matchmaking <span>→</span>
        </button>
      </form>
    </section>
  );
}

function JobsPanel({ jobs, refresh }: { jobs: Job[]; refresh: () => void }) {
  return (
    <section className="panel jobs">
      <div className="section-heading">
        <div>
          <span className="eyebrow">Controller-owned</span>
          <h2>Recent jobs</h2>
        </div>
        <button className="text-button" type="button" onClick={refresh}>
          Refresh
        </button>
      </div>
      {jobs.length === 0 ? (
        <div className="empty">
          <span>◇</span>
          <p>No jobs yet. A V12 smoke run is a safe first step.</p>
        </div>
      ) : (
        <div className="job-list">
          {jobs.map((job) => (
            <article className="job" key={job.id}>
              <div>
                <StatusDot
                  ready={job.status === "completed" || job.status === "running"}
                  label={job.status}
                />
                <h3>{job.label}</h3>
                <p>{job.logs.at(-1) ?? job.argv.join(" ")}</p>
                {job.artifact_path && <code>{job.artifact_path}</code>}
              </div>
              {job.status === "running" && (
                <button
                  className="danger"
                  type="button"
                  onClick={() => void stopJob(job.id).then(refresh)}
                >
                  Stop
                </button>
              )}
            </article>
          ))}
        </div>
      )}
    </section>
  );
}

export default function App() {
  const [page, setPage] = useState<Page>("overview");
  const [workflow, setWorkflow] = useState<Workflow>("local-training");
  const [status, setStatus] = useState<CapabilityStatus | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loadError, setLoadError] = useState("");
  const pageHeading = useRef<HTMLHeadingElement>(null);

  async function refresh() {
    try {
      const [nextStatus, nextJobs] = await Promise.all([
        loadStatus(),
        loadJobs(),
      ]);
      setStatus(nextStatus);
      setJobs(nextJobs);
      setLoadError("");
    } catch (reason) {
      setLoadError(
        reason instanceof Error ? reason.message : "Controller unavailable.",
      );
    }
  }
  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 2500);
    return () => window.clearInterval(timer);
  }, []);
  useEffect(() => {
    pageHeading.current?.focus();
  }, [page]);
  const activityJobs = useMemo(
    () => jobs.filter((job) => !job.kind.startsWith("dependency.")),
    [jobs],
  );
  const running = useMemo(
    () => activityJobs.filter((job) => job.status === "running").length,
    [activityJobs],
  );
  const stackReady = Boolean(status?.engine.healthy && status?.pixi.built);
  const accountReady = Boolean(status?.hosted.api_key_configured);
  const trainingStep = jobs.some(
    (job) => job.kind.startsWith("training.") && job.status === "completed",
  )
    ? 3
    : jobs.some(
          (job) =>
            job.kind.startsWith("training.") &&
            ["queued", "running"].includes(job.status),
        )
      ? 2
      : 1;
  const playtestStep = jobs.some((job) => job.kind === "playtest.agent")
    ? 3
    : stackReady
      ? 2
      : 1;
  const leagueStep = jobs.some((job) => job.kind === "matchmaking.agent")
    ? 3
    : accountReady
      ? 2
      : 1;
  function selectWorkflow(next: Workflow) {
    setWorkflow(next);
    setPage(
      next === "local-playtest"
        ? "playtest"
        : next === "matchmaking"
          ? "compete"
          : "train",
    );
  }

  return (
    <div className="app-shell">
      <aside>
        <a
          className="brand"
          href={leagueUrl}
          target="_blank"
          rel="noreferrer"
          aria-label="Open Deep Deck League"
        >
          <img src={leagueLogoUrl} alt="Deep Deck League" />
          <span>Learner</span>
        </a>
        <span className="local-badge">● Local workbench</span>
        <nav aria-label="Main navigation">
          {pages.map((item) => (
            <button
              className={page === item.id ? "active" : ""}
              type="button"
              key={item.id}
              onClick={() => setPage(item.id)}
            >
              <span>{item.glyph}</span>
              {item.label}
            </button>
          ))}
        </nav>
        <div className="aside-foot">
          <small>Public AI laboratory</small>
          <a
            className="patreon-link"
            href={patreonUrl}
            target="_blank"
            rel="noreferrer"
          >
            <PatreonMark />
            <span>Support on Patreon</span>
            <b aria-hidden="true">↗</b>
          </a>
          <a
            className="source-link"
            href="https://github.com/dd-the-dd/DeepDeckLearner"
            target="_blank"
            rel="noreferrer"
          >
            View source <span aria-hidden="true">↗</span>
          </a>
        </div>
      </aside>
      <main>
        <header>
          <div>
            <span className="eyebrow">Deep Deck AI laboratory</span>
            <h1 ref={pageHeading} tabIndex={-1}>
              {pageHeadings[page]}
            </h1>
          </div>
          <div className="health">
            {page === "playtest" ? (
              <>
                <StatusDot
                  ready={Boolean(status?.engine.healthy)}
                  label="Engine"
                />
                <StatusDot ready={Boolean(status?.pixi.built)} label="Pixi" />
              </>
            ) : page === "compete" ? (
              <StatusDot ready={accountReady} label="League account" />
            ) : (
              <StatusDot
                ready={Boolean(status?.controller.ready)}
                label="Workbench"
              />
            )}
            {running > 0 && (
              <span className="running-count">{running} running</span>
            )}
          </div>
        </header>
        {loadError && (
          <p className="controller-error" role="alert">
            Local controller: {loadError}
          </p>
        )}
        {page === "overview" && (
          <>
            <section className="intro">
              <p>
                Choose your outcome. DeepDeckLearner will show only the setup
                and decisions needed to reach it.
              </p>
              <div className="first-run-callout">
                <span>New here?</span>
                <strong>Start with Train an agent.</strong>
                <p>
                  Choose the model and decks it should learn before starting a
                  real training run.
                </p>
              </div>
              <div className="workflow-grid">
                {(
                  [
                    "local-training",
                    "local-playtest",
                    "matchmaking",
                  ] as Workflow[]
                ).map((item) => (
                  <WorkflowCard
                    key={item}
                    workflow={item}
                    status={status}
                    onSelect={selectWorkflow}
                  />
                ))}
              </div>
            </section>
            <WorkspaceSummary status={status} />
            {activityJobs.length > 0 && (
              <JobsPanel jobs={activityJobs} refresh={refresh} />
            )}
          </>
        )}
        {page === "train" && (
          <>
            <WorkflowJourney
              active={trainingStep}
              steps={[
                { label: "Configure", detail: "Model, format, and decks" },
                { label: "Train", detail: "Collect trajectories and learn" },
                { label: "Test", detail: "Watch its decisions" },
              ]}
            />
            {workflow === "online-training" ? (
              <OnlinePanel status={status} />
            ) : (
              <TrainingForm status={status} refresh={refresh} />
            )}
            <div className="segmented">
              <button
                className={workflow === "local-training" ? "selected" : ""}
                onClick={() => setWorkflow("local-training")}
              >
                Local
              </button>
              <button
                className={workflow === "online-training" ? "selected" : ""}
                onClick={() => setWorkflow("online-training")}
              >
                Hosted (later)
              </button>
            </div>
            {activityJobs.length > 0 && (
              <JobsPanel jobs={activityJobs} refresh={refresh} />
            )}
          </>
        )}
        {page === "playtest" && (
          <>
            <WorkflowJourney
              active={playtestStep}
              steps={[
                { label: "Prepare", detail: "Engine + Pixi" },
                { label: "Choose", detail: "Agent and decks" },
                { label: "Run", detail: "Inspect agent activity" },
              ]}
            />
            <DependencyPanel status={status} jobs={jobs} refresh={refresh} />
            {stackReady ? (
              <PlaytestForm status={status} refresh={refresh} />
            ) : (
              <LockedNextStep />
            )}
            {activityJobs.length > 0 && (
              <JobsPanel jobs={activityJobs} refresh={refresh} />
            )}
          </>
        )}
        {page === "compete" && (
          <>
            <WorkflowJourney
              active={leagueStep}
              steps={[
                { label: "Connect", detail: "Add your account key" },
                { label: "Choose", detail: "Agent and legal deck" },
                { label: "Queue", detail: "Join matchmaking" },
              ]}
            />
            <MatchmakingForm
              status={status}
              jobs={activityJobs}
              refresh={refresh}
            />
            {activityJobs.length > 0 && (
              <JobsPanel jobs={activityJobs} refresh={refresh} />
            )}
          </>
        )}
        {page === "representation" && (
          <section className="panel prose">
            <span className="eyebrow">Magic → tensor</span>
            <h2>A decision, not a screenshot</h2>
            <p>
              The encoder combines the observable game state, each legal action,
              known deck context, and a previous-state delta. V11 keeps four
              multiplayer value slots; V12 specializes the value head for
              two-player Legacy.
            </p>
            <div className="tensor-flow">
              <span>Game observation</span>
              <b>+</b>
              <span>Legal action</span>
              <b>+</b>
              <span>Known deck</span>
              <b>→</b>
              <span>Feature tensor</span>
            </div>
            <p>
              Feature indices and masks are versioned in{" "}
              <code>deepdeck_examples.deep_learning.encoding</code>. See the ML
              guide before changing them: a checkpoint is only compatible with
              the schema it learned.
            </p>
          </section>
        )}
        {page === "models" && (
          <section className="model-grid">
            <article className="panel model">
              <span>V12</span>
              <h2>Two-player policy</h2>
              <p>
                Legacy-oriented example with two value slots. Weights are
                intentionally not public.
              </p>
            </article>
            <article className="panel model">
              <span>V11</span>
              <h2>Multiplayer policy</h2>
              <p>
                Commander-oriented example with four value slots and the same
                public encoder family.
              </p>
            </article>
            <article className="panel model">
              <span>Baseline</span>
              <h2>Readable behavior</h2>
              <p>
                Random and Alexios rule-based agents make protocol and behavior
                testing approachable.
              </p>
            </article>
          </section>
        )}
      </main>
    </div>
  );
}
