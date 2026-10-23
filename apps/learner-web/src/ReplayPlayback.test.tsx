import { useState } from 'react';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';

import LocalPixiReplay, { replayFrameView } from './LocalPixiReplay';
import type { SavedReplay } from './api';

vi.mock('./LocalPixiRenderer', () => ({ default: ({ view, playbackControls }: { view: { state?: { turnNumber?: number; status?: string } }; playbackControls?: import('react').ReactNode }) => <div aria-label="Recorded table"><div className="pixi-game-controls">{playbackControls}</div>Turn {view.state?.turnNumber} · {view.state?.status}</div> }));
afterEach(() => { cleanup(); vi.useRealTimers(); });

const replay: SavedReplay = {
  schemaVersion: 'deepdeck-replay/v1', id: 'two-rounds', createdAtUnixMs: 1,
  metadata: { roundNumber: 2 },
  frames: Array.from({ length: 12 }, (_, index) => ({
    revision: index < 6 ? index : index - 6,
    state: { turnNumber: index < 6 ? index + 1 : index - 5, players: [], status: index === 5 || index === 11 ? 'completed' : 'inProgress' },
    matchState: { gameNumber: index < 6 ? 1 : 2, phase: index === 11 ? 'complete' : 'game' },
  })),
};

function Player() {
  const [frame, setFrame] = useState(0);
  return <LocalPixiReplay deckVersionIds={[]} frameIndex={frame} onFrameIndexChange={setFrame} replay={replay} />;
}

test('automatic playback crosses a completed first round and reaches the final frame of the entire match', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  render(<Player />);
  await act(async () => {});
  expect(screen.getByRole('button', { name: 'Pause' })).toBeVisible();
  expect(screen.getByRole('slider', {name:'Replay frame'}).closest('.pixi-game-controls')).not.toBeNull();
  for (let step = 1; step < replay.frames.length; step++) {
    await act(async () => { await vi.advanceTimersByTimeAsync(700); });
    expect(screen.getByRole('slider', { name: 'Replay frame' })).toHaveValue(String(step));
    if (step === 5) expect(screen.getByRole('button', { name: 'Pause' })).toBeVisible();
  }
  expect(screen.getByRole('button', { name: 'Next frame' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Play' })).toBeVisible();
});

test('manual frame stepping pauses automatic playback instead of immediately skipping ahead', async () => {
  vi.useFakeTimers({shouldAdvanceTime:true});
  render(<Player />);
  await act(async () => {});
  fireEvent.click(screen.getByRole('button',{name:'Next frame'}));
  expect(screen.getByRole('slider',{name:'Replay frame'})).toHaveValue('1');
  expect(screen.getByRole('button',{name:'Play'})).toBeVisible();
  await act(async () => {await vi.advanceTimersByTimeAsync(1400);});
  expect(screen.getByRole('slider',{name:'Replay frame'})).toHaveValue('1');
});

test('every frame has a unique playback revision even when engine revisions reset in the next round', () => {
  expect(replay.frames.map((_, index) => replayFrameView(replay, index)?.revision)).toEqual(Array.from({ length: 12 }, (_, index) => index + 1));
});

test('the final frame can be reached directly and playback can restart', async () => {
  render(<Player />);
  await act(async () => {});
  fireEvent.click(screen.getByRole('button', { name: 'Last frame' }));
  expect(screen.getByRole('slider', { name: 'Replay frame' })).toHaveValue('11');
  fireEvent.click(screen.getByRole('button', { name: 'Play' }));
  expect(screen.getByRole('slider', { name: 'Replay frame' })).toHaveValue('0');
  expect(screen.getByRole('button', { name: 'Pause' })).toBeVisible();
});
