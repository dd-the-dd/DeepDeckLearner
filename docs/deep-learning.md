# Train the V11 and V12 examples

This is a small, public learning pipeline: visible observations, exact legal
actions from Rust, deterministic feature encoding, a PyTorch policy, supervised
training, checkpoints, and SDK inference. No private weights or match history are
included.

## Magic state to tensor

At each decision, Rust provides two important objects:

1. the game state visible to this player;
2. the exact list of legal actions at that moment.

`DecisionEncoder` converts each candidate decision into three feature streams:

- **state features**: turn, phase, life, zones, visible permanents, stack, and
  other observable facts;
- **delta features**: changes and events since the previous observation;
- **action features**: action kind, source, target, cost, and other declared
  option fields.

Known cards from the agent's own deck may be included as context. Hidden cards
from an opponent must never be inserted. Compact deterministic feature hashing
keeps this starter independent of a private vocabulary. The checkpoint records
the feature size; changing the schema means training a compatible checkpoint.

The policy does not generate free-form commands. It produces one logit for every
legal action, then the SDK returns the identifier with the selected score.

## Model families

V11 separately encodes visible state and deltas, keeps a `GRUCell` memory between
decisions, scores the dynamic action set, and predicts up to four player values.

V12 keeps the same policy structure but predicts a bounded scalar `V(s)`. Its
value output is `[V(s), -V(s)]`, which makes this public example a two-player
starting point.

## Verify the pipeline without data

Use the workbench or the CLI:

```powershell
deepdeck-train v11 --smoke --epochs 2 --output runs/v11-smoke
deepdeck-train v12 --smoke --epochs 2 --output runs/v12-smoke
```

Each checkpoint contains `config.json` and `model.pt`. The loader uses
`torch.load(..., weights_only=True)`. Checkpoints, runs, and common weight files
are ignored by Git.

## JSON Lines trajectory format

One line is one decision:

```json
{"observation":{"turnNumber":1,"step":"precombatMain","players":[{"id":"p1","life":20},{"id":"p2","life":20}]},"legalActions":[{"id":"land","kind":"playLand"},{"id":"pass","kind":"passPriority"}],"chosenActionId":"land","valueTargets":[1.0,-1.0]}
```

- `observation`: the visible Engine projection;
- `legalActions`, or `decision.options`: exact actions offered by Rust;
- `chosenActionId`: an ID present in that action list;
- `previousObservation`: optional previous visible observation;
- `knownDeck`: optional definitions from the agent's own known deck;
- `valueTargets`: optional outcome/value targets by relative seat.

Train and resume without mutating the source checkpoint:

```powershell
deepdeck-train v12 `
  --dataset data/legacy-decisions.jsonl `
  --epochs 10 `
  --learning-rate 0.0003 `
  --output runs/my-v12

deepdeck-train v12 `
  --dataset data/new-decisions.jsonl `
  --resume runs/my-v12 `
  --output runs/my-v12-next
```

## What the baseline optimizes

The trainer applies cross-entropy behavior cloning to `chosenActionId` and MSE
regression to `valueTargets`. This is a baseline, not a required algorithm. PPO,
tree search, self-play, and offline RL can reuse:

- `DecisionEncoder.encode(...)` for tensor construction;
- `PolicyV11.forward(...)` or `PolicyV12.forward(...)` for logits and values;
- `save_checkpoint(...)` and `load_checkpoint(...)` for inference compatibility;
- `DeepLearningAgent` for local or hosted inference.

An inference match never changes weights. Learning requires an explicit dataset
or collector, reward definition, and trainer call.

## Hosted and deck-driven learning

The convenient target flow is: select formats/decks, collect complete self-play
trajectories, train one learner while other agents stay in inference, then test
the checkpoint against a human locally. The first public workbench exposes this
shape but enables it only when `trajectory-v1` is implemented.

That contract must version observations, legal actions, chosen actions, terminal
placements, rewards, encoder schema, Engine revision, and deck content hashes.
Hosted replay storage alone is not automatically a valid learning dataset.
