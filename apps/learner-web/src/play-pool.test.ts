import {expect, test} from 'vitest';
import {playPool} from './play-pool';
import type {DeckSummary, ProgrammaticAgent} from './api';
import type {PlaySeat} from './play-preparation';

const tempo={id:'tempo',name:'Tempo',format:'legacy',tags:['tempo'],collections:['test']} as DeckSummary;
const combo={...tempo,id:'combo',name:'Combo',tags:['combo']};
const missing={...tempo,id:'missing',unavailable:true};
const first={id:'one',name:'First',format:'legacy',tags:['blue'],collections:['club'],deckVersionIds:['combo'],decks:[combo]} as ProgrammaticAgent;
const second={...first,id:'two',name:'Second',deckVersionIds:['tempo','missing'],decks:[tempo,missing]};
const seat:PlaySeat={kind:'agent',agentId:'random',deckId:'random'};

test('random pool intersects agent filters, deck filters, mastery and available local snapshots',()=>{
  const result=playPool({...seat,agentTag:'blue',deckTag:'tempo'},'legacy',[first,second],[tempo,combo]);
  expect(result.candidates.map(c=>[c.agent.id,c.decks.map(d=>d.id)])).toEqual([['two',['tempo']]]);
  expect(result.decks.map(d=>d.id)).toEqual(['tempo']);expect(result.problem).toBe('');
});
test('a fixed deck with a random agent restricts the preview to agents who master it',()=>{
  const result=playPool({...seat,deckId:'combo'},'legacy',[first,second],[tempo,combo]);
  expect(result.candidates.map(c=>c.agent.id)).toEqual(['one']);
});
test('shared mastered decks count once, while each agent keeps its own options',()=>{
  const both={...first,id:'both',deckVersionIds:['tempo','combo'],decks:[tempo,combo]};
  const result=playPool(seat,'legacy',[first,both],[tempo,combo]);
  expect(result.candidates).toHaveLength(2);expect(result.decks).toHaveLength(2);
  expect(result.candidates.map(c=>c.decks.length)).toEqual([1,2]);
});
test('an empty joint pool never falls back to unrelated agents or decks',()=>{
  for(const filter of [{agentCollection:'missing'},{deckCollection:'missing'},{deckTag:'tempo',agentId:'one'}]){
    const result=playPool({...seat,...filter},'legacy',[first,second],[tempo,combo]);
    expect(result.candidates).toEqual([]);expect(result.decks).toEqual([]);expect(result.problem).not.toBe('');
  }
});
test('missing local snapshots, unavailable mastery and different formats are excluded',()=>{
  const unavailable={...first,decks:[{...combo,unavailable:true}]};
  expect(playPool(seat,'legacy',[unavailable],[combo]).candidates).toEqual([]);
  expect(playPool(seat,'legacy',[first],[]).candidates).toEqual([]);
  expect(playPool(seat,'commander',[first],[tempo,combo]).candidates).toEqual([]);
});
test('human random pools use matching local decks without requiring agent mastery',()=>{
  const result=playPool({kind:'human',agentId:'',deckId:'random',deckTag:'tempo'},'legacy',[first],[tempo,combo]);
  expect(result.decks.map(d=>d.id)).toEqual(['tempo']);expect(result.candidates).toEqual([]);expect(result.problem).toBe('');
});
