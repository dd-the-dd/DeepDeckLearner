import { readAssistantStream } from './assistant-stream';

export type CapabilityStatus = {
  controller: { ready: boolean; version: string };
  paths: { project: string; trajectory: string; checkpoints: string };
  sdk: { ready: boolean };
  torch: { ready: boolean };
  engine: { source_available: boolean; revision: string | null; pinned_revision: string | null; tracked_branch?: string | null; target_revision?: string | null; synced: boolean; dirty: boolean; built: boolean; url: string; healthy: boolean };
  pixi: { source_available: boolean; built: boolean; build_present: boolean; built_revision: string | null; revision: string | null; pinned_revision: string | null; tracked_branch?: string | null; target_revision?: string | null; synced: boolean; dirty: boolean };
  hosted: { api_key_configured: boolean; trajectory_training: boolean; reason: string };
  workflows: Record<string, boolean>;
};

export type AccountStatus = {
  configured: boolean;
  valid: boolean | null;
  reason: string;
};

export type LeagueAccount = AccountStatus & {
  user?: { id: string; displayName?: string; username?: string; email?: string } | null;
  agentKeyConfigured?: boolean;
};

export type LeagueConnection = {
  apiUrl: string;
  siteUrl: string;
  environment: 'production' | 'staging' | 'custom';
  reachable: boolean;
  releaseProfile: string | null;
  capabilities: string[];
  naturalCardSearchEnabled: boolean;
  reason: string;
};

export type DeckSyncStatus = {
  linked: boolean;
  workspaceId: string | null;
  publishedVersionId: string | null;
  needsUpload: boolean;
  needsPublishing: boolean;
  syncedAt: string | null;
};

export type ServerDeckWorkspace = {
  id: string;
  name: string;
  description?: string;
  latestVersionId?: string;
  version?: number;
  hasUnpublishedChanges: boolean;
  state: { format?: string; cards?: Array<{ quantity?: number }> };
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
    sessionStatus?: 'playing' | 'finished';
    currentMatchIndex?: number;
    deckNames?: string[];
    participantNames?: string[];
    humanPlayerIds?: string[];
    deckVersionIds?: string[];
    matchup?: string;
    batchId?: string;
    replayId?: string | null;
    recordingStatus?: 'complete' | 'completed' | 'gameOver' | 'error' | 'timeout' | 'interrupted';
    agents?: string[];
    seats?: Array<{agentId: string}>;
    selectionOrder?: string;
    playerDeck?: { id: string; name: string };
    opponentDeck?: { id: string; name: string };
    completed?: number;
    total?: number;
    replaySync?: { enabled: boolean; collected: number; pending: number; lastError: string | null; checkedAtUnixMs: number };
    leagueRun?: {completedEntries:number;activeEntries:number;maxMatches:number|null;durationSeconds:number|null;serverRegistered?:boolean;registrationGeneration?:number;lastError?:string|null};
  } | null;
};

export type DeckPresentation = {
  versionId: string;
  name: string;
  cards: Array<Record<string, unknown>>;
};

export type DeckCover = { name: string; cardId?: string | null; imageUrl: string; reason?: string };

export type DeckSummary = {
  tags?: string[];
  collections?: string[];
  id: string;
  deckId?: string;
  name: string;
  creator?: string;
  version: number;
  format?: string;
  colors: string[];
  playableCardCount: number;
  cached?: boolean;
  unavailable?: boolean;
  coverCard?: DeckCover | null;
  syncStatus?: DeckSyncStatus;
};

export type DeckWorkspaceCard = {
  isToken?: boolean;
  isGamePiece?: boolean;
  entryId?: string;
  cardId?: string;
  scryfallId?: string;
  oracleId?: string | null;
  name: string;
  quantity: number;
  section: 'main' | 'sideboard' | 'considering' | 'commander' | 'companion';
  tags: string[];
  typeLine?: string;
  manaCost?: string;
  manaValue?: number;
  imageUri?: string;
  colorIdentity?: string[];
};

export type DeckWorkspace = {
  tags?: string[];
  collections?: string[];
  schemaVersion: 'deepdeck-local-deck/v1';
  id: string;
  name: string;
  description: string;
  format: 'legacy' | 'commander';
  version: number;
  cards: DeckWorkspaceCard[];
  combos: Array<Record<string, unknown>>;
  openingHands: Array<Record<string, unknown>>;
  serverState?: Record<string,unknown>;
  origin: { kind: 'local' | 'league'; versionId?: string };
  createdAt: string;
  updatedAt: string;
  syncStatus?: DeckSyncStatus;
};

export type DeckWorkspaceSummary = DeckSummary & {
  description: string;
  readyForPlay: boolean;
  workspace: true;
  sectionCounts: Record<string, number>;
  updatedAt: string;
};

export type ExperienceSettings = {
  replayStorageError?: string | null;
  replayStorageLimitBytes: number;
  replayStorageUsedBytes: number;
  replayPollSeconds: number;
  autoCollectLeagueReplays: boolean;
};

export type LeagueCapacity = {
  status: 'ready' | 'unavailable' | 'unsupported';
  limit: number | null;
  used: number | null;
  available: number | null;
  reason: string;
};

export async function loadLeagueCapacity(): Promise<LeagueCapacity> {
  return json<LeagueCapacity>(await fetch('/api/v1/league-capacity'));
}

export type LeagueLease = {mode:'continuous'} | {mode:'matches';matches:number} | {mode:'duration';minutes:number};
export type LeagueQueueEntry = {
  id:string;agentId:string;name:string;format:string;competitionVersionId:string;deckVersionIds:string[];lease:LeagueLease;
  completedMatches:number;elapsedSeconds:number;state:'queued'|'playing'|'blocked'|'done';error:string|null;
  turn:{dispatchId:string;sequence:number;jobId:string|null;createdAt:string}|null;
};
export type LeagueTurnSummary = {
  dispatchId:string;name:string;finishedAt?:string;jobId?:string|null;error:string|null;
  result:{completedEntries:number;elapsedSeconds:number;outcome:string|null;matchId:string|null}|null;
};
export type ReplayInboxItem={modelId:string;name:string;available:boolean;collected:number;pending:number;currentServerPending:number;otherServerPending:number;unknownServerPending:number;lastError:string|null;lastAttemptAtUnixMs:number|null};
export type ReplayInbox={items:ReplayInboxItem[];observedAtUnixMs:number};
export async function loadReplayInbox():Promise<ReplayInbox>{
  return json<ReplayInbox>(await fetch('/api/v1/replay-inbox'));
}
export async function recoverReplayInbox(modelId:string):Promise<Job>{
  return json<Job>(await authorizedFetch(`/api/v1/replay-inbox/${encodeURIComponent(modelId)}/collect`,{method:'POST'}));
}
export type LeagueQueue = {
  id:string;revision:number;status:'paused'|'running'|'pausing';strategy:'round_robin'|'random'|'respect_format';
  formatSeats?:{legacy:number;commander:number};
  entries:LeagueQueueEntry[];message:string|null;capacity:(LeagueCapacity & {checkedAt:string})|null;
  controllerError?:string;
  history:LeagueTurnSummary[];
};
export async function loadLeagueQueue():Promise<LeagueQueue> {
  return json<LeagueQueue>(await fetch('/api/v1/league-queue'));
}
export type LeagueQueueEntryDraft=Pick<LeagueQueueEntry,'agentId'|'competitionVersionId'|'deckVersionIds'|'lease'>;
export async function appendLeagueQueueEntry(revision:number,entry:LeagueQueueEntryDraft):Promise<LeagueQueue> {
  return json<LeagueQueue>(await authorizedFetch('/api/v1/league-queue/entries',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({revision,entry})}));
}
export async function removeLeagueQueueEntry(entryId:string,revision:number):Promise<LeagueQueue> {
  return json<LeagueQueue>(await authorizedFetch(`/api/v1/league-queue/entries/${encodeURIComponent(entryId)}/remove`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({revision})}));
}
export async function saveLeagueQueue(queue:Pick<LeagueQueue,'revision'|'strategy'|'formatSeats'> & {entries:Array<Pick<LeagueQueueEntry,'agentId'|'competitionVersionId'|'deckVersionIds'|'lease'>>}):Promise<LeagueQueue> {
  return json<LeagueQueue>(await authorizedFetch('/api/v1/league-queue',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(queue)}));
}
export async function controlLeagueQueue(action:'pause'|'resume'|'retry',revision:number,entryId?:string):Promise<LeagueQueue> {
  return json<LeagueQueue>(await authorizedFetch(`/api/v1/league-queue/${action}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({revision,entryId})}));
}

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
  architecture: string;
  format: "legacy" | "commander";
  description: string;
  createdAt: string;
  runPath: string;
  checkpointPath: string;
  status: string;
  ready: boolean;
  reservePlaytest: boolean;
  source?: "user-trained" | "local-frozen-checkpoint" | "local-programmatic-agent";
  selfPlayAllSeats?: boolean;
  decks: DeckSummary[];
  diskBytes: number;
  weightsBytes: number;
  trainingState: {
    phase?: string | null;
    desiredState?: string | null;
    completedGames: number;
    trainingStep: number;
    parallelGames: number;
    activeGames: number;
    updatedAtUnixMs?: number | null;
  };
};

export type ActiveGame = {
  id: string;
  sessionId: string | null;
  source: 'training' | 'local' | 'league';
  jobId: string | null;
  modelId: string | null;
  modelName: string;
  worker: number;
  status: string;
  mode: string | null;
  decks: Array<string | null>;
  players: number;
  playersState: Array<{
    id?: string;
    name?: string;
    life?: number;
    hasLost?: boolean;
    handCount?: number;
    battlefieldCount?: number;
  }>;
  turnNumber: number | null;
  roundNumber: number | null;
  decisions: number;
  startedAtUnixMs: number | null;
  updatedAtUnixMs: number | null;
  canCancel: boolean;
  watchUrl?: string | null;
};

export type ProgrammaticAgent = {
  tags?: string[];
  collections?: string[];
  schemaVersion: 'deepdeck-programmatic-agent/v1';
  id: string;
  name: string;
  description: string;
  format: 'legacy' | 'commander';
  profile: 'cautious' | 'balanced' | 'aggressive';
  deckVersionIds: string[];
  decks: DeckSummary[];
  heuristics: Record<string, number>;
  strategyGraph: {
    version: string;
    archetypeMix: Record<'aggro' | 'tempo' | 'midrange' | 'control' | 'combo', number>;
    deckAdaptation: number;
    weights: Record<string, number>;
    temperature: number;
    seed: number;
    customNodes?: AgentDecisionNode[];
    patterns: Array<{
      id: string;
      cardNames: string[];
      textContains: string[];
      requireTags: string[];
      addTags: string[];
      nodeAdjustments: Record<string, number>;
    }>;
  };
  cardPreferences: {
    prefer: string[];
    avoid: string[];
    holdForInteraction: string[];
  };
  strategyNotes: string;
  revisionReason: string;
  revision: number;
  createdAt: string;
  updatedAt: string;
};

export type AgentDecisionNode = {
  id: string; name: string; enabled: boolean; weight: number; bias: number;
  when: { actionKinds: string[]; sourceCards: string[]; sourceTags: string[]; targetCards: string[]; targetTags: string[]; turn: string; role: string; targetController: string };
  inputs: Array<{ from: string; weight: number }>;
};

export type HeuristicCatalog = {
  schemaVersion: 'deepdeck-heuristic-catalog/v2';
  modelVersion: string;
  weightRange: { minimum: number; maximum: number; default: number };
  temperatureRange: { minimum: number; maximum: number; default: number };
  deckAdaptationRange: { minimum: number; maximum: number; default: number };
  archetypes: string[];
  profiles: string[];
  postureWeights?: Record<string, Record<string, number>>;
  items: Array<{ id: string; name: string; description: string }>;
  customNodeSignals?: Array<{id: string; name: string}>;
  customNodeActionKinds?: string[];
};

export type AgentRevision = { revision: number; name: string; createdAt: string; reason: string; profile: string; deckCount: number; patternCount: number; current: boolean; hasPythonSnapshot: boolean };

export type LlmSettings = {
  providerName: string;
  baseUrl: string;
  model: string;
  hasApiKey: boolean;
  configured: boolean;
  leagueMcpEnabled?: boolean;
  providerId?: string;
  authMode?: 'apiKey' | 'chatgpt';
  providers?: LlmProvider[];
  chatgpt?: ChatGPTAccounts;
};

export type LlmProvider = { id: string; name: string; baseUrl: string; auth: string; note: string; docs: string };
export type ChatGPTAccounts = { accounts: Array<{ id: string; label: string; connected: boolean; registration: string }>; activeAccountId: string | null; usageUrl: string; pendingFlowId?: string | null; revocationConfirmed?: boolean };
export async function chatGPTAction(action: 'start' | 'cancel' | 'select' | 'disconnect', payload: Record<string, string> = {}): Promise<ChatGPTAccounts & { flowId?: string; status?: string }> {
  return json(await authorizedFetch(`/api/v1/agent-studio/chatgpt/${action}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }));
}
export async function loadChatGPTAccounts(): Promise<ChatGPTAccounts> {
  return json(await authorizedFetch('/api/v1/agent-studio/chatgpt'));
}
export async function loadChatGPTModels(): Promise<{ items: Array<{ id: string; name: string }> }> {
  return json(await authorizedFetch('/api/v1/agent-studio/chatgpt/models'));
}
export async function loadProviderModels(payload: Record<string, unknown>): Promise<{ items: Array<{ id: string; name: string }> }> {
  return json(await authorizedFetch('/api/v1/agent-studio/llm-models', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }));
}
export async function loadChatGPTFlow(flowId: string): Promise<{ status: string }> {
  return json(await authorizedFetch(`/api/v1/agent-studio/chatgpt/flows/${encodeURIComponent(flowId)}`));
}

export type AssistantToolEvent = { tool: string; ok: boolean; error?: string; errorCode?: string; httpStatus?: number; result?: unknown; arguments?: Record<string, unknown>; transport?: 'mcp' };
export type ResearchCard = { name: string; scryfallId?: string; typeLine?: string; type_line?: string; oracleText?: string; oracle_text?: string; manaCost?: string; mana_cost?: string; imageUri?: string; imageUrl?: string; image_uris?: { normal?: string } };
export type CardSearchLocation = { page: number; page_offset: number };
export type CardResearch = { items: ResearchCard[]; query: string; source: string; omittedItems?: number; pagination?: { page: number; totalItems?: number; totalPages?: number }; hasMore?: boolean;
  resultWindow?: {page: number; pageOffset: number; shown: number; totalOnPage: number; next: CardSearchLocation | null; navigationLimitReached: boolean};
};

export type TrainingStatistic = {
  modelId: string;
  modelName: string;
  architecture: 'v11' | 'v12' | 'v13';
  format: string;
  completedGames: number;
  totalRLEpisodes?: number;
  engineCompletedGames?: number;
  trainingStep: number;
  parallelGames: number;
  activeGames: number;
  phase: string;
  desiredState: string;
  trainingElapsedSeconds: number;
  simulationSeconds: number;
  modelTrainingSeconds: number;
  averageGameSeconds: number | null;
  metricWindow: '50' | '200' | '1000' | '5000' | 'all';
  metricRecordCount: number;
  metricPointsReturned: number;
  windowSummary: {
    sampleType: 'games' | 'rl-episodes' | 'synthetic-batches';
    gameMetricsAvailable: boolean;
    engineGameMetrics: boolean;
    attemptedSamples?: number;
    generatedSamples: number;
    usedSamples: number;
    failedSamples: number;
    utilizationRate: number | null;
    coveredTrainingSteps: number;
    generatedDecisions: number;
    usedDecisions: number;
    averageGameSeconds: number | null;
    p50GameSeconds: number | null;
    p95GameSeconds: number | null;
    simulationSeconds: number | null;
    trainingSeconds: number | null;
    collectionWallSeconds: number | null;
    gamesPerHour: number | null;
  };
  latestMetrics: Array<{
    episode: number;
    trainingStep: number;
    loss: number | null;
    policyLoss: number | null;
    valueLoss: number | null;
    entropy: number | null;
    episodeReward: number | null;
    approxKl: number | null;
    clipFraction: number | null;
    dataSource: string | null;
    losses?: Record<string, number>;
    gameDurationSeconds: number | null;
  }>;
  updatedAtUnixMs: number | null;
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
  architecture: "v11" | "v12" | "v13" | "v15" | "programmatic";
  deckVersionId: string;
  deckName: string;
  format: string;
  ratingSystem: "plackett-luce" | "engine-self-play" | "local-results";
  mu: number;
  sigma: number;
  ordinal: number;
  rank: number | null;
  matches: number;
  gameWins: number;
  gameLosses: number;
  draws?: number;
  winRate: number | null;
};

export type TrainingSettings = {
  schemaVersion: 'training-platform/v1';
  modelId: string;
  architecture: string;
  savedGameLimit: number;
  checkpointEvery: number;
  evaluationEvery: number;
  evaluationGamesPerScenario: number;
  stages: {
    worldModel: boolean;
    reinforcementLearning: boolean;
    engineEvaluation: boolean;
  };
  targets: {
    worldModelSteps: number;
    reinforcementLearningEpisodes: number;
  };
  curriculum: {
    enabled: boolean;
    adaptive: boolean;
    scenarioIds: string[];
  };
  engineEvaluationSupported: boolean;
  requiresRestart: boolean;
};

export type CurriculumScenario = {
  id: string;
  family: string;
  revision: number;
  label: string;
  description: string;
  difficulty: number;
  objective: string;
  target_round: number | null;
  opening_hand_pool_size: number | null;
  opening_hand_candidate_cards: string[];
  opening_hand_roles: string[][];
  free_casts: boolean;
  miracle_pressure: number;
  legacy_match: boolean;
  deck_hint: string | null;
  opponent_deck_hint: string | null;
  fixed_hand: string[];
  success_cards: string[];
  success_zones: string[];
  success_action_sequence: string[];
  sideboard_target_cards: string[];
  sideboard_cut_cards: string[];
  tags: string[];
};

export type TrainingCurriculum = {
  schemaVersion: 'deepdeck-training-curriculum/v1';
  scenarios: CurriculumScenario[];
};

export type TrainingEvidence = {
  modelId: string;
  status: 'not-connected' | 'not-evaluated' | 'insufficient' | 'measured' | 'verified';
  verdict: string;
  engineEvaluationSupported: boolean;
  magicEvidence: boolean;
  fixedSeeds: boolean;
  evaluationPeriods: number;
  games: number;
  wins: number;
  losses: number;
  draws: number;
  winRate: number | null;
  lower95: number | null;
  upper95: number | null;
  promotions: number;
  trend: Array<{
    period: number;
    trainingStep: number;
    opponent: string | null;
    games: number;
    winRate: number | null;
    lower95: number | null;
    upper95: number | null;
    promoted: boolean;
  }>;
  byDeck: Array<{
    deck: string;
    games: number;
    wins: number;
    losses: number;
    draws: number;
    winRate: number | null;
    lower95: number | null;
    upper95: number | null;
  }>;
  curriculum: {
    games: number;
    evaluationPeriods: number;
    evaluationByScenario: Array<{
      scenarioId: string;
      games: number;
      successes: number;
      successRate: number;
      meanMastery: number;
    }>;
    latestEvaluation: {
      expectedScenarios: number;
      completedScenarios: number;
      successes: number;
      successRate: number;
      meanMastery: number;
      failedScenarios: number;
      recoveredScenarios?: number;
      engineErrors?: number;
    } | null;
    byScenario: Array<{
      scenarioId: string;
      family: string;
      scenarioVersion: number;
      objective: string;
      difficulty: number | null;
      games: number;
      successes: number;
      successRate: number | null;
      averageRound: number | null;
      averageDecisions: number | null;
      averageMilestoneProgress: number | null;
      averageDamageProgress: number | null;
      averageSideboardCards: number | null;
      sideboardTargetCoverage: number | null;
      sideboardCutCoverage: number | null;
      recent: boolean[];
      adaptiveWeight: number | null;
    }>;
  };
};

export type SavedReplaySummary = {
  id: string;
  source?: string;
  createdAtUnixMs: number | null;
  episode: number | null;
  seed: number | null;
  matchupId: string | null;
  decks: string[];
  deckNames?: string[];
  participants?: Array<{ name: string; playerId?: string; kind?: string; agentId?: string; deckId?: string }>;
  gameMode: string | null;
  gameStatus: string | null;
  roundNumber: number | null;
  matchSummary?: ReplayMatchSummary;
  winner: string | null;
  durationSeconds: number | null;
  frameCount: number;
  bytes: number;
  saved: boolean;
  viewing: boolean;
};

export type ReplayMatchSummary = {
  version: string; scope: 'match' | 'game'; winsByPlayerId: Record<string, number> | null;
  playerIds: string[]; gamesToWin: number | null;
  roundTurns: Array<{ round: number; turns: number; complete: boolean }>;
  totalTurns: number | null; turnsExact: boolean;
};

export type LocalPerformance = {
  id: string;
  name: string;
  matches: number;
  wins: number;
  losses: number;
  draws: number;
  winRate: number;
  confidence95: [number, number] | null;
  decks: number;
};

export type LocalStatistics = {
  days: number;
  summary: { matches: number; seatResults: number; agents: number; players: number; decks: number };
  agents: LocalPerformance[];
  decks: LocalPerformance[];
  players: LocalPerformance[];
  recentMatches: Array<{ id: string; createdAtUnixMs: number | null; format: string; winner: string | null; participants: Array<{ name: string; deck: string; result: string }> }>;
};

export async function loadLocalStatistics(days = 0): Promise<LocalStatistics> {
  return json<LocalStatistics>(await fetch(`/api/v1/statistics/local?days=${days}`));
}

export type TrackingRule = {kind: 'agent' | 'deck' | 'player'; id: string; groups: string[]};
export type StatisticsStorage = {
  storageLimitBytes: number; usedBytes: number; legacyBytes: number; matchFiles: number;
  legacyEngineBytes?: number; engineAnalyticsDisabled?: boolean | null;
  rules: TrackingRule[]; groups: Array<{id: string; name: string; description: string}>;
  metrics: Record<string, string>; lastError: string | null; quotaScope: string; removedMatches?: number;
};
export type AdvancedStatistics = {
  kind: string; id: string; seatResults: number; trackedSeatResults: number;
  totals: Record<string, {sum: number; mean: number; samples: number}>;
  snapshots: Record<string, {mean: number; samples: number}>;
  curve: Array<{turn: number; values: Record<string, {mean: number; samples: number}>}>;
  droppedTurnSamples: number; note: string;
};
export async function loadStatisticsStorage(): Promise<StatisticsStorage> {
  return json<StatisticsStorage>(await fetch('/api/v1/statistics/storage'));
}
export async function saveStatisticsStorage(payload: {storageLimitBytes: number; rules: TrackingRule[]}): Promise<StatisticsStorage> {
  return json<StatisticsStorage>(await authorizedFetch('/api/v1/statistics/storage', {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)}));
}
export async function loadAdvancedStatistics(kind: string, identity: string, days: number): Promise<AdvancedStatistics> {
  return json<AdvancedStatistics>(await fetch(`/api/v1/statistics/advanced?${new URLSearchParams({kind, identity, days:String(days)})}`));
}

export type SavedReplay = {
  schemaVersion: 'deepdeck-replay/v1';
  id: string;
  createdAtUnixMs: number;
  metadata: Record<string, unknown>;
  cardCatalog?: Record<string, unknown>;
  frames: Array<Record<string, unknown>>;
};

export type AgentTrainingParameter = {
  key: string;
  label: string;
  kind: 'integer' | 'number' | 'boolean' | 'string' | 'choice';
  default: string | number | boolean | null;
  description: string;
  min: number | null;
  max: number | null;
  step: number | null;
  choices: Array<string | number | boolean | null>;
  apply: 'live' | 'next-phase' | 'next-run';
};

export type AgentTrainingContract = {
  schemaVersion: 'deepdeck-agent-training/v1';
  agentId: string;
  displayName: string;
  ownsCurriculum: boolean;
  ownsPromotionCriteria: boolean;
  proposesExperiments: boolean;
  replaySupport: 'none' | 'summary' | 'full';
  phases: Array<{
    id: string;
    label: string;
    description: string;
    controls: Array<'start' | 'pause' | 'resume' | 'stop' | 'evaluate' | 'checkpoint'>;
    metrics: Array<{
      key: string;
      label: string;
      kind: 'loss' | 'reward' | 'rate' | 'count' | 'duration' | 'scalar';
      direction: 'minimize' | 'maximize' | 'target' | 'neutral';
      unit: string;
      description: string;
      group: string;
    }>;
    parameters: AgentTrainingParameter[];
  }>;
};

export type AgentTrainingPublication = {
  contract: AgentTrainingContract;
  state: Record<string, unknown> | null;
  control: Record<string, unknown> | null;
  parameterValues: Record<string, string | number | boolean | null>;
  metrics: Array<{
    schemaVersion: string;
    agentId: string;
    phase: string;
    step: number;
    metrics: Record<string, number>;
    recordedAtUnixMs: number | null;
    context?: Record<string, unknown>;
  }>;
};

type Page<T> = {
  items: T[];
  offline?: boolean;
  pagination?: {
    page?: number;
    totalPages?: number;
    total_pages?: number;
    hasNextPage?: boolean;
    has_next_page?: boolean;
  };
};

let sessionToken = '';

export class ApiError extends Error {
  constructor(message: string, readonly status: number, readonly code?: string) { super(message); this.name = 'ApiError'; }
}

async function json<T>(response: Response): Promise<T> {
  const body = await response.json();
  if (!response.ok) {
    const detail=body.detail;
    const reference=response.headers.get('x-request-id');
    const suffix=reference && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(reference) ? ` Reference: ${reference}.` : '';
    throw new ApiError((typeof detail==='string'?detail:detail?.message ?? `HTTP ${response.status}`)+suffix, response.status,detail?.code);
  }
  return body as T;
}

export type LocalDiagnosticReport = {
  localOnly: true; storedEntries: number; usedBytes: number;
  limits: { maxEntries: number; databaseBytes: number; retentionDays: number };
  items: Array<{ reference: string; createdAtUnix: number; method: string; route: string; status: number; durationMs: number; category: string }>;
};

export async function loadLocalDiagnostics(errorsOnly = true): Promise<LocalDiagnosticReport> {
  return json(await authorizedFetch(`/api/v1/diagnostics?errors_only=${errorsOnly}&limit=50`, { cache: 'no-store' }));
}

export async function reportLocalRenderFailure(): Promise<{ reference: string; recorded: boolean }> {
  return json(await authorizedFetch('/api/v1/diagnostics/ui', { method: 'POST',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ kind: 'render_error' }),
    signal: AbortSignal.timeout(5000) }));
}

async function token(forceRefresh = false): Promise<string> {
  if (forceRefresh) {
    sessionToken = '';
  }
  if (!sessionToken) {
    sessionToken = (await json<{ token: string }>(await fetch('/api/v1/session'))).token;
  }
  return sessionToken;
}

async function authorizedFetch(input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  headers.set('X-DeepDeck-Token', await token());
  let response = await fetch(input, { ...init, headers });
  if (response.status !== 403) return response;

  const body = await response.clone().json().catch(() => null) as { detail?: string } | null;
  if (body?.detail !== 'Invalid local session token.') return response;

  const retryHeaders = new Headers(init.headers);
  retryHeaders.set('X-DeepDeck-Token', await token(true));
  response = await fetch(input, { ...init, headers: retryHeaders });
  return response;
}

export async function loadStatus(): Promise<CapabilityStatus> {
  return json<CapabilityStatus>(await fetch('/api/v1/status'));
}

export async function loadExperienceSettings(): Promise<ExperienceSettings> {
  return json<ExperienceSettings>(await fetch('/api/v1/experience/settings'));
}

export async function saveExperienceSettings(
  payload: Partial<Pick<ExperienceSettings, 'replayStorageLimitBytes' | 'replayPollSeconds' | 'autoCollectLeagueReplays'>>,
): Promise<ExperienceSettings> {
  return json<ExperienceSettings>(await authorizedFetch('/api/v1/experience/settings', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }));
}

export async function loadJobs(view: 'all' | 'player' = 'all'): Promise<Job[]> {
  return json<Job[]>(await fetch(view === 'player' ? '/api/v1/jobs?view=player' : '/api/v1/jobs'));
}

export type LocalCompetitionKind = 'single' | 'double' | 'round_robin' | 'round_robin_cut' | 'swiss' | 'loss_limit' | 'groups_cut' | 'random_season' | 'random_season_cut';
export type CompetitionEntrant = { id: string; name: string; seed: number; agent_id: string; deck_id: string; player_id?: string; agentName?: string; kind?: 'human' | 'agent' };
export type CompetitionMatch = {
  id: string; stage: string; round: number; players: Array<string | null>;
  status: 'pending' | 'ready' | 'dispatching' | 'playing' | 'done' | 'bye' | 'skipped' | 'error';
  winner: string | null; loser: string | null; score: number[] | null; replayId: string | null;
  jobId?: string; rematch?: boolean; attempts: Array<{ replayId?: string; error?: string }>;
  deckNames?: string[];
  group?: string; seriesWins?: number[]; seriesGames?: Array<{winner: string; score: number[] | null; replayId?: string | null}>;
};
export type CompetitionStanding = { id: string; name: string; seed: number; rank: number; played: number; wins: number; losses: number; draws: number; byes: number; points: number; gameWins: number; gameLosses: number; omw: number; gwp: number; ogw: number };
export type LocalCompetition = {
  id: string; name: string; type: LocalCompetitionKind; status: 'running' | 'pausing' | 'paused' | 'completed';
  createdAt: string; updatedAt: string; entrants: CompetitionEntrant[]; matches: CompetitionMatch[];
  parameters: { legs: number; rounds: number; cut: number; losses: number; playoffWins?: number; reseed?: boolean; seedAdvantage?: boolean; groups?: number; qualifiersPerGroup?: number; seasonGamesPerPlayer?: number; seasonAllowByes?: boolean; seasonScheduleVersion?: string };
  format?: 'legacy' | 'commander'; playoffSeeds?: string[];
  groupStandings?: Array<{id: string; name: string; standings: CompetitionStanding[]}>;
  champion: string | null; activeJobId: string | null; error: string | null;
  standings?: CompetitionStanding[]; qualifyingStandings?: CompetitionStanding[];
  playSettings: { firstSeed: number; seedSource: string; aiSpeed: string };
};
export async function loadLocalCompetitions(): Promise<LocalCompetition[]> {
  return (await json<{items: LocalCompetition[]}>(await fetch('/api/v1/local-competitions'))).items;
}
export async function loadLocalCompetition(id: string): Promise<LocalCompetition> {
  return json<LocalCompetition>(await fetch(`/api/v1/local-competitions/${encodeURIComponent(id)}`));
}
export async function createLocalCompetition(payload: Record<string, unknown>): Promise<LocalCompetition> {
  return json<LocalCompetition>(await authorizedFetch('/api/v1/local-competitions', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) }));
}
export async function controlLocalCompetition(id: string, action: 'pause' | 'resume'): Promise<LocalCompetition> {
  return json<LocalCompetition>(await authorizedFetch(`/api/v1/local-competitions/${encodeURIComponent(id)}/${action}`, { method: 'POST' }));
}

export async function loadJob(jobId: string): Promise<Job> {
  return json<Job>(await fetch(`/api/v1/jobs/${encodeURIComponent(jobId)}`));
}

export async function loadModels(): Promise<LocalModel[]> {
  return (await json<Page<LocalModel>>(await fetch('/api/v1/models'))).items;
}

export async function loadProgrammaticAgents(): Promise<ProgrammaticAgent[]> {
  return (await json<Page<ProgrammaticAgent>>(await fetch('/api/v1/agent-studio/agents'))).items;
}

export async function loadHeuristicCatalog(): Promise<HeuristicCatalog> {
  return json<HeuristicCatalog>(await fetch('/api/v1/agent-studio/heuristics'));
}

export async function createProgrammaticAgent(payload: Record<string, unknown>): Promise<ProgrammaticAgent> {
  return json<ProgrammaticAgent>(await authorizedFetch('/api/v1/agent-studio/agents', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }));
}

export async function updateProgrammaticAgent(agentId: string, payload: Record<string, unknown>): Promise<ProgrammaticAgent> {
  return json<ProgrammaticAgent>(await authorizedFetch(`/api/v1/agent-studio/agents/${encodeURIComponent(agentId)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }));
}

export async function loadAgentRevisions(agentId: string): Promise<AgentRevision[]> {
  return (await json<Page<AgentRevision>>(await fetch(`/api/v1/agent-studio/agents/${encodeURIComponent(agentId)}/revisions`))).items;
}

export async function restoreAgentRevision(agentId: string, revision: number, expectedRevision: number): Promise<ProgrammaticAgent> {
  return json(await authorizedFetch(`/api/v1/agent-studio/agents/${encodeURIComponent(agentId)}/revisions/${revision}/restore`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ expectedRevision }),
  }));
}

export async function loadLlmSettings(): Promise<LlmSettings> {
  return json<LlmSettings>(await fetch('/api/v1/agent-studio/llm-settings'));
}

export async function saveLlmSettings(payload: Record<string, unknown>): Promise<LlmSettings> {
  return json<LlmSettings>(await authorizedFetch('/api/v1/agent-studio/llm-settings', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }));
}

export async function chatWithAgentStudio(
  messages: Array<{ role: 'user' | 'assistant'; content: string }>,
  context: 'agent' | 'deck' = 'agent',
  activeDeckId = '',
  activeAgentId = '',
  onEvent?: (event: AssistantStreamEvent) => void,
): Promise<{
  message: string;
  toolEvents: AssistantToolEvent[];
  agents: ProgrammaticAgent[];
}> {
  const response=await authorizedFetch('/api/v1/agent-studio/chat'+(onEvent?'/stream':''), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(onEvent ? { Accept: 'text/event-stream' } : {}) },
    body: JSON.stringify({ messages: messages.map(({ role, content }) => ({ role, content })), context, activeDeckId: activeDeckId || undefined, activeAgentId: activeAgentId || undefined }),
  });
  if(!onEvent) return json(response);
  if(!response.ok) return json(response);
  return readAssistantStream(response, onEvent);
}

export type AssistantStreamEvent =
  | {type:'delta';text:string}
  | {type:'status';message:string}
  | {type:'tool';event:AssistantToolEvent}
  | {type:'error';message:string}
  | {type:'done';result:{message:string;toolEvents:AssistantToolEvent[];agents:ProgrammaticAgent[]}};

export async function testLeagueMcp(): Promise<{ connected: boolean; toolEvents: AssistantToolEvent[] }> {
  return json(await authorizedFetch('/api/v1/agent-studio/mcp-test', { method: 'POST' }));
}

export async function researchCards(payload: Record<string, unknown>): Promise<CardResearch> {
  return json(await authorizedFetch('/api/v1/research/cards', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }));
}

export async function loadAccountStatus(): Promise<AccountStatus> {
  return json<AccountStatus>(await fetch('/api/v1/account/status'));
}

export async function loadLeagueAccount(): Promise<LeagueAccount> {
  return json(await fetch('/api/v1/league-account'));
}

export async function loadLeagueConnection(): Promise<LeagueConnection> {
  return json(await fetch('/api/v1/league-connection'));
}

export async function signInLeague(identity: string, password: string, createAgentKey: boolean): Promise<LeagueAccount> {
  return json(await authorizedFetch('/api/v1/league-account/login', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ identity, password, createAgentKey }),
  }));
}

export async function signOutLeague(): Promise<LeagueAccount> {
  return json(await authorizedFetch('/api/v1/league-account/logout', { method: 'POST' }));
}

export async function prepareLeagueDecks(deckVersionIds: string[]): Promise<{ deckVersionIds: string[] }> {
  return json(await authorizedFetch('/api/v1/league-decks/prepare', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ deckVersionIds }),
  }));
}

export async function createModel(payload: Record<string, unknown>): Promise<LocalModel> {
  return json<LocalModel>(
    await authorizedFetch('/api/v1/models', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),
  );
}

export async function deleteModel(modelId: string): Promise<{ id: string; name: string; deleted: boolean; reclaimedBytes: number }> {
  return json(
    await authorizedFetch(`/api/v1/models/${encodeURIComponent(modelId)}`, {
      method: 'DELETE',
    }),
  );
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
    await authorizedFetch(`/api/v1/models/${encodeURIComponent(modelId)}/resources`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(plan),
    }),
  );
}

export async function loadDeckStatistics(): Promise<DeckStatistic[]> {
  return (await json<Page<DeckStatistic>>(await fetch('/api/v1/statistics/decks'))).items;
}

export async function loadTrainingStatistics(window: TrainingStatistic['metricWindow'] = '200'): Promise<TrainingStatistic[]> {
  const query = new URLSearchParams({ window });
  return (await json<Page<TrainingStatistic>>(await fetch(`/api/v1/statistics/training?${query}`))).items;
}

export async function loadTrainingSettings(modelId: string): Promise<TrainingSettings> {
  return json<TrainingSettings>(
    await fetch(`/api/v1/models/${encodeURIComponent(modelId)}/training-settings`),
  );
}

export async function loadTrainingCurriculum(): Promise<TrainingCurriculum> {
  return json<TrainingCurriculum>(await fetch('/api/v1/training/curriculum'));
}

export async function saveTrainingSettings(
  modelId: string,
  settings: TrainingSettings,
): Promise<TrainingSettings> {
  return json<TrainingSettings>(
    await authorizedFetch(`/api/v1/models/${encodeURIComponent(modelId)}/training-settings`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(settings),
    }),
  );
}

export async function loadTrainingEvidence(modelId: string): Promise<TrainingEvidence> {
  return json<TrainingEvidence>(
    await fetch(`/api/v1/models/${encodeURIComponent(modelId)}/evidence`),
  );
}

export async function loadAgentTrainingContract(modelId: string): Promise<AgentTrainingPublication | null> {
  const response = await fetch(`/api/v1/models/${encodeURIComponent(modelId)}/training-contract`);
  if (response.status === 404) return null;
  if (!response.headers.get('Content-Type')?.includes('application/json')) return null;
  const publication = await json<AgentTrainingPublication | unknown>(response);
  if (!publication || typeof publication !== 'object' || !('contract' in publication)) return null;
  return publication as AgentTrainingPublication;
}

export async function saveAgentTrainingControl(
  modelId: string,
  payload: { phase: string; action: string; parameters: Record<string, unknown> },
): Promise<Record<string, unknown>> {
  return json(
    await authorizedFetch(`/api/v1/models/${encodeURIComponent(modelId)}/training-control`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),
  );
}

export async function loadLocalTableReplays(signal?: AbortSignal): Promise<SavedReplaySummary[]> {
  return loadSavedReplays('local-tables', signal);
}

export async function loadSavedReplays(modelId: string, signal?:AbortSignal): Promise<SavedReplaySummary[]> {
  return (
    await json<Page<SavedReplaySummary>>(
      await fetch(`/api/v1/models/${encodeURIComponent(modelId)}/replays`,signal?{signal}:undefined),
    )
  ).items;
}

export async function loadSavedReplay(
  modelId: string,
  replayId: string,
): Promise<SavedReplay> {
  return json<SavedReplay>(
    await fetch(`/api/v1/models/${encodeURIComponent(modelId)}/replays/${encodeURIComponent(replayId)}`),
  );
}

export async function acquireSavedReplay(
  modelId: string,
  replayId: string,
): Promise<{ replay: SavedReplay; leaseId: string; expiresAtUnixMs: number; saved: boolean }> {
  const response = await authorizedFetch(`/api/v1/models/${encodeURIComponent(modelId)}/replays/${encodeURIComponent(replayId)}/leases`, {
    method: 'POST',
  });
  if (response.status === 404 || !response.headers.get('Content-Type')?.includes('application/json')) {
    return {
      replay: await loadSavedReplay(modelId, replayId),
      leaseId: '',
      expiresAtUnixMs: 0,
      saved: false,
    };
  }
  return json(response);
}

export async function renewSavedReplayLease(
  modelId: string,
  replayId: string,
  leaseId: string,
): Promise<{ expiresAtUnixMs: number }> {
  return json(
    await authorizedFetch(`/api/v1/models/${encodeURIComponent(modelId)}/replays/${encodeURIComponent(replayId)}/leases/${encodeURIComponent(leaseId)}`, {
      method: 'PUT',
    }),
  );
}

export async function releaseSavedReplayLease(
  modelId: string,
  replayId: string,
  leaseId: string,
): Promise<{ released: boolean }> {
  return json(
    await authorizedFetch(`/api/v1/models/${encodeURIComponent(modelId)}/replays/${encodeURIComponent(replayId)}/leases/${encodeURIComponent(leaseId)}`, {
      method: 'DELETE',
    }),
  );
}

export async function saveReplayForever(
  modelId: string,
  replayId: string,
): Promise<{ saved: boolean }> {
  return json(
    await authorizedFetch(`/api/v1/models/${encodeURIComponent(modelId)}/replays/${encodeURIComponent(replayId)}/save`, {
      method: 'POST',
    }),
  );
}

export async function setReplayFavorite(modelId: string, replayId: string, favorite: boolean): Promise<{ saved: boolean }> {
  return json(await authorizedFetch(`/api/v1/models/${encodeURIComponent(modelId)}/replays/${encodeURIComponent(replayId)}/favorite`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ favorite }),
  }));
}

export async function loadActiveGames(): Promise<ActiveGame[]> {
  return (await json<Page<ActiveGame>>(await fetch('/api/v1/games'))).items;
}

export async function stopGame(gameId: string): Promise<ActiveGame> {
  return json<ActiveGame>(
    await authorizedFetch(`/api/v1/games/${encodeURIComponent(gameId)}/stop`, {
      method: 'POST',
    }),
  );
}

export async function saveApiKey(apiKey: string): Promise<{ configured: boolean }> {
  return json<{ configured: boolean }>(
    await authorizedFetch('/api/v1/settings/api-key', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ api_key: apiKey }),
    }),
  );
}

export async function searchDeckPage(search: string, format: string, page = 1): Promise<Page<DeckSummary>> {
  const query = new URLSearchParams({ search, format, page: String(page) });
  return json<Page<DeckSummary>>(await fetch(`/api/v1/catalog/decks?${query}`));
}

export async function updateModel(
  modelId: string,
  payload: Pick<LocalModel, 'name' | 'decks' | 'reservePlaytest' | 'selfPlayAllSeats'>,
): Promise<LocalModel> {
  return json<LocalModel>(
    await authorizedFetch(`/api/v1/models/${encodeURIComponent(modelId)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),
  );
}

export async function searchDecks(search: string, format: string): Promise<DeckSummary[]> {
  return (await searchDeckPage(search, format)).items;
}

export async function loadDownloadedDecks(format = ''): Promise<DeckSummary[]> {
  const query = new URLSearchParams();
  if (format) query.set('format', format);
  return (await json<Page<DeckSummary>>(await fetch(`/api/v1/catalog/downloaded-decks?${query}`))).items;
}

export async function loadDeckWorkspaces(): Promise<DeckWorkspaceSummary[]> {
  return (await json<Page<DeckWorkspaceSummary>>(await fetch('/api/v1/deck-workshop/decks'))).items;
}

export async function loadServerDeckWorkspaces(): Promise<ServerDeckWorkspace[]> {
  return (await json<Page<ServerDeckWorkspace>>(await fetch('/api/v1/deck-workshop/server-decks'))).items;
}

export async function importServerDeckWorkspace(workspaceId: string): Promise<DeckWorkspace> {
  return json(await authorizedFetch(`/api/v1/deck-workshop/server-decks/${encodeURIComponent(workspaceId)}/import`, { method: 'POST' }));
}

export async function syncDeckWorkspace(deckId: string, action: 'sync' | 'publish' | 'pull', replace = false, expectedVersion?: number): Promise<DeckWorkspace> {
  return json(await authorizedFetch(`/api/v1/deck-workshop/decks/${encodeURIComponent(deckId)}/${action}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({...(action === 'pull' ? { replaceLocalDraft: replace } : { replaceSiteDraft: replace }), ...(expectedVersion !== undefined?{expectedVersion}:{})}),
  }));
}

export async function loadDeckWorkspace(deckId: string): Promise<DeckWorkspace> {
  return json<DeckWorkspace>(await fetch(`/api/v1/deck-workshop/decks/${encodeURIComponent(deckId)}`));
}

export async function createDeckWorkspace(payload: {
  name: string;
  format: 'legacy' | 'commander';
  description?: string;
}): Promise<DeckWorkspace> {
  return json<DeckWorkspace>(await authorizedFetch('/api/v1/deck-workshop/decks', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }));
}

export async function forkDeckWorkspace(versionId: string): Promise<DeckWorkspace> {
  return json<DeckWorkspace>(await authorizedFetch(`/api/v1/deck-workshop/from-download/${encodeURIComponent(versionId)}`, {
    method: 'POST',
  }));
}

export async function saveDeckWorkspace(
  deckId: string,
  payload: Partial<Pick<DeckWorkspace, 'name' | 'description' | 'cards' | 'combos' | 'openingHands' | 'tags' | 'collections'>> & {expectedVersion?: number},
): Promise<DeckWorkspace> {
  return json<DeckWorkspace>(await authorizedFetch(`/api/v1/deck-workshop/decks/${encodeURIComponent(deckId)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }));
}

export async function deleteDeckWorkspace(deckId: string): Promise<{ id: string; name: string; deleted: boolean }> {
  return json(await authorizedFetch(`/api/v1/deck-workshop/decks/${encodeURIComponent(deckId)}`, {
    method: 'DELETE',
  }));
}

export async function autocompleteCards(query: string): Promise<string[]> {
  const params = new URLSearchParams({ query });
  return (await json<{ data: string[] }>(await fetch(`/api/v1/catalog/cards/autocomplete?${params}`))).data;
}

export async function loadAllFormatDecks(format: string): Promise<DeckSummary[]> {
  const decks = new Map<string, DeckSummary>();
  for (let page = 1; page <= 100; page += 1) {
    const result = await searchDeckPage("", format, page);
    result.items.forEach((deck) => decks.set(deck.id, deck));
    const pagination = result.pagination;
    const totalPages = pagination?.totalPages ?? pagination?.total_pages;
    const hasNextPage = pagination?.hasNextPage ?? pagination?.has_next_page;
    if (
      result.items.length === 0 ||
      (typeof totalPages === "number" && page >= totalPages) ||
      hasNextPage === false ||
      (!pagination && result.items.length < 12)
    ) {
      break;
    }
  }
  return [...decks.values()];
}

export async function downloadDeck(versionId: string): Promise<{ versionId: string; name: string; format: string; cardCount: number; rawCardCount: number; path: string }> {
  return json(await authorizedFetch(`/api/v1/catalog/decks/${encodeURIComponent(versionId)}/download`, {
    method: 'POST',
  }));
}

export async function loadTrainingDeckPool(): Promise<{ decks: DeckSummary[] }> {
  return json(await fetch('/api/v1/training/deck-pool'));
}

export async function saveTrainingDeckPool(decks: DeckSummary[]): Promise<{ decks: DeckSummary[] }> {
  return json(await authorizedFetch('/api/v1/training/deck-pool', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
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
    await authorizedFetch('/api/v1/jobs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),
  );
}

export async function stopJob(jobId: string): Promise<Job> {
  return json<Job>(
    await authorizedFetch(`/api/v1/jobs/${jobId}/stop`, {
      method: 'POST',
    }),
  );
}
