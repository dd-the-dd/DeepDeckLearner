# DeepDeckLearner technical discovery

Parent epic: [#4](https://github.com/dd-the-dd/DeepDeckLearner/issues/4)

## Current capabilities

- V11 and V12 examples can infer through `DeepDeckAgent`.
- The trainer consumes versioned JSONL samples and has a deterministic smoke mode.
- DeepDeckEngine exposes a loopback HTTP server and agent WebSocket protocol.
- DeepDeckPixi is a reusable Vite package and Web Component.

## Gaps

- The public SDK does not yet collect complete observation/action/reward
  trajectories from selected deck self-play.
- Hosted replay data is not declared equivalent to a training trajectory.
- The hosted server does not advertise a versioned online-training capability.
- Pixi needs a concrete local session/replay URL; rendering alone cannot create a
  game or agent session.

## Decisions

1. Use a loopback Python controller because a browser cannot safely launch Python
   or Rust processes.
2. Use argv-only subprocesses from an allowlist; never expose arbitrary shell.
3. Pin Engine and Pixi commits. A scheduled dependency PR may propose updates,
   but a user run must be reproducible.
4. Keep API keys in the controller environment and return only a boolean presence
   signal to React.
5. Deliver dataset/smoke training first. Deck-driven and hosted training remain
   explicit capabilities, not optimistic UI promises.
6. Runtime synchronization means the gitlinks pinned by DeepDeckLearner. A
   running local app never advances Engine or Pixi to an unreviewed remote head.
7. Pixi has a prepare/build lifecycle rather than a standalone server lifecycle.
   Engine remains the long-running local process owned by a controller job.
8. A missing or stale submodule is a setup condition, not a reason to reject the
   primary setup action. The controller owns one composite allowlisted bootstrap
   command; browser-side sequencing is rejected because a refresh could abandon
   the sequence between jobs.
9. The home screen is an intent router. Capability diagnostics belong inside the
   workflow that consumes them, not in a global prerequisite dashboard.

## Options considered

- **Floating Git dependencies:** freshest code, but non-reproducible and capable
  of breaking users without review. Rejected for runtime defaults.
- **Copying Engine/Pixi source:** simple clone, but creates the duplicate sources
  of truth the repository split was intended to remove. Rejected.
- **Git submodules:** clear provenance and convenient contributor checkout, but
  requires recursive clone awareness. Accepted for source development.
- **Published packages/releases:** best user installation path. Adopted as the
  target distribution mechanism while submodules validate integration in CI.

## Enabling work after the first release

- Define `trajectory-v1` across Engine, SDK, learner, and hosted API.
- Add format/deck catalog download with content hashes and licensing metadata.
- Add self-play worker limits for CPU, memory, games, and concurrent learners.
- Publish signed Engine binaries and the Pixi package from protected tags.

## LAN and secret decisions

1. LAN access is opt-in. It uses short-lived in-memory device sessions and a
   rotating pairing code; it never distributes the account API key to clients.
2. Secret configuration is a host-only controller operation. Use the platform
   credential vault first and an ignored permission-restricted `.env` only when
   the operating system has no available keyring backend.
3. The local API rejects non-private browser origins. HTTPS remains the future
   choice for operation beyond a trusted private LAN; LAN mode is not a
   public-network deployment mode.

