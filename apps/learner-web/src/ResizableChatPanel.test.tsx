import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import ResizableChatPanel from './ResizableChatPanel';

beforeEach(() => {localStorage.clear(); Object.defineProperty(window,'innerWidth',{configurable:true,writable:true,value:1440});});
afterEach(() => {cleanup();vi.restoreAllMocks();vi.unstubAllGlobals();});
const panel = () => screen.getByLabelText('agent assistant');
const handle = () => screen.getByRole('separator',{name:'Chat panel width'});
test('resizes with the keyboard and retains width after reopening/remounting', () => {
  const {unmount} = render(<ResizableChatPanel open label="agent assistant">Chat</ResizableChatPanel>);
  expect(panel()).toHaveStyle({width:'440px'});
  fireEvent.keyDown(handle(),{key:'ArrowLeft'});
  expect(panel()).toHaveStyle({width:'472px'});
  unmount();
  render(<ResizableChatPanel open label="agent assistant">Chat</ResizableChatPanel>);
  expect(panel()).toHaveStyle({width:'472px'});
  fireEvent.doubleClick(handle());
  expect(panel()).toHaveStyle({width:'440px'});
});
test('clamps to viewport and restores preferred desktop size after a window resize', () => {
  localStorage.setItem('deepdeck.chatPanelWidth','800');
  render(<ResizableChatPanel open label="agent assistant">Chat</ResizableChatPanel>);
  window.innerWidth=390; fireEvent(window,new Event('resize'));
  expect(panel()).toHaveStyle({width:'374px'});
  window.innerWidth=1440; fireEvent(window,new Event('resize'));
  expect(panel()).toHaveStyle({width:'800px'});
  fireEvent.keyDown(handle(),{key:'Home'});
  expect(panel()).toHaveStyle({width:'320px'});
  fireEvent.keyDown(handle(),{key:'End'});
  expect(panel()).toHaveStyle({width:'1120px'});
});
test('unavailable storage and invalid stored sizes do not prevent using chat', () => {
  localStorage.setItem('deepdeck.chatPanelWidth','Infinity');
  vi.spyOn(Storage.prototype,'setItem').mockImplementation(()=>{throw new Error('storage disabled');});
  render(<ResizableChatPanel open label="agent assistant">Chat</ResizableChatPanel>);
  expect(panel()).toHaveStyle({width:'440px'});
  fireEvent.keyDown(handle(),{key:'ArrowRight'});
  expect(panel()).toHaveStyle({width:'408px'});
});
test('dragging the left edge adjusts width and releases selection/cursor after closing', () => {
  vi.stubGlobal('PointerEvent', MouseEvent);
  const {rerender} = render(<ResizableChatPanel open label="agent assistant">Chat</ResizableChatPanel>);
  fireEvent.pointerDown(handle(),{button:0,clientX:1000});
  fireEvent.pointerMove(handle(),{clientX:750});
  expect(panel()).toHaveStyle({width:'690px'});
  expect(document.body.style.userSelect).toBe('none');
  rerender(<ResizableChatPanel open={false} label="agent assistant">Chat</ResizableChatPanel>);
  expect(document.body.style.userSelect).toBe('');
  expect(document.body.style.cursor).toBe('');
  expect(screen.getByLabelText('Chat panel width')).toHaveAttribute('tabindex','-1');
});
