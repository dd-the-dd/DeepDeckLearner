# DeepDeckLearner

DeepDeckLearner is the public AI laboratory for Deep Deck. It combines readable
rule-based agents, trainable V11/V12 examples, and a local React workbench for
training and behavior tests. It contains no private model weights.

DeepDeckEngine remains authoritative for Magic rules and legal actions. The
workbench consumes versioned sources from
[`DeepDeckEngine`](https://github.com/dd-the-dd/DeepDeckEngine),
[`DeepDeckPixi`](https://github.com/dd-the-dd/DeepDeckPixi), and
[`DeepDeckAgent`](https://github.com/dd-the-dd/DeepDeckAgent) as pinned Git
submodules. It does not copy their code.

## Quick start for a Magic player

Requirements: Git, Python 3.10+, Node.js 22.13+, and PowerShell 7.

```powershell
git clone --recurse-submodules https://github.com/dd-the-dd/DeepDeckLearner.git
cd DeepDeckLearner
pwsh ./scripts/setup.ps1
```

Open the workbench:

```powershell
.\.venv\Scripts\deepdeck-learner.exe
```

It opens `http://127.0.0.1:8765`. Start with **Train locally → V12 → Smoke
sample**. The smoke run verifies the complete encoder, model, optimizer, and
checkpoint path without requiring a deck or dataset.

The browser UI cannot launch arbitrary commands. A loopback-only Python
controller validates a small allowlist of training and playtest jobs. It never
sends your API key to React.

## What works now

- Train V11 or V12 from the built-in smoke samples.
- Train from a JSONL decision trajectory and resume a checkpoint.
- Configure CPU/CUDA, epochs, learning rate, and seed.
- Run random, Alexios, V11, or V12 against a local Engine session.
- Inspect dependency readiness and bounded job logs.
- Connect an inference agent to Deep Deck League with your account API key.

Deck-driven self-play training and hosted weight updates are visible as
**unavailable** until the public `trajectory-v1` contract exists. The UI does not
substitute smoke data or call inference “training.”

## The two learning paths

### Magic-first

Choose the model family and input. A V12 smoke run is the safe first action. For
a real experiment, choose a `.jsonl` trajectory collected with the same encoder
schema. Advanced optimizer fields stay collapsed by default.

### ML-first

The public pipeline performs behavior cloning on the chosen legal action and
MSE regression on player-value targets. V11 uses four multiplayer value slots;
V12 exposes a two-player zero-sum value pair. The model scores only actions
declared legal by Rust.

Read [the deep-learning guide](docs/deep-learning.md) for the Magic-to-tensor
representation, dataset schema, checkpoint contract, and extension points.

## Test behavior locally

Start the public Rust server in a second terminal:

```powershell
cargo run --manifest-path external/deepdeck-engine/Cargo.toml --locked --bin mtg-engine-server
```

After importing two decks into its local catalog, open **Playtest** and provide
their Engine deck-session IDs. The first seat uses your selected example; the
second uses Engine's `ai-random` controller by default.

The initial workbench starts and monitors the local agent. A complete visual
playtest additionally needs a Pixi local-client host and a concrete session
trace; that integration is deliberately tracked after `trajectory-v1`, rather
than fabricating game state in React.

## Public examples

- `random`: reproducible random selection from exact legal actions.
- `alexios`: a readable priority policy for Alexios, equipment, protection,
  removal, goad, combat tricks, Food, and Clues.
- `v11`: recurrent PyTorch policy with multiplayer value estimates.
- `v12`: two-player variant with antisymmetric value estimates.

The original command-line interfaces remain available:

```powershell
deepdeck-train v12 --smoke --epochs 2 --output runs/v12-smoke
deepdeck-example v12 --target local --checkpoint runs/v12-smoke
deepdeck-example alexios --target ddl --speed 1s
```

## Hosted inference and future online training

Copy `.env.example` to `.env`, then set your account-owned
`DEEPDECK_API_KEY`, competition version, and deck version. The API key identifies
the account; the agent manifest remains free to describe its own model/version.
No browser login or `gcloud auth login` is required.

Playing a hosted match currently performs inference only. Training from hosted
play will require a compatible trajectory capability containing observations,
legal actions, chosen actions, rewards, and terminal placement. A replay is not
assumed to satisfy that contract.

## Dependency updates

Clone with `--recurse-submodules` or run:

```powershell
git submodule update --init --recursive
```

Engine, Pixi, and Agent revisions are pinned so an experiment remains
reproducible. Updates are reviewed through pull requests and CI, rather than
following a floating `main` branch at runtime.

## Develop

```powershell
.\.venv\Scripts\Activate.ps1
ruff check .
mypy
pytest
npm --prefix apps/learner-web run lint
npm --prefix apps/learner-web test
npm --prefix apps/learner-web run build
```

Only `@dd-the-dd` can update protected `main`. Everyone else can propose a pull
request. See [publishing and protection](docs/publishing.md).
