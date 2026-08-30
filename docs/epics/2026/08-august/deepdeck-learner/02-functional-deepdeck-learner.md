# DeepDeckLearner functional analysis

Parent epic: [#4](https://github.com/dd-the-dd/DeepDeckLearner/issues/4)

## Workflows

### F1 - Inspect readiness (Must)

The workbench reports Python, SDK, Engine, Pixi, dataset, API-key, and hosted
trajectory-contract readiness. Every blocker includes a corrective action.

### F2 - Train locally (Must)

The user chooses V11 or V12 and either a JSONL trajectory dataset or the built-in
smoke sample. Beginner mode exposes model, input, and output only. Advanced mode
adds epochs, batch size, learning rate, seed, device, and checkpoint location.
The UI previews the allowlisted command, starts it, streams job state, and can
request a graceful stop.

### F3 - Configure deck-driven training (Should)

The user chooses one or more legal decks and a format. Until the collector
contract is implemented, the workbench preserves this configuration but marks
the run blocked instead of silently substituting smoke data.

### F4 - Train from hosted play (Should)

The workbench reads an account API key from the controller environment, never
from browser persistence. It can start online learning only when the server and
SDK advertise a compatible, versioned trajectory endpoint. Otherwise it explains
that online inference remains available but learning is not.

### F5 - Test locally (Must)

The user chooses an example agent, local endpoint, agent deck session, and
opponent deck session. The controller starts the existing local agent command.
If Pixi is built and a visual-session URL exists, the workbench opens it in the
dedicated playtest area.

### F6 - Inspect the Magic-to-tensor representation (Should)

The workbench links observations to the documented feature vector, reports its
schema version and dimensions, and distinguishes observable facts from masks and
action features. Tensor changes are code-reviewed and versioned.

### F7 - Preserve reproducibility (Must)

Every job records model version, parameters, dependency revisions, start/end
times, exit status, and artifact paths. Secrets and full environment dumps are
excluded.

### F8 - Configure League access step by step (Must)

The workbench links directly to the Autonomous agents section of the user's
Deep Deck League account, explains that the generated key is shown only once,
and lets the host paste, verify, and save it from Settings. The key value is
never returned to React after submission.

### F9 - Select decks by name (Must)

Hosted matchmaking searches the public Deep Deck League deck catalog by name,
creator, and format. Local playtesting reads the Engine's legal-deck catalog.
Both interfaces display human-readable deck names while retaining version and
session identifiers only in submitted controller payloads and job arguments.

### F10 - Manage the local runtime (Must)

The workbench reports whether the pinned Engine and Pixi sources are installed,
synchronized, locally modified, built, and running or ready. One primary action
initializes both missing submodules, synchronizes both reviewed commits, builds
Pixi when stale, builds Engine when stale, and starts Engine. Individual controls
remain available only as recovery tools. Runtime updates never follow an upstream
floating branch and never overwrite local submodule changes.

### F11 - Choose an outcome first (Must) — [#11](https://github.com/dd-the-dd/DeepDeckLearner/issues/11)

The first screen asks whether the user wants to train an agent, test an agent
locally, or send an agent to League matchmaking. It does not embed a training
form or expose dependency internals before that choice. Each selected workflow
has a three-step journey, one primary next action, and only the readiness state
relevant to that outcome.

## User stories and acceptance criteria

### Story A - First smoke run

As a Magic player, I can select V12, choose the smoke sample, and start training.

- The start action is disabled with a reason when Python dependencies are absent.
- A successful run creates a checkpoint and the UI exposes its local path.
- Refreshing the browser does not terminate the controller-owned job.

### Story B - Dataset run

As an ML developer, I can train from JSONL and tune advanced parameters.

- Invalid paths and numeric ranges are rejected before process launch.
- The exact argv is visible; no value is interpolated into a shell string.
- The job captures bounded stdout/stderr without leaking environment secrets.

### Story C - Local behavior test

As a Magic player, I can start a local agent and understand each missing setup
step.

- Engine health is checked at the configured loopback URL.
- Both deck session identifiers are required.
- Pixi absence does not prevent a headless agent run, but is clearly indicated.

### Story D - Online training safety

As an AI developer, I cannot accidentally mistake online inference for training.

- The hosted-training button remains disabled until a compatible capability is
  advertised.
- The browser never receives the API key value.

### Story E - Guided matchmaking access

As a Magic player, I can find the exact account section that creates my API key
and save it from the workbench without editing an `.env` file.

- The workbench links to `/account#autonomous-agents` on Deep Deck League.
- The UI reports configured/not configured without exposing the key.
- A matchmaking job cannot start without a controller-owned key.
- The child agent process receives only the explicitly allowlisted account key.

### Story F - Human-readable deck selection

As a Magic player, I choose decks by name instead of copying identifiers.

- Hosted results are filtered by format and searched through the public catalog.
- Active competition and deck version identifiers remain internal.
- Local playtest choices come from the Engine's legal decks for that format.
- Empty, loading, unavailable, and no-result states do not expose an ID field.

### Story G - One-click local stack — [#12](https://github.com/dd-the-dd/DeepDeckLearner/issues/12)

As a Magic player, I can prepare and start the compatible local game stack
without copying terminal commands.

- Engine and Pixi expose installed, update-required, build-required, ready,
  running, local-changes, and failed states.
- `Set up Engine + Pixi` initializes missing sources, synchronizes stale sources,
  prepares Pixi, and starts Engine without separate prerequisite buttons.
- Synchronization checks out only the commits pinned by DeepDeckLearner.
- Dirty submodules are never overwritten and explain how the user can recover.
- Every operation is an allowlisted controller job with bounded logs and stop
  behavior; the browser cannot submit a command or repository path.

### Story H - Intent-first onboarding — [#11](https://github.com/dd-the-dd/DeepDeckLearner/issues/11)

As a Magic player, I choose what I want to accomplish before seeing technical
configuration.

- Home presents the three supported outcomes in plain Magic-oriented language.
- Training explicitly states that Engine, Pixi, decks, and an account key are not
  needed for the first checkpoint.
- Local play reveals runtime setup before matchup selection and unlocks the next
  step when both dependencies are ready.
- Matchmaking starts with account connection, then deck choice, then queueing.
- Technical revisions and individual dependency controls remain available under
  progressive disclosure.

### F12 - Secure local and LAN access (Must)

The workbench defaults to loopback access. Settings can enable a LAN listener
and persist its port, after which the controller restarts. Browsers on that
trusted private network receive an in-memory session and open the workbench
directly. Only a browser running on the host may change the account key or
network listener, including when it uses one of the host's own LAN addresses.

### F13 - Configure the account key in the workbench (Must)

The host user pastes an account key into a masked Settings field. The controller
validates its shape, verifies it with Deep Deck League, stores it in the system
credential vault, and returns only configured and provider metadata. The UI
never writes `.env` and supports replacement and disconnection without ever
reading the stored value.

### Story I - Secure LAN workbench

As a local user, I can opt into access from my trusted private LAN without
configuring the League key on every browser.

- Loopback remains the default and LAN mode requires an explicit host action.
- Every trusted LAN browser receives an in-memory session rather than the League
  API key and opens the workbench directly.
- Untrusted browser origins and unauthenticated controller APIs are rejected.
- API-key and network mutations remain restricted to the host computer.

### F14 - Configure once, then choose an operation (Must)

The primary workbench journey has three ordered stages. Application setup
connects the League account and prepares the pinned Engine and Pixi revisions.
Agent setup selects a model family, format, and named training-deck pool and
persists that non-secret configuration in the local controller. Use then offers
Playtest against AI, Train, and Run in the League without asking for the same
model and format again.

### Story J - Persistent agent training profile

As a Magic player, I can prepare the agent I want to improve before choosing
how to use it.

- The profile selects V11 or V12 and Legacy or Commander.
- Decks are found by name through the authenticated League catalog; raw deck
  version identifiers are never beginner inputs.
- The pool supports multiple decks, removal before saving, empty/loading/error
  states, and remains available after a controller restart.
- Changing format clears incompatible unsaved deck selections.
- The Use stage summarizes the saved profile and offers Playtest against AI,
  Train, and Run in the League as distinct actions.

### F15 - Curated deck bundles (Should)

Agent setup offers dated, sourced bundles that resolve curated archetype names
against the authenticated League catalog. Applying a bundle appends every
unique match to the unsaved pool, preserves existing choices, and reports each
archetype that is unavailable instead of silently substituting another deck.
The first bundle covers the leading August 2026 Legacy archetypes; bundle data
is versioned so later metagame updates remain reviewable.

### F16 - Personal League training lots (Must)

An authenticated player can create a named, format-specific training lot on
Deep Deck League by selecting one to 100 deck versions. The League owns the
lot and exposes it only to the browser session or an account API key belonging
to the same user. `considering` cards are excluded from its training manifest.

DeepDeckLearner lists those personal lots separately from public curated
bundles. Each lot shows deck count, playable-card count, unique-card count and
the exact uncompressed manifest size before download. Loading one downloads
the versioned card-and-zone manifest without images, stores it below
`.deepdeck/training-lots/`, switches format, and replaces the current pool.
Completion reports the bytes actually received and the local path.

