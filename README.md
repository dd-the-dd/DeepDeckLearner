# Deep Deck Agent Examples

This repository contains four public agents designed to be easy to understand and modify:

- `random` selects a random legal action with a reproducible seed;
- `alexios` follows an explicit priority list to play the **Alexios, Deimos of Kosmos**
  deck quickly;
- `v11` demonstrates a recurrent PyTorch network with multiplayer value estimates;
- `v12` adapts that policy to an antisymmetric two-player value.

They use the
[`DeepDeckAgent`](https://github.com/dd-the-dd/DeepDeckAgent) package. The Rust engine
remains authoritative for the rules and rejects any action outside the legal-action list.

## Local installation before the PyPI release

Place both repositories in the same directory:

```text
Projects/
├── DeepDeckAgent/
└── DeepDeckAgentExamples/
```

Then run:

```powershell
cd DeepDeckAgentExamples
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e "../DeepDeckAgent[dev]"
python -m pip install -e ".[dev]" --no-deps
pytest
```

Install the optional PyTorch dependencies for the V11/V12 examples:

```powershell
python -m pip install -e ".[deep-learning]"
```

The random baseline and Alexios agent do not require PyTorch.

## One program, two targets

The same demo can serve a local engine or join Deep Deck League matchmaking:

```powershell
# Wait for a game created by the local engine.
deepdeck-example alexios --target local

# Connect to the public service, queue, play, and queue again after the match.
deepdeck-example alexios --target ddl
```

To create the local game as well, provide two deck IDs already imported into the engine:

```powershell
$env:DEEPDECK_LOCAL_DECK_SESSION_ID = "alexios"
$env:DEEPDECK_LOCAL_OPPONENT_DECK_SESSION_ID = "commander-opponent"
deepdeck-example alexios --target local --start-local-game --local-format commander
```

The first seat uses the example's WebSocket controller. The second uses `ai-random` by
default; use `--local-opponent-controller` to select another controller.

## Random baseline

```powershell
deepdeck-example random --target local --speed 100ms --seed 42
```

The seed makes the choice sequence reproducible. The agent never invents actions and
cannot see hidden opponent cards.

## Alexios agent

```powershell
$env:ALEXIOS_DECK_ID = "alexios"
deepdeck-example alexios --target local --speed 1s
```

Its current policy is intentionally simple, deterministic, and programmatic:

1. keep a hand with at least three lands; otherwise, mulligan;
2. play an available land;
3. cast Alexios as soon as the engine allows it;
4. respond with redirection when an opposing stack object targets Alexios;
5. during the agent's main phase, equip Alexios as much as possible while reserving the
   estimated mana for redirection or a lethal combat trick;
6. remove an opposing creature first when it is strong enough to kill Alexios in combat;
7. cast equipment, then creatures, then other permanents;
8. use remaining removal on the strongest opposing creature;
9. when a goad effect is available, target the strongest opposing creature;
10. hold an instant-speed boost when it can turn an Alexios attack into a surprise kill;
11. sacrifice Food and Clue tokens only when no higher-priority action remains;
12. attack with Alexios first and prefer opponents with lower life totals.

The mana and lethality estimates are intentionally basic. This example shows where to
write strategy code; it is not presented as a competitive agent.

The complete implementation is in
[`src/deepdeck_examples/alexios.py`](src/deepdeck_examples/alexios.py).

## V11 and V12 deep-learning examples

This repository contains no model weights. Git ignores `*.pt`, `*.pth`, `*.ckpt`, `runs/`,
and `checkpoints/`. Train a checkpoint or explicitly provide one before playing:

```powershell
# Two bundled samples verify the end-to-end pipeline.
deepdeck-train v12 --smoke --epochs 2 --output runs/v12-smoke

# Run local inference with the generated checkpoint.
deepdeck-example v12 --target local --checkpoint runs/v12-smoke
```

`--allow-untrained` permits random weights only for protocol and connection testing.
Without a checkpoint, the command normally refuses to start V11 or V12.

The examples preserve the important design ideas from the internal versions:

- V11 separately encodes visible state and changes since the previous decision, maintains
  GRU memory, assigns a logit to every dynamic legal action, and predicts up to four player
  values;
- V12 uses the same policy but produces one zero-sum value `V(s)` and exposes
  `[V(s), -V(s)]`, so this public example is limited to two-player Legacy games.

The public encoder uses compact feature hashing and does not depend on a private vocabulary
or data file. It is a trainable starting point, not a checkpoint-compatible copy of the
private production model and not a promise of playing strength.

The JSON Lines format, training objectives, checkpoint resume behavior, and extension
points are documented in [`docs/deep-learning.md`](docs/deep-learning.md).

## Deep Deck League public matchmaking

After creating an agent and generating its key under **Account → Autonomous agents**:

```powershell
Copy-Item .env.example .env
# Fill in the key, agent slug/version, and the three matchmaking UUIDs.
deepdeck-example alexios --target ddl --speed 1s
```

The `.env` file loads automatically, so you do not need to export each variable in
PowerShell.

Never copy the engine's global key into this repository. A public signing key may identify
an official version, but every user must connect with their own DDL account and revocable
key. The configuration's `agent_id` must match the slug of the agent linked to that key.

The public command connects the WebSocket first, joins the queue, makes decisions, and
queues again after each match. It can run as a background service without a browser and
without `gcloud auth login`. Add `--once` to stop after one game.

## Modify the strategy

The most important method is `choose_priority`. Each block returns an `Action` already
declared legal by Rust. Tests use small, readable states and can be run after every change:

```powershell
ruff check .
mypy
pytest
```

## Repository ownership

The code is public, readable, and forkable. Only `@dd-the-dd` has write access and can
update `main`; everyone else can propose a pull request without receiving push access.

The protection procedure is documented in [`docs/publishing.md`](docs/publishing.md).
