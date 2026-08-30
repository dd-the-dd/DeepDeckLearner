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
  status: 'queued' | 'running' | 'completed' | 'failed';
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  exit_code: number | null;
  artifact_path: string | null;
  logs: string[];
};

export type DeckSummary = {
  id: string;
  name: string;
  creator?: string;
  version: number;
  format?: string;
  colors?: string[];
  playableCardCount?: number;
};

export type CompetitionSummary = {
  versionId: string;
  name: string;
  status: string;
  format: string;
  timeControl: string;
};

export type LocalDeck = { deckSessionId: string; deckName: string };

export type TrainingProfile = {
  model: 'v11' | 'v12';
  format: 'legacy' | 'commander';
  decks: DeckSummary[];
};

export type DeckBundle = {
  id: string;
  name: string;
  description: string;
  format: 'legacy' | 'commander';
  updatedAt: string;
  sources: string[];
  archetypes: Array<{ name: string; queries: string[] }>;
};

export type LocalSession = {
  id: string;
  label: string;
  role: 'owner' | 'lan';
  created_at: string;
};

export type LearnerSettings = {
  network: {
    mode: 'local' | 'lan';
    port: number;
    restart_required: boolean;
    lan_urls: string[];
  };
  account: {
    configured: boolean;
    provider: string | null;
    externally_managed: boolean;
  };
  access: {
    role: 'owner' | 'lan';
  };
};

type Page<T> = { items: T[] };

let sessionToken = '';
const sessionStorageKey = 'deepdeck-learner-session';

async function json<T>(response: Response): Promise<T> {
  const body = await response.json();
  if (!response.ok) {
    throw new Error(body.detail ?? `HTTP ${response.status}`);
  }
  return body as T;
}

async function token(): Promise<string> {
  if (!sessionToken) {
    sessionToken = window.sessionStorage.getItem(sessionStorageKey) ?? '';
  }
  if (!sessionToken) {
    await connectLocalSession();
    sessionToken = window.sessionStorage.getItem(sessionStorageKey) ?? '';
    if (!sessionToken) throw new Error('Unable to open a local session.');
  }
  return sessionToken;
}

async function authorizedHeaders(extra?: HeadersInit): Promise<Headers> {
  const headers = new Headers(extra);
  headers.set('X-DeepDeck-Token', await token());
  return headers;
}

export async function connectLocalSession(): Promise<LocalSession> {
  const existing = sessionToken || window.sessionStorage.getItem(sessionStorageKey) || '';
  const response = await fetch('/api/v1/session', {
    headers: existing ? { 'X-DeepDeck-Token': existing } : undefined,
  });
  if (response.status === 401 || response.status === 403) {
    sessionToken = '';
    window.sessionStorage.removeItem(sessionStorageKey);
    throw new Error('This browser origin is not allowed to open DeepDeckLearner.');
  }
  const connected = await json<{ token: string; session: LocalSession }>(response);
  sessionToken = connected.token;
  window.sessionStorage.setItem(sessionStorageKey, sessionToken);
  return connected.session;
}

export async function loadStatus(): Promise<CapabilityStatus> {
  return json<CapabilityStatus>(await fetch('/api/v1/status', { headers: await authorizedHeaders() }));
}

export async function loadJobs(): Promise<Job[]> {
  return json<Job[]>(await fetch('/api/v1/jobs', { headers: await authorizedHeaders() }));
}

export async function searchDecks(search: string, format: string): Promise<DeckSummary[]> {
  const query = new URLSearchParams({ search, format });
  return (await json<Page<DeckSummary>>(await fetch(`/api/v1/catalog/decks?${query}`, { headers: await authorizedHeaders() }))).items;
}

export async function loadCompetitions(): Promise<CompetitionSummary[]> {
  return (await json<Page<CompetitionSummary>>(await fetch('/api/v1/catalog/competitions', { headers: await authorizedHeaders() }))).items;
}

export async function loadLocalDecks(format: string, engineUrl: string): Promise<LocalDeck[]> {
  const query = new URLSearchParams({ format, engine_url: engineUrl });
  return json<LocalDeck[]>(await fetch(`/api/v1/catalog/local-decks?${query}`, { headers: await authorizedHeaders() }));
}

export async function startJob(payload: Record<string, unknown>): Promise<Job> {
  return json<Job>(
    await fetch('/api/v1/jobs', {
      method: 'POST',
      headers: await authorizedHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(payload),
    }),
  );
}

export async function stopJob(jobId: string): Promise<Job> {
  return json<Job>(
    await fetch(`/api/v1/jobs/${jobId}/stop`, {
      method: 'POST',
      headers: await authorizedHeaders(),
    }),
  );
}

export async function loadDeckBundles(format: string): Promise<DeckBundle[]> {
  const query = new URLSearchParams({ format });
  return (await json<Page<DeckBundle>>(
    await fetch(`/api/v1/catalog/deck-bundles?${query}`, { headers: await authorizedHeaders() }),
  )).items;
}

export async function loadTrainingProfile(): Promise<TrainingProfile> {
  return json<TrainingProfile>(
    await fetch('/api/v1/training-profile', { headers: await authorizedHeaders() }),
  );
}

export async function saveTrainingProfile(profile: TrainingProfile): Promise<TrainingProfile> {
  return json<TrainingProfile>(
    await fetch('/api/v1/training-profile', {
      method: 'PUT',
      headers: await authorizedHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(profile),
    }),
  );
}

export async function loadSettings(): Promise<LearnerSettings> {
  return json<LearnerSettings>(
    await fetch('/api/v1/settings', { headers: await authorizedHeaders() }),
  );
}

export async function saveApiKey(apiKey: string): Promise<void> {
  await json(
    await fetch('/api/v1/settings/api-key', {
      method: 'PUT',
      headers: await authorizedHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ api_key: apiKey }),
    }),
  );
}

export async function deleteApiKey(): Promise<void> {
  await json(
    await fetch('/api/v1/settings/api-key', {
      method: 'DELETE',
      headers: await authorizedHeaders(),
    }),
  );
}

export async function saveNetworkSettings(mode: 'local' | 'lan', port: number): Promise<boolean> {
  const result = await json<{ restart_required: boolean }>(
    await fetch('/api/v1/settings/network', {
      method: 'PUT',
      headers: await authorizedHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ mode, port }),
    }),
  );
  return result.restart_required;
}

export async function restartWorkbench(): Promise<void> {
  await json(
    await fetch('/api/v1/settings/restart', {
      method: 'POST',
      headers: await authorizedHeaders(),
    }),
  );
}
