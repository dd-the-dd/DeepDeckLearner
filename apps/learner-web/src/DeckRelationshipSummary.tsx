import {useMemo,useState} from 'react';
import type {DeckWorkspaceCard} from './api';

const record=(value:unknown):Record<string,unknown>=>value && typeof value==='object' && !Array.isArray(value)?value as Record<string,unknown>:{};
export function relationshipRows(cards:DeckWorkspaceCard[],state:unknown) {
  const cohesion=record(record(state).cohesion);
  const rows:Array<{id:string;left:string;right:string;kind:string;score:number|null;directed:boolean}>=[];
  for(const [field,kind,directed] of [['cardLinks','Combo / synergy',false],['findabilityLinks','Finds',true],['castabilityLinks','Helps cast',true]] as const) {
    for(const [id,score] of Object.entries(record(cohesion[field]))) {
      const pieces=id.split('|');if(pieces.length!==2) continue;
      let identifiers:string[];try{identifiers=pieces.map(p=>decodeURIComponent(p));}catch{continue;}
      const names=identifiers.map(key=>cards.find(c=>c.entryId===key)?.name ?? 'Card no longer in this deck');
      rows.push({id:`${field}:${id}`,left:names[0],right:names[1],kind,directed,score:typeof score==='number' && Number.isFinite(score) && score>=-10 && score<=10?score:null});
    }
  }
  return rows;
}

export default function DeckRelationshipSummary({cards,state,openAssistant}:{cards:DeckWorkspaceCard[];state:unknown;openAssistant:()=>void}) {
  const rows=useMemo(()=>relationshipRows(cards,state),[cards,state]);
  const [page,setPage]=useState(0);
  const last=Math.max(0,Math.ceil(rows.length/10)-1),current=Math.min(page,last);
  return <details className="study-relationship-panel"><summary>Card relationships · {rows.length}</summary>
    <p>Saved combo, synergy, findability and castability scores. These are deck-building annotations, not win probabilities or verified combos. Ask the copilot to adjust a relationship.</p>
    {!rows.length?<p>No card relationships saved yet.</p>:<><div className="study-relationship-table"><table><thead><tr><th>Card</th><th>Relationship</th><th>Card</th><th>Score</th></tr></thead><tbody>{rows.slice(current*10,(current+1)*10).map(row=><tr key={row.id}><td>{row.left}</td><td>{row.kind} {row.directed?'→':'↔'}</td><td>{row.right}</td><td>{row.score===null?'Imported value':`${row.score>0?'+':''}${row.score}`}</td></tr>)}</tbody></table></div>{last>0 && <div className="study-note-actions"><button type="button" disabled={current===0} onClick={()=>setPage(current-1)}>Previous relationships</button><span>Page {current+1} / {last+1}</span><button type="button" disabled={current===last} onClick={()=>setPage(current+1)}>Next relationships</button></div>}</>}
    <button type="button" onClick={openAssistant}>Discuss card relationships</button>
  </details>;
}
