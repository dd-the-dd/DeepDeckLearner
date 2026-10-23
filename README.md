# DeepDeckLearner

DeepDeckLearner is a local player laboratory for Deep Deck League. Play Magic,
build decks, personalize programmatic agents, send them into the League, review
replays, and compare local results from one application. Neural training tools
remain available through the advanced APIs and command-line interfaces. No
private model weights are included.

DeepDeckEngine remains authoritative for Magic rules and legal actions. The
workbench consumes versioned sources from
[`DeepDeckEngine`](https://github.com/dd-the-dd/DeepDeckEngine),
[`DeepDeckPixi`](https://github.com/dd-the-dd/DeepDeckPixi), and
[`DeepDeckAgent`](https://github.com/dd-the-dd/DeepDeckAgent). Engine and Pixi
follow their own `origin/main`; the Agent SDK remains pinned. Learner does not
copy their code.

The default agents use weighted decision graphs, not neural-model training.
Personalize strategic priorities, card/tag patterns and decision noise, then
playtest through the Python Agent SDK. An optional LLM helps edit the strategy
outside matches; normal play does not need PyTorch, CUDA or model weights.

## Quick start for a Magic player

### Windows portable preview

Use the Windows ZIP attached to the selected [Player release](https://github.com/dd-the-dd/DeepDeckLearner/releases).
When provided, extract the **entire** archive and open
`DeepDeck.exe`. It includes the Python runtime, frontend, Agent SDK worker and
compiled Engine; players do not need Git, Node.js, Rust or Python. The native
window requires Microsoft Edge WebView2 Runtime. This preview is unsigned;
there is no signed installer or automatic updater. GitHub's generated source
ZIP is not the portable player application.

Private decks, agents, replay storage and settings live separately under
`%LOCALAPPDATA%/DeepDeck/PlayerLab/.deepdeck/`, not beside the executables.
Replacing the extracted application folder does not replace those files. A
profile lock prevents two desktop windows editing the same data. Closing the
window warns about unsaved agent drafts and active jobs, stops owned jobs and
finalizes responsive replay recorders; keep the app open during League play.
An already-running shared Engine is never stopped by the launcher.

### From source

These instructions target the **0.3.1 Player application**, with Play, Decks and
Agents as its starting point. The earlier `v0.2.0` release opens the historical
training interface and is not the app described here. The source check below
stops before installation if an older revision was cloned.

Requirements: Git, Python 3.10+, Node.js 22.13+, and a Rust toolchain for the
first local Engine build. PowerShell is optional. On Windows, install the Visual
C++ Build Tools when the bundled Rust linker cannot use an installed Windows
SDK. On macOS, install the Xcode command-line tools. On Linux, install your
distribution's C/C++ build tools and OpenSSL development package.

### Windows

```powershell
git clone --branch main --recurse-submodules https://github.com/dd-the-dd/DeepDeckLearner.git
cd DeepDeckLearner
py -c "from pathlib import Path; assert './PlayerApp' in Path('apps/learner-web/src/main.tsx').read_text(encoding='utf-8'), 'This checkout opens the old interface; the Player sources must be published first.'"
py scripts/workbench.py setup
py scripts/workbench.py start
```

The PowerShell convenience wrapper remains available as
`pwsh ./scripts/setup.ps1`.

### macOS and Linux

```sh
git clone --branch main --recurse-submodules https://github.com/dd-the-dd/DeepDeckLearner.git
cd DeepDeckLearner
python3 -c "from pathlib import Path; assert './PlayerApp' in Path('apps/learner-web/src/main.tsx').read_text(encoding='utf-8'), 'This checkout opens the old interface; the Player sources must be published first.'"
python3 scripts/workbench.py setup
python3 scripts/workbench.py start
```

The POSIX setup shortcut is also available as `sh scripts/setup.sh`.

Source setup installs the programmatic player dependencies by default, without
PyTorch or Python development tools. Add `--deep-learning` for neural training
and `--dev` for tests/linting, for example
`py scripts/workbench.py setup --deep-learning --dev`. The same options are
accepted by `update`; updating without them does not uninstall existing extras.
The wrappers accept `-DeepLearning -Dev` (PowerShell) or
`--deep-learning --dev` (shell). No existing environments or training data are
removed automatically.

Source setup builds the frontend before installing Learner. Manual editable or
wheel builds must follow that order too. Packaging verifies the Player build's
`deepdeck-web.json` source hashes and excludes retained chunks from older builds.
For an isolated frontend build, set `DEEPDECK_BUILD_WEB_DIST` to its output folder
when building the wheel or source archive; the running app's assets stay intact.
CI installs the wheel in a clean environment and runs
`scripts/verify_player_installation.py` without PyTorch before accepting it.

It opens `http://127.0.0.1:8765` with seven player-facing pages:

- **Play:** choose Legacy or Commander, the player or agent in each seat, and
  each deck. Human seats can browse local and League decks. AI seats are limited
  to the agent's deck pool, including a Random option. AI batches run a chosen
  number of matches. The current Engine uses best-of-three Legacy matches and
  single-game Commander tables.
- **Decks:** create local lists, edit quantities and zones, import League lists,
  and open an optional deck-building assistant.
- **Agents:** create and tune deck specialists using strategic weights,
  archetype mixes, card/tag patterns, and adjustable decision noise.
- **League:** deploy an agent with its eligible published decks and bring it
  home when finished. Agent keys are configured in Settings.
- **Competitions:** organize local AI tournaments and follow their saved
  brackets, standings, live matches and replays.
- **Replays:** browse League or local games, watch the Pixi replay, and keep
  selected games permanently. League is the default library view.
- **Insights:** compare actual local agent, deck, and player results, filter by
  period, and prepare a 20-match experiment.

Insights distinguishes loading, unavailable counts and a successfully loaded
empty view. Refresh failures keep the last loaded comparison for the same
period. Changing period hides mismatched numbers and ignores late responses.
Use **Refresh results** or **Retry results** without launching new matches;
background reads wait 30 seconds after completion rather than overlapping.

On small screens, all seven pages stay in one bottom navigation row;
**Competitions** is shortened to **Events**. Icon-only tablet navigation retains
full accessible names and hover titles. The current page is announced and
keyboard focus remains visible.

During an app session, interactive tables and AI batches retain independent
formats, seats and playtest settings. Visiting the competition builder does not
replace either setup. The first batch starts from the current table's seat
choices, converts human seats to AI seats, and uses its own default safety limit.
Returning to the table restores its human seat and timers. Decks and agents are
revalidated against the current library, not trusted just because they were
previously selected. Preparations survive tab changes, table launches, returns
from games and failed launches. Insights can explicitly set up a 20-match batch;
**Playtest this agent** updates the table's pilot and format without replacing
the saved batch. Nothing is relaunched on return. These drafts are held only in
memory and are not restored after reloading or restarting the app.

Random seats show a collapsible preview of playable agents and their matching
mastered decks. Counts intersect format, entity tags/collections, mastery and
available local snapshots; shared deck versions count once. Empty random pools
block launch and never fall back to unrelated choices. A missing saved filter
stays visible and can be cleared explicitly. This preview does not reserve or
draw anything: the local controller checks and freezes the actual candidates at
launch, then performs the per-match draws. Human random seats use matching local
decks without requiring an agent to master them.

Expand **Playtest settings** to choose the opening seat, an optional shuffle
seed, and the per-game turn limit. Human tables have no inactivity or overall
match timer by default; optional safety limits stop the table and save an
unfinished trace, not a forfeit result. AI-only matches require a bounded safety
limit (10 minutes by default). In Legacy, the opening-seat choice applies to the
first game; later games follow the Engine's match rules. These settings are not
League clocks. Local replays record the actual seed, opening seat, limits and
saved agent versions under **Match setup & limits**. Matching settings help
compare experiments but do not guarantee identical results across Engine or
agent versions, decision noise, and asynchronous execution.

Each seat in **Play a game** independently accepts a human or an AI. Local shared-
screen tables support two humans in Legacy or up to four humans in Commander,
including tables without any AI installed. This is pass-and-play on one computer,
not an online multiplayer invitation: the next human decision replaces the board
with a handover screen, and that player explicitly reveals their hand. **Hide hand /
pass screen** in the bottom controls hides it again. Only the acknowledged player's
private hand/sideboard and explicit card reveals are supplied to the renderer;
opponent hands and libraries stay concealed. This is presentation privacy, not an
authentication boundary against someone inspecting the local Engine/API or files.
Connection/action failures conceal the board, show a safe error and require a new
ready gesture after a successful refresh. No failed action is automatically retried.

Human-only games are recorded under `.deepdeck/local-tables/`, not a fabricated
agent directory. Replays includes this collection even with an empty agent library;
the same global disk quota, viewing leases and favorite protection apply. Agent
tables and AI batches keep their usual recording locations. First-time Oracle
preparation can take longer with a cold cache; a shared deck is prepared once per
table. AI batch mode still requires AI seats only. Competitions also accept multiple
local human entrants, using the same shared-screen handovers.

Source verification: `python scripts/verify_hotseat.py` uses an already running
loopback Engine and a new isolated data profile. It downloads public decks and
checks real Pixi two/four-human opening-hand handovers, manual concealment and
interrupted-replay playback; no LLM call or League deployment is made. These checks
do not prove completed human matches or a complete tournament matrix. QA reports
are retained in ignored `.deepdeck/validation/hotseat-*/` directories.

Local competitions support single and double elimination, round robin,
round-robin qualification into seeded playoffs, Swiss rounds and elimination
after a configurable loss count, as well as groups into playoffs and random
seasons with an optional playoff cut. Matches run sequentially; keep the app open.
Game format is selected explicitly. Legacy uses duels; Commander uses real
four-player tables and currently supports single elimination, four-player groups,
and random seasons. Commander season fields must be multiples of four; elimination
fields/cuts must be powers of four (4, 16, 64, ...). Commander groups contain four
entrants each and must produce a power-of-four cut. Incompatible formats and
counts are rejected before launch, rather than silently switching to duels.

Each entrant is a player or saved AI; the same Player/AI control is used in Play.
Player decks use the shared local/League picker. AI decks remain restricted to
mastered decks, with a fresh random selection per match when requested. Human
matches use the interactive table opened from **Play current match** in history;
they default to relaxed, untimed play unless you explicitly choose a match limit.
AI-only matches always retain a safety limit. Interrupted human matches pause
the organizer without assigning a forfeit, and **Interrupted attempts & recordings**
keeps their replay links after resuming. Human registration identifiers remain
stable across rounds, seat rotation and retries; these are local competition
identities, not League accounts or globally linked player profiles.

Tooltips explain competition and match parameters. Playoff series have a match-win
target (default one): four wins gives a Legacy best-of-seven series of matches,
each still best of three Magic games. Series stop immediately at the target and
retain every completed match/replay, including across pause/restart. Reseeding
pairs the best surviving Legacy seed with the worst each round; Commander spreads
survivors across seeded four-player tables. Double elimination retains its fixed
winners/losers paths. Optional opening advantage lets the higher seed start the
first match and rotates opening seats in subsequent series matches; normal Legacy
rules still determine the starter of later games within each match.

Group qualification ranks finishers within each group before comparing match
percentages/tiebreakers across groups. Random-season pairings do not use points:
Legacy minimizes repeats and balances byes; Commander uses a deterministic seeded
heuristic to reduce repeated tablemates. These are recreational schedules, not
certified tournament rules. Qualification seeds remain fixed throughout playoffs.

Saved history includes standings, brackets, live viewing and replay links.
Pause/resume acknowledgements cannot be replaced by older in-flight reads.
A failed history read is labelled unavailable rather than empty; previously
loaded results remain visible, with automatic and manual retries. Control errors
remain visible across background refreshes and never claim an unconfirmed change.

Press **Start game engine** in Play or the competition builder when Engine is offline. The first source
build can take several minutes. Windows builds discover installed Visual C++
tools and the Windows SDK without requiring a Developer Command Prompt. Source
synchronization follows each dependency's explicit `main` branch and refuses
to overwrite local changes. The Agent SDK remains pinned by this repository.

The browser UI cannot launch arbitrary commands. A loopback-only Python
controller validates a small allowlist of training and playtest jobs. It never
sends your API key to React.

## Programmatic Agent Studio

The agent workshop builds deck-specific agents without neural training. A shared
weighted strategy graph ranks Engine-provided legal actions using reusable
signals for mulligans, mana, board development, interaction, combat, clocks,
combo progress, and protection. Each agent lives under ignored
`.deepdeck/agents/<id>/` with `agent.json`, an editable `agent.py`, and policy
and Python revisions. Policy edits preserve custom Python. Local play and League
use the same DeepDeck Agent SDK runtime.

In **Agents**, select a specialist from your roster, attach local or imported
decks, and adjust its posture without replacing its archetype plan. Card/tag
rules have a no-code editor; detailed weights and decision variety are under
the expandable strategy settings. **Save strategy** activates a new version.
**Saved versions** restores an older policy as a new version while keeping the
current Python source. Unsaved drafts are protected when navigating away;
concurrent edits require reloading rather than silently overwriting a newer
version. The copilot uses the same revision and active-agent edit checks as
manual saves, including every AI seat in a running or queued table/batch.

From a saved deck, **Create an agent for this deck** opens a one-shot creation
draft with its name, format and mastered-deck choice prepared. Unsaved deck
details, notes/tags and version conflicts must be resolved first. The shortcut
does not create files, publish a deck or launch a game. Confirm **Create agent**
after choosing the strategy; missing or mismatched decks block creation.
The prepared agent draft has the normal navigation/close protection and can be
cancelled without reopening itself on later visits.

**Playtest this agent** and **Prepare for League** carry the selected saved
agent into the corresponding setup. Local human/AI tables and AI
batches record replays. Closing an in-progress table normally gives the
runner time to save a partial trace marked **interrupted** before stopping;
timeouts/disconnections preserve the available trace too. Unfinished games do
not add wins, losses or draws to Insights. Forced termination or a full disk
can still prevent finalization. Older programmatic playtest requests now use
the same recorded table runtime; advanced neural playtests remain separate.

Starting a table opens a preparation screen that observes that exact job and
enters the game as soon as its session is ready. Reconnecting never submits a
second match. You can cancel or keep preparation in the background and reopen
it from Play. **Back to lab** waits for the controller to finalize the available
trace before closing its Engine session. A failed close stays visible and can
be retried.

Everything works manually. The assistant connection menu offers ChatGPT plan
sign-in, OpenAI API, Claude/Anthropic, Gemini, Mistral, DeepSeek, Grok/xAI, Groq,
OpenRouter, local Ollama and LM Studio, or a custom compatible gateway. Model
discovery lists the provider's IDs without generating text. Choose a model with
function calling; compatibility and access remain provider/model dependent.

Eligible ChatGPT users can select **ChatGPT subscription**, then **Continue
with ChatGPT**, authorize plan usage in their system browser, choose an
account-specific model and save. This uses the official public-client OAuth
flow and streaming Responses API with `store: false`; it never reads Codex's
credentials or silently falls back to paid API access. Plan limits remain shared
with the account's other usage. Manage app access in
[ChatGPT settings](https://chatgpt.com/settings/usage). Browser consent and a
successful live request must still be checked by the account owner; automated
tests use local signed-token and streaming SDK fixtures, not paid models.

Provider notes link to official documentation (reviewed October 18, 2026).
[Anthropic requires approval for third-party Claude subscription sign-in](https://code.claude.com/docs/en/agent-sdk/overview),
so its preset uses an API key. [Mistral's current plans share included API,
Studio and Vibe usage](https://docs.mistral.ai/admin/billing-usage/subscriptions).
Google API free tiers and eligible subscription/developer credits depend on the
account and billing project; no unlimited subscription allowance is assumed.

Chat runs through the official OpenAI Agents SDK with tracing disabled. Its
validated local tools inspect and edit decks and policies, search the catalog,
and import decks. Playing a programmatic agent does not call an LLM. Credentials
stay in ignored `.deepdeck/llm-api-credentials.bin` and
`.deepdeck/chatgpt-credentials.bin`, protected with Windows DPAPI on Windows and
owner-only files elsewhere, and are never returned to the browser. API keys are
isolated by exact endpoint. Legacy LLM keys migrate out of `secrets.json` when
settings are next saved; the League key is preserved. A `DEEPDECK_LLM_API_KEY`
environment override is bound to the first configured endpoint; explicitly set
`DEEPDECK_LLM_BASE_URL` to bind it to a different endpoint.

Chat tools run away from the UI's event loop; local chat mutations for one
profile are serialized to protect their read/modify/write sequences. If the
model disconnects after tools have saved work, the assistant returns the real
completed actions instead of implying the whole request failed. A started table
can still be opened without repeating the chat request. Closing the copilot
while it works does not unexpectedly open a game, and opening one warns before
discarding an unsaved agent draft.

The global copilot has the same deck tools from every tab, even when focused on
agent strategy. `import_league_deck` downloads a published League version and
forks an editable local copy without changing the public source. `import_decklist`
creates a local deck from a complete text list in one call (zone headings,
`SB:` rows, repeated lands and Arena printing suffixes are supported).
`create_deck` also accepts a structured card list. Unresolved names reject the
whole import rather than silently dropping cards or leaving a partial deck.
`update_deck_cards` applies absolute quantities in one version-checked save while
preserving unmentioned cards and tags; `move_card` preserves zone-transfer
identities. Plan tools edit the same name, game plan, tags, collections and study
fields as the editor. Saved results refresh that editor and offer **Open deck in
editor**; unsaved drafts remain protected. These actions are local, not server
publication, and importing a list does not certify format legality.

Live spectators tolerate concealed sideboard slots without resolving their
identities or artwork. A Pixi render failure is contained inside the spectator,
with explicit display retry/back controls that do not stop or restart gameplay.
Importing the Python app factory does not initialize the default user profile or
run competition/League recovery. The compatible `deepdeck_learner.app:app` entry
point initializes its default FastAPI instance only when explicitly requested.

The deck editor also provides manual advanced card research: rules text, card
type, color identity and mana-value bounds use the production League's native
search grammar. The mana-symbol selector accepts every color combination,
including three/four-color Commander identities, with explicit unrestricted
and colorless-only modes. Colored selections also include colorless identities;
they do not require a card to contain every selected color.
Search results can be added to the selected deck zone. This
public research requires neither an agent key nor an LLM. **Next results** and
**Previous results** browse bounded 20-card windows, including all remaining
cards before advancing to the next server page. Paging uses the last submitted
filters even if the form has since changed. Failed requests retain the previous
results and retry the same cursor. Changing format clears old results; stale
responses cannot populate the new format. The copilot's `search_cards` tool can
use `resultWindow.next` with the same filters and its `page`/`page_offset` fields.
The public catalog is read again on each request, not frozen as a snapshot.
Semantic search is
optional and reports a server limitation when unavailable; it is not silently
replaced with a keyword search.

In the assistant settings, **Use my League MCP connection** optionally connects
the local Agents SDK to that same server's `/mcp` endpoint. It prefers the
configured account API key, using a bound website session only when no key is
configured; it never uses the LLM key and respects the account's MCP access.
The connection exposes only card research, owned deck lists/workspaces and tags;
publishing and remote mutations are not available to the model. Its test button
checks tool discovery without calling an LLM. Settings provides key-only
**Save & test connection** and **Test saved key** actions; email/password is not
required for MCP. A matchmaking key being accepted does not prove MCP permission.
Denied checks report the server's HTTP status and sanitized reason. A Patreon
denial offers the League support page and account connection/synchronization,
without hardcoding a membership tier or inferring rights from a saved key.
If the SDK hides the transport error, one read-only MCP ping recovers the refusal;
this invokes no tool, opens no session and never follows redirects.
Known response-schema failures (such as a missing `resultType`) instead report
`protocol_mismatch` without another ping or a sign-in/Patreon prompt. The saved
key is retained; fixing the client/server contract is required before retrying.
Local tools remain available when
MCP is disabled or unavailable. The chat displays actual tool outcomes and can
move card copies between zones or adjust bounded combo, synergy, findability
and castability relationships in a local draft. The configured model provider
receives chat and tool results; League credentials are not included in them.

Local deck workspaces live under ignored `.deepdeck/deck-workspaces/`. Settings
also offers an optional website session under advanced deck synchronization for
operations that do not yet accept an API key; the password is not saved.
On Windows, the retained site session is protected with user-bound DPAPI.
Signing in can optionally create a 90-day agent key. A manually saved key takes
precedence over an older environment or `.env` key after restart.

Read-only browser QA: `DEEPDECK_QA_LEAGUE_CONNECTION=1 python scripts/verify_player_app.py`
checks key-only setup, refusal details, Patreon/account links and optional website
session on desktop/mobile. It requires a configured saved key but uses a fixture
for the MCP test response; it saves no credential and makes no paid LLM request.

The workshop imports your site drafts, uploads local edits, publishes versions,
and downloads site changes. A conflicting draft requires an explicit choice;
local and remote replacement backups stay in the ignored workspace. Agents keep
their local deck identities while deployment advertises the corresponding
published version IDs to the Agent SDK. **Publish & send to League** publishes
all attached local decks and activates selected owned decks in your account's AI
slots when the selected server exposes that capability. Production releases
without personal AI slots use their competition pool instead; unsupported
staging-only endpoints are not called. It never automatically removes an existing active deck when capacity is
full. Signing out does not stop workers or delete their separately saved key.
League publication checks the deck version displayed by the agent before any
upload. If publication stops partway through, completed publications remain on
the site and are reported; no worker is started by that failed attempt. Worker
submission acknowledgements are distinct from a confirmed League connection,
and a failed roster refresh does not turn a successful submission into a retry.
An uncertain worker response asks the user to inspect the roster first. Bringing
an agent home waits for a terminal local-worker acknowledgement; the UI does not
free an account place itself. Missing capacity remains unknown until the server
reports it, including on production releases without the capacity endpoint.

League deployment can remain continuous (default), return after 1–10,000
completed matches, or stop accepting matches after 1–10,080 minutes. The timer
starts when the worker begins connecting; queued tickets are cancelled at expiry,
but assigned matches finish, so a timed run can end later than its timer. A
cancellation/assignment race is reconciled before disconnecting. Failed and
cancelled matches do not consume the completed-match limit. Advanced concurrent
workers share one reservation budget; their count is completed seat entries.
These local stop rules never grant extra server places. Registration/cancellation
errors remain visible rather than being reported as clean completion.
The roster shows reported progress and the four most recent bounded or failed
runs, including failed continuous deployments. New worker failures retain a
bounded, sanitized server reason and HTTP status in their run report, with a
shortcut to connection settings. Historical failures without such a reason stay
explicitly unconfirmed; they are not reconstructed as completed matches.
Workers try replay collection once more on a finite exit; unavailable replays
remain in the durable inbox, not falsely marked downloaded.
The **pending replay downloads** panel in League and Replays reads that inbox,
independently of retained worker logs. Explicit recovery checks at most 10 games
without launching an SDK agent or using a League place. It requires enabled
collection and a connected account, honors the replay quota/favorites, and leaves
unavailable games pending. Each inbox entry is bound to its recorded API origin;
another server's or an unknown legacy origin is never queried with the current
account key. Automatic workers attempt up to 50 entries per pass in least-recently
attempted order, so a missing old replay does not starve newer games. Durable
per-game claims prevent concurrent collectors from duplicating the same download.
After five minutes a crashed owner's claim can be recovered, but a live owner
(verified by PID and process start time) is not displaced by a slow transfer.
Collected snapshots are compacted while
their game IDs remain as deduplication tombstones after replay eviction.

**League → Add to rotation / Manage rotation** keeps a durable waiting bench of
distinct programmatic agents. Each entry has its own competition, mastered deck
pool and continuous, completed-match or cumulative deployment-time budget.
Round robin gives available agents turns in list order; random draws afresh each
turn (repeats are possible). Adding to a paused bench does not start a worker.
Explicitly start the rotation and keep the app open. While running, new entries
can join the waiting bench and take their next available turn. It dispatches normal one-seat SDK
workers, one matchmaking attempt per turn, only after a fresh authenticated
server-capacity response. Unknown or unsupported capacity means no dispatch;
membership tiers and extra places are never inferred locally.

Waiting entries can be removed while other agents play; an atomic last-moment
check refuses removal if that agent has just acquired an active turn. Appending
or removing sends only the intended entry, never a stale whole-bench snapshot.
Active turns, usage totals and round-robin order are preserved. Pause fences
waiting tickets and finishes assigned matches before changing decks, budgets or
rotation order. A restart pauses and fences persisted turns; resume explicitly. Durable
dispatch identities recover the child-creation crash window without duplicate
launches. A private receipt, written after worker disconnect, confirms completed
matches and elapsed deployment time; missing outcomes require explicit review,
not a guessed win, loss or duration. Connecting/matchmaking/game time consumes
the time lease, but waiting locally on the bench does not. The last 30 summaries
and 100 finished queue-worker diagnostics are retained; manual jobs, replay files,
starred games and monotone stop fences are not pruned with those diagnostics.
The League page always shows a compact rotation overview, including dispatched
turns, waiting agents, finished budgets and review counts. Opening details shows
the six most recent turn receipts, with an option to inspect all 30 retained
records. Missing receipts stay unconfirmed (including duration); completion is
not presented as a win or proof that its replay has already been downloaded.

The job API accepts `league_run: {mode: "continuous"}`, `{mode: "matches",
matches: 10}` or `{mode: "duration", minutes: 60}` for the bundled SDK worker.
The corresponding CLI flags are `--league-max-matches` and
`--league-duration-seconds`. They cannot be combined with each other. A match-count
limit cannot be combined with `--once`; a duration can bound a single attempt.
The pinned Agent SDK and server capacity rules are unchanged.

Each managed built-in League worker now has its own `--league-run-id`, separate
from the agent's policy identity. Restart reconciliation and detached stopping
check that execution, not just a shared agent/checkpoint path. Exact job lookup
also works beyond the recent 100-row dashboard window. SDK registration reports
include a connection flag and reconnect generation; starting a local process is
not proof of server registration. These observations do not grant account places.

Replay settings independently control automatic League collection and the disk
cap. Quota-only saves do not re-enable collection or overwrite a custom polling
interval; unsent settings survive background refreshes. Pausing collection does
not stop an agent or erase recordings, and a check already in progress may
finish. Settings apply at the next collector check. The cap is not preallocated
disk space; reducing it can retire the oldest unprotected recordings.
League and Replays show the configured state, last agent reports, pending games
and download/storage errors. Historical collected totals are not a count of files
still retained under quota. A stopped agent's latest pending report stays visible;
reopening the replay library alone does not restart its collector.

Local deck edits are version-checked too. If the copilot or another window changes
a deck while you are typing, the editor keeps your unsaved name, plan, labels and
card tags. Choose **Load latest deck** to discard the draft, or **Keep my edited
details** to reapply only those fields over the newer list. Unsent card actions
are not automatically replayed. Changing tabs warns before discarding a draft.
The app sends `expectedVersion` for saves, sync, publish and pull; deck chat tools
require `expected_version` from the most recent `get_deck` or successful edit.
Writers share an OS-backed lock, including sync metadata, so concurrent writes
cannot both accept the same checked version. Legacy API callers omitting this
optional version remain compatible but do not receive optimistic conflict checks.
Deck JSON writes briefly retry Windows sharing/access failures during atomic
replacement, without deleting the old destination to force a save. Exhausted
retries and disk-full errors remain failures; no success is fabricated.
The chat's **+ Add** shortcut also checks the version, serializes card additions
with chat requests, and never retries a conflict automatically. A confirmed add
followed by a failed library refresh is labelled saved; users are warned not to
add the card a second time just to refresh the display.
If the save response is lost or a server error leaves its outcome uncertain,
the chat asks the user to check the saved quantity before adding another copy.

The deck notebook displays and edits combo ideas and opening-hand examples,
including annotations saved by the copilot. Practice draws sample actual main-deck
copies without replacement; sideboard, considering, commander, companion and game
pieces are excluded. Each draw is independent, not a London-mulligan simulation.
Keep/mulligan verdicts are personal study notes, not an automatic agent policy or
proof that a combo works. Card relationship scores are displayed with direction
and pagination; the copilot can edit them through its existing validated tool.
Apply a note to the draft, then **Save deck**. Editing cards does not silently save
or erase unsent study notes. Concurrent unrelated notes can be combined, but edits
to the same named note require review. Names must be unique within each section.
Notebook data survives the existing League draft synchronization as metadata.

The agent copilot's read-only `inspect_deck` tool reads downloaded cards, personal
card tags, notebook notes and directed relationship scores, with bounded pages
and deck/agent revision identifiers. Its chat receipt distinguishes annotations
from the selected agent's active strategy tags. To use a personal role in play,
the copilot must explicitly save a card/tag pattern and graph changes in a new
agent revision; reading a note does not train the agent or prove a combo works.
Unknown imported metadata is not forwarded wholesale to the model.

The default League target is production at `https://www.deepdeckleague.com/api/v1`.
The legacy `https://deepdeckleague.com/api/v1` setting is normalized to that same
canonical production host for account, catalog, SDK WebSocket, and replay traffic.
It never falls back to staging. An explicitly configured staging/custom URL is
preserved. Settings shows the actual server and its advertised capabilities.
Production and staging have separate account keys; a rejected key must be
replaced or recreated by signing in to the selected server.

The website's complete advanced search/MCP workflow, signed desktop installer
and automatic updates remain integration work. Account and deck synchronization have
contract tests; live-server sign-in still requires your credentials and an
available League server.

### Building a Windows preview

Build on Windows x64, after the normal frontend and Engine builds. Use an
isolated environment so neural-training/GPU libraries are not bundled:

```powershell
py -m venv .deepdeck/desktop-build/venv
.deepdeck/desktop-build/venv/Scripts/python.exe -m pip install ./external/deepdeck-agent ".[desktop,desktop-build]"
.deepdeck/desktop-build/venv/Scripts/python.exe scripts/build_desktop.py --check
.deepdeck/desktop-build/venv/Scripts/python.exe scripts/build_desktop.py
python scripts/verify_desktop.py "dist/desktop/<timestamp>/DeepDeck-0.3.1-windows-x64.zip" --native --browser --work-dir .deepdeck/desktop-build/qa
```

The build script imports its freshness checks from this checkout's `src/`, not
an older Learner wheel still installed in the isolated environment. Its read-only
preflight reports `learnerSource` so that provenance can be checked before a
build; an already-loaded package from another installation is rejected.

The default build compiles the frontend into its own timestamped staging folder.
It does not modify the source app's `dist/`, which retains older hashed chunks
for already-open tabs; those historical chunks are not included in the new ZIP.
Use `--skip-web-build` only to deliberately package the existing source `dist/`
as-is, including any historical assets it contains.

`--check` prints a read-only JSON preflight and exits nonzero if a prerequisite
is missing. It checks component provenance/main guards, Engine freshness,
builder/frontend prerequisites and free space at the requested output/work
locations, without building, downloading, creating those directories or deleting
anything. Low-space warnings use an advisory 2 GiB headroom recommendation, not
a measured artifact size or a guarantee; Rust rebuilds and full verification need
additional space. Two locations on the same volume share that free space.
Use `--output-root` and `--work-root` to inspect or build on another drive.
Dirty components still require `--allow-dirty-components` for an explicitly
marked development preview; this never bypasses Engine freshness or `origin/main`.

By default the builder refuses dirty Engine/Pixi/Agent sources. Developers may
explicitly use `--allow-dirty-components` for a development preview containing
uncommitted fixes: the manifest marks each dirty component and the archive's
checksum identifies the exact build. This never commits, changes branches, or
bypasses the requirement that Engine and Pixi HEAD match fetched `origin/main`.
The builder still verifies the Engine binary is current. It packages an explicit asset allowlist,
never the developer's `.deepdeck` workspace or `.env`. ZIPs and verification
reports go under ignored `dist/desktop/` by default. Use `--output-root PATH`
for the archive and `--work-root PATH` for PyInstaller's temporary build files
when another drive has more free space; each build gets a fresh timestamped
subdirectory. The verifier extracts into its own
temporary directory, removes development tools from the app's PATH, starts the
bundled Engine on a dedicated port, imports a real public deck, completes a
two-match SDK batch, checks replays/statistics, then uses a loopback LLM fixture
through the actual SDK to edit a strategy and launch human play on that same
Engine. It verifies untimed waiting, an explicit inactivity limit, partial
replays without fabricated results, and recovery of its owned bundled Engine.
Finally it checks the seven consumer tabs (including Competitions) in a hidden native window. `--work-dir` controls
the location of retained isolated QA installations/profiles when the system
drive is low on space. It never uses paid models
or touches the developer's active Engine or private agents.

`--browser` additionally starts a table through the actual Play screen in that
isolated QA profile, checks preparation and the Pixi canvas, leaves using **Back
to lab**, and verifies the interrupted recording did not add a match result.

## Replay collection and storage

Local spectators can choose a fixed seat or a global view of all recorded hands.
New local matches keep a separate, bounded Engine spectator stream: both hands
are available, but library order and private sideboards stay concealed. This
does not change the observations given to AI policies. Network-game seats cannot
access the omniscient stream. Older recordings with redacted cards remain
redacted; the viewer explicitly reports unavailable identities.

The live viewer joins at the latest state, then queues subsequent actions at the
replay pace (700 ms per frame at 1×). Pause and speed changes affect viewing only;
Jump to live discards the pending viewing queue, not the recording or match.
Completed local recordings supply a final tail when the worker closes a session.
The Engine stream retains at most 512 frames / about 32 MiB per session, and the
viewer at most 2,000 pending frames. Gaps or buffer eviction are reported, not
silently reconstructed. Old Engines fall back to basic snapshots.

Live views, replays and playable tables offer opt-in procedural Web Audio sound
effects for lands, spells/abilities, attacks, damage, turns and game completion.
These original effects are covered by this repository's MIT license, with no
downloaded audio assets or third-party service. Sound starts muted and requires
a user click; volume can be adjusted or set to zero. Spectator cues follow the
displayed timeline, not the faster AI worker. Browser QA:
`DEEPDECK_QA_SPECTATOR=1` with `scripts/verify_player_app.py` uses read-only,
browser-local fixtures to check Pixi, controls and audio on desktop/mobile.

`python scripts/verify_spectator_stream.py` checks the actual running local Engine
with two temporary SDK agents and two short AI-only matches. It verifies both
recorded hands, concealed private zones (including the final frame), monotonic
spectator frames, visual/audio events and decision diagnostics, and that opposing
hands stay hidden in SDK observations. Its isolated recordings/statistics are
removed afterward; it does not use or change the personal player profile, call
a paid LLM, join the League, or restart the Engine. New local recordings retain
minimal event summaries for sound/stack playback: new events plus one latest
event on quiet frames, not a repeated full journal or arbitrary private payloads.

`python scripts/verify_commander_desktop.py dist/desktop/<timestamp>/DeepDeck`
checks a real four-seat Commander match with public Alexios, Yenna and Tatyova
decks in a fresh portable-player profile. It uses no model or League deployment,
checks all four recorded hands/private zones and per-agent replays, and verifies
one match/four seat results. It leaves its report under ignored
`.deepdeck/validation/commander-desktop-*`; the normal player profile is not used.

League workers keep a durable inbox of finished games and retry unavailable
replays every ten minutes by default while deployed (the interval is configurable).
Native server deltas and card artwork
metadata are stored locally, then expanded for the Pixi player when opened.
Worker output goes to local log files so closing the UI does not break an agent's
connection. Collection stops with the worker.
Local Engine and stack-setup jobs also write to durable files under
`.deepdeck/worker-logs/`, so restarting the web app does not close their stdout
pipe and cause the Rust logger to abort subsequent games.
The player shell requests `/api/v1/jobs?view=player`, excluding neural-training
jobs before applying the recent-history limit. The advanced `/api/v1/jobs` view
and training APIs are unchanged; no historical runs are deleted.

The replay library distinguishes loading, unavailable/partial reads and a
confirmed empty view. A failed per-agent read retains that agent's last loaded
recordings; successful reads can still update other agents. Refreshes are
sequential, bounded to 30 seconds and cancelled on navigation/restart. A late
pre-favorite response cannot undo a confirmed star. Competition links wait for
known library data before claiming that a recording was evicted, and an open
reader keeps its replay, viewing lease and current step during refresh failures.
Optional read-only browser QA: `DEEPDECK_QA_REPLAY_LIBRARY_RECOVERY=1` with
`scripts/verify_player_app.py` exercises held reads, retained real recordings,
explicit retry and desktop/mobile layouts without changing games or favorites.

The default replay quota is 2 GiB, configurable in Settings. Old unpinned games
are removed first; active viewing leases and permanently kept games are
protected. Collection remembers retired game IDs so it does not download the
same old games again after quota cleanup. Local matches also retain results
independently of replay cleanup. Insights counts a shared match once, while
agent/deck/player rows count their individual seat results.

## What works now

- Create and tune private programmatic agents for downloaded decks.
- Use those agents in the existing local Playtest and League workflows.
- Optionally edit agents through an OpenAI-compatible LLM endpoint.
- Train V11 or V12 from the built-in smoke samples.
- Train from a JSONL decision trajectory and resume a checkpoint.
- Prefer CUDA automatically and fall back to CPU when no compatible GPU is available.
- Configure trajectory input, CPU/CUDA, epochs, learning rate, and seed through advanced CLI/settings APIs.
- Run random, Alexios, V11, or V12 against a local Engine session.
- Inspect dependency readiness and bounded job logs.
- Connect an inference agent to Deep Deck League with your account API key.

The player interface does not start neural training. Advanced training utilities
remain separate from programmatic play and do not treat an inference match or
a visual replay as a hosted training trajectory.

## Advanced neural tools

Neural training and GPU controls are outside the main player interface. Their
command-line tools and controller APIs remain available. The workbench creates
the ignored `.deepdeck/trajectories/decisions.jsonl` path and its parent directory
on startup for existing training workflows.

The public pipeline performs behavior cloning on the chosen legal action and
MSE regression on player-value targets. V11 uses four multiplayer value slots;
V12 exposes a two-player zero-sum value pair. The model scores only actions
declared legal by Rust.

Read [the deep-learning guide](docs/deep-learning.md) for the Magic-to-tensor
representation, dataset schema, checkpoint contract, and extension points.

## Test behavior locally

Start the public Rust server in a second terminal:

```sh
cargo run --manifest-path external/deepdeck-engine/Cargo.toml --locked --bin mtg-engine-server
```

Open **Play** and select decks and agents by name. A table accepts any mix of
humans and AIs: two seats in Legacy or four in Commander; all-AI tables can also be
watched live. The integrated Pixi renderer reads the Engine's actual session
view. No separate Pixi server or manually copied session identifier is needed.

For the ten installed benchmark decks, `python scripts/prepare_example_agents.py`
creates local starter agents without overwriting existing agents with those
names. `python scripts/verify_player_app.py` checks the seven player tabs in installed
headless Edge and writes ignored desktop/mobile screenshots under
`.deepdeck/ui-preview/`.

The source frontend includes a root display-recovery boundary: a rendering
failure shows recovery controls instead of a blank window. Retry and reload are
explicit user actions; recovery does not repeat game/League actions, delete saved
files, or display private exception text. A fixed `render_error` marker goes only
to the local controller (never the exception object) and displays a reference if
it was recorded. Unsaved edits may need to be entered again. This does not catch
every asynchronous browser error. Older portable previews need rebuilding to
include these source changes.

### Local-only diagnostic journal

Settings → **Local diagnostics** exposes a private support journal. It is read
only on request; copying metadata to the clipboard is also explicit and nothing
is automatically uploaded. The controller protects journal access with its local
session token and stores only generated references, timestamps, HTTP methods,
code-owned route templates, statuses, durations and predefined outcome categories.
Successful read-only polling is omitted. No URLs/query values, dynamic IDs, keys,
headers, bodies, searches, card/deck data, chat content or exception text enter this
journal. MCP connection failures are distinct from HTTP success; an interrupted
stream retains its committed status and is not relabeled as a completed success.

The private `.deepdeck/diagnostics.sqlite3` database is limited to 2,000 entries
and an 8 MiB SQLite page budget. SQLite can briefly need an additional database-sized
rollback journal during a write. Expired entries (older than fourteen days) are
removed on the next journal read/write, and oldest entries are evicted first when
full. This budget is separate from replays, local statistics and existing raw worker
logs. A full/busy/unavailable diagnostic store must not fail a player action;
an unrecognized pre-existing database is refused rather than overwritten.

Unexpected controller exceptions return a generic error and a server-generated
`X-Request-ID`, which the frontend displays. The journal can be searched visually
using that reference. The render-marker endpoint accepts only the fixed category,
requires local authorization, and caps its actual streamed body at 512 bytes.
Display errors use a `CLIENT` marker with status zero, not an invented HTTP 500.

## Public examples

- `random`: reproducible random selection from exact legal actions.
- `alexios`: a readable priority policy for Alexios, equipment, protection,
  removal, goad, combat tricks, Food, and Clues.
- `forge-cautious`, `forge-balanced`, `forge-aggressive`: clean-room,
  programmatic Forge-style references that rank the Engine's exact legal
  actions without copying or linking Forge's GPL-3.0 implementation. See the
  [Forge reference-agent notes](docs/forge-reference-agents.md).
- `v11`: recurrent PyTorch policy with multiplayer value estimates.
- `v12`: two-player variant with antisymmetric value estimates.

The original command-line interfaces remain available:

```sh
deepdeck-train v12 --smoke --epochs 2 --output runs/v12-smoke
deepdeck-example v12 --target local --checkpoint runs/v12-smoke
deepdeck-example alexios --target ddl --speed 1s
deepdeck-example forge-balanced --target local --start-local-game
```

The complete Oracle AI implementation is also installed with the workbench:

```sh
oracle-ai-train --config configs/oracle-ai/train-smoke.yaml
oracle-ai-league-train --config configs/oracle-ai/league-v12-legacy.yaml
oracle-ai-v12-clients --help
```

See [the Oracle AI reference](docs/oracle-ai.md) for the production model,
self-play, checkpoint, evaluation, and League-runner contracts. Generated runs,
weights, trajectories, and local datasets stay under ignored local directories.

## Hosted inference and future online training

Copy `.env.example` to `.env`, then set your account-owned
`DEEPDECK_API_KEY`. The Matchmaking screen finds competitions and account-visible
decks by name, so users do not enter version identifiers. Catalog requests are
refused locally without the key and send it to Deep Deck League as a Bearer token.
The key identifies the account; the agent manifest remains free to describe its
own model/version. No browser login or `gcloud auth login` is required.

Playing a hosted match currently performs inference only. Training from hosted
play will require a compatible trajectory capability containing observations,
legal actions, chosen actions, rewards, and terminal placement. A replay is not
assumed to satisfy that contract.

## Dependency updates

The workbench follows `origin/main` for Engine and Pixi while leaving the Agent
SDK pinned by this repository. Update the local dependencies with:

```sh
# Windows
py scripts/workbench.py update

# macOS / Linux
python3 scripts/workbench.py update
```

Engine and Pixi follow their respective `origin/main` branches so work in other clones or
feature branches cannot affect this checkout. The Agent SDK remains pinned. Run the update
command whenever you want to fetch the newest Engine and Pixi commits.

The [C4 architecture views](docs/epics/2026/08-august/deepdeck-learner/06-architecture-deepdeck-learner.md)
show the system context, local containers, protocol boundaries, and the exact
roles of Learner, Engine, Pixi, Agent, and the hosted League.

## Source, installation, and personal storage

| Area | Role | Needed in a player installation? |
| --- | --- | --- |
| `src/`, `apps/learner-web/src/`, `configs/`, `scripts/`, dependency source references | Shared, versioned application and default strategy code | Compiled/packaged output only; not the development checkout |
| `.deepdeck/agents/`, `decks/`, `deck-workspaces/`, `players/` | Private agents, policies, versions, decks, and player data | Yes, preserve the profile; excluded from Git |
| `.deepdeck/learner.db`, statistics, replays, credentials and settings | Personal history, jobs/competitions, results and account configuration | Preserve; excluded from Git; never treat the whole profile as a cache |
| `.deepdeck/runs/`, trajectories, checkpoints | Optional neural training results | Only for users who need them; not disposable build caches |
| `.venv/`, `node_modules/`, Cargo `target/` | Development dependencies and generated compilation output | No; rebuildable, but not while related builds/processes are using them |
| `dist/desktop/`, desktop build staging, QA extractions | Generated releases and their test copies | Keep the chosen release/rollback; old verified-unused copies can be retired |

Portable and Python wheel installations keep their profile outside the application directory,
under `%LOCALAPPDATA%/DeepDeck/PlayerLab/.deepdeck/`. A source launch uses the
workspace profile instead. Replay and statistics quotas do not limit Cargo
caches, training results, or release staging directories.

Avoid creating a new Cargo target directory for every task: reuse the checkout's
normal target when safe, and explicitly retire isolated test targets after the
task. Never delete an entire `.tmp` directory on its name alone: it can contain
databases, reports, or other non-regenerable files. Do not share arbitrary target
directories between independently modified Engine branches just to save space.

## Develop

```sh
# Run from the project root after setup, on every supported OS.
python scripts/workbench.py setup --dev
ruff check .
mypy
pytest
npm --prefix apps/learner-web run lint
npm --prefix apps/learner-web test
npm --prefix apps/learner-web run build
```

Only `@dd-the-dd` can update protected `main`. Everyone else can propose a pull
request. See [publishing and protection](docs/publishing.md).
