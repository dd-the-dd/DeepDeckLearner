import { useEffect, useState, type FormEvent } from 'react';
import type { AgentDecisionNode, HeuristicCatalog } from './api';
import './agent-decision-nodes.css';

const split = (text: string) => [...new Set(text.split(/[,;\n]/).map(s => s.trim()).filter(Boolean))];
const names = (text: string) => [...new Set(text.split('\n').map(s => s.trim()).filter(Boolean))];
const emptyWhen = () => ({actionKinds: [], sourceCards: [], sourceTags: [], targetCards: [], targetTags: [], turn: 'any', role: 'any', targetController: 'any'});
const blank = (id: string): AgentDecisionNode => ({id, name: 'My decision', enabled: true, weight: 1, bias: .5, when: emptyWhen(), inputs: []});
const actionNames: Record<string,string> = {castSpell:'Cast a spell', activateAbility:'Activate an ability', playLand:'Play a land', passPriority:'Pass priority', declareAttacker:'Attack', declareBlocker:'Block', finishAttackers:'Finish attacks', finishBlockers:'Finish blocks', keepHand:'Keep opening hand', takeMulligan:'Mulligan', bottomCard:'Put a card on the bottom', specialAction:'Special action'};
function describeWhen(when: AgentDecisionNode['when']) {
  return [when.actionKinds.map(a=>actionNames[a] ?? a).join(' / ') || 'All supported actions',
    when.sourceCards.length ? `Source: ${when.sourceCards.join(' / ')}` : '',
    when.sourceTags.length ? `Source tags: ${when.sourceTags.join(' + ')}` : '',
    when.targetCards.length ? `Target: ${when.targetCards.join(' / ')}` : '',
    when.targetTags.length ? `Target tags: ${when.targetTags.join(' + ')}` : '',
    when.targetController==='opponent' ? 'Opposing target only' : when.targetController==='self' ? 'Our target only' : '',
    when.turn==='ours' ? 'Our turn only' : when.turn==='opponent' ? "Opponent's turn only" : '',
    when.role!=='any' ? `Role: ${when.role}` : ''].filter(Boolean).join(' · ');
}

export default function AgentDecisionNodes({nodes, catalog, onChange, openAssistant, onEditingChange}: {
  nodes: AgentDecisionNode[]; catalog: HeuristicCatalog | null; onChange: (nodes: AgentDecisionNode[]) => void;
  openAssistant: () => void; onEditingChange: (editing: boolean) => void;
}) {
  const [original, setOriginalState] = useState<string | null>(null);
  const [draft, setDraft] = useState<AgentDecisionNode | null>(null);
  const [error, setError] = useState('');
  const [input, setInput] = useState('');
  const [filters, setFilters] = useState({sourceCards:'',sourceTags:'',targetCards:'',targetTags:''});
  function loadFilters(node: AgentDecisionNode) {setFilters({sourceCards:node.when.sourceCards.join('\n'),sourceTags:node.when.sourceTags.join(', '),targetCards:node.when.targetCards.join('\n'),targetTags:node.when.targetTags.join(', ')});}
  function setOriginal(id: string | null) {setOriginalState(id);const node=nodes.find(n=>n.id===id);if(node) loadFilters(node);}
  useEffect(() => {onEditingChange(draft !== null); return () => onEditingChange(false);}, [draft, onEditingChange]);
  const signals = [...(catalog?.customNodeSignals ?? []), ...(catalog?.items ?? []).map(c => ({id:`base.${c.id}`, name:`Base concern · ${c.name}`})), ...nodes.filter(n => n.id !== draft?.id).map(n => ({id:`node.${n.id}`, name:`Custom node · ${n.name}`}))];
  const patch = (value: Partial<AgentDecisionNode>) => setDraft(d => d ? {...d, ...value} : d);
  function begin(template?: 'draw' | 'combo') {
    let index = nodes.length + 1; while(nodes.some(n => n.id === `decision-${index}`)) index++;
    const node = blank(`decision-${index}`);
    if (template === 'draw') Object.assign(node, {name:'Refill an empty hand', bias:0, when:{...emptyWhen(), actionKinds:['castSpell','activateAbility'], sourceTags:['card_draw']}, inputs:[{from:'need.draw_cards',weight:1}]});
    if (template === 'combo') Object.assign(node, {name:'Answer opposing combo pieces', bias:.8, when:{...emptyWhen(), actionKinds:['castSpell','activateAbility'], sourceTags:['interaction'], targetTags:['combo_enabler'], targetController:'opponent'}});
    setOriginal(null); setDraft(node); loadFilters(node); setError(''); setInput('');
  }
  function apply(event: FormEvent) {
    event.preventDefault(); if(!draft) return;
    if (!draft.name.trim() || !/^[A-Za-z0-9_-]{1,64}$/.test(draft.id)) {setError('Give this node a name and a short identifier.');return;}
    if (nodes.some(n => n.id !== original && n.id === draft.id)) {setError('Another node already uses that identifier.');return;}
    if (![draft.bias,draft.weight,...draft.inputs.map(i=>i.weight)].every(Number.isFinite) || Math.abs(draft.bias)>1 || draft.weight<0 || draft.weight>2 || draft.inputs.some(i=>Math.abs(i.weight)>2)) {setError('Keep bias between −1 and 1, importance between 0 and 2, and input weights between −2 and 2.');return;}
    const saved = {...draft,when:{...draft.when,sourceCards:names(filters.sourceCards),sourceTags:split(filters.sourceTags),targetCards:names(filters.targetCards),targetTags:split(filters.targetTags)}};
    const next = original ? nodes.map(n=>n.id===original?saved:n) : [...nodes,saved];
    const visiting = new Set<string>(); const done = new Set<string>(); const byId = new Map(next.map(n=>[n.id,n]));
    function visit(id: string) {if(visiting.has(id)) throw new Error('These connections form a cycle. Remove the connection back to an earlier node.'); if(done.has(id)) return;visiting.add(id);for(const edge of byId.get(id)?.inputs ?? []) {if(edge.from.startsWith('node.')) {const parent=edge.from.slice(5);if(!byId.has(parent)) throw new Error('A referenced node is missing.');visit(parent);}}visiting.delete(id);done.add(id);}
    try {next.forEach(n=>visit(n.id));} catch(reason) {setError(String(reason instanceof Error ? reason.message : reason));return;}
    onChange(next);setDraft(null);
  }
  function remove(id: string) {
    const references = nodes.filter(n=>n.inputs.some(e=>e.from===`node.${id}`));
    if(references.length) {setError(`Remove connections from ${references.map(n=>n.name).join(', ')} before deleting this node.`);return;}
    onChange(nodes.filter(n=>n.id!==id));setError('');
  }
  return <section className="agent-custom-nodes" aria-label="Custom decision graph">
    <header><div><h3>Your decision graph</h3><p>Add a new strategic concern, connect it to board needs or another node, and choose when it applies.</p></div><button type="button" disabled={nodes.length>=32 || !!draft} onClick={()=>begin()}>+ Add decision node</button></header>
    <div className="node-templates"><span>Start with an example</span><button type="button" disabled={!!draft || nodes.length>=32 || !catalog?.customNodeSignals?.length} onClick={()=>begin('draw')}>Refill my hand</button><button type="button" disabled={!!draft || nodes.length>=32} onClick={()=>begin('combo')}>Answer combo pieces</button><button className="text-action" type="button" onClick={openAssistant}>Describe it to the copilot →</button></div>
    {!nodes.length && !draft && <p className="muted">Your shared graph is already active. Add nodes only for the extra decisions your deck needs.</p>}
    <ol className="decision-node-list">{nodes.map(n=><li key={n.id} className={!n.enabled?'node-disabled':''}>
      <div className="decision-node-flow"><div className="node-input-pills">
        {n.inputs.map(edge=><span key={edge.from}>{signals.find(s=>s.id===edge.from)?.name ?? edge.from} × {edge.weight}</span>)}
        {!n.inputs.length && <span>Matching conditions</span>}
      </div><span aria-hidden="true">→</span><div><strong>{n.name}</strong>
        <small>Importance × {n.weight} · bias {n.bias}{!n.enabled?' · disabled':''}</small>
        <small>{describeWhen(n.when)}</small>
      </div><span aria-hidden="true">→</span><span>Decision score</span></div>
      <div className="node-actions">
        <button type="button" aria-label={`Edit node ${n.name}`} disabled={!!draft} onClick={()=>{setOriginal(n.id);setDraft(structuredClone(n));setError('');setInput('');}}>Edit</button>
        <button type="button" aria-label={`${n.enabled?'Disable':'Enable'} ${n.name}`} disabled={!!draft} onClick={()=>onChange(nodes.map(item=>item.id===n.id?{...item,enabled:!item.enabled}:item))}>{n.enabled?'Disable':'Enable'}</button>
        <button type="button" aria-label={`Remove node ${n.name}`} disabled={!!draft} onClick={()=>remove(n.id)}>Remove</button>
      </div></li>)}</ol>
    {draft && <form className="agent-rule-form" onSubmit={apply}>
      <h4>{original?'Edit decision node':'Create a decision node'}</h4>
      <label>Decision name<input maxLength={100} value={draft.name} onChange={e=>patch({name:e.target.value})}/></label>
      <label>Node identifier<input maxLength={64} value={draft.id} disabled={!!original} onChange={e=>patch({id:e.target.value})}/><small>Stable identifier for connections; no code.</small></label>
      <div className="node-condition-grid"><label>Action<select value={draft.when.actionKinds.length===1?draft.when.actionKinds[0]:draft.when.actionKinds.length?'__multiple':''} onChange={e=>{if(e.target.value!=='__multiple') patch({when:{...draft.when,actionKinds:e.target.value?[e.target.value]:[]}});}}><option value="">All supported actions</option>{draft.when.actionKinds.length>1 && <option value="__multiple">{draft.when.actionKinds.map(a=>actionNames[a] ?? a).join(' + ')}</option>}{(catalog?.customNodeActionKinds ?? Object.keys(actionNames)).map(a=><option key={a} value={a}>{actionNames[a] ?? a}</option>)}</select>{draft.when.actionKinds.length>1 && <small>Current actions: {draft.when.actionKinds.map(a=>actionNames[a] ?? a).join(', ')}. Choosing an action replaces this list.</small>}</label>
      <label>Whose turn?<select value={draft.when.turn} onChange={e=>patch({when:{...draft.when,turn:e.target.value}})}><option value="any">Any turn</option><option value="ours">Our turn</option><option value="opponent">Opponent's turn</option></select></label>
      <label>Our role<select value={draft.when.role} onChange={e=>patch({when:{...draft.when,role:e.target.value}})}><option value="any">Any role</option><option value="beatdown">Pressuring the opponent</option><option value="control">Defending / controlling</option></select></label>
      <label>Target belongs to<select value={draft.when.targetController} onChange={e=>patch({when:{...draft.when,targetController:e.target.value}})}><option value="any">Anyone / no target needed</option><option value="opponent">An opponent</option><option value="self">Us</option></select></label></div>
      <label>Source cards · one exact name per line<textarea rows={2} value={filters.sourceCards} onChange={e=>setFilters({...filters,sourceCards:e.target.value})}/></label>
      <label>Source must have all tags<input value={filters.sourceTags} onChange={e=>setFilters({...filters,sourceTags:e.target.value})}/></label>
      <label>Target cards · one exact name per line<textarea rows={2} value={filters.targetCards} onChange={e=>setFilters({...filters,targetCards:e.target.value})}/></label>
      <label>Target must have all tags<input value={filters.targetTags} onChange={e=>setFilters({...filters,targetTags:e.target.value})}/></label>
      <p>All filled conditions must match. A target's name, tags and controller must describe the same target. Tags can be assigned in Card & tag rules above.</p>
      <label>Importance<input type="number" step=".1" min="0" max="2" value={draft.weight} onChange={e=>patch({weight:Number(e.target.value)})}/></label>
      <label>Base preference<input type="number" step=".1" min="-1" max="1" value={draft.bias} onChange={e=>patch({bias:Number(e.target.value)})}/><small>Positive favors matching actions; negative discourages them. Zero relies entirely on inputs.</small></label>
      <label>Connect an input<select value={input} onChange={e=>setInput(e.target.value)}><option value="">Choose a board need, base concern or node</option>{signals.filter(s=>!draft.inputs.some(i=>i.from===s.id)).map(s=><option key={s.id} value={s.id}>{s.name}</option>)}</select></label><button type="button" disabled={!input || draft.inputs.length>=12} onClick={()=>{patch({inputs:[...draft.inputs,{from:input,weight:1}]});setInput('');}}>Connect input</button>
      {draft.inputs.map((edge,index)=><div className="rule-adjustment" key={edge.from}><label>{signals.find(s=>s.id===edge.from)?.name ?? edge.from}<input aria-label={`Input weight ${edge.from}`} type="number" step=".1" min="-2" max="2" value={edge.weight} onChange={e=>patch({inputs:draft.inputs.map((v,i)=>i===index?{...v,weight:Number(e.target.value)}:v)})}/></label><button type="button" onClick={()=>patch({inputs:draft.inputs.filter((_,i)=>i!==index)})}>Disconnect {edge.from}</button></div>)}
      <details><summary>How this changes a decision</summary><p>Inputs and base preference are added, then bounded between −1 and +1. This value × importance × 100 is added to a legal action's score. Connections from another node use its bounded weighted value. A disabled or nonmatching node sends zero. No LLM runs during play.</p><p>Applies to spells, abilities, lands, passing, combat, mulligans and bottoming cards. It does not change legality, sideboarding or resolution choices. Keeping a four-card hand remains the baseline safety rule. These are custom preferences, not proven improvements.</p></details>
      <div className="form-actions"><button type="submit" className="primary-action">Apply node to draft</button><button type="button" onClick={()=>setDraft(null)}>Cancel node</button></div>
    </form>}
    {error && <p role="alert" className="inline-error">{error}</p>}
  </section>;
}
