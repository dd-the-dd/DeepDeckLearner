import type { EngineView } from './LocalPixiTable';

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' ? value as Record<string, unknown> : {};
}

/** Local hot-seat presentation, not an authentication boundary for Engine's local API. */
export function viewForLocalSeat(view: EngineView, viewerId: string): EngineView {
  const modifiers = view.state?.ruleModifiers ?? [];
  const visible = new Set<string>();
  const revealed = new Set<string>();
  for (const modifier of modifiers) {
    if (modifier.viewerId && modifier.viewerId !== viewerId) continue;
    if (modifier.kind === 'knownHandCards' && Array.isArray(modifier.cardInstanceIds)) {
      modifier.cardInstanceIds.forEach(id => visible.add(String(id)));
    }
    if (modifier.kind === 'revealedHand' && typeof modifier.playerId === 'string'
        && Number(modifier.expiresAfterTurn ?? view.state?.turnNumber) >= Number(view.state?.turnNumber ?? 0)) revealed.add(modifier.playerId);
  }
  const decision = view.decision?.playerId === viewerId ? view.decision : null;
  const choice = record(decision?.choice);
  for (const key of ['candidateCardInstanceIds', 'cardInstanceIds']) {
    if (Array.isArray(choice[key])) choice[key].forEach(id => visible.add(String(id)));
  }
  const conceal = (value: unknown) => {
    const card = record(value);
    // A raw Engine view may refer to another seat. Its knownToViewer flag is not
    // proof that this shared-screen viewer knows an opponent's private card.
    if (visible.has(String(card.instanceId))) return value;
    return {instanceId: card.instanceId, definition: {name: 'Hidden card', typeLine: 'Card'}, flags: {knownToViewer: false}};
  };
  return {...view, decision, state: {...view.state,
    ruleModifiers: modifiers.filter(modifier => !modifier.viewerId || modifier.viewerId === viewerId),
    players: view.state?.players?.map(player => ({...player,
      hand: player.id === viewerId || revealed.has(player.id) ? player.hand : player.hand?.map(conceal),
      library: player.library?.map(conceal),
      sideboard: player.id === viewerId ? player.sideboard : player.sideboard?.map(conceal),
    })),
  }};
}
