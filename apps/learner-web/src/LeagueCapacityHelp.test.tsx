import {cleanup, render, screen} from '@testing-library/react';
import {afterEach, expect, test} from 'vitest';
import LeagueCapacityHelp, {leagueSupportUrl} from './LeagueCapacityHelp';

afterEach(cleanup);

test('explains simultaneous places without classifying the user as free or inventing a quota', () => {
  render(<LeagueCapacityHelp/>);
  expect(screen.getByText(/Higher Patreon tiers include more simultaneous/)).toBeVisible();
  expect(screen.getByText(/Already supporting/)).toHaveTextContent('Check your League account access before upgrading');
  expect(screen.getByRole('link')).toHaveAttribute('href', 'https://www.deepdeckleague.com/support');
  expect(screen.getByRole('link')).toHaveAttribute('target', '_blank');
  expect(screen.getByRole('link')).toHaveAttribute('rel', 'noopener noreferrer');
  expect(document.body).not.toHaveTextContent(/free plan|12 places|4 places|8 places/i);
  expect(screen.queryByRole('button')).toBeNull();
});

test.each([
  ['https://staging.deepdeckleague.com', 'https://staging.deepdeckleague.com/support'],
  ['https://league.example.test/', 'https://league.example.test/support'],
  ['http://127.0.0.1:8796', 'http://127.0.0.1:8796/support'],
  ['http://localhost:8796', 'http://localhost:8796/support'],
] as const)('uses the selected server %s without billing or account writes', (url, expected) => {
  render(<LeagueCapacityHelp siteUrl={url}/>);
  expect(screen.getByRole('link')).toHaveAttribute('href', expected);
});

test.each([
  undefined, '', 'not a URL', 'javascript:alert(1)', 'http://example.test',
  'https://name:secret@example.test', 'https://example.test/?token=secret',
  'https://example.test/#token', 'file:///private',
])('never forwards credentials, fragments or unsafe URLs: %s', value => {
  expect(leagueSupportUrl(value)).toBe('https://www.deepdeckleague.com/support');
});
