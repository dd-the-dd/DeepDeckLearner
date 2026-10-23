export type LeagueRunDraft = {mode:'continuous'|'matches'|'duration';matches:string;minutes:string};
export const DEFAULT_LEAGUE_RUN:LeagueRunDraft = {mode:'continuous',matches:'10',minutes:'60'};
export function leagueRunError(draft:LeagueRunDraft):string {
  if(!['continuous','matches','duration'].includes(draft.mode))return 'Choose a supported deployment duration.';
  if(draft.mode==='continuous')return '';
  const value=Number(draft.mode==='matches'?draft.matches:draft.minutes), max=draft.mode==='matches'?10000:10080;
  return Number.isInteger(value)&&value>=1&&value<=max?'':`Choose a whole number of ${draft.mode==='matches'?'completed matches':'minutes'} between 1 and ${max}.`;
}
export function leagueRunPayload(draft:LeagueRunDraft) {
  const error=leagueRunError(draft);if(error)throw new Error(error);
  return draft.mode==='matches'?{mode:'matches',matches:Number(draft.matches)}:draft.mode==='duration'?{mode:'duration',minutes:Number(draft.minutes)}:{mode:'continuous'};
}
export function leagueRunSummary(draft:LeagueRunDraft):string {
  return draft.mode==='matches'?`Return after ${Number(draft.matches)} completed matches.`:draft.mode==='duration'?`Stop accepting matches after ${Number(draft.minutes)} minutes, finishing any assigned match.`:'Keep playing until brought home.';
}
export default function LeagueRunSettings({value,onChange,disabled=false}:{value:LeagueRunDraft;onChange:(value:LeagueRunDraft)=>void;disabled?:boolean}) {
  return <fieldset className="league-run-settings" disabled={disabled}><legend>When should this agent return?</legend><div>
    <label>Deployment duration<select value={value.mode} onChange={event=>onChange({...value,mode:event.target.value as LeagueRunDraft['mode']})}><option value="continuous">Keep playing until I bring it home</option><option value="matches">After a number of completed matches</option><option value="duration">After a duration</option></select></label>
    {value.mode==='matches'&&<label>Completed matches<input type="number" min={1} max={10000} step={1} value={value.matches} onChange={event=>onChange({...value,matches:event.target.value})}/></label>}
    {value.mode==='duration'&&<label>Duration in minutes<input type="number" min={1} max={10080} step={1} value={value.minutes} onChange={event=>onChange({...value,minutes:event.target.value})}/></label>}</div>
    <p>{value.mode==='continuous'?'Keep this app open. The agent keeps requesting matches until you stop it.':value.mode==='matches'?'Only completed matches count. Failed or cancelled attempts do not use this limit.':'The timer begins when the League worker starts connecting. Waiting tickets are cancelled at expiry; an assigned match finishes before returning, so a run may exceed the selected duration. Network failures are reported, not treated as a confirmed stop.'}</p>
    <small>These are local stop settings, not extra League places. Account capacity is still decided by the server.</small>
    {leagueRunError(value)&&<p role="alert" className="inline-error">{leagueRunError(value)}</p>}
  </fieldset>;
}
