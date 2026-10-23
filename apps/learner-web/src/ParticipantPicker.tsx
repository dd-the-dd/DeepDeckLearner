import type { DeckSummary, ProgrammaticAgent } from './api';
import HelpTip from './HelpTip';

export function ParticipantKindPicker({kind, onChange, humanDisabled = false}: {kind: 'human' | 'agent'; onChange: (kind: 'human' | 'agent') => void; humanDisabled?: boolean}) {
  return <div className="seat-kind" aria-label="Participant type"><button type="button" className={kind === 'human' ? 'active' : ''} aria-pressed={kind === 'human'} disabled={humanDisabled} onClick={() => onChange('human')}>Player</button><button type="button" className={kind === 'agent' ? 'active' : ''} aria-pressed={kind === 'agent'} onClick={() => onChange('agent')}>AI</button></div>;
}

export function ParticipantDeckPicker({kind, agentId, deckId, agents, decks, onAgent, onDeck, onBrowse, prefix = ''}: {
  kind: 'human' | 'agent'; agentId: string; deckId: string; agents: ProgrammaticAgent[]; decks: DeckSummary[];
  onAgent: (id: string) => void; onDeck: (id: string) => void; onBrowse: () => void; prefix?: string;
}) {
  return <>
    {kind === 'agent' && <label>Agent<HelpTip label={`${prefix}agent`}>Choose a saved programmatic AI that supports this game format. Its mastered decks determine the choices below.</HelpTip><select aria-label={`${prefix}agent`} value={agentId} onChange={e => onAgent(e.target.value)}><option value="">Choose an agent</option>{agents.map(agent => <option key={agent.id} value={agent.id}>{agent.name}</option>)}</select></label>}
    <label>Deck<HelpTip label={`${prefix}deck`}>{kind === 'human' ? 'Choose a local or imported deck. Browse opens the same local and League picker as a normal match.' : 'Choose a mastered deck, or draw a random mastered deck separately for every match, including matches in a playoff series.'}</HelpTip><select aria-label={`${prefix}deck`} value={deckId} onChange={e => onDeck(e.target.value)}><option value="">Choose a deck</option>{kind === 'agent' && <option value="random">Random from mastered decks · each match</option>}{decks.map(deck => <option key={deck.id} value={deck.id} disabled={Boolean(deck.unavailable)}>{deck.name}{deck.unavailable ? ' · unavailable' : ''}</option>)}</select></label>
    {kind === 'human' && <button type="button" className="browse-seat-decks" onClick={onBrowse}>Browse local & League decks</button>}
  </>;
}
