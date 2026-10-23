import HelpTip from './HelpTip';

export type PlaySettingsDraft = {
  seed: string; startingSeat: string; maxTurns: number; humanIdle: string; matchLimit: string; aiSpeed: '1ms' | '10ms' | '100ms' | '1s' | '10s';
};

export const DEFAULT_PLAY_SETTINGS: PlaySettingsDraft = { seed: '', startingSeat: 'rotate', maxTurns: 60, humanIdle: '', matchLimit: '', aiSpeed: '1ms' };
const TURN_LIMITS = [20, 30, 40, 60, 100];

export function playSettingsPayload(draft: PlaySettingsDraft, hasHuman: boolean, seats: number) {
  const whole = (value: number, min: number, max: number) => Number.isInteger(value) && value >= min && value <= max;
  const seed = draft.seed.trim() ? Number(draft.seed) : undefined;
  if (seed !== undefined && !whole(seed, 0, 2147483647)) throw new Error('Use a whole-number seed from 0 to 2147483647, or leave it blank for random.');
  if (!whole(draft.maxTurns, 20, 2000)) throw new Error('The turn limit must be a whole number from 20 to 2000.');
  const starting = ['rotate', 'random'].includes(draft.startingSeat) ? draft.startingSeat : Number(draft.startingSeat);
  if (typeof starting === 'number' && !whole(starting, 1, seats)) throw new Error('Choose an opening seat available in this format.');
  const idle = hasHuman && draft.humanIdle ? Number(draft.humanIdle) : null;
  if (idle !== null && !whole(idle, 5, 600)) throw new Error('Human inactivity limits must be between 5 seconds and 10 minutes.');
  const limit = draft.matchLimit ? Number(draft.matchLimit) : null;
  if (limit !== null && !whole(limit, 30, 43200)) throw new Error('Choose a match safety limit from 30 seconds to 12 hours.');
  if (!hasHuman && limit === null) throw new Error('Choose a safety limit for AI-only matches.');
  if (!['1ms', '10ms', '100ms', '1s', '10s'].includes(draft.aiSpeed)) throw new Error('Choose an available AI rhythm.');
  return { seed, starting_seat: starting, ai_speed: draft.aiSpeed, max_turns: draft.maxTurns, human_decision_timeout_seconds: idle, match_timeout_seconds: limit };
}

export default function PlaySettings({ value, onChange, hasHuman, seats }: { value: PlaySettingsDraft; onChange: (value: PlaySettingsDraft) => void; hasHuman: boolean; seats: number }) {
  const patch = (next: Partial<PlaySettingsDraft>) => onChange({ ...value, ...next });
  return <details className="local-play-settings"><summary><strong>Playtest settings</strong><span>{hasHuman && !value.humanIdle ? 'No human timer' : hasHuman ? `${Number(value.humanIdle) / 60} min inactivity limit` : 'AI-only experiment'} · {value.seed.trim() ? `seed ${value.seed}` : 'random seed'}</span></summary>
    <p>Format rules stay standard. These controls affect local play only, not League time controls.</p>
    <div className="local-play-fields">
      <label>Opening seat<HelpTip label="Opening seat">Rotate the opening seat, randomize it from the seed, or fix a seat. In Legacy this selects the first game only. A competition's higher-seed advantage overrides this during playoffs.</HelpTip><select aria-label="Opening seat" value={value.startingSeat} onChange={(event) => patch({ startingSeat: event.target.value })}><option value="rotate">Rotate between matches</option><option value="random">Random from seed</option>{Array.from({ length: seats }, (_, index) => <option key={index} value={String(index + 1)}>Seat {index + 1}</option>)}</select><small>In Legacy, this selects the first game; later games follow match rules.</small></label>
      <label>Shuffle seed<HelpTip label="Shuffle seed">Leave blank for a fresh experiment. A fixed seed repeats the same scheduling and deck choices with the same entrant pool; game timing and changed agents may still change results.</HelpTip><input aria-label="Shuffle seed" inputMode="numeric" value={value.seed} onChange={(event) => patch({ seed: event.target.value })} placeholder="Random each run" /><small>Leave blank for a new seed at every launch. Controls per-match agent/deck draws, opening seats and shuffles. Explicit seeds reproduce the same setup with the same candidate pool; random draws can repeat.</small></label>
      <label>Turn limit per game<HelpTip label="Turn limit per game">A safety bound for endless games. Reaching it stops the unfinished game; the organizer pauses without inventing a result.</HelpTip><select aria-label="Turn limit per game" value={value.maxTurns} onChange={(event) => patch({ maxTurns: Number(event.target.value) })}>{!TURN_LIMITS.includes(value.maxTurns) && <option value={value.maxTurns}>{value.maxTurns} turns · saved setting</option>}{TURN_LIMITS.map(turns => <option key={turns} value={turns}>{turns} turns{turns === 60 ? ' · default' : ''}</option>)}</select><small>Stops a game that reaches this limit. Default: 60 turns.</small></label>
      <label>Local AI rhythm<HelpTip label="Local AI rhythm">Minimum duration of a local AI decision, including its computation. A decision that takes longer is returned as soon as it is ready. The SDK transport safety allowance stays separate; League rhythms are unchanged.</HelpTip><select aria-label="Local AI rhythm" value={value.aiSpeed} onChange={(event) => patch({ aiSpeed: event.target.value as PlaySettingsDraft['aiSpeed'] })}><option value="1ms">1 ms · default</option><option value="10ms">10 ms</option><option value="100ms">100 ms</option>{['1s', '10s'].includes(value.aiSpeed) && <option value={value.aiSpeed}>{value.aiSpeed} · saved setting</option>}</select><small>A local pacing target, not a guaranteed response time or an AI calculation deadline.</small></label>
      {hasHuman && <label>Human inactivity limit<HelpTip label="Human inactivity limit">Optional time per unanswered human decision. This ends an unattended table, not a tournament forfeit. It does not affect AI-only tables.</HelpTip><select aria-label="Human inactivity limit" value={value.humanIdle} onChange={(event) => patch({ humanIdle: event.target.value })}><option value="">None · take your time</option><option value="60">1 minute per decision</option><option value="180">3 minutes per decision</option><option value="600">10 minutes per decision</option></select><small>Optional: ends the table if you leave a decision unanswered. It is not a forfeit clock.</small></label>}
      <label>Match safety limit<HelpTip label="Match safety limit">Maximum duration of one match, not the entire playoff series. A timeout saves the available trace and pauses the competition without assigning a result. AI-only matches always need a limit.</HelpTip><select aria-label="Match safety limit" value={value.matchLimit} onChange={(event) => patch({ matchLimit: event.target.value })}>{hasHuman && <option value="">None · relaxed play</option>}<option value="60">1 minute per match</option><option value="180">3 minutes per match</option><option value="600">10 minutes per match</option>{value.matchLimit && !['60', '180', '600'].includes(value.matchLimit) && <option value={value.matchLimit}>{Number(value.matchLimit) / 60} minutes · saved setting</option>}</select><small>Stops unfinished matches and saves the available trace, without adding a win, loss or draw.</small></label>
    </div>
  </details>;
}

export function ReplayPlaySettings({ metadata }: { metadata: Record<string, unknown> }) {
  const value = metadata.playSettings;
  const settings = value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null;
  const error = typeof metadata.captureError === 'string' ? metadata.captureError : '';
  if (!settings && !error) return null;
  const seconds = (value: unknown) => value === null ? 'None' : typeof value === 'number' ? `${value} seconds` : 'Not recorded';
  const versions = Array.isArray(metadata.agentVersions) ? metadata.agentVersions.flatMap((item) => item && typeof item === 'object' && typeof item.name === 'string' && typeof item.revision === 'number' ? [`${item.name} · v${item.revision}`] : []) : [];
  return <details className="replay-play-settings"><summary>Match setup & limits</summary>{settings && <dl><div><dt>Shuffle seed</dt><dd>{typeof metadata.seed === 'number' ? metadata.seed : 'Not recorded'}</dd></div><div><dt>Opening seat</dt><dd>{typeof metadata.startingSeat === 'number' ? `Seat ${metadata.startingSeat}` : 'Not recorded'}</dd></div><div><dt>Turn limit per game</dt><dd>{typeof settings.maxTurns === 'number' ? settings.maxTurns : 'Not recorded'}</dd></div><div><dt>Human inactivity limit</dt><dd>{seconds(settings.humanDecisionTimeoutSeconds)}</dd></div><div><dt>Match safety limit</dt><dd>{seconds(settings.matchTimeoutSeconds)}</dd></div>{versions.length > 0 && <div><dt>Agent versions</dt><dd>{versions.join(' / ')}</dd></div>}</dl>}{error && <p className="inline-error">{error}</p>}<p>Setup choices are recorded for comparison. Agent versions, decision variety and Engine timing can also affect results.</p></details>;
}
