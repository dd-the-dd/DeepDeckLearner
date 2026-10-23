import type { EngineView } from './LocalPixiTable';

export function recordedCardIsHidden(candidate: unknown): boolean {
  if (!candidate || typeof candidate !== 'object') return true;
  const card = candidate as Record<string, unknown>;
  const definition = card.definition as Record<string, unknown> | undefined;
  return /^hidden(?:[ :_-]|$)/i.test(String(card.instanceId ?? card.id ?? ''))
    || /^hidden(?:[ :_-]|$)/i.test(String(definition?.id ?? card.cardId ?? ''))
    || /^(hidden card|unknown card)$/i.test(String(definition?.name ?? card.name ?? ''));
}

export default function SpectatorPerspective({view, value, onChange, compact = false}: {view: EngineView | null; value: string; onChange: (value: string) => void; compact?: boolean}) {
  const players = view?.state?.players ?? [];
  const relevant = value === 'global' ? players : players.filter(p => p.id === value);
  const missing = relevant.reduce((count, p) => count + (p.hand ?? []).filter(recordedCardIsHidden).length, 0);
  return <div className={`spectator-perspective${compact ? ' compact' : ''}`} title={value === 'global' ? 'All available hands are visible. Libraries remain face down.' : 'This seat stays at the bottom. Other hands remain hidden unless revealed.'}>
    <label>{compact ? 'Seat' : 'Perspective'}<select aria-label="Spectator perspective" value={value} onChange={e => onChange(e.target.value)}>
      <option value="global">Global · all recorded hands</option>
      {players.map((player, index) => <option key={player.id} value={player.id}>Seat {index + 1} · {player.name ?? player.id}</option>)}
      {value !== 'global' && !players.some(p => p.id === value) && <option value={value}>Selected seat · waiting</option>}
    </select></label>
    {!compact && <small>{value === 'global' ? 'All available hands are visible. Libraries remain face down.' : 'This seat stays at the bottom, even during another player’s turn. Other hands remain hidden unless revealed.'}</small>}
    {missing > 0 && <small role="status">{missing} card{missing === 1 ? '' : 's'} hidden in the recording. Missing identities cannot be recovered from this frame.</small>}
  </div>;
}
