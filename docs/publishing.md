# Publier le dépôt public

Le public peut lire, cloner, forker et proposer une pull request. Le ruleset fourni
réserve cependant les mises à jour, suppressions et réécritures de `main` au compte
`@dd-the-dd`.

Après avoir installé GitHub CLI et ouvert la session du propriétaire :

```powershell
gh auth login
.\scripts\publish.ps1
```

Le script crée le dépôt public, pousse `main`, puis applique réellement le fichier
`.github/rulesets/protect-main.json` avec l'API GitHub. Le simple fait de committer ce
fichier ne suffit pas à activer la protection.
