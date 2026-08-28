# DeepDeckLearner architecture

Parent epic: [#4](https://github.com/dd-the-dd/DeepDeckLearner/issues/4)

## Boundaries

```text
React workbench
    | loopback JSON/SSE, session token
Python learner controller
    | allowlisted argv        | versioned SDK protocol
Trainer / example agents      +---------------- Hosted league
    |
DeepDeckEngine local server ---- versioned session ---- DeepDeckPixi client
```

- React owns configuration UX and job presentation.
- The controller owns validation, capability detection, subprocess lifetime,
  redacted metadata, and logs.
- Existing example modules own model inference and dataset training.
- DeepDeckEngine owns rules and state. DeepDeckPixi owns game visualization.
- The hosted league owns accounts, matchmaking, decks, and rankings.

## Repository structure

```text
apps/learner-web/             React + TypeScript workbench
src/deepdeck_learner/         loopback controller and CLI
src/deepdeck_examples/        public V11/V12 and rule-based examples
external/deepdeck-engine/     pinned Git submodule
external/deepdeck-pixi/       pinned Git submodule
external/deepdeck-agent/      pinned Git submodule
docs/                         beginner, ML, and product contracts
```

## Local API v1

- `GET /api/v1/health` - controller readiness.
- `GET /api/v1/status` - capability booleans and dependency revisions.
- `GET /api/v1/jobs` - bounded recent job summaries.
- `POST /api/v1/jobs` - validate and start an allowlisted job.
- `GET /api/v1/jobs/{id}` - job metadata and bounded logs.
- `POST /api/v1/jobs/{id}/stop` - graceful termination request.

The initial job kinds are `training.smoke`, `training.dataset`, and
`playtest.agent`. `training.decks` and `training.hosted` are returned by status as
unsupported until their versioned capability contracts exist.

## Job execution

The controller creates argv from typed values. It runs without `shell=True`,
inherits only an allowlisted environment, bounds log memory, and records child
process state independently of browser connections. One training job runs at a
time by default to avoid GPU contention; local play can use a separate limit.

## Configuration and secrets

Non-secret defaults may be stored in `.deepdeck/learner.json`. API keys are read
from `DEEPDECK_API_KEY` or a local `.env` ignored by Git. Status reports
`api_key_configured: true|false`, never the key or an environment dump.

The HTTP listener defaults to `127.0.0.1`. Non-loopback binding is rejected by
the first release because the controller can launch local processes.

## Dependency updates

Engine and Pixi submodule commits are the development compatibility lock. CI
checks them recursively. A dependency-update workflow may open a pull request,
run Engine/Pixi/Learner contracts, and require normal branch protection before
merge. Runtime releases should later consume signed/tagged artifacts matching the
same compatibility lock.

## Failure handling

- Missing dependency: prevent launch and provide the exact setup action.
- Engine unhealthy: retain form state, show endpoint and retry.
- Child failure: mark failed, retain bounded output, expose CLI reproduction.
- Browser disconnect: job continues; reconnection reads controller state.
- Controller stop: child processes receive graceful termination, then a bounded
  forced termination only when necessary.
