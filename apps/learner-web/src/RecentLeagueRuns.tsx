import { useState } from 'react';

import type { Job } from './api';
import LeagueRunReport from './LeagueRunReport';

const storageKey = 'deepdeck.dismissedLeagueRuns.v1';
const maxDismissed = 200;

function readDismissed(): string[] {
  try {
    const saved: unknown = JSON.parse(window.localStorage.getItem(storageKey) ?? '[]');
    return Array.isArray(saved)
      ? [...new Set(saved.filter((id): id is string => typeof id === 'string' && id.length > 0 && id.length <= 256))].slice(-maxDismissed)
      : [];
  } catch {
    return [];
  }
}

/** Hides finished-run notifications only; never mutates a worker or its history. */
export default function RecentLeagueRuns({ jobs }: { jobs: Job[] }) {
  const [dismissed, setDismissed] = useState(readDismissed);
  const recent = jobs.filter(job => job.kind === 'matchmaking.agent'
    && ['completed', 'failed', 'stopped'].includes(job.status)
    && (job.status === 'failed' || job.details?.leagueRun?.maxMatches != null || job.details?.leagueRun?.durationSeconds != null))
    .sort((a, b) => (b.finished_at ?? b.created_at).localeCompare(a.finished_at ?? a.created_at));
  const visible = recent.filter(job => !dismissed.includes(job.id)).slice(0, 4);
  const hiddenCount = recent.filter(job => dismissed.includes(job.id)).length;

  function save(next: string[]) {
    setDismissed(next);
    try {
      window.localStorage.setItem(storageKey, JSON.stringify(next));
    } catch { /* Keep dismissal usable when browser storage is unavailable. */ }
  }

  if (!recent.length) return null;
  return <section className="feature-card recent-league-runs" aria-label="Recent League runs">
    <header className="recent-league-runs-heading"><h2>Recent League runs</h2>
      {hiddenCount > 0 && <button type="button" className="text-action" onClick={() => save([])}>Show dismissed runs ({hiddenCount})</button>}
    </header>
    {visible.map(job => <article key={job.id}>
      <div className="recent-league-run-heading"><div><strong>{job.label}</strong>
        <small>{job.status === 'failed' ? 'Worker ended with an error' : job.status === 'stopped' ? 'Brought home' : 'Worker finished'}</small>
      </div><button type="button" className="league-dismiss-button" aria-label={`Dismiss League run for ${job.label}`}
        title="Hide this notification. Run history and replays are kept."
        onClick={() => save([...dismissed.filter(id => id !== job.id), job.id].slice(-maxDismissed))}>
        <span aria-hidden="true">×</span>
      </button></div>
      <LeagueRunReport job={job} />
    </article>)}
    {!visible.length && <p>All recent run notifications are hidden. Your history and replays are still saved.</p>}
    {visible.length > 0 && <p>Worker status is local. Available League places are still reported by the server. Dismissing a notification keeps its history and replays.</p>}
  </section>;
}
