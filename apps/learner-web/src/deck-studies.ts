import type {DeckWorkspaceCard} from './api';

export type StudyNote = Record<string,unknown>;
export const noteName = (note:StudyNote) => typeof note?.name==='string'?note.name:'Untitled note';
export const noteCards = (note:StudyNote) => Array.isArray(note?.cards)?note.cards.filter((n):n is string=>typeof n==='string'):[];
export const noteText = (note:StudyNote) => typeof note?.notes==='string'?note.notes:'';

export function sampleOpeningHand(cards:DeckWorkspaceCard[],size=7,random:()=>number=Math.random):DeckWorkspaceCard[] {
  if(!Number.isInteger(size)||size<1||size>20) throw new Error('Choose a hand size between 1 and 20.');
  const pool=cards.filter(c=>c.section==='main' && !c.isToken && !c.isGamePiece && !/^(token|emblem)\b/i.test(c.typeLine ?? '') && Number.isInteger(c.quantity) && c.quantity>0).map(card=>({card,count:card.quantity}));
  let total=pool.reduce((sum,item)=>sum+item.count,0);
  if(total<size) throw new Error(`The main deck needs at least ${size} playable cards to draw this hand.`);
  const hand=[];
  for(let draw=0;draw<size;draw++) {
    const value=random();if(!Number.isFinite(value)||value<0||value>=1) throw new Error('The random draw could not be generated.');
    let index=Math.floor(value*total);
    for(const item of pool) {if(index<item.count) {hand.push(item.card);item.count--;total--;break;}index-=item.count;}
  }
  return hand;
}

const canonical = (value:unknown):string => JSON.stringify(value,(_key,v)=>v && typeof v==='object' && !Array.isArray(v)?Object.fromEntries(Object.entries(v).sort(([a],[b])=>a.localeCompare(b))):v);
const key = (note:StudyNote) => typeof note?.name==='string' && note.name.trim()?`name:${note.name.trim().toLocaleLowerCase()}`:`legacy:${canonical(note)}`;

// A conservative three-way merge: changed notes are matched by their unique
// name, unrelated new notes survive, edits to the same note never win silently.
export function mergeStudyNotes(base:StudyNote[],draft:StudyNote[],latest:StudyNote[]):StudyNote[] {
  function mapped(items:StudyNote[]) {const out=new Map<string,StudyNote>();for(const note of items) {const id=key(note);if(out.has(id)) throw new Error('Duplicate note names need to be resolved before combining study changes.');out.set(id,note);}return out;}
  const old=mapped(base),ours=mapped(draft),theirs=mapped(latest),out=new Map(theirs);
  for(const id of new Set([...old.keys(),...ours.keys()])) {
    const before=old.get(id),after=ours.get(id),current=theirs.get(id);
    if(canonical(before)===canonical(after)) continue;
    if(canonical(current)!==canonical(before) && canonical(current)!==canonical(after)) throw new Error(`The study note “${noteName(after ?? before ?? {})}” changed in both copies. Review the latest notes before applying your version.`);
    if(after) out.set(id,after);else out.delete(id);
  }
  return [...out.values()];
}
