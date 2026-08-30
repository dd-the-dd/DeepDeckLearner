# DeepDeckLearner test plan

Parent epic: [#4](https://github.com/dd-the-dd/DeepDeckLearner/issues/4)

## Unit tests

- Readiness detection reports missing and available dependencies deterministically.
- Job validation rejects unknown modes, non-loopback endpoints, invalid ranges,
  missing datasets, and unsafe output paths.
- Command construction returns an argv list and never invokes a shell.
- Job metadata redacts secret names and values.
- React forms preserve beginner values while advanced settings are toggled.
- CUDA preference resolves to CPU when CUDA is unavailable.
- Hosted catalog routes return 401 without `DEEPDECK_API_KEY` and never perform
  an anonymous upstream request.
- Dependency names and actions are allowlisted; paths cannot be supplied by React.
- Engine build freshness compares the executable with its Rust inputs.
- Synchronization refuses a dirty dependency and checks out only its gitlink.
- Composite setup refuses every dirty dependency before synchronizing either one.
- Composite setup initializes both sources, prepares Pixi, and starts Engine in
  that order when the workspace is fresh.

## Integration tests

- Controller health and status endpoints work on loopback.
- A smoke training job transitions queued -> running -> completed and records an
  artifact path.
- A failing child process transitions to failed and exposes bounded diagnostics.
- A local play request checks Engine health before starting the agent.
- Frontend production build and the pinned Pixi compatibility metadata are checked.
- The default project trajectory path and empty file are created idempotently.
- Pixi preparation runs fixed install/build commands and records the built revision.
- Starting an already-built Engine uses the executable without recompiling it.
- One-click setup skips compatible sources and already-built Pixi, and exits
  successfully when Engine is already healthy.

## Contract tests

- Engine health/session payloads use a declared API version.
- Pixi compatibility metadata accepts the pinned Engine revision.
- Online training stays disabled when the server omits trajectory capability.

## End-to-end acceptance

On Windows and Linux, follow the README from a fresh clone, launch the workbench,
complete a V12 smoke run, and inspect the resulting checkpoint. With a local
Engine running, launch a random-agent playtest and reach the visual client or an
actionable headless-success state.

From a non-recursive clone, open Playtest and press `Set up Engine + Pixi` once.
Both gitlinks must initialize, Pixi must build, Engine must become healthy, and
the browser must retain progress while the controller job continues. Home must
show outcome choices without an embedded training form.

## CI gates

- Python lint, strict typing, tests, and package verification.
- Frontend lint, tests, typecheck, and production build.
- Recursive dependency checkout and pinned compatibility check.
- No workflow publishes from pull requests or receives write permissions in test.

## Secure settings regression

- Account-key validation rejects malformed and upstream-rejected keys; responses,
  settings, job metadata, and logs never contain the submitted value.
- Trusted LAN requests receive a non-owner in-memory session and can access
  normal workbench routes without an app-specific pairing step.
- Untrusted browser origins are rejected, and LAN sessions cannot mutate
  host-only settings.
- Network configuration accepts only `local|lan` and ports 1024-65535, persists
  atomically, and reports when a restart is required.
- The CLI reloads persisted listener settings after an in-app restart request.
- Browser-submitted keys are stored only in the system credential vault; an
  unavailable credential backend fails safely and never writes `.env`.
- Agent training profiles accept only V11/V12, Legacy/Commander, unique named
  decks in the selected format, and at most 100 deck versions. A saved profile
  survives controller recreation.
- The UI starts in Setup when required application capabilities are missing,
  exposes one Engine/Pixi setup action, and presents Agent setup before the
  Playtest, Train, and League operations.
- Legacy bundle metadata exposes its date, sources, and eight named archetypes;
  applying it preserves existing selections, deduplicates resolved deck
  versions, and reports catalog misses.
