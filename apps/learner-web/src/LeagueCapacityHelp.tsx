const PRODUCTION_SUPPORT = 'https://www.deepdeckleague.com/support';

/** Follow the selected League; never attach the agent key or infer a local tier. */
export function leagueSupportUrl(siteUrl?: string | null): string {
  try {
    const url = new URL(siteUrl || PRODUCTION_SUPPORT);
    const local = ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname);
    if (url.username || url.password || url.search || url.hash ||
      (url.protocol !== 'https:' && !(url.protocol === 'http:' && local))) return PRODUCTION_SUPPORT;
    return new URL('/support', url).href;
  } catch {
    return PRODUCTION_SUPPORT;
  }
}

export default function LeagueCapacityHelp({siteUrl}: {siteUrl?: string | null}) {
  return <span className="league-capacity-help">
    <span>Higher Patreon tiers include more simultaneous League agent places.</span>
    <a href={leagueSupportUrl(siteUrl)} target="_blank" rel="noopener noreferrer"
      aria-label="Compare Patreon plans for more League agent places (opens in a new tab)">
      Compare Patreon plans <span aria-hidden="true">↗</span>
    </a>
    <small>Already supporting? Check your League account access before upgrading. Your current allowance is supplied by the server.</small>
  </span>;
}
