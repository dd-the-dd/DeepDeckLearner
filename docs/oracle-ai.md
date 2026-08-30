# Oracle AI

DeepDeckLearner owns the trainable Python side of the Magic-playing agent. The
code lives in `src/oracle_ai`; Engine remains a separately versioned rules and
game-session dependency.

It also contains the public `oracle_ai.agents` SDK for rule-based and learned
agents. The SDK connects to Rust over `/ai/agents/ws`, negotiates a typed agent
manifest, reconstructs full or delta player observations, and exposes one
async callback per protocol request. `OracleModelAgent` runs existing V11 or
V12 checkpoints through this interface while tensor creation remains in
Python. The public connection layer is maintained in
[`DeepDeckAgent`](https://github.com/dd-the-dd/DeepDeckAgent).

## Authority boundary

Rust remains authoritative for game state, legal actions, decision revisions,
action validation and state mutation. Python receives a versioned decision packet
and returns one action ID from the exact offered set. The structured V2 encoder
uses only public objects plus the deciding player's hand; opposing hands and
library identities are ignored even though the current engine packet still
contains them.

## Implemented trainable V1

The package now includes:

- deterministic feature hashing for arbitrary structured state and action JSON;
- a PyTorch Transformer encoder;
- a dynamic policy head with exactly one logit per legal action;
- a state-value head;
- stochastic training and deterministic inference;
- generalized advantage estimation and clipped PPO updates;
- a Gym-like adapter over Rust `/game/sessions`;
- seat-swapped, seeded matchup sampling;
- checkpoint manifests, optimizer state and training resumption primitives;
- an in-process smoke environment proving that collection, PPO and checkpointing work end to end.

The hashing encoder is a transitional `hashing-observation/v1` implementation. It makes the architecture trainable now while preserving the same model/environment boundary that the richer Rust `for_model` schema will later use.

## Structured V2 learner

Legacy V1 checkpoints remain loadable. A learner configured with
`architecture: structured-v2` uses a separate `structured-observation/v2`
checkpoint family with:

- one game-configuration token and one cyclic game-phase token;
- one numeric summary token per player, including zone, mana, permanent and stack counts;
- one numeric token per visible hand card, permanent and stack object;
- one word-encoded token per parsed Oracle rule;
- one token per legal action;
- learned relative-player embeddings anchored on the active turn player;
- learned token-type embeddings;
- learned word-order embeddings inside every text token;
- nonnegative scalar normalization `x / (x + 20) - 1`, clamped to `-1` below zero.

The default V2 Transformer has 23.7 million parameters: width 384, six layers,
eight attention heads and a 1536-unit feed-forward block. Checkpoint manifests
select V1 or V2 dynamically, so one model registry can serve the unchanged V1
ground truth and V2 candidates or promoted champions simultaneously.

The seven token categories are `GAME_CONFIGURATION`, `GAME_PHASE`,
`PLAYER_STATS`, `CARD_STATS`, `PERMANENT_STATS`, `ORACLE_TEXT`, and
`LEGAL_ACTION`. BLAKE2b is only the deterministic mapping from a normalized word
to a vocabulary bucket; the vectors in `nn.Embedding` are learned. This preserves
checkpoint portability but does not give semantically related words a useful
initial proximity. A future domain tokenizer should use a versioned
SentencePiece/BPE vocabulary trained on Oracle text instead of hashed whole words.

## Structured V3 learner

`architecture: structured-v3` keeps the V2 token schema while replacing the
discrete relative-player lookup with a cyclic coordinate projected into the model
width. The player making the decision is always angle zero. For `N` players, a
seat at relative offset `k` uses `theta = 2*pi*k/N` and contributes
`[sin(theta), cos(theta)]`. This yields 180 degrees in two-player games, 120/240
degrees in three-player games, and 90/180/270 degrees in four-player games.

A supported V2-to-V3 resume migration preserves all compatible learned weights,
the training step, and the previous no-player embedding. It initializes the new
cyclic projection from a least-squares approximation of the V2 seat embeddings
and resets only the optimizer state.

PPO can also apply training-only partial-observation dropout. Whole nonessential
state tokens, individual word IDs, and individual numeric values have separate
rates. Game configuration and phase tokens are never removed, legal-action tokens
are never masked, and the exact masked state used to choose an action is stored in
the PPO trajectory. Inference and fixed-seed evaluation remain unmasked.

## Semantic V4 learner

`architecture: structured-v4` preserves the V3 Transformer dimensions and cyclic
player encoding, but removes card names and human-facing action labels from the
observation. Visible cards are represented by numeric characteristics, type, mana
cost, and the structured Oracle rules emitted by Rust. Legal actions also include
the semantic fragments of their source and target Oracle models, such as trigger,
cost, effect, filter, and keyword nodes.

A supported V3-to-V4 resume migration copies every model weight and the training
step while resetting the optimizer. Tests enforce that renaming a visible card
does not change V4 word tokens, while changing its parsed Oracle rules changes
both state and action tokens.

## Action-conditioned V5 learner

`architecture: structured-v5` keeps the V4 Oracle tokens and state Transformer,
then lets every legal-action token cross-attend the full encoded state through a
configurable number of action-conditioning layers. The decision-context token is
added to every action query, so mulligan, priority, combat, blocking, and
resolution choices can attend to different state evidence before scoring.

The V4-to-V5 migration preserves every V4 parameter and training step. The new
contextual score is gated as a zero-initialized residual over the exact V4 logits,
which prevents an architecture upgrade from replacing a learned policy with a
random action head. Model and observation dropout can then train the new path
without changing V4 checkpoints.

## Planning-ready V6 learner

`architecture: structured-v6` combines the structured Transformer with an
AlphaZero-inspired root policy-improvement step. The Transformer emits priors,
a state value and one `Q(s,a)` estimate per legal action. PUCT converts those
outputs into visit counts; self-play samples from the visit distribution and
trains the policy toward that improved target instead of directly imitating its
own logits.

V6 also replaces Blake2b word buckets with a compact contextual semantic
Transformer. Its tokenizer uses collision-free structural tokens and UTF-8 byte
fallbacks, includes public graveyard cards and the latest game events, and
returns action-specific attention labels and activation norms for diagnostics.
The current search is root-only. Full multi-step MCTS requires branchable Rust
session snapshots and remains the next planning extension.

## Strategic V7 learner

`architecture: structured-v7` adds a permutation-stable representation of the
deciding player's known deck and pools those card/rule tokens into a learned deck
latent. A strategic-plan latent combines that deck representation with the
current state and decision context, then conditions every legal-action score,
the per-action value estimates, and the state-value critic. Card names remain
excluded from semantic observations, so V7 learns parsed Magic concepts rather
than a card-name lookup table.

The initial V7 league freezes the existing `structured-v6` step-zero checkpoint
as `ia-gt-0`; the identifier is retained for league protocol compatibility, but
the baseline is V6 rather than the legacy hashing model. This first trainable
slice implements the V7.1 deck latent and strategic conditioning described by
PR #71. Persistent plans, opponent belief, a learned world model, and multi-step
MPC remain later V7 curriculum stages.

## Consequence-planning V9 learner

`architecture: structured-v9` keeps the V7 state, Oracle, action, event, and
known-deck tokens while adding three explicit strategic latents: deck capability,
opponent belief, and a gated plan carried between decisions by the same player.
Every legal action predicts a probability distribution over 23 public board and
flow statistics for four future decision horizons and up to four relative player
seats. Hand state is separated into retained cards, newly acquired cards, and
total cards. The predicted means and uncertainties are embedded back into both the
policy and `Q(s,a)` heads.

V9 trains from terminal reinforcement-learning rewards while using low-weight
Gaussian future-prediction, opponent-belief, and temporal-plan consistency
losses. It deliberately bypasses V6 root PUCT, so the direct action-conditioned
policy must learn to use its consequence model. The separate
`ia-v9-in-training` service uses port `8792`; V8 remains available unchanged on
`ia-in-training`.

V10 can collect a frozen-policy rollout batch through multiple independent Rust
sessions. `parallelGameWorkers` controls concurrent games and
`rolloutBatchGames` controls how many completed trajectories feed one PPO update.
Evaluation boundaries still occur on the configured exact game counts.

Training records include a compact `behavior` summary. Evaluation records add
the same metrics per game and across the period: policy confidence and entropy,
decision/action distributions, land/cast/attack opportunities taken, and explicit
`criticalMulliganToOneOrLess` and `mulliganToZero` anomaly counters.

## Install and test

```bash
python -m pip install -e ".[deep-learning,dev]"
python -m pytest tests/oracle_ai
```

## Smoke training

This runs without the Rust server and writes a real PyTorch checkpoint:

```bash
oracle-ai-train \
  --config configs/oracle-ai/train-smoke.yaml \
  --smoke
```

## Training against Rust

1. Start the Rust engine server on `127.0.0.1:8787`.
2. Replace each placeholder `setup.players` in `configs/train-v1.yaml` with a valid versioned `GameSetup` payload.
3. Ensure every non-learner player is configured as an automatic opponent.
4. Run:

```bash
oracle-ai-train --config configs/oracle-ai/train-v1.yaml
```

The trainer samples matchups, generates independent seeds, swaps seats every episode, collects authoritative trajectories and periodically writes:

```text
runs/oracle-ai-v1/
├── resolved-config.json
└── checkpoints/
    └── step-<n>/
        ├── checkpoint.pt
        └── manifest.json
```

## Self-play league training

`oracle-ai-league-train` runs shared-policy self-play: every player in a training
game is controlled by the current learner and contributes a player-relative PPO
trajectory. Training seeds come from a deterministic non-repeating 64-bit stream.
Set `continuous: true` to train without an episode limit.

The V6 league samples a fresh setup for every training episode. It independently
chooses Free or Commander, two to four players, the starting player, and every
seat's deck with replacement, so mirror matchups remain possible. Free games also
sample starting life from 20 to 40 and zero to two free mulligans. Commander games
use only validated 100-card Commander decks, force 40 life and one free mulligan.
The randomizer state is resumed through `matchupRandomizerSkip`; evaluation
scenarios and seeds remain fixed.

Evaluation is separate from training:

- the learner publishes a persistent `ia-in-training` inference service;
- the ground-truth registry publishes every frozen `ia-gt-N` model through one service;
- both services stay online while checkpoints and promoted models are hot-reloaded;
- the candidate is deterministic during evaluation;
- `ia-gt-0` is the frozen V6 step-zero baseline for the V7 league;
- the same reserved evaluation seeds are reused at every evaluation period;
- reserved evaluation seeds are never issued to training;
- `trainingSeedSkip` advances the unique training stream when resuming a run;
- `matchupRandomizerSkip` resumes the matchup sequence at the same attempt;
- scenarios can vary player count, candidate seat, starting player and candidate deck;
- reports include wins, losses, draws, errors, turns to win, decisions and service latency;
- three consecutive 100% evaluation periods freeze the structured candidate as
  `ia-gt-(N+1)` and make it the next evaluation ground truth while preserving
  `ia-gt-0` as a selectable V6 baseline controller;
- every report identifies the exact opponent version.

The league output contains checkpoints, promoted champions, service logs, JSONL
training and evaluation metrics, the fixed seed manifest, current league state,
the dynamic `model-registry.json`, and both `learning-curve.csv` and
`learning-curve.svg`. Rust discovers `/v1/models` and exposes the available
`ai-random`, `ia-gt-N`, and `ia-in-training` controllers to the browser through
`GET /ai/controllers`; adding a new ground-truth version requires no UI change.

Compact matchup entries can reference a JSON deck catalog instead of embedding
every card repeatedly:

```yaml
deckCatalog: path/to/compiled-decks.json
trainingSeed: 1729086421
trainingSeedSkip: 0
matchupRandomizerSkip: 0
continuous: true
evaluationEvery: 5
serviceRefreshEvery: 1

trainingMatchups:
  - id: mobilize-vs-sektuar
    decks: [Mobilize, "Sek'tuar"]
    startingPlayer: 0
    freeMulligans: 1

evaluation:
  seed: 456498132
  gamesPerScenario: 2
  perfectPeriodsForPromotion: 3
  championPort: 8790
  candidatePort: 8791
  device: cpu
  scenarios:
    - id: evaluate-mobilize
      decks: [Mobilize, "Sek'tuar"]
      candidatePlayerId: player-1
      candidateDeck: Mobilize
```

The current eight-deck league rebuilds its catalog from the browser's saved
sessions, then trains across every distinct two-, three-, and four-player deck
lineup with zero, one, or two free mulligans:

```bash
npm run ai:decks
oracle-ai-league-train --config configs/oracle-ai/league-v5-eight-decks.yaml
```

This produces 462 randomized training matchups. Each fixed evaluation period
covers every candidate deck once at two, three, and four players (24 scenarios),
and runs after every 20 completed self-play games. Follow progress with:

```powershell
Get-Content runs/oracle-ai-league-v5-eight-decks/training.jsonl -Wait -Tail 3
```

Run the league while Rust listens on the configured `engineUrl`:

```bash
oracle-ai-league-train --config path/to/league.yaml
```

## Learner-owned inference

DeepDeckLearner starts and monitors Python inference services. Engine only calls
the configured HTTP or agent-protocol endpoint and never starts Python, selects
a model version, or reads a checkpoint. The ground-truth registry can listen on
`8790` and the current training snapshot on `8791`; the league trainer reuses
compatible services and hot-reloads the stable checkpoint.

The workbench environment lives at `.venv`. Model registries, live checkpoints,
logs, and trajectories stay under the ignored `.deepdeck/` or `runs/`
directories. Learner owns device selection, service lifecycle, and the capacity
reserved for playtesting.

League training releases completed trajectories from memory and clears the CUDA
cache between episodes. After a CUDA out-of-memory error it clears gradients and
cached tensors, then waits for `cudaOutOfMemoryBackoffSeconds` (30 seconds by
default) before retrying.

## Serve a checkpoint

```bash
ORACLE_AI_CHECKPOINT=runs/oracle-ai-v1/checkpoints/step-1000 \
python -m uvicorn oracle_ai.app:app \
  --app-dir src \
  --host 127.0.0.1 \
  --port 8790
```

Without `ORACLE_AI_CHECKPOINT`, the API serves an explicitly named untrained Transformer. It remains legal-action-safe but is not a meaningful opponent.

## API

```text
GET  /health
POST /v1/decisions
```

The local application boundary is documented in the
[DeepDeckLearner architecture](epics/2026/08-august/deepdeck-learner/06-architecture-deepdeck-learner.md).
