# DeepDeckLearner UI/UX design

Parent epic: [#4](https://github.com/dd-the-dd/DeepDeckLearner/issues/4)

## Experience model

The workbench uses a dark, game-table palette related to Deep Deck League while
always displaying a `LOCAL WORKBENCH` badge. The label and loopback URL prevent
confusion between a user's machine and the hosted league.

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
| Models            | Model [V12]  Input [Smoke sample]  [Start]        |
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

