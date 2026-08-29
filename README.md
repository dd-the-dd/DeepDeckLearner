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

Requirements: Git, Python 3.10+, Node.js 22.13+, PowerShell 7, and a Rust
toolchain for the first local Engine build. On Windows, install the Visual C++
Build Tools when the bundled Rust linker cannot use an installed Windows SDK.

```powershell
git clone --recurse-submodules https://github.com/dd-the-dd/DeepDeckLearner.git
cd DeepDeckLearner
pwsh ./scripts/setup.ps1
```

Open the workbench:

```powershell
.\.venv\Scripts\deepdeck-learner.exe
```

It opens `http://127.0.0.1:8765`. Home first asks whether you want to **Train an
agent**, **Test an agent locally**, or **Send an agent to the League**. Start with
**Train an agent → V12 → Train V12 now**. The beginner form selects the built-in
smoke trajectory for you. It verifies the complete encoder, model, optimizer,
and checkpoint path without requiring Engine, Pixi, a deck, a dataset, or an API
key.

For local play, press **Set up Engine + Pixi** once. The controller initializes
missing submodules, synchronizes the reviewed revisions, prepares Pixi, builds
Engine when necessary, and starts a sleeping Engine. Technical revisions and
individual recovery controls stay under a disclosure. Synchronization never
follows a floating branch and refuses to overwrite local changes inside either
public dependency.

The browser UI cannot launch arbitrary commands. A loopback-only Python
controller validates a small allowlist of training and playtest jobs. It never
sends your API key to React.

## What works now

- Train V11 or V12 from the built-in smoke samples.
- Train from a JSONL decision trajectory and resume a checkpoint.
- Prefer CUDA automatically and fall back to CPU when no compatible GPU is available.
- Configure trajectory input, CPU/CUDA, epochs, learning rate, and seed under Advanced.
- Run random, Alexios, V11, or V12 against a local Engine session.
- Inspect dependency readiness and bounded job logs.
- Connect an inference agent to Deep Deck League with your account API key.

Deck-driven self-play training and hosted weight updates are visible as
**unavailable** until the public `trajectory-v1` contract exists. The UI does not
substitute smoke data or call inference “training.”

## The two learning paths

### Magic-first

Choose the model family and start. A V12 smoke run is the safe first action. The
trajectory selector, project-relative `.deepdeck/trajectories/decisions.jsonl`
path, and optimizer controls stay under Advanced. The workbench creates that
ignored local trajectory file and its parent directory when it starts.

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

The workbench starts and monitors the local agent. Pixi is a renderer package,
not a second server: the setup button prepares it, while a complete visual
playtest still needs its local-client host and a concrete session trace. That
remaining integration is shown honestly rather than fabricating game state in
React.

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
`DEEPDECK_API_KEY`. The Matchmaking screen finds competitions and account-visible
decks by name, so users do not enter version identifiers. Catalog requests are
refused locally without the key and send it to Deep Deck League as a Bearer token.
The key identifies the account; the agent manifest remains free to describe its
own model/version. No browser login or `gcloud auth login` is required.

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

The [C4 architecture views](docs/epics/2026/08-august/deepdeck-learner/06-architecture-deepdeck-learner.md)
show the system context, local containers, protocol boundaries, and the exact
roles of Learner, Engine, Pixi, Agent, and the hosted League.

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
