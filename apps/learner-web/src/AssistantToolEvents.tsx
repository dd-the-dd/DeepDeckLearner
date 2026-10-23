import { useState } from 'react';

import type { AssistantToolEvent, Job, ResearchCard } from './api';
import DeckInspectionSummary from './DeckInspectionSummary';
import { manaText, safeCardImage } from './AssistantMessage';

const LABELS: Record<string, string> = {
  search_cards: 'Card search', search_league_decks: 'League deck search', league_search_cards: 'League card research',
  set_card_search_filters: 'Advanced card search prepared',
  league_list_decks: 'Your site decks', league_get_deck_workspace: 'Site deck inspected', league_list_card_tags: 'Site tags inspected',
  create_deck: 'Local deck created', import_league_deck: 'Deck imported', add_card: 'Card added locally', edit_card: 'Card updated locally',
  import_decklist: 'Deck list imported locally', update_deck_cards: 'Deck list updated locally',
  move_card: 'Cards moved locally', set_card_relationship: 'Relationship score saved locally', update_deck_plan: 'Deck plan saved locally',
  create_agent: 'Agent created', update_agent: 'Strategy revision saved', start_playtest: 'Playtest started',
  get_deck: 'Local deck inspected', list_local_decks: 'Local decks inspected', get_agent: 'Agent inspected', list_agents: 'Agents inspected',
  list_decks: 'Downloaded decks inspected', list_heuristics: 'Strategy graph inspected', league_mcp_connection: 'League MCP connection',
  inspect_deck: 'Deck strategy inspected',
  navigate_app: 'Page suggested',
};

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

export default function AssistantToolEvents({ events, onAddCard, onOpenPlaytest, onNavigate, onOpenDeck, addZone = 'main deck', maxCards = 6, disabled = false }: { events: AssistantToolEvent[]; onAddCard?: (name: string) => Promise<void>; onOpenPlaytest?: (job: Job) => void; onNavigate?: (page: string) => void; onOpenDeck?: (id: string) => void; addZone?: string; maxCards?: number; disabled?: boolean }) {
  const [adding, setAdding] = useState('');
  const [error, setError] = useState('');
  async function add(name: string) {
    if (!onAddCard) return;
    setAdding(name); setError('');
    try { await onAddCard(name); }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'The card could not be added.'); }
    finally { setAdding(''); }
  }
  return <div className="assistant-tool-events" aria-label="Assistant actions">{events.map((event, index) => {
    const result = record(event.result);
    const isCardSearch = ['search_cards', 'set_card_search_filters', 'league_search_cards'].includes(event.tool);
    const cards = isCardSearch && Array.isArray(result.items) ? result.items.filter((item): item is ResearchCard => typeof record(item).name === 'string').slice(0, maxCards) : [];
    const query = result.query || event.arguments?.q || event.arguments?.query;
    const label = LABELS[event.tool] || event.tool.replaceAll('_', ' ');
    const playtest = event.ok && event.tool === 'start_playtest' && typeof result.id === 'string' && result.kind === 'playtest.table';
    const inspection = event.ok && event.tool === 'inspect_deck' && result.schemaVersion === 'deepdeck-deck-inspection/v1' && result.readOnly === true;
    const navigation = event.ok && event.tool === 'navigate_app' && typeof result.page === 'string' && ['play', 'decks', 'agents', 'league', 'competitions', 'replays', 'insights'].includes(result.page);
    const savedDeck = event.ok && ['create_deck', 'import_decklist', 'import_league_deck', 'update_deck_cards', 'add_card', 'edit_card', 'move_card', 'set_card_relationship', 'update_deck_plan'].includes(event.tool) && typeof result.id === 'string' && /^localdeck-[a-f0-9]{12}$/.test(result.id);
    return <details className={`assistant-tool-event ${event.ok ? 'succeeded' : 'failed'}`} key={`${event.tool}-${index}`} open={!event.ok || cards.length > 0 || playtest || inspection || navigation || savedDeck}>
      <summary><span aria-hidden="true">{event.ok ? '✓' : '!'}</span>{label}<small>{navigation ? 'Your choice' : event.transport === 'mcp' ? 'MCP' : result.source === 'local-cache' ? 'Local cache' : event.ok ? 'Done' : 'Needs attention'}</small></summary>
      {!event.ok ? <><p role="status">{event.error || 'This action did not complete. Your local work remains available.'}</p>
        {event.tool === 'league_mcp_connection' && <div className="league-access-actions">
          {event.errorCode === 'daily_quota_reached' || event.errorCode === 'rate_limited' ? <><p>{event.errorCode === 'daily_quota_reached' ? 'Your daily tool allowance resets at 00:00 UTC. Patreon offers a higher allowance.' : 'Wait before retrying; the server is limiting request bursts.'} Keep your current key.</p><a href="https://www.deepdeckleague.com/account" target="_blank" rel="noreferrer">View my MCP allowance ↗</a><a href="https://www.deepdeckleague.com/support" target="_blank" rel="noreferrer">Compare Patreon allowances ↗</a></> : event.errorCode === 'protocol_mismatch' ? <><p>MCP compatibility needs a software update. Keep your current credential; reconnecting or changing membership is not the fix for this response-format error.</p><a href="https://www.deepdeckleague.com/developers" target="_blank" rel="noreferrer">League MCP compatibility information ↗</a></> : <>
          {(event.errorCode === 'membership_required' || (!event.errorCode && /patreon/i.test(event.error || ''))) && <><p>The server requires an active Patreon membership for MCP. If you already subscribe, connect Patreon to your League account, then test again. Your LLM subscription is separate.</p><a href="https://www.deepdeckleague.com/support" target="_blank" rel="noreferrer">View Patreon membership options ↗</a></>}
          <a href="https://www.deepdeckleague.com/account" target="_blank" rel="noreferrer">Sign in / manage my League account ↗</a>
          <button type="button" onClick={()=>window.dispatchEvent(new Event('deepdeck:open-league-settings'))}>Connect with a League API key</button>
          </>}
        </div>}
      </> : <>
        {Boolean(query) && <code className="assistant-search-query">{String(query)}</code>}
        {isCardSearch && typeof result.source==='string' && <p className="assistant-search-source">Source: {result.source==='scryfall'?'Scryfall public catalog':'DeepDeck League catalog'}</p>}
        {isCardSearch && result.filters && typeof result.filters==='object' ? <p className="assistant-filter-status">Advanced filters prepared for the matching deck editor. No card was added.</p> : null}
        {typeof result.name === 'string' && <p>{result.name}{typeof result.revision === 'number' ? ` · revision ${result.revision}` : ''}</p>}
        {typeof result.warning === 'string' && <p>{result.warning}</p>}
        {inspection && <DeckInspectionSummary result={result} />}
        {navigation && onNavigate && <button type="button" disabled={disabled} onClick={() => onNavigate(String(result.page))}>Open page: {String(result.page)}</button>}
        {savedDeck && <><p>Saved on this computer{typeof result.version === 'number' ? ` · revision ${result.version}` : ''}. Not published to the League.</p>{onOpenDeck && <button type="button" disabled={disabled} onClick={() => onOpenDeck(String(result.id))}>Open deck in editor</button>}</>}
        {playtest && onOpenPlaytest && <button type="button" className="assistant-open-table" disabled={disabled} onClick={() => onOpenPlaytest(event.result as Job)}>Open table</button>}
        {Array.isArray(result.items) && <p>{isCardSearch ? cards.length : result.items.length} result{(isCardSearch ? cards.length : result.items.length) === 1 ? '' : 's'} shown{typeof result.omittedItems === 'number' && result.omittedItems + result.items.length - cards.length > 0 && isCardSearch ? ` · ${result.omittedItems + result.items.length - cards.length} more on this page; refine filters` : ''}</p>}
        {cards.length > 0 && <div className="assistant-card-results">{cards.map((card, cardIndex) => <div key={`${card.name}-${cardIndex}`}>{safeCardImage(card.imageUri || card.imageUrl || card.image_uris?.normal) && <img className="assistant-card-art" src={safeCardImage(card.imageUri || card.imageUrl || card.image_uris?.normal)} alt={card.name} loading="lazy" onError={event=>{event.currentTarget.hidden=true;}}/>}<div><strong>{card.name}</strong><small>{card.typeLine || card.type_line} {manaText(card.manaCost || card.mana_cost || '')}</small><p>{manaText(card.oracleText || card.oracle_text || '')}</p></div>{onAddCard && <button type="button" disabled={Boolean(adding) || disabled} onClick={() => void add(card.name)} aria-label={`Add ${card.name} to ${addZone}`}>{adding === card.name ? 'Adding…' : '+ Add'}</button>}</div>)}</div>}
        {event.tool === 'league_mcp_connection' && <p>Connected for research. Deck edits stay local until you publish them.</p>}
      </>}
    </details>;
  })}{error && <p className="inline-error" role="alert">{error}</p>}</div>;
}
