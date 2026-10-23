import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';

import AgentHistory from './AgentHistory';
import AgentPatterns from './AgentPatterns';
import AgentDecisionNodes from './AgentDecisionNodes';
import AsyncActionButton from './AsyncActionButton';
import DeckPicker from './DeckPicker';
import EntityLabels, { parseLabels, supportsEntityLabels } from './EntityLabels';
import {
  ApiError, createProgrammaticAgent, loadDownloadedDecks, loadHeuristicCatalog, loadProgrammaticAgents, updateProgrammaticAgent,
  type DeckSummary, type HeuristicCatalog, type ProgrammaticAgent,
} from './api';

type Archetype = keyof ProgrammaticAgent['strategyGraph']['archetypeMix'];
type Profile = ProgrammaticAgent['profile'];
export type AgentCreationDeck = {id:string;name:string;format:'legacy'|'commander'};
type Draft = ReturnType<typeof toDraft>;
const ARCHETYPES: Array<{ id: Archetype; name: string; description: string }> = [
  { id: 'aggro', name: 'Aggro', description: 'Develop threats and close the game quickly.' },
  { id: 'tempo', name: 'Tempo', description: 'Keep a lead with efficient interaction.' },
  { id: 'midrange', name: 'Midrange', description: 'Trade resources and build a stronger board.' },
  { id: 'control', name: 'Control', description: 'Stabilize and preserve answers.' },
  { id: 'combo', name: 'Combo', description: 'Assemble and protect a decisive engine.' },
];
const BALANCED_MIX: Record<Archetype, number> = { aggro: .15, tempo: .2, midrange: .35, control: .2, combo: .1 };
const POSTURE_NAMES: Record<Profile, string> = { cautious: 'Patient & protective', balanced: 'Balanced', aggressive: 'Proactive' };
const POSTURE_WEIGHTS: Record<Profile, Record<string, number>> = {
  cautious: { resource_preservation: 1.4, timing_patience: 1.3, protect_advantage: 1.4, combat_survival: 1.2 },
  balanced: { resource_preservation: 1, timing_patience: 1, protect_advantage: 1, combat_survival: 1 },
  aggressive: { resource_preservation: .8, timing_patience: .8, protect_advantage: 1.1, combat_survival: .9 },
};
const cardLines = (value: string) => [...new Set(value.split('\n').map((item) => item.trim()).filter(Boolean))];
function toDraft(agent: ProgrammaticAgent) {
  return {
    name: agent.name, profile: agent.profile, deckIds: [...agent.deckVersionIds], weights: { ...agent.heuristics },
    tags: (agent.tags ?? []).join(', '), collections: (agent.collections ?? []).join(', '),
    mix: { ...agent.strategyGraph.archetypeMix }, temperature: agent.strategyGraph.temperature, adaptation: agent.strategyGraph.deckAdaptation,
    seed: agent.strategyGraph.seed, patterns: structuredClone(agent.strategyGraph.patterns), customNodes: structuredClone(agent.strategyGraph.customNodes ?? []), notes: agent.strategyNotes,
    prefer: agent.cardPreferences.prefer.join('\n'), avoid: agent.cardPreferences.avoid.join('\n'), hold: agent.cardPreferences.holdForInteraction.join('\n'),
  };
}
function revisionReason(base: Draft, draft: Draft) {
  const labels: Array<[keyof Draft, string]> = [['name', 'Renamed agent'], ['deckIds', 'Updated mastered decks'], ['profile', 'Changed posture'], ['weights', 'Adjusted decision priorities'], ['mix', 'Adjusted archetype blend'], ['adaptation', 'Adjusted deck adaptation'], ['temperature', 'Adjusted decision variety'], ['patterns', 'Updated card/tag rules'], ['seed', 'Changed decision seed'], ['notes', 'Updated copilot notes'], ['prefer', 'Updated preferred cards'], ['avoid', 'Updated avoided cards'], ['hold', 'Updated interaction preferences']];
  labels.push(['customNodes', 'Updated custom decision nodes']);
  return labels.filter(([key]) => JSON.stringify(base[key]) !== JSON.stringify(draft[key])).map(([, label]) => label).join('; ').slice(0, 500) || 'Saved strategy from the workshop.';
}

function DeckPool({ ids, decks, disabled, onRemove, allowEmpty = false }: { ids: string[]; decks: DeckSummary[]; disabled: boolean; onRemove: (id: string) => void; allowEmpty?: boolean }) {
  return <ul className="agent-deck-pool">{ids.map((id) => {
    const deck = decks.find((item) => item.id === id);
    return <li key={id}><span className="agent-deck-initial" aria-hidden="true">{(deck?.name || '?').slice(0, 2).toUpperCase()}</span><div><strong>{deck?.name || id}</strong><small>{!deck || deck.unavailable ? 'Unavailable locally · re-import or remove' : `${deck.format} · ${deck.playableCardCount} cards · ${id.startsWith('localdeck-') ? 'local draft' : 'downloaded'}`}</small></div><button type="button" disabled={disabled || (!allowEmpty && ids.length <= 1)} onClick={() => onRemove(id)} aria-label={`Remove ${deck?.name || id} from agent`}>Remove</button></li>;
  })}</ul>;
}

export default function AgentWorkspace({ refresh, openPlaytest, revisionKey = '', openAssistant, openLeague, onDirtyChange, initialDeck, onInitialDeckUsed }: {
  refresh: () => void | Promise<void>; openPlaytest: (agent?: ProgrammaticAgent) => void; revisionKey?: string;
  openAssistant: () => void; openLeague: (agent: ProgrammaticAgent) => void; onDirtyChange: (dirty: boolean) => void;
  initialDeck?: AgentCreationDeck | null; onInitialDeckUsed?: () => void;
}) {
  const [agents, setAgents] = useState<ProgrammaticAgent[]>([]);
  const [localDecks, setLocalDecks] = useState<DeckSummary[]>([]);
  const [catalog, setCatalog] = useState<HeuristicCatalog | null>(null);
  const [selectedId, setSelectedId] = useState('');
  const [base, setBase] = useState<ProgrammaticAgent | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [draftEpoch, setDraftEpoch] = useState(0);
  const [search, setSearch] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [hasRuleDraft, setHasRuleDraft] = useState(false);
  const [hasNodeDraft, setHasNodeDraft] = useState(false);
  const [creatingNew, setCreatingNew] = useState(Boolean(initialDeck));
  const [newName, setNewName] = useState(initialDeck ? `${initialDeck.name} pilot`.slice(0,64) : '');
  const [newFormat, setNewFormat] = useState<'legacy' | 'commander'>(initialDeck?.format ?? 'legacy');
  const [newProfile, setNewProfile] = useState<Profile>('balanced');
  const [newPlan, setNewPlan] = useState<Archetype | ''>('');
  const [newDeckIds, setNewDeckIds] = useState<string[]>(initialDeck ? [initialDeck.id] : []);
  useEffect(()=>{if(initialDeck) onInitialDeckUsed?.();},[initialDeck,onInitialDeckUsed]);
  const [creating, setCreating] = useState(false);
  const [picker, setPicker] = useState<'new' | 'edit' | null>(null);
  const sequence = useRef(0);
  const dirtyRef = useRef(false);
  const selected = agents.find((agent) => agent.id === selectedId) ?? null;
  const changed = Boolean(base && draft && JSON.stringify(toDraft(base)) !== JSON.stringify(draft));
  const editorDirty = changed || hasRuleDraft || hasNodeDraft;
  const newDirty = creatingNew && Boolean(newName.trim() || newDeckIds.length);
  const dirty = editorDirty || newDirty;
  const newDeckUnavailable = newDeckIds.some(id=>!localDecks.some(deck=>deck.id===id && deck.format===newFormat && !deck.unavailable));
  dirtyRef.current = dirty;
  const conflict = Boolean(base && selected?.id === base.id && selected.revision !== base.revision);

  const reload = useCallback(async () => {
    const request = ++sequence.current;
    const [nextAgents, nextCatalog, nextDecks] = await Promise.all([loadProgrammaticAgents(), loadHeuristicCatalog(), loadDownloadedDecks()]);
    if (request !== sequence.current) return;
    setAgents(nextAgents); setCatalog(nextCatalog); setLocalDecks(nextDecks); setLoading(false);
    setSelectedId((current) => {
      const active = window.localStorage.getItem('deepdeck.activeAgentId');
      if (!dirtyRef.current && nextAgents.some((agent) => agent.id === active)) return active!;
      return nextAgents.some((agent) => agent.id === current) ? current : nextAgents[0]?.id || '';
    });
  }, []);
  useEffect(() => {
    void reload().catch((reason) => { setLoading(false); setError(reason instanceof Error ? reason.message : 'Your agents could not be loaded.'); });
    return () => { sequence.current += 1; };
  }, [reload, revisionKey]);
  const adopt = useCallback((agent: ProgrammaticAgent) => {
    setBase(agent); setDraft(toDraft(agent)); setHasRuleDraft(false); setHasNodeDraft(false); setDraftEpoch((value) => value + 1); setError('');
  }, []);
  useEffect(() => {
    if (!selected) return;
    window.localStorage.setItem('deepdeck.activeAgentId', selected.id);
    if (base?.id !== selected.id || (base.revision !== selected.revision && !editorDirty)) adopt(selected);
  }, [selected, base, editorDirty, adopt]);
  useEffect(() => { onDirtyChange(dirty); return () => onDirtyChange(false); }, [dirty, onDirtyChange]);
  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ''; };
    window.addEventListener('beforeunload', warn); return () => window.removeEventListener('beforeunload', warn);
  }, [dirty]);

  function patch(patchValue: Partial<Draft>) { setDraft((current) => current ? { ...current, ...patchValue } : current); setMessage(''); }
  function chooseAgent(agent: ProgrammaticAgent) {
    if ((agent.id !== selectedId || creatingNew) && dirty && !window.confirm('Discard the unsaved draft and open this agent?')) return;
    setSelectedId(agent.id); window.localStorage.setItem('deepdeck.activeAgentId', agent.id); adopt(agent); setCreatingNew(false); setMessage('');
  }
  function showNew() {
    if (dirty && !window.confirm('Discard the unsaved draft and create a new agent?')) return;
    if (selected) adopt(selected);
    setNewName(''); setNewDeckIds([]); setCreatingNew(true); setError(''); setMessage('');
  }
  async function refreshLibrary() { setLocalDecks(await loadDownloadedDecks()); }
  function pickDeck(id: string) {
    if (picker === 'new') setNewDeckIds((current) => current.includes(id) ? current : [...current, id]);
    else if (draft && !draft.deckIds.includes(id)) patch({ deckIds: [...draft.deckIds, id] });
    setPicker(null);
  }
  async function accepted(agent: ProgrammaticAgent, note: string) {
    setAgents((current) => [agent, ...current.filter((item) => item.id !== agent.id)]);
    setSelectedId(agent.id); window.localStorage.setItem('deepdeck.activeAgentId', agent.id); adopt(agent); setMessage(note);
    try { await refresh(); } catch { setMessage(`${note} The library refresh will retry shortly.`); }
  }
  async function create(event: FormEvent) {
    event.preventDefault(); setCreating(true); setError('');
    try {
      if(loading || !newDeckIds.length || newDeckUnavailable) throw new Error('Choose available decks matching the agent format before creating it.');
      const agent = await createProgrammaticAgent({ name: newName, format: newFormat, profile: newProfile, deckVersionIds: newDeckIds,
        heuristics: catalog?.postureWeights?.[newProfile] ?? POSTURE_WEIGHTS[newProfile], strategyGraph: { archetypeMix: newPlan ? Object.fromEntries(ARCHETYPES.map((item) => [item.id, item.id === newPlan ? 1 : 0])) : BALANCED_MIX, deckAdaptation: .65 } });
      setCreatingNew(false); await accepted(agent, 'Your agent is ready for a local playtest. No LLM is needed to play.');
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'This agent could not be created.'); }
    finally { setCreating(false); }
  }
  async function save() {
    if (!base || !draft || hasRuleDraft || hasNodeDraft) return;
    setSaving(true); setError(''); setMessage('');
    try {
      const saved = await updateProgrammaticAgent(base.id, { expectedRevision: base.revision, name: draft.name, profile: draft.profile, deckVersionIds: draft.deckIds,
        ...(supportsEntityLabels(base) ? {tags: parseLabels(draft.tags), collections: parseLabels(draft.collections)} : {}),
        heuristics: draft.weights, strategyGraph: { archetypeMix: draft.mix, deckAdaptation: draft.adaptation, temperature: draft.temperature, seed: draft.seed, patterns: draft.patterns, customNodes: draft.customNodes },
        strategyNotes: draft.notes, cardPreferences: { prefer: cardLines(draft.prefer), avoid: cardLines(draft.avoid), holdForInteraction: cardLines(draft.hold) }, revisionReason: revisionReason(toDraft(base), draft) });
      await accepted(saved, `Version ${saved.revision} saved. Your previous strategy is available in Saved versions.`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'This strategy could not be saved.');
      if (reason instanceof ApiError && reason.status === 409) void reload().catch(() => undefined);
    } finally { setSaving(false); }
  }

  const poolDecks = [...localDecks, ...(selected?.decks || []).filter((deck) => !localDecks.some((local) => local.id === deck.id))];
  const missingDeck = draft?.deckIds.some((id) => !poolDecks.some((deck) => deck.id === id && !deck.unavailable));
  const blendTotal = draft ? Object.values(draft.mix).reduce((sum, value) => sum + value, 0) : 1;
  const visibleAgents = agents.filter((agent) => `${agent.name} ${agent.format} ${agent.tags?.join(' ')} ${agent.collections?.join(' ')}`.toLowerCase().includes(search.toLowerCase()));
  const showCreate = creatingNew || (!loading && !agents.length);
  return <div className="agent-studio-v2">
    <aside className="agent-roster feature-card">
      <header><div><span className="section-kicker">Your roster</span><h2>My agents <small>{agents.length}</small></h2></div><button className="primary-action" type="button" onClick={showNew}>+ New agent</button></header>
      <label className="agent-roster-search">Find an agent<input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Name or format" /></label>
      {loading ? <p role="status">Loading your agents…</p> : <nav aria-label="Your agents">{visibleAgents.map((agent) => <button type="button" key={agent.id} className={selectedId === agent.id && !showCreate ? 'active' : ''} onClick={() => chooseAgent(agent)}><span className="agent-monogram" aria-hidden="true">{agent.name.slice(0, 2).toUpperCase()}</span><span><strong>{agent.name}</strong><small>{agent.format} · {agent.decks.length} deck{agent.decks.length === 1 ? '' : 's'}</small></span><small>v{agent.revision}</small></button>)}</nav>}
      {!loading && !visibleAgents.length && <p className="agent-roster-empty">{agents.length ? 'No matching agent. Try another name.' : 'Create a deck specialist, then test it at your own table.'}</p>}
      <p className="agent-local-note">Private on this computer. Your agents play with Python and the DeepDeck Agent SDK, without calling an LLM.</p>
    </aside>
    <div className="agent-workspace">
      {showCreate && <section className="feature-card agent-create-v2"><header><div><span className="section-kicker">A shared strategic baseline</span><h2>Create your agent</h2><p>Name it, choose a play style and attach the decks it can pilot. You can personalize everything afterwards.</p></div>{agents.length > 0 && <button type="button" disabled={creating} onClick={() => { if (!newDirty || window.confirm('Discard this new agent draft?')) setCreatingNew(false); }}>Cancel</button>}</header>
        <form onSubmit={create}><fieldset disabled={creating}><div className="agent-editor-basics"><label>Agent name<input value={newName} maxLength={64} onChange={(event) => setNewName(event.target.value)} placeholder="My tempo pilot" required minLength={2} /></label><label>Format<select value={newFormat} onChange={(event) => { setNewFormat(event.target.value as typeof newFormat); setNewDeckIds([]); }}><option value="legacy">Legacy</option><option value="commander">Commander</option></select></label><label>Deck plan<select value={newPlan} onChange={(event) => setNewPlan(event.target.value as typeof newPlan)}><option value="">Deck-informed balance</option>{ARCHETYPES.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label><label>Starting posture<select value={newProfile} onChange={(event) => setNewProfile(event.target.value as Profile)}>{Object.entries(POSTURE_NAMES).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label></div>
          <section className="mastered-decks"><header><div><h3>Decks this agent can play</h3><p>Pick a local draft or import a published League deck.</p></div><button type="button" disabled={loading} onClick={() => setPicker('new')}>Choose decks</button></header>{!loading && <DeckPool ids={newDeckIds} decks={localDecks} disabled={creating} allowEmpty onRemove={(id) => setNewDeckIds((current) => current.filter((item) => item !== id))} />}{!newDeckIds.length && <p className="agent-pattern-empty">Choose at least one deck to create your agent.</p>}</section>
          {loading ? <p role="status">Loading your deck library…</p> : newDeckUnavailable && <p className="inline-error" role="alert">An attached deck is unavailable or has a different format. Remove it and choose an available deck.</p>}
          <AsyncActionButton type="submit" className="primary-action" loading={creating} loadingLabel="Creating your agent…" disabled={loading || newDeckUnavailable || newName.trim().length < 2 || !newDeckIds.length}>Create agent</AsyncActionButton>
        </fieldset></form></section>}
      {!showCreate && selected && base && draft && <section className="feature-card agent-editor-v2" aria-label="Agent strategy editor">
        <header className="agent-editor-heading"><span className="agent-monogram" aria-hidden="true">{selected.name.slice(0, 2).toUpperCase()}</span><div><span className="section-kicker">{selected.format} · version {base.revision}</span><h2>{selected.name}</h2><p>{editorDirty ? 'Your draft is not active yet.' : 'Personalize its priorities, then experiment at your table.'}</p></div><span className={`dd-pill ${editorDirty ? 'warn' : 'ready'}`}><i />{editorDirty ? 'Unsaved draft' : 'Saved locally'}</span></header>
        <a className="strategy-map-link" href="/assets/strategy-graphs.html" target="_blank" rel="noreferrer">Explore the strategy graph ↗</a>
        {conflict && <section className="agent-conflict" role="alert"><strong>A newer strategy is available</strong><p>The copilot or another window saved version {selected.revision}. Your draft is preserved and has not overwritten it.</p><button type="button" onClick={() => adopt(selected)}>Discard draft & load latest</button></section>}
        <fieldset disabled={saving} className="agent-edit-fields"><div className="agent-editor-basics"><label>Agent name<input value={draft.name} maxLength={64} onChange={(event) => patch({ name: event.target.value })} /></label><label>Posture preset<select value={draft.profile} onChange={(event) => { const profile = event.target.value as Profile; patch({ profile, weights: { ...draft.weights, ...(catalog?.postureWeights?.[profile] ?? POSTURE_WEIGHTS[profile]) } }); }}>{Object.entries(POSTURE_NAMES).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select><small>Adjusts patience, protection and resource priorities; keeps your deck plan.</small></label></div>
          {supportsEntityLabels(base) ? <EntityLabels tags={draft.tags} collections={draft.collections} onChange={(field, value) => patch({ [field]: value })} /> : <p>Restart the app after your active games finish to enable tags and collections.</p>}
          <section className="mastered-decks"><header><div><h3>Mastered decks <small>{draft.deckIds.length}</small></h3><p>Play and League offer this pool, plus Random. Keep at least one deck attached.</p></div><button type="button" onClick={() => setPicker('edit')}>+ Add deck</button></header><DeckPool ids={draft.deckIds} decks={poolDecks} disabled={saving} onRemove={(id) => patch({ deckIds: draft.deckIds.filter((item) => item !== id) })} /></section>
          <details className="agent-advanced"><summary><span>Play style & decision variety</span><small>Weighted strategy graph</small></summary><p>Blend the approaches your deck needs. Percentages show relative shares. Deck adaptation adds signals detected from its cards.</p><div className="archetype-grid">{ARCHETYPES.map((archetype) => <label key={archetype.id}><span><strong>{archetype.name}</strong><output>{Math.round(draft.mix[archetype.id] / (blendTotal || 1) * 100)}%</output></span><small>{archetype.description}</small><input aria-label={`${archetype.name} influence`} type="range" min="0" max="1" step="0.05" value={draft.mix[archetype.id]} onChange={(event) => patch({ mix: { ...draft.mix, [archetype.id]: Number(event.target.value) } })} /></label>)}</div>
            <div className="noise-control"><label><span><strong>Adapt to its decks</strong><small>At 100%, the detected identity replaces this manual blend.</small></span><output>{Math.round(draft.adaptation * 100)}%</output><input aria-label="Deck adaptation" type="range" min="0" max="1" step="0.05" value={draft.adaptation} onChange={(event) => patch({ adaptation: Number(event.target.value) })} /></label><label><span><strong>Decision variety</strong><small>Zero picks the highest score. Higher values explore other plausible choices.</small></span><output>{Math.round(draft.temperature * 100)}%</output><input aria-label="Decision variety" type="range" min="0" max="1" step="0.05" value={draft.temperature} onChange={(event) => patch({ temperature: Number(event.target.value) })} /></label></div>
          </details>
          <AgentPatterns key={`${selected.id}:${draftEpoch}`} patterns={draft.patterns} catalog={catalog} onChange={(patterns) => patch({ patterns })} openAssistant={openAssistant} onEditingChange={setHasRuleDraft} />
          <AgentDecisionNodes key={`nodes:${selected.id}:${draftEpoch}`} nodes={draft.customNodes} catalog={catalog} onChange={(customNodes) => patch({customNodes})} openAssistant={openAssistant} onEditingChange={setHasNodeDraft} />
          <details className="agent-advanced"><summary><span>Fine-tune decision priorities</span><small>{catalog?.items.length || 0} shared concerns</small></summary><p>1 keeps the shared baseline, 0 disables a concern, and 2 doubles its importance. Card rules can refine these priorities.</p><div className="heuristic-grid">{catalog?.items.map((item) => <label key={item.id}><span><strong>{item.name}</strong><small>{item.description}</small></span><output>{(draft.weights[item.id] ?? 1).toFixed(1)}</output><input aria-label={`${item.name} priority`} type="range" min="0" max="2" step="0.1" value={draft.weights[item.id] ?? 1} onChange={(event) => patch({ weights: { ...draft.weights, [item.id]: Number(event.target.value) } })} /></label>)}</div></details>
          <details className="agent-advanced"><summary><span>Card preferences & reproducibility</span></summary><p>One exact card name per line, including any commas in its name. Preferences influence scores; the Engine still determines legality.</p><div className="agent-preferences"><label>Prefer playing<textarea rows={3} value={draft.prefer} onChange={(event) => patch({ prefer: event.target.value })} /></label><label>Avoid playing<textarea rows={3} value={draft.avoid} onChange={(event) => patch({ avoid: event.target.value })} /></label><label>Keep for interaction<textarea rows={3} value={draft.hold} onChange={(event) => patch({ hold: event.target.value })} /></label><label>Reproducible decision seed<input type="number" min="0" max="2147483647" value={draft.seed} onChange={(event) => patch({ seed: Number(event.target.value) })} /></label></div></details>
          <label className="agent-copilot-notes">Notes for the copilot<textarea rows={3} value={draft.notes} maxLength={4000} onChange={(event) => patch({ notes: event.target.value })} placeholder="How should this deck feel to play?" /><small>Notes guide chat edits. Weights and card rules control actual decisions.</small></label>
        </fieldset>
        <AgentHistory key={selected.id} agent={selected} dirty={editorDirty || saving} onRestored={(agent) => accepted(agent, `Restored as version ${agent.revision}. Your current Python source was kept.`)} />
        {hasRuleDraft && <p className="inline-notice">Apply or cancel the open card rule before saving the strategy.</p>}
        {hasNodeDraft && <p className="inline-notice">Apply or cancel the open decision node before saving the strategy.</p>}
        {missingDeck && <p className="inline-error" role="alert">A mastered deck is unavailable. Re-import it or remove it from this pool before playing.</p>}
        <div className="agent-save-bar"><div><strong>{editorDirty ? 'Activate your changes' : `Version ${base.revision} is saved`}</strong><small>{editorDirty ? 'Each save keeps a version you can restore.' : 'No LLM is called while your agent plays.'}</small></div><AsyncActionButton type="button" className="primary-action" loading={saving} loadingLabel="Saving strategy…" disabled={!changed || conflict || hasRuleDraft || hasNodeDraft || draft.name.trim().length < 2 || !blendTotal} onClick={() => void save()}>Save strategy</AsyncActionButton>{editorDirty && <button type="button" disabled={saving} onClick={() => { if (window.confirm('Discard this unsaved strategy draft?')) adopt(selected); }}>Discard draft</button>}</div>
        <div className="agent-next-actions"><button type="button" disabled={editorDirty || saving || missingDeck} onClick={() => openPlaytest(selected)}>Playtest this agent →</button><button type="button" disabled={editorDirty || saving || missingDeck} onClick={() => openLeague(selected)}>Prepare for League →</button>{editorDirty && <small>Save your strategy before playing or joining the League.</small>}</div>
      </section>}
      {error && <div className="agent-feedback"><p className="inline-error" role="alert">{error}</p>{!agents.length && <button type="button" onClick={() => void reload().catch(() => undefined)}>Retry loading agents</button>}</div>}
      {message && <p className="inline-success agent-feedback" role="status">{message}</p>}
    </div>
    {picker && <DeckPicker decks={localDecks} format={picker === 'new' ? newFormat : selected?.format || 'legacy'} title="Choose a mastered deck" description="Choose a local draft or import a League list. Local drafts may need publishing before League deployment." selectedIds={picker === 'new' ? newDeckIds : draft?.deckIds || []} onPick={pickDeck} onClose={() => setPicker(null)} onImported={refreshLibrary} />}
  </div>;
}
