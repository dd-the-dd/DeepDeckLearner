import { useEffect, useState, type FormEvent } from 'react';

import type { HeuristicCatalog, ProgrammaticAgent } from './api';

type Pattern = ProgrammaticAgent['strategyGraph']['patterns'][number];
const lines = (value: string) => [...new Set(value.split('\n').map((item) => item.trim()).filter(Boolean))];
const tags = (value: string) => [...new Set(value.split(/[,;\n]/).map((item) => item.trim()).filter(Boolean))];

export default function AgentPatterns({ patterns, catalog, onChange, openAssistant, onEditingChange }: { patterns: Pattern[]; catalog: HeuristicCatalog | null; onChange: (patterns: Pattern[]) => void; openAssistant?: () => void; onEditingChange?: (editing: boolean) => void }) {
  const [editing, setEditing] = useState<number | 'new' | null>(null);
  const [id, setId] = useState('');
  const [cardNames, setCardNames] = useState('');
  const [requiredTags, setRequiredTags] = useState('');
  const [textContains, setTextContains] = useState('');
  const [addTags, setAddTags] = useState('');
  const [adjustments, setAdjustments] = useState<Record<string, number>>({});
  const [concern, setConcern] = useState('');
  const [error, setError] = useState('');
  useEffect(() => { onEditingChange?.(editing !== null); return () => onEditingChange?.(false); }, [editing, onEditingChange]);
  function begin(index: number | 'new') {
    const pattern = index === 'new' ? null : patterns[index];
    let generated = patterns.length + 1;
    while (patterns.some((item) => item.id === `card-rule-${generated}`)) generated += 1;
    setEditing(index); setError(''); setConcern('');
    setId(pattern?.id || `card-rule-${generated}`);
    setCardNames(pattern?.cardNames.join('\n') || ''); setRequiredTags(pattern?.requireTags.join(', ') || '');
    setTextContains(pattern?.textContains.join('\n') || ''); setAddTags(pattern?.addTags.join(', ') || '');
    setAdjustments({ ...pattern?.nodeAdjustments });
  }
  function apply(event: FormEvent) {
    event.preventDefault(); setError('');
    const pattern: Pattern = { id: id.trim(), cardNames: lines(cardNames), requireTags: tags(requiredTags), textContains: lines(textContains), addTags: tags(addTags), nodeAdjustments: adjustments };
    if (!/^[A-Za-z0-9_.:-]{1,80}$/.test(pattern.id)) { setError('Use a short rule name with letters, numbers, dashes or underscores.'); return; }
    if (patterns.some((item, index) => index !== editing && item.id === pattern.id)) { setError('Another rule already uses this name.'); return; }
    if (!pattern.cardNames.length && !pattern.requireTags.length && !pattern.textContains.length) { setError('Choose at least one card, tag or rules-text condition. Use strategy weights for an all-card change.'); return; }
    if (!pattern.addTags.length && !Object.keys(adjustments).length) { setError('Add a tag or a decision concern for this rule.'); return; }
    if (Object.values(adjustments).some((value) => !Number.isFinite(value) || value < -2 || value > 2)) { setError('Each concern adjustment must stay between −2 and +2.'); return; }
    onChange(editing === 'new' ? [...patterns, pattern] : patterns.map((item, index) => index === editing ? pattern : item));
    setEditing(null);
  }
  return <section className="agent-patterns" aria-label="Card and tag rules">
    <header><div><h3>Card & tag rules</h3><p>Teach exceptions for your cards without writing code. Rules adjust the shared decision graph, not Magic legality.</p></div><button type="button" disabled={patterns.length >= 100} onClick={() => begin('new')}>+ Add rule</button></header>
    {openAssistant && <button className="text-action" type="button" onClick={openAssistant}>Describe a rule to the copilot →</button>}
    {!patterns.length && editing === null && <p className="agent-pattern-empty">No custom rules yet. The shared strategy already handles mulligans, combat, interaction and clocks.</p>}
    <ul>{patterns.map((pattern, index) => <li key={pattern.id}><div><strong>{pattern.id}</strong><span>{[pattern.cardNames.length ? pattern.cardNames.join(' / ') : '', pattern.requireTags.length ? `all tags: ${pattern.requireTags.join(', ')}` : '', pattern.textContains.length ? `text: ${pattern.textContains.join(' / ')}` : ''].filter(Boolean).join(' · ')}</span><small>{[...pattern.addTags.map((tag) => `+ #${tag}`), ...Object.entries(pattern.nodeAdjustments).map(([node, weight]) => `${catalog?.items.find((item) => item.id === node)?.name || node} ${weight >= 0 ? '+' : ''}${weight}`)].join(' · ')}</small></div><button type="button" onClick={() => begin(index)} aria-label={`Edit rule ${pattern.id}`}>Edit</button><button type="button" onClick={() => { onChange(patterns.filter((_, item) => item !== index)); if (editing !== null) setEditing(null); }} aria-label={`Remove rule ${pattern.id}`}>Remove</button></li>)}</ul>
    {editing !== null && <form className="agent-rule-form" onSubmit={apply}>
      <h4>{editing === 'new' ? 'Create a card rule' : 'Edit this rule'}</h4>
      <label>Rule name<input value={id} maxLength={80} onChange={(event) => setId(event.target.value)} /></label>
      <label>Match these cards · one name per line<textarea rows={3} value={cardNames} onChange={(event) => setCardNames(event.target.value)} placeholder={'Force of Will\nBrainstorm'} /></label>
      <label>Require all these tags<input value={requiredTags} onChange={(event) => setRequiredTags(event.target.value)} placeholder="combo_enabler, permanent" /></label>
      <details><summary>Also match rules text</summary><label>Any of these fragments · one per line<textarea rows={2} value={textContains} onChange={(event) => setTextContains(event.target.value)} /></label></details>
      <p>All filled conditions must match. Card names within the list are alternatives. Tags use their exact spelling.</p>
      <label>Add these strategy tags<input value={addTags} onChange={(event) => setAddTags(event.target.value)} placeholder="combo_enabler, must_answer" /></label>
      <label>Decision concern<select value={concern} onChange={(event) => setConcern(event.target.value)}><option value="">Choose a concern to adjust</option>{catalog?.items.filter((item) => !(item.id in adjustments)).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
      <button type="button" disabled={!concern} onClick={() => { setAdjustments({ ...adjustments, [concern]: .5 }); setConcern(''); }}>Add concern</button>
      {Object.entries(adjustments).map(([node, value]) => <div className="rule-adjustment" key={node}><label>{catalog?.items.find((item) => item.id === node)?.name || node}<input aria-label={`Adjustment for ${node}`} type="number" min="-2" max="2" step="0.1" value={value} onChange={(event) => setAdjustments({ ...adjustments, [node]: Number(event.target.value) })} /></label><button type="button" aria-label={`Remove concern ${node}`} onClick={() => setAdjustments(Object.fromEntries(Object.entries(adjustments).filter(([key]) => key !== node)))}>Remove</button></div>)}
      <p>Positive values increase this concern; negative values reduce it for matching cards. Save the strategy to activate the rule.</p>
      <div className="form-actions"><button className="primary-action" type="submit">Apply rule to draft</button><button type="button" onClick={() => setEditing(null)}>Cancel rule</button></div>
      {error && <p className="inline-error" role="alert">{error}</p>}
    </form>}
  </section>;
}
