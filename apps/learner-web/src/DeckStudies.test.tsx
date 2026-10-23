import {useState} from 'react';
import {cleanup,fireEvent,render,screen} from '@testing-library/react';
import {afterEach,expect,test,vi} from 'vitest';
import DeckStudies,{type StudyDraft} from './DeckStudies';
import {sampleOpeningHand,mergeStudyNotes} from './deck-studies';
import type {DeckWorkspaceCard} from './api';
import DeckRelationshipSummary,{relationshipRows} from './DeckRelationshipSummary';

afterEach(cleanup);
const card=(name:string,quantity=1,section:DeckWorkspaceCard['section']='main'):DeckWorkspaceCard=>({name,quantity,section,tags:[]});
const empty={combos:[],openingHands:[]};
function Harness({initial=empty,cards=[card('Island',60)],changed=vi.fn(),editing=vi.fn()}:{initial?:StudyDraft;cards?:DeckWorkspaceCard[];changed?:(value:StudyDraft)=>void;editing?:(value:boolean)=>void}) {
  const [value,setValue]=useState<StudyDraft>(initial);
  return <DeckStudies value={value} cards={cards} version={1} disabled={false} dirty={value!==initial} onChange={next=>{changed(next);setValue(next);}} onEditingChange={editing} onDiscard={()=>setValue(initial)} openAssistant={vi.fn()}/>;
}

test('draws respect quantities and exclude every non-main zone and auxiliary game piece',()=>{
  const cards=[card('Island',2),card('Brainstorm'),card('Sideboard',50,'sideboard'),card('Idea',10,'considering'),card('Commander',1,'commander'),card('Companion',1,'companion'),{...card('Marker',99),isGamePiece:true},{...card('Soldier',99),typeLine:'Token Creature'}];
  expect(sampleOpeningHand(cards,3,()=>0).map(c=>c.name)).toEqual(['Island','Island','Brainstorm']);
  expect(()=>sampleOpeningHand(cards,4)).toThrow('at least 4');
  expect(()=>sampleOpeningHand(cards,0)).toThrow('between 1 and 20');
  expect(sampleOpeningHand([card('A'),card('B')],1,()=>.99)[0].name).toBe('B');
});

test('three-way merging preserves unrelated chat notes and detects same-note conflicts',()=>{
  const a={name:'A',cards:['Island'],notes:'Old'};
  const ours={...a,notes:'Our plan'},newNote={name:'New chat idea',cards:['Brainstorm']};
  expect(mergeStudyNotes([a],[ours],[a,newNote])).toEqual([ours,newNote]);
  expect(mergeStudyNotes([a],[],[a,newNote])).toEqual([newNote]);
  expect(()=>mergeStudyNotes([a],[ours],[{...a,notes:'Their plan'}])).toThrow('changed in both');
  expect(mergeStudyNotes([a],[{...a,name:'Renamed'}],[a,newNote])).toEqual([newNote,{...a,name:'Renamed'}]);
  expect(mergeStudyNotes([a],[{notes:'Old',cards:['Island'],name:'A'}],[newNote])).toEqual([newNote]);
  expect(()=>mergeStudyNotes([],[a],[a,{...a,name:'a'}])).toThrow('Duplicate');
});

test('a drawn hand can be annotated as a keep without a model or a match',()=>{
  const changed=vi.fn(),editing=vi.fn();render(<Harness changed={changed} editing={editing}/>);
  fireEvent.click(screen.getByRole('button',{name:'Draw a fresh hand'}));
  expect(screen.getAllByText('Island')).toHaveLength(7);
  expect(changed).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button',{name:'Annotate this hand'}));
  expect(editing).toHaveBeenLastCalledWith(true);
  fireEvent.change(screen.getByLabelText('Your hand verdict'),{target:{value:'keep'}});
  fireEvent.change(screen.getByLabelText('Why does this work?'),{target:{value:'A deliberately annotated example.'}});
  fireEvent.click(screen.getByRole('button',{name:'Apply study note to draft'}));
  expect(changed).toHaveBeenCalledWith({combos:[],openingHands:[expect.objectContaining({cards:Array(7).fill('Island'),keep:true})]});
  expect(screen.getByText('Keep · personal note')).toBeVisible();
  expect(screen.getByText(/not saved yet/)).toBeVisible();
});

test('combo notes preserve exact names with commas and imported metadata',()=>{
  const changed=vi.fn();render(<Harness changed={changed} initial={{combos:[{name:'Plan',cards:['Thalia, Guardian of Thraben'],notes:'Old',customMetadata:{kept:true}}],openingHands:[]}}/>);
  fireEvent.click(screen.getByRole('button',{name:'Edit combos note Plan'}));
  expect(screen.getByLabelText('Study cards · one copy per line')).toHaveValue('Thalia, Guardian of Thraben');
  fireEvent.change(screen.getByLabelText('Why does this work?'),{target:{value:'New'}});
  fireEvent.click(screen.getByRole('button',{name:'Apply study note to draft'}));
  expect(changed).toHaveBeenCalledWith(expect.objectContaining({combos:[{name:'Plan',cards:['Thalia, Guardian of Thraben'],notes:'New',customMetadata:{kept:true}}]}));
  expect(screen.getByText('Not in the current main deck')).toBeVisible();
});

test('canceling an incomplete study form never changes the saved lists',()=>{
  const changed=vi.fn();render(<Harness changed={changed}/>);
  fireEvent.click(screen.getByRole('button',{name:'+ Add combo note'}));
  fireEvent.click(screen.getByRole('button',{name:'Apply study note to draft'}));
  expect(screen.getByRole('alert')).toHaveTextContent('Add cards or describe');
  expect(changed).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button',{name:'Cancel study note'}));
  expect(screen.queryByRole('button',{name:'Apply study note to draft'})).toBeNull();
});

test('relationship scores preserve direction and resolve escaped entry ids to card names',()=>{
  const cards=[{...card('Tutor'),entryId:'left/card'},{...card('Payoff'),entryId:'right card'}];
  const state={cohesion:{findabilityLinks:{'left%2Fcard|right%20card':7},cardLinks:{'left%2Fcard|right%20card':-2},castabilityLinks:{'%broken|none':1}}};
  const rows=relationshipRows(cards,state);
  expect(rows).toEqual([expect.objectContaining({left:'Tutor',right:'Payoff',kind:'Combo / synergy',score:-2,directed:false}),expect.objectContaining({left:'Tutor',right:'Payoff',kind:'Finds',score:7,directed:true})]);
  render(<DeckRelationshipSummary cards={cards} state={state} openAssistant={vi.fn()}/>);
  fireEvent.click(screen.getByText('Card relationships · 2'));
  expect(screen.getByRole('table')).toHaveTextContent('Finds →');
  expect(screen.getByRole('table')).toHaveTextContent('+7');
});
