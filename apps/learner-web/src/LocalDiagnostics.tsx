import { useEffect, useRef, useState } from 'react';
import { loadLocalDiagnostics, type LocalDiagnosticReport } from './api';

export default function LocalDiagnostics() {
  const [report, setReport] = useState<LocalDiagnosticReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [errorsOnly, setErrorsOnly] = useState(true);
  const epoch = useRef(0);
  useEffect(() => () => { epoch.current++; }, []);
  async function load() {
    const current = ++epoch.current;
    setBusy(true); setMessage('');
    try { const data = await loadLocalDiagnostics(errorsOnly); if (current === epoch.current) setReport(data); }
    catch { if (current === epoch.current) { setReport(null); setMessage('Local diagnostics could not be loaded. Your saved decks, agents and replays were not changed.'); } }
    finally { if (current === epoch.current) setBusy(false); }
  }
  async function copy() {
    if (!report) return;
    try { await navigator.clipboard.writeText(JSON.stringify(report, null, 2)); setMessage('Copied to your clipboard. Review the metadata before sharing it; nothing was uploaded.'); }
    catch { setMessage('Clipboard access was unavailable. You can read the references below; nothing was uploaded.'); }
  }
  return <details className="local-diagnostics"><summary>Local diagnostics · private support journal</summary>
    <p>Request metadata stays on this computer. No API keys, messages, searches, card data or exception text are recorded. Successful read-only polling is omitted.</p>
    <p>The journal shows up to 2,000 entries from the last 14 days, in an 8 MiB database. Expired entries are purged when opened or written. SQLite may use an additional temporary rollback journal during a write; older entries are replaced first.</p>
    <label><input type="checkbox" checked={errorsOnly} onChange={(event) => { setErrorsOnly(event.target.checked); setReport(null); epoch.current++; setBusy(false); }} />Errors and interrupted requests only</label>
    <div className="local-diagnostics-actions"><button type="button" disabled={busy} onClick={() => void load()}>{busy ? 'Reading local journal…' : 'Read local journal'}</button>{report && <button type="button" onClick={() => void copy()}>Copy diagnostic metadata</button>}</div>
    {message && <p role="status">{message}</p>}
    {report && <><p>{report.storedEntries} stored · {(report.usedBytes / 1024).toFixed(0)} KiB. Showing the latest {report.items.length} matching entries.</p>
      {!report.items.length && <p>No matching local diagnostics.</p>}
      <ol>{report.items.map((item) => <li key={item.reference}><details><summary>{item.category.replaceAll('_', ' ')} · {new Date(item.createdAtUnix * 1000).toLocaleString()}</summary><p>{item.method === 'CLIENT' ? 'Display failure · local marker, not an HTTP response' : `${item.method} ${item.route} · status ${item.status} · ${item.durationMs} ms`}</p><code>{item.reference}</code>{item.category === 'response_interrupted' && <p>The response was interrupted after its headers were sent; the displayed status is the original header status, not a completed success.</p>}</details></li>)}</ol>
    </>}
  </details>;
}
