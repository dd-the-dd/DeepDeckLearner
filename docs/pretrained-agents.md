# Official pretrained agents

DeepDeckLearner publishes two frozen inference agents as checksum-verified GitHub
release assets. The repository contains their code and model cards, while the
large binary weights stay out of Git history.

| Agent | Format | Training step | Download |
|---|---|---:|---:|
| V12.1 | Legacy, two players | 418,148 | 116 MiB |
| V11.1 | Commander, four players | 186,266 | 118 MiB |

Install one or both agents from the project root:

```sh
deepdeck-models list
deepdeck-models install v12.1
deepdeck-models install v11.1
```

The installer downloads from the `pretrained-agents-v1` GitHub release, checks
the pinned SHA-256 digest before extraction, validates the Oracle model family
and training step, and registers the model in `.deepdeck/runs/`. Restart or
refresh the local workbench and the downloaded model appears as a playable
agent. The files remain local and ignored by Git.

The example runner installs a missing official model automatically, so the
shortest inference command is:

```sh
# V12.1 against a local Legacy Engine
deepdeck-example v12.1 --target local

# V11.1 in public Commander matchmaking
deepdeck-example v11.1 --target ddl \
  --competition-version-id COMPETITION_ID \
  --deck-version-id DECK_ID
```

Pass `--checkpoint PATH` to either command to use a compatible checkpoint you
already downloaded. Set `DEEPDECK_PROJECT_ROOT` when the command is launched
outside the repository root.

These bundles contain model weights and inference metadata only. Optimizer
state was intentionally removed, reducing each download by roughly two thirds.
They can initialize a new experiment, but they do not reproduce an exact
optimizer resume. V12.1 was trained for Legacy and V11.1 for Commander; neither
is a claim of perfect play.

Implementation tracking: [GitHub #22](https://github.com/dd-the-dd/DeepDeckLearner/issues/22).
