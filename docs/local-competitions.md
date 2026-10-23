# Local competitions

In **Play → Organize a competition**, add local human entrants with a deck, or
agents with a specific/random mastered deck, choose a format and start. The
**Competitions** tab keeps history, live match access, standings, bracket views
and individual replay links. Initial seeding may follow registration order or
shuffle from a fresh seed; supplying a seed reproduces that initial draw.

Round-robin events also show a head-to-head matrix: each row uses that entrant's
score perspective, each leg remains separate, and finished games link to their
saved replay. Byes and playoff results are excluded from this qualification grid.

Legacy uses two-player best-of-three matches and supports every listed event
type. Commander uses four-player single-game tables and supports elimination,
groups and random seasons (with optional playoffs). Random seasons accept any
field of at least four entrants; elimination fields and playoff cuts must be
powers of four. Groups still need a multiple of four entrants. Each Commander
group contains exactly four entrants. Incompatible formats/counts are rejected.
Any entrant may be human or AI; no installed agent is required for an all-human
event. Human tables use local shared-screen handovers, not online invitations.

## Formats

- Single elimination: seeded power-of-two bracket, byes for smaller fields.
- Double elimination: upper/lower brackets, two losses to eliminate; grand final
  resets if the lower-bracket finalist beats the undefeated finalist.
- Round robin: every pair meets once per leg, configurable 1–10 legs.
- Round robin → playoffs: configurable qualifier count; standings determine
  playoff seeds. Non-power-of-two cuts receive bracket byes.
- Recreational Swiss: configurable number of rounds, no elimination. Minimum
  weight blossom matching minimizes repeats before score gaps. Repeats are
  marked in the UI when necessary. This is **not** certified EventLink pairing.
- N-loss elimination: adaptive rounds until one entrant remains below the
  configured loss threshold. This is separate from traditional double elimination.
- Groups → playoffs: group round robins qualify a configurable number per group.
- Random season: configurable rounds, with an optional seeded playoff cut.

### Commander season quotas and byes

New Commander seasons configure **matches per player**, not physical rounds.
With N entrants and M real matches each, equal quotas are feasible exactly when
N × M is divisible by four, assuming repeat opponents are allowed. Thus M must
be a multiple of 4 / gcd(N, 4): five entrants can each play four times in five
distinct four-player tables; each entrant is absent from exactly one table.
That physical rest is not a bye and awards no points. The scheduler reduces
repeated opponents where possible, without promising unique opponents generally.

Optional season byes allow other quotas. R = (N × M) mod 4 seeded entrants
receive one awarded bye instead of a real match; everyone has M match-or-bye
opportunities and real match counts differ by at most one. A bye awards
**3 standings points**, but no real win, played match/game, replay or statistical
victory. Byes count only in percentage tiebreaker credits (one game in Commander,
two in Legacy); actual game-win counters remain unchanged. This is an explicit
recreational scheduling choice, not a claim about official Commander policy.

New events store `seasonScheduleVersion = commander-per-player/v1` and their
per-player quota. Existing saved events without that marker retain their old
round-based schedule; reading history does not rewrite or replay them.

Playoffs can require several match wins to advance: four wins means at most seven
matches, not seven individual Legacy games. Optional reseeding pairs the best
surviving Legacy seed with the lowest, or balances surviving Commander seeds across
four-player tables. The higher-seed opening advantage applies to the first match
of a series, then rotates. These are recreational scheduling rules.

Match points: 3/1/0. Tiebreakers: opponents' match percentage, game percentage,
opponents' game percentage, then initial seed. Percentage floors and bye treatment
follow [Magic Tournament Rules, §3.1 and Appendix C](https://media.wizards.com/ContentResources/WPN/MTG_MTR_2026_Feb27_EN.pdf).
Unknown game scores are not fabricated. The bracket, not cumulative points,
determines an elimination champion. Standings display played W/L/D separately
from byes. Knockout draws require an explicit retry, not a randomly chosen winner.

## Execution and persistence

New local setups offer AI pacing of 1, 10 or 100 milliseconds (default 1ms),
turn limits of 20, 30, 40, 60 or 100 (default 60), and timed-match limits of
1, 3 or 10 minutes (AI-only default 10 minutes). Human tables retain the
explicit relaxed/no-clock option. Previously saved values remain readable and
accepted; existing competitions and replay metadata are not silently migrated.

Millisecond pacing is a minimum duration per local decision, including policy
computation, not a tiny cancellation deadline or a response-time guarantee.
The SDK uses its existing realtime transport category for these local choices;
recordings distinguish requested pacing, transport speed and actual Engine
decision timeout. League time controls and the SDK public speed enum are unchanged.

Season-round and playoff-qualifier fields support clearing, typing and native
number-input arrows. Blank or out-of-range values cannot create an event.
Start competition remains clickable to explain an invalid setup in a focused
error message; invalid inputs never reach creation. If the initial Engine
status is pending or unhealthy, a fresh read confirms the configured Engine
before creating an event, instead of silently disabling the button.
`DEEPDECK_QA_LOCAL_SETTINGS=1 python scripts/verify_player_app.py` checks the
presets, actual keyboard editing/ArrowUp and desktop/mobile layout without
creating any match or competition.

The controller's `CompetitionService` dispatches one normal `playtest.batch`
child job for an AI-only match or `playtest.table` when a human is present, using
the same SDK agents, Engine, statistics and replay
storage as ordinary local batches. Each competition progresses sequentially.
The competition and bracket state live in the ignored `.deepdeck/learner.db`
`local_competitions` table; no League deployment or cloud inference is involved.
Replay files remain subject to the existing local replay quota/favorite rules.
Evicting a replay does not erase competition results.

Human matches default to no inactivity or overall match clock. Explicitly chosen
limits survive entrant changes; AI-only matches always have a safety limit. The
first launch can spend time preparing decks/Oracle rules; history labels this as
preparation rather than offering a duplicate launch. **Play current match** opens
the next human table. Each player explicitly reveals their own hand after passing
the screen. Multiple human opponents are never silently converted to AIs.

Keep the app running to schedule subsequent matches. Pause finishes the current
match but dispatches no next match. Engine errors, timeouts and stopped children
pause the organizer without recording a false loss. Resume reconnects a known
child or retries only the unfinished match. An application restart preserves
results and requires explicit resume; it never silently relaunches a tournament.
Dispatch intents and child identifiers are persisted so resume can recover a
child created immediately before an app crash. Seeds vary per match and retry.
The controller also enforces a separate wall-clock deadline (match limit plus
60 seconds shutdown grace), so a policy blocking its own event loop cannot leave
the organizer waiting indefinitely.

Interrupted attempts keep their replay links under **Interrupted attempts &
recordings**, including after a retry succeeds. The partial attempt is not a
played match or a forfeit and does not add a win/loss/draw to local statistics.

Each human registration has a deterministic, competition-scoped local player ID.
Names and identifiers travel with the entrant when seat order changes, so results
do not merge into the first human chair. Same-named entrants and separate events
remain distinct; this is not a global identity or a League account link. Existing
competition JSON without the optional identity field derives the same ID at
dispatch. Historical statistics are preserved, not rewritten or retroactively
merged; ordinary non-competition table identities retain their prior behavior.

Entrant agent identities and deck choices stay fixed. A random deck is selected
only from that agent's mastered pool. Policies are validated again when each
child starts; versions may change between matches if the user edits an idle
agent, so initial agent revisions are retained as provenance rather than claiming
an immutable strategy snapshot.

## Verification

`tests/test_local_competitions.py` covers all formats, odd fields, byes,
double-final reset, RR-to-playoff seeding, draw handling, pause/error/restart,
protected HTTP routes and the real child-job dispatch contract.
`LocalCompetitions.test.tsx` covers format controls, entrant/deck restrictions,
history, read-only live viewing and pause/resume actions.
`tests/test_competition_humans.py` covers human rounds in Legacy/Commander, stable
identity after serialization and seat reversal, actual statistics storage,
invalid/duplicate identities, older event compatibility, interruption and resume.

`python scripts/verify_human_competition.py` creates a fresh source QA profile and
uses an already healthy loopback Engine. Edge exercises named handovers from
history and an intentional stop/replay. An automated driver then uses the real
human-action endpoint to finish two Legacy round-robin legs after resume. The
driver uses the SDK graph for priority choices and explicit human card-name/combat
interaction adapters; it is not a model-strength benchmark or SDK transport test.
An observed run completed five real games in two matches (2–1 and 2–0, life-total
outcomes), retained the partial attempt and verified four seat results for two
stable local identities. Legacy/Commander unit coverage is broader than this
specific live event; a full live tournament-format/field-size matrix remains open.

`python scripts/verify_competition_history.py <owned-verification.json>` inspects
that successful QA profile without launching another match. Its browser guard
permits only reads and replay-viewing leases. Desktop/mobile history, head-to-head
matrix, interrupted/full replay links, last frame and statistics-versus-standings
checks passed. Reports/screenshots live in ignored
`.deepdeck/validation/human-competition-*/`. No user profile, paid LLM, League
deployment or production database is modified by these checks. Local Engine legacy
analytics recording is disabled; statistics stay in the isolated profile.

Optional browser check: `DEEPDECK_QA_COMPETITIONS=1` with
`scripts/verify_player_app.py` verifies an existing real competition and the
10-agent builder on desktop/mobile without launching another event.

`python scripts/verify_mixed_commander.py` separately verifies a real four-seat
table with two simulated human participants and two registered SDK agents, using
public Yenna/Tatyova/Alexios/Yenna decks. Edge checks named handovers, the actual
Pixi scene's hand privacy and interrupted replay playback. The automated driver
answers only human IDs; SDK workers control the AI seats. A completed seed-194052
run ended at turn 32 with Alexios winning, 175 replay frames, both agent replay
copies and four local seat results. Agent handler timing was recorded; Engine
decision acknowledgements were unavailable, so zero fallback usage is not proven.
The report is under `.deepdeck/validation/mixed-commander-25x84_an/` in the observed
workspace. This is one mixed table, not a completed mixed tournament matrix or a
manual test of every possible human choice. Earlier failed profiles are retained
as failures; they exposed the redundant-equipment loop documented in the strategy
graph changelog.

`python scripts/verify_competition_history.py <owned-mixed-report.json>
--mixed-commander` reopens that completed profile with the current built frontend.
It checks all four fixed-seat perspectives and the global view in actual Pixi,
keeps libraries hidden, reaches the last frame and checks desktop/mobile header
bounds. Only replay-viewing leases are allowed as browser mutations; local
statistics stay unchanged and no match is launched. The separate
`replay-view-verification.json` defaults to failure and becomes successful only
after the browser assertions and clean source-controller shutdown.

`python scripts/verify_grouped_commander.py` exercises eight real SDK agents in
two Commander groups, two qualifiers per group and a first-to-two playoff series.
It checks qualification, opening-seat rotation, each recording and local match
statistics only if the whole event completes. The initial observed run did not
pass: one group completed, but Yenna/Tatyova/Yenna/Tatyova failed at turn 52 with
an activated-source-not-on-battlefield Engine error. The organizer paused without
inventing a winner, retained a 278-frame failed replay and stored only the first
completed table's four results. That initial failure did not validate the full
live grouped-event flow; the correction and verified recovery are described below. The failed profile
is retained under `.deepdeck/validation/grouped-commander-mofrkj52/`.

Subsequent inspection identified `flags.phasedOut: true` on Yenna in the final
pre-action battlefield snapshot: Haystack had phased her out, not moved her to
another zone. The Engine's source lookup rejected phased-out objects correctly,
but the ordinary activated-action generator still advertised that ability.
The source correction in `external/deepdeck-engine` passes the two focused
normal/any-player/equipment regressions, including phase-in return, and all 727
library unit tests. The corrected local Engine has been rebuilt and restarted.
The original failed run is not relabeled as passed. This does not establish
complete phasing coverage or, by itself, a completed group event.

`python scripts/verify_grouped_commander.py --resume-report <owned-failed-report>`
reopens that owned profile, explicitly resumes its paused event and checks that
completed encounters retain their winner and replay. A separate timestamped
resume report preserves the original failure evidence. Cleanup only uses sessions
from newly created jobs; historical session numbers may be reused after an Engine
restart and must not authorize deleting a new table.

The observed recovery completed with eight SDK agents, two qualifying groups and
a two-match final: four recorded matches, sixteen seat results, Alexios champion.
The first group was preserved (turn 23); the retried group ended at turn 44, and
the final matches at turns 24 and 25. Final opening seats rotated from 1 to 2.
Reports: `resume-verification-1792481260465425700.json` and
`group-history-verification.json` inside the same owned profile folder. Actual
desktop/mobile history, qualification tables, series replay links and playback
to the final frame passed; viewing left statistics unchanged. The retry uses a
fresh seed, not an identical-seed strategy-strength comparison.

Recordings now have a separate structured worker acknowledgement, even when the
Engine subsequently fails. It updates the recording link, never the completed
count or winner. If every write fails (including a quota filled by favorites),
the worker reports no recording instead of claiming one was saved.

Older missing attempt links are recovered on read only from a matching owned
single-match batch manifest and recording header: batch, competition, match and
recording identifiers must agree. Paths are confined to the local profile,
reads are bounded and frames are not expanded to find the link. Missing, unsafe
or unrecognized records remain unresolved; no outcome is inferred from a file
name or exception message. Favorites and human-only collections are included.
This view hydration does not rewrite stored competition JSON.

`verify_competition_history.py <owned-resume-report> --grouped-commander
--failed-attempt` opened the actual old 278-frame Engine-error recording from
history and reached its last frame, then checked the completed series replay.
Statistics and the stored competition payload stayed unchanged. The user app's
five historical error-attempt links also resolve to files present on disk.
Regression coverage: `tests/test_competition_recordings.py` and the local batch
failure/quota tests. Broader live format/field-size and manual human-choice
coverage remain open.

History cards distinguish completed bracket encounters from individual matches
played: a three-match playoff series adds three played matches, including its
already completed matches while the encounter is still in progress. Byes and
skipped rounds are not played matches.
