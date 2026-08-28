# Publish and protect DeepDeckLearner

The public can read, clone, fork, and propose pull requests. The repository
ruleset reserves updates, deletion, and history rewrites on `main` to
`@dd-the-dd` and requires the configured CI checks before merge.

After authenticating GitHub CLI as the owner:

```powershell
gh auth login
.\scripts\publish.ps1
```

The script creates or updates the public repository, pushes `main`, and applies
`.github/rulesets/protect-main.json` through the GitHub API. Committing the
ruleset file alone does not activate protection.

Engine, Pixi, and Agent submodule changes must be reviewed like source changes.
Release workflows build the React application before packaging the Python wheel,
so the `deepdeck-learner` command includes the workbench UI.
