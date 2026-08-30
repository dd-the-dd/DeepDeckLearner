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

It opens `http://127.0.0.1:8765` in the packaged app; the React development UI
uses port `5174`. Follow the three workbench stages: **Setup** connects the
League key and prepares Engine/Pixi, **Agent setup** selects V11/V12, format, and
a named training-deck pool, and **Use** offers Playtest against AI, Train, and
Run in the League. The saved non-secret agent profile survives controller
restarts.

Agent setup can load a curated metagame bundle or one of your private training
lots created on Deep Deck League. A personal lot shows its deck/card counts and
manifest size before download, excludes card images, and is cached under
`.deepdeck/training-lots/` after you choose it.

For local play, press **Set up Engine + Pixi** once. The controller initializes
missing submodules, synchronizes the reviewed revisions, prepares Pixi, builds
Engine when necessary, and starts a sleeping Engine. Technical revisions and
individual recovery controls stay under a disclosure. Synchronization never
follows a floating branch and refuses to overwrite local changes inside either
public dependency.

The browser UI cannot launch arbitrary commands. A local Python controller
validates a small allowlist of training and playtest jobs. It never sends your
API key back to React. Loopback is the default; opt-in LAN access is intended
for a trusted private network and gives each browser an in-memory session.

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

After importing two decks into its local catalog, open **Use → Playtest against
AI** and choose them by name. The first seat uses the model and format saved in
Agent setup; the second uses Engine's `ai-random` controller by default.

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

Open **Setup**, paste the account-owned key shown once by Deep Deck League,
and press **Save and verify**. DeepDeckLearner verifies the key and stores it in
the operating-system credential vault. The browser never stores the key and
this workflow never writes it to `.env`. The key identifies the account; the
agent manifest remains free to
describe its own model/version. No browser login or `gcloud auth login` is
required. Advanced users may still provide `DEEPDECK_API_KEY` directly in the
environment.

Setup also chooses **This computer only** or **Local network**. LAN mode
persists the selected port, restarts the packaged controller, and displays its
private-network URLs. Browsers on that trusted network open the workbench
directly. Configure the Windows firewall for the Private profile only. API-key
and listener changes remain available only from the host computer, including
when the host opens the app through its own LAN address.

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
