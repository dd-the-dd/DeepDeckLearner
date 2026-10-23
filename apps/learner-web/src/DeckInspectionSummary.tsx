function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
}
const rows = (value: unknown) => Array.isArray(value) ? value.map(record) : [];
function tags(value: unknown) {
  const list = Array.isArray(value) ? value.filter((v): v is string => typeof v === 'string') : [];
  return list.slice(0, 12).join(', ') + (list.length > 12 ? ` · ${list.length - 12} more` : '');
}
const count = (value: unknown) => typeof value === 'number' && Number.isFinite(value) ? value : 0;

function PageCount({page, label}: {page: unknown; label: string}) {
  const p = record(page);
  return <span>{count(p.returned)} of {count(p.total)} {label} on this page{p.hasMore === true ? ' · more pages available' : ''}</span>;
}

/** A bounded, read-only receipt, not a claim that notes trained or changed the agent. */
export default function DeckInspectionSummary({result}: {result: Record<string, unknown>}) {
  const cards = rows(result.cards);
  const notes = record(result.studyNotes);
  const pages = record(result.notePagination);
  const relationships = rows(result.relationships);
  return <section className="assistant-deck-inspection" aria-label="Deck strategy inspection">
    <p className="inspection-receipt">Read-only local snapshot{typeof result.deckRevision === 'number' ? ` · deck revision ${result.deckRevision}` : ''}{typeof result.agentRevision === 'number' ? ` · agent revision ${result.agentRevision}` : ' · no agent selected'}</p>
    <p><PageCount page={result.pagination} label="card entries" /></p>
    <p>Personal deck tags and study notes do not change how the agent plays. Strategy changes require a saved agent revision.</p>
    {!!cards.length && <ul className="inspection-cards">{cards.slice(0, 6).map((card, i) => <li key={i}>
      <strong>{typeof card.name === 'string' ? card.name : 'Unknown card'}</strong>
      <small>{typeof card.quantity === 'number' ? `${card.quantity} × ` : ''}{typeof card.section === 'string' ? card.section : ''}{card.isGamePiece === true ? ' · token / game piece' : ''}</small>
      <span>Deck annotations: {tags(card.deckTags) || 'none'}</span>
      <span>Active strategy tags: {card.activeAgentTags === null ? 'select an agent to inspect its rules' : tags(card.activeAgentTags) || 'none'}{card.activeAgentTagsTruncated === true ? ' · truncated' : ''}</span>
    </li>)}</ul>}
    {cards.length > 6 && <p>{cards.length - 6} additional card entries returned to the copilot on this page.</p>}
    {(['combos', 'openingHands'] as const).map(key => <details key={key}>
      <summary><PageCount page={pages[key]} label={key === 'combos' ? 'combo notes' : 'opening-hand notes'} /></summary>
      <ul>{rows(notes[key]).slice(0, 10).map((note, i) => <li key={i}>
        <strong>{typeof note.name === 'string' ? note.name : 'Unnamed note'}</strong>
        {key === 'openingHands' && <span> · {note.keep === true ? 'Keep' : note.keep === false ? 'Mulligan' : 'Not rated'}</span>}
        {!!tags(note.cards) && <small>{tags(note.cards)}</small>}
        {typeof note.notes === 'string' && <p>{note.notes}</p>}
      </li>)}</ul>
    </details>)}
    <details><summary><PageCount page={result.relationshipPagination} label="relationship scores" /></summary>
      <ul>{relationships.slice(0, 6).map((link, i) => <li key={i}>
        {typeof link.leftCard === 'string' ? link.leftCard : 'Unavailable card'} {link.directed === true ? '→' : '↔'} {typeof link.rightCard === 'string' ? link.rightCard : 'Unavailable card'}
        <small>{link.kind === 'findabilityLinks' ? 'Finds' : link.kind === 'castabilityLinks' ? 'Helps cast' : 'Synergy'} · {typeof link.score === 'number' ? link.score : 'unrated'}</small>
      </li>)}</ul>{relationships.length > 6 && <p>{relationships.length - 6} additional scores returned to the copilot on this page.</p>}
    </details>
  </section>;
}
