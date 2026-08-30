# DeepDeckLearner vision

Status: Approved for initial implementation

Parent epic: [#4](https://github.com/dd-the-dd/DeepDeckLearner/issues/4)

## Outcome

DeepDeckLearner is the public home for Deep Deck's AI experiments. It gives a
Magic player a guided way to train and test an agent while keeping the model,
encoding, reward, and optimizer visible and editable for ML practitioners.

The first release provides one local workbench with three honest workflows:

1. train a V11 or V12 example from a local trajectory dataset;
2. prepare and monitor an online-training connection when the hosted trajectory
   contract is available;
3. launch an agent against a local DeepDeckEngine session and open the local
   visual client.

## Users

- A Magic player who understands decks and formats but not ML tooling.
- An AI developer who wants reproducible baselines and direct control over the
  tensor representation and training parameters.
- A Deep Deck contributor who needs to exercise Engine, Pixi, SDK, and model
  compatibility without the private league repository.

## Principles

- Progressive disclosure: deck and model first; optimizer details later.
- No fake readiness: unavailable engine, Pixi, dataset, key, or server contracts
  are shown as blockers before a job can start.
- Versioned sources of truth: Engine and Pixi are pinned dependencies and are
  updated through reviewed pull requests, never a floating production `main`.
- Local-first secrets: API keys saved through the workbench remain in the
  operating-system credential vault and are never persisted by the browser or
  written to `.env`.
- Rust stays authoritative for Oracle parsing, legal actions, and mutation.

## Success measures

- A new user can install dependencies, open the workbench, and run a training
  smoke test in at most three documented commands.
- A user can tell why a workflow cannot start without reading terminal output.
- An advanced user can inspect the exact command and configuration before launch.
- Engine and Pixi revisions used by a run are recorded with its job metadata.

## Non-goals for the first release

- Reimplementing game rules, matchmaking, or Pixi rendering in this repository.
- Shipping private production weights.
- Claiming that hosted replays are sufficient training trajectories before a
  versioned observation/action/reward contract exists.
- Starting arbitrary shell commands from the web UI.

