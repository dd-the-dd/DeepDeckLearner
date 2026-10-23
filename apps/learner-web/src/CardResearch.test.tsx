import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import CardResearch from './CardResearch';
import * as api from './api';
import type { CardResearch as Result } from './api';

vi.mock('./api', async original => ({...await original<typeof import('./api')>(), researchCards: vi.fn()}));
const first: Result = {source:'league',query:'o:"draw"',items:[{name:'First card'}],resultWindow:{page:1,pageOffset:0,shown:1,totalOnPage:25,next:{page:1,page_offset:20},navigationLimitReached:false}};
const second: Result = {...first,items:[{name:'Later card'}],resultWindow:{...first.resultWindow!,pageOffset:20,shown:5,next:{page:2,page_offset:0}}};
beforeEach(() => {vi.resetAllMocks();vi.mocked(api.researchCards).mockResolvedValue(first);});
afterEach(cleanup);
async function start() {
  fireEvent.click(screen.getByText('Find cards with advanced filters'));
  fireEvent.change(screen.getByLabelText('Rules text contains'),{target:{value:'draw'}});
  fireEvent.click(screen.getByRole('button',{name:'Search Legacy cards'}));
  await screen.findByText('First card');
}

test('next and previous use submitted filters, even when the form has changed',async()=>{
  render(<CardResearch format="legacy" zone="Sideboard" disabled={false} onAdd={vi.fn()} />);
  await start();
  expect(screen.getByRole('button',{name:'Previous results'})).toBeDisabled();
  fireEvent.change(screen.getByLabelText('Rules text contains'),{target:{value:'destroy'}});
  expect(screen.getByRole('status')).toHaveTextContent('last submitted search');
  vi.mocked(api.researchCards).mockResolvedValueOnce(second);
  fireEvent.click(screen.getByRole('button',{name:'Next results'}));
  await screen.findByText('Later card');
  expect(api.researchCards).toHaveBeenLastCalledWith(expect.objectContaining({oracle_contains:'draw',page:1,page_offset:20}));
  expect(screen.getByText(/cards 21–25 of 25/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:'Previous results'}));
  await screen.findByText('First card');
  expect(api.researchCards).toHaveBeenLastCalledWith(expect.objectContaining({oracle_contains:'draw',page:1,page_offset:0}));
  fireEvent.click(screen.getByRole('button',{name:'Search Legacy cards'}));
  await waitFor(()=>expect(api.researchCards).toHaveBeenLastCalledWith(expect.objectContaining({oracle_contains:'destroy',page:1,page_offset:0})));
});

test('a failed next page preserves results and retries the same location',async()=>{
  render(<CardResearch format="legacy" zone="Main deck" disabled={false} onAdd={vi.fn()} />);
  await start();
  vi.mocked(api.researchCards).mockRejectedValueOnce(new Error('Search temporarily unavailable'));
  fireEvent.click(screen.getByRole('button',{name:'Next results'}));
  expect(await screen.findByRole('alert')).toHaveTextContent('temporarily unavailable');
  expect(screen.getByText('First card')).toBeInTheDocument();
  expect(screen.getByRole('button',{name:'Previous results'})).toBeDisabled();
  vi.mocked(api.researchCards).mockResolvedValueOnce(second);
  fireEvent.click(screen.getByRole('button',{name:'Next results'}));
  await screen.findByText('Later card');
  expect(vi.mocked(api.researchCards).mock.calls.slice(1).map(([args])=>[args.page,args.page_offset])).toEqual([[1,20],[1,20]]);
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});

test('the last window has no fabricated next page and results still add to the chosen zone',async()=>{
  const add = vi.fn().mockResolvedValue(undefined);
  vi.mocked(api.researchCards).mockResolvedValue({...first,resultWindow:{...first.resultWindow!,next:null}});
  render(<CardResearch format="legacy" zone="Considering" disabled={false} onAdd={add} />);
  await start();
  expect(screen.getByRole('button',{name:'Next results'})).toBeDisabled();
  fireEvent.click(screen.getByRole('button',{name:'Add First card to Considering'}));
  await waitFor(()=>expect(add).toHaveBeenCalledWith('First card'));
});

test('a late response cannot populate a changed game format',async()=>{
  let finish!: (value:Result)=>void;
  vi.mocked(api.researchCards).mockReturnValueOnce(new Promise(resolve=>{finish=resolve;}));
  const view=render(<CardResearch format="legacy" zone="Main deck" disabled={false} onAdd={vi.fn()} />);
  fireEvent.click(screen.getByText('Find cards with advanced filters'));
  fireEvent.change(screen.getByLabelText('Rules text contains'),{target:{value:'draw'}});
  fireEvent.click(screen.getByRole('button',{name:'Search Legacy cards'}));
  view.rerender(<CardResearch format="commander" zone="Main deck" disabled={false} onAdd={vi.fn()} />);
  await act(async()=>{finish(first);});
  expect(screen.queryByText('First card')).not.toBeInTheDocument();
  expect(screen.getByRole('button',{name:'Search Commander cards'})).toBeEnabled();
});
