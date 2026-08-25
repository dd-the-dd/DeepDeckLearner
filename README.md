# Deep Deck Agent Examples

Deux agents publics et volontairement faciles à lire :

- `random` choisit une action légale au hasard avec une graine reproductible;
- `alexios` suit une liste explicite de priorités pour jouer rapidement le deck
  **Alexios, Deimos of Kosmos**.

Ils utilisent le paquet
[`deepdeck-agent-sdk`](https://github.com/dd-the-dd/deepdeck-agent-sdk). Le moteur Rust
reste l'autorité sur les règles et refuse une action qui n'est pas dans la liste légale.

## Installation locale avant publication PyPI

Placez les deux dépôts dans le même dossier :

```text
Projet/
├── deepdeck-agent-sdk/
└── deepdeck-agent-examples/
```

Puis :

```powershell
cd deepdeck-agent-examples
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e "../deepdeck-agent-sdk[dev]"
python -m pip install -e ".[dev]" --no-deps
pytest
```

## Baseline aléatoire

```powershell
deepdeck-example random --target local --speed 100ms --seed 42
```

La graine rend la suite de choix reproductible. L'agent n'invente aucune action et ne
connaît pas les cartes cachées adverses.

## Agent Alexios

```powershell
$env:ALEXIOS_DECK_ID = "alexios"
deepdeck-example alexios --target local --speed 1s
```

Sa politique actuelle est intentionnellement « dumb », déterministe et programmatique :

1. garder une main avec au moins trois terrains, sinon prendre un mulligan;
2. jouer un terrain disponible;
3. lancer Alexios dès que le moteur l'autorise;
4. répondre avec une redirection lorsqu'un objet adverse sur la pile cible Alexios;
5. durant notre phase principale, équiper Alexios autant que possible tout en réservant
   le mana estimé d'une redirection ou d'un boost létal;
6. retirer d'abord une créature adverse assez forte pour tuer Alexios au combat;
7. lancer les équipements, puis les créatures, puis les autres permanents;
8. utiliser les removals restants sur la créature adverse la plus puissante;
9. si un effet de goad est disponible, cibler la créature adverse la plus puissante;
10. conserver un boost instantané lorsqu'il peut transformer une attaque d'Alexios en
    élimination surprise;
11. craquer Food et Clue seulement lorsqu'aucune action prioritaire ne reste;
12. attaquer d'abord avec Alexios et préférer les adversaires à faible total de vie.

Les estimations de mana et de létalité sont volontairement simples. Cet exemple montre
où écrire la stratégie; il n'est pas présenté comme un agent compétitif.

Le code complet est dans
[`src/deepdeck_examples/alexios.py`](src/deepdeck_examples/alexios.py).

## Deep Deck League public

Lorsque le serveur aura déployé les jetons de runner liés aux comptes :

```powershell
$env:DEEPDECK_AGENT_URL = "wss://.../ai/agents/ws"
$env:DEEPDECK_ACCESS_TOKEN = "votre-jeton-personnel-court"
deepdeck-example alexios --target ddl --speed 1s
```

Ne copiez jamais la clé globale du moteur dans ce dépôt. Une clé publique de signature
peut identifier une version officielle, mais chaque personne doit se connecter avec son
propre compte DDL et son propre jeton révocable.

## Modifier la stratégie

La fonction la plus importante est `choose_priority`. Chaque bloc retourne un objet
`Action` déjà déclaré légal par Rust. Les tests utilisent de petits états lisibles et
peuvent être exécutés après chaque changement :

```powershell
ruff check .
mypy
pytest
```

## Propriété du dépôt

Le code est lisible et forkable publiquement. Seul `@dd-the-dd` possède l'accès en
écriture et peut mettre à jour la branche `main`; les autres personnes peuvent proposer
une pull request sans obtenir de droit de push.

La procédure qui applique cette protection est dans
[`docs/publishing.md`](docs/publishing.md).
