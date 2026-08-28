export type CapabilityStatus = {
  controller: { ready: boolean; version: string };
  sdk: { ready: boolean };
  torch: { ready: boolean };
  engine: { source_available: boolean; revision: string | null; url: string; healthy: boolean };
  pixi: { source_available: boolean; built: boolean; revision: string | null };
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

export async function searchDecks(search: string, format: string): Promise<DeckSummary[]> {
  const query = new URLSearchParams({ search, format });
  return (await json<Page<DeckSummary>>(await fetch(`/api/v1/catalog/decks?${query}`))).items;
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
