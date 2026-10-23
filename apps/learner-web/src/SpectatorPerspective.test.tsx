import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import SpectatorPerspective, { recordedCardIsHidden } from './SpectatorPerspective';

afterEach(cleanup);
test('supports every seat and global view, with an honest legacy-recording warning', () => {
  const change=vi.fn();
  render(<SpectatorPerspective value="global" onChange={change} view={{sessionId:'r',revision:1,state:{players:[{id:'a',name:'Alice',hand:[{instanceId:'hidden:hand:a:0'}]},{id:'b',name:'Bob',hand:[{name:'Ponder'}]}]}}} />);
  expect(screen.getAllByRole('option')).toHaveLength(3);
  expect(screen.getByRole('status')).toHaveTextContent('1 card hidden');
  fireEvent.change(screen.getByLabelText('Spectator perspective'),{target:{value:'b'}});
  expect(change).toHaveBeenCalledWith('b');
  expect(recordedCardIsHidden({definition:{id:'hidden-card',name:'Hidden card'}})).toBe(true);
  expect(recordedCardIsHidden({definition:{id:'ponder',name:'Ponder'}})).toBe(false);
});
