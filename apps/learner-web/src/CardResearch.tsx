import { useEffect, useRef, useState, type FormEvent } from 'react';

import AssistantToolEvents from './AssistantToolEvents';
import AsyncActionButton from './AsyncActionButton';
import ColorIdentityFilter from './ColorIdentityFilter';
import { researchCards, type CardResearch as ResearchResult, type CardSearchLocation } from './api';
import { preparedCardSearch, type PreparedCardSearch } from './card-search-bridge';

export default function CardResearch({ format, zone, disabled, onAdd, deckId='' }: {
  format: 'legacy' | 'commander'; zone: string; disabled: boolean; onAdd?: (name: string) => Promise<void>; deckId?:string;
}) {
  const [query, setQuery] = useState('');
  const [oracle, setOracle] = useState('');
  const [type, setType] = useState('');
  const [identity, setIdentity] = useState('');
  const [minimum, setMinimum] = useState('');
  const [maximum, setMaximum] = useState('');
  const [semantic, setSemantic] = useState('');
  const [commanderOnly, setCommanderOnly] = useState(false);
  const [colorCount, setColorCount] = useState('');
  const [identityMatch, setIdentityMatch] = useState<'within'|'exact'>('within');
  const [prepared, setPrepared] = useState(false);
  const container = useRef<HTMLDetailsElement>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ResearchResult | null>(null);
  const [submitted, setSubmitted] = useState<Record<string, unknown> | null>(null);
  const [trail, setTrail] = useState<CardSearchLocation[]>([]);
  const [error, setError] = useState('');
  const requestEpoch = useRef(0);
  const pending = useRef(false);
  useEffect(() => {
    requestEpoch.current += 1; pending.current = false;
    setResult(null); setSubmitted(null); setTrail([]); setError(''); setBusy(false);
    return () => {requestEpoch.current += 1;};
  }, [format]);
  useEffect(()=>{
    function apply(plan:PreparedCardSearch|null) {
      if(!plan || plan.deckId!==deckId || plan.filters.format!==format) return;
      const f=plan.filters;
      requestEpoch.current++; pending.current=false; setBusy(false); setError('');
      setQuery(f.query);setOracle(f.oracle_contains);setSemantic(f.semantic_text);setType(f.types.join('|'));setIdentity(f.color_identity);
      setMinimum(f.min_mana_value===undefined?'':String(f.min_mana_value));setMaximum(f.max_mana_value===undefined?'':String(f.max_mana_value));
      setCommanderOnly(f.commander_only);setColorCount(f.color_count===undefined?'':String(f.color_count));setIdentityMatch(f.identity_match);
      setResult(plan.result??null);setSubmitted(plan.result?f:null);setTrail(plan.result?[{page:1,page_offset:0}]:[]);setPrepared(true);
      if(container.current) container.current.open=true;
    }
    apply(preparedCardSearch(deckId,format));
    const receive=(event:Event)=>apply((event as CustomEvent<PreparedCardSearch>).detail);
    window.addEventListener('deepdeck:card-search-filters',receive);
    return ()=>window.removeEventListener('deepdeck:card-search-filters',receive);
  },[deckId,format]);
  const filters = { format, query, oracle_contains: oracle, semantic_text: semantic,
    types: type ? type.split('|') : [], color_identity: identity, identity_match:identityMatch, commander_only:commanderOnly,
    ...(colorCount!==''?{color_count:Number(colorCount)}:{}),
    ...(minimum !== '' ? { min_mana_value: Number(minimum) } : {}),
    ...(maximum !== '' ? { max_mana_value: Number(maximum) } : {}),
  };
  async function load(filters: Record<string, unknown>, locations: CardSearchLocation[]) {
    if (pending.current || disabled) return;
    pending.current = true;
    const epoch = ++requestEpoch.current;
    setBusy(true); setError('');
    try {
      const next = await researchCards({...filters, ...locations[locations.length - 1]});
      if (epoch !== requestEpoch.current) return;
      setResult(next); setSubmitted(filters); setTrail(locations);
    } catch (reason) {
      if (epoch === requestEpoch.current) setError(reason instanceof Error ? reason.message : 'Card research is unavailable. You can still add a card by name above.');
    } finally {
      if (epoch === requestEpoch.current) {pending.current = false; setBusy(false);}
    }
  }
  async function search(event: FormEvent) {
    event.preventDefault();
    await load(filters, [{page: 1, page_offset: 0}]);
  }
  const resultWindow = result?.resultWindow;
  const changed = submitted && JSON.stringify(Object.entries(filters).sort()) !== JSON.stringify(Object.entries(submitted).sort());
  return <details className="advanced-card-search" ref={container}>
    <summary>Find cards with advanced filters <span>No LLM required</span></summary>
    <form onSubmit={search}>
      <label className="research-wide">Name or search expression<input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={'e.g. t:instant o:"draw"'} /></label>
      <label className="research-wide">Rules text contains<input value={oracle} onChange={(event) => setOracle(event.target.value)} placeholder="destroy target creature" /></label>
      <label className="research-wide">Card type<select value={type} onChange={(event) => setType(event.target.value)}><option value="">Any type</option>{type.includes('|') && <option value={type}>{type.split('|').join(' OR ')}</option>}{['Creature', 'Instant', 'Sorcery', 'Enchantment', 'Artifact', 'Planeswalker', 'Land', 'Battle'].map((value) => <option key={value}>{value}</option>)}</select></label>
      <ColorIdentityFilter value={identity} onChange={setIdentity} disabled={disabled}/>
      <label>Identity matching<select value={identityMatch} onChange={event=>setIdentityMatch(event.target.value as 'within'|'exact')}><option value="within">Within selected colors</option><option value="exact">Exactly selected colors</option></select></label>
      <label>Number of identity colors<select value={colorCount} onChange={event=>setColorCount(event.target.value)}><option value="">Any number</option>{[0,1,2,3,4,5].map(count=><option key={count} value={count}>{count}</option>)}</select></label>
      {format==='commander' && <label className="research-wide"><input type="checkbox" checked={commanderOnly} onChange={event=>setCommanderOnly(event.target.checked)}/> Eligible as a commander (not just legal in the 99)</label>}
      <label>Minimum mana value<input type="number" min="0" max="100" step="any" value={minimum} onChange={(event) => setMinimum(event.target.value)} placeholder="0" /></label>
      <label>Maximum mana value<input type="number" min="0" max="100" step="any" value={maximum} onChange={(event) => setMaximum(event.target.value)} placeholder="Any" /></label>
      <details className="research-wide semantic-search-option"><summary>Semantic search · server-dependent</summary><label>Describe the effect<input value={semantic} onChange={(event) => setSemantic(event.target.value)} placeholder="protect my creatures from removal" /></label><p>Only available if your League server enables semantic search. An unavailable feature is reported, never replaced with unrelated results.</p></details>
      <AsyncActionButton type="submit" className="secondary-action research-wide" loading={busy} loadingLabel="Searching cards…" disabled={disabled || (!query.trim() && !oracle.trim() && !type && !identity && !semantic.trim() && minimum === '' && maximum === '' && !commanderOnly && colorCount==='')}>Search {format === 'legacy' ? 'Legacy' : 'Commander'} cards</AsyncActionButton>
    </form>
    {prepared && <p className="assistant-filter-status" role="status">Filters prepared by your assistant. You can refine them here; no deck cards were changed.</p>}
    <p className="research-destination">Additions go to <strong>{zone}</strong>. Refine filters to narrow broad results.</p>
    {changed && <p className="research-destination" role="status">Filters have changed. Results and page buttons still use your last submitted search. Search again to apply the new filters.</p>}
    {resultWindow && <div className="research-pagination" role="group" aria-label="Card search pages">
      <p aria-live="polite">Server page {resultWindow.page}{resultWindow.totalOnPage ? ` · cards ${resultWindow.pageOffset + 1}–${resultWindow.pageOffset + resultWindow.shown} of ${resultWindow.totalOnPage} on this page` : ' · no matching cards'}{result?.pagination?.totalItems !== undefined ? ` · ${result.pagination.totalItems} total matches` : ''}</p>
      <button type="button" disabled={busy || disabled || trail.length < 2} onClick={() => {if (submitted) void load(submitted, trail.slice(0, -1));}}>Previous results</button>
      <button type="button" disabled={busy || disabled || !resultWindow.next} onClick={() => {if (submitted && resultWindow.next) void load(submitted, [...trail, resultWindow.next]);}}>Next results</button>
      {resultWindow.navigationLimitReached && <small>Server page limit reached. Refine the search to continue.</small>}
      <small>Pages are fetched again when opened. The server catalog can change between reads.</small>
    </div>}
    <AssistantToolEvents events={result ? [{tool: 'search_cards', ok: true, result}] : []} onAddCard={onAdd} disabled={disabled || busy} addZone={zone} maxCards={20} />
    {error && <p className="inline-error" role="alert">{error}</p>}
  </details>;
}
