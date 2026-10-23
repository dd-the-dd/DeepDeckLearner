import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, test } from 'vitest';
import AssistantToolEvents from './AssistantToolEvents';

afterEach(cleanup);
const result = {
  schemaVersion: 'deepdeck-deck-inspection/v1', readOnly: true, name: 'Combo deck', deckRevision: 4, agentRevision: 2,
  cards: [{name: 'Ponder', quantity: 4, section: 'main', deckTags: ['combo-search'], activeAgentTags: ['sorcery', 'card_selection']}],
  pagination: {returned: 1, total: 15, hasMore: true},
  studyNotes: {combos: [{name: 'Find protection', cards: ['Ponder'], notes: 'Protect the combo.'}], openingHands: [{name: 'Example hand', keep: true}]},
  notePagination: {combos: {returned: 1, total: 3, hasMore: true}, openingHands: {returned: 1, total: 1}},
  relationships: [{leftCard: 'Ponder', rightCard: 'Force of Will', kind: 'findabilityLinks', directed: true, score: 2}],
  relationshipPagination: {returned: 1, total: 1}, secret: 'should-never-be-rendered',
};

test('inspection is visible and distinguishes personal annotations, active rules and paged evidence', () => {
  render(<AssistantToolEvents events={[{tool: 'inspect_deck', ok: true, result}]} />);
  expect(screen.getByText('Deck strategy inspected').closest('details')).toHaveAttribute('open');
  expect(screen.getByText(/Read-only local snapshot/)).toHaveTextContent('deck revision 4 · agent revision 2');
  expect(screen.getByText('Deck annotations: combo-search')).toBeInTheDocument();
  expect(screen.getByText('Active strategy tags: sorcery, card_selection')).toBeInTheDocument();
  expect(screen.getByText(/Personal deck tags and study notes do not change/)).toBeInTheDocument();
  expect(screen.getByText('1 of 15 card entries on this page · more pages available')).toBeInTheDocument();
  expect(screen.getByText('1 of 3 combo notes on this page · more pages available')).toBeInTheDocument();
  expect(screen.getByText('Ponder → Force of Will')).toBeInTheDocument();
  expect(screen.queryByText('should-never-be-rendered')).not.toBeInTheDocument();
  expect(screen.queryByRole('button')).not.toBeInTheDocument();
});

test('no selected agent does not imply annotated tags are active', () => {
  render(<AssistantToolEvents events={[{tool: 'inspect_deck', ok: true, result: {...result, agentRevision: null, cards: [{name: 'Ponder', deckTags: ['combo-search'], activeAgentTags: null}]}}]} />);
  expect(screen.getByText(/no agent selected/)).toBeInTheDocument();
  expect(screen.getByText('Active strategy tags: select an agent to inspect its rules')).toBeInTheDocument();
});

test('failed inspection is an error, never an inspection receipt', () => {
  render(<AssistantToolEvents events={[{tool: 'inspect_deck', ok: false, error: 'Download this deck first.'}]} />);
  expect(screen.getByRole('status')).toHaveTextContent('Download this deck first.');
  expect(screen.queryByLabelText('Deck strategy inspection')).not.toBeInTheDocument();
});

test('note text is plain text and malformed optional fields are safe', () => {
  render(<AssistantToolEvents events={[{tool: 'inspect_deck', ok: true, result: {...result, cards: [null], pagination: null, studyNotes: {combos: [{name: 'Imported', notes: '<script>window.bad = true</script>'}]}, notePagination: null}}]} />);
  expect(screen.getByText('<script>window.bad = true</script>')).toBeInTheDocument();
  expect(document.querySelector('script')).toBeNull();
});
