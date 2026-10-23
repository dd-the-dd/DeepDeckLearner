import { Fragment, useEffect, useState, type ReactNode } from 'react';
import { ManaCost } from './DeckVisuals';
import { resolveMissingArtwork } from './missingCardArtwork';
import type { AssistantToolEvent, ResearchCard } from './api';
import './assistant-rich-content.css';

export function manaText(text: string): ReactNode[] {
  // An explicit allowlist keeps arbitrary braces, code and unknown symbols readable.
  const token = /(?:\{(?:[0-9]|1[0-9]|20|[WUBRGCXYZSP]|[WUBRG]\/P|2\/[WUBRG]|[WUBRG]\/[WUBRG])\})+|\b[WUBRG]{2,5}\b/g;
  const result: ReactNode[] = [];
  let start = 0;
  for (const match of text.matchAll(token)) {
    result.push(text.slice(start, match.index));
    const value = match[0];
    if (!value.startsWith('{') && new Set(value).size !== value.length) result.push(value);
    else result.push(<ManaCost key={`mana-${match.index}`} value={value.startsWith('{') ? value : [...value].map(color=>`{${color}}`).join('')} />);
    start = match.index! + value.length;
  }
  result.push(text.slice(start));
  return result;
}

export function safeCardImage(value: unknown): string | undefined {
  if (typeof value !== 'string') return undefined;
  if (/^\/api\/scryfall-images\/[a-zA-Z0-9/_?.=&%-]+$/.test(value)) return value;
  try {
    const url = new URL(value);
    if (url.protocol === 'https:' && url.hostname === 'cards.scryfall.io' && !url.username && !url.password && !url.port)
      return '/api/scryfall-images' + url.pathname + url.search;
  } catch { /* Render a named fallback, never an arbitrary remote resource. */ }
  return undefined;
}

export function ChatMagicCard({name, card}: {name:string; card?:ResearchCard}) {
  const direct = safeCardImage(card?.imageUri || card?.imageUrl || card?.image_uris?.normal);
  const [resolved, setResolved] = useState<{name:string; image?:string}|null>(null);
  const [failed, setFailed] = useState('');
  useEffect(()=>{
    if (direct) return;
    let active = true;
    void resolveMissingArtwork({key:`chat:${name}`,name,token:false,power:'',toughness:''}).then(art=>{
      if(active) setResolved({name,image:safeCardImage(art?.imageUrl)});
    });
    return ()=>{active=false;};
  },[name,direct]);
  const image = direct || (resolved?.name===name ? resolved.image : undefined);
  return <figure className="chat-magic-card" aria-label={`Magic card: ${name}`}>
    {image && image!==failed ? <img src={image} alt={name} loading="lazy" decoding="async" onError={()=>setFailed(image)}/> : <div className="chat-card-fallback">{name}<small>{resolved || failed ? 'Artwork unavailable' : 'Loading card…'}</small></div>}
    <figcaption>{name}{card?.manaCost || card?.mana_cost ? <ManaCost value={card.manaCost || card.mana_cost || ''}/> : null}</figcaption>
  </figure>;
}

export default function AssistantMessage({content, events=[]}: {content:string; events?:AssistantToolEvent[]}) {
  const cards = new Map<string,ResearchCard>();
  for (const event of events) {
    const result = event.ok && event.result && typeof event.result==='object' ? event.result as {items?:ResearchCard[]} : null;
    if(Array.isArray(result?.items)) for(const card of result.items) if(typeof card?.name==='string') cards.set(card.name.toLowerCase(),card);
  }
  const blocks:ReactNode[]=[];
  const expression=/('''|```)\s*magic_card\s+-name\s+"([^"\r\n]{1,150})"\s*\1/g;
  let start=0, count=0;
  for(const match of content.matchAll(expression)) {
    if(count++>=8) break;
    blocks.push(<Fragment key={`text-${match.index}`}>{manaText(content.slice(start,match.index))}</Fragment>);
    const name=match[2].trim();
    blocks.push(name ? <ChatMagicCard key={`card-${match.index}`} name={name} card={cards.get(name.toLowerCase())}/> : match[0]);
    start=match.index!+match[0].length;
  }
  blocks.push(<Fragment key="tail">{manaText(content.slice(start))}</Fragment>);
  return <div className="assistant-rich-message">{blocks}</div>;
}
