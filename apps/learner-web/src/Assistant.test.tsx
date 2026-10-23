import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import { AssistantDrawer, AssistantSetup } from './PlayerApp';
import CardResearch from './CardResearch';
import * as api from './api';
import type { DeckWorkspace, Job, LlmSettings } from './api';

vi.mock('./api', async (original) => ({ ...await original<typeof import('./api')>(),
  saveLlmSettings: vi.fn(), loadLlmSettings: vi.fn(), testLeagueMcp: vi.fn(), chatWithAgentStudio: vi.fn(),
  researchCards: vi.fn(), loadDeckWorkspace: vi.fn(), saveDeckWorkspace: vi.fn(),
  loadProviderModels: vi.fn(), chatGPTAction: vi.fn(), loadChatGPTAccounts: vi.fn(), loadChatGPTModels: vi.fn(), loadChatGPTFlow: vi.fn(),
}));
const settings: LlmSettings = { providerName: 'My local provider', configured: true, baseUrl: 'http://localhost:11434/v1', model: 'test-local', hasApiKey: true, leagueMcpEnabled: false };
const deck: DeckWorkspace = { schemaVersion: 'deepdeck-local-deck/v1', id: 'localdeck-123456789abc', name: 'Tempo', description: '', format: 'legacy', version: 1, cards: [], combos: [], openingHands: [], origin: { kind: 'local' }, createdAt: '', updatedAt: '' };
beforeEach(() => {
  vi.clearAllMocks(); window.localStorage.clear();
  vi.mocked(api.saveLlmSettings).mockResolvedValue(settings);
  vi.mocked(api.loadLlmSettings).mockResolvedValue(settings);
  vi.mocked(api.loadDeckWorkspace).mockResolvedValue(deck);
  vi.mocked(api.saveDeckWorkspace).mockImplementation(async (_, payload) => ({ ...deck, ...payload } as DeckWorkspace));
  vi.mocked(api.loadChatGPTAccounts).mockResolvedValue({ accounts: [], activeAccountId: null, usageUrl: 'https://chatgpt.com/settings/usage' });
  vi.mocked(api.loadChatGPTModels).mockResolvedValue({ items: [] });
});
afterEach(cleanup);

test('streamed text is visible before completion and tools do not need a second request',async()=>{
  let finish!:(value:Awaited<ReturnType<typeof api.chatWithAgentStudio>>)=>void;
  vi.mocked(api.chatWithAgentStudio).mockImplementation(async (_messages,_context,_deck,_agent,onEvent)=>{
    onEvent!({type:'delta',text:'A first fragment {R}'});
    return new Promise(resolve=>{finish=resolve;});
  });
  render(<AssistantDrawer context="agent" open onClose={vi.fn()} onChanged={vi.fn()} onStarted={vi.fn()}/>);
  fireEvent.change(await screen.findByPlaceholderText('Describe a strategy change…'),{target:{value:'Help me choose cards'}});
  fireEvent.click(screen.getByRole('button',{name:'Send'}));
  expect(await screen.findByText('A first fragment')).toBeInTheDocument();
  expect(screen.getByRole('img',{name:'Mana cost {R}'})).toBeInTheDocument();
  expect(screen.getByRole('button',{name:'Send'})).toBeDisabled();
  finish({message:'Completed.',toolEvents:[],agents:[]});
  await screen.findByText('Completed.');
  expect(api.chatWithAgentStudio).toHaveBeenCalledOnce();
  vi.mocked(api.chatWithAgentStudio).mockReset();
});

test.each(['deck', 'agent'] as const)('Enter sends the %s chat, while Shift+Enter and IME input stay multiline', async context => {
  vi.mocked(api.chatWithAgentStudio).mockResolvedValue({ message: 'Saved.', toolEvents: [], agents: [] });
  render(<AssistantDrawer context={context} open onClose={vi.fn()} onChanged={vi.fn()} onStarted={vi.fn()} />);
  const input = await screen.findByTitle('Enter to send · Shift+Enter for a new line');
  fireEvent.change(input, { target: { value: 'First line\nSecond line' } });
  expect(fireEvent.keyDown(input, { key: 'Enter', shiftKey: true })).toBe(true);
  expect(fireEvent.keyDown(input, { key: 'Enter', isComposing: true })).toBe(true);
  expect(fireEvent.keyDown(input, { key: 'Enter', keyCode: 229 })).toBe(true);
  expect(fireEvent.keyDown(input, { key: 'Enter', repeat: true })).toBe(false);
  expect(api.chatWithAgentStudio).not.toHaveBeenCalled();
  expect(input).toHaveValue('First line\nSecond line');
  expect(fireEvent.keyDown(input, { key: 'Enter' })).toBe(false);
  await screen.findByText('Saved.');
  expect(api.chatWithAgentStudio).toHaveBeenCalledExactlyOnceWith(
    [{ role: 'user', content: `${context === 'deck' ? '[Deck workshop] ' : ''}First line\nSecond line` }], context, '', '', expect.any(Function),
  );
  expect(input).toHaveValue('');
});

test('Enter never sends whitespace or a second message while the assistant is working', async () => {
  let finish!: (value: Awaited<ReturnType<typeof api.chatWithAgentStudio>>) => void;
  vi.mocked(api.chatWithAgentStudio).mockReturnValue(new Promise(resolve => { finish = resolve; }));
  render(<AssistantDrawer context="agent" open onClose={vi.fn()} onChanged={vi.fn()} onStarted={vi.fn()} />);
  const input = await screen.findByTitle('Enter to send · Shift+Enter for a new line');
  fireEvent.change(input, { target: { value: '   ' } });
  fireEvent.keyDown(input, { key: 'Enter' });
  expect(api.chatWithAgentStudio).not.toHaveBeenCalled();
  fireEvent.change(input, { target: { value: 'Tune my agent' } });
  fireEvent.keyDown(input, { key: 'Enter' });
  await waitFor(() => expect(api.chatWithAgentStudio).toHaveBeenCalledOnce());
  fireEvent.change(input, { target: { value: 'My next draft' } });
  fireEvent.keyDown(input, { key: 'Enter' });
  expect(api.chatWithAgentStudio).toHaveBeenCalledOnce();
  expect(input).toHaveValue('My next draft');
  finish({ message: 'Done.', toolEvents: [], agents: [] });
  await screen.findByText('Done.');
});

test('the navigation tool shows an explicit action without navigating automatically', async () => {
  vi.mocked(api.chatWithAgentStudio).mockResolvedValue({message:'Open your recordings when ready.', toolEvents:[{tool:'navigate_app',ok:true,result:{page:'replays',requiresConfirmation:true,navigated:false}}],agents:[]});
  const navigate=vi.fn();
  render(<AssistantDrawer context="agent" open onClose={vi.fn()} onChanged={vi.fn()} onStarted={vi.fn()} onNavigate={navigate} />);
  fireEvent.change(await screen.findByPlaceholderText('Describe a strategy change…'), {target:{value:'Show my replays'}});
  fireEvent.click(screen.getByRole('button',{name:'Send'}));
  const button=await screen.findByRole('button',{name:'Open page: replays'});
  expect(button).toBeVisible();
  expect(navigate).not.toHaveBeenCalled();
  fireEvent.click(button);
  expect(navigate).toHaveBeenCalledExactlyOnceWith('replays');
});

test('editing provider settings preserves the saved secret unless explicitly removed', async () => {
  render(<AssistantSetup initial={settings} onSaved={vi.fn()} />);
  fireEvent.change(screen.getByLabelText('Model'), { target: { value: 'another-local-model' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save assistant settings' }));
  await waitFor(() => expect(api.saveLlmSettings).toHaveBeenCalledWith(expect.objectContaining({ model: 'another-local-model', leagueMcpEnabled: false })));
  expect(vi.mocked(api.saveLlmSettings).mock.calls[0][0]).not.toHaveProperty('apiKey');
  fireEvent.click(screen.getByLabelText('Remove saved model key'));
  fireEvent.click(screen.getByRole('button', { name: 'Save assistant settings' }));
  await waitFor(() => expect(api.saveLlmSettings).toHaveBeenLastCalledWith(expect.objectContaining({ apiKey: '' })));
});

test('optional League MCP can be tested without making an LLM request', async () => {
  vi.mocked(api.testLeagueMcp).mockResolvedValue({ connected: false, toolEvents: [{ tool: 'league_mcp_connection', transport: 'mcp', ok: false, error: 'Your production account needs MCP access.' }] });
  render(<AssistantSetup initial={settings} onSaved={vi.fn()} />);
  const button = screen.getByRole('button', { name: 'Test League tools · no LLM call' });
  expect(button).toBeDisabled();
  fireEvent.click(screen.getByLabelText('Use my League MCP connection'));
  fireEvent.click(button);
  expect(await screen.findByText('Your production account needs MCP access.')).toBeVisible();
  expect(api.chatWithAgentStudio).not.toHaveBeenCalled();
  expect(api.saveLlmSettings).not.toHaveBeenCalled();
});

test('a successful MCP check explicitly confirms access instead of treating the preference as proof',async()=>{
  vi.mocked(api.testLeagueMcp).mockResolvedValue({connected:true,toolEvents:[{tool:'league_mcp_connection',transport:'mcp',ok:true,result:{tools:['league_search_cards']}}]});
  render(<AssistantSetup initial={{...settings,leagueMcpEnabled:true}} onSaved={vi.fn()}/>);
  expect(screen.getByText(/MCP requested, not yet verified/)).toBeVisible();
  fireEvent.click(screen.getByRole('button',{name:'Test League tools · no LLM call'}));
  expect(await screen.findByText('MCP connected. Your permitted League research tools are available.')).toBeVisible();
  expect(api.chatWithAgentStudio).not.toHaveBeenCalled();
  expect(api.saveLlmSettings).not.toHaveBeenCalled();
});

const providers = [
  { id: 'chatgpt', name: 'ChatGPT subscription', baseUrl: 'https://api.openai.com/v1', auth: 'chatgpt', note: 'Eligible plans; shared limits.', docs: 'https://developers.openai.com/siwc/token-sharing-open-source' },
  { id: 'anthropic', name: 'Claude · Anthropic', baseUrl: 'https://api.anthropic.com/v1', auth: 'apiKey', note: 'API key, not Pro/Max sign-in.', docs: 'https://platform.claude.com/docs/en/cli-sdks-libraries/libraries/openai-sdk' },
  { id: 'custom', name: 'Custom', baseUrl: '', auth: 'custom', note: 'Compatible provider.', docs: '' },
];

test('changing provider clears typed credentials and fills the documented endpoint', async () => {
  render(<AssistantSetup initial={{ ...settings, providers }} onSaved={vi.fn()} />);
  fireEvent.change(screen.getByLabelText('API key'), { target: { value: 'old-provider-secret' } });
  fireEvent.change(screen.getByLabelText('LLM provider'), { target: { value: 'anthropic' } });
  expect(screen.getByLabelText('OpenAI-compatible URL')).toHaveValue('https://api.anthropic.com/v1');
  expect(screen.getByLabelText('API key')).toHaveValue('');
  expect(screen.queryByLabelText('Remove saved model key')).toBeNull();
  fireEvent.change(screen.getByLabelText('Model'), { target: { value: 'my-claude-model' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save assistant settings' }));
  await waitFor(() => expect(api.saveLlmSettings).toHaveBeenCalledWith(expect.objectContaining({ providerId: 'anthropic', authMode: 'apiKey' })));
  expect(vi.mocked(api.saveLlmSettings).mock.calls[0][0]).not.toHaveProperty('apiKey');
});

test('ChatGPT sign-in requires explicit action and never requests an API key', async () => {
  vi.mocked(api.chatGPTAction).mockResolvedValue({ accounts: [], activeAccountId: null, usageUrl: '', flowId: 'flow-test', status: 'pending' });
  render(<AssistantSetup initial={{ ...settings, providers }} onSaved={vi.fn()} />);
  fireEvent.change(screen.getByLabelText('LLM provider'), { target: { value: 'chatgpt' } });
  await screen.findByRole('button', { name: 'Continue with ChatGPT' });
  expect(screen.queryByLabelText('API key')).toBeNull();
  expect(api.chatGPTAction).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Continue with ChatGPT' }));
  await screen.findByText('Complete sign-in in your browser. Waiting for permission…');
  expect(api.chatGPTAction).toHaveBeenCalledWith('start', {});
  expect(screen.getByRole('button', { name: 'Save assistant settings' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Cancel sign-in' }));
  await waitFor(() => expect(api.chatGPTAction).toHaveBeenCalledWith('cancel', { flowId: 'flow-test' }));
  expect(api.chatWithAgentStudio).not.toHaveBeenCalled();
});

test('connected ChatGPT account uses its real model catalog before saving', async () => {
  const accounts = { accounts: [{ id: 'account-a', label: 'person@example.test', registration: '1234', connected: true }], activeAccountId: 'account-a', usageUrl: 'https://chatgpt.com/settings/usage' };
  vi.mocked(api.loadChatGPTAccounts).mockResolvedValue(accounts);
  vi.mocked(api.loadChatGPTModels).mockResolvedValue({ items: [{ id: 'account-model', name: 'My available model' }] });
  render(<AssistantSetup initial={{ ...settings, providers, chatgpt: accounts }} onSaved={vi.fn()} />);
  fireEvent.change(screen.getByLabelText('LLM provider'), { target: { value: 'chatgpt' } });
  await screen.findByRole('option', { name: 'My available model' });
  fireEvent.click(screen.getByRole('button', { name: 'Got it' }));
  expect(window.localStorage.getItem('deepdeck.chatgptPlanWelcomeSeen')).toBe('1');
  expect(screen.queryByRole('dialog')).toBeNull();
  fireEvent.change(screen.getByLabelText('ChatGPT model'), { target: { value: 'account-model' } });
  await waitFor(() => expect(screen.getByRole('button', { name: 'Save assistant settings' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: 'Save assistant settings' }));
  await waitFor(() => expect(api.saveLlmSettings).toHaveBeenCalledWith(expect.objectContaining({ authMode: 'chatgpt', providerId: 'chatgpt', model: 'account-model' })));
  expect(api.chatWithAgentStudio).not.toHaveBeenCalled();
});

test('model discovery lists provider IDs without generating a chat', async () => {
  vi.mocked(api.loadProviderModels).mockResolvedValue({ items: [{ id: 'local-model', name: 'Installed model' }] });
  render(<AssistantSetup initial={{ ...settings, providers }} onSaved={vi.fn()} />);
  fireEvent.click(screen.getByRole('button', { name: 'Load available models · no generation' }));
  await screen.findByRole('option', { name: 'Installed model' });
  fireEvent.change(screen.getByLabelText('Available models'), { target: { value: 'local-model' } });
  expect(screen.getByLabelText('Model')).toHaveValue('local-model');
  expect(api.chatWithAgentStudio).not.toHaveBeenCalled();
});

test('advanced search sends real filters and adds a result to the selected zone without an LLM', async () => {
  vi.mocked(api.researchCards).mockResolvedValue({ source: 'league', query: 'o:"draw" mv<=2', items: [{ name: 'Brainstorm', typeLine: 'Instant', manaCost: '{U}', oracleText: 'Draw three cards.' }] });
  const add = vi.fn().mockResolvedValue(undefined);
  render(<CardResearch format="legacy" zone="Sideboard" disabled={false} onAdd={add} />);
  fireEvent.click(screen.getByText('Find cards with advanced filters'));
  fireEvent.change(screen.getByLabelText('Rules text contains'), { target: { value: 'draw' } });
  fireEvent.change(screen.getByLabelText('Maximum mana value'), { target: { value: '2' } });
  fireEvent.click(screen.getByRole('button', { name: 'Search Legacy cards' }));
  await screen.findByText('Draw three cards.');
  expect(api.researchCards).toHaveBeenCalledWith(expect.objectContaining({ format: 'legacy', oracle_contains: 'draw', max_mana_value: 2 }));
  fireEvent.click(screen.getByRole('button', { name: 'Add Brainstorm to Sideboard' }));
  await waitFor(() => expect(add).toHaveBeenCalledWith('Brainstorm'));
  expect(api.chatWithAgentStudio).not.toHaveBeenCalled();
});

test('a search error is visible and never presented as successful results', async () => {
  vi.mocked(api.researchCards).mockRejectedValue(new Error('Semantic search is disabled on production.'));
  render(<CardResearch format="legacy" zone="Main deck" disabled={false} onAdd={vi.fn()} />);
  fireEvent.click(screen.getByText('Find cards with advanced filters'));
  fireEvent.change(screen.getByLabelText('Rules text contains'), { target: { value: 'draw' } });
  fireEvent.click(screen.getByRole('button', { name: 'Search Legacy cards' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('disabled on production');
  expect(screen.queryByText('Done')).toBeNull();
});

test('chat displays actual tools and refreshes relationship edits in the active deck', async () => {
  window.localStorage.setItem('deepdeck.activeDeckId', deck.id);
  vi.mocked(api.chatWithAgentStudio).mockResolvedValue({ message: 'Combo score updated.', toolEvents: [{ tool: 'set_card_relationship', ok: true, result: { name: 'Tempo' } }], agents: [] });
  const changed = vi.fn();
  render(<AssistantDrawer context="deck" open onClose={vi.fn()} onChanged={changed} onStarted={vi.fn()} />);
  await screen.findByText('Tell me what you want to build.');
  fireEvent.change(screen.getByPlaceholderText('Ask for cards, a deck plan, tags…'), { target: { value: 'Rate these cards as combo pieces.' } });
  fireEvent.click(screen.getByRole('button', { name: 'Send' }));
  expect(await screen.findByText('Relationship score saved locally')).toBeVisible();
  await waitFor(() => expect(changed).toHaveBeenCalledOnce());
  expect(api.chatWithAgentStudio).toHaveBeenCalledWith(expect.any(Array), 'deck', deck.id, '', expect.any(Function));
});

test.each(['import_decklist','import_league_deck','create_deck','update_deck_cards'])('global agent-focused chat refreshes a real %s deck and preserves its conversation', async tool => {
  const imported = {...deck, name:'Toph Metalbender', format:'commander' as const, version:3};
  vi.mocked(api.chatWithAgentStudio).mockResolvedValue({message:'Your editable local deck is saved.',toolEvents:[{tool,ok:true,result:imported}],agents:[]});
  const changed=vi.fn(), openDeck=vi.fn();
  const props={context:'agent' as const,onClose:vi.fn(),onChanged:changed,onStarted:vi.fn(),onOpenDeck:openDeck};
  const view=render(<AssistantDrawer {...props} open/>);
  const field=await screen.findByPlaceholderText('Describe a strategy change…');
  fireEvent.change(field,{target:{value:'Add the list yourself.'}});
  fireEvent.click(screen.getByRole('button',{name:'Send'}));
  await screen.findByText('Your editable local deck is saved.');
  await waitFor(()=>expect(changed).toHaveBeenCalledWith(deck.id));
  expect(window.localStorage.getItem('deepdeck.activeDeckId')).toBe(deck.id);
  expect(screen.getByText('Saved on this computer · revision 3. Not published to the League.')).toBeVisible();
  const action=screen.getByRole('button',{name:'Open deck in editor'});
  expect(openDeck).not.toHaveBeenCalled();
  fireEvent.click(action);
  expect(openDeck).toHaveBeenCalledExactlyOnceWith(deck.id);
  view.rerender(<AssistantDrawer {...props} open={false}/>);
  view.rerender(<AssistantDrawer {...props} open/>);
  expect(screen.getByText('Add the list yourself.')).toBeVisible();
  expect(screen.getByText('Your editable local deck is saved.')).toBeVisible();
  fireEvent.change(screen.getByPlaceholderText('Describe a strategy change…'),{target:{value:'Rename this deck.'}});
  fireEvent.click(screen.getByRole('button',{name:'Send'}));
  await waitFor(()=>expect(api.chatWithAgentStudio).toHaveBeenLastCalledWith(expect.any(Array),'agent',deck.id,'',expect.any(Function)));
});

test('a failed import never selects a deck or offers to open an invented copy',async()=>{
  vi.mocked(api.chatWithAgentStudio).mockResolvedValue({message:'A card name needs correction.',toolEvents:[{tool:'import_decklist',ok:false,error:'Missing card could not be resolved.'}],agents:[]});
  const changed=vi.fn();
  render(<AssistantDrawer context="agent" open onClose={vi.fn()} onChanged={changed} onStarted={vi.fn()} onOpenDeck={vi.fn()}/>);
  fireEvent.change(await screen.findByPlaceholderText('Describe a strategy change…'),{target:{value:'Import this list'}});
  fireEvent.click(screen.getByRole('button',{name:'Send'}));
  await screen.findByText('A card name needs correction.');
  expect(window.localStorage.getItem('deepdeck.activeDeckId')).toBeNull();
  expect(screen.queryByRole('button',{name:'Open deck in editor'})).toBeNull();
  expect(changed).not.toHaveBeenCalled();
});

test('a suggested card is only saved after the user clicks Add', async () => {
  window.localStorage.setItem('deepdeck.activeDeckId', deck.id);
  vi.mocked(api.chatWithAgentStudio).mockResolvedValue({ message: 'Here is one option.', toolEvents: [{ tool: 'search_cards', ok: true, result: { query: 'draw', items: [{ name: 'Brainstorm' }] } }], agents: [] });
  render(<AssistantDrawer context="deck" open onClose={vi.fn()} onChanged={vi.fn()} onStarted={vi.fn()} />);
  await screen.findByText('Tell me what you want to build.');
  fireEvent.change(screen.getByPlaceholderText('Ask for cards, a deck plan, tags…'), { target: { value: 'Suggest a draw spell.' } });
  fireEvent.click(screen.getByRole('button', { name: 'Send' }));
  const add = await screen.findByRole('button', { name: 'Add Brainstorm to main deck' });
  expect(api.saveDeckWorkspace).not.toHaveBeenCalled();
  fireEvent.click(add);
  await waitFor(() => expect(api.saveDeckWorkspace).toHaveBeenCalledWith(deck.id, { expectedVersion: deck.version, cards: [{ name: 'Brainstorm', quantity: 1, section: 'main', tags: [] }] }));
  await screen.findByText('Brainstorm added to the main deck.');
});

async function suggestedCard(changed = vi.fn()) {
  window.localStorage.setItem('deepdeck.activeDeckId', deck.id);
  vi.mocked(api.chatWithAgentStudio).mockResolvedValue({ message: 'Try this card.', toolEvents: [{tool: 'search_cards', ok: true, result: {items: [{name: 'Brainstorm'}]}}], agents: [] });
  render(<AssistantDrawer context="deck" open onClose={vi.fn()} onChanged={changed} onStarted={vi.fn()} />);
  await screen.findByText('Tell me what you want to build.');
  fireEvent.change(screen.getByPlaceholderText('Ask for cards, a deck plan, tags…'), {target: {value: 'Suggest a draw spell.'}});
  fireEvent.click(screen.getByRole('button', {name: 'Send'}));
  const add = await screen.findByRole('button', {name: 'Add Brainstorm to main deck'});
  await waitFor(() => expect(add).toBeEnabled());
  return add;
}

test('suggestion conflict does not retry automatically and a manual retry reads the latest deck', async () => {
  const add = await suggestedCard();
  vi.mocked(api.saveDeckWorkspace).mockRejectedValueOnce(new api.ApiError('Stale version', 409));
  fireEvent.click(add);
  expect(await screen.findByRole('alert')).toHaveTextContent('Nothing was overwritten');
  expect(api.saveDeckWorkspace).toHaveBeenCalledOnce();
  expect(screen.queryByText('Brainstorm added to the main deck.')).not.toBeInTheDocument();
  vi.mocked(api.loadDeckWorkspace).mockResolvedValue({...deck, version: 5, cards: [{name: 'Island', quantity: 20, section: 'main', tags: ['mana']}]});
  await waitFor(() => expect(add).toBeEnabled());
  fireEvent.click(add);
  await screen.findByText('Brainstorm added to the main deck.');
  expect(api.saveDeckWorkspace).toHaveBeenLastCalledWith(deck.id, {expectedVersion: 5, cards: [
    {name: 'Island', quantity: 20, section: 'main', tags: ['mana']}, {name: 'Brainstorm', quantity: 1, section: 'main', tags: []},
  ]});
  expect(api.chatWithAgentStudio).toHaveBeenCalledOnce();
});

test('confirmed card save with refresh failure reports success without encouraging a duplicate add', async () => {
  const changed = vi.fn().mockRejectedValue(new Error('Refresh offline'));
  const add = await suggestedCard(changed);
  fireEvent.click(add);
  await screen.findByText('Brainstorm added to the main deck.');
  expect(await screen.findByRole('alert')).toHaveTextContent('Brainstorm was saved');
  expect(screen.getByRole('alert')).toHaveTextContent('Do not add it again');
  expect(api.saveDeckWorkspace).toHaveBeenCalledOnce();
  expect(changed).toHaveBeenCalledOnce();
});

test('adding a suggested card locks the composer and duplicate clicks until the save finishes', async () => {
  let finish!: (value: DeckWorkspace) => void;
  vi.mocked(api.saveDeckWorkspace).mockReturnValue(new Promise(resolve => {finish = resolve;}));
  const add = await suggestedCard();
  fireEvent.click(add);
  await waitFor(() => expect(api.saveDeckWorkspace).toHaveBeenCalledOnce());
  expect(add).toBeDisabled();
  fireEvent.click(add);
  fireEvent.change(screen.getByPlaceholderText('Ask for cards, a deck plan, tags…'), {target: {value: 'Another change'}});
  expect(screen.getByRole('button', {name: 'Send'})).toBeDisabled();
  expect(api.saveDeckWorkspace).toHaveBeenCalledOnce();
  finish({...deck, version: 2});
  await screen.findByText('Brainstorm added to the main deck.');
  await waitFor(() => expect(screen.getByRole('button', {name: 'Send'})).toBeEnabled());
});

test('changing active deck while the suggestion reads cancels the write', async () => {
  let finish!: (value: DeckWorkspace) => void;
  vi.mocked(api.loadDeckWorkspace).mockReturnValue(new Promise(resolve => {finish = resolve;}));
  const add = await suggestedCard();
  fireEvent.click(add);
  await waitFor(() => expect(api.loadDeckWorkspace).toHaveBeenCalledOnce());
  window.localStorage.setItem('deepdeck.activeDeckId', 'different-deck');
  finish(deck);
  expect(await screen.findByRole('alert')).toHaveTextContent('No card was added');
  expect(api.saveDeckWorkspace).not.toHaveBeenCalled();
});

test('suggestion increments only the main-deck copy and preserves both zones and their tags', async () => {
  const cards: DeckWorkspace['cards'] = [
    {name: 'Brainstorm', quantity: 2, section: 'main', tags: ['selection']},
    {name: 'Brainstorm', quantity: 1, section: 'sideboard', tags: ['side-plan']},
  ];
  vi.mocked(api.loadDeckWorkspace).mockResolvedValue({...deck, version: 7, cards});
  const add = await suggestedCard();
  fireEvent.click(add);
  await screen.findByText('Brainstorm added to the main deck.');
  expect(api.saveDeckWorkspace).toHaveBeenCalledWith(deck.id, {expectedVersion: 7, cards: [
    {...cards[0], quantity: 3}, cards[1],
  ]});
  expect(cards[0].quantity).toBe(2);
  expect(cards[0].tags).toEqual(['selection']);
});

test.each([new TypeError('Failed to fetch'), new api.ApiError('Response unavailable', 503)])('uncertain card-save response warns to check quantity before retrying (%s)', async failure => {
  vi.mocked(api.saveDeckWorkspace).mockRejectedValueOnce(failure);
  const add = await suggestedCard();
  fireEvent.click(add);
  expect(await screen.findByRole('alert')).toHaveTextContent('Could not confirm whether Brainstorm was saved');
  expect(screen.getByRole('alert')).toHaveTextContent('check its quantity before adding it again');
  expect(api.saveDeckWorkspace).toHaveBeenCalledOnce();
  expect(screen.queryByText('Brainstorm added to the main deck.')).not.toBeInTheDocument();
});

const table = { id: 'chat-table', kind: 'playtest.table', status: 'queued', details: {} } as Job;
const startedReply = { message: 'Your table is preparing.', toolEvents: [{ tool: 'start_playtest', ok: true, result: table }], agents: [] };
async function requestPlaytest() {
  await screen.findByText('Describe how this agent should play.');
  fireEvent.change(screen.getByPlaceholderText('Describe a strategy change…'), { target: { value: 'Let me play against my agent now.' } });
  fireEvent.click(screen.getByRole('button', { name: 'Send' }));
}

test('a successful chat-created table opens without waiting for a slow library refresh', async () => {
  vi.mocked(api.chatWithAgentStudio).mockResolvedValue(startedReply);
  let refreshed!: () => void;
  const changed = vi.fn(() => new Promise<void>((resolve) => { refreshed = resolve; }));
  const started = vi.fn(); const close = vi.fn();
  render(<AssistantDrawer context="agent" open onClose={close} onChanged={changed} onStarted={started} />);
  await requestPlaytest();
  await waitFor(() => expect(started).toHaveBeenCalledWith(table));
  expect(close).toHaveBeenCalledOnce(); expect(changed).toHaveBeenCalledOnce();
  refreshed();
});

test('declining to leave an unsaved draft keeps a usable Open table action', async () => {
  vi.mocked(api.chatWithAgentStudio).mockResolvedValue(startedReply);
  const started = vi.fn().mockReturnValueOnce(false).mockReturnValue(true); const close = vi.fn();
  render(<AssistantDrawer context="agent" open onClose={close} onChanged={vi.fn()} onStarted={started} />);
  await requestPlaytest();
  const open = await screen.findByRole('button', { name: 'Open table' });
  await waitFor(() => expect(open).toBeEnabled());
  expect(close).not.toHaveBeenCalled();
  fireEvent.click(open);
  expect(started).toHaveBeenNthCalledWith(2, table); expect(close).toHaveBeenCalledOnce();
  expect(api.chatWithAgentStudio).toHaveBeenCalledOnce();
});

test('closing the assistant while it works never unexpectedly opens a background table', async () => {
  let reply!: (value: typeof startedReply) => void;
  vi.mocked(api.chatWithAgentStudio).mockReturnValue(new Promise((resolve) => { reply = resolve; }));
  const started = vi.fn(); const changed = vi.fn();
  const view = render(<AssistantDrawer context="agent" open onClose={vi.fn()} onChanged={changed} onStarted={started} />);
  await requestPlaytest();
  view.rerender(<AssistantDrawer context="agent" open={false} onClose={vi.fn()} onChanged={changed} onStarted={started} />);
  reply(startedReply);
  await waitFor(() => expect(changed).toHaveBeenCalledOnce());
  expect(started).not.toHaveBeenCalled();
  view.rerender(<AssistantDrawer context="agent" open onClose={vi.fn()} onChanged={changed} onStarted={started} />);
  expect(await screen.findByRole('button', { name: 'Open table' })).toBeEnabled();
});

test('a refresh failure reports completed actions without asking the user to repeat them', async () => {
  vi.mocked(api.chatWithAgentStudio).mockResolvedValue({ message: 'Strategy saved.', toolEvents: [{ tool: 'update_agent', ok: true, result: { name: 'Tempo pilot', revision: 3 } }], agents: [] });
  render(<AssistantDrawer context="agent" open onClose={vi.fn()} onChanged={() => Promise.reject(new Error('Offline'))} onStarted={vi.fn()} />);
  await requestPlaytest();
  expect(await screen.findByRole('alert')).toHaveTextContent('Your actions completed');
  expect(screen.getByText('Strategy revision saved')).toBeInTheDocument();
  expect(api.chatWithAgentStudio).toHaveBeenCalledOnce();
});
