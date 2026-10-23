import { useEffect, useState } from "react";

import { loadLocalStatistics, type LocalStatistics } from "./api";
import InsightsGuide from './InsightsGuide';
import AdvancedInsights from './AdvancedInsights';
import AsyncActionButton from './AsyncActionButton';
import type { DeckSummary, ProgrammaticAgent } from './api';

export default function LocalInsights({ onStartBatch, onViewReplays, agents = [], decks = [] }: { onStartBatch: () => void; onViewReplays: () => void; agents?: ProgrammaticAgent[]; decks?: DeckSummary[] }) {
  const [snapshot, setSnapshot] = useState<{days:number; data:LocalStatistics} | null>(null);
  const [group, setGroup] = useState<"agents" | "decks" | "players">("agents");
  const [days, setDays] = useState(0);
  const [failure, setFailure] = useState<{days:number; message:string} | null>(null);
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    setFailure(null);
    async function refresh() {
      setLoading(true);
      try {
        const result = await loadLocalStatistics(days);
        if (active) { setSnapshot({days,data:result}); setFailure(null); }
      } catch (reason) { if (active) setFailure({days,message:reason instanceof Error ? reason.message : "Statistics could not be loaded."}); }
      finally {if (active) {setLoading(false);timer=setTimeout(() => void refresh(),30_000);}}
    }
    void refresh();
    return () => { active = false; window.clearTimeout(timer); };
  }, [days, revision]);
  const statistics = snapshot?.days === days ? snapshot.data : null;
  const error = failure?.days === days ? failure.message : '';
  const rows = statistics?.[group] ?? [];
  return <div className="insights-hub">
    <div className="insights-controls"><div className="segmented compact"><button type="button" className={group === "agents" ? "active" : ""} onClick={() => setGroup("agents")}>Agents</button><button type="button" className={group === "decks" ? "active" : ""} onClick={() => setGroup("decks")}>Decks</button><button type="button" className={group === "players" ? "active" : ""} onClick={() => setGroup("players")}>Players</button></div><label>Period<select value={days} onChange={(event) => setDays(Number(event.target.value))}><option value="0">All time</option><option value="7">Last 7 days</option><option value="30">Last 30 days</option><option value="90">Last 90 days</option></select></label></div>
    <div className="insights-refresh"><p role="status">{loading ? statistics ? 'Refreshing saved results…' : 'Reading saved match results…' : error ? statistics ? 'Showing the last loaded results for this period.' : 'Saved results are unavailable, not an empty match history.' : 'Saved results loaded. Automatic refresh every 30 seconds.'}</p><AsyncActionButton loading={loading} loadingLabel="Refreshing results…" onClick={()=>setRevision(value=>value+1)}>{error?'Retry results':'Refresh results'}</AsyncActionButton></div>
    {error && <p className="inline-error" role="alert">{error} {statistics?'Previously loaded results remain visible.':'The match count is unknown; no zero results are assumed.'} Retry now or wait for the next automatic check.</p>}
    <InsightsGuide />
    <section className="insight-kpis"><article><span>Recorded matches</span><strong>{statistics?.summary.matches ?? "—"}</strong><small>Each match counted once</small></article><article><span>Agents tested</span><strong>{statistics?.summary.agents ?? "—"}</strong><small>With local match results</small></article><article><span>Decks tested</span><strong>{statistics?.summary.decks ?? "—"}</strong><small>Across players and agents</small></article><article><span>Your matches</span><strong>{statistics?.players.reduce((sum, player) => sum + player.matches, 0) ?? "—"}</strong><small>Human against AI</small></article></section>
    <section className="feature-card performance-table"><header className="feature-heading"><div><span className="section-kicker">Local evidence</span><h2>{group === "agents" ? "How your agents perform" : group === "decks" ? "How your decks perform" : "Your results at the table"}</h2><p>Results come from completed local matches. Small samples need more play before drawing conclusions.</p></div></header>{rows.length ? <div className="table-scroll"><table><thead><tr><th>{group === "agents" ? "Agent" : group === "decks" ? "Deck" : "Player"}</th><th>Matches</th><th>W–L–D</th><th>Win rate</th><th>95% interval</th></tr></thead><tbody>{rows.map((row) => <tr key={row.id}><td><strong>{row.name}</strong>{group !== "decks" && <small>{row.decks} deck{row.decks === 1 ? "" : "s"}</small>}</td><td>{row.matches}</td><td>{row.wins}–{row.losses}–{row.draws}</td><td><div className="rate-cell"><i><b style={{ width: `${row.winRate * 100}%` }} /></i><span>{Math.round(row.winRate * 100)}%</span></div></td><td>{row.confidence95 ? `${Math.round(row.confidence95[0] * 100)}–${Math.round(row.confidence95[1] * 100)}%` : "—"}</td></tr>)}</tbody></table></div> : <div className="dd-empty"><h3>{statistics ? "No results in this view yet" : error ? 'Local results unavailable' : "Loading local results…"}</h3><p>{statistics?'No completed matches match this view. Try another period, play against an agent or set up a batch.':error?'Your saved matches have not been counted. Use Retry results to load them again.':'Reading the saved results for this period. No match needs to be started.'}</p>{statistics && <button className="primary-action" type="button" onClick={onStartBatch}>Set up an AI batch</button>}</div>}</section>
    <section className="insight-grid"><article className="feature-card"><span className="section-kicker">Quick experiment</span><h2>Test a new deck rapidly</h2><p>Start with 20 matches against known opponents. Choose the seats and decks before launching.</p><button className="primary-action" type="button" onClick={onStartBatch}>Set up a 20-match batch</button></article><article className="feature-card"><span className="section-kicker">Recent matches</span><h2>Review the games behind the numbers</h2>{statistics?.recentMatches.length ? <ol className="recent-match-list">{statistics.recentMatches.slice(0, 5).map((match) => <li key={match.id}><strong>{[...new Set(match.participants.map((player) => player.deck))].join(" vs ")}</strong><small>{match.winner ? `${match.winner} won` : "Draw"} · {match.format}</small></li>)}</ol> : <p>{statistics?'Your last completed matches will appear here.':error?'Recent matches are unavailable until results can be loaded.':'Loading recent matches…'}</p>}<button type="button" className="primary-action" onClick={onViewReplays}>Open replays</button></article></section>
    <AdvancedInsights kind={group === 'agents' ? 'agent' : group === 'decks' ? 'deck' : 'player'} days={days} targets={[...new Map([...(group === 'agents' ? agents : group === 'decks' ? decks : [{id:'local-player', name:'You'}]), ...rows].map(row => [row.id, {id:row.id, name:row.name}])).values()]} onSaved={() => setRevision(r => r + 1)} />
  </div>;
}
