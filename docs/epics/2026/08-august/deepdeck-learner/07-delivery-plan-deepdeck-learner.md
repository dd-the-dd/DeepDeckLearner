# DeepDeckLearner delivery plan

Parent epic: [#4](https://github.com/dd-the-dd/DeepDeckLearner/issues/4)

1. Rename the repository and distribution while preserving legacy CLI aliases.
2. Add pinned Engine and Pixi source dependencies and compatibility CI.
3. Ship the loopback controller, readiness model, safe job runner, and tests.
4. Ship the React overview, local-training, and local-playtest flows.
5. Rewrite the README around a three-command Magic-player start and an advanced
   ML path including the tensor representation.
6. Validate Windows/Linux setup, open a reviewed pull request, and merge only
   after protected CI succeeds.
7. Deliver Story G's allowlisted local-runtime manager: pinned dependency sync,
   Pixi preparation, Engine start/stop ownership, readiness states, and tests.
8. Follow with trajectory-v1, deck synchronization, hosted learning, resource
   quotas, and signed dependency releases as separate reviewed work.

## Guided workflow correction

1. [#11](https://github.com/dd-the-dd/DeepDeckLearner/issues/11): replace the
   mixed dashboard with outcome-first navigation and workflow progress.
2. [#12](https://github.com/dd-the-dd/DeepDeckLearner/issues/12): ship the
   controller-owned composite Engine + Pixi bootstrap with fresh-clone tests.
3. [#13](https://github.com/dd-the-dd/DeepDeckLearner/issues/13): publish the C4
   system-context and container views and reconcile README terminology.
4. Run the full Python/frontend suites, exercise the already-ready and
   fresh-clone setup paths, and merge only after protected CI succeeds.
5. Add vault-only host-managed key storage, an opt-in trusted-LAN listener,
   restricted LAN sessions, origin enforcement, restart ownership, and the
   Settings UI.
6. Reframe onboarding as Setup, Agent setup, and Use; persist the model, format,
   and authenticated training-deck pool before exposing playtest, training, and
   League operations.

