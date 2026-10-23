# Strategic graph releases

### Combat must earn value — 2026-10-21

- Reproduced the reported Eldrazi–Naya decision from the saved local competition:
  replay `batch-12756b315b474ab7-0001`, frame 22, turn 6. Guide of Souls (1/2)
  attacked into an untapped Thought-Knot Seer (4/4). The recorded explanation
  wrongly tagged Guide as evasive because its triggered ability *mentions*
  a flying counter; it awarded a 58-point connection bonus and scored the
  attack at 170.587. No flying counter was present on the saved Guide.
- Combat now reads actual top-level keyword abilities, keyword counters and
  standalone keyword text, separately from the broad tags used to classify decks.
  Flying checks flying/reach blockers; trample and menace are not unblockability.
- Attack scoring considers the defending player's available blockers, individual
  and paired blocks and an all-blockers response; it evaluates actual material
  exchange, first/double strike, deathtouch, indestructibility, marked damage and
  trample damage. Sampling limits paired-block work on token-heavy boards.
  Merely declaring an attack no longer earns theoretical damage or false lethal.
- Keeping a creature back has a marginal defensive value: an ordinary 1/2 can
  prevent a nontrampling 4/4's four damage, not just two points corresponding
  to its toughness. Vigilance preserves that option. Direct, unconditional
  self-attack draw/token/life triggers can contribute weighted value.
- Existing node weights and custom decision nodes still control preferences;
  this is not a ban on sacrificing an attacker. Open mana, an arbitrary instant
  in hand, or the mention of a future ability does not invent a winning trick.
  Predictive trick casting, bluff policies, conditional/optional attack-trigger
  planning, arbitrary static ability grants and full coordinated attack-set
  search remain outside this bounded heuristic.
- Re-evaluating the saved frame with the recorded archetype mix and temperature
  gives the attack -110.868 versus 0 for finishing attackers; the agent keeps
  Guide back. Opposing private hand/library identities were excluded. This is
  a decision regression, not a re-run of the match or evidence of improved win rate.
- Added 29 combat regressions, including the SDK reference-agent path, all three
  profiles at zero/default/maximum exploration, actual flying counters, reach,
  menace, trample, double blocks, real lethal, multiplayer targets, useful
  triggers and a deliberate custom-node override. The ten-deck family benchmark
  remains 10/10 (classification only).
- Strategy references: Wizards' [Attacking and Blocking](https://magic.wizards.com/en/news/feature/attacking-and-blocking-2015-07-27)
  and [Blocking a Bluff](https://magic.wizards.com/en/news/feature/blocking-bluff-2017-10-11).
  They explain why combat should consider exchanges, real tricks and ability
  benefits without treating every uncertain combat as a reason never to attack.
- Implementation revision: `combat-value/2026-10-21`. Newly started Python
  agent workers use it without changing private `agent.py` or `agent.json`;
  already-running workers and older portable distributions are not hot-patched.

### Redundant equipment activation — 2026-10-20

- An actual mixed Commander table exposed repeated zero-cost Lightning Greaves
  equip activations on its current bearer. The last human snapshot showed a
  commander-zone choice, but the recorded SDK decisions identified the equip
  loop; the Engine eventually expired the waiting action request.
- The shared graph now recognizes `decisions.equip`, nested Engine targets and
  the source's public `attachedTo`. Re-equipping the same bearer receives an
  explicit -1000 resource-preservation contribution, before generic artifact
  development rewards. Ordinary resource-weight changes do not remove it;
  deliberate attachment-trigger strategies can override it with custom nodes.
  Equipping a different bearer remains available. No Engine legality change,
  policy migration or private agent rewrite is involved.
- Regression coverage includes deterministic/noisy choices, the actual SDK
  reference-agent path and equipping a different bearer. This is a loop fix,
  not evidence of higher match win rate or a general equipment-transfer model.
- Implementation revision: `declarative-nodes/2026-10-20-equipment`. New Python
  workers load it; existing workers and the older portable ZIP do not.
- The same mixed Commander seed then completed at turn 32 (Alexios winner,
  175 recorded frames, four local seat results). The ten-deck classifier remains
  10/10. The full source suites passed: 1087 Python and 384 frontend tests.
  SDK handler records were present, but Engine acknowledgements were unavailable;
  this does not establish zero fallback use or general strategy superiority.

### Declarative custom decision nodes — 2026-10-19

- Private policies can extend the shared graph with bounded weighted nodes,
  filtered by action, card, tag, turn, role and target controller. A validated
  directed acyclic graph connects board needs, normalized base scores and
  other custom nodes. No executable expressions or in-game LLM calls.
- The Agents workshop provides examples, a form editor and input → node →
  score cards. The copilot uses the same revision-checked validation. Disabled
  and nonmatching nodes propagate zero; unknown inputs and cycles are rejected.
- Empty extensions preserve existing scores and fingerprints. New nodes are
  optional preferences, not a claim of higher playing strength. The ten-deck
  classifier remains 10/10; this is not a win-rate benchmark.
- `scripts/verify_custom_decision_nodes.py` uses an isolated real Engine and
  SDK agent. A node adds exactly 50 to Brainstorm's score; the Engine accepts
  the cast, draws three cards, accepts the SDK's typed return/order choices and
  puts the resolved spell in the graveyard. No user policy, table, account or
  statistics are changed. Initial verification seed: 2.
- Implementation revision: `declarative-nodes/2026-10-19`. The separately
  verified earlier Windows ZIP does not contain this later source change.

### Land denial and Engine target references — 2026-10-19

- Live local events showed a Wasteland being sacrificed and its ability later
  fizzling, with no intervening target-changing action. This is consistent with
  self-targeting, but that event record does not retain the exact declared target.
- Reproduced a target-view mismatch: the current Engine emits nested enum
  references (`permanent`, `player`, `card`, `stackObject`), while the installed
  SDK's convenience properties recognized only flat `kind` references. The
  reference agent now adapts its read-only SDK views without modifying legal
  action payloads, IDs, the SDK checkout, or Engine rules. Stack-protection
  evaluation uses the same adapter; its regression cases now use real Engine
  target serialization.
- Land destruction evaluates opposing color-source redundancy, public color
  demand, remaining land count, utility, static big-mana output, custom combo
  tags, fetch-land escape risk, and the opportunity cost of sacrificing our own
  mana source. It never reads hidden opposing hand/library identities to infer
  color demand. Activated mana production is distinguished from destruction.
- Existing weighted nodes and card/tag patterns control these contributions.
  Own-land destruction has a strong weighted resource penalty, not a legality
  ban, so deliberate deck-specific exceptions remain possible. No policy schema
  or private user agent file is migrated or overwritten.
- `scripts/verify_land_strategy.py` starts its own isolated Engine, compiles the
  real Wasteland rule, receives actual legal actions, avoids the self-target,
  submits an opposing Otawara target, and verifies that the Engine destroyed it.
  It does not attach to, restart, or play a user's table. The first successful
  verification used seed 2. The ten-deck classification check remains 10/10;
  that is not evidence of improved match win rate.
- Implementation revision: `land-denial-targets/2026-10-19`. Already-running
  Python workers keep their loaded implementation; new workers use this one.

Only revisions that improve the regression suite and the ten-deck benchmark should receive a new
model version. An agent policy records the exact `modelVersion`, node multipliers, archetype mix,
patterns, temperature, and seed needed to reproduce its decisions.

## strategy-graph/v1 — 2026-10-17

- Added weighted concerns for resources, tempo, card advantage, board development, clocks, combo
  assembly and disruption, protection, combat, timing, and mulligans.
- Added soft aggro, tempo, midrange, control, and combo deck profiles.
- Added contextual beatdown/control role assignment and board-needs estimates.
- Added functional-opening-hand evaluation and London-mulligan bottoming priorities.
- Added last-useful-moment timing for instants, permanent target valuation, combat trades, lethal,
  and crackback safety.
- Added bounded user/LLM patterns that can tag cards and adjust known graph nodes without executing
  generated code.
- Added reproducible, score-bounded decision noise.
- Established a ten-deck Legacy classification benchmark and focused decision regression tests.

### Kept improvement: mulligan, sequencing, and action compaction

Using identical deck pairings and seeds, the Engine evaluation moved from 3 wins, 3 losses, and
4 turn-limit draws to 5 wins, 2 losses, and 3 draws. This revision is retained because it also
passes the deterministic decision suite and the 10/10 deck-family benchmark.

- Fixed a sunk-cost error that encouraged repeated mulligans and added a four-card floor.
- Added colored-source-aware land sequencing and lower-value payment-source selection.
- Added strategic card selection/order for searches, hand replacement, discard, and combo surveil.
- Collapsed equivalent mana-payment permutations before scoring; a 5,001-action regression case
  now reduces to two strategic candidates without choosing an illegal action.
- Isolated deck adaptation by player so concurrent League matches do not overwrite one another.

### Stack target alignment — 2026-10-18

- Reproduced Force of Will countering our own Show and Tell across all five archetypes and
  decision temperatures 0, 0.05 and 1. The old stack-urgency bonus ignored target control.
- Only opposing stack objects count toward the need to answer the stack. Countering our own
  spell now carries a weighted resource-loss cost, rather than an unconditional action ban.
- Countering an actual opposing response targeting our spell/permanent/player receives the
  protection bonus. Copying a friendly spell is not treated as countering it.
- Unknown targets/controllers and explicitly uncounterable objects receive no counter reward.
- Reserve scarce countermagic for consequential threats and actual combo protection. A cheap
  opposing cantrip carries extra opportunity cost; spare counters reduce that cost. A specific
  `must_answer` card/tag rule can override the low-threat classification.
- Card/tag pattern adjustments also inspect stack targets; target tags no longer reclassify
  the counter itself as a combo payoff or other target role.
- Priority explanations identify the implementation as `stack-target-alignment/2026-10-18`.
  Local replay frames retain selected targets, stack targets and uncounterability for diagnosis.
  The v1 policy schema remains compatible; existing user weights and Python are untouched.

The standalone French diagram is `apps/learner-web/public/assets/strategy-graphs.html`.
It runs offline and is served at `/assets/strategy-graphs.html` after a frontend build. Its
archetype matrix is checked against the actual Python node weights by a regression test.
