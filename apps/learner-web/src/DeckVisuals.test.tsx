import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';

import { DeckArtwork, ManaColors, ManaCost } from './DeckVisuals';
import type { DeckSummary } from './api';

const deck: DeckSummary = { id: 'visual-fixture', name: 'Sneak Attack', version: 1, colors: ['U', 'R'], playableCardCount: 75 };
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

test('uses the site mana font glyphs, with accessible color names and no fake letter tokens', () => {
  render(<ManaColors colors={['u', 'R', 'R', 'invalid']} />);
  expect(screen.getByRole('img', { name: 'Blue, Red' })).toBeInTheDocument();
  expect(document.querySelector('.ms-u.ms-cost')).not.toBeNull();
  expect(document.querySelectorAll('.ms')).toHaveLength(2);
  expect(document.querySelector('.mana-u')).toBeNull();
});

test('renders colorless and hybrid mana costs with the real symbols', () => {
  render(<><ManaColors colors={[]} /><ManaCost value="{2}{U/B}{R}" /></>);
  expect(screen.getByRole('img', { name: 'Colorless' })).toBeInTheDocument();
  expect(screen.getByRole('img', { name: 'Mana cost {2}{U/B}{R}' })).toBeInTheDocument();
  expect(document.querySelector('.ms-ub')).not.toBeNull();
});

test('uses the supplied card immediately without any network call', () => {
  const fetch = vi.fn(); vi.stubGlobal('fetch', fetch);
  render(<DeckArtwork deck={{ ...deck, coverCard: { name: 'Sneak Attack', imageUrl: 'https://cards.example/sneak.jpg' } }} />);
  expect(screen.getByRole('img', { name: 'Sneak Attack' })).toHaveAttribute('src', 'https://cards.example/sneak.jpg');
  expect(fetch).not.toHaveBeenCalled();
});

test('fetches public artwork without importing the deck and shares simultaneous requests', async () => {
  const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ coverCard: { name: 'Threat', imageUrl: 'https://cards.example/threat.jpg' } }) });
  vi.stubGlobal('fetch', fetch);
  render(<><DeckArtwork deck={deck} /><DeckArtwork deck={deck} /></>);
  await waitFor(() => expect(screen.getAllByRole('img', { name: 'Threat' })).toHaveLength(2));
  expect(fetch).toHaveBeenCalledExactlyOnceWith('/api/v1/catalog/decks/visual-fixture/cover');
});

test('empty decks and failed images show an honest card placeholder, not a broken image', () => {
  const { rerender } = render(<DeckArtwork deck={{ ...deck, coverCard: null }} />);
  expect(screen.getByRole('img', { name: 'No card artwork available' })).toBeInTheDocument();
  rerender(<DeckArtwork deck={{ ...deck, coverCard: { name: 'Threat', imageUrl: 'https://cards.example/broken.jpg' } }} />);
  fireEvent.error(screen.getByRole('img', { name: 'Threat' }));
  expect(screen.getByRole('img', { name: 'No card artwork available' })).toBeInTheDocument();
});
