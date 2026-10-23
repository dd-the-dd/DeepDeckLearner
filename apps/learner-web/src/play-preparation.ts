import type { ProgrammaticAgent } from './api';
import { DEFAULT_PLAY_SETTINGS, type PlaySettingsDraft } from './PlaySettings';

export type PlaySeat = { kind: 'human' | 'agent'; agentId: string; deckId: string; agentTag?: string; agentCollection?: string; deckTag?: string; deckCollection?: string };
export type PlayPreparation = { format: 'legacy' | 'commander'; seats: PlaySeat[]; settings: PlaySettingsDraft };
export type PlayPreparations = { table: PlayPreparation; batch: PlayPreparation | null };
export type PlayHubDraft = { mode: 'table' | 'batch' | 'competition'; games: number; preparations: PlayPreparations };

export function initialPreparations(agents: ProgrammaticAgent[], preferred?: ProgrammaticAgent): PlayPreparations {
  const format = preferred?.format ?? 'legacy';
  const agent = preferred ?? agents.find(candidate => candidate.format === format);
  return {table: {format, settings: {...DEFAULT_PLAY_SETTINGS}, seats: [
    {kind: 'human', agentId: '', deckId: ''},
    {kind: 'agent', agentId: agent?.id ?? '', deckId: agent?.deckVersionIds[0] ?? 'random'},
  ]}, batch: null};
}

/** Explicit agent shortcut: change only the table, never the saved batch. */
export function agentPlayDraft(current: PlayHubDraft | null, agent?: ProgrammaticAgent): PlayHubDraft | null {
  if (!agent) return current ? {...current, mode: 'table'} : null;
  const preparations = current?.preparations ?? initialPreparations([], agent);
  const old = preparations.table;
  const count = agent.format === 'commander' ? 4 : 2;
  const oldHuman = old.seats.findIndex(seat => seat.kind === 'human');
  const humanIndex = oldHuman >= 0 && oldHuman < count ? oldHuman : 0;
  const human = old.seats[oldHuman] ?? {kind: 'human' as const, agentId: '', deckId: ''};
  const seats: PlaySeat[] = Array.from({length: count}, (_, index) => {
    if (index === humanIndex) return old.format === agent.format ? {...human} : {kind: 'human', agentId: '', deckId: ''};
    const previous = old.seats[index];
    return {kind: 'agent', agentId: agent.id,
      deckId: previous?.kind === 'agent' && agent.deckVersionIds.includes(previous.deckId) ? previous.deckId : 'random'};
  });
  return {mode: 'table', games: current?.games ?? 10,
    preparations: {...preparations, table: {format: agent.format, seats, settings: {...old.settings}}}};
}
