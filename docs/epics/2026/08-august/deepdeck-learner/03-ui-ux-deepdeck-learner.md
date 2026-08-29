# DeepDeckLearner UI/UX design

Parent epic: [#4](https://github.com/dd-the-dd/DeepDeckLearner/issues/4)

## Experience model

The workbench shares Deep Deck League's paper, burgundy, slate-blue, serif, and
editorial visual language. A dark burgundy application rail, denser controls,
status chips, and the persistent `LOCAL WORKBENCH` badge distinguish it as a
local desktop-like tool rather than another hosted site page.

The default screen is an intent router, not a dashboard. It asks one question:
**What do you want to do?** The three answers are **Train an agent**, **Test an
agent locally**, and **Send an agent to the League**. A first-run callout explains
that training is the shortest path and does not require Engine, Pixi, decks, or
an account key. Hosted training remains a secondary `Hosted (later)` option on
the Train screen and cannot be mistaken for the recommended start.

After an intent is selected, a three-step journey remains above the working
area. Readiness is contextual: Engine/Pixi appear for local play, the account key
appears for League matchmaking, and neither distracts from local training.

## Information architecture

- Home: intent cards, recommended first action, contextual workspace summary,
  and recent activity only when activity exists.
- Train: local/online segmented control, beginner fields, advanced disclosure.
- Playtest: local agent and deck-session configuration plus visual client.
- Matchmaking: account-key checklist, public deck search, agent configuration,
  and queue status.
- Representation: Magic-to-tensor schema, dimensions, masks, and examples.
- Models: V11/V12 descriptions, checkpoints, and experiment metadata.

## Primary layout

```text
+-----------------------------------------------------------------------+
| DeepDeckLearner  LOCAL WORKBENCH                 Workbench ready       |
+-------------------+---------------------------------------------------+
| Home              | What do you want to do?                          |
| Train             | New here? Start with Train an agent.             |
| Playtest          |                                                   |
| Matchmaking       | [ Train ] [ Test locally ] [ Send to League ]     |
| Representation    |                                                   |
| Models            | Workspace: Training ready | Local play setup     |
+-------------------+---------------------------------------------------+
```

Local play then narrows to one sequence:

```text
Prepare Engine + Pixi  ->  Choose agent and decks  ->  Launch behavior test
       [Set up Engine + Pixi]
       1 Sources   2 Pixi build   3 Engine running
       > Technical details and recovery controls
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

