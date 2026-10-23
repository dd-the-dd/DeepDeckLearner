/* eslint-disable @typescript-eslint/no-explicit-any -- Engine's versioned JSON presentation. */
type Card = Record<string, any>;
export type ArtworkRequest = {key: string; name: string; token: boolean; power: string; toughness: string};

export function missingArtworkRequests(scene: Card): ArtworkRequest[] {
  const cards: Card[] = (scene.players ?? []).flatMap((p: Card) => [
    ...(p.battlefield ?? []), ...(p.hand ?? []), ...(p.zones ?? []).flatMap((z: Card) => z.cards ?? []),
  ]);
  cards.push(...(scene.stack ?? []).map((s: Card) => s.card).filter(Boolean));
  const unique = new Map<string, ArtworkRequest>();
  for (const card of cards) {
    const raw = card.raw ?? card;
    const name = String(card.name ?? raw.name ?? '').trim();
    // Only the final privacy-filtered scene is eligible. In particular never
    // consult an opponent's raw hand, hidden slot, face-down morph or library.
    if (card.faceDown || card.imageUrl || !name || /^(hidden|unknown|library)(?:\s|:|$)/i.test(name)) continue;
    const definitionId = String(raw.definitionId ?? '');
    const key = definitionId || `card-art:${name.normalize('NFKC').toLocaleLowerCase('en-US').replace(/\s+/g, ' ')}`;
    unique.set(key, {key, name, token: Boolean(raw.isToken || /^token:/i.test(definitionId)), power: String(raw.power ?? ''), toughness: String(raw.toughness ?? '')});
  }
  return [...unique.values()].slice(0, 128);
}

const cache = new Map<string, {expires: number; result: Promise<Card | null>}>();
const waiting: (() => void)[] = [];
let active = 0;
function drain() { while (active < 3 && waiting.length) { active++; waiting.shift()!(); } }

export function resolveMissingArtwork(request: ArtworkRequest): Promise<Card | null> {
  const identity = JSON.stringify([request.name, request.token, request.power, request.toughness]);
  const cached = cache.get(identity);
  if (cached && cached.expires > Date.now()) return cached.result;
  if (waiting.length >= 256) return Promise.resolve(null);
  const result = new Promise<Card | null>(resolve => {
    waiting.push(() => {
      const abort = new AbortController();
      const timer = window.setTimeout(() => abort.abort(), 12000);
      const query = new URLSearchParams({name: request.name, token: String(request.token), power: request.power, toughness: request.toughness});
      void fetch(`/api/v1/catalog/cards/artwork?${query}`, {signal: abort.signal})
        .then(async response => response.ok ? (await response.json()).artwork : null)
        .then(artwork => resolve(artwork?.imageUrl ? artwork : null), () => resolve(null))
        .finally(() => { window.clearTimeout(timer); active--; drain(); });
    });
    drain();
  });
  // Metadata only; no disk growth or request on every action. Retry transient
  // failures after five minutes, and bound cache retention across many matches.
  cache.set(identity, {expires: Date.now() + 300000, result});
  if (cache.size > 512) cache.delete(cache.keys().next().value!);
  return result;
}
