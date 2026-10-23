import type { ReplayMatchSummary } from './api';

export default function ReplayMatchFacts({ summary, names = [] }: { summary?: ReplayMatchSummary; names?: string[] }) {
  if (!summary) return <div className="replay-match-facts"><span>Score and turns not recorded</span></div>;
  const score = summary.winsByPlayerId;
  const breakdown = summary.roundTurns.map((round) => `Game ${round.round}: ${round.turns} turns${round.complete ? '' : ' observed'}`).join(' · ');
  return <div className="replay-match-facts">
    <div aria-label="Games won"><small>{summary.scope === 'game' ? 'Recorded game wins' : 'Games won'}</small><strong title={summary.playerIds.map((id, index) => `${names[index] || `Seat ${index + 1}`}: ${score?.[id] ?? '?'} game wins`).join(' · ')}>{score && summary.playerIds.length ? summary.playerIds.map((id) => score[id] ?? '?').join(' – ') : 'Not recorded'}</strong></div>
    <div title={breakdown}><small>{summary.turnsExact ? 'Turns played' : 'Turns observed'}</small><strong>{summary.totalTurns ?? '—'}<span> turns{summary.roundTurns.length > 1 ? ` · ${summary.roundTurns.length} games` : ''}</span></strong></div>
    {summary.totalTurns !== null && !summary.turnsExact && <small className="replay-turns-note">Partial turn history; this is the recorded minimum.</small>}
  </div>;
}
