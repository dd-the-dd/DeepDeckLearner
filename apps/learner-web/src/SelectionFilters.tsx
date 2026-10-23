export type SelectionFilter = { agentTag?: string; agentCollection?: string; deckTag?: string; deckCollection?: string };

export function labelMatch(entity: {tags?: string[]; collections?: string[]}, tag?: string, collection?: string) {
  return (!tag || entity.tags?.includes(tag)) && (!collection || entity.collections?.includes(collection));
}

export default function SelectionFilters({kind, items, value, onChange}: {
  kind:'agent'|'deck'; items:Array<{tags?:string[];collections?:string[]}>; value:SelectionFilter; onChange:(patch:SelectionFilter)=>void;
}) {
  const tagKey = kind === 'agent' ? 'agentTag' : 'deckTag';
  const collectionKey = kind === 'agent' ? 'agentCollection' : 'deckCollection';
  return <div className="selection-filters">{(['tags','collections'] as const).map((field)=> {
    const key = field === 'tags' ? tagKey : collectionKey;
    const values = [...new Set(items.flatMap((item)=>item[field] ?? []))].sort();
    const selected=value[key] ?? '';
    return <label key={field}>{kind === 'agent' ? 'Agent' : 'Deck'} {field === 'tags' ? 'tag' : 'collection'}<select value={selected} onChange={(event)=>onChange({[key]:event.target.value})}><option value="">All {field}</option>{selected && !values.includes(selected) && <option value={selected}>{selected} · not in current pool</option>}{values.map((label)=><option key={label} value={label}>{label}</option>)}</select></label>;
  })}</div>;
}
