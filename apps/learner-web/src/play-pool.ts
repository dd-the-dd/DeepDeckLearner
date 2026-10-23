import type {DeckSummary, ProgrammaticAgent} from './api';
import {labelMatch} from './SelectionFilters';
import type {PlaySeat} from './play-preparation';

export type PlayPool = {
  candidates: Array<{agent: ProgrammaticAgent; decks: DeckSummary[]}>;
  decks: DeckSummary[];
  problem: string;
};

/** Preview only. The local controller remains authoritative and freezes the draw. */
export function playPool(seat: PlaySeat, format: 'legacy' | 'commander', agents: ProgrammaticAgent[], localDecks: DeckSummary[]): PlayPool {
  const local = localDecks.filter(deck => deck.format === format && !deck.unavailable && labelMatch(deck, seat.deckTag, seat.deckCollection));
  const requested = local.filter(deck => seat.deckId === 'random' || deck.id === seat.deckId);
  if (seat.kind === 'human') return {candidates: [], decks: requested, problem: requested.length ? '' : 'No local deck matches this choice. Change the deck filters or import a matching deck.'};
  const selected = agents.filter(agent => agent.format === format && (seat.agentId === 'random' || agent.id === seat.agentId) && labelMatch(agent, seat.agentTag, seat.agentCollection));
  const candidates = selected.flatMap(agent => {
    const mastered = requested.filter(deck => agent.deckVersionIds.includes(deck.id) && agent.decks.some(known => known.id === deck.id && !known.unavailable));
    return mastered.length ? [{agent, decks: mastered}] : [];
  });
  const decks = [...new Map(candidates.flatMap(candidate => candidate.decks).map(deck => [deck.id, deck])).values()];
  const problem = candidates.length ? '' : !selected.length ? 'No agent matches the selected format, tags and collection. Change the agent filters.'
    : seat.deckId !== 'random' && seat.deckId ? 'No matching agent can play this deck. Choose a mastered deck, or change the agent and deck filters.'
    : 'No available mastered deck matches this pool. Change the deck filters or re-import a deck in Decks.';
  return {candidates, decks, problem};
}
