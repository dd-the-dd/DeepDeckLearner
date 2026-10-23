import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import AgentHistory from './AgentHistory';
import AgentWorkspace from './AgentWorkspace';
import * as api from './api';
import type { DeckSummary, HeuristicCatalog, ProgrammaticAgent } from './api';

vi.mock('./api', async (original) => ({ ...await original<typeof import('./api')>(),
  createProgrammaticAgent: vi.fn(), loadDownloadedDecks: vi.fn(), loadHeuristicCatalog: vi.fn(), loadProgrammaticAgents: vi.fn(),
  updateProgrammaticAgent: vi.fn(), loadAgentRevisions: vi.fn(), restoreAgentRevision: vi.fn(), downloadDeck: vi.fn(), searchDeckPage: vi.fn(),
}));
const tempo: DeckSummary = { id: 'localdeck-123456789abc', name: 'Local Tempo', format: 'legacy', version: 1, colors: ['U'], playableCardCount: 60 };
const combo: DeckSummary = { ...tempo, id: 'league-combo', name: 'League Combo' };
const commander: DeckSummary = { ...tempo, id: 'commander-deck', name: 'Commander list', format: 'commander', playableCardCount: 100 };
const agent: ProgrammaticAgent = {
  schemaVersion: 'deepdeck-programmatic-agent/v1', id: 'programmatic-123456789abc', name: 'My Combo Pilot', description: '', format: 'legacy', profile: 'balanced', deckVersionIds: [tempo.id], decks: [tempo],
  heuristics: { resource_preservation: 1, timing_patience: 1, protect_advantage: 1, combat_survival: 1, interaction: 1 },
  strategyGraph: { version: 'strategy-graph/v1', archetypeMix: { aggro: 0, tempo: 0, midrange: 0, control: 0, combo: 1 }, deckAdaptation: .65, weights: {}, temperature: 0, seed: 0, patterns: [] },
  cardPreferences: { prefer: ['Emrakul, the Aeons Torn'], avoid: [], holdForInteraction: [] }, strategyNotes: '', revisionReason: '', revision: 7, createdAt: '', updatedAt: '',
};
const catalog: HeuristicCatalog = {
  schemaVersion: 'deepdeck-heuristic-catalog/v2', modelVersion: 'strategy-graph/v1', weightRange: { minimum: 0, maximum: 2, default: 1 }, temperatureRange: { minimum: 0, maximum: 1, default: 0 },
  deckAdaptationRange: { minimum: 0, maximum: 1, default: .65 }, archetypes: ['aggro', 'tempo', 'midrange', 'control', 'combo'], profiles: ['balanced', 'cautious', 'aggressive'],
  items: [{ id: 'protect_advantage', name: 'Protect an advantage', description: 'Protect the plan.' }, { id: 'resource_preservation', name: 'Preserve resources', description: 'Keep interaction.' }],
};
function props() { return { refresh: vi.fn(), openPlaytest: vi.fn(), openAssistant: vi.fn(), openLeague: vi.fn(), onDirtyChange: vi.fn() }; }
async function loaded() { await screen.findByRole('region', { name: 'Agent strategy editor' }); }
beforeEach(() => {
  vi.clearAllMocks(); window.localStorage.clear();
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', { configurable: true, value: function (this: HTMLDialogElement) { this.open = true; } });
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  vi.mocked(api.loadProgrammaticAgents).mockResolvedValue([structuredClone(agent)]);
  vi.mocked(api.loadDownloadedDecks).mockResolvedValue([tempo, combo, commander]);
  vi.mocked(api.loadHeuristicCatalog).mockResolvedValue(catalog);
  vi.mocked(api.updateProgrammaticAgent).mockImplementation(async (_, payload) => ({ ...agent, ...payload, strategyGraph: { ...agent.strategyGraph, ...(payload.strategyGraph as object) }, revision: 8 } as ProgrammaticAgent));
  vi.mocked(api.loadAgentRevisions).mockResolvedValue([]);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

test('a deck handoff prepares the correct format and pool, but creates only on confirmation',async()=>{
  const consumed=vi.fn();const callbacks=props();
  vi.mocked(api.createProgrammaticAgent).mockResolvedValue({...agent,id:'new-specialist',format:'commander',name:'Commander list pilot',deckVersionIds:[commander.id],decks:[commander]});
  const view=render(<AgentWorkspace {...callbacks} initialDeck={{id:commander.id,name:commander.name,format:'commander'}} onInitialDeckUsed={consumed}/>);
  await screen.findByRole('heading',{name:'Create your agent'});
  await waitFor(()=>expect(screen.getByRole('button',{name:'Create agent'})).toBeEnabled());
  expect(screen.getByLabelText('Agent name')).toHaveValue('Commander list pilot');
  expect(screen.getByLabelText('Format')).toHaveValue('commander');
  expect(screen.getByRole('button',{name:'Remove Commander list from agent'})).toBeEnabled();
  expect(consumed).toHaveBeenCalled();
  expect(api.createProgrammaticAgent).not.toHaveBeenCalled();
  view.rerender(<AgentWorkspace {...callbacks} initialDeck={null} onInitialDeckUsed={consumed}/>);
  expect(screen.getByLabelText('Agent name')).toHaveValue('Commander list pilot');
  fireEvent.click(screen.getByRole('button',{name:'Create agent'}));
  await waitFor(()=>expect(api.createProgrammaticAgent).toHaveBeenCalledWith(expect.objectContaining({name:'Commander list pilot',format:'commander',deckVersionIds:[commander.id]})));
  expect(callbacks.openPlaytest).not.toHaveBeenCalled();expect(callbacks.openLeague).not.toHaveBeenCalled();
});

test.each(['missing','wrong-format'])('an %s handoff must be corrected before creation',async mode=>{
  render(<AgentWorkspace {...props()} initialDeck={{id:mode==='missing'?'missing-deck':tempo.id,name:'A deck',format:'commander'}}/>);
  expect(await screen.findByRole('alert')).toHaveTextContent('unavailable or has a different format');
  expect(screen.getByRole('button',{name:'Create agent'})).toBeDisabled();
  expect(api.createProgrammaticAgent).not.toHaveBeenCalled();
});

test('decision nodes are revision-saved and unfinished node editors cannot be silently saved', async () => {
  render(<AgentWorkspace {...props()} />); await loaded();
  fireEvent.click(screen.getByRole('button', {name:'+ Add decision node'}));
  expect(screen.getByRole('button', {name:'Save strategy'})).toBeDisabled();
  expect(screen.getByRole('button', {name:'Playtest this agent →'})).toBeDisabled();
  fireEvent.change(screen.getByLabelText('Decision name'), {target:{value:'My new concern'}});
  fireEvent.click(screen.getByRole('button', {name:'Apply node to draft'}));
  fireEvent.click(screen.getByRole('button', {name:'Save strategy'}));
  await waitFor(() => expect(api.updateProgrammaticAgent).toHaveBeenCalledWith(agent.id,expect.objectContaining({expectedRevision:7,revisionReason:expect.stringContaining('custom decision nodes'),strategyGraph:expect.objectContaining({customNodes:[expect.objectContaining({name:'My new concern'})]})})));
});

test('creates an agent from a local draft without downloading or configuring an LLM', async () => {
  vi.mocked(api.createProgrammaticAgent).mockResolvedValue({ ...agent, id: 'programmatic-23456789abcd', name: 'A local pilot', revision: 1 });
  render(<AgentWorkspace {...props()} />); await loaded();
  fireEvent.click(screen.getByRole('button', { name: '+ New agent' }));
  fireEvent.change(screen.getByLabelText('Agent name'), { target: { value: 'A local pilot' } });
  fireEvent.change(screen.getByLabelText('Deck plan'), { target: { value: 'combo' } });
  fireEvent.change(screen.getByLabelText('Starting posture'), { target: { value: 'cautious' } });
  fireEvent.click(screen.getByRole('button', { name: 'Choose decks' }));
  const dialog = screen.getByRole('dialog');
  expect(within(dialog).queryByText('Commander list')).toBeNull();
  const row = within(dialog).getByText('Local Tempo').closest('article')!;
  fireEvent.click(within(row).getByRole('button', { name: 'Use deck' }));
  fireEvent.click(screen.getByRole('button', { name: 'Create agent' }));
  await waitFor(() => expect(api.createProgrammaticAgent).toHaveBeenCalledWith(expect.objectContaining({ name: 'A local pilot', deckVersionIds: [tempo.id], profile: 'cautious', heuristics: expect.objectContaining({ resource_preservation: 1.4 }), strategyGraph: expect.objectContaining({ archetypeMix: { aggro: 0, tempo: 0, midrange: 0, control: 0, combo: 1 } }) })));
  expect(api.downloadDeck).not.toHaveBeenCalled();
  await screen.findByText('Your agent is ready for a local playtest. No LLM is needed to play.');
});

test('adds mastered decks with the common picker and saves a revision-checked pool', async () => {
  render(<AgentWorkspace {...props()} />); await loaded();
  expect(screen.getByRole('button', { name: 'Remove Local Tempo from agent' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: '+ Add deck' }));
  expect(screen.getByRole('button', { name: 'Already selected' })).toBeDisabled();
  const row = within(screen.getByRole('dialog')).getByText('League Combo').closest('article')!;
  fireEvent.click(within(row).getByRole('button', { name: 'Use deck' }));
  fireEvent.click(screen.getByRole('button', { name: 'Save strategy' }));
  await waitFor(() => expect(api.updateProgrammaticAgent).toHaveBeenCalledWith(agent.id, expect.objectContaining({ expectedRevision: 7, deckVersionIds: [tempo.id, combo.id] })));
});

test('a patient posture increases preservation without replacing the combo plan', async () => {
  render(<AgentWorkspace {...props()} />); await loaded();
  fireEvent.change(screen.getByLabelText(/Posture preset/), { target: { value: 'cautious' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save strategy' }));
  await waitFor(() => expect(api.updateProgrammaticAgent).toHaveBeenCalledWith(agent.id, expect.objectContaining({
    profile: 'cautious', heuristics: expect.objectContaining({ resource_preservation: 1.4, protect_advantage: 1.4 }), strategyGraph: expect.objectContaining({ archetypeMix: agent.strategyGraph.archetypeMix }),
  })));
});

test('a background refresh preserves an unsaved draft and a newer revision requires reloading', async () => {
  const callbacks = props(); const view = render(<AgentWorkspace {...callbacks} revisionKey="first" />); await loaded();
  fireEvent.change(screen.getByLabelText('Agent name'), { target: { value: 'My unsaved idea' } });
  vi.mocked(api.loadProgrammaticAgents).mockResolvedValue([{ ...agent, name: 'Copilot update', revision: 8 }]);
  view.rerender(<AgentWorkspace {...callbacks} revisionKey="newer" />);
  expect(await screen.findByText('A newer strategy is available')).toBeVisible();
  expect(screen.getByLabelText('Agent name')).toHaveValue('My unsaved idea');
  expect(screen.getByRole('button', { name: 'Save strategy' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Discard draft & load latest' }));
  await waitFor(() => expect(screen.getByLabelText('Agent name')).toHaveValue('Copilot update'));
  expect(api.updateProgrammaticAgent).not.toHaveBeenCalled();
});

test('card rules are editable without code and incomplete rule forms block saves', async () => {
  render(<AgentWorkspace {...props()} />); await loaded();
  fireEvent.click(screen.getByRole('button', { name: '+ Add rule' }));
  fireEvent.change(screen.getByLabelText('Match these cards · one name per line'), { target: { value: 'Emrakul, the Aeons Torn' } });
  fireEvent.change(screen.getByLabelText('Add these strategy tags'), { target: { value: 'combo_payoff' } });
  expect(screen.getByRole('button', { name: 'Save strategy' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Apply rule to draft' }));
  fireEvent.click(screen.getByRole('button', { name: 'Save strategy' }));
  await waitFor(() => expect(api.updateProgrammaticAgent).toHaveBeenCalledWith(agent.id, expect.objectContaining({ strategyGraph: expect.objectContaining({ patterns: [expect.objectContaining({ cardNames: ['Emrakul, the Aeons Torn'], addTags: ['combo_payoff'] })] }) })));
});

test('card preferences keep commas inside actual Magic card names', async () => {
  render(<AgentWorkspace {...props()} />); await loaded();
  fireEvent.click(screen.getByText('Card preferences & reproducibility'));
  fireEvent.change(screen.getByLabelText('Prefer playing'), { target: { value: 'Emrakul, the Aeons Torn\nForce of Will' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save strategy' }));
  await waitFor(() => expect(api.updateProgrammaticAgent).toHaveBeenCalledWith(agent.id, expect.objectContaining({ cardPreferences: expect.objectContaining({ prefer: ['Emrakul, the Aeons Torn', 'Force of Will'] }) })));
});

test('playtest and League actions carry the currently selected saved agent', async () => {
  const callbacks = props(); render(<AgentWorkspace {...callbacks} />); await loaded();
  fireEvent.click(screen.getByRole('button', { name: 'Playtest this agent →' }));
  fireEvent.click(screen.getByRole('button', { name: 'Prepare for League →' }));
  expect(callbacks.openPlaytest).toHaveBeenCalledWith(agent);
  expect(callbacks.openLeague).toHaveBeenCalledWith(agent);
  fireEvent.change(screen.getByLabelText('Agent name'), { target: { value: 'Uncommitted name' } });
  expect(screen.getByRole('button', { name: 'Playtest this agent →' })).toBeDisabled();
  await waitFor(() => expect(callbacks.onDirtyChange).toHaveBeenLastCalledWith(true));
});

test('a revision conflict from the server never clears the user draft', async () => {
  vi.mocked(api.updateProgrammaticAgent).mockRejectedValue(new api.ApiError('The strategy changed elsewhere.', 409));
  render(<AgentWorkspace {...props()} />); await loaded();
  fireEvent.change(screen.getByLabelText('Agent name'), { target: { value: 'Keep this draft' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save strategy' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('changed elsewhere');
  expect(screen.getByLabelText('Agent name')).toHaveValue('Keep this draft');
});

test('history restores a strategy as a new version with the latest revision precondition', async () => {
  const restored = { ...agent, revision: 8 };
  vi.mocked(api.loadAgentRevisions).mockResolvedValue([{ revision: 6, name: agent.name, createdAt: '2026-10-18T00:00:00Z', reason: 'Before this experiment', profile: 'balanced', deckCount: 1, patternCount: 0, current: false, hasPythonSnapshot: true }]);
  vi.mocked(api.restoreAgentRevision).mockResolvedValue(restored);
  const accepted = vi.fn(); render(<AgentHistory agent={agent} dirty={false} onRestored={accepted} />);
  fireEvent.click(screen.getByText('Saved versions'));
  fireEvent.click(await screen.findByRole('button', { name: 'Restore v6' }));
  await waitFor(() => expect(api.restoreAgentRevision).toHaveBeenCalledWith(agent.id, 6, 7));
  await waitFor(() => expect(accepted).toHaveBeenCalledWith(restored));
  expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining('keeps your current Python'));
});
