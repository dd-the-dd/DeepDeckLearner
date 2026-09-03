import {
  Component,
  FormEvent,
  Suspense,
  lazy,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import {
  createModel,
  deleteModel,
  updateModel,
  loadAccountStatus,
  loadCompetitions,
  loadActiveGames,
  loadDeckStatistics,
  loadJobs,
  loadAllFormatDecks,
  loadModelResources,
  loadModels,
  loadResources,
  loadStatus,
  loadTrainingStatistics,
  downloadDeck,
  loadTrainingDeckPool,
  saveApiKey,
  saveModelResources,
  saveTrainingDeckPool,
  searchDecks,
  startJob,
  stopGame,
  stopJob,
  type CapabilityStatus,
  type AccountStatus,
  type ActiveGame,
  type CompetitionSummary,
  type DeckSummary,
  type DeckStatistic,
  type Job,
  type LocalModel,
  type ResourcePlan,
  type ResourceSnapshot,
  type TrainingStatistic,
} from "./api";
import { workflowBlockers, type Workflow } from "./readiness";

const LocalPixiTable = lazy(() => import("./LocalPixiTable"));

class PixiErrorBoundary extends Component<
  { children: ReactNode; onClose: () => void },
  { error: string }
> {
  state = { error: "" };

  static getDerivedStateFromError(reason: unknown) {
    return {
      error: reason instanceof Error ? reason.message : "Pixi could not open this table.",
    };
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <section className="pixi-recovery" role="alert">
        <span className="eyebrow">Local table recovery</span>
        <h2>Pixi could not display this game</h2>
        <p>{this.state.error}</p>
        <button className="primary" type="button" onClick={this.props.onClose}>
          Return to the workbench
        </button>
      </section>
    );
  }
}

type Page =
  | "overview"
  | "train"
  | "jobs"
  | "playtest"
  | "compete"
  | "statistics"
  | "representation"
  | "models";

const pages: Array<{ id: Page; label: string; glyph: string }> = [
  { id: "train", label: "Agent configuration", glyph: "01" },
  { id: "jobs", label: "Jobs", glyph: "02" },
  { id: "playtest", label: "Play against your AI", glyph: "03" },
  { id: "statistics", label: "Training statistics", glyph: "04" },
];

const pageHeadings: Record<Page, string> = {
  overview: "What do you want to do?",
  train: "Agent configuration",
  jobs: "Jobs and live games",
  playtest: "Play against your AI",
  compete: "Play in the League",
  statistics: "Training statistics",
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

function WorkspaceSummary({ status, account }: { status: CapabilityStatus | null; account: AccountStatus | null }) {
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
          ready={account?.valid === true}
          label="League account"
        />
      </div>
    </section>
  );
}

function AccountSetup({ status, account, refresh }: { status: CapabilityStatus | null; account: AccountStatus | null; refresh: () => void }) {
  const [apiKey, setApiKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [replacing, setReplacing] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setMessage("");
    try {
      await saveApiKey(apiKey);
      setApiKey("");
      setReplacing(false);
      setMessage("API key saved on this computer.");
      refresh();
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "Unable to save the API key.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel account-setup">
      <div>
        <span className="eyebrow">Application setup</span>
        <h2>Connect your League account</h2>
        <p>The key stays on this computer and is never displayed again after saving.</p>
      </div>
      {!status || !account ? (
        <div className="notice" role="status"><strong>Checking this computer…</strong><span>Looking for a previously saved League account key.</span></div>
      ) : account.valid === true && !replacing ? (
        <div className="notice success account-connected" role="status">
          <div><strong>Account connected</strong><span>Saved in this workbench's private local data folder and restored after restart.</span></div>
          <button type="button" onClick={() => setReplacing(true)}>Replace key</button>
        </div>
      ) : (
        <form onSubmit={submit}>
          {account.configured && <div className="notice warning"><strong>{account.valid === false ? "Saved key rejected" : "League unavailable"}</strong><span>{account.reason}</span></div>}
          <label>
            Deep Deck League API key
            <input type="password" autoComplete="off" value={apiKey} onChange={(event) => setApiKey(event.target.value)} placeholder="ddl_agent_…" />
          </label>
          <button className="primary" type="submit" disabled={busy || apiKey.trim().length < 24}>{busy ? "Saving…" : "Save API key"}</button>
          {account.valid === true && <button type="button" onClick={() => { setReplacing(false); setApiKey(""); }}>Cancel</button>}
        </form>
      )}
      {message && <p className={message.startsWith("API key saved") ? "form-success" : "form-error"} role="status">{message}</p>}
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

const dependencyKinds = new Set([
  "dependency.stack.prepare",
  "dependency.engine.start",
  "dependency.pixi.prepare",
  "dependency.sync",
]);

function dependencyActivity(
  kind: string,
  status: CapabilityStatus | null,
  job?: Job,
) {
  const engine = status?.engine;
  const pixi = status?.pixi;
  if (kind === "dependency.stack.prepare") {
    if (!engine?.synced || !pixi?.synced) {
      return {
        title: "Syncing compatible Engine and Pixi sources",
        detail: "Retrieving the reviewed revisions selected for this Learner release.",
        step: 1,
      };
    }
    if (!pixi.built) {
      return {
        title: "Building the Pixi visual client",
        detail: "Installing locked packages and preparing the local game table.",
        step: 2,
      };
    }
    if (!engine?.built) {
      return {
        title: "Compiling DeepDeckEngine",
        detail: "Rust is building the local rules server. The first build can take a few minutes.",
        step: 3,
      };
    }
    return {
      title: engine.healthy ? "Verifying the local game stack" : "Starting DeepDeckEngine",
      detail: engine.healthy
        ? "Checking that Engine and Pixi are ready for local games."
        : "Starting the freshly built rules server on this computer.",
      step: 3,
    };
  }
  if (kind === "dependency.engine.start") {
    return {
      title: engine?.built ? "Starting DeepDeckEngine" : "Building DeepDeckEngine",
      detail: engine?.built
        ? "Starting the local rules server and checking its health."
        : "Compiling the Rust rules server before starting it locally.",
      step: 3,
    };
  }
  if (kind === "dependency.pixi.prepare") {
    return {
      title: "Building the Pixi visual client",
      detail: "Preparing the full-screen local table and its browser assets.",
      step: 2,
    };
  }
  const syncingPixi = kind === "dependency.sync.pixi" || job?.label.toLowerCase().includes("pixi");
  return {
    title: syncingPixi ? "Syncing DeepDeckPixi" : "Syncing DeepDeckEngine",
    detail: "Switching to the compatible reviewed revision without overwriting local work.",
    step: 1,
  };
}

function DependencyLoader({
  kind,
  status,
  job,
}: {
  kind: string;
  status: CapabilityStatus | null;
  job?: Job;
}) {
  const activity = dependencyActivity(kind, status, job);
  const latestLog = job?.logs.filter((line) => line.trim()).at(-1);
  return (
    <aside
      className="operation-loader"
      role="status"
      aria-label={activity.title}
      aria-live="polite"
    >
      <div className="operation-loader-visual" aria-hidden="true">
        <i />
        <i />
        <span>DD</span>
      </div>
      <div className="operation-loader-copy">
        <span className="eyebrow">Working locally · safe to leave this tab open</span>
        <strong>{activity.title}</strong>
        <p>{activity.detail}</p>
        <div className="operation-loader-track" aria-hidden="true"><i /></div>
        <small>{latestLog || (job?.status === "queued" ? "Waiting for the local worker…" : "Preparing the next step…")}</small>
      </div>
      <ol className="operation-loader-stages" aria-label="Setup progress">
        {["Sources", "Pixi", "Engine"].map((label, index) => {
          const number = index + 1;
          return (
            <li
              className={number < activity.step ? "complete" : number === activity.step ? "active" : ""}
              key={label}
            >
              <span>{number < activity.step ? "✓" : number}</span>
              {label}
            </li>
          );
        })}
      </ol>
    </aside>
  );
}

function DependencyPanel({
  status,
  jobs,
  refresh,
}: {
  status: CapabilityStatus | null;
  jobs: Job[];
  refresh: () => void | Promise<void>;
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
  const activeDependencyJob = jobs.find(
    (job) => dependencyKinds.has(job.kind) && ["queued", "running"].includes(job.status),
  );
  const busyKind = {
    stack: "dependency.stack.prepare",
    engine: "dependency.engine.start",
    pixi: "dependency.pixi.prepare",
    "engine-sync": "dependency.sync.engine",
    "pixi-sync": "dependency.sync.pixi",
  }[busy];
  const activeKind = activeDependencyJob?.kind ?? busyKind;
  const dirty = Boolean(engine?.dirty || pixi?.dirty);

  async function run(action: string, payload: Record<string, unknown>) {
    setBusy(action);
    setError("");
    try {
      await startJob(payload);
      await refresh();
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
      await refresh();
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
          disabled={!status || Boolean(busy) || Boolean(activeDependencyJob) || dirty}
          onClick={() => void startStack()}
        >
          {stackReady
            ? stackJob || busy === "stack"
              ? "Verifying…"
              : "Verify & repair Engine + Pixi"
            : stackJob || busy === "stack"
              ? "Setting up…"
              : "Set up Engine + Pixi"}
        </button>
      </div>
      {activeKind && (
        <DependencyLoader
          kind={activeKind}
          status={status}
          job={activeDependencyJob}
        />
      )}
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
                  Boolean(busy) ||
                  Boolean(activeDependencyJob)
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
                disabled={
                  engine?.synced ||
                  engine?.dirty ||
                  Boolean(busy) ||
                  Boolean(activeDependencyJob)
                }
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
                  Boolean(busy) ||
                  Boolean(activeDependencyJob)
                }
                onClick={() =>
                  void run("pixi", { kind: "dependency.pixi.prepare" })
                }
              >
                {pixi?.built ? "Ready" : "Prepare"}
              </button>
              <button
                type="button"
                disabled={
                  pixi?.synced ||
                  pixi?.dirty ||
                  Boolean(busy) ||
                  Boolean(activeDependencyJob)
                }
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

export function TrainingForm({
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
  const [formats, setFormats] = useState<string[]>(["legacy"]);
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
    void Promise.all(formats.map((format) => searchDecks("", format)))
      .then((groups) => {
        if (!active) return;
        const items = groups.flat();
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
  }, [formats, status?.hosted.api_key_configured]);

  function toggleFormat(format: string) {
    setFormats((current) =>
      current.includes(format)
        ? current.length === 1
          ? current
          : current.filter((item) => item !== format)
        : [...current, format],
    );
    setSelectedDecks([]);
  }

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
        <fieldset className="format-picker">
          <legend>Formats</legend>
          <div>
            {["legacy", "commander"].map((format) => (
              <button
                key={format}
                type="button"
                className={formats.includes(format) ? "selected" : ""}
                aria-pressed={formats.includes(format)}
                onClick={() => toggleFormat(format)}
              >
                {format[0].toUpperCase() + format.slice(1)}
              </button>
            ))}
          </div>
          <small>Choose one or more formats.</small>
        </fieldset>
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
            Your account has no deck for the selected formats.
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

function LocalTrainingForm({ status, account, refresh }: { status: CapabilityStatus | null; account: AccountStatus | null; refresh: () => void }) {
  const [query, setQuery] = useState("");
  const [decks, setDecks] = useState<DeckSummary[]>([]);
  const [selected, setSelected] = useState<DeckSummary[]>([]);
  const [busy, setBusy] = useState("");
  const [model, setModel] = useState("v12");
  const [modelName, setModelName] = useState("");
  const [reservePlaytest, setReservePlaytest] = useState(true);
  const [selfPlayAllSeats, setSelfPlayAllSeats] = useState(true);
  const [starting, setStarting] = useState(false);
  const [created, setCreated] = useState(false);
  const [error, setError] = useState("");
  const requiredFormat = model === "v12" ? "legacy" : "commander";
  const compatibleDeckCount = selected.filter(
    (deck) => deck.format?.toLowerCase() === requiredFormat,
  ).length;

  useEffect(() => {
    if (account?.valid !== true) return;
    let active = true;
    const timer = window.setTimeout(() => {
      void Promise.all([searchDecks(query, "legacy"), searchDecks(query, "commander")])
        .then((groups) => { if (active) setDecks(groups.flat()); })
        .catch((reason) => { if (active) setError(reason instanceof Error ? reason.message : "Unable to search decks."); });
    }, 250);
    return () => { active = false; window.clearTimeout(timer); };
  }, [account?.valid, query]);

  useEffect(() => {
    void loadTrainingDeckPool().then((pool) => setSelected(pool.decks)).catch(() => undefined);
  }, []);

  async function toggleDeck(deck: DeckSummary) {
    if (selected.some((item) => item.id === deck.id)) {
      const next = selected.filter((item) => item.id !== deck.id);
      setSelected(next);
      void saveTrainingDeckPool(next);
      return;
    }
    setBusy(deck.id);
    setError("");
    try {
      const downloaded = await downloadDeck(deck.id);
      const next = [...selected, { ...deck, playableCardCount: downloaded.cardCount }];
      setSelected(next);
      await saveTrainingDeckPool(next);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to download this deck.");
    } finally {
      setBusy("");
    }
  }

  async function configureAgent() {
    setStarting(true);
    setCreated(false);
    setError("");
    try {
      await createModel({
        model,
        model_name: modelName.trim(),
        parallel_matches: 1,
        gpu_memory_mb: 0,
        reserve_playtest: reservePlaytest,
        self_play_all_seats: selfPlayAllSeats,
      });
      setCreated(true);
      refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to configure this agent.");
    } finally {
      setStarting(false);
    }
  }

  return <section className="panel configure local-training-form">
    <div className="section-heading"><div><span className="eyebrow">New agent</span><h2>Architecture and training decks</h2><p className="section-lead">Create the agent identity here. CPU, GPU and simultaneous games are assigned later from Jobs.</p></div><span className="step">CONFIG</span></div>
    {account?.valid !== true && <div className="notice warning"><strong>{account?.configured ? "Replace the rejected API key" : "API key required"}</strong><span>{selected.length > 0 ? "Your downloaded deck snapshots remain available locally. A valid key is only required to add more decks or join the League." : account?.reason ?? "Configure your Deep Deck League API key to access training decks."}</span></div>}
    <>
      <div className="training-pool-heading"><div><strong>Training deck pool</strong><small>{selected.length === 0 ? "No deck selected" : `${selected.length} deck${selected.length === 1 ? "" : "s"} ready locally`}</small></div></div>
      {selected.length > 0 && <div className="selected-training-pool">{selected.map((deck) => <button type="button" key={deck.id} onClick={() => void toggleDeck(deck)} title="Remove from training pool"><span><strong>{deck.name}</strong><small>{deck.format ?? "Deck"} · v{deck.version}</small></span><b aria-hidden="true">×</b></button>)}</div>}
      {account?.valid === true && <><label className="deck-search">Add decks<input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search any deck by name…" autoFocus /></label>
      <div className="training-deck-grid" aria-label="Training deck results">
        {decks.map((deck) => { const isSelected = selected.some((item) => item.id === deck.id); return <button key={deck.id} type="button" className={isSelected ? "selected" : ""} onClick={() => void toggleDeck(deck)} disabled={Boolean(busy)}><span aria-hidden="true">{busy === deck.id ? "…" : isSelected ? "✓" : "+"}</span><strong>{deck.name}</strong><small>{deck.format ?? "Deck"} · v{deck.version} · {deck.playableCardCount} cards</small></button>; })}
      </div></>}
      {busy && <p className="deck-pool-empty" role="status">Adding the deck to the local pool…</p>}
      {selected.length > 0 && <div className="notice success" role="status"><strong>Training pool ready locally</strong><span>{selected.length} immutable deck snapshot{selected.length === 1 ? "" : "s"} stored in .deepdeck/decks.</span></div>}
      {error && <p className="form-error" role="alert">{error}</p>}
      {selected.length > 0 && <div className="training-launch">
        <label>Model name<input value={modelName} maxLength={64} onChange={(event) => setModelName(event.target.value)} placeholder="Example: Montréal Control" /><small>This is your AI's name. V11 or V12 is only its starting architecture.</small></label>
        <label>Model<select value={model} onChange={(event) => setModel(event.target.value)}><option value="v12" disabled={!selected.some((deck) => deck.format?.toLowerCase() === "legacy")}>V12 · Legacy</option><option value="v11" disabled={!selected.some((deck) => deck.format?.toLowerCase() === "commander")}>V11 · Commander</option></select><small>{compatibleDeckCount} {requiredFormat} deck{compatibleDeckCount === 1 ? "" : "s"} available for this model.</small></label>
        <div className="model-implementation"><strong>{model === "v12" ? "V12 · structured two-player policy" : "V11 · structured multiplayer policy"}</strong><p>{model === "v12" ? "Designed for two-player Legacy: structured observations, legal-action encoding, two relative value slots, self-play collection and PPO updates." : "Designed for Commander: structured observations, legal-action encoding, four multiplayer value slots, shared-policy self-play and PPO updates."}</p><small>Deep Deck provides the architecture and training implementation. The generated weights, name and local model belong to this workspace.</small></div>
        <div className="agent-mode-grid">
          <label className="check-setting"><input type="checkbox" checked={selfPlayAllSeats} onChange={(event) => setSelfPlayAllSeats(event.target.checked)} /><span><strong>Shared-model self-play</strong><small>{selfPlayAllSeats ? "The same model controls every player in each training game." : "Half of training games use the built-in anchor opponent."}</small></span></label>
          <label className="check-setting"><input type="checkbox" checked={reservePlaytest} onChange={(event) => setReservePlaytest(event.target.checked)} /><span><strong>Publish playable weights</strong><small>Keeps a stable checkpoint available while newer weights train.</small></span></label>
        </div>
        {created && <div className="notice success" role="status"><strong>Agent configured</strong><span>Open Jobs to assign simultaneous games and start training.</span></div>}
        <div className="form-actions"><button className="primary" type="button" onClick={() => void configureAgent()} disabled={starting || created || modelName.trim().length < 2 || compatibleDeckCount === 0 || !status?.torch.ready || !status?.engine.healthy}>{starting ? "Preparing agent…" : created ? "Agent configured" : `Create ${modelName.trim() || model.toUpperCase()}`}</button><small>{modelName.trim().length < 2 ? "Give your model a name first." : compatibleDeckCount === 0 ? `Add a ${requiredFormat} deck for this agent.` : "No training starts until you explicitly start it in Jobs."}</small></div>
      </div>}
    </>
  </section>;
}

function formatBytes(bytes: number | null | undefined) {
  if (bytes === null || bytes === undefined) return "Unavailable";
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(0)} MiB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GiB`;
}

function AgentEditor({ model, onClose, refresh }: {
  model: LocalModel;
  onClose: () => void;
  refresh: () => void;
}) {
  const [name, setName] = useState(model.name);
  const [decks, setDecks] = useState<DeckSummary[]>(model.decks);
  const [results, setResults] = useState<DeckSummary[]>([]);
  const [query, setQuery] = useState("");
  const [reservePlaytest, setReservePlaytest] = useState(model.reservePlaytest);
  const [selfPlayAllSeats, setSelfPlayAllSeats] = useState(model.selfPlayAllSeats !== false);
  const [busy, setBusy] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    const timer = window.setTimeout(() => {
      void searchDecks(query, model.format)
        .then((items) => { if (active) setResults(items); })
        .catch((reason) => { if (active) setError(reason instanceof Error ? reason.message : "Unable to search decks."); });
    }, 250);
    return () => { active = false; window.clearTimeout(timer); };
  }, [model.format, query]);

  async function toggleDeck(deck: DeckSummary) {
    if (decks.some((item) => item.id === deck.id)) {
      setDecks((current) => current.filter((item) => item.id !== deck.id));
      return;
    }
    setBusy(deck.id);
    setError("");
    try {
      const downloaded = await downloadDeck(deck.id);
      setDecks((current) => [...current, { ...deck, playableCardCount: downloaded.cardCount }]);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to download this deck.");
    } finally {
      setBusy("");
    }
  }

  async function save() {
    setSaving(true);
    setError("");
    try {
      await updateModel(model.id, {
        name: name.trim(),
        decks,
        reservePlaytest,
        selfPlayAllSeats,
      });
      refresh();
      onClose();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to update this agent.");
    } finally {
      setSaving(false);
    }
  }

  return <div className="agent-editor">
    <div className="training-launch">
      <label>Agent name<input value={name} maxLength={64} onChange={(event) => setName(event.target.value)} /></label>
      <div className="automatic-setting"><span>Architecture</span><strong>{model.architecture.toUpperCase()} · {model.format}</strong><small>The architecture stays fixed so existing weights remain compatible.</small></div>
    </div>
    <div className="training-pool-heading"><div><strong>Decks this agent can play and train with</strong><small>{decks.length} selected</small></div></div>
    {decks.length > 0 && <div className="selected-training-pool">{decks.map((deck) => <button type="button" key={deck.id} onClick={() => void toggleDeck(deck)} title="Remove from this agent"><span><strong>{deck.name}</strong><small>{deck.format} · v{deck.version}</small></span><b aria-hidden="true">×</b></button>)}</div>}
    <label className="deck-search">Add {model.format} decks<input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search decks by name…" /></label>
    <div className="training-deck-grid" aria-label={`Deck results for ${model.name}`}>
      {results.map((deck) => { const selected = decks.some((item) => item.id === deck.id); return <button key={deck.id} type="button" className={selected ? "selected" : ""} disabled={Boolean(busy)} onClick={() => void toggleDeck(deck)}><span aria-hidden="true">{busy === deck.id ? "…" : selected ? "✓" : "+"}</span><strong>{deck.name}</strong><small>v{deck.version} · {deck.playableCardCount} cards</small></button>; })}
    </div>
    <div className="agent-mode-grid">
      <label className="check-setting"><input type="checkbox" checked={selfPlayAllSeats} onChange={(event) => setSelfPlayAllSeats(event.target.checked)} /><span><strong>Shared-model self-play</strong><small>The same model controls every training seat.</small></span></label>
      <label className="check-setting"><input type="checkbox" checked={reservePlaytest} onChange={(event) => setReservePlaytest(event.target.checked)} /><span><strong>Publish playable weights</strong><small>Keep a stable checkpoint available for playtests.</small></span></label>
    </div>
    {error && <p className="form-error" role="alert">{error}</p>}
    <div className="form-actions"><button type="button" onClick={onClose}>Cancel</button><button className="primary" type="button" disabled={saving || name.trim().length < 2 || decks.length === 0} onClick={() => void save()}>{saving ? "Saving agent…" : "Save agent"}</button><small>Stop all of this agent's jobs before saving changes.</small></div>
  </div>;
}

function AgentCatalog({
  models,
  resources,
  refresh,
}: {
  models: LocalModel[];
  resources: ResourceSnapshot | null;
  refresh: () => void;
}) {
  const [confirming, setConfirming] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [deleting, setDeleting] = useState("");
  const [editing, setEditing] = useState("");
  const [message, setMessage] = useState("");

  async function remove(model: LocalModel) {
    setDeleting(model.id);
    setMessage("");
    try {
      const result = await deleteModel(model.id);
      setConfirming("");
      setConfirmation("");
      setMessage(`${result.name} deleted · ${formatBytes(result.reclaimedBytes)} reclaimed.`);
      refresh();
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "Unable to delete this agent.");
    } finally {
      setDeleting("");
    }
  }

  if (models.length === 0) {
    return <div className="empty-agent"><span>AI</span><div><strong>No configured agent yet</strong><p>Choose an architecture, give it a deck pool, then create its local identity.</p></div></div>;
  }
  return <div className="agent-catalog">
    {models.map((model) => {
      const activeWorkers = resources?.workers.filter((worker) => worker.modelId === model.id) ?? [];
      const isConfirming = confirming === model.id;
      return <article className="agent-card" key={model.id}>
        <div className="agent-card-main">
          <span className={`model-ready ${model.ready ? "ready" : ""}`}>{model.ready ? "Playable weights" : model.status === "running" ? "Preparing weights" : "Configured"}</span>
          <h3>{model.name}</h3>
          <p>{model.description}</p>
          <div className="agent-card-tags"><span>{model.architecture.toUpperCase()}</span><span>{model.format}</span><span>{model.selfPlayAllSeats === false ? "Mixed opponents" : "Shared-model self-play"}</span></div>
        </div>
        <dl className="agent-card-facts">
          <div><dt>Training decks</dt><dd>{model.decks.length}</dd></div>
          <div><dt>Disk space</dt><dd>{formatBytes(model.diskBytes)}</dd><small>{formatBytes(model.weightsBytes)} weights</small></div>
          <div><dt>Live workers</dt><dd>{activeWorkers.reduce((total, worker) => total + worker.workerSlots, 0)}</dd></div>
          <div><dt>Games learned</dt><dd>{model.trainingState?.completedGames ?? 0}</dd></div>
        </dl>
        <div className="agent-deck-list">{model.decks.map((deck) => <span key={deck.id}>{deck.name}</span>)}</div>
        {!isConfirming && <div className="agent-card-actions"><button type="button" disabled={activeWorkers.length > 0} onClick={() => { setEditing(editing === model.id ? "" : model.id); setMessage(""); }}>{editing === model.id ? "Close editor" : "Edit agent and decks"}</button><button className="danger subtle" type="button" onClick={() => { setEditing(""); setConfirming(model.id); setConfirmation(""); setMessage(""); }}>Delete agent and weights</button></div>}
        {editing === model.id && <AgentEditor model={model} refresh={refresh} onClose={() => setEditing("")} />}
        {isConfirming && <div className="delete-agent-confirm" role="alertdialog" aria-label={`Delete ${model.name}`}>
          <strong>Delete {formatBytes(model.diskBytes)} permanently?</strong>
          <p>The agent, checkpoints, training history and local statistics in this run will be removed.</p>
          {activeWorkers.length > 0 ? <small>Stop its active jobs first.</small> : <label>Type <b>{model.name}</b><input value={confirmation} onChange={(event) => setConfirmation(event.target.value)} /></label>}
          <div><button type="button" onClick={() => { setConfirming(""); setConfirmation(""); }}>Keep agent</button><button className="danger" type="button" disabled={activeWorkers.length > 0 || confirmation !== model.name || deleting === model.id} onClick={() => void remove(model)}>{deleting === model.id ? "Deleting…" : "Delete files"}</button></div>
        </div>}
      </article>;
    })}
    {message && <p className={message.includes("deleted") ? "form-success" : "form-error"} role="status">{message}</p>}
  </div>;
}

function ResourceSystemSummary({ resources }: { resources: ResourceSnapshot | null }) {
  if (!resources) return null;
  const ramPercent = resources.system.ramTotalBytes
    ? Math.round((resources.system.ramUsedBytes / resources.system.ramTotalBytes) * 100)
    : 0;
  return (
    <section className="resource-summary" aria-label="Local resource usage">
      <article><span>System RAM</span><strong>{formatBytes(resources.system.ramUsedBytes)}</strong><small>{ramPercent}% of {formatBytes(resources.system.ramTotalBytes)}</small></article>
      <article><span>GPU memory</span><strong>{formatBytes(resources.system.gpuUsedBytes)}</strong><small>{resources.system.gpuTotalBytes === null ? "NVIDIA telemetry unavailable" : `of ${formatBytes(resources.system.gpuTotalBytes)}`}</small></article>
      <article><span>Local Engine</span><strong>{formatBytes(resources.engine.ramBytes)}</strong><small>{resources.engine.activeLocalGames ? `≈ ${formatBytes(resources.engine.ramPerGameEstimate)} per active game` : "No active local game"}</small></article>
    </section>
  );
}

function AgentAllocationRow({
  model,
  resources,
  jobs,
  refresh,
}: {
  model: LocalModel;
  resources: ResourceSnapshot | null;
  jobs: Job[];
  refresh: () => void;
}) {
  const [plan, setPlan] = useState<ResourcePlan | null>(null);
  const [savedPlan, setSavedPlan] = useState<ResourcePlan | null>(null);
  const [saving, setSaving] = useState(false);
  const [working, setWorking] = useState(false);
  const [message, setMessage] = useState("");
  const workers = resources?.workers.filter((worker) => worker.modelId === model.id) ?? [];
  const trainingWorker = workers.find((worker) => worker.kind === "training.pool");
  const trainingJob = jobs.find((job) => job.model_id === model.id && job.kind === "training.pool" && ["queued", "running"].includes(job.status));
  const trainingActive = Boolean(trainingWorker || trainingJob);

  useEffect(() => {
    let active = true;
    void loadModelResources(model.id)
      .then((value) => { if (active) { setPlan(value); setSavedPlan(value); } })
      .catch((reason) => { if (active) setMessage(reason instanceof Error ? reason.message : "Unable to load resources."); });
    return () => { active = false; };
  }, [model.id]);

  function update(key: keyof ResourcePlan, value: number) {
    setPlan((current) => current ? { ...current, [key]: value } : current);
  }

  async function save() {
    if (!plan) return;
    setSaving(true);
    setMessage("");
    try {
      const saved = await saveModelResources(model.id, plan);
      setPlan(saved);
      setSavedPlan(saved);
      setMessage(trainingActive ? "Allocation saved · trainer updates after the current batch." : "Allocation saved · ready to start.");
      refresh();
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "Unable to save resources.");
    } finally {
      setSaving(false);
    }
  }

  async function toggleTraining() {
    if (!plan) return;
    setWorking(true);
    setMessage("");
    try {
      if (trainingActive) {
        const jobId = trainingJob?.id ?? trainingWorker?.jobId;
        if (jobId) await stopJob(jobId);
        setMessage("Training stopped. The agent and its weights are preserved.");
      } else {
        await startJob({ kind: "training.pool", model_id: model.id });
        setMessage("Training is starting. Live games will appear below.");
      }
      refresh();
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "Unable to update training.");
    } finally {
      setWorking(false);
    }
  }

  const ramBytes = workers.reduce((total, worker) => total + worker.ramBytes, 0);
  const gpuBytes = workers.reduce(
    (total, worker) => total + (worker.gpuBytes ?? 0),
    0,
  );

  return (
    <tr>
      <th scope="row">
        <span className={`model-ready ${model.ready ? "ready" : ""}`}>
          {trainingActive ? "Training live" : model.ready ? "Playable" : "Configured"}
        </span>
        <strong>{model.name}</strong>
        <small>{model.architecture.toUpperCase()} · {model.format}</small>
      </th>
      {!plan ? (
        <td colSpan={5}>Loading allocation…</td>
      ) : (
        <>
          <td className="agent-allocation-resources">
            <label>
              GPU cap
              <span>
                <input
                  aria-label={`GPU memory limit for ${model.name}`}
                  type="number"
                  min="0"
                  max="24576"
                  step="256"
                  value={plan.gpuMemoryMb}
                  onChange={(event) => update("gpuMemoryMb", Number(event.target.value))}
                />
                MiB
              </span>
            </label>
            <small>{formatBytes(ramBytes)} RAM · {formatBytes(gpuBytes)} GPU active</small>
          </td>
          {([
            ["trainingMatches", "Self-play", 32],
            ["localMatches", "Playtest", 8],
            ["leagueMatches", "League", 32],
          ] as const).map(([key, label, maximum]) => (
            <td className="agent-allocation-slot" key={key}>
              <input
                aria-label={`${label} slots for ${model.name}`}
                type="number"
                min="0"
                max={maximum}
                value={plan[key]}
                onChange={(event) => update(key, Number(event.target.value))}
              />
              <small>{plan[key] === 0 ? "Paused" : `${plan[key]} simultaneous`}</small>
            </td>
          ))}
        </>
      )}
      <td className="agent-allocation-save">
        <button className="secondary" type="button" disabled={saving || !plan || JSON.stringify(plan) === JSON.stringify(savedPlan)} onClick={() => void save()}>
          {saving ? "Saving…" : JSON.stringify(plan) === JSON.stringify(savedPlan) ? "Allocation saved" : "Save allocation"}
        </button>
        <button className={trainingActive ? "danger" : "primary"} type="button" disabled={working || !plan || (!trainingActive && plan.trainingMatches < 1)} onClick={() => void toggleTraining()}>{working ? "Working…" : trainingActive ? "Stop training" : "Start training"}</button>
        {message && <small role="status">{message}</small>}
      </td>
    </tr>
  );
}

function AgentAllocationTable({
  models,
  resources,
  jobs,
  refresh,
}: {
  models: LocalModel[];
  resources: ResourceSnapshot | null;
  jobs: Job[];
  refresh: () => void;
}) {
  return (
    <div className="agent-allocation-scroll">
      <table className="agent-allocation-table" aria-label="Agent resource allocation">
        <thead>
          <tr>
            <th scope="col">Agent</th>
            <th scope="col">Resources</th>
            <th scope="col">Self-play games</th>
            <th scope="col">Playtest slots</th>
            <th scope="col">League connections</th>
            <th scope="col">Actions</th>
          </tr>
        </thead>
        <tbody>
          {models.map((model) => (
            <AgentAllocationRow
              key={model.id}
              model={model}
              resources={resources}
              jobs={jobs}
              refresh={refresh}
            />
          ))}
        </tbody>
      </table>
      <p className="resource-caveat">
        Values are per-agent limits. Zero pauses that activity without deleting the agent.
        RAM and GPU usage come from the active process tree.
      </p>
    </div>
  );
}

function ActiveWorkersPanel({
  resources,
  jobs,
  refresh,
}: {
  resources: ResourceSnapshot | null;
  jobs: Job[];
  refresh: () => void;
}) {
  const [stopping, setStopping] = useState("");
  const [error, setError] = useState("");
  const workers = resources?.workers ?? [];
  const queued = jobs.filter(
    (job) => ["queued", "running"].includes(job.status) && !workers.some((worker) => worker.jobId === job.id),
  );

  async function stop(workerId: string) {
    setStopping(workerId);
    setError("");
    try {
      await stopJob(workerId);
      refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to stop this worker.");
    } finally {
      setStopping("");
    }
  }

  return <section className="panel active-workers">
    <div className="section-heading"><div><span className="eyebrow">Processes owned by this workbench</span><h2>Active services</h2><p className="section-lead">This is the source of truth for occupied slots. Recovered processes can be stopped here too.</p></div><button className="text-button" type="button" onClick={refresh}>Refresh</button></div>
    {workers.length === 0 && queued.length === 0 ? <div className="empty"><span>◇</span><p>No worker is holding a training, playtest or League slot.</p></div> : <div className="worker-table" role="table">
      <div className="worker-row worker-head" role="row"><span>Service</span><span>Slots</span><span>RAM</span><span>GPU</span><span>Process</span><span /></div>
      {workers.map((worker) => <div className="worker-row" role="row" key={worker.jobId}>
        <span><strong>{worker.label}</strong><small>{worker.kind === "training.pool" ? "Local self-play trainer" : worker.kind === "matchmaking.agent" ? "League connection" : "Local playtest"}</small></span>
        <span><strong>{worker.workerSlots}</strong><small>{worker.workerSlots === 1 ? "worker" : "workers"}</small></span>
        <span><strong>{formatBytes(worker.ramBytes)}</strong><small>{formatBytes(worker.ramPerWorkerEstimate)} / worker</small></span>
        <span><strong>{formatBytes(worker.gpuBytes)}</strong><small>{worker.gpuBytes === null ? "No process telemetry" : `${formatBytes(worker.gpuPerWorkerEstimate)} / worker`}</small></span>
        <span><code>{worker.pids.join(", ")}</code><small>{worker.jobId.startsWith("recovered-") ? "Recovered after restart" : "Controller attached"}</small></span>
        <span><button className="danger subtle" type="button" disabled={stopping === worker.jobId} onClick={() => void stop(worker.jobId)}>{stopping === worker.jobId ? "Stopping…" : "Stop"}</button></span>
      </div>)}
      {queued.map((job) => <div className="worker-row pending" role="row" key={job.id}><span><strong>{job.label}</strong><small>{job.kind}</small></span><span>—</span><span>Starting…</span><span>—</span><span><code>{job.id.slice(0, 8)}</code></span><span><button className="danger subtle" type="button" disabled={stopping === job.id} onClick={() => void stop(job.id)}>Cancel</button></span></div>)}
    </div>}
    {error && <p className="form-error" role="alert">{error}</p>}
  </section>;
}

function LeagueConnectionsPanel({
  account,
  models,
  resources,
  jobs,
  refresh,
}: {
  account: AccountStatus | null;
  models: LocalModel[];
  resources: ResourceSnapshot | null;
  jobs: Job[];
  refresh: () => void;
}) {
  const [competitions, setCompetitions] = useState<CompetitionSummary[]>([]);
  const [catalogs, setCatalogs] = useState<Record<string, DeckSummary[]>>({});
  const [deckModes, setDeckModes] = useState<Record<string, "all" | "pool" | "single">>({});
  const [singleDecks, setSingleDecks] = useState<Record<string, string>>({});
  const [deckPools, setDeckPools] = useState<Record<string, string[]>>({});
  const [loadingFormats, setLoadingFormats] = useState<string[]>([]);
  const [working, setWorking] = useState("");
  const [messages, setMessages] = useState<Record<string, string>>({});
  const leagueFormats = [...new Set(models.filter((model) => model.ready).map((model) => model.format))].sort().join(",");

  useEffect(() => {
    if (account?.valid !== true) {
      setCompetitions([]);
      setCatalogs({});
      return;
    }
    const formats = leagueFormats.split(",").filter(Boolean);
    setLoadingFormats(formats);
    void Promise.all([
      loadCompetitions(),
      ...formats.map(async (format) => [format, await loadAllFormatDecks(format)] as const),
    ])
      .then(([loadedCompetitions, ...loadedCatalogs]) => {
        setCompetitions(loadedCompetitions as CompetitionSummary[]);
        setCatalogs(Object.fromEntries(loadedCatalogs as ReadonlyArray<readonly [string, DeckSummary[]]>));
        setMessages((current) => ({ ...current, global: "" }));
      })
      .catch((reason) => setMessages((current) => ({ ...current, global: reason instanceof Error ? reason.message : "Unable to load League decks and competitions." })))
      .finally(() => setLoadingFormats([]));
  }, [account?.valid, leagueFormats]);

  function decksFor(model: LocalModel) {
    return catalogs[model.format] ?? model.decks.filter((deck) => deck.format?.toLowerCase() === model.format);
  }

  function togglePoolDeck(modelId: string, deckId: string) {
    setDeckPools((current) => {
      const selected = current[modelId] ?? [];
      return {
        ...current,
        [modelId]: selected.includes(deckId)
          ? selected.filter((id) => id !== deckId)
          : [...selected, deckId],
      };
    });
  }

  async function connect(model: LocalModel) {
    const availableDecks = decksFor(model);
    const mode = deckModes[model.id] ?? "single";
    const selectedIds = mode === "all"
      ? availableDecks.map((deck) => deck.id)
      : mode === "pool"
        ? deckPools[model.id] ?? []
        : [singleDecks[model.id] ?? availableDecks[0]?.id].filter((id): id is string => Boolean(id));
    const selected = selectedIds
      .map((id) => availableDecks.find((deck) => deck.id === id))
      .filter((deck): deck is DeckSummary => Boolean(deck));
    const competition = competitions.find((item) => item.format.toLowerCase() === model.format);
    if (!competition) return;
    if (selected.length === 0) {
      setMessages((current) => ({ ...current, [model.id]: mode === "pool" ? "Choose at least one deck for this League pool." : `No valid ${model.format} deck is available.` }));
      return;
    }
    setWorking(model.id);
    setMessages((current) => ({ ...current, [model.id]: "" }));
    try {
      const plan = await loadModelResources(model.id);
      if (plan.leagueMatches === 0) {
        setMessages((current) => ({ ...current, [model.id]: "Set League connections above to at least 1, then save the allocation." }));
        return;
      }
      const existingWorkers = (resources?.workers ?? []).filter(
        (worker) => worker.modelId === model.id && worker.kind === "matchmaking.agent",
      );
      const queuedJobs = jobs.filter(
        (job) => job.model_id === model.id && job.kind === "matchmaking.agent" && job.status === "queued",
      );
      for (const jobId of new Set([
        ...existingWorkers.map((worker) => worker.jobId),
        ...queuedJobs.map((job) => job.id),
      ])) {
        await stopJob(jobId);
      }
      await startJob({
        kind: "matchmaking.agent",
        model_id: model.id,
        agent: model.architecture,
        checkpoint: model.checkpointPath,
        speed: "1s",
        continuous: true,
        connections: plan.leagueMatches,
        competition_version_id: competition.versionId,
        deck_version_ids: selected.map((deck) => deck.id),
      });
      const usedDecks = Math.min(plan.leagueMatches, selected.length);
      const selfPlay = plan.leagueMatches >= 2
        ? " The League prioritizes distinct agents, then pairs these seats together only when too few distinct agents are queued to fill the table."
        : " Allocate at least 2 League connections to allow self-play when the queue is empty.";
      setMessages((current) => ({ ...current, [model.id]: `${plan.leagueMatches} League seat${plan.leagueMatches === 1 ? "" : "s"} started through one agent connection across ${usedDecks} deck${usedDecks === 1 ? "" : "s"}.${selfPlay}` }));
      refresh();
    } catch (reason) {
      setMessages((current) => ({ ...current, [model.id]: reason instanceof Error ? reason.message : "Unable to connect this agent to the League." }));
    } finally {
      setWorking("");
    }
  }

  return <section className="panel league-connections">
    <div className="section-heading"><div><span className="eyebrow">Optional remote activity</span><h2>League connections</h2><p className="section-lead">Start the number of persistent connections saved in the allocation table. The League fills tables with distinct agents first, then uses your additional seats as self-play fallback.</p></div></div>
    {account?.valid !== true ? <div className="notice warning"><strong>{account?.configured ? "Saved League key rejected" : "League account not connected"}</strong><span>{account?.reason ?? "Add the API key in Agent configuration. Nothing else is required on a separate page."}</span></div> : <div className="league-agent-list">
      {models.filter((model) => model.ready).map((model) => {
        const competition = competitions.find((item) => item.format.toLowerCase() === model.format);
        const live = (resources?.workers ?? []).filter((worker) => worker.modelId === model.id && worker.kind === "matchmaking.agent").length;
        const availableDecks = decksFor(model);
        const mode = deckModes[model.id] ?? "single";
        const pool = deckPools[model.id] ?? [];
        const loading = loadingFormats.includes(model.format);
        return <article key={model.id}>
          <div><span className="model-ready ready">{live} connected</span><strong>{model.name}</strong><small>{competition ? competition.name : `No active ${model.format} competition`}</small></div>
          <div className="league-deck-configuration">
            <span className="field-label">League decks</span>
            <div className="league-deck-modes" role="group" aria-label={`Deck selection for ${model.name}`}>
              {(["all", "pool", "single"] as const).map((candidate) => <button key={candidate} type="button" className={mode === candidate ? "selected" : ""} aria-pressed={mode === candidate} onClick={() => setDeckModes((current) => ({ ...current, [model.id]: candidate }))}>{candidate === "all" ? "All legal" : candidate === "pool" ? "Deck pool" : "One deck"}</button>)}
            </div>
            {loading ? <small>Loading valid {model.format} decks…</small> : mode === "single" ? <select aria-label={`Deck for ${model.name}`} value={singleDecks[model.id] ?? availableDecks[0]?.id ?? ""} onChange={(event) => setSingleDecks((current) => ({ ...current, [model.id]: event.target.value }))}>{availableDecks.map((deck) => <option value={deck.id} key={deck.id}>{deck.name}</option>)}</select> : mode === "pool" ? <div className="league-deck-pool">{availableDecks.map((deck) => <label key={deck.id}><input type="checkbox" checked={pool.includes(deck.id)} onChange={() => togglePoolDeck(model.id, deck.id)} /><span>{deck.name}</span></label>)}</div> : <small>{availableDecks.length} valid {model.format} deck{availableDecks.length === 1 ? "" : "s"} will be rotated across the allocated connections.</small>}
          </div>
          <button className="secondary" type="button" disabled={working === model.id || loading || !competition || availableDecks.length === 0 || (mode === "pool" && pool.length === 0)} onClick={() => void connect(model)}>{working === model.id ? "Connecting…" : "Connect allocated slots"}</button>
          {messages[model.id] && <small role="status">{messages[model.id]}</small>}
        </article>;
      })}
      {models.every((model) => !model.ready) && <div className="empty"><span>◇</span><p>Playable weights are required before connecting to the League.</p></div>}
    </div>}
    {messages.global && <p className="form-error" role="alert">{messages.global}</p>}
  </section>;
}

function elapsedLabel(startedAtUnixMs: number | null) {
  if (!startedAtUnixMs) return "Starting";
  const seconds = Math.max(0, Math.round((Date.now() - startedAtUnixMs) / 1000));
  if (seconds < 60) return `${seconds}s`;
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

function ActiveGamesPanel({ games, refresh, onOpen }: { games: ActiveGame[]; refresh: () => void; onOpen: (game: ActiveGame) => void }) {
  const [stopping, setStopping] = useState("");
  const [error, setError] = useState("");

  async function cancel(game: ActiveGame) {
    setStopping(game.id);
    setError("");
    try {
      await stopGame(game.id);
      refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to cancel this game.");
    } finally {
      setStopping("");
    }
  }

  return <section className="panel live-games">
    <div className="section-heading"><div><span className="eyebrow"><i className={games.length ? "live-pulse" : ""} /> Live game telemetry</span><h2>Games in progress</h2><p className="section-lead">Training, local playtest, and League matches appear here as soon as a game starts.</p></div><span className="live-game-count">{games.length} live</span></div>
    {games.length === 0 ? <div className="empty live-empty"><span>0</span><div><strong>No game is currently running</strong><p>If a slot still looks occupied, check Active services above: the process can be stopped even before an Engine session appears.</p></div></div> : <div className="live-game-grid">
      {games.map((game) => <article className="live-game-card" key={game.id}>
        <header><span className={`game-source ${game.source}`}>{game.source === "training" ? `Worker ${game.worker}` : game.source === "league" ? "League" : "Local"}</span><strong>{elapsedLabel(game.startedAtUnixMs)}</strong></header>
        <h3>{game.modelName}</h3>
        <p>{game.decks.filter(Boolean).join(" vs ") || "Preparing matchup"}</p>
        <div className="game-progress"><span>Round <b>{game.roundNumber ?? "—"}</b></span><span>Turn <b>{game.turnNumber ?? "—"}</b></span><span>Decisions <b>{game.decisions}</b></span><span>Players <b>{game.players}</b></span></div>
        {game.playersState.length > 0 && <div className="game-player-strip">{game.playersState.map((player, index) => <span className={player.hasLost ? "lost" : ""} key={player.id ?? index}><b>{player.life ?? "—"}</b><small>{player.name ?? `P${index + 1}`} · {player.handCount ?? 0} cards</small></span>)}</div>}
        <footer><small>{game.mode ?? game.status}{game.sessionId ? ` · ${game.sessionId}` : " · session opening"}</small><div className="live-game-actions">{game.source === "local" && game.jobId && <button type="button" onClick={() => onOpen(game)}>Open game</button>}{game.source === "league" && game.watchUrl && <a className="button-link" href={game.watchUrl} target="_blank" rel="noreferrer">Watch League</a>}{game.source !== "league" && <button className="danger" type="button" disabled={!game.canCancel || stopping === game.id} onClick={() => void cancel(game)}>{stopping === game.id ? "Cancelling…" : game.canCancel ? "Cancel game" : "Opening…"}</button>}</div></footer>
      </article>)}
    </div>}
    {error && <p className="form-error" role="alert">{error}</p>}
  </section>;
}

function JobsDashboard({
  account,
  models,
  resources,
  jobs,
  games,
  refresh,
  onOpenGame,
}: {
  account: AccountStatus | null;
  models: LocalModel[];
  resources: ResourceSnapshot | null;
  jobs: Job[];
  games: ActiveGame[];
  refresh: () => void;
  onOpenGame: (game: ActiveGame) => void;
}) {
  return <div className="jobs-dashboard">
    <section className="jobs-intro"><div><span className="eyebrow">Capacity → activity → game</span><h2>One operational view</h2><p>Save the capacity you want, start the trainer, then follow every worker and Engine game below. League connections also appear here; they no longer need a separate page.</p></div><div className="jobs-legend"><span><i className="training" /> Training</span><span><i className="local" /> Playtest</span><span><i className="league" /> League</span></div></section>
    <ResourceSystemSummary resources={resources} />
    <section className="panel allocation-panel"><div className="section-heading"><div><span className="eyebrow">Desired capacity</span><h2>Agent allocations</h2><p className="section-lead">Self-play games use the same model for every seat when that mode is enabled in Agent configuration.</p></div></div>
      {models.length ? <AgentAllocationTable models={models} resources={resources} jobs={jobs} refresh={refresh} /> : <div className="empty"><span>AI</span><p>Create an agent configuration before allocating jobs.</p></div>}
    </section>
    <LeagueConnectionsPanel account={account} models={models} resources={resources} jobs={jobs} refresh={refresh} />
    <ActiveWorkersPanel resources={resources} jobs={jobs} refresh={refresh} />
    <ActiveGamesPanel games={games} refresh={refresh} onOpen={onOpenGame} />
  </div>;
}

function trainingMetrics(job: Job) {
  for (const line of [...job.logs].reverse()) {
    try {
      const value = JSON.parse(line) as Record<string, unknown>;
      if (typeof value.loss === "number") return value;
      const training = value.training;
      if (training && typeof training === "object") {
        const record = training as Record<string, unknown>;
        const ppo = record.ppo;
        if (ppo && typeof ppo === "object") {
          return {
            ...(ppo as Record<string, unknown>),
            updates: record.trainingStep ?? record.episode,
          };
        }
      }
    } catch { /* Non-JSON log line. */ }
  }
  return null;
}

function StatisticsPage({ jobs, decks, training }: { jobs: Job[]; decks: DeckStatistic[]; training: TrainingStatistic[] }) {
  const [selectedId, setSelectedId] = useState("");
  const runs = jobs.filter((job) => job.kind.startsWith("training."));
  const selected = training.find((item) => item.modelId === selectedId) ?? training[0];
  const selectedDecks = selected ? decks.filter((deck) => deck.modelId === selected.modelId) : decks;
  const decidedGames = selectedDecks.reduce((total, deck) => total + deck.gameWins + deck.gameLosses, 0);
  const wins = selectedDecks.reduce((total, deck) => total + deck.gameWins, 0);
  const lossPoints = selected?.latestMetrics.filter((point) => typeof point.loss === "number") ?? [];
  const maximumLoss = Math.max(0.0001, ...lossPoints.map((point) => Math.abs(point.loss ?? 0)));
  return <section className="statistics-page">
    <section className="statistics-toolbar"><div><span className="eyebrow">Local training history</span><h2>{selected?.modelName ?? "No trained agent yet"}</h2></div>{training.length > 0 && <label>Agent<select value={selected?.modelId ?? ""} onChange={(event) => setSelectedId(event.target.value)}>{training.map((item) => <option value={item.modelId} key={item.modelId}>{item.modelName} · {item.architecture.toUpperCase()}</option>)}</select></label>}</section>
    <div className="stats-summary training-kpis"><article><span>Completed games</span><strong>{selected?.completedGames ?? 0}</strong><small>{selected?.activeGames ?? 0} live now</small></article><article><span>Training updates</span><strong>{selected?.trainingStep ?? 0}</strong><small>{selected?.parallelGames ?? 0} simultaneous slots</small></article><article><span>Deck-pool win rate</span><strong>{decidedGames ? `${Math.round((wins / decidedGames) * 100)}%` : "—"}</strong><small>{decidedGames} decided games</small></article><article><span>Average game</span><strong>{selected?.averageGameSeconds ? `${selected.averageGameSeconds.toFixed(1)}s` : "—"}</strong><small>{selected?.phase ?? "not started"}</small></article></div>
    <section className="panel training-curve"><div className="section-heading"><div><span className="eyebrow">Last {lossPoints.length} PPO updates</span><h2>Learning curve</h2><p className="section-lead">Total loss by training update. Hover a bar for policy, value and entropy details.</p></div>{selected && <span className={`training-phase ${selected.activeGames ? "live" : ""}`}>{selected.activeGames ? "● collecting games" : selected.desiredState}</span>}</div>
      {lossPoints.length === 0 ? <div className="empty"><span>↗</span><p>The curve appears after the first completed training game.</p></div> : <div className="loss-chart" aria-label="Training loss chart">{lossPoints.map((point, index) => <i key={`${point.trainingStep}-${index}`} style={{ height: `${Math.max(4, (Math.abs(point.loss ?? 0) / maximumLoss) * 100)}%` }} title={`Step ${point.trainingStep} · loss ${point.loss?.toFixed(4)} · policy ${point.policyLoss?.toFixed(4) ?? "—"} · value ${point.valueLoss?.toFixed(4) ?? "—"} · entropy ${point.entropy?.toFixed(4) ?? "—"}`}><span>{point.loss?.toFixed(3)}</span></i>)}</div>}
    </section>
    <section className="panel"><div className="section-heading"><div><span className="eyebrow">Balanced self-play matchmaking</span><h2>Deck ratings</h2><p className="section-lead">The trainer uses a Plackett–Luce rating, similar in purpose to Elo but designed for ranked multiplayer outcomes. Conservative rating (μ − 3σ) is used to sample closer matchups.</p></div></div>
      {selectedDecks.length === 0 ? <div className="empty"><span>◇</span><p>Deck ratings will appear when this agent completes games.</p></div> : <div className="stats-table deck-stats-table" role="table">
        <div className="stats-row stats-head" role="row"><span>Agent · deck</span><span>PL rank</span><span>Conservative</span><span>μ ± σ</span><span>Matches</span><span>Game W–L</span><span>Win rate</span></div>
        {selectedDecks.map((deck) => <div className="stats-row" role="row" key={`${deck.modelId}-${deck.deckVersionId}`}><span><strong>{deck.deckName}</strong><small>{deck.modelName} · {deck.format}</small></span><span>{deck.rank ?? "—"}</span><span>{deck.ordinal.toFixed(2)}</span><span>{deck.mu.toFixed(2)} ± {deck.sigma.toFixed(2)}</span><span>{deck.matches}</span><span>{deck.gameWins}–{deck.gameLosses}</span><span>{deck.winRate === null ? "—" : `${Math.round(deck.winRate * 100)}%`}</span></div>)}
      </div>}
    </section>
    <section className="panel"><div className="section-heading"><div><span className="eyebrow">Stored locally in .deepdeck/learner.db</span><h2>Controller history</h2></div></div>{runs.length === 0 ? <div className="empty"><span>◇</span><p>No local training job has been launched yet.</p></div> : <div className="stats-table" role="table"><div className="stats-row stats-head" role="row"><span>Run</span><span>Status</span><span>Loss</span><span>Policy</span><span>Value</span><span>Updates</span></div>{runs.map((job) => { const metrics = trainingMetrics(job); return <div className="stats-row" role="row" key={job.id}><span><strong>{job.label}</strong><small>{new Date(job.created_at).toLocaleString()}</small></span><span>{job.status}</span><span>{metrics ? Number(metrics.loss).toFixed(4) : "—"}</span><span>{metrics ? Number(metrics.policy_loss).toFixed(4) : "—"}</span><span>{metrics ? Number(metrics.value_loss).toFixed(4) : "—"}</span><span>{metrics ? String(metrics.updates) : "—"}</span></div>; })}</div>}</section>
  </section>;
}

function PlaytestForm({
  status,
  models,
  refresh,
  onStarted,
}: {
  status: CapabilityStatus | null;
  models: LocalModel[];
  refresh: () => void;
  onStarted: (jobId: string) => void;
}) {
  const playableModels = models.filter((model) => model.ready);
  const [modelId, setModelId] = useState("");
  const selectedModel = playableModels.find((model) => model.id === modelId);
  const format = selectedModel?.format ?? "legacy";
  const [ownDeck, setOwnDeck] = useState("");
  const [opponentDeck, setOpponentDeck] = useState("");
  const [deckSearch, setDeckSearch] = useState("");
  const [error, setError] = useState("");
  const blockers = workflowBlockers(status, "local-playtest");
  useEffect(() => {
    if (!playableModels.some((model) => model.id === modelId)) {
      const first = playableModels[0];
      setModelId(first?.id ?? "");
      setOwnDeck(first ? "random" : "");
      setOpponentDeck(first ? "random" : "");
    }
  }, [modelId, playableModels]);
  const poolDecks = selectedModel?.decks ?? [];
  const visibleDecks = poolDecks.filter((deck) =>
    deck.name.toLowerCase().includes(deckSearch.toLowerCase()),
  );
  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    try {
      const job = await startJob({
        kind: "playtest.agent",
        model_id: selectedModel?.id,
        agent: selectedModel?.architecture,
        checkpoint: selectedModel?.checkpointPath,
        format,
        deck_version_id: ownDeck,
        opponent_deck_version_id: opponentDeck,
        engine_url: status?.engine.url,
      });
      onStarted(job.id);
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
            required
            value={modelId}
            onChange={(event) => {
              const next = playableModels.find((model) => model.id === event.target.value);
              setModelId(event.target.value);
              setOwnDeck(next ? "random" : "");
              setOpponentDeck(next ? "random" : "");
            }}
          >
            <option value="">Choose one of your models</option>
            {playableModels.map((model) => <option key={model.id} value={model.id}>{model.name} · {model.architecture.toUpperCase()}</option>)}
          </select>
          <small>Only models with local playable weights are listed.</small>
        </label>
        <div className="automatic-setting"><span>Format</span><strong>{format[0].toUpperCase() + format.slice(1)}</strong><small>Defined by your model's architecture.</small></div>
      </div>
      {playableModels.length === 0 && <div className="notice warning"><strong>No playable local model yet</strong><span>Start V11 or V12 training and wait for its first local checkpoint.</span></div>}
      <label>
        Search this model's training pool
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
            <option value="random">Random from this pool</option>
            {visibleDecks.map((deck) => (
              <option key={deck.id} value={deck.id}>
                {deck.name}
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
            <option value="random">Random near your deck's Plackett–Luce strength</option>
            {visibleDecks.map((deck) => (
              <option key={deck.id} value={deck.id}>
                {deck.name}
              </option>
            ))}
          </select>
        </label>
      </div>
      {selectedModel && poolDecks.length === 0 && <div className="notice warning"><strong>No deck snapshot is attached to this model</strong><span>Start a new named training run from a deck pool.</span></div>}
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
        <strong>Selection order</strong>
        <span>
          Your random deck is resolved first. The AI deck is then sampled by
          Plackett–Luce proximity, while preserving some matchup diversity.
        </span>
      </div>
      <button
        className="primary"
        disabled={blockers.length > 0 || !selectedModel || !ownDeck || !opponentDeck}
      >
        Launch behavior test <span>▶</span>
      </button>
    </form>
  );
}

function MatchmakingForm({
  status,
  models,
  refresh,
}: {
  status: CapabilityStatus | null;
  models: LocalModel[];
  refresh: () => void;
}) {
  const playableModels = models.filter((model) => model.ready);
  const [modelId, setModelId] = useState("");
  const selectedModel = playableModels.find((model) => model.id === modelId);
  const [format, setFormat] = useState("legacy");
  const [query, setQuery] = useState("");
  const [decks, setDecks] = useState<DeckSummary[]>([]);
  const [selectedDeck, setSelectedDeck] = useState<DeckSummary | null>(null);
  const [competitions, setCompetitions] = useState<CompetitionSummary[]>([]);
  const [speed, setSpeed] = useState("1s");
  const [continuous, setContinuous] = useState(false);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState("");
  const blockers = workflowBlockers(status, "matchmaking");
  const competition = competitions.find(
    (item) => item.format.toLowerCase() === format,
  );

  useEffect(() => {
    if (!playableModels.some((model) => model.id === modelId)) {
      setModelId(playableModels[0]?.id ?? "");
      return;
    }
    if (selectedModel && selectedModel.format !== format) {
      setFormat(selectedModel.format);
      setDecks([]);
      setSelectedDeck(null);
    }
  }, [format, modelId, playableModels, selectedModel]);

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
        model_id: selectedModel?.id,
        agent: selectedModel?.architecture,
        speed,
        continuous,
        checkpoint: selectedModel?.checkpointPath,
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
          <input value={format[0].toUpperCase() + format.slice(1)} readOnly />
          <small>Set by the selected local model.</small>
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
              required
              value={modelId}
              onChange={(event) => setModelId(event.target.value)}
            >
              <option value="">Choose one of your models</option>
              {playableModels.map((model) => <option key={model.id} value={model.id}>{model.name} · {model.architecture.toUpperCase()}</option>)}
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
        {playableModels.length === 0 && <div className="notice warning"><strong>No local AI can join yet</strong><span>Start training and wait for your model's first playable checkpoint.</span></div>}
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
            !selectedModel
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
  const [page, setPage] = useState<Page>("train");
  const [status, setStatus] = useState<CapabilityStatus | null>(null);
  const [account, setAccount] = useState<AccountStatus | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [models, setModels] = useState<LocalModel[]>([]);
  const [resources, setResources] = useState<ResourceSnapshot | null>(null);
  const [games, setGames] = useState<ActiveGame[]>([]);
  const [deckStatistics, setDeckStatistics] = useState<DeckStatistic[]>([]);
  const [trainingStatistics, setTrainingStatistics] = useState<TrainingStatistic[]>([]);
  const [loadError, setLoadError] = useState("");
  const [setupOpen, setSetupOpen] = useState(true);
  const [trainingOpen, setTrainingOpen] = useState(true);
  const [creatingTraining, setCreatingTraining] = useState(false);
  const [openedPlaytestJobId, setOpenedPlaytestJobId] = useState("");
  const pageHeading = useRef<HTMLHeadingElement>(null);
  const setupWasResolved = useRef(false);
  const refreshInFlight = useRef(false);

  async function refresh() {
    if (refreshInFlight.current) return;
    refreshInFlight.current = true;
    try {
      const results = await Promise.allSettled([
        loadStatus().then(setStatus),
        loadAccountStatus().then(setAccount),
        loadJobs().then(setJobs),
        loadModels().then(setModels),
        loadResources().then(setResources),
        loadActiveGames().then(setGames),
        loadDeckStatistics().then(setDeckStatistics),
        loadTrainingStatistics().then(setTrainingStatistics),
      ]);
      const failure = results.find(
        (result): result is PromiseRejectedResult => result.status === "rejected",
      );
      setLoadError(
        failure
          ? failure.reason instanceof Error
            ? failure.reason.message
            : "Controller unavailable."
          : "",
      );
    } finally {
      refreshInFlight.current = false;
    }
  }
  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 2000);
    return () => window.clearInterval(timer);
  }, []);
  useEffect(() => {
    pageHeading.current?.focus();
  }, [page]);
  const activityJobs = useMemo(
    () => jobs.filter((job) => !job.kind.startsWith("dependency.")),
    [jobs],
  );
  const userModels = useMemo(
    () => models.filter((model) => model.source !== "local-frozen-checkpoint"),
    [models],
  );
  const running = resources?.workers.length ?? activityJobs.filter((job) => job.status === "running").length;
  const stackReady = Boolean(status?.engine.healthy && status?.pixi.built);
  const activePlaytest = openedPlaytestJobId ? jobs.find(
    (job) =>
      job.id === openedPlaytestJobId &&
      job.kind === "playtest.agent" &&
      job.status === "running" &&
      job.details?.sessionId,
  ) : undefined;
  const accountReady = account?.valid === true;
  const setupReady = Boolean(
    accountReady && status?.engine.synced && status?.pixi.synced,
  );
  useEffect(() => {
    if (setupReady && !setupWasResolved.current) setSetupOpen(false);
    if (status) setupWasResolved.current = setupReady;
  }, [setupReady, status]);
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
      {activePlaytest?.details?.sessionId && (
        <PixiErrorBoundary
          key={activePlaytest.details.sessionId}
          onClose={() => {
            setOpenedPlaytestJobId("");
            void stopJob(activePlaytest.id).then(refresh);
          }}
        >
          <Suspense fallback={<section className="pixi-recovery" role="status"><h2>Opening Pixi…</h2></section>}>
            <LocalPixiTable
              deckVersionIds={[
                activePlaytest.details.playerDeck?.id ?? "",
                activePlaytest.details.opponentDeck?.id ?? "",
              ].filter(Boolean)}
              engineUrl={activePlaytest.details.engineUrl ?? status?.engine.url ?? "http://127.0.0.1:8787"}
              sessionId={activePlaytest.details.sessionId}
              matchup={`${activePlaytest.details.playerDeck?.name ?? "Your deck"} vs ${activePlaytest.details.opponentDeck?.name ?? "AI deck"}`}
              onClose={() => {
                setOpenedPlaytestJobId("");
                void stopJob(activePlaytest.id).then(refresh);
              }}
            />
          </Suspense>
        </PixiErrorBoundary>
      )}
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
            <WorkspaceSummary status={status} account={account} />
            <AccountSetup status={status} account={account} refresh={refresh} />
            {activityJobs.length > 0 && (
              <JobsPanel jobs={activityJobs} refresh={refresh} />
            )}
          </>
        )}
        {page === "train" && (
          <>
            <section className={`flow-section ${setupReady ? "complete" : "attention"}`}>
              <button className="flow-section-toggle" type="button" aria-expanded={setupOpen} onClick={() => setSetupOpen(!setupOpen)}>
                <span><b>1</b><span><strong>Application setup</strong><small>API key, Engine and Pixi</small></span></span>
                <span className="flow-section-state">{setupReady ? "Ready" : "Action required"}<i>{setupOpen ? "−" : "+"}</i></span>
              </button>
              {setupOpen && <div className="flow-section-body"><AccountSetup status={status} account={account} refresh={refresh} /><DependencyPanel status={status} jobs={jobs} refresh={refresh} /></div>}
            </section>

            <section className="flow-section">
              <button className="flow-section-toggle" type="button" aria-expanded={trainingOpen} onClick={() => setTrainingOpen(!trainingOpen)}>
                <span><b>2</b><span><strong>Your agents</strong><small>Review trained agents and past runs</small></span></span>
                <span className="flow-section-state">{userModels.length} agent{userModels.length === 1 ? "" : "s"}<i>{trainingOpen ? "−" : "+"}</i></span>
              </button>
              {trainingOpen && <div className="flow-section-body">
                <div className="agent-toolbar">
                  <div><span className="eyebrow">Your AI</span><h2>Configured agents</h2><p>Identity, architecture, owned weights and the immutable deck pool each agent consumes.</p></div>
                  <button className="primary" type="button" onClick={() => setCreatingTraining(!creatingTraining)}>{creatingTraining ? "Cancel" : "+ New agent"}</button>
                </div>
                <AgentCatalog models={userModels} resources={resources} refresh={refresh} />
                {creatingTraining && <div className="new-training"><LocalTrainingForm status={status} account={account} refresh={refresh} /></div>}
              </div>}
            </section>
          </>
        )}
        {page === "jobs" && <JobsDashboard account={account} models={userModels} resources={resources} jobs={activityJobs} games={games} refresh={refresh} onOpenGame={(game) => { if (game.jobId) { setOpenedPlaytestJobId(game.jobId); setPage("playtest"); } }} />}
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
              <PlaytestForm status={status} models={userModels} refresh={refresh} onStarted={setOpenedPlaytestJobId} />
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
              models={userModels}
              refresh={refresh}
            />
            {activityJobs.length > 0 && (
              <JobsPanel jobs={activityJobs} refresh={refresh} />
            )}
          </>
        )}
        {page === "statistics" && <StatisticsPage jobs={activityJobs} decks={deckStatistics} training={trainingStatistics} />}
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
              <h2>Structured two-player policy</h2>
              <p>
                Legacy architecture with structured observations, encoded legal
                actions, two relative value slots, self-play collection and PPO
                updates. Deep Deck publishes the implementation; training creates
                the user's own local weights.
              </p>
            </article>
            <article className="panel model">
              <span>V11</span>
              <h2>Structured multiplayer policy</h2>
              <p>
                Commander architecture with four multiplayer value slots,
                shared-policy self-play and PPO updates. Its weights are created
                and retained in the user's local workspace.
              </p>
            </article>
          </section>
        )}
      </main>
    </div>
  );
}
