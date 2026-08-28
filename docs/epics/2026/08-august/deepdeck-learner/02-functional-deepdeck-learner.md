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
Deep Deck League account, explains that the generated `.env` line is shown only
once, and reports whether the restarted controller detected the key. The key
value stays in the controller environment and is never returned to React.

### F9 - Select decks by name (Must)

Hosted matchmaking searches the public Deep Deck League deck catalog by name,
creator, and format. Local playtesting reads the Engine's legal-deck catalog.
Both interfaces display human-readable deck names while retaining version and
session identifiers only in submitted controller payloads and job arguments.

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
and understand where its `.env` line belongs.

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

