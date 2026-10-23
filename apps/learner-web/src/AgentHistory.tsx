import { useEffect, useState } from 'react';

import AsyncActionButton from './AsyncActionButton';
import { loadAgentRevisions, restoreAgentRevision, type AgentRevision, type ProgrammaticAgent } from './api';

export default function AgentHistory({ agent, dirty, onRestored }: { agent: ProgrammaticAgent; dirty: boolean; onRestored: (agent: ProgrammaticAgent) => void | Promise<void> }) {
  const [open, setOpen] = useState(false);
  const [revisions, setRevisions] = useState<AgentRevision[]>([]);
  const [loading, setLoading] = useState(false);
  const [restoring, setRestoring] = useState<number | null>(null);
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);
  useEffect(() => {
    if (!open) return;
    let active = true;
    setLoading(true); setError('');
    void loadAgentRevisions(agent.id).then((items) => { if (active) setRevisions(items); })
      .catch((reason) => { if (active) setError(reason instanceof Error ? reason.message : 'Saved versions could not be loaded.'); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [open, agent.id, agent.revision, reload]);
  async function restore(revision: number) {
    if (!window.confirm(`Restore strategy and mastered decks from version ${revision}? This creates a new version and keeps your current Python source.`)) return;
    setRestoring(revision); setError('');
    try { await onRestored(await restoreAgentRevision(agent.id, revision, agent.revision)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'The strategy could not be restored.'); }
    finally { setRestoring(null); }
  }
  return <details className="agent-advanced agent-history" open={open} onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary><span>Saved versions</span><small>Current · v{agent.revision}</small></summary>
    <p>Every save keeps a version. Restore weights, card/tag rules and mastered decks as a new version; your current Python stays unchanged.</p>
    {dirty && <p className="inline-notice">Save or discard your draft before restoring another version.</p>}
    {loading ? <p role="status">Loading saved versions…</p> : <ol>{revisions.map((revision) => <li key={revision.revision}><div><strong>Version {revision.revision}{revision.current ? ' · current' : ''}</strong><span>{revision.reason}</span><small>{new Date(revision.createdAt).toLocaleString()} · {revision.deckCount} deck{revision.deckCount === 1 ? '' : 's'} · {revision.patternCount} rule{revision.patternCount === 1 ? '' : 's'}</small></div>{!revision.current && <AsyncActionButton type="button" loading={restoring === revision.revision} loadingLabel="Restoring…" disabled={dirty || restoring !== null} onClick={() => void restore(revision.revision)}>Restore v{revision.revision}</AsyncActionButton>}</li>)}</ol>}
    {error && <div><p className="inline-error" role="alert">{error}</p><button type="button" onClick={() => setReload((value) => value + 1)}>Retry history</button></div>}
  </details>;
}
