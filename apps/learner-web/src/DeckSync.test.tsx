import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';

import AccountConnection from './AccountConnection';
import DeckWorkshop from './DeckWorkshop';
import * as api from './api';
import type { DeckWorkspace, DeckWorkspaceSummary, LeagueAccount } from './api';

vi.mock('./api', async (importOriginal) => {
  const original = await importOriginal<typeof import('./api')>();
  return {
    ...original,
    signInLeague: vi.fn(), signOutLeague: vi.fn(), loadDeckWorkspaces: vi.fn(), loadDeckWorkspace: vi.fn(),
    loadServerDeckWorkspaces: vi.fn(), importServerDeckWorkspace: vi.fn(), saveDeckWorkspace: vi.fn(), syncDeckWorkspace: vi.fn(),
  };
});

const account: LeagueAccount = { configured: true, valid: true, reason: 'Connected', user: { id: 'player-1', displayName: 'League Player' } };
const deck: DeckWorkspace = {
  schemaVersion: 'deepdeck-local-deck/v1', id: 'localdeck-123456789abc', name: 'My Tempo', description: 'Stay ahead', format: 'legacy', version: 1,
  cards: [{ name: 'Island', quantity: 60, section: 'main', tags: ['mana-source'] }], combos: [], openingHands: [], origin: { kind: 'local' }, createdAt: '', updatedAt: '',
  syncStatus: { linked: true, workspaceId: 'workspace-1', publishedVersionId: null, needsUpload: true, needsPublishing: true, syncedAt: null },
};
const summary: DeckWorkspaceSummary = { ...deck, colors: ['U'], playableCardCount: 60, readyForPlay: true, workspace: true, sectionCounts: { main: 60 } };

beforeEach(() => {
  vi.clearAllMocks(); window.localStorage.clear();
  vi.mocked(api.loadDeckWorkspaces).mockResolvedValue([summary]);
  vi.mocked(api.loadDeckWorkspace).mockResolvedValue(structuredClone(deck));
  vi.mocked(api.loadServerDeckWorkspaces).mockResolvedValue([]);
  vi.mocked(api.saveDeckWorkspace).mockImplementation(async (_, payload) => ({ ...structuredClone(deck), ...payload } as DeckWorkspace));
  vi.mocked(api.syncDeckWorkspace).mockResolvedValue(structuredClone(deck));
});
afterEach(cleanup);

test('the deck-to-agent shortcut uses the saved deck and cannot discard unsaved edits',async()=>{
  const prepare=vi.fn();
  render(<DeckWorkshop downloaded={[summary]} account={account} openSettings={vi.fn()} onImported={vi.fn()} openAssistant={vi.fn()} onCreateAgent={prepare}/>);
  fireEvent.click(screen.getByRole('button',{name:'Open deck'}));
  const shortcut=await screen.findByRole('button',{name:'Create an agent for this deck →'});
  expect(shortcut).toBeEnabled();
  fireEvent.change(screen.getByLabelText('Deck name'),{target:{value:'My saved specialist deck'}});
  expect(shortcut).toBeDisabled();
  fireEvent.click(shortcut);
  expect(prepare).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button',{name:'Save deck'}));
  await waitFor(()=>expect(shortcut).toBeEnabled());
  fireEvent.click(shortcut);
  expect(prepare).toHaveBeenCalledWith(expect.objectContaining({id:deck.id,name:'My saved specialist deck',format:'legacy'}));
  expect(api.syncDeckWorkspace).not.toHaveBeenCalled();
});

test('a deck containing only game pieces cannot prepare an agent',async()=>{
  vi.mocked(api.loadDeckWorkspace).mockResolvedValue({...deck,cards:[{name:'Soldier token',quantity:20,section:'main',tags:[],isToken:true}]});
  render(<DeckWorkshop downloaded={[summary]} account={account} openSettings={vi.fn()} onImported={vi.fn()} openAssistant={vi.fn()} onCreateAgent={vi.fn()}/>);
  fireEvent.click(screen.getByRole('button',{name:'Open deck'}));
  expect(await screen.findByRole('button',{name:'Create an agent for this deck →'})).toBeDisabled();
  expect(screen.getByText('Add playable cards before preparing an agent.')).toBeInTheDocument();
});

describe('League account connection', () => {
  test('sign-in uses account credentials and an explicit agent-key choice, without storing the password', async () => {
    vi.mocked(api.signInLeague).mockResolvedValue(account);
    const changed = vi.fn();
    render(<AccountConnection account={null} agentAccount={null} onChanged={changed} />);
    fireEvent.change(screen.getByLabelText('Email or username'), { target: { value: 'player' } });
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'private-password' } });
    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.click(screen.getByRole('button', { name: 'Connect my account' }));
    await waitFor(() => expect(api.signInLeague).toHaveBeenCalledWith('player', 'private-password', false));
    await waitFor(() => expect(changed).toHaveBeenCalledOnce());
    expect(screen.queryByLabelText('Password')).toBeNull();
    expect(screen.getByText('Deck synchronization connected')).toBeVisible();
    expect(screen.getByRole('status')).toHaveTextContent('Account connected');
    expect(JSON.stringify(window.localStorage)).not.toContain('private-password');
  });

  test('a server error leaves a readable explanation and does not invent a connected state', async () => {
    vi.mocked(api.signInLeague).mockRejectedValue(new Error('The League sign-in service is unavailable.'));
    render(<AccountConnection account={null} agentAccount={null} onChanged={vi.fn()} />);
    fireEvent.change(screen.getByLabelText('Email or username'), { target: { value: 'player' } });
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'private-password' } });
    fireEvent.click(screen.getByRole('button', { name: 'Connect my account' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('unavailable');
    expect(screen.queryByText('Deck synchronization connected')).toBeNull();
  });
  test('a successful sign-in with a failed refresh stays connected without asking for the password again', async () => {
    vi.mocked(api.signInLeague).mockResolvedValue(account);
    render(<AccountConnection account={null} agentAccount={null} onChanged={vi.fn().mockRejectedValue(new Error('refresh unavailable'))} />);
    fireEvent.change(screen.getByLabelText('Email or username'),{target:{value:'player'}});
    fireEvent.change(screen.getByLabelText('Password'),{target:{value:'private-password'}});
    fireEvent.click(screen.getByRole('button',{name:'Connect my account'}));
    expect(await screen.findByText('Deck synchronization connected')).toBeVisible();
    expect(await screen.findByText(/account change succeeded/)).toBeVisible();
    expect(screen.queryByRole('alert')).toBeNull();
    expect(api.signInLeague).toHaveBeenCalledOnce();
    expect(screen.getByRole('link',{name:'Open DeepDeck League ↗'})).toHaveAttribute('href','https://deepdeckleague.com');
  });
});

describe('Deck synchronization', () => {
  async function openEditor() {
    render(<DeckWorkshop downloaded={[summary]} account={account} openSettings={vi.fn()} onImported={vi.fn()} openAssistant={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Open deck' }));
    await screen.findByLabelText('Deck name');
  }

  test('publishing saves unsent text fields and keeps the current local deck identity', async () => {
    await openEditor();
    fireEvent.change(screen.getByLabelText('Deck name'), { target: { value: 'Renamed Tempo' } });
    fireEvent.click(screen.getByRole('button', { name: 'Publish for League' }));
    await waitFor(() => expect(api.saveDeckWorkspace).toHaveBeenCalledWith(deck.id, { name: 'Renamed Tempo', description: deck.description, expectedVersion:1 }));
    await waitFor(() => expect(api.syncDeckWorkspace).toHaveBeenCalledWith(deck.id, 'publish', false,1));
  });

  test('a conflict requires an explicit choice before replacing the site draft', async () => {
    vi.mocked(api.syncDeckWorkspace).mockRejectedValueOnce(new api.ApiError('The site draft changed.', 409));
    await openEditor();
    fireEvent.click(screen.getByRole('button', { name: 'Save to site' }));
    await screen.findByText('Choose which version to keep');
    expect(api.syncDeckWorkspace).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole('button', { name: 'Use this local version' }));
    await waitFor(() => expect(api.syncDeckWorkspace).toHaveBeenLastCalledWith(deck.id, 'sync', true,1));
  });

  test('returning to the library saves edited deck details first', async () => {
    await openEditor();
    fireEvent.change(screen.getByLabelText('Game plan'), { target: { value: 'Protect the combo pieces' } });
    fireEvent.click(screen.getByRole('button', { name: '← Library' }));
    await waitFor(() => expect(api.saveDeckWorkspace).toHaveBeenCalledWith(deck.id, { name: deck.name, description: 'Protect the combo pieces', expectedVersion:1 }));
    await screen.findByText('Your deck workshop');
  });

  test('a background deck refresh does not erase text being edited', async () => {
    const props = { downloaded: [summary], account, openSettings: vi.fn(), onImported: vi.fn(), openAssistant: vi.fn() };
    const view = render(<DeckWorkshop {...props} revisionKey={0} />);
    fireEvent.click(screen.getByRole('button', { name: 'Open deck' }));
    await screen.findByLabelText('Deck name');
    fireEvent.change(screen.getByLabelText('Deck name'), { target: { value: 'My unsent edit' } });
    vi.mocked(api.loadDeckWorkspace).mockResolvedValue({ ...structuredClone(deck), version: 2 });
    view.rerender(<DeckWorkshop {...props} revisionKey={1} />);
    await screen.findByText('This local deck has a newer version');
    expect(screen.getByLabelText('Deck name')).toHaveValue('My unsent edit');
    fireEvent.click(screen.getByRole('button', { name: 'Publish for League' }));
    expect(api.saveDeckWorkspace).not.toHaveBeenCalled();
    expect(api.syncDeckWorkspace).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button',{name:'Keep my edited details'}));
    await waitFor(() => expect(api.saveDeckWorkspace).toHaveBeenCalledWith(deck.id, {name:'My unsent edit',expectedVersion:2}));
  });

  test('an already imported site deck opens its local copy without creating duplicates', async () => {
    vi.mocked(api.loadServerDeckWorkspaces).mockResolvedValue([{ id: 'workspace-1', name: 'My Tempo on site', hasUnpublishedChanges: true, state: { format: 'legacy' } }]);
    render(<DeckWorkshop downloaded={[summary]} account={account} openSettings={vi.fn()} onImported={vi.fn()} openAssistant={vi.fn()} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Open local copy' }));
    await screen.findByLabelText('Deck name');
    expect(api.loadDeckWorkspace).toHaveBeenCalledWith(deck.id);
    expect(api.importServerDeckWorkspace).not.toHaveBeenCalled();
  });

  test('a clean refresh adopts copilot tags instead of silently saving old labels later', async () => {
    const initial={...structuredClone(deck),tags:['tempo'],collections:['local']};
    vi.mocked(api.loadDeckWorkspace).mockResolvedValue(initial);
    const props={downloaded:[summary],account,openSettings:vi.fn(),onImported:vi.fn(),openAssistant:vi.fn()};
    const view=render(<DeckWorkshop {...props} revisionKey={0}/>);
    fireEvent.click(screen.getByRole('button',{name:'Open deck'}));
    expect(await screen.findByLabelText('Tags (comma separated)')).toHaveValue('tempo');
    vi.mocked(api.loadDeckWorkspace).mockResolvedValue({...initial,version:2,tags:['combo'],collections:['new pool']});
    view.rerender(<DeckWorkshop {...props} revisionKey={1}/>);
    await waitFor(()=>expect(screen.getByLabelText('Tags (comma separated)')).toHaveValue('combo'));
    expect(screen.getByLabelText('Collections (comma separated)')).toHaveValue('new pool');
    expect(screen.queryByText('This local deck has a newer version')).toBeNull();
  });

  test('reapplying a dirty name preserves copilot descriptions and does not resend old cards', async () => {
    const props={downloaded:[summary],account,openSettings:vi.fn(),onImported:vi.fn(),openAssistant:vi.fn()};
    const view=render(<DeckWorkshop {...props} revisionKey={0}/>);
    fireEvent.click(screen.getByRole('button',{name:'Open deck'}));await screen.findByLabelText('Deck name');
    fireEvent.change(screen.getByLabelText('Deck name'),{target:{value:'My draft name'}});
    const remote={...structuredClone(deck),version:2,description:'Copilot plan',cards:[{...deck.cards[0],quantity:58}]};
    vi.mocked(api.loadDeckWorkspace).mockResolvedValue(remote);
    vi.mocked(api.saveDeckWorkspace).mockImplementation(async(_,payload)=>({...remote,...payload,version:3}));
    view.rerender(<DeckWorkshop {...props} revisionKey={1}/>);
    await screen.findByText('This local deck has a newer version');
    fireEvent.click(screen.getByRole('button',{name:'Keep my edited details'}));
    await waitFor(()=>expect(api.saveDeckWorkspace).toHaveBeenLastCalledWith(deck.id,{name:'My draft name',expectedVersion:2}));
    expect(screen.getByLabelText('Game plan')).toHaveValue('Copilot plan');
    expect(screen.getByText(/58 playable cards/)).toBeVisible();
  });

  test('unsent card tag text survives a copilot refresh and only its field is reapplied', async () => {
    const changed=vi.fn();
    const props={downloaded:[summary],account,openSettings:vi.fn(),onImported:vi.fn(),openAssistant:vi.fn(),onDirtyChange:changed};
    const view=render(<DeckWorkshop {...props} revisionKey={0}/>);
    fireEvent.click(screen.getByRole('button',{name:'Open deck'}));await screen.findByLabelText('Deck name');
    fireEvent.change(screen.getByLabelText('Tags for Island'),{target:{value:'land, ComboPiece'}});
    expect(changed).toHaveBeenLastCalledWith(true);
    const remote={...structuredClone(deck),version:2,cards:[{...deck.cards[0],quantity:57}]};
    vi.mocked(api.loadDeckWorkspace).mockResolvedValue(remote);
    view.rerender(<DeckWorkshop {...props} revisionKey={1}/>);
    await screen.findByText('This local deck has a newer version');
    expect(screen.getByLabelText('Tags for Island')).toHaveValue('land, ComboPiece');
    fireEvent.click(screen.getByRole('button',{name:'Keep my edited details'}));
    await waitFor(()=>expect(api.saveDeckWorkspace).toHaveBeenCalledWith(deck.id,{expectedVersion:2,cards:[{...remote.cards[0],tags:['land','ComboPiece']}]}));
  });

  test('a local save conflict during publication never offers to overwrite the site draft', async () => {
    await openEditor();
    fireEvent.change(screen.getByLabelText('Deck name'),{target:{value:'Unsent'}});
    vi.mocked(api.saveDeckWorkspace).mockRejectedValueOnce(new api.ApiError('This local deck changed.',409));
    vi.mocked(api.loadDeckWorkspace).mockResolvedValue({...structuredClone(deck),version:2,name:'Copilot name'});
    fireEvent.click(screen.getByRole('button',{name:'Publish for League'}));
    await screen.findByText('This local deck has a newer version');
    expect(screen.queryByText('Choose which version to keep')).toBeNull();
    expect(api.syncDeckWorkspace).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button',{name:'Load latest deck'}));
    expect(screen.getByLabelText('Deck name')).toHaveValue('Copilot name');
  });

  test('a late local revision conflict from publishing is not mistaken for a remote conflict',async()=>{
    await openEditor();
    vi.mocked(api.syncDeckWorkspace).mockRejectedValueOnce(new api.ApiError('Local deck changed.',409,'local_deck_revision_conflict'));
    vi.mocked(api.loadDeckWorkspace).mockResolvedValue({...structuredClone(deck),name:'Newer local deck',version:2});
    fireEvent.click(screen.getByRole('button',{name:'Publish for League'}));
    await screen.findByText('This local deck has a newer version');
    expect(screen.queryByRole('button',{name:'Use this local version'})).toBeNull();
    expect(screen.getByRole('button',{name:'Keep my edited details'})).toBeDisabled();
  });

  test('a new chat-created deck opens when clean, but cannot replace an unsent deck draft',async()=>{
    const props={downloaded:[summary],account,openSettings:vi.fn(),onImported:vi.fn(),openAssistant:vi.fn()};
    const view=render(<DeckWorkshop {...props} revisionKey={0}/>);
    fireEvent.click(screen.getByRole('button',{name:'Open deck'}));await screen.findByLabelText('Deck name');
    fireEvent.change(screen.getByLabelText('Deck name'),{target:{value:'Must keep this draft'}});
    const created={...structuredClone(deck),id:'localdeck-abcdefabcdef',name:'Chat-created deck'};
    window.localStorage.setItem('deepdeck.activeDeckId',created.id);
    vi.mocked(api.loadDeckWorkspace).mockImplementation(async id=>id===created.id?created:structuredClone(deck));
    view.rerender(<DeckWorkshop {...props} revisionKey={1}/>);
    await waitFor(()=>expect(api.loadDeckWorkspace).toHaveBeenLastCalledWith(deck.id));
    expect(screen.getByLabelText('Deck name')).toHaveValue('Must keep this draft');
    expect(window.localStorage.getItem('deepdeck.activeDeckId')).toBe(deck.id);
    fireEvent.change(screen.getByLabelText('Deck name'),{target:{value:deck.name}});
    window.localStorage.setItem('deepdeck.activeDeckId',created.id);
    view.rerender(<DeckWorkshop {...props} revisionKey={2}/>);
    await waitFor(()=>expect(screen.getByLabelText('Deck name')).toHaveValue('Chat-created deck'));
  });

  test('study forms stay drafts and saving the deck commits their notes with a version guard',async()=>{
    await openEditor();
    fireEvent.click(screen.getByRole('button',{name:'+ Add combo note'}));
    expect(screen.getByRole('button',{name:'Save deck'})).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Study note name'),{target:{value:'My plan'}});
    fireEvent.change(screen.getByLabelText('Study cards · one copy per line'),{target:{value:'Island'}});
    fireEvent.click(screen.getByRole('button',{name:'Apply study note to draft'}));
    expect(api.saveDeckWorkspace).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button',{name:'Save deck changes'}));
    await waitFor(()=>expect(api.saveDeckWorkspace).toHaveBeenCalledWith(deck.id,expect.objectContaining({expectedVersion:1,combos:[{name:'My plan',cards:['Island'],notes:''}],openingHands:[]})));
  });

  test('automatic card edits do not silently commit or erase applied but unsaved study notes',async()=>{
    await openEditor();
    vi.mocked(api.saveDeckWorkspace).mockImplementation(async(_,payload)=>({...structuredClone(deck),...payload,version:2}));
    fireEvent.click(screen.getByRole('button',{name:'+ Add combo note'}));
    fireEvent.change(screen.getByLabelText('Study cards · one copy per line'),{target:{value:'Island'}});
    fireEvent.click(screen.getByRole('button',{name:'Apply study note to draft'}));
    fireEvent.click(screen.getByRole('button',{name:'＋'}));
    await waitFor(()=>expect(api.saveDeckWorkspace).toHaveBeenCalledOnce());
    expect(vi.mocked(api.saveDeckWorkspace).mock.calls[0][1]).not.toHaveProperty('combos');
    await screen.findByText('Card updated.');
    expect(screen.getByText('Combo 1')).toBeVisible();
    expect(screen.getByText(/Study changes are not saved yet/)).toBeVisible();
    fireEvent.click(screen.getByRole('button',{name:'Save deck'}));
    await waitFor(()=>expect(api.saveDeckWorkspace).toHaveBeenLastCalledWith(deck.id,expect.objectContaining({expectedVersion:2,combos:[expect.objectContaining({name:'Combo 1'})]})));
  });

  test('study conflict reapply preserves unrelated notes added by the copilot',async()=>{
    const props={downloaded:[summary],account,openSettings:vi.fn(),onImported:vi.fn(),openAssistant:vi.fn()};
    const view=render(<DeckWorkshop {...props} revisionKey={0}/>);
    fireEvent.click(screen.getByRole('button',{name:'Open deck'}));await screen.findByLabelText('Deck name');
    fireEvent.click(screen.getByRole('button',{name:'+ Add combo note'}));
    fireEvent.change(screen.getByLabelText('Study note name'),{target:{value:'Our plan'}});
    fireEvent.change(screen.getByLabelText('Study cards · one copy per line'),{target:{value:'Island'}});
    fireEvent.click(screen.getByRole('button',{name:'Apply study note to draft'}));
    const added={name:'Chat plan',cards:['Brainstorm'],notes:'Added in another window'};
    vi.mocked(api.loadDeckWorkspace).mockResolvedValue({...structuredClone(deck),version:2,combos:[added]});
    view.rerender(<DeckWorkshop {...props} revisionKey={1}/>);
    await screen.findByText('This local deck has a newer version');
    fireEvent.click(screen.getByRole('button',{name:'Keep my edited details'}));
    await waitFor(()=>expect(api.saveDeckWorkspace).toHaveBeenCalledWith(deck.id,{expectedVersion:2,combos:[added,{name:'Our plan',cards:['Island'],notes:''}]}));
  });

  test('a late older read cannot roll back a successfully saved local deck',async()=>{
    const props={downloaded:[summary],account,openSettings:vi.fn(),onImported:vi.fn(),openAssistant:vi.fn()};
    const view=render(<DeckWorkshop {...props} revisionKey={0}/>);
    fireEvent.click(screen.getByRole('button',{name:'Open deck'}));await screen.findByLabelText('Deck name');
    vi.mocked(api.saveDeckWorkspace).mockResolvedValue({...structuredClone(deck),version:2,name:'Saved new name'});
    fireEvent.change(screen.getByLabelText('Deck name'),{target:{value:'Saved new name'}});
    fireEvent.click(screen.getByRole('button',{name:'Save deck'}));await screen.findByText('Deck details saved.');
    view.rerender(<DeckWorkshop {...props} revisionKey={1}/>);
    await waitFor(()=>expect(api.loadDeckWorkspace).toHaveBeenCalledTimes(2));
    expect(screen.getByLabelText('Deck name')).toHaveValue('Saved new name');
    expect(screen.getByText(/revision 2/)).toBeVisible();
  });
});
