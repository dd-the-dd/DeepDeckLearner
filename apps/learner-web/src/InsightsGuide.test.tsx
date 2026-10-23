import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, test } from 'vitest';
import InsightsGuide from './InsightsGuide';

afterEach(cleanup);

test('explains uncertainty with a concrete example and separates it from next-match prediction', () => {
  render(<InsightsGuide />);
  expect(screen.getByText('What does the 95% interval mean?')).toBeVisible();
  expect(screen.getByText(/10 wins in 20 matches/)).toBeVisible();
  expect(screen.getByText(/about 30–70%/)).toBeVisible();
  expect(screen.getByText('It does not mean a 95% chance of winning the next match.')).toBeVisible();
  expect(screen.getByText(/Compare like with like/)).toBeVisible();
});
