# DeepDeckLearner test plan

Parent epic: [#4](https://github.com/dd-the-dd/DeepDeckLearner/issues/4)

## Unit tests

- Readiness detection reports missing and available dependencies deterministically.
- Job validation rejects unknown modes, non-loopback endpoints, invalid ranges,
  missing datasets, and unsafe output paths.
- Command construction returns an argv list and never invokes a shell.
- Job metadata redacts secret names and values.
- React forms preserve beginner values while advanced settings are toggled.

## Integration tests

- Controller health and status endpoints work on loopback.
- A smoke training job transitions queued -> running -> completed and records an
  artifact path.
- A failing child process transitions to failed and exposes bounded diagnostics.
- A local play request checks Engine health before starting the agent.
- Frontend production build and the pinned Pixi compatibility metadata are checked.

## Contract tests

- Engine health/session payloads use a declared API version.
- Pixi compatibility metadata accepts the pinned Engine revision.
- Online training stays disabled when the server omits trajectory capability.

## End-to-end acceptance

On Windows and Linux, follow the README from a fresh clone, launch the workbench,
complete a V12 smoke run, and inspect the resulting checkpoint. With a local
Engine running, launch a random-agent playtest and reach the visual client or an
actionable headless-success state.

## CI gates

- Python lint, strict typing, tests, and package verification.
- Frontend lint, tests, typecheck, and production build.
- Recursive dependency checkout and pinned compatibility check.
- No workflow publishes from pull requests or receives write permissions in test.
