# DeepDeckLearner UI/UX design

Parent epic: [#4](https://github.com/dd-the-dd/DeepDeckLearner/issues/4)

## Experience model

The workbench shares Deep Deck League's paper, burgundy, slate-blue, serif, and
editorial visual language. A dark burgundy application rail, denser controls,
status chips, and the persistent `LOCAL WORKBENCH` badge distinguish it as a
local desktop-like tool rather than another hosted site page.

The default screen uses three workflow cards: **Train locally**, **Test locally**,
and **Join matchmaking**. Hosted training remains available from the Train page
as an explicitly unavailable capability. Each card shows `Ready`, `Needs setup`, `Running`, or
`Unavailable` before the user enters a form.

## Information architecture

- Overview: dependency health, quick starts, recent jobs.
- Train: local/online segmented control, beginner fields, advanced disclosure.
- Playtest: local agent and deck-session configuration plus visual client.
- Matchmaking: account-key checklist, public deck search, agent configuration,
  and queue status.
- Representation: Magic-to-tensor schema, dimensions, masks, and examples.
- Models: V11/V12 descriptions, checkpoints, and experiment metadata.

## Primary layout

```text
+-----------------------------------------------------------------------+
| DeepDeckLearner  LOCAL WORKBENCH       Engine ready | Pixi ready       |
+-------------------+---------------------------------------------------+
| Overview          | Start from what you know                          |
| Train             | [ Train locally ] [ Train online ] [ Test local ] |
| Playtest          |                                                   |
| Matchmaking       | Account key -> Find deck -> Join queue             |
| Representation    | Configure                                        |
| Models            | Model [V12]  Compute [GPU preferred] [Start]      |
|                   | > Advanced settings                               |
|                   |                                                   |
|                   | Recent jobs                                      |
|                   | V12 smoke   Running ...                           |
+-------------------+---------------------------------------------------+
```

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
- A compact **Local runtime** panel appears on Overview and Playtest. Each row
  shows the current and compatible short revisions plus one primary lifecycle
  action and one synchronization action. The panel-level `Start local stack`
  action skips components that are already ready.
- Pixi uses `Prepare` rather than a misleading server verb: it is a renderer
  package built for the local visual client, while Engine is the process that
  actually starts and remains running.
- Synchronization is labelled `Sync version`, with nearby copy explaining that
  it uses reviewed pinned commits rather than upstream `main`.
- Advanced settings never reset beginner selections when collapsed.
- Start buttons use a nearby blocker list instead of a generic disabled cursor.
- Job output is a bounded log region with pause-scroll and copy controls.
- Destructive stop actions request confirmation only for an active job.

## States

- Initial: skeletons limited to the readiness row; forms remain stable.
- Empty: example dataset guidance and one-click smoke option.
- Error: plain cause, affected workflow, retry, and command-line fallback.
- Running: elapsed time, phase, last log line, stop action.
- Complete: checkpoint/artifact path and suggested `Test locally` follow-up.
- Unsupported: visible roadmap reason; no inert primary action.
- Dependency update: current/compatible revisions, progress in Recent jobs, and
  an actionable dirty-worktree or toolchain error without losing page state.
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

The initial release uses CSS shapes and the existing Deep Deck wordmark treatment.
It does not create AI imagery. Engine-rendered card imagery remains owned by Pixi.

