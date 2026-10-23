import type {PlayPool} from './play-pool';

export default function PlayPoolPreview({pool, seat, human, onClearFilters, disabled}: {pool: PlayPool; seat: number; human: boolean; onClearFilters?:()=>void; disabled?:boolean}) {
  const deckCount = pool.decks.length;
  return <details className="seat-pool-preview" aria-label={`Seat ${seat} random selection`} open={Boolean(pool.problem)}>
    <summary>{human ? 'Random deck' : `${pool.candidates.length} playable agent${pool.candidates.length === 1 ? '' : 's'}`} · {deckCount} available deck{deckCount === 1 ? '' : 's'}</summary>
    {pool.problem ? <><p role="status">{pool.problem} The draw is not widened automatically.</p>{onClearFilters && <button type="button" disabled={disabled} onClick={onClearFilters}>Clear selection filters</button>}</> : <>
      <p>{human ? 'One of these local decks will be drawn.' : 'The chosen agent only receives one of its own decks matching these filters.'} Repeats are possible; leave the seed blank for a fresh run. The local controller checks the pool and deck legality at launch.</p>
      <ul>{human ? pool.decks.slice(0, 8).map(deck => <li key={deck.id}>{deck.name}</li>) : pool.candidates.slice(0, 8).map(candidate => <li key={candidate.agent.id}><strong>{candidate.agent.name}</strong><span>{candidate.decks.length} mastered deck{candidate.decks.length === 1 ? '' : 's'}: {candidate.decks.slice(0, 5).map(deck => deck.name).join(', ')}{candidate.decks.length > 5 ? ` · ${candidate.decks.length - 5} more` : ''}</span></li>)}</ul>
      {(human ? deckCount : pool.candidates.length) > 8 && <p>{(human ? deckCount : pool.candidates.length) - 8} more candidates. Refine the filters to inspect a smaller pool.</p>}
    </>}
  </details>;
}
