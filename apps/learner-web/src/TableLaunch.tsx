import { useEffect, useRef, useState } from 'react';

import AsyncActionButton from './AsyncActionButton';
import { loadJob, stopJob, type Job } from './api';

export function mergeObservedJob(current: Job, incoming: Job): Job {
  if (current.id !== incoming.id) return incoming;
  const progress = (job: Job) => job.status === 'queued' ? 0 : job.status === 'running' ? 1 : 2;
  if (progress(current) > progress(incoming)) return current;
  return { ...incoming, details: { ...current.details, ...incoming.details } };
}

/** Observe the existing job; reconnecting must never launch another match. */
export default function TableLaunch({ job, onUpdate, onClose }: {
  job: Job; onUpdate: (job: Job) => void; onClose: () => void;
}) {
  const [error, setError] = useState('');
  const [stopping, setStopping] = useState(false);
  const stopRequested = useRef(false);
  const pending = ['queued', 'running'].includes(job.status);
  useEffect(() => {
    if (!pending || stopping) return;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const latest = await loadJob(job.id);
        if (disposed || stopRequested.current) return;
        setError('');
        onUpdate(latest);
        if (['queued', 'running'].includes(latest.status) && !latest.details?.sessionId) {
          timer = setTimeout(() => void poll(), 1000);
        }
      } catch (reason) {
        if (disposed || stopRequested.current) return;
        setError(`Reconnecting to your existing table. ${reason instanceof Error ? reason.message : 'The local controller is unavailable.'}`);
        timer = setTimeout(() => void poll(), 2000);
      }
    }
    void poll();
    return () => { disposed = true; clearTimeout(timer); };
  }, [job.id, pending, stopping, onUpdate]);

  async function cancel() {
    stopRequested.current = true;
    setStopping(true); setError('');
    try { await stopJob(job.id); onClose(); }
    catch (reason) {
      stopRequested.current = false;
      setError(reason instanceof Error ? reason.message : 'The table could not be stopped.');
    } finally { setStopping(false); }
  }
  const title = pending ? 'Opening your table…' : job.status === 'failed'
    ? job.details?.sessionId ? 'The table stopped with an error.' : 'Your table could not start.'
    : job.status === 'stopped' ? 'The table was stopped.' : 'This table has finished.';
  const setupIssue = !pending && job.status === 'failed'
    ? [...(job.logs ?? [])].reverse().find((line) => line.includes('The Engine could not start this table:'))
      ?.split('The Engine could not start this table:')[1]?.trim().replace(/^illegal game setup:\s*/, '').replace(/\[[a-z0-9-]+\]:\s*/g, '· ')
    : '';
  return <main className="table-launch-page"><section aria-labelledby="table-launch-title">
    <div className="table-launch-brand" aria-hidden="true">DD</div>
    <span className="page-eyebrow">DeepDeck · local play</span>
    <h1 id="table-launch-title">{title}</h1>
    <p>{pending ? 'Connecting the saved agents and opening the game on your computer. You do not need to start it again.' : 'Your saved decks and strategies are still available. Any available recording is in Replays.'}</p>
    {pending && <ol className="table-launch-steps" aria-label="Table preparation" role="status">
      <li className="done"><span aria-hidden="true">✓</span><div><strong>Setup accepted</strong><small>Your seats, decks and settings are saved.</small></div></li>
      <li className="current"><span className="async-button-spinner" aria-hidden="true" /><div><strong>{job.status === 'queued' ? 'Waiting for the local worker' : 'Connecting agent & Engine'}</strong><small>The table will open here as soon as it is ready.</small></div></li>
    </ol>}
    <p className="table-launch-match">{job.label}</p>
    {error && <p className="inline-error" role="alert">{error}</p>}
    {setupIssue && <p className="inline-error" role="alert">{setupIssue}</p>}
    {!pending && job.logs?.length > 0 && <details><summary>Technical details</summary><pre>{job.logs.slice(-8).join('\n')}</pre></details>}
    <div className="table-launch-actions">{pending ? <>
      <AsyncActionButton type="button" loading={stopping} loadingLabel="Stopping & saving trace…" onClick={() => void cancel()}>Cancel launch</AsyncActionButton>
      <button type="button" disabled={stopping} onClick={onClose}>Keep preparing in background</button>
    </> : <button type="button" onClick={onClose}>Return to app</button>}</div>
    {pending && <small>Background tables can be reopened from Play. Cancelling stops only this table.</small>}
  </section></main>;
}
