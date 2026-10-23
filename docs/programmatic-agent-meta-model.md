# Programmatic agent meta-model

Status: `strategy-graph/v1` implemented; advanced confidence, multiplayer, and offline
optimization items below remain the roadmap.

## Objective

DeepDeckLearner should provide a strong deterministic agent before a user writes
deck-specific code. An optional LLM translates the user's strategic intent into
a bounded policy, but never chooses actions during a game. The runtime always
ranks actions declared legal by DeepDeckEngine.

This separation provides three properties:

- the agent works without an LLM connection;
- the same state, policy, and seed produce the same decision;
- every decision can expose the contributing models and scores.

## Decision pipeline

```text
Engine observation and legal actions
              |
              v
     typed strategic features
              |
              v
  specialized deterministic models
              |
              v
 weighted meta-model and context gates
              |
              v
 rank legal actions + decision explanation
```

Engine remains authoritative for rules, continuous effects, legality, targets,
and state mutation. Learner consumes versioned typed facts; it should not infer
rules from card-name strings when Engine can expose a semantic fact.

## Specialized models

### Board-state evaluator

Produces normalized advantage and risk values from public game state:

- material and permanent quality;
- usable mana this turn and next turn;
- cards in hand and expected access to cards;
- life, poison, library size, and other loss conditions;
- stack control and available interaction;
- board resilience and exposure to sweepers;
- known and inferred information, with separate confidence values.

It must keep value and tempo separate. A player can be ahead on material while
being behind on the active clock.

### Strategic-needs evaluator

Produces a `0..1` urgency vector on every priority decision:

```text
develop_board
draw_cards
remove_permanent
answer_stack
protect_advantage
stabilize_life
advance_win_condition
preserve_resources
```

Needs are contextual. For example, `develop_board` decreases when the current
clock already wins quickly, the opponent is likely to have a sweeper, or casting
a permanent would consume required interaction mana.

### Clock detector

A clock is a credible path to winning or losing with an estimated number of
turns and a confidence value. Detectors are composable:

- combat damage and commander damage;
- repeated non-combat damage or life loss;
- poison counters;
- mill and forced draw;
- alternate win or loss conditions;
- deterministic or probabilistic combo patterns.

Each detected clock records its components and disruption points. A Dark Depths
pattern, for example, identifies both the enabler and payoff and can value a
Wasteland action by the clock it breaks, not merely by the permanent it removes.

The meta-model compares our clock with every opposing clock. When our clock is
faster and sufficiently robust, `protect_advantage` and `preserve_resources`
increase. When an opposing clock is faster, disruption and stabilization gain
urgency.

### Threat and removal evaluator

Every opposing spell or permanent receives independent scores for:

- immediate impact;
- future value per turn;
- combo centrality (enabler, bridge, or payoff);
- clock acceleration;
- difficulty of answering later;
- recursion and replacement risk;
- collateral cost of the available answer.

Deck policies can increase the weight of combo centrality. This supports a user
request such as "remove combo pieces even when another permanent has more raw
value" without hard-coding individual card names into the shared model.

### Combat evaluator

Combat is evaluated per legal attack or block configuration rather than with a
single global aggression switch. Features include:

- damage and resulting clock delta;
- attacker survival, trade quality, and death value;
- evasion and legal blockers;
- attack and damage triggers;
- lethal and prevent-lethal conditions;
- crackback risk after attackers tap;
- mana and cards left for combat tricks or interaction;
- planeswalker and battle targets;
- multiplayer retaliation and threat exposure.

The UI may show a recommendation per creature, but the scorer must evaluate the
combined attack configuration because two individually safe attacks can create
an unsafe crackback.

### Resource and sequencing evaluator

Ranks mana use, land sequencing, timing, and holding priority. It penalizes an
otherwise valuable action when it consumes mana or cards required by a more
urgent model. It also values waiting when an instant-speed line preserves more
information or options.

## Weighted meta-model

Every legal action receives a feature vector from the specialized models. The
initial combination can remain transparent:

```text
score(action) = base_quality(action)
              + sum(policy_weight[i] * action_feature[i])
              + context_adjustments(action)
              - risk_penalties(action)
```

Feature families are normalized before weighting so a large raw card-value
number cannot accidentally dominate a clock or lethal feature. Hard gates are
reserved for correctness and exceptional priorities such as taking a forced
legal action, executing certain lethal, or preventing certain lethal.

Weights should normally use `0..2`, where `1` is the strong reusable baseline.
Expert-only negative or wider ranges can be added later after evaluation shows
that they remain stable.

Recommended policy families:

```text
board.develop
cards.draw
interaction.remove_permanent
interaction.answer_stack
interaction.combo_disruption
advantage.protect
resources.preserve
clock.advance
clock.disrupt
combat.attack
combat.trade
combat.survival
```

## Policy owned by the user

The ignored local agent directory stores policy, versions, and explanations.
The shared implementation and strong defaults remain committed.

```json
{
  "schemaVersion": "deepdeck-programmatic-agent/v1",
  "strategyGraph": {
    "version": "strategy-graph/v1",
    "archetypeMix": {
      "aggro": 0.05,
      "tempo": 0.20,
      "midrange": 0.10,
      "control": 0.15,
      "combo": 0.50
    },
    "deckAdaptation": 0.65,
    "weights": {
      "combo_disruption": 1.9,
      "protect_advantage": 1.6,
      "board_development": 0.8,
      "card_advantage": 1.1,
      "combat_survival": 0.7
    },
    "temperature": 0.15,
    "seed": 42,
    "patterns": [
      {
        "id": "remove-named-combo-piece",
        "cardNames": ["Example Engine"],
        "addTags": ["combo_payoff"],
        "nodeAdjustments": {"combo_disruption": 1.0}
      }
    ]
  }
}
```

Every revision is retained under `.deepdeck/agents/<id>/revisions/` with its
`revisionReason`. The active copy remains `agent.json`. Replays and evaluations can reference
the agent id, revision, strategy-model version, temperature, and seed.

## LLM boundary

The LLM receives bounded tools instead of filesystem or Python execution:

- inspect a downloaded deck and its detected roles;
- inspect the model and weight catalog;
- inspect an agent policy and recent decision evidence;
- propose a typed weight or targeting-rule patch;
- simulate the patch on saved decisions;
- compare the old and proposed policy;
- apply a confirmed patch as a new revision;
- explicitly start a playtest when requested.

The controller validates identifiers, ranges, selectors, and policy size. The
LLM cannot create an unknown model, bypass legality, or silently apply a policy.
Python generation remains an advanced extension built from approved interfaces;
the strong meta-model is the default fallback for every generated agent.

## Explanation contract

Each chosen action should optionally emit a compact decision record:

```json
{
  "action": "activate Wasteland targeting Thespian's Stage",
  "score": 235,
  "alternatives": [
    {"action": "cast Murktide Regent", "score": 118}
  ],
  "contributions": [
    {"model": "combo_disruption", "value": 182},
    {"model": "protect_advantage", "value": 74},
    {"model": "resource_cost", "value": -21}
  ],
  "clocks": {
    "ours": {"kind": "combat", "turns": 3, "confidence": 0.91},
    "opponent": {"kind": "combo", "turns": 1, "confidence": 0.96}
  }
}
```

Full records are optional because they consume more replay storage. The replay
quota manager can remove old detailed traces while retaining compact replays.

## Evaluation requirements

### Declarative custom decision nodes

The implemented `strategyGraph.customNodes` extension adds up to 32 weighted
nodes without Python expressions, imports, file access or model calls during
play. Nodes are stored in the private policy, revisioned and restored with it.
An omitted `customNodes` field preserves existing nodes; `[]` explicitly removes
them. Existing policies without nodes retain their scores and fingerprint.

Each node has an `id`, `name`, `enabled`, `weight` (0–2), `bias` (−1–1), `when`
conditions and up to 12 `inputs` of `{from, weight}` (edge weight −2–2). Inputs
refer to `need.*`, `state.*` and `hand.*` signals listed by `list_heuristics`,
`base.<concern>` (that action's summed base contribution divided by 100 and
clamped to ±1), or `node.<id>`. References are validated and the graph is
topologically ordered; cycles, duplicate edges and unknown signals are rejected.

Activation is `clamp(bias + sum(input × edgeWeight), −1, 1)`. A matching node
adds `100 × activation × nodeWeight` to the action score; its outgoing signal
is `clamp(activation × nodeWeight, −1, 1)`. Disabled/nonmatching nodes output
zero. `when` filters can restrict action kinds, turn, current strategic role,
source names/tags and target names/tags/controller. Target conditions must
match the same card, including the stack object's actual controller. Names
are case-insensitive exact matches; required tags are case-sensitive AND filters.

For example, a draw rule can require source tag `card_draw`, action `castSpell`,
zero bias and input `{from: "need.draw_cards", weight: 1}`. A separate rule can
favor interaction targeting an opponent's `combo_enabler` tag. Card/tag rules
assign deck-specific tags first. Inputs can chain these nodes, independently
of their order in a submitted policy.

The workshop offers forms, examples and a compact input → node → score view;
the copilot uses the same validated `create_agent`/`update_agent` tools.
Changes remain drafts until revision-checked saving. No automatic win-rate
improvement is claimed. Nodes affect priority, attacking, blocking, opening
keep/mulligan and bottom-card decisions, not legality, resolution choices or
sideboarding. Positive bottom-card scores favor bottoming; mulligan nodes
adjust the keep score relative to its baseline threshold, retaining the
four-card-hand safety guard.

Before a policy becomes the active revision, run:

1. deterministic unit scenarios for lethal, prevent-lethal, clocks, removal,
   combat, and resource preservation;
2. regression decisions extracted from protected replays;
3. mirrored-seat games against reference agents;
4. comparison against the previous revision, with confidence intervals;
5. latency and invalid-action checks.

The application should report behavioral changes, not only win rate. Relevant
metrics include missed lethal, failed clock disruption, unused mana, removal
target quality, attack regret, and decisions that changed between revisions.

## Delivery slices

1. Define typed feature, clock, need, action-score, and explanation contracts.
2. Implement board, combat-damage clock, removal, and combat evaluators.
3. Integrate the weighted meta-model into the programmatic reference agent.
4. Add combo-role annotations and non-combat clock detectors from Engine facts.
5. Give the chat bounded policy-patch and simulation tools.
6. Calibrate the baseline on saved scenarios and reference-agent leagues. (Initial 10-deck
   classifier and Engine-vs-random evaluation implemented.)
7. Add optional offline weight optimization while preserving explanations.
