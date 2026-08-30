# DeepDeckLearner UI/UX design

Parent epic: [#4](https://github.com/dd-the-dd/DeepDeckLearner/issues/4)

## Experience model

The workbench shares Deep Deck League's paper, burgundy, slate-blue, serif, and
editorial visual language. A dark burgundy application rail, denser controls,
status chips, and the persistent `LOCAL WORKBENCH` badge distinguish it as a
local desktop-like tool rather than another hosted site page.

The default experience is one ordered journey: **Setup → Agent setup → Use**.
Setup owns the League key and compatible Engine/Pixi runtime. Once those are
ready, Agent setup becomes the natural landing stage and asks for model, format,
and a named deck pool. Use then offers **Playtest against AI**, **Train**, and
**Run in the League**. Technical and ML details remain progressively disclosed.

## Information architecture

- Setup: account-key connection, Engine/Pixi synchronization, build and health.
- Agent setup: model, format, authenticated named-deck search, selected pool,
  and save state.
- Use: profile summary and three operation cards; the selected operation reveals
  its focused form below.
- Activity: controller-owned jobs, progress, artifacts, and stop actions.
- Representation: Magic-to-tensor schema, dimensions, masks, and examples.
- Models: V11/V12 descriptions, checkpoints, and experiment metadata.

## Primary layout

```text
+-----------------------------------------------------------------------+
| DeepDeckLearner  LOCAL WORKBENCH                 Workbench ready       |
+-------------------+---------------------------------------------------+
| 1 Setup           | Configure this application                       |
| 2 Agent setup     | Model | Format | Training deck pool               |
| 3 Use             | [ Playtest ] [ Train ] [ Run in League ]          |
| Activity          |                                                   |
| Representation    |                                                   |
| Models            | Workspace: Training ready | Local play setup     |
+-------------------+---------------------------------------------------+
```

The workbench narrows to one sequence:

```text
Connect + prepare  ->  Configure agent  ->  Choose how to use it
  API key              V11 / V12           Playtest against AI
  Engine + Pixi        Format               Train
                       Deck pool            Run in the League
```

Reference wireframe: [guided workflow](assets/guided-workflow-wireframe.svg).

## Interaction details

- Beginner labels use Magic language: format, deck, opponent, and checkpoint.
- Deck identifiers never appear as beginner inputs. Hosted decks use the League
  search and local decks use the Engine legal-deck catalog.
- Account setup links to the exact site section that creates an autonomous-agent
  key, then reports only whether the controller detected it.
- ML terms include a concise tooltip and link to the representation guide.
- Trajectory input and path are advanced controls. The suggested file lives
  under `.deepdeck/trajectories/` in the project and is created automatically.
- CUDA is the visible default and falls back to CPU without blocking beginners.
- Hosted deck and competition discovery is disabled until the local controller
  detects an account API key; the controller also enforces this boundary.
- The Agent setup deck pool uses the authenticated League catalog and dense
  selectable result rows. Selected decks remain visible as removable chips/cards
  even when the search query changes.
- Quick start bundles appear between model/format and the selected pool. A
  bundle card shows its as-of date, archetype count, compact archetype chips,
  sources, and one `Add bundle to pool` action. While resolving, format changes
  and other bundle actions are disabled. Completion reports missing catalog
  archetypes while keeping every successful addition editable.
- Setup completion advances the first-run emphasis to Agent setup but never
  interrupts a user who deliberately navigates back to Setup.
- Local runtime appears only in Playtest. `Set up Engine + Pixi` is the single
  normal action: it initializes missing gitlinks, synchronizes stale gitlinks,
  prepares Pixi, and starts Engine. Its three stages remain visible across
  browser refreshes because the controller owns the composite job.
- Current/compatible revisions and individual synchronization/start controls are
  inside `Technical details and individual controls`. They are recovery tools,
  not prerequisite choices for a beginner.
- Pixi uses `Prepare` rather than a misleading server verb: it is a renderer
  package built for the local visual client, while Engine is the process that
  actually starts and remains running.
- Synchronization is labelled `Sync version`, with nearby copy explaining that
  it uses reviewed pinned commits rather than upstream `main`.
- Advanced settings never reset beginner selections when collapsed.
- Start buttons use a nearby blocker list instead of a generic disabled cursor.
- Job output is a bounded log region with pause-scroll and copy controls.
- Destructive stop actions request confirmation only for an active job.
- Home never embeds a workflow form. Selecting a card changes the page and moves
  focus to the workflow heading; the browser Back action is a future routing
  enhancement because the current workbench stores navigation in React state.

## States

- Initial: skeletons limited to the readiness row; forms remain stable.
- Empty: example dataset guidance and one-click smoke option.
- Error: plain cause, affected workflow, retry, and command-line fallback.
- Running: elapsed time, phase, last log line, stop action.
- Complete: checkpoint/artifact path and suggested `Test locally` follow-up.
- Unsupported: visible roadmap reason; no inert primary action.
- Dependency update: current/compatible revisions, progress in Recent jobs, and
  an actionable dirty-worktree or toolchain error without losing page state.
- Composite setup: stage 1 sources, stage 2 renderer, and stage 3 Engine each
  show pending/active/complete text and symbols. A dirty dependency disables the
  main action and explicitly promises that no files were overwritten.
- No deck results: keep the query and format visible, explain that no legal deck
  matched, and offer another search without falling back to a raw identifier.
- Partial bundle: retain resolved decks, list unresolved archetypes by name, and
  leave Save under explicit user control.

## Responsive behavior

- At 960 px the side navigation becomes a compact top navigation.
- At 720 px workflow cards and forms become one column.
- Logs remain horizontally scrollable; the full page never scrolls sideways.
- Touch targets are at least 44 px and status is never conveyed by color alone.

## Accessibility

- Semantic headings, labels, field descriptions, and live job-status regions.
- Keyboard access for navigation, disclosures, tabs, and job actions.
- WCAG AA contrast for text and controls; reduced-motion disables card slides.
- Focus moves to the first validation error and returns to Start after correction.

## Visual assets

The application rail displays the canonical Deep Deck League logo from the
League's hosted public asset, followed by the `Learner` product suffix. The logo
links back to the League in a new tab and retains meaningful alternative text if
the hosted asset is unavailable. Referencing the canonical asset avoids shipping
and maintaining a second multi-megabyte logo copy in this public workbench.

The rail footer includes a compact `Support on Patreon` action using the same
Patreon glyph and burgundy/paper treatment as the League. It opens the official
Deep Deck League Patreon page in a new tab, remains visible in the responsive
top-navigation layout, and is visually subordinate to workflow actions. Source
code remains a separate secondary link so community support is not confused with
an application capability.

The release does not create AI imagery. Engine-rendered card imagery remains
owned by Pixi.

## Secure local settings

Settings contains the League connection and local/LAN listener. The host
receives a masked `Save and verify` field for the real `ddl_agent_...` key; the
value is cleared after submission and never rendered again. A trusted LAN
browser opens the workbench directly and never receives that key. Opening the
workbench through one of the host computer's own LAN addresses still yields the
owner experience, so the host can configure the key from that browser URL.

Network mode uses two explicit choices: `This computer only` and `Local
network`. A changed listener exposes `Restart now`, detected LAN addresses, and
Windows Private-network firewall guidance. A LAN device can operate normal
workflows but sees an explanation in Settings instead of disabled secret and
security controls.

