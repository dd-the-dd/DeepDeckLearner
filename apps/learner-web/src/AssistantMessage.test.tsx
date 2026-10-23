import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import AssistantMessage, {safeCardImage} from './AssistantMessage';
import {resolveMissingArtwork} from './missingCardArtwork';
import CardResearch from './CardResearch';
import {publishCardSearch} from './card-search-bridge';

vi.mock('./missingCardArtwork',()=>({resolveMissingArtwork:vi.fn().mockResolvedValue(null)}));
afterEach(()=>{cleanup();vi.clearAllMocks();});

test('mana costs and bare color identities display real accessible symbols',()=>{
  render(<AssistantMessage content="Spend {2}{U/B}{R}. My identity is WUBG. Keep {unknown} readable."/>);
  expect(screen.getByRole('img',{name:'Mana cost {2}{U/B}{R}'})).toBeInTheDocument();
  expect(screen.getByRole('img',{name:'Mana cost {W}{U}{B}{G}'})).toBeInTheDocument();
  expect(document.querySelector('.ms-ub')).not.toBeNull();
  expect(screen.getByText(/Keep \{unknown\} readable/)).toBeInTheDocument();
});

test.each(["'''",'```'])('complete %s card blocks use verified result artwork without another lookup',fence=>{
  render(<AssistantMessage content={`Try ${fence}magic_card -name "Lightning Bolt"${fence}`} events={[{tool:'search_cards',ok:true,result:{items:[{name:'Lightning Bolt',image_uris:{normal:'https://cards.scryfall.io/normal/front/a/b/card.jpg'},mana_cost:'{R}'}]}}]}/>);
  expect(screen.getByRole('img',{name:'Lightning Bolt'})).toHaveAttribute('src','/api/scryfall-images/normal/front/a/b/card.jpg');
  expect(resolveMissingArtwork).not.toHaveBeenCalled();
});

test('a card becomes visible only after its closing fence; arbitrary markup stays text',async()=>{
  const view=render(<AssistantMessage content={'\'\'\'magic_card -name "Atraxa, Praetors’ Voice"'}/>);
  expect(document.querySelector('figure')).toBeNull();
  view.rerender(<AssistantMessage content={'\'\'\'magic_card -name "Atraxa, Praetors’ Voice"\'\'\' <script>bad()</script>'}/>);
  expect(document.querySelector('figure')).not.toBeNull();
  expect(document.querySelector('script')).toBeNull();
  await waitFor(()=>expect(resolveMissingArtwork).toHaveBeenCalledOnce());
});

test('only known local/Scryfall image locations are rendered',()=>{
  for(const value of ['javascript:bad()','https://evil.example/image.jpg','https://cards.scryfall.io@evil.example/a.jpg']) expect(safeCardImage(value)).toBeUndefined();
});

test('assistant filters populate a matching deck search without mutating cards or fetching again',async()=>{
  const add=vi.fn();
  render(<CardResearch format="commander" deckId="chat-test-deck" zone="Main deck" disabled={false} onAdd={add}/>);
  const filters={format:'commander',query:'',oracle_contains:'',semantic_text:'',types:[],color_identity:'',identity_match:'within',commander_only:true,color_count:4};
  fireEvent(window,new CustomEvent('deepdeck:card-search-filters',{detail:{deckId:'other-deck',filters}}));
  expect(screen.getByLabelText('Number of identity colors')).toHaveValue('');
  fireEvent(window,new CustomEvent('deepdeck:card-search-filters',{detail:{deckId:'chat-test-deck',filters}}));
  expect(screen.getByLabelText('Number of identity colors')).toHaveValue('4');
  expect(screen.getByLabelText(/Eligible as a commander/)).toBeChecked();
  expect(document.querySelector<HTMLDetailsElement>('.advanced-card-search')!.open).toBe(true);
  expect(add).not.toHaveBeenCalled();
  expect(publishCardSearch({filters},'chat-test-deck')).toBe(true);
});
