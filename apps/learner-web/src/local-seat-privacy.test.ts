import {expect, test} from 'vitest';
import {viewForLocalSeat} from './local-seat-privacy';
import type {EngineView} from './LocalPixiTable';

const card = (id:string) => ({instanceId:id,definition:{name:`Private ${id}`,oracleText:'private rules'},flags:{knownToViewer:true}});
const fixture = ():EngineView => ({
  sessionId:'hotseat', revision:1,
  state:{turnNumber:3,players:['a','b','c','d'].map(id=>({
    id, hand:[card(`${id}-hand`)], library:[card(`${id}-library`)], sideboard:[card(`${id}-side`)],
  }))},
  decision:{id:'choose-a',playerId:'a',kind:'choice',choice:{candidateCardInstanceIds:['a-library']}},
});

test('every shared-screen seat sees its hand/sideboard but no other private zones or inherited visibility flags',()=>{
  for(const id of ['a','b','c','d']){
    const raw=fixture(), result=viewForLocalSeat(raw,id);
    for(const player of result.state!.players!){
      const text=JSON.stringify(player);
      if(player.id===id){expect(text).toContain(`Private ${id}-hand`);expect(text).toContain(`Private ${id}-side`);}
      else expect(text).not.toContain('Private');
      if(id!=='a'||player.id!=='a') expect(JSON.stringify(player.library)).not.toContain('Private');
    }
    expect(result.decision?.playerId??null).toBe(id==='a'?'a':null);
    expect(JSON.stringify(raw)).toContain('Private d-hand');
  }
});

test('only this viewer’s explicit known cards, current choices and public reveals bypass concealment',()=>{
  const raw=fixture();
  raw.state!.ruleModifiers=[
    {kind:'knownHandCards',viewerId:'a',cardInstanceIds:['b-hand']},
    {kind:'knownHandCards',viewerId:'b',cardInstanceIds:['c-hand']},
    {kind:'observedCardsNote',viewerId:'b',text:'private opponent note'},
    {kind:'revealedHand',playerId:'d',expiresAfterTurn:3},
  ];
  const result=viewForLocalSeat(raw,'a');
  expect(JSON.stringify(result.state!.players![1].hand)).toContain('Private b-hand');
  expect(JSON.stringify(result.state!.players![2].hand)).not.toContain('Private');
  expect(JSON.stringify(result.state!.players![3].hand)).toContain('Private d-hand');
  expect(JSON.stringify(result.state!.ruleModifiers)).not.toContain('private opponent note');
  raw.state!.turnNumber=4;
  expect(JSON.stringify(viewForLocalSeat(raw,'a').state!.players![3].hand)).not.toContain('Private');
});
