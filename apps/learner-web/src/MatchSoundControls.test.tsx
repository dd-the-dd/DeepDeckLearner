import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import MatchSoundControls, { matchCue } from './MatchSoundControls';
import type { EngineView } from './LocalPixiTable';

const frame = (revision: number, extra: Partial<EngineView> = {}): EngineView => ({sessionId:'game:1', revision, state:{turnNumber:1, players:[{id:'a',life:20}]}, ...extra});
afterEach(() => {cleanup(); vi.unstubAllGlobals();});

test('sound follows forward playback only, without inventing initial or repeated actions', () => {
  expect(matchCue(null, frame(1))).toBeNull();
  expect(matchCue(frame(2), frame(1))).toBeNull();
  expect(matchCue(frame(1), frame(1))).toBeNull();
  expect(matchCue(frame(1), frame(2,{sessionId:'different'}))).toBeNull();
  expect(matchCue(frame(1), frame(2,{state:{turnNumber:2}}))).toBe('turn');
  expect(matchCue(frame(1), frame(2,{state:{players:[{id:'a',life:17}]}}))).toBe('damage');
  expect(matchCue(frame(1), frame(2,{state:{outcome:{winner:'a'}}}))).toBe('finish');
  for (const [kind,cue] of [['castSpell','cast'],['playLand','land'],['declareAttackers','attack'],['activateAbility','ability']] as const) {
    expect(matchCue(frame(1), {...frame(2),selectedAction:{kind}} as EngineView)).toBe(cue);
  }
});

test('audio is enabled by default, respects mute, and releases its context', async () => {
  const start = vi.fn(), close = vi.fn(), resume = vi.fn().mockResolvedValue(undefined);
  const oscillator = () => ({frequency:{setValueAtTime:vi.fn()},connect:vi.fn(),disconnect:vi.fn(),start,stop:vi.fn()});
  const createOscillator = vi.fn(oscillator);
  const createGain = () => ({gain:{setValueAtTime:vi.fn(),linearRampToValueAtTime:vi.fn(),exponentialRampToValueAtTime:vi.fn()},connect:vi.fn(),disconnect:vi.fn()});
  const Audio = vi.fn(function () { return {state:'running',currentTime:10,resume,close,createOscillator,createGain}; });
  vi.stubGlobal('AudioContext', Audio);
  const {rerender,unmount} = render(<MatchSoundControls view={frame(1)} />);
  await waitFor(() => expect(screen.getByRole('button',{name:'Sound on'})).toHaveAttribute('aria-pressed','true'));
  expect(Audio).toHaveBeenCalledOnce();
  expect(start).not.toHaveBeenCalled();
  rerender(<MatchSoundControls view={frame(2,{state:{turnNumber:2}})} />);
  expect(start).toHaveBeenCalledTimes(2);
  fireEvent.click(screen.getByRole('button',{name:'Sound on'}));
  rerender(<MatchSoundControls view={frame(4,{state:{turnNumber:4}})} />);
  expect(start).toHaveBeenCalledTimes(2);
  unmount();
  expect(close).toHaveBeenCalledOnce();
});

test('an unsupported audio device does not prevent viewing', async () => {
  vi.stubGlobal('AudioContext',undefined);
  render(<MatchSoundControls view={frame(1)} />);
  expect(await screen.findByRole('status')).toHaveTextContent('Audio is unavailable');
});

test('phase cues follow real phase boundaries, not priority or individual beginning/combat steps', () => {
  const step = (revision: number, name: string) => frame(revision,{state:{turnNumber:1,step:name}});
  expect(matchCue(step(1,'upkeep'),step(2,'draw'))).toBeNull();
  expect(matchCue(step(2,'draw'),step(3,'precombatMain'))).toBe('phase');
  expect(matchCue(step(3,'precombatMain'),step(4,'beginningOfCombat'))).toBe('phase');
  expect(matchCue(step(4,'beginningOfCombat'),step(5,'declareAttackers'))).toBeNull();
  expect(matchCue(step(5,'declareAttackers'),step(6,'declareBlockers'))).toBeNull();
  expect(matchCue(step(6,'combatDamage'),step(7,'postcombatMain'))).toBe('phase');
  expect(matchCue(step(7,'endStep'),step(8,'cleanup'))).toBeNull();
  expect(matchCue(step(1,'unknown'),step(2,'draw'))).toBeNull();
});

test('public event cues are deduplicated and important effects win simultaneous bursts', () => {
  const previous = frame(1,{state:{events:[{sequence:1,kind:'cardDrawn'}]}});
  expect(matchCue(previous,frame(2,{state:{events:[{sequence:1,kind:'cardDrawn'}]}}))).toBeNull();
  for (const [kind,cue] of [['cardDrawn','draw'],['cardMilled','mill'],['triggeredAbilityPutOnStack','trigger'],['permanentDestroyed','death'],['permanentDied','death'],['spellCountered','counter'],['blockerDeclared','block'],['zoneShuffled','shuffle'],['cardMovedToBattlefield','arrival']] as const) {
    expect(matchCue(previous,frame(2,{state:{events:[{sequence:2,kind}]}}))).toBe(cue);
  }
  expect(matchCue(previous,frame(2,{state:{events:[{sequence:2,kind:'cardDrawn'},{sequence:3,kind:'spellCountered'}]}}))).toBe('counter');
  expect(matchCue(previous,{...frame(2,{state:{events:[{sequence:1,kind:'cardDrawn'}]}}),selectedAction:{kind:'castSpell'}} as EngineView)).toBeNull();
});

test('synchronized audio waits for visual completion and never plays stale muted cues', async () => {
  const start = vi.fn(), stop = vi.fn();
  const gain = () => ({gain:{setValueAtTime:vi.fn(),linearRampToValueAtTime:vi.fn(),exponentialRampToValueAtTime:vi.fn()},connect:vi.fn(),disconnect:vi.fn()});
  const Audio = vi.fn(function () {return {state:'running',currentTime:10,resume:vi.fn().mockResolvedValue(undefined),close:vi.fn(),createGain:gain,createOscillator:()=>({frequency:{setValueAtTime:vi.fn()},connect:vi.fn(),disconnect:vi.fn(),start,stop})};});
  vi.stubGlobal('AudioContext',Audio);
  const {rerender} = render(<MatchSoundControls view={frame(1)} synchronized />);
  await waitFor(() => expect(screen.getByRole('button',{name:'Sound on'})).toHaveAttribute('aria-pressed','true'));
  rerender(<MatchSoundControls view={frame(2,{state:{turnNumber:2}})} synchronized />);
  expect(start).not.toHaveBeenCalled();
  rerender(<MatchSoundControls view={frame(2)} synchronized signal={{id:1,cue:'phase'}} />);
  expect(start).toHaveBeenCalledOnce();
  rerender(<MatchSoundControls view={frame(2)} synchronized signal={{id:1,cue:'phase'}} />);
  expect(start).toHaveBeenCalledOnce();
  rerender(<MatchSoundControls view={frame(3)} synchronized signal={{id:2,cue:'damage'}} />);
  expect(start).toHaveBeenCalledTimes(3);
  expect(stop).toHaveBeenCalledTimes(4); // Three scheduled stops, plus cancellation of the phase voice.
  fireEvent.change(screen.getByRole('slider',{name:'Match sound volume'}),{target:{value:0}});
  expect(stop).toHaveBeenCalledTimes(6);
  rerender(<MatchSoundControls view={frame(4)} synchronized signal={{id:3,cue:'finish'}} />);
  fireEvent.change(screen.getByRole('slider',{name:'Match sound volume'}),{target:{value:50}});
  expect(start).toHaveBeenCalledTimes(3);
});

test('blocked autoplay is explained and a gesture resumes sound without replaying past cues', async () => {
  const audio = {state:'suspended',currentTime:1,resume:vi.fn(async () => {}),close:vi.fn(),createGain:()=>({gain:{setValueAtTime:vi.fn()},connect:vi.fn()})};
  vi.stubGlobal('AudioContext', vi.fn(function () {return audio;}));
  render(<MatchSoundControls view={frame(1)} />);
  expect(await screen.findByRole('status')).toHaveTextContent('Click the table');
  audio.resume.mockImplementation(async () => {audio.state='running';});
  fireEvent.pointerDown(document.body);
  await waitFor(() => expect(screen.queryByRole('status')).toBeNull());
  expect(screen.getByRole('button',{name:'Sound on'})).toHaveAttribute('aria-pressed','true');
});
