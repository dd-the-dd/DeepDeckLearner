import {useState} from 'react';
import {cleanup, fireEvent, render, screen} from '@testing-library/react';
import {afterEach, expect, test, vi} from 'vitest';
import AgentDecisionNodes from './AgentDecisionNodes';
import type {AgentDecisionNode, HeuristicCatalog} from './api';

afterEach(cleanup);
const catalog = {items:[{id:'combo_disruption',name:'Disrupt combos'}],customNodeSignals:[{id:'need.draw_cards',name:'Need more cards'}],customNodeActionKinds:['castSpell','keepHand']} as HeuristicCatalog;
const base = (id: string, inputs: AgentDecisionNode['inputs'] = []): AgentDecisionNode => ({id,name:id,enabled:true,weight:1,bias:.5,when:{actionKinds:[],sourceCards:[],sourceTags:[],targetCards:[],targetTags:[],turn:'any',role:'any',targetController:'any'},inputs});
function Harness({initial=[],changed=vi.fn(),editing=vi.fn()}: {initial?:AgentDecisionNode[];changed?:(nodes:AgentDecisionNode[])=>void;editing?:(value:boolean)=>void}) {
  const [nodes,setNodes]=useState(initial);
  return <AgentDecisionNodes nodes={nodes} catalog={catalog} onChange={next=>{changed(next);setNodes(next);}} openAssistant={vi.fn()} onEditingChange={editing}/>;
}

test('an example is only a draft until applied; editing and disabling preserve its graph',()=>{
  const changed=vi.fn(),editing=vi.fn();render(<Harness changed={changed} editing={editing}/>);
  fireEvent.click(screen.getByRole('button',{name:'Refill my hand'}));
  expect(changed).not.toHaveBeenCalled();expect(editing).toHaveBeenLastCalledWith(true);
  fireEvent.click(screen.getByRole('button',{name:'Apply node to draft'}));
  expect(changed).toHaveBeenLastCalledWith([expect.objectContaining({name:'Refill an empty hand',bias:0,when:expect.objectContaining({sourceTags:['card_draw']}),inputs:[{from:'need.draw_cards',weight:1}]})]);
  fireEvent.click(screen.getByRole('button',{name:'Edit node Refill an empty hand'}));
  expect(screen.getByLabelText('Source must have all tags')).toHaveValue('card_draw');
  fireEvent.change(screen.getByLabelText('Importance'),{target:{value:'1.7'}});
  fireEvent.click(screen.getByRole('button',{name:'Apply node to draft'}));
  fireEvent.click(screen.getByRole('button',{name:'Disable Refill an empty hand'}));
  expect(changed).toHaveBeenLastCalledWith([expect.objectContaining({enabled:false,weight:1.7,inputs:[{from:'need.draw_cards',weight:1}]})]);
});

test('card names retain commas and multi-tag typing is not reset after separators',()=>{
  const changed=vi.fn();render(<Harness changed={changed}/>);
  fireEvent.click(screen.getByRole('button',{name:'+ Add decision node'}));
  fireEvent.change(screen.getByLabelText('Source cards · one exact name per line'),{target:{value:'Thalia, Guardian of Thraben\nBrainstorm'}});
  fireEvent.change(screen.getByLabelText('Source must have all tags'),{target:{value:'creature, '}});
  expect(screen.getByLabelText('Source must have all tags')).toHaveValue('creature, ');
  fireEvent.change(screen.getByLabelText('Source must have all tags'),{target:{value:'creature, tax'}});
  fireEvent.click(screen.getByRole('button',{name:'Apply node to draft'}));
  expect(changed).toHaveBeenCalledWith([expect.objectContaining({when:expect.objectContaining({sourceCards:['Thalia, Guardian of Thraben','Brainstorm'],sourceTags:['creature','tax']})})]);
});

test('cycles and removal of referenced nodes are blocked without silently deleting connections',()=>{
  const changed=vi.fn();render(<Harness initial={[base('first'),base('second',[{from:'node.first',weight:1}])]} changed={changed}/>);
  fireEvent.click(screen.getByRole('button',{name:'Remove node first'}));
  expect(screen.getByRole('alert')).toHaveTextContent('Remove connections from second');
  expect(changed).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button',{name:'Edit node first'}));
  fireEvent.change(screen.getByLabelText('Connect an input'),{target:{value:'node.second'}});
  fireEvent.click(screen.getByRole('button',{name:'Connect input'}));
  fireEvent.click(screen.getByRole('button',{name:'Apply node to draft'}));
  expect(screen.getByRole('alert')).toHaveTextContent('cycle');expect(changed).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button',{name:'Cancel node'}));
});

test('negative input weights and narrow target filters are preserved',()=>{
  const changed=vi.fn();render(<Harness changed={changed}/>);
  fireEvent.click(screen.getByRole('button',{name:'Answer combo pieces'}));
  expect(screen.getByLabelText('Target belongs to')).toHaveValue('opponent');
  fireEvent.change(screen.getByLabelText('Connect an input'),{target:{value:'base.combo_disruption'}});
  fireEvent.click(screen.getByRole('button',{name:'Connect input'}));
  fireEvent.change(screen.getByLabelText('Input weight base.combo_disruption'),{target:{value:'-.5'}});
  fireEvent.click(screen.getByRole('button',{name:'Apply node to draft'}));
  expect(changed).toHaveBeenCalledWith([expect.objectContaining({when:expect.objectContaining({sourceTags:['interaction'],targetTags:['combo_enabler'],targetController:'opponent'}),inputs:[{from:'base.combo_disruption',weight:-.5}]})]);
});
