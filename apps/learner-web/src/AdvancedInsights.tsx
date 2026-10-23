import { useEffect, useState } from 'react';
import AsyncActionButton from './AsyncActionButton';
import { loadAdvancedStatistics, loadStatisticsStorage, saveStatisticsStorage,
  type AdvancedStatistics, type StatisticsStorage, type TrackingRule } from './api';

type Kind = TrackingRule['kind'];
type Target = {id: string; name: string};
const mb = (bytes: number) => (bytes / 1024 ** 2).toFixed(bytes < 1024 ** 2 ? 2 : 1);
const num = (n: number) => new Intl.NumberFormat(undefined, {maximumFractionDigits: 2}).format(n);

function TurnCurve({data, metric, name}: {data: AdvancedStatistics; metric: string; name: string}) {
  const samples = data.curve.filter(p => p.values[metric]);
  if (!samples.length) return null;
  const min = Math.min(0, ...samples.map(p => p.values[metric].mean));
  const max = Math.max(1, ...samples.map(p => p.values[metric].mean));
  const maxTurn = Math.max(2, ...samples.map(p => p.turn));
  const path = samples.map(p => `${40 + (p.turn - 1) / (maxTurn - 1) * 600},${175 - (p.values[metric].mean - min) / (max - min) * 145}`).join(' ');
  return <figure className="insight-curve"><svg viewBox="0 0 680 210" role="img" aria-label={`${name}: observed mean by game turn`}><line x1="40" x2="640" y1="175" y2="175" /><line x1="40" x2="40" y1="30" y2="175" /><text x="8" y="37">{num(max)}</text><text x="8" y="178">{num(min)}</text><text x="40" y="200">Turn 1</text><text x="583" y="200">Turn {maxTurn}</text><polyline points={path} /></svg><figcaption>{name} · one last observation per numbered turn and game. Long games contribute more late-turn samples; this is not a prediction.</figcaption><details><summary>View values and sample sizes</summary><div className="table-scroll"><table><thead><tr><th>Game turn</th><th>Observed mean</th><th>Samples</th></tr></thead><tbody>{samples.map(p => <tr key={p.turn}><td>{p.turn}</td><td>{num(p.values[metric].mean)}</td><td>{p.values[metric].samples}</td></tr>)}</tbody></table></div></details></figure>;
}

export default function AdvancedInsights({kind, targets, days, onSaved}: {kind: Kind; targets: Target[]; days: number; onSaved: () => void}) {
  const [settings, setSettings] = useState<StatisticsStorage | null>(null);
  const [rules, setRules] = useState<TrackingRule[]>([]);
  const [limit, setLimit] = useState('128');
  const [identity, setIdentity] = useState('');
  const [data, setData] = useState<AdvancedStatistics | null>(null);
  const [metric, setMetric] = useState('cardsInHand');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let active = true;
    void loadStatisticsStorage().then(s => {if (active) {setSettings(s); setRules(s.rules); setLimit(String(s.storageLimitBytes / 1024 ** 2));}}).catch(e => {if (active) setError(String(e));});
    const timer = setInterval(() => {void loadStatisticsStorage().then(s => {if (active) setSettings(current => current ? {...current, usedBytes:s.usedBytes, matchFiles:s.matchFiles, legacyBytes:s.legacyBytes, lastError:s.lastError, legacyEngineBytes:s.legacyEngineBytes, engineAnalyticsDisabled:s.engineAnalyticsDisabled} : s);}).catch(() => {});}, 30000);
    return () => {active = false; clearInterval(timer);};
  }, []);
  const key = JSON.stringify(targets.map(t => t.id));
  useEffect(() => {setIdentity(current => targets.some(t => t.id === current) ? current : targets[0]?.id ?? '');}, [kind, key]);
  useEffect(() => {
    let active = true; let timer: ReturnType<typeof setTimeout>;
    setData(null);
    async function refresh() {
      if (!identity) return;
      try {const result = await loadAdvancedStatistics(kind, identity, days); if (active) setData(result);}
      catch (e) {if (active) setError(String(e));}
      finally {if (active) timer = setTimeout(() => void refresh(), 30000);}
    }
    void refresh(); return () => {active = false; clearTimeout(timer);};
  }, [kind, identity, days, revision]);
  const selected = rules.find(r => r.kind === kind && r.id === identity)?.groups ?? [];
  function toggle(group: string) {
    const next = selected.includes(group) ? selected.filter(g => g !== group) : [...selected, group];
    setRules(current => [...current.filter(r => !(r.kind === kind && r.id === identity)), ...(next.length ? [{kind, id:identity, groups:next}] : [])]);
    setNotice('Unsaved tracking choices');
  }
  async function save() {
    setSaving(true); setError('');
    try {
      const amount = Number(limit);
      if (!Number.isInteger(amount) || amount < 1 || amount > 10240) throw new Error('Choose a whole number of MB between 1 and 10240.');
      const result = await saveStatisticsStorage({storageLimitBytes:amount * 1024 ** 2, rules});
      setSettings(result); setRules(result.rules); setRevision(r => r + 1); onSaved();
      setNotice(`Saved. New tracking applies from the next match.${result.removedMatches ? ` ${result.removedMatches} oldest match summaries removed to meet your budget.` : ''}`);
    } catch (e) {setError(e instanceof Error ? e.message : String(e));}
    finally {setSaving(false);}
  }
  const available = Object.keys(data?.snapshots ?? {});
  const curveMetric = available.includes(metric) ? metric : available[0];
  const dirty = settings && (JSON.stringify(settings.rules) !== JSON.stringify(rules) || Number(limit) * 1024 ** 2 !== settings.storageLimitBytes);
  return <section className="feature-card advanced-insights" aria-label="Advanced statistics">
    <header className="feature-heading"><div><span className="section-kicker">Your experiment</span><h2>Track what matters to your deck</h2><p>Choose the agent, deck or player and the details worth keeping. Advanced tracking is off until you enable it.</p></div>{dirty && <span className="tracking-draft">Unsaved changes</span>}</header>
    {error && <p className="inline-error" role="alert">{error}</p>}{settings?.lastError && <p className="inline-error" role="alert">Some statistics could not be saved: {settings.lastError}</p>}
    {settings?.engineAnalyticsDisabled === false && <p className="inline-error" role="alert">Your game engine did not confirm that duplicate statistics are disabled. Restart with the updated Engine to keep new local statistics within this budget.</p>}
    {!!settings?.legacyEngineBytes && <p className="advanced-data-note">{mb(settings.legacyEngineBytes)} MB of historical Engine statistics are included in the space used and preserved read-only. New local matches request that duplicate Engine statistics be disabled; the Engine response is checked to confirm this.</p>}
    {data && (data.totals.unconfirmedChoices?.sum ?? 0) > 0 && <p className="advanced-data-note">Some SDK choices had no confirmation or used a fallback. They are not counted as successful casts or abilities. Event counts reflect captured Engine events; automatic mana payments may not emit mana-production events.</p>}
    <div className="advanced-insights-layout"><div className="tracking-config"><label>Track this {kind}<select value={identity} onChange={e => setIdentity(e.target.value)}><option value="" disabled>Choose a {kind}</option>{targets.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}</select></label>
      {!targets.length && <p>Create an agent or deck, or play a local game first.</p>}
      <fieldset disabled={!identity || !settings}><legend>Keep these metric groups</legend>{settings?.groups.map(g => <label className="tracking-group" key={g.id}><input type="checkbox" checked={selected.includes(g.id)} onChange={() => toggle(g.id)} /><span><strong>{g.name}</strong><small>{g.description}</small></span></label>)}</fieldset>
      <div className="statistics-budget"><h3>Statistics storage</h3><p>Separate from your replay space. This is a maximum, not a disk reservation.</p><label>Maximum statistics space (MB)<input type="number" min={1} max={10240} value={limit} onChange={e => setLimit(e.target.value)} /></label><meter min={0} max={settings?.storageLimitBytes ?? 1} value={settings?.usedBytes ?? 0} aria-label="Statistics disk usage" /><small>{settings ? `${mb(settings.usedBytes)} / ${mb(settings.storageLimitBytes)} MB · ${settings.matchFiles} match summaries` : 'Checking storage…'}</small><p>When full, the oldest match summaries are removed with their advanced data. Replays and competition brackets are not deleted.</p>{!!settings?.legacyBytes && <p>{mb(settings.legacyBytes)} MB of older result files are counted and kept read-only. The limit cannot be reduced below these files.</p>}<details><summary>What does this budget cover?</summary><p>{settings?.quotaScope} Settings and lock files are small fixed overhead.</p></details></div>
      <AsyncActionButton className="primary-action" loading={saving} loadingLabel="Saving tracking…" disabled={!settings || !dirty} onClick={() => void save()}>Save tracking & storage</AsyncActionButton>{notice && <p role="status" className="tracking-notice">{notice}</p>}
    </div><div className="advanced-evidence"><span className="section-kicker">Observed evidence</span><h3>{targets.find(t => t.id === identity)?.name ?? 'Choose a target'}</h3><p>{data ? `${data.trackedSeatResults} tracked seat results / ${data.seatResults} retained seat results in this period` : identity ? 'Loading recorded measurements…' : 'Choose what you want to investigate.'}</p>
      {data && !data.trackedSeatResults && <div className="dd-empty"><h3>No advanced samples yet</h3><p>Enable the groups you want, save, then play or run a batch. Existing results are not backfilled with guessed measurements.</p></div>}
      {!!data?.trackedSeatResults && <><div className="advanced-metric-grid">{Object.entries(data.totals).map(([id, value]) => <article key={id}><span>{settings?.metrics[id] ?? id}</span><strong>{num(value.mean)}</strong><small>Mean per tracked seat result · n={value.samples}</small><small>Total: {num(value.sum)}</small></article>)}</div>{available.length > 0 && <><label>Observed turn curve<select value={curveMetric} onChange={e => setMetric(e.target.value)}>{available.map(m => <option key={m} value={m}>{settings?.metrics[m] ?? m}</option>)}</select></label><TurnCurve data={data} metric={curveMetric} name={settings?.metrics[curveMetric] ?? curveMetric} /></>}<p className="advanced-data-note">{data.note} {data.droppedTurnSamples > 0 ? `${data.droppedTurnSamples} older turn samples were omitted by the per-match sample limit.` : ''}</p></>}
    </div></div>
  </section>;
}
