import { useEffect, useRef, useState } from 'react';
import AsyncActionButton from './AsyncActionButton';
import PlaySettings, { DEFAULT_PLAY_SETTINGS, playSettingsPayload } from './PlaySettings';
import { DeckArtwork } from './DeckVisuals';
import DeckPicker from './DeckPicker';
import HelpTip from './HelpTip';
import { ParticipantKindPicker, ParticipantDeckPicker } from './ParticipantPicker';
import { controlLocalCompetition, createLocalCompetition, loadJob, loadLocalCompetition, loadLocalCompetitions, loadStatus,
  type CompetitionStanding, type DeckSummary, type Job, type LocalCompetition, type LocalCompetitionKind, type ProgrammaticAgent } from './api';

export const COMPETITION_FORMATS: Array<{id: LocalCompetitionKind; name: string; description: string}> = [
  {id: 'single', name: 'Single elimination', description: 'One loss eliminates an entrant. Winners advance through a seeded bracket.'},
  {id: 'double', name: 'Double elimination', description: 'A winners bracket and a losers bracket. Two losses eliminate an entrant. The grand final resets if the undefeated finalist loses.'},
  {id: 'round_robin', name: 'Round robin', description: 'Everyone faces everyone. Choose one or several legs; the final standings determine the winner.'},
  {id: 'round_robin_cut', name: 'Round robin → playoffs', description: 'Everyone faces everyone first. The highest-ranked entrants qualify and their standings seed a single-elimination bracket.'},
  {id: 'swiss', name: 'Swiss', description: 'A fixed number of rounds, paired by similar match points. Nobody is eliminated. Repeat opponents are avoided where possible.'},
  {id: 'loss_limit', name: 'Elimination after N losses', description: 'Entrants keep playing until they reach your loss limit. The last remaining entrant wins. This is not a traditional double-elimination bracket.'},
  {id: 'groups_cut', name: 'Groups → playoffs', description: 'Play within balanced seeded groups, then qualify the best entrants in each group for playoffs.'},
  {id: 'random_season', name: 'Random season', description: 'Opponents are independent of standings. Legacy uses rounds; Commander uses a match quota per player, with balanced four-player tables and optional byes.'},
  {id: 'random_season_cut', name: 'Random season → playoffs', description: 'Play a season against random opponents, then use its standings to seed the playoffs.'},
];
const label = (kind: LocalCompetitionKind) => COMPETITION_FORMATS.find(f => f.id === kind)?.name ?? kind;
const stageLabel: Record<string, string> = { winners: 'Winners bracket', losers: 'Losers bracket', final: 'Grand final', round_robin: 'Round robin', swiss: 'Swiss rounds', loss_limit: 'Survival rounds', groups: 'Group matches', season: 'Season matches' };
type DraftEntrant = { key: number; agent_id: string; deck_id: string; name: string; kind?: 'human' | 'agent' };

export function CompetitionBuilder({ agents, decks = [], refreshDecks, engineUrl, ready, onCreated, onStartEngine, enginePreparing = false, engineError }: { agents: ProgrammaticAgent[]; decks?: DeckSummary[]; refreshDecks?: () => void | Promise<void>; engineUrl: string; ready: boolean; onCreated: (competition: LocalCompetition) => void; onStartEngine?: () => Promise<void>; enginePreparing?: boolean; engineError?: string }) {
  const [format, setFormat] = useState<'legacy' | 'commander'>('legacy');
  const eligible = agents.filter(a => a.format === format && a.decks.some(d => !d.unavailable));
  const [kind, setKind] = useState<LocalCompetitionKind>('single');
  const [name, setName] = useState('My local competition');
  const [entrants, setEntrants] = useState<DraftEntrant[]>([]);
  const [seeding, setSeeding] = useState('random');
  const [legs, setLegs] = useState(1);
  const [rounds, setRounds] = useState<string | null>(null);
  const [allowSeasonByes, setAllowSeasonByes] = useState(false);
  const [losses, setLosses] = useState(3);
  const [cut, setCut] = useState<string | null>(null);
  const [groups, setGroups] = useState(2);
  const [qualifiers, setQualifiers] = useState(1);
  const [playoffWins, setPlayoffWins] = useState(1);
  const [reseed, setReseed] = useState(true);
  const [seedAdvantage, setSeedAdvantage] = useState(true);
  const [deckPicker, setDeckPicker] = useState<number | null>(null);
  const [engineBusy, setEngineBusy] = useState(false);
  const [settings, setSettings] = useState({...DEFAULT_PLAY_SETTINGS, matchLimit: '600'});
  const chosenMatchLimit = useRef<string|null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const errorNotice = useRef<HTMLParagraphElement>(null);
  useEffect(() => {
    if (error) {
      errorNotice.current?.focus();
      errorNotice.current?.scrollIntoView?.({block: 'nearest'});
    }
  }, [error]);
  function add(agent: ProgrammaticAgent) {
    setEntrants(current => [...current, { key: Math.max(0, ...current.map(p => p.key)) + 1, agent_id: agent.id, deck_id: 'random', name: agent.name }]);
  }
  function update(key: number, patch: Partial<DraftEntrant>) { setEntrants(current => current.map(p => p.key === key ? {...p, ...patch} : p)); }
  const n = entrants.length;
  const hasHuman = entrants.some(p => p.kind === 'human');
  useEffect(()=>{
    const desired=chosenMatchLimit.current ?? (hasHuman ? '' : '600');
    const matchLimit=hasHuman ? desired : desired || '600';
    setSettings(current=>current.matchLimit===matchLimit ? current : {...current,matchLimit});
  },[hasHuman]);
  const rr = kind === 'round_robin' || kind === 'round_robin_cut' || kind === 'groups_cut';
  const season = kind.startsWith('random_season');
  const commanderSeason = format === 'commander' && season;
  const matchStep = n % 4 === 0 ? 1 : n % 2 === 0 ? 2 : 4;
  const roundDefault = Math.max(1, Math.ceil(Math.log2(Math.max(n, 2))));
  const seasonDefault = commanderSeason && !allowSeasonByes ? Math.ceil(roundDefault / matchStep) * matchStep : roundDefault;
  const seasonFieldLabel = commanderSeason ? 'Season matches per player' : season ? 'Season rounds' : 'Swiss rounds';
  const playoffs = ['single', 'double', 'round_robin_cut', 'groups_cut', 'random_season_cut'].includes(kind);
  const cutField = kind === 'round_robin_cut' || kind === 'random_season_cut';
  const availableFormats = COMPETITION_FORMATS.filter(f => format === 'legacy' || ['single', 'groups_cut', 'random_season', 'random_season_cut'].includes(f.id));
  const swissRounds = rounds === null ? seasonDefault : rounds === '' ? NaN : Number(rounds);
  const playoffCut = cut === null ? Math.min(Math.max(n, 2), 4) : cut === '' ? NaN : Number(cut);
  const whole = (value: number, low: number, high: number) => Number.isInteger(value) && value >= low && value <= high;
  const powerFour = (value: number): boolean => value >= 4 && Number.isInteger(Math.log(value) / Math.log(4));
  const validation = n < 2 ? 'Add at least two entrants.'
    : entrants.some(p => p.kind === 'human' ? !decks.some(d => d.id === p.deck_id && d.format === format && !d.unavailable) : !eligible.some(a => a.id === p.agent_id && (p.deck_id === 'random' || a.decks.some(d => d.id === p.deck_id && !d.unavailable)))) ? 'Choose a local deck for the player and an available agent with a mastered deck for each AI.'
    : rr && !whole(legs, 1, 10) ? 'Choose a whole number of round-robin legs from 1 to 10.'
    : cutField && !whole(playoffCut, 2, n) ? `Choose between 2 and ${n} playoff qualifiers.`
    : format === 'commander' && n < 4 ? 'Commander needs at least four entrants, with four players per table.'
    : format === 'commander' && !season && n % 4 ? 'Commander elimination/groups need complete four-player tables. Use a random season for other field sizes.'
    : format === 'commander' && kind === 'single' && !powerFour(n) ? 'Commander elimination needs 4, 16, 64… entrants. Use a season with playoffs for other fields.'
    : format === 'commander' && cutField && !powerFour(playoffCut) ? 'Commander playoffs need 4, 16, 64… qualifiers.'
    : kind === 'groups_cut' && (!whole(groups, 2, Math.floor(n / 2)) || !whole(qualifiers, 1, Math.floor(n / groups))) ? 'Every group needs at least two entrants and enough places for its qualifiers.'
    : format === 'commander' && kind === 'groups_cut' && (groups * 4 !== n || !powerFour(groups * qualifiers)) ? 'Commander groups need exactly four entrants and a total of 4, 16, 64… playoff qualifiers.'
    : season && !whole(swissRounds, 1, 100) ? commanderSeason ? 'Choose 1 to 100 season matches per player.' : 'Choose 1 to 100 season rounds.'
    : commanderSeason && !allowSeasonByes && n * swissRounds % 4 !== 0 ? `With ${n} entrants, choose a multiple of ${matchStep} matches per player, or allow season byes. The number of player appearances must be divisible by four.`
    : playoffs && !whole(playoffWins, 1, 10) ? 'Choose 1 to 10 match wins to advance.'
    : kind === 'swiss' && !whole(swissRounds, 1, n) ? `Choose a whole number of Swiss rounds from 1 to ${n}.`
    : kind === 'loss_limit' && !whole(losses, 1, 100) ? 'Choose a whole number of losses from 1 to 100.' : '';
  const expected = validation || `${n} entrants · ${format === 'legacy' ? 'duels' : 'four-player tables'}${playoffs ? ` · first to ${playoffWins} playoff match win${playoffWins > 1 ? 's' : ''}` : ''}`;
  async function launch() {
    setBusy(true); setError('');
    try {
      if (validation) throw new Error(validation);
      // A pending or transient health read must not leave an otherwise valid
      // form permanently disabled. Recheck before creating any event.
      if (!ready) {
        const observed = await loadStatus();
        if (!observed.engine.healthy || observed.engine.url.replace(/\/$/, '') !== engineUrl.replace(/\/$/, '')) {
          throw new Error('The local Engine is not ready yet. Start it above, wait for preparation to finish, then try again. No competition was created.');
        }
      }
      const competition = await createLocalCompetition({ name, type: kind, format, seeding,
        entrants: entrants.map(({agent_id, deck_id, name: entrantName, kind: participantKind}) => ({agent_id, deck_id, name: entrantName, ...(participantKind === 'human' ? {kind: 'human'} : {})})),
        ...(rr ? {legs} : {}), ...(kind === 'swiss' || season ? {rounds: swissRounds} : {}), ...(kind === 'loss_limit' ? {losses} : {}), ...(cutField ? {cut: playoffCut} : {}),
        ...(kind === 'groups_cut' ? {groups, qualifiers_per_group: qualifiers} : {}),
        ...(commanderSeason ? {allow_season_byes: allowSeasonByes} : {}),
        ...(playoffs ? {playoff_wins: playoffWins, reseed: kind !== 'double' && reseed, seed_advantage: seedAdvantage} : {}),
        engine_url: engineUrl, ...playSettingsPayload(settings, hasHuman, format === 'legacy' ? 2 : 4),
      });
      onCreated(competition);
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not create the competition.'); }
    finally { setBusy(false); }
  }
  return <div className="competition-builder" onChangeCapture={() => {if (error) setError('');}}>
    {!ready && !enginePreparing && engineError && <p className="inline-error" role="alert">Engine preparation failed: {engineError} You can retry without launching a competition.</p>}
    {!ready && <section className="setup-card runtime-setup"><h3>{enginePreparing || engineBusy ? 'Preparing your game engine…' : 'Start your game engine'}</h3><p>The competition needs the local Engine. Keep preparing below while it starts; no matches are launched by this button.</p>{onStartEngine && <AsyncActionButton loading={engineBusy || enginePreparing} loadingLabel="Preparing Engine…" onClick={async () => {setEngineBusy(true); setError(''); try {await onStartEngine();} catch (reason) {setError(reason instanceof Error ? reason.message : 'Could not start Engine.');} finally {setEngineBusy(false);}}}>Start game engine</AsyncActionButton>}</section>}
    <section className="setup-card"><header className="feature-heading"><div><span className="section-kicker">Competition</span><h2>Your players. Your tournament.</h2><p>{format === 'legacy' ? 'Legacy matches are best of three Magic games.' : 'Commander matches are one four-player Magic game.'} Playoff series can contain several matches.</p></div></header>
      <div className="competition-form-grid">
      <label>Game format<HelpTip label="Game format">Legacy uses two-player matches, each best of three games. Commander uses four-player tables. Changing format keeps entrants visible but you must reselect incompatible agents and decks.</HelpTip><select aria-label="Game format" value={format} onChange={e => {setFormat(e.target.value as 'legacy' | 'commander'); setKind('single'); setSettings(current => ({...current, startingSeat: 'rotate'}));}}><option value="legacy">Legacy · 1 vs 1</option><option value="commander">Commander · 4 players</option></select></label>
      <label>Competition name<HelpTip label="Competition name">A name to find this event later in your local competition history.</HelpTip><input aria-label="Competition name" value={name} maxLength={120} onChange={e => setName(e.target.value)} /></label>
      <label>Competition format<HelpTip label="Competition format">Choose how opponents are scheduled and how a champion is determined. Commander only offers formats that can keep four players at every table.</HelpTip><select aria-label="Competition format" value={kind} onChange={e => setKind(e.target.value as LocalCompetitionKind)}>{availableFormats.map(f => <option key={f.id} value={f.id}>{f.name}</option>)}</select></label>
      <label>Initial seeding<HelpTip label="Initial seeding">The initial ranking used for groups, elimination brackets and final tiebreaks. Shuffle uses the experiment seed; registration order follows the entrant list. Season results replace this ranking for playoffs.</HelpTip><select aria-label="Initial seeding" value={seeding} onChange={e => setSeeding(e.target.value)}><option value="random">Shuffle entrants</option><option value="registration">Registration order</option></select></label>
      {rr && <label>Round-robin legs<HelpTip label="Round-robin legs">How often each pair meets in Legacy, within its group if applicable. For Commander groups, each leg is one match with all four group members.</HelpTip><input aria-label="Round-robin legs" type="number" min={1} max={10} value={legs} onChange={e => setLegs(Number(e.target.value))} /></label>}
      {cutField && <label>Playoff qualifiers<HelpTip label="Playoff qualifiers">How many entrants advance from the season standings. Legacy accepts any number from two; Commander playoffs need 4, 16, 64… qualifiers.</HelpTip><input aria-label="Playoff qualifiers" type="number" step={1} min={2} max={Math.max(n, 2)} value={cut ?? playoffCut} onChange={e => setCut(e.target.value)} /></label>}
      {kind === 'groups_cut' && <><label>Groups<HelpTip label="Groups">Entrants are distributed in a seeded snake to balance the groups. Legacy groups differ in size by at most one. Commander requires exactly four entrants in every group.</HelpTip><input aria-label="Groups" type="number" min={2} max={Math.max(2, Math.floor(n / 2))} value={groups} onChange={e => setGroups(Number(e.target.value))} /></label><label>Qualifiers per group<HelpTip label="Qualifiers per group">The best entrants from each group advance. Group winners are seeded before runners-up, then by match percentage and the usual tiebreaks.</HelpTip><input aria-label="Qualifiers per group" type="number" min={1} max={Math.max(1, Math.floor(n / groups))} value={qualifiers} onChange={e => setQualifiers(Number(e.target.value))} /></label></>}
      {(kind === 'swiss' || season) && <label>{seasonFieldLabel}<HelpTip label={seasonFieldLabel}>{commanderSeason ? 'Real matches per player when byes are disabled. Players may rest between four-player tables without receiving points. For equal counts, entrants × matches per player must be divisible by four. With byes enabled, a bye counts toward this quota instead of a real match and awards 3 standings points, never a played-match win.' : season ? 'Every entrant plays once per round, except a rotating bye in odd Legacy fields. Opponents are randomized without using standings; repeat opponents are reduced where possible.' : 'Each round pairs entrants with similar points. Opponent repeats are minimized. Nobody is eliminated.'}</HelpTip><input aria-label={seasonFieldLabel} type="number" step={commanderSeason && !allowSeasonByes ? matchStep : 1} min={commanderSeason && !allowSeasonByes ? matchStep : 1} max={season ? 100 : Math.max(n, 1)} value={rounds ?? swissRounds} onChange={e => setRounds(e.target.value)} /></label>}
      {commanderSeason && <label><span>Allow season byes<HelpTip label="Allow season byes">Allows any match quota. At most three entrants receive one seeded bye to fill otherwise incomplete player appearances. Each awarded bye gives 3 standings points but no real win, match, game or replay. Ordinary rests between scheduled tables do not award points.</HelpTip></span><input aria-label="Allow season byes" type="checkbox" checked={allowSeasonByes} onChange={e => setAllowSeasonByes(e.target.checked)} /></label>}
      {kind === 'loss_limit' && <label>Losses before elimination<HelpTip label="Losses before elimination">An entrant is eliminated after this many match losses. Errors and interrupted matches never count as losses.</HelpTip><input aria-label="Losses before elimination" type="number" min={1} max={100} value={losses} onChange={e => setLosses(Number(e.target.value))} /></label>}
      {playoffs && <><label>Playoff match wins to advance<HelpTip label="Playoff match wins to advance">How many complete matches a participant must win to take a playoff series. In Legacy, four wins means a best-of-seven series of matches; each match itself is best of three Magic games. Stop as soon as someone reaches the target. Commander series keep all four participants until one reaches the target.</HelpTip><input aria-label="Playoff match wins to advance" type="number" min={1} max={10} value={playoffWins} onChange={e => setPlayoffWins(Number(e.target.value))} /><small>{format === 'legacy' ? `Best of ${playoffWins * 2 - 1} matches` : `First to ${playoffWins} match wins`}</small></label>
      {kind !== 'double' && <label><span>Reseed after each round<HelpTip label="Reseed after each round">Preserve the qualification ranking throughout playoffs. In Legacy, the best surviving seed faces the lowest surviving seed each round. Commander redistributes surviving seeds across balanced four-player tables. A fixed Legacy bracket preserves its original paths instead.</HelpTip></span><input aria-label="Reseed after each round" type="checkbox" checked={format === 'commander' || reseed} disabled={format === 'commander'} onChange={e => setReseed(e.target.checked)} /></label>}
      <label><span>Higher-seed opening advantage<HelpTip label="Higher-seed opening advantage">The best seed starts the first match of the series. Later series matches rotate the opening seat. This chooses the first game of a Legacy match only; later games follow normal match rules. Overrides Opening seat during playoffs, not during the season.</HelpTip></span><input aria-label="Higher-seed opening advantage" type="checkbox" checked={seedAdvantage} onChange={e => setSeedAdvantage(e.target.checked)} /></label></>}
      </div><p className="competition-explainer">{COMPETITION_FORMATS.find(f => f.id === kind)?.description}</p>
      {commanderSeason && n >= 4 && whole(swissRounds, 1, 100) && (allowSeasonByes || n * swissRounds % 4 === 0) && <p className="competition-explainer" aria-label="Commander season plan">{n} entrants · {swissRounds} {allowSeasonByes ? 'match-or-bye opportunities' : 'real matches'} per player · {Math.floor(n * swissRounds / 4)} four-player matches · {allowSeasonByes ? n * swissRounds % 4 : 0} awarded byes. Each bye awards 3 standings points, not a played win.</p>}
      <p className="muted">{format === 'legacy' ? 'Odd fields receive byes. Elimination brackets accept non-power-of-two fields.' : 'Every Commander table has four players. Seasons accept any field of at least four entrants. Equal real-match counts require entrants × matches per player divisible by four; optional byes allow other quotas. Elimination fields and playoff cuts still need 4, 16, 64… entrants.'} Initial seed is the final tie-breaker; shuffled seeding makes that order reproducible from the experiment seed.</p>
    </section>
    <section className="setup-card"><header className="feature-heading"><div><span className="section-kicker">Entrants · {n}</span><h2>Build your field</h2><p>Each entrant is a player or AI with its deck choice. Human matches use shared-screen handovers on this computer; AI entries can reuse an agent with different decks or strategies.</p></div><button type="button" disabled={!eligible.length} onClick={() => setEntrants(current => [...current, ...eligible.map((a, i) => ({key: Math.max(0, ...current.map(p => p.key)) + i + 1, agent_id: a.id, deck_id: 'random', name: a.name}))])}>Add all agents</button></header>
      <div className="entrant-add"><label>Add an entrant<HelpTip label="Add an entrant">Add any number of local players or AIs allowed by this competition format. Humans pass the screen before revealing their hands; this does not invite online players. An AI uses only its mastered decks. Players can import local or League decks.</HelpTip><select aria-label="Add an entrant" value="" onChange={e => {if (e.target.value === 'human') setEntrants(current => {const key=Math.max(0, ...current.map(p=>p.key))+1; return [...current, {key, kind: 'human', agent_id: '', deck_id: '', name: current.some(p=>p.kind==='human') ? `Player ${key}` : 'You'}];}); else {const agent = eligible.find(a => a.id === e.target.value); if (agent) add(agent);}}}><option value="">Choose a player or AI…</option><option value="human">Local player</option>{eligible.map(a => <option key={a.id} value={a.id}>AI · {a.name}</option>)}</select></label></div>
      {!eligible.length && <p>Add local players, or create a {format === 'legacy' ? 'Legacy' : 'Commander'} agent in Agents if you want AI entrants.</p>}
      {!n && <div className="competition-empty">Your competition starts here. Add players or agents individually, or bring in your entire agent collection.</div>}
      <ol className="competition-entrants">{entrants.map((p, index) => {const agent = eligible.find(a => a.id === p.agent_id); const deck = p.kind==='human' ? decks.find(d=>d.id===p.deck_id) : agent?.decks.find(d => d.id === p.deck_id) ?? agent?.decks[0]; return <li key={p.key}>
        <span className="entrant-seed">{index + 1}</span>{deck && <DeckArtwork deck={deck} className="entrant-art" />}<ParticipantKindPicker kind={p.kind ?? 'agent'} onChange={next => update(p.key, {kind: next, agent_id: next === 'human' ? '' : eligible[0]?.id ?? '', deck_id: next === 'human' ? '' : 'random', name: next === 'human' ? hasHuman && p.kind!=='human' ? `Player ${index+1}` : 'You' : eligible[0]?.name ?? 'AI'})} />
        <label>Entrant name<HelpTip label={`Entrant ${index + 1} name`}>The display name for this entrant in standings and brackets. Multiple entries may use the same AI with different decks.</HelpTip><input aria-label={`Entrant ${index + 1} name`} value={p.name} maxLength={100} onChange={e => update(p.key, {name: e.target.value})} /><small>{p.kind === 'human' ? 'Local player' : agent?.name ?? 'Unavailable agent'}</small></label>
        <ParticipantDeckPicker prefix={`Entrant ${index + 1} `} kind={p.kind ?? 'agent'} agentId={p.agent_id} deckId={p.deck_id} agents={eligible} decks={p.kind === 'human' ? decks.filter(d => d.format === format) : agent?.decks.filter(d => !d.unavailable) ?? []} onAgent={id => update(p.key, {agent_id: id, deck_id: 'random'})} onDeck={id => update(p.key, {deck_id: id})} onBrowse={() => setDeckPicker(p.key)} />
        <button type="button" aria-label={`Remove entrant ${index + 1}`} onClick={() => setEntrants(current => current.filter(item => item.key !== p.key))}>Remove</button></li>;})}</ol>
    </section>
    <PlaySettings value={settings} onChange={next=>{if(next.matchLimit!==settings.matchLimit)chosenMatchLimit.current=next.matchLimit;setSettings(next);}} hasHuman={hasHuman} seats={format === 'legacy' ? 2 : 4} />
    {deckPicker !== null && <DeckPicker decks={decks} format={format} onImported={refreshDecks ?? (() => {})} onClose={() => setDeckPicker(null)} onPick={id => {update(deckPicker, {deck_id: id}); setDeckPicker(null);}} />}
    {validation && <p id="competition-start-validation" className="competition-validation" role="status">{validation}</p>}
    <section className="launch-bar"><div><strong>{expected}</strong><small>{ready ? 'Engine ready' : 'Engine status will be checked before starting'} · matches run sequentially on this computer</small><small>Keep the app open. Errors pause the competition; completed results remain saved.</small></div><AsyncActionButton className="launch-button" loading={busy} loadingLabel="Creating competition…" aria-describedby={validation ? 'competition-start-validation' : undefined} onClick={() => void launch()}>Start competition</AsyncActionButton></section>
    {error && <p ref={errorNotice} tabIndex={-1} className="inline-error" role="alert">{error}</p>}
  </div>;
}

function Standings({rows}: {rows: CompetitionStanding[]}) {
  return <div className="competition-table-scroll"><table className="competition-standings"><thead><tr><th>Rank</th><th>Entrant</th><th>W–L–D</th><th>Points</th><th>Games</th><th>Byes</th><th title="Opponents’ match-win percentage">Opp. match %</th><th title="Game-win percentage (33% floor for tiebreakers)">Game %</th><th title="Opponents’ game-win percentage">Opp. game %</th></tr></thead><tbody>{rows.map(r => <tr key={r.id}><td>{r.rank}</td><th scope="row">{r.name}</th><td>{r.wins}–{r.losses}–{r.draws}</td><td><strong>{r.points}</strong></td><td>{r.gameWins}–{r.gameLosses}</td><td>{r.byes}</td><td>{(r.omw * 100).toFixed(1)}%</td><td>{(r.gwp * 100).toFixed(1)}%</td><td>{(r.ogw * 100).toFixed(1)}%</td></tr>)}</tbody></table></div>;
}

export function RoundRobinMatrix({competition, onReplays}: {competition: LocalCompetition; onReplays: (id?: string) => void}) {
  const pairs = new Map<string, LocalCompetition['matches']>();
  for (const match of competition.matches) {
    if (match.stage !== 'round_robin' || !match.players[0] || !match.players[1]) continue;
    for (const [row, column] of [[match.players[0], match.players[1]], [match.players[1], match.players[0]]]) {
      const key = JSON.stringify([row, column]);
      pairs.set(key, [...(pairs.get(key) ?? []), match]);
    }
  }
  return <section className="setup-card competition-matrix-section"><h3>Head-to-head results</h3>
    <p className="muted">Read each score from the row entrant’s perspective. Each leg is shown separately. Playoff matches are not included.</p>
    <div className="competition-table-scroll" role="region" aria-label="Round-robin results" tabIndex={0}>
      <table className="competition-matrix"><caption>Round-robin head-to-head results</caption><thead><tr><th scope="col">Entrant</th>{competition.entrants.map(p => <th scope="col" key={p.id}>#{p.seed}<span>{p.name}</span></th>)}</tr></thead>
        <tbody>{competition.entrants.map(row => <tr key={row.id}><th scope="row">#{row.seed} {row.name}</th>{competition.entrants.map(column => <td key={column.id}>
          {row.id === column.id ? <span aria-label="Same entrant">—</span> : (pairs.get(JSON.stringify([row.id, column.id])) ?? []).map(match => {
            const seat = match.players.indexOf(row.id);
            const result = match.status !== 'done' ? match.status === 'playing' ? 'Live' : match.status === 'error' ? 'Needs attention' : 'Not played'
              : match.winner === row.id ? 'Win' : match.winner ? 'Loss' : 'Draw';
            const score = match.status === 'done' && match.score ? `${match.score[seat]}–${match.score[1 - seat]}` : null;
            const text = `${result}${score ? ` ${score}` : ''}`;
            const description = `${row.name} against ${column.name}, round ${match.round}: ${text}`;
            return match.status === 'done' && match.replayId
              ? <button type="button" key={match.id} className={`matrix-result matrix-${result.toLowerCase()}`} aria-label={`${description}. Watch replay`} onClick={() => onReplays(match.replayId!)}>{text}</button>
              : <span key={match.id} className={`matrix-result matrix-${result.toLowerCase().replaceAll(' ', '-')}`} aria-label={description}>{text}</span>;
          })}
        </td>)}</tr>)}</tbody>
      </table>
    </div>
  </section>;
}

export default function CompetitionHistory({initialId, onCreate, onWatch, onReplays}: {initialId?: string; onCreate: () => void; onWatch: (job: Job) => void; onReplays: (id?: string) => void}) {
  const [items, setItems] = useState<LocalCompetition[]>([]);
  const [selectedId, setSelectedId] = useState(initialId ?? '');
  const [selected, setSelected] = useState<LocalCompetition | null>(null);
  const [loadError, setLoadError] = useState('');
  const [actionError, setActionError] = useState('');
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [retry, setRetry] = useState(0);
  const requestEpoch = useRef(0);
  const viewEpoch = useRef(0);
  const controlPending = useRef(false);
  const mounted = useRef(false);
  useEffect(() => {mounted.current = true; return () => {mounted.current = false;};}, []);
  useEffect(() => {
    let active = true; let timer: ReturnType<typeof setTimeout>;
    setLoading(true);
    setLoadError('');
    setActionError('');
    async function poll() {
      const epoch = requestEpoch.current;
      try {
        const [history, detail] = await Promise.all([loadLocalCompetitions(), selectedId ? loadLocalCompetition(selectedId) : Promise.resolve(null)]);
        if (active && epoch === requestEpoch.current && !controlPending.current) { setItems(history); setSelected(detail); setLoadError(''); }
      } catch (reason) { if (active && epoch === requestEpoch.current) setLoadError(reason instanceof Error ? reason.message : 'Could not load competitions.'); }
      finally { if (active) {setLoading(false); timer = setTimeout(() => void poll(), 2000);} }
    }
    void poll(); return () => {active = false; viewEpoch.current += 1; clearTimeout(timer);};
  }, [selectedId, retry]);
  async function control(action: 'pause' | 'resume') {
    if (!selected || controlPending.current) return;
    controlPending.current = true;
    requestEpoch.current += 1;
    const view = viewEpoch.current;
    setBusy(true); setActionError('');
    try {
      const updated = await controlLocalCompetition(selected.id, action);
      if (view === viewEpoch.current) setSelected(updated);
    }
    catch (reason) {
      if (view === viewEpoch.current) setActionError(`${reason instanceof Error ? reason.message : 'Could not update competition.'} The change is not confirmed. Check the current status before retrying.`);
    }
    finally {
      requestEpoch.current += 1;
      controlPending.current = false;
      if (mounted.current) setBusy(false);
    }
  }
  async function watch(id: string) {
    const view = viewEpoch.current;
    try {const job = await loadJob(id); if (view === viewEpoch.current) onWatch(job);}
    catch (reason) {if (view === viewEpoch.current) setActionError(reason instanceof Error ? reason.message : 'Could not open the live match.');}
  }
  const people = new Map(selected?.entrants.map(p => [p.id, p]) ?? []);
  const completed = selected?.matches.filter(m => m.status === 'done').length ?? 0;
  const stages = [...new Set(selected?.matches.map(m => m.stage))];
  return <div className="competition-history">
    {loadError && <div className="inline-error" role="alert"><p>{loadError} {selected || items.length ? 'Previously loaded results remain visible.' : 'Saved competitions could not be loaded.'} Retrying automatically.</p><button type="button" disabled={busy} onClick={() => setRetry(value => value + 1)}>Retry loading</button></div>}
    {actionError && <p className="inline-error" role="alert">{actionError}</p>}
    {!selectedId ? <><section className="feature-heading"><div><span className="section-kicker">On this computer</span><h2>Competition history</h2><p>Brackets, standings and results remain here after your agents finish playing.</p></div><button type="button" className="primary-action" onClick={onCreate}>New competition</button></section>
      {loading && <p role="status">Loading your competitions…</p>}
      {!loading && !loadError && !items.length && <section className="setup-card competition-empty"><h3>Let your agents compete.</h3><p>Run a bracket, a round robin or a Swiss event, then follow the results here.</p><button type="button" onClick={onCreate}>Create your first competition</button></section>}
      <div className="competition-history-grid">{items.map(c => {
        const winner = c.entrants.find(p => p.id === c.champion);
        const encounters = c.matches.filter(m => m.status === 'done').length;
        const matchesPlayed = c.matches.reduce((count, match) => count + (
          match.seriesGames?.length ?? (match.status === 'done' ? 1 : 0)
        ), 0);
        return <button type="button" className="competition-history-card" key={c.id} onClick={() => {setSelected(null); setSelectedId(c.id);}}><small>{label(c.type)} · {new Date(c.createdAt).toLocaleDateString()}</small><h3>{c.name}</h3><span className={`competition-status ${c.status}`}>{c.status}</span><p>{c.entrants.length} entrants · {encounters} encounter{encounters === 1 ? '' : 's'} completed · {matchesPlayed} match{matchesPlayed === 1 ? '' : 'es'} played</p><strong>{winner ? `Winner · ${winner.name}` : c.error ? 'Needs attention' : 'Open competition →'}</strong></button>;
      })}</div></> : <>
      <button type="button" onClick={() => {setSelectedId(''); setSelected(null);}}>← All competitions</button>
      {!selected && <p role="status">{loadError ? 'Competition details are unavailable. You can retry or return to your history.' : 'Loading competition…'}</p>}
      {selected && <><section className="setup-card competition-summary"><div><span className="section-kicker">{label(selected.type)} · {selected.format === 'commander' ? 'Commander · four-player games' : 'Legacy · best-of-three games'}</span><h2>{selected.name}</h2><span className={`competition-status ${selected.status}`}>{selected.status}</span><p>{selected.entrants.length} entrants · {completed} encounters completed · seed {selected.playSettings.firstSeed}</p>{(selected.parameters.playoffWins ?? 1) > 1 && <p>Playoff series: first to {selected.parameters.playoffWins} match wins. Each match is recorded separately.</p>}{selected.parameters.reseed && <p>Playoff seeds are preserved; surviving entrants are reseeded each round.</p>}{selected.champion && <h3 className="competition-champion">Winner · {people.get(selected.champion)?.name}</h3>}</div><div className="competition-actions">
        {selected.activeJobId && <button type="button" className="primary-action" onClick={() => void watch(selected.activeJobId!)}>{selected.matches.find(m => m.status === 'playing')?.players.some(p => p && people.get(p)?.kind === 'human') ? 'Play current match' : 'Watch current match'}</button>}
        {selected.status === 'running' && <AsyncActionButton loading={busy} loadingLabel="Pausing…" onClick={() => void control('pause')}>Pause after this match</AsyncActionButton>}
        {selected.status === 'pausing' && <span>Finishing the current match before pausing…</span>}
        {selected.status === 'paused' && <AsyncActionButton loading={busy} loadingLabel="Resuming…" onClick={() => void control('resume')}>Resume competition</AsyncActionButton>}
        <button type="button" onClick={() => onReplays()}>Open saved replays</button></div></section>
        {selected.error && <p role="alert" className="inline-error">Competition paused: {selected.error} Completed matches are preserved. Resume retries only an unfinished match.</p>}
        {selected.type.includes('round_robin') && <RoundRobinMatrix competition={selected} onReplays={onReplays} />}
        {selected.groupStandings?.map(group => <section className="setup-card" key={group.id}><h3>{group.name} standings</h3><Standings rows={group.standings} /></section>)}
        {['round_robin_cut', 'groups_cut', 'random_season_cut'].includes(selected.type) && !!selected.qualifyingStandings?.length && <section className="setup-card"><h3>Qualification standings · playoff seeds</h3><Standings rows={selected.qualifyingStandings} /></section>}
        <section className="setup-card"><h3>{selected.type.includes('round_robin') || selected.type === 'swiss' ? 'Standings' : 'Entrant statistics'}</h3><Standings rows={selected.standings ?? []} /><details><summary>How are standings calculated?</summary><p>Match win: 3 points; draw: 1; loss: 0. Byes award 3 points and count as 2–0 for percentage tiebreakers, but are not played matches. Ties: opponents’ match percentage, game percentage, opponents’ game percentage, then initial seed. Percentages use a 33% floor. Unknown game scores are not invented.</p><p>Recreational Swiss pairing minimizes repeat opponents first, then score differences. This is not an EventLink-certified pairing system. Elimination champions are determined by the bracket, not by accumulated points.</p></details></section>
        {stages.map(stage => {const matches = selected.matches.filter(m => m.stage === stage); const rounds = [...new Set(matches.map(m => m.round))]; const bracket = ['winners', 'losers', 'final'].includes(stage); return <section className="setup-card competition-stage" key={stage}><h3>{stageLabel[stage] ?? stage}</h3><div className={`competition-rounds ${bracket ? 'is-bracket' : ''}`}>{rounds.map(round => <div className="competition-round" key={round}><h4>{stage === 'final' && round === 2 ? 'Final reset · if needed' : `Round ${round}`}</h4><div className="competition-round-matches">{matches.filter(m => m.round === round).map(m => <article key={m.id} className={`competition-match ${m.status}`}>
          <header><small>{m.group ? `${selected.groupStandings?.find(g => g.id === m.group)?.name ?? m.group} · ` : ''}{m.id}{m.rematch ? ' · repeat opponent' : ''}</small><span>{m.status==='dispatching'?'Preparing table…':m.status}</span></header>
          {m.status==='dispatching' && <p role="status">Preparing decks and Oracle rules. The first launch can take longer; this match is already queued.</p>}
          {m.seriesWins && <small>Series match wins · first to {selected.parameters.playoffWins}</small>}
          {(m.players.length ? m.players : [null, null]).map((p, i) => <div key={i} className={p && p === m.winner ? 'match-winner' : ''}><span>{p ? <><small>#{bracket && selected.playoffSeeds?.includes(p) ? selected.playoffSeeds.indexOf(p) + 1 : people.get(p)?.seed}</small> {people.get(p)?.name}{m.deckNames?.[i] && <small className="match-deck-name">{m.deckNames[i]}</small>}</> : m.status === 'pending' || m.status === 'skipped' ? 'To be determined' : 'Bye'}</span><strong>{m.seriesWins?.[i] ?? m.score?.[i] ?? (p && p === m.winner ? '✓' : '–')}</strong></div>)}
          {!!m.seriesGames?.length && <details><summary>Series matches & replays</summary>{m.seriesGames.map((game, i) => <p key={i}>Match {i + 1}: {people.get(game.winner)?.name}{game.score ? ` · ${game.score.join('–')}` : ''}{game.replayId && <button type="button" onClick={() => onReplays(game.replayId!)}>Replay match {i + 1}</button>}</p>)}</details>}
          {!!m.attempts?.some(attempt=>attempt.error && attempt.replayId) && <details><summary>Interrupted attempts & recordings</summary>{m.attempts.map((attempt,index)=>attempt.error && attempt.replayId ? <p key={index}>Attempt {index+1}: {attempt.error} <button type="button" onClick={()=>onReplays(attempt.replayId!)}>Replay interrupted attempt {index+1}</button></p> : null)}</details>}
          {m.status === 'playing' && m.jobId && <button type="button" onClick={() => void watch(m.jobId!)}>{m.players.some(p=>p && people.get(p)?.kind==='human')?'Play match':'Watch live'}</button>}{m.replayId && <button type="button" onClick={() => onReplays(m.replayId!)}>Watch replay</button>}{m.status === 'skipped' && <small>Not needed: the undefeated finalist won.</small>}
        </article>)}</div></div>)}</div></section>;})}
      </>}
    </>}
  </div>;
}
