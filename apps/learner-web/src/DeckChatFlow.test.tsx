import { useState } from 'react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { AssistantDrawer } from './PlayerApp';
import DeckWorkshop from './DeckWorkshop';
import * as api from './api';
import type { DeckWorkspace } from './api';

vi.mock('./api', async original => ({...await original<typeof import('./api')>(),
  loadLlmSettings:vi.fn(), chatWithAgentStudio:vi.fn(), loadDeckWorkspaces:vi.fn(), loadDeckWorkspace:vi.fn(), saveDeckWorkspace:vi.fn(),
}));
const imported:DeckWorkspace={schemaVersion:'deepdeck-local-deck/v1',id:'localdeck-abcdef123456',name:'Toph Metalbender',description:'Artifacts and landfall',format:'commander',version:1,
  cards:[{name:'Toph, the First Metalbender',quantity:1,section:'commander',tags:['engine']},{name:'Forest',quantity:34,section:'main',tags:['mana']}],
  tags:['landfall'],collections:['experiments'],combos:[],openingHands:[],origin:{kind:'local'},createdAt:'',updatedAt:''};
function Workspace() {
  const [revision,setRevision]=useState(0);
  return <><DeckWorkshop downloaded={[]} account={null} openSettings={()=>{}} onImported={()=>{}} openAssistant={()=>{}} revisionKey={revision}/>
    <AssistantDrawer context="agent" open onClose={()=>{}} onStarted={()=>{}} onChanged={()=>setRevision(value=>value+1)}/></>;
}
beforeEach(()=>{
  vi.clearAllMocks();window.localStorage.clear();
  vi.mocked(api.loadLlmSettings).mockResolvedValue({configured:true,providerName:'Fixture',baseUrl:'http://127.0.0.1:11434/v1',model:'fixture',hasApiKey:false});
  vi.mocked(api.loadDeckWorkspaces).mockResolvedValue([]);
  vi.mocked(api.loadDeckWorkspace).mockResolvedValue(imported);
});
afterEach(cleanup);
async function ask(message:string) {
  fireEvent.change(await screen.findByPlaceholderText('Describe a strategy change…'),{target:{value:message}});
  await waitFor(()=>expect(screen.getByRole('button',{name:'Send'})).toBeEnabled());
  fireEvent.click(screen.getByRole('button',{name:'Send'}));
}

test('a global chat import fills the real deck editor, then subsequent tools update its fields and card quantities',async()=>{
  vi.mocked(api.chatWithAgentStudio).mockResolvedValueOnce({message:'Imported the list.',agents:[],toolEvents:[{tool:'import_decklist',ok:true,result:imported}]});
  render(<Workspace/>);
  await ask('Yes, add the whole list yourself.');
  expect(await screen.findByLabelText('Deck name')).toHaveValue(imported.name);
  expect(screen.getByLabelText('Game plan')).toHaveValue(imported.description);
  expect(screen.getByLabelText('Tags for Forest')).toHaveValue('mana');
  expect(screen.getByText('34',{selector:'.quantity-stepper strong'})).toBeVisible();
  expect(api.saveDeckWorkspace).not.toHaveBeenCalled();
  const updated={...imported,name:'Toph Artifact Engine',description:'Protect Toph',version:2,tags:['artifact-engine'],collections:['commander-night'],cards:[imported.cards[0],{...imported.cards[1],quantity:35,tags:['mana','basic']}]};
  vi.mocked(api.loadDeckWorkspace).mockResolvedValue(updated);
  vi.mocked(api.chatWithAgentStudio).mockResolvedValueOnce({message:'Saved your changes.',agents:[],toolEvents:[{tool:'update_deck_cards',ok:true,result:updated},{tool:'update_deck_plan',ok:true,result:updated}]});
  await ask('Rename it and use 35 Forests.');
  await waitFor(()=>expect(screen.getByLabelText('Deck name')).toHaveValue(updated.name));
  expect(screen.getByLabelText('Game plan')).toHaveValue('Protect Toph');
  expect(screen.getByText('35',{selector:'.quantity-stepper strong'})).toBeVisible();
  expect(screen.getByLabelText('Tags for Forest')).toHaveValue('mana, basic');
  expect(screen.getByLabelText('Tags (comma separated)')).toHaveValue('artifact-engine');
  expect(screen.getByLabelText('Collections (comma separated)')).toHaveValue('commander-night');
  expect(api.saveDeckWorkspace).not.toHaveBeenCalled();
  expect(api.chatWithAgentStudio).toHaveBeenLastCalledWith(expect.any(Array),'agent',imported.id,'',expect.any(Function));
});

test('a tool save cannot silently overwrite an unsent name in the real editor',async()=>{
  vi.mocked(api.chatWithAgentStudio).mockResolvedValueOnce({message:'Imported.',agents:[],toolEvents:[{tool:'import_decklist',ok:true,result:imported}]});
  render(<Workspace/>);await ask('Import.');
  fireEvent.change(await screen.findByLabelText('Deck name'),{target:{value:'My unsaved name'}});
  const updated={...imported,name:'Chat name',version:2};
  vi.mocked(api.loadDeckWorkspace).mockResolvedValue(updated);
  vi.mocked(api.chatWithAgentStudio).mockResolvedValueOnce({message:'Changed the saved name.',agents:[],toolEvents:[{tool:'update_deck_plan',ok:true,result:updated}]});
  await ask('Change the saved deck name.');
  expect(await screen.findByText('This local deck has a newer version')).toBeVisible();
  expect(screen.getByLabelText('Deck name')).toHaveValue('My unsaved name');
  expect(api.saveDeckWorkspace).not.toHaveBeenCalled();
});
