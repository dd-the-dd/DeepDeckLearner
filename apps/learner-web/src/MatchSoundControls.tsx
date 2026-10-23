// Original procedural effects, MIT licensed. No samples or network requests.
import { useEffect, useRef, useState } from 'react';
import type { EngineView } from './LocalPixiTable';

export type MatchCue = 'cast' | 'land' | 'arrival' | 'attack' | 'block' | 'damage' | 'death' | 'ability' | 'trigger' | 'counter' | 'draw' | 'mill' | 'shuffle' | 'phase' | 'turn' | 'finish';
export type SoundSignal = {id: number; cue: MatchCue};
const eventCues: Record<string, MatchCue> = {
  spellCast:'cast', landPlayed:'land', attackersDeclared:'attack', attackerDeclared:'attack', blockersDeclared:'block', blockerDeclared:'block',
  damageDealt:'damage', creatureDied:'death', permanentDied:'death', permanentDestroyed:'death',
  cardMovedToBattlefield:'arrival', permanentEnteredBattlefield:'arrival',
  activatedAbilityPutOnStack:'ability', triggeredAbilityPutOnStack:'trigger', reflexiveTriggeredAbilityPutOnStack:'trigger',
  spellCountered:'counter', stackObjectCountered:'counter', cardDrawn:'draw', cardsMilled:'mill', cardMilled:'mill', libraryShuffled:'shuffle',
  zoneShuffled:'shuffle', graveyardShuffledIntoLibrary:'shuffle', handAndGraveyardShuffledIntoLibrary:'shuffle',
};
const importance: Record<MatchCue, number> = {finish:10,damage:8,death:7,counter:7,attack:6,block:6,land:5,cast:5,arrival:5,ability:4,trigger:4,draw:3,mill:3,shuffle:3,turn:2,phase:1};
export function matchCue(previous: EngineView | null, next: EngineView): MatchCue | null {
  if (!previous || previous.sessionId !== next.sessionId || next.revision <= previous.revision) return null;
  if (next.state?.outcome?.winner && !previous.state?.outcome?.winner) return 'finish';
  if (next.state?.players?.some(p => {
    const prior = previous.state?.players?.find(player => player.id === p.id);
    return typeof p.life === 'number' && typeof prior?.life === 'number' && p.life < prior.life;
  })) return 'damage';
  const priorSequence = Math.max(0, ...(previous.state?.events ?? []).map(e => Number(e.sequence ?? 0)));
  const cues = (next.state?.events ?? []).filter(e => Number(e.sequence ?? 0) > priorSequence)
    .map(e => eventCues[String(e.kind)]).filter((cue): cue is MatchCue => Boolean(cue));
  if (cues.length) return cues.sort((a,b) => importance[b] - importance[a])[0];
  const action = next.state?.events?.length ? '' : (next as EngineView & {selectedAction?: {kind?: string}}).selectedAction?.kind ?? '';
  if (/attack/i.test(action)) return 'attack';
  if (/block/i.test(action)) return 'block';
  if (/playLand/i.test(action)) return 'land';
  if (/activate/i.test(action)) return 'ability';
  if (/cast/i.test(action)) return 'cast';
  if (next.state?.turnNumber !== previous.state?.turnNumber) return 'turn';
  // Only real phase changes, not individual steps or priority passes.
  const phase = (state: EngineView['state']) => state?.phase ?? phaseForStep(state?.step);
  if (phase(previous.state) && phase(next.state) && phase(previous.state) !== phase(next.state)) return 'phase';
  return null;
}
export function phaseForStep(step?: string): string | undefined {
  return ({untap:'beginning',upkeep:'beginning',draw:'beginning',precombatMain:'precombatMain',main1:'precombatMain',beginCombat:'combat',beginningOfCombat:'combat',declareAttackers:'combat',declareBlockers:'combat',firstStrikeDamage:'combat',combatDamage:'combat',endCombat:'combat',endOfCombat:'combat',postcombatMain:'postcombatMain',main2:'postcombatMain',end:'ending',endStep:'ending',cleanup:'ending'} as Record<string,string>)[step ?? ''];
}
const notes: Record<MatchCue, number[]> = {cast:[440,660],land:[170,255],arrival:[262,392],attack:[155,196],block:[196,147],damage:[110,82],death:[165,110,73],ability:[330,494],trigger:[587,784],counter:[494,247],draw:[740,880],mill:[220,147],shuffle:[260,330,293],phase:[523],turn:[392,523],finish:[392,494,587,784]};

export default function MatchSoundControls({view, signal, synchronized = false, resetKey = ''}: {view: EngineView | null; signal?: SoundSignal | null; synchronized?: boolean; resetKey?: string}) {
  const [enabled, setEnabled] = useState(true);
  const [needsGesture, setNeedsGesture] = useState(false);
  const [volume, setVolume] = useState(25);
  const [error, setError] = useState('');
  const context = useRef<AudioContext | null>(null);
  const master = useRef<GainNode | null>(null);
  const previous = useRef<EngineView | null>(null);
  const consumedSignal = useRef<number | undefined>(undefined);
  const voices = useRef(new Set<OscillatorNode>());
  const lastSound = useRef({time:-Infinity, priority:0});
  const stop = () => {
    for (const oscillator of voices.current) {try {oscillator.stop();} catch {/* Already ended. */}}
    voices.current.clear();
  };
  function initializeAudio() {
    if (!context.current || context.current.state === 'closed') {
      context.current = new AudioContext();
      master.current = context.current.createGain();
      master.current.connect(context.current.destination);
      master.current.gain.setValueAtTime(volume / 100, context.current.currentTime);
    }
    return context.current;
  }
  async function unlock() {
    try {
      const audio = initializeAudio();
      // An initial resume may remain pending until the first user gesture.
      // Keep playback running and explain the browser restriction explicitly.
      setNeedsGesture(audio.state !== 'running');
      await audio.resume();
      setNeedsGesture(audio.state !== 'running');
      setError('');
    } catch {setError('Audio is unavailable in this browser.');}
  }
  async function toggle() {
    if (enabled) {stop(); setEnabled(false); return;}
    setEnabled(true);
    await unlock();
  }
  useEffect(() => {
    if (!enabled) return;
    void unlock();
    const resume = () => { if (context.current?.state !== 'running') void unlock(); };
    document.addEventListener('pointerdown', resume, true);
    document.addEventListener('keydown', resume, true);
    return () => {
      document.removeEventListener('pointerdown', resume, true);
      document.removeEventListener('keydown', resume, true);
    };
    // Audio preference changes, not every game frame, manage the context.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled]);
  useEffect(() => {
    if (master.current && context.current) master.current.gain.setValueAtTime(volume / 100, context.current.currentTime);
    if (!volume) stop();
  }, [volume, enabled]);
  useEffect(() => {stop(); previous.current = view;}, [resetKey]);
  useEffect(() => {
    const prior = previous.current;
    previous.current = view;
    let cue: MatchCue | null = null;
    if (synchronized) {
      if (signal && signal.id !== consumedSignal.current) cue = signal.cue;
      consumedSignal.current = signal?.id;
    } else if (view) cue = matchCue(prior, view);
    const audio = context.current;
    if (!cue || !enabled || !audio || !volume || audio.state !== 'running') return;
    // At most one short gesture (four oscillator voices), prioritizing important events.
    if (audio.currentTime - lastSound.current.time < .10 && importance[cue] <= lastSound.current.priority) return;
    if (voices.current.size && importance[cue] < lastSound.current.priority) return;
    stop();
    lastSound.current = {time:audio.currentTime, priority:importance[cue]};
    const gain = cue === 'phase' ? .035 : cue === 'turn' ? .055 : .12;
    notes[cue].forEach((frequency, index) => {
      const oscillator = audio.createOscillator();
      const envelope = audio.createGain();
      const start = audio.currentTime + index * .045;
      const duration = cue === 'phase' ? .09 : .15;
      oscillator.type = ['damage','death','attack','block','land'].includes(cue) ? 'triangle' : 'sine';
      oscillator.frequency.setValueAtTime(frequency, start);
      envelope.gain.setValueAtTime(0, start);
      envelope.gain.linearRampToValueAtTime(gain, start + .008);
      envelope.gain.exponentialRampToValueAtTime(.0001, start + duration);
      oscillator.connect(envelope); envelope.connect(master.current ?? audio.destination);
      voices.current.add(oscillator);
      oscillator.start(start); oscillator.stop(start + duration + .015);
      oscillator.onended = () => {voices.current.delete(oscillator); oscillator.disconnect(); envelope.disconnect();};
    });
  }, [view, signal, synchronized, enabled, volume]);
  useEffect(() => () => {stop(); void context.current?.close(); context.current = null; master.current = null;}, []);
  return <div className="match-sound-controls" title="Original MIT-licensed synthesized effects · synchronized with the visual table">
    <button type="button" aria-pressed={enabled} onClick={() => void toggle()}>{enabled ? 'Sound on' : 'Sound off'}</button>
    <label>Volume<input aria-label="Match sound volume" type="range" min={0} max={100} value={volume} onChange={e => setVolume(Number(e.target.value))} /></label>
    {error && <small role="status">{error}</small>}
    {!error && enabled && needsGesture && <small role="status">Click the table to enable browser audio.</small>}
  </div>;
}
