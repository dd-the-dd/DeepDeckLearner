import {expect, test} from 'vitest';
import {agentPlayDraft, initialPreparations, type PlayHubDraft} from './play-preparation';
import type {ProgrammaticAgent} from './api';

const legacy = {id:'a',format:'legacy',deckVersionIds:['d']} as ProgrammaticAgent;
const commander = {id:'b',format:'commander',deckVersionIds:['c']} as ProgrammaticAgent;
function draft(): PlayHubDraft {
  const preparations=initialPreparations([legacy]);
  preparations.table.seats[0].deckId='human-deck';
  preparations.table.seats[1]={kind:'agent',agentId:'other',deckId:'d',agentTag:'old-filter',deckCollection:'old-pool'};
  preparations.batch={...preparations.table,settings:{...preparations.table.settings,seed:'99',matchLimit:'1800'}};
  return {mode:'batch',games:7,preparations};
}
test('agent shortcut keeps the same-format human and mastered deck, clears stale AI filters and never mutates the batch',()=>{
  const old=draft();const original=JSON.stringify(old);const updated=agentPlayDraft(old,legacy)!;
  expect(updated.mode).toBe('table');expect(updated.games).toBe(7);
  expect(updated.preparations.table.seats).toEqual([{kind:'human',agentId:'',deckId:'human-deck'},{kind:'agent',agentId:'a',deckId:'d'}]);
  expect(updated.preparations.batch).toBe(old.preparations.batch);
  expect(JSON.stringify(old)).toBe(original);
});
test('format-changing shortcut clears incompatible human deck and fills every AI seat with the requested pilot',()=>{
  const updated=agentPlayDraft(draft(),commander)!;
  expect(updated.preparations.table.format).toBe('commander');
  expect(updated.preparations.table.seats).toEqual([{kind:'human',agentId:'',deckId:''},...Array(3).fill({kind:'agent',agentId:'b',deckId:'random'})]);
});
test('a human in the fourth seat moves to an available seat when a Legacy specialist is requested',()=>{
  const old=agentPlayDraft(draft(),commander)!;
  old.preparations.table.seats[0]={kind:'agent',agentId:'b',deckId:'c'};
  old.preparations.table.seats[3]={kind:'human',agentId:'',deckId:'commander-human'};
  const updated=agentPlayDraft(old,legacy)!;
  expect(updated.preparations.table.seats).toEqual([{kind:'human',agentId:'',deckId:''},{kind:'agent',agentId:'a',deckId:'random'}]);
});
test('an unspecified shortcut only changes mode and a fresh specific shortcut creates the right field',()=>{
  const old=draft();expect(agentPlayDraft(old)?.preparations).toBe(old.preparations);
  expect(agentPlayDraft(null)).toBeNull();
  const created=agentPlayDraft(null,commander)!;
  expect(created.preparations.batch).toBeNull();expect(created.preparations.table.seats).toHaveLength(4);
});
