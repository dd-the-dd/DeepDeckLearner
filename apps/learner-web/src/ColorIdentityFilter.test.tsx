import { useState } from 'react';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import ColorIdentityFilter from './ColorIdentityFilter';
import CardResearch from './CardResearch';
import * as api from './api';

vi.mock('./api',async original=>({...await original<typeof import('./api')>(),researchCards:vi.fn()}));
beforeEach(()=>{vi.resetAllMocks();vi.mocked(api.researchCards).mockResolvedValue({items:[],query:'draw',source:'league'});});
afterEach(cleanup);
const labels: Record<string,string>={W:'White',U:'Blue',B:'Black',R:'Red',G:'Green'};
const combinations=Array.from({length:32},(_,mask)=>'WUBRG'.split('').filter((_,index)=>mask&(1<<index)).join(''));

test.each([...combinations,'C'])('manual research supports the complete identity %s',async identity=>{
  render(<CardResearch format="commander" zone="Main deck" disabled={false} onAdd={vi.fn()}/>);
  fireEvent.click(screen.getByText('Find cards with advanced filters'));
  fireEvent.change(screen.getByLabelText('Rules text contains'),{target:{value:'draw'}});
  const group=within(screen.getByRole('group',{name:'Within color identity'}));
  if(identity==='C') fireEvent.click(group.getByRole('button',{name:'Colorless only'}));
  else for(const color of identity) fireEvent.click(group.getByRole('button',{name:labels[color]}));
  fireEvent.click(screen.getByRole('button',{name:'Search Commander cards'}));
  await waitFor(()=>expect(api.researchCards).toHaveBeenCalledWith(expect.objectContaining({color_identity:identity,format:'commander'})));
});

test('colorless is exclusive, colored identities include colorless, and clearing restores any colors',()=>{
  function Harness(){const[value,setValue]=useState('');return <ColorIdentityFilter value={value} onChange={setValue}/>;}
  render(<Harness/>);
  const any=screen.getByRole('button',{name:'Any colors'});
  expect(any).toHaveAttribute('aria-pressed','true');
  fireEvent.click(screen.getByRole('button',{name:'Blue'}));
  fireEvent.click(screen.getByRole('button',{name:'Red'}));
  expect(screen.getByText(/Colorless cards are included/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:'Colorless only'}));
  expect(screen.getByRole('button',{name:'Blue'})).toHaveAttribute('aria-pressed','false');
  expect(screen.getByRole('button',{name:'Red'})).toHaveAttribute('aria-pressed','false');
  expect(screen.getByText('Only cards with a colorless identity.')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:'Green'}));
  expect(screen.getByRole('button',{name:'Colorless only'})).toHaveAttribute('aria-pressed','false');
  fireEvent.click(screen.getByRole('button',{name:'Green'}));
  expect(any).toHaveAttribute('aria-pressed','true');
});

test('selection is canonical, accessible, and uses the existing mana symbols',()=>{
  const changed=vi.fn();
  const view=render(<ColorIdentityFilter value="G" onChange={changed}/>);
  fireEvent.click(screen.getByRole('button',{name:'White'}));
  expect(changed).toHaveBeenLastCalledWith('WG');
  expect(screen.getByRole('button',{name:'Green'}).querySelector('.ms-g')).toBeTruthy();
  view.rerender(<ColorIdentityFilter value="WG" onChange={changed} disabled/>);
  expect(screen.getByRole('button',{name:'White'})).toBeDisabled();
  fireEvent.click(screen.getByRole('button',{name:'White'}));
  expect(changed).toHaveBeenCalledTimes(1);
});
