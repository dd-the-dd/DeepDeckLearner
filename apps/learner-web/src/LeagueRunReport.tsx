import type { Job } from './api';
export default function LeagueRunReport({job}:{job:Job}) {
  const run=job.details?.leagueRun;
  const error=run?.lastError || (job.status==='failed'?'No detailed server reason was recorded for this attempt. Its final result is not confirmed.':null);
  return <>{run&&<small className="league-run-report" title="Completed counts are this worker's seat participations in finished matches. Failed or cancelled attempts are excluded.">
    Completed: {run.completedEntries}{run.maxMatches!==null?` / ${run.maxMatches}`:''}
    {run.durationSeconds!==null?` · timer ${run.durationSeconds/60} min`:''}
    {['running','queued'].includes(job.status)?` · ${run.activeEntries} active`:''}
  </small>}{error&&<div className="league-run-error"><p role="alert">{error}</p><button type="button" onClick={()=>window.dispatchEvent(new Event('deepdeck:open-league-settings'))}>Open League connection settings</button></div>}</>;
}
