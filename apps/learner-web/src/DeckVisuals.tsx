import { useEffect, useRef, useState } from 'react';

import type { DeckCover, DeckSummary } from './api';

const labels: Record<string, string> = { W: 'White', U: 'Blue', B: 'Black', R: 'Red', G: 'Green', C: 'Colorless' };

export function ManaColors({ colors }: { colors: string[] }) {
  const normalized = [...new Set(colors.map((color) => color.toUpperCase()).filter((color) => color in labels))];
  const symbols = normalized.length ? normalized : ['C'];
  return <span className="mana-symbols" role="img" aria-label={symbols.map((color) => labels[color]).join(', ')}>
    {symbols.map((color) => <i className={`ms ms-${color.toLowerCase()} ms-cost`} title={labels[color]} aria-hidden="true" key={color} />)}
  </span>;
}

export function ManaCost({ value }: { value: string }) {
  const symbols = [...value.matchAll(/\{([0-9WUBRGCXYZSP/]+)\}/gi)].map((match) => match[1]);
  if (!symbols.length) return null;
  return <span className="mana-symbols mana-cost" role="img" aria-label={`Mana cost ${value}`} title={value}>
    {symbols.map((symbol, index) => <i className={`ms ms-${symbol.toLowerCase().replaceAll('/', '')} ms-cost`} aria-hidden="true" key={`${symbol}-${index}`} />)}
  </span>;
}

const covers = new Map<string, Promise<DeckCover | null>>();
function loadCover(deck: DeckSummary): Promise<DeckCover | null> {
  const key = `${deck.id}:${deck.version}`;
  let request = covers.get(key);
  if (!request) {
    request = fetch(`/api/v1/catalog/decks/${encodeURIComponent(deck.id)}/cover`)
      .then(async (response) => {
        if (!response.ok) throw new Error('Artwork unavailable');
        const value = await response.json() as { coverCard: DeckCover | null };
        return value.coverCard;
      })
      .catch(() => { covers.delete(key); return null; });
    covers.set(key, request);
  }
  return request;
}

export function DeckArtwork({ deck, className = '' }: { deck: DeckSummary; className?: string }) {
  const container = useRef<HTMLSpanElement>(null);
  const [resolved, setResolved] = useState<{ key: string; cover: DeckCover | null } | null>(null);
  const [failedImage, setFailedImage] = useState('');
  const key = `${deck.id}:${deck.version}`;
  const cover = deck.coverCard !== undefined ? deck.coverCard : resolved?.key === key ? resolved.cover : null;
  useEffect(() => {
    if (deck.coverCard !== undefined || deck.unavailable) return;
    let active = true;
    const resolve = () => { void loadCover(deck).then((result) => { if (active) setResolved({ key, cover: result }); }); };
    if (typeof IntersectionObserver === 'undefined') resolve();
    const observer = typeof IntersectionObserver === 'undefined' ? null : new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) { observer?.disconnect(); resolve(); }
    }, { rootMargin: '120px' });
    if (container.current) observer?.observe(container.current);
    return () => { active = false; observer?.disconnect(); };
  }, [deck.id, deck.version, deck.coverCard, deck.unavailable, key]); // Versioned snapshots are immutable.
  const image = cover?.imageUrl;
  return <span ref={container} className={`deck-artwork ${className}`} title={cover ? `Representative card: ${cover.name}` : 'No card artwork available'}>
    {image && image !== failedImage
      ? <img src={image} alt={cover.name} loading="lazy" decoding="async" onError={() => setFailedImage(image)} />
      : <span className="deck-artwork-empty" role="img" aria-label="No card artwork available"><svg viewBox="0 0 40 56" aria-hidden="true"><rect x="3" y="3" width="34" height="50" rx="4" /><path d="M10 13h20v20H10zM11 40h18M11 45h12M14 28l6-10 6 10z" /></svg></span>}
  </span>;
}
