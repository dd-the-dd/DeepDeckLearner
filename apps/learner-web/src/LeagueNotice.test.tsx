import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';

import LeagueNotice from './LeagueNotice';

afterEach(cleanup);

test.each([false, true])('a notice retains its accessible role and closes only on an explicit click (error=%s)', error => {
  const dismiss = vi.fn();
  render(<LeagueNotice error={error} label="League result" onDismiss={dismiss}>The controller result.</LeagueNotice>);
  expect(screen.getByRole(error ? 'alert' : 'status', { name: 'League result' })).toHaveTextContent('The controller result.');
  expect(dismiss).not.toHaveBeenCalled();
  const button = screen.getByRole('button', { name: 'Dismiss League result' });
  expect(button).toHaveAttribute('type', 'button');
  fireEvent.click(button);
  expect(dismiss).toHaveBeenCalledOnce();
});
