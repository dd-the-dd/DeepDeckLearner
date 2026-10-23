import {useEffect,useId,useState,type FormEvent} from 'react';
import type {DeckWorkspaceCard} from './api';
import {sampleOpeningHand,noteCards,noteName,noteText,type StudyNote} from './deck-studies';
import './deck-studies.css';
import DeckRelationshipSummary from './DeckRelationshipSummary';
import AsyncActionButton from './AsyncActionButton';

export type StudyDraft = {combos:StudyNote[];openingHands:StudyNote[]};
type Editor = {kind:keyof StudyDraft;index:number|null;name:string;cards:string;notes:string;verdict:string};

function StudyCards({names,cards}:{names:string[];cards:DeckWorkspaceCard[]}) {
  return <div className="study-card-strip">{names.map((name,index)=>{
    const card=cards.find(c=>c.section==='main' && c.name.toLocaleLowerCase()===name.toLocaleLowerCase()) ?? cards.find(c=>c.name.toLocaleLowerCase()===name.toLocaleLowerCase());
    return <div className="study-card" key={`${name}:${index}`}>
      {card?.imageUri?<img src={card.imageUri} alt="" loading="lazy"/>:<span className="study-card-placeholder" aria-hidden="true">{card?.manaCost || '◇'}</span>}
      <strong>{name}</strong>{(!card || card.section!=='main' || card.isGamePiece || card.isToken) && <small>Not in the current main deck</small>}
    </div>;
  })}</div>;
}

export default function DeckStudies({value,cards,version,disabled,dirty,onChange,onEditingChange,onDiscard,openAssistant,latest,serverState,onSave,saveDisabled=false}: {
  value:StudyDraft;cards:DeckWorkspaceCard[];version:number;disabled:boolean;dirty:boolean;
  onChange:(value:StudyDraft)=>void;onEditingChange:(value:boolean)=>void;onDiscard:()=>void;openAssistant:()=>void;
  latest?:StudyDraft;
  serverState?:Record<string,unknown>;
  onSave?:()=>Promise<unknown>;saveDisabled?:boolean;
}) {
  const [editor,setEditor]=useState<Editor|null>(null);
  const cardHintId=useId();
  const [error,setError]=useState('');
  const [size,setSize]=useState(7);
  const [sample,setSample]=useState<{version:number;cards:DeckWorkspaceCard[]}|null>(null);
  useEffect(()=>{onEditingChange(editor!==null);return ()=>onEditingChange(false);},[editor,onEditingChange]);
  function begin(kind:keyof StudyDraft,index:number|null,hand?:string[]) {
    const note=index!==null?value[kind][index]:null;
    let number=value[kind].length+1;
    const prefix=kind==='combos'?'Combo':'Opening hand';
    while(value[kind].some(n=>noteName(n)===`${prefix} ${number}`)) number++;
    setEditor({kind,index,name:note?noteName(note):`${prefix} ${number}`,cards:(hand ?? (note?noteCards(note):[])).join('\n'),
               notes:note?noteText(note):'',verdict:typeof note?.keep==='boolean'?(note.keep?'keep':'mulligan'):'unknown'});
    setError('');
  }
  function apply(event:FormEvent) {
    event.preventDefault();if(!editor) return;
    const selected=editor.cards.split('\n').map(s=>s.trim()).filter(Boolean);
    if(!editor.name.trim() || editor.name.trim().length>120) {setError('Give this note a name of 1–120 characters.');return;}
    if(value[editor.kind].some((n,i)=>i!==editor.index && noteName(n).toLocaleLowerCase()===editor.name.trim().toLocaleLowerCase())) {setError('Another note already uses this name.');return;}
    if(selected.length>20 || selected.some(n=>n.length>200)) {setError('List at most 20 card names, one copy per line.');return;}
    if(!selected.length && !editor.notes.trim()) {setError('Add cards or describe the idea before applying this note.');return;}
    const note:StudyNote={...(editor.index===null?{}:value[editor.kind][editor.index]),name:editor.name.trim(),cards:selected,notes:editor.notes.trim()};
    if(editor.kind==='openingHands') {if(editor.verdict==='unknown') delete note.keep;else note.keep=editor.verdict==='keep';}
    onChange({...value,[editor.kind]:editor.index===null?[...value[editor.kind],note]:value[editor.kind].map((n,i)=>i===editor.index?note:n)});
    setEditor(null);setError('');
  }
  function draw() {try {setSample({version,cards:sampleOpeningHand(cards,size)});setError('');}catch(reason){setError(reason instanceof Error?reason.message:'Unable to draw a practice hand.');}}
  return <section className="deck-studies" aria-label="Deck study notebook">
    <header className="feature-heading"><div><span className="section-kicker">Your game plan, made visible</span><h3>Combos & opening hands</h3><p>Review what the copilot saved, add your own notes, and explore starting hands.</p></div><button type="button" onClick={openAssistant}>Discuss this deck</button></header>
    <p className="study-disclaimer">These are study notes, not Engine-verified combos or automatic agent rules. Save the deck to keep your edits; publish to sync them with your League draft.</p>
    <div className="study-draw-controls"><label>Practice hand size<input type="number" min="1" max="20" value={size} onChange={e=>setSize(Number(e.target.value))}/></label><button type="button" disabled={disabled} onClick={draw}>Draw a fresh hand</button><small>Main deck only · actual copy counts · no sideboard, commander or tokens. Each draw is a fresh sample, not a London mulligan simulation.</small></div>
    {sample && <article className="study-practice-hand"><h4>Your practice draw · {sample.cards.length} cards</h4><StudyCards names={sample.cards.map(c=>c.name)} cards={cards}/>{sample.version!==version && <p>This sample came from deck revision {sample.version}. Draw again to use the current list.</p>}<button type="button" disabled={disabled || !!editor || value.openingHands.length>=100} onClick={()=>begin('openingHands',null,sample.cards.map(c=>c.name))}>Annotate this hand</button></article>}
    <div className="study-note-columns">{(['combos','openingHands'] as const).map(kind=><section key={kind} aria-label={kind==='combos'?'Combo notes':'Opening-hand notes'}>
      <header><h4>{kind==='combos'?'Combo ideas':'Opening-hand examples'} <span>{value[kind].length}</span></h4><button type="button" disabled={disabled || !!editor || value[kind].length>=100} onClick={()=>begin(kind,null)}>+ {kind==='combos'?'Add combo note':'Add hand note'}</button></header>
      {!value[kind].length && <p className="study-empty">{kind==='combos'?'Which cards work together? Save the pieces and explain the sequence.':'Save a sample or describe a hand you would keep or mulligan.'}</p>}
      {value[kind].map((note,index)=><article className="study-note" key={index}><header><h5>{noteName(note)}</h5>{kind==='openingHands' && <span className={`study-verdict ${typeof note?.keep==='boolean'?(note.keep?'keep':'mulligan'):'unknown'}`}>{typeof note?.keep==='boolean'?(note.keep?'Keep · personal note':'Mulligan · personal note'):'Not evaluated'}</span>}</header><StudyCards names={noteCards(note)} cards={cards}/>{noteText(note) && <p>{noteText(note)}</p>}<div className="study-note-actions"><button type="button" disabled={disabled || !!editor} aria-label={`Edit ${kind} note ${noteName(note)}`} onClick={()=>begin(kind,index)}>Edit</button><button type="button" disabled={disabled || !!editor} aria-label={`Remove ${kind} note ${noteName(note)}`} onClick={()=>onChange({...value,[kind]:value[kind].filter((_,i)=>i!==index)})}>Remove</button></div></article>)}
    </section>)}</div>
    <DeckRelationshipSummary cards={cards} state={serverState} openAssistant={openAssistant}/>
    {editor && <form className="study-note-form" onSubmit={apply}><h4>{editor.index===null?'Create':'Edit'} {editor.kind==='combos'?'a combo note':'an opening-hand note'}</h4><fieldset disabled={disabled}>
      <label>Study note name<input maxLength={120} value={editor.name} onChange={e=>setEditor({...editor,name:e.target.value})}/></label>
      <label>Study cards · one copy per line<textarea aria-label="Study cards · one copy per line" aria-describedby={cardHintId} rows={5} value={editor.cards} onChange={e=>setEditor({...editor,cards:e.target.value})}/><small id={cardHintId}>Use exact names, including commas. Repeat a name for multiple copies. Cards not currently in the main deck are flagged, not silently substituted.</small></label>
      {editor.kind==='openingHands' && <label>Your hand verdict<select value={editor.verdict} onChange={e=>setEditor({...editor,verdict:e.target.value})}><option value="unknown">Not evaluated</option><option value="keep">I would keep</option><option value="mulligan">I would mulligan</option></select></label>}
      <label>Why does this work?<textarea rows={4} maxLength={2000} value={editor.notes} onChange={e=>setEditor({...editor,notes:e.target.value})} placeholder={editor.kind==='combos'?'Pieces, sequencing, protection and what can interrupt the plan.':'Mana, early plays, interaction and which draw would improve it.'}/></label>
      <div className="study-note-actions"><button type="submit" className="primary-action">Apply study note to draft</button><button type="button" onClick={()=>{setEditor(null);setError('');}}>Cancel study note</button></div>
    </fieldset></form>}
    {dirty && !editor && <div className="study-draft-notice"><p>Study changes are not saved yet.</p><div className="study-note-actions">{onSave && <AsyncActionButton className="primary-action" type="button" loading={disabled} disabled={saveDisabled} loadingLabel="Saving deck…" onClick={()=>void onSave()}>Save deck changes</AsyncActionButton>}<button type="button" disabled={disabled} onClick={onDiscard}>Discard study changes</button></div></div>}
    {latest && <details className="study-latest"><summary>Latest saved study notes · for conflict review</summary>{(['combos','openingHands'] as const).map(kind=><div key={kind}><h4>{kind==='combos'?'Latest combos':'Latest opening hands'}</h4>{latest[kind].map((note,index)=><p key={index}><strong>{noteName(note)}</strong> · {noteCards(note).join(' / ')} · {noteText(note)}</p>)}</div>)}</details>}
    {error && <p className="inline-error" role="alert">{error}</p>}
  </section>;
}
