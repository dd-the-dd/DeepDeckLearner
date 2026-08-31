export type CapabilityStatus = {
  controller: { ready: boolean; version: string };
  paths: { project: string; trajectory: string; checkpoints: string };
  sdk: { ready: boolean };
  torch: { ready: boolean };
  engine: { source_available: boolean; revision: string | null; pinned_revision: string | null; synced: boolean; dirty: boolean; built: boolean; url: string; healthy: boolean };
  pixi: { source_available: boolean; built: boolean; build_present: boolean; built_revision: string | null; revision: string | null; pinned_revision: string | null; synced: boolean; dirty: boolean };
  hosted: { api_key_configured: boolean; trajectory_training: boolean; reason: string };
  workflows: Record<string, boolean>;
};

export type Job = {
  id: string;
  kind: string;
  label: string;
  argv: string[];
  status: 'queued' | 'running' | 'completed' | 'failed' | 'stopped';
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  exit_code: number | null;
  artifact_path: string | null;
  logs: string[];
  model_id?: string | null;
  worker_slots?: number;
  details?: {
    engineUrl?: string;
    sessionId?: string;
    selectionOrder?: string;
    playerDeck?: { id: string; name: string };
    opponentDeck?: { id: string; name: string };
  } | null;
};

export type DeckPresentation = {
  versionId: string;
  name: string;
  cards: Array<Record<string, unknown>>;
};

export type DeckSummary = {
  id: string;
  name: string;
  creator?: string;
  version: number;
  format?: string;
  colors: string[];
  playableCardCount: number;
};

export type CompetitionSummary = {
  versionId: string;
  name: string;
  status: string;
  format: string;
  timeControl: string;
};

export type LocalDeck = { deckSessionId: string; deckName: string };

export type LocalModel = {
  id: string;
  name: string;
  architecture: "v11" | "v12";
  format: "legacy" | "commander";
  description: string;
  createdAt: string;
  runPath: string;
  checkpointPath: string;
  status: string;
  ready: boolean;
  reservePlaytest: boolean;
  decks: DeckSummary[];
};

export type ResourcePlan = {
  trainingMatches: number;
  leagueMatches: number;
  localMatches: number;
  gpuMemoryMb: number;
};

export type ResourceWorker = {
  jobId: string;
  modelId: string | null;
  label: string;
  kind: string;
  pids: number[];
  workerSlots: number;
  ramBytes: number;
  gpuBytes: number | null;
  ramPerWorkerEstimate: number;
  gpuPerWorkerEstimate: number | null;
};

export type ResourceSnapshot = {
  system: {
    ramTotalBytes: number;
    ramUsedBytes: number;
    ramAvailableBytes: number;
    gpuTotalBytes: number | null;
    gpuUsedBytes: number | null;
    gpuProcessTelemetry: boolean;
  };
  workers: ResourceWorker[];
  engine: {
    ramBytes: number;
    activeLocalGames: number;
    ramPerGameEstimate: number;
    attribution: string;
  };
};

export type DeckStatistic = {
  modelId: string;
  modelName: string;
  architecture: "v11" | "v12";
  deckVersionId: string;
  deckName: string;
  format: string;
  ratingSystem: "plackett-luce";
  mu: number;
  sigma: number;
  ordinal: number;
  rank: number | null;
  matches: number;
  gameWins: number;
  gameLosses: number;
  winRate: number | null;
};

type Page<T> = { items: T[] };

let sessionToken = '';

async function json<T>(response: Response): Promise<T> {
  const body = await response.json();
  if (!response.ok) {
    throw new Error(body.detail ?? `HTTP ${response.status}`);
  }
  return body as T;
}

async function token(): Promise<string> {
  if (!sessionToken) {
    sessionToken = (await json<{ token: string }>(await fetch('/api/v1/session'))).token;
  }
  return sessionToken;
}

export async function loadStatus(): Promise<CapabilityStatus> {
  return json<CapabilityStatus>(await fetch('/api/v1/status'));
}

export async function loadJobs(): Promise<Job[]> {
  return json<Job[]>(await fetch('/api/v1/jobs'));
}

export async function loadModels(): Promise<LocalModel[]> {
  return (await json<Page<LocalModel>>(await fetch('/api/v1/models'))).items;
}

export async function loadResources(): Promise<ResourceSnapshot> {
  return json<ResourceSnapshot>(await fetch('/api/v1/resources'));
}

export async function loadModelResources(modelId: string): Promise<ResourcePlan> {
  return json<ResourcePlan>(
    await fetch(`/api/v1/models/${encodeURIComponent(modelId)}/resources`),
  );
}

export async function saveModelResources(
  modelId: string,
  plan: ResourcePlan,
): Promise<ResourcePlan> {
  return json<ResourcePlan>(
    await fetch(`/api/v1/models/${encodeURIComponent(modelId)}/resources`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', 'X-DeepDeck-Token': await token() },
      body: JSON.stringify(plan),
    }),
  );
}

export async function loadDeckStatistics(): Promise<DeckStatistic[]> {
  return (await json<Page<DeckStatistic>>(await fetch('/api/v1/statistics/decks'))).items;
}

export async function saveApiKey(apiKey: string): Promise<{ configured: boolean }> {
  return json<{ configured: boolean }>(
    await fetch('/api/v1/settings/api-key', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-DeepDeck-Token': await token() },
      body: JSON.stringify({ api_key: apiKey }),
    }),
  );
}

export async function searchDecks(search: string, format: string): Promise<DeckSummary[]> {
  const query = new URLSearchParams({ search, format });
  return (await json<Page<DeckSummary>>(await fetch(`/api/v1/catalog/decks?${query}`))).items;
}

export async function downloadDeck(versionId: string): Promise<{ versionId: string; name: string; format: string; cardCount: number; path: string }> {
  return json(await fetch(`/api/v1/catalog/decks/${encodeURIComponent(versionId)}/download`, {
    method: 'POST',
    headers: { 'X-DeepDeck-Token': await token() },
  }));
}

export async function loadTrainingDeckPool(): Promise<{ decks: DeckSummary[] }> {
  return json(await fetch('/api/v1/training/deck-pool'));
}

export async function saveTrainingDeckPool(decks: DeckSummary[]): Promise<{ decks: DeckSummary[] }> {
  return json(await fetch('/api/v1/training/deck-pool', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', 'X-DeepDeck-Token': await token() },
    body: JSON.stringify({ decks }),
  }));
}

export async function loadCompetitions(): Promise<CompetitionSummary[]> {
  return (await json<Page<CompetitionSummary>>(await fetch('/api/v1/catalog/competitions'))).items;
}

export async function loadLocalDecks(format: string, engineUrl: string): Promise<LocalDeck[]> {
  const query = new URLSearchParams({ format, engine_url: engineUrl });
  return json<LocalDeck[]>(await fetch(`/api/v1/catalog/local-decks?${query}`));
}

export async function startJob(payload: Record<string, unknown>): Promise<Job> {
  return json<Job>(
    await fetch('/api/v1/jobs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-DeepDeck-Token': await token() },
      body: JSON.stringify(payload),
    }),
  );
}

export async function stopJob(jobId: string): Promise<Job> {
  return json<Job>(
    await fetch(`/api/v1/jobs/${jobId}/stop`, {
      method: 'POST',
      headers: { 'X-DeepDeck-Token': await token() },
    }),
  );
}
