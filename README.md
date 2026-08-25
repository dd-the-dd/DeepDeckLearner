# Deep Deck Agent Examples

Quatre agents publics et volontairement faciles à modifier :

- `random` choisit une action légale au hasard avec une graine reproductible;
- `alexios` suit une liste explicite de priorités pour jouer rapidement le deck
  **Alexios, Deimos of Kosmos**;
- `v11` montre un réseau PyTorch récurrent avec une valeur multijoueur;
- `v12` reprend cette politique avec une valeur antisymétrique à deux joueurs.

Ils utilisent le paquet
[`DeepDeckAgent`](https://github.com/dd-the-dd/DeepDeckAgent). Le moteur Rust
reste l'autorité sur les règles et refuse une action qui n'est pas dans la liste légale.

## Installation locale avant publication PyPI

Placez les deux dépôts dans le même dossier :

```text
Projet/
├── DeepDeckAgent/
└── DeepDeckAgentExamples/
```

Puis :

```powershell
cd DeepDeckAgentExamples
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e "../DeepDeckAgent[dev]"
python -m pip install -e ".[dev]" --no-deps
pytest
```

Pour les exemples V11/V12, ajoutez PyTorch avec l'extra optionnel :

```powershell
python -m pip install -e ".[deep-learning]"
```

Le baseline aléatoire et Alexios restent utilisables sans installer PyTorch.

## Un seul programme, deux cibles

La même démo peut servir un moteur local ou entrer dans le matchmaking Deep Deck League :

```powershell
# Attend une partie créée par le moteur local.
deepdeck-example alexios --target local

# Se connecte au service public, entre dans la file, puis s'y remet après le match.
deepdeck-example alexios --target ddl
```

Pour créer aussi une partie locale, renseignez deux identifiants de deck déjà importés
dans le moteur :

```powershell
$env:DEEPDECK_LOCAL_DECK_SESSION_ID = "alexios"
$env:DEEPDECK_LOCAL_OPPONENT_DECK_SESSION_ID = "commander-opponent"
deepdeck-example alexios --target local --start-local-game --local-format commander
```

Le premier siège utilise le contrôleur WebSocket de l'exemple. Le second utilise
`ai-random` par défaut; `--local-opponent-controller` permet d'en choisir un autre.

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

## Exemples deep learning V11 et V12

Aucun poids n'est inclus dans ce dépôt. Les fichiers `*.pt`, `*.pth`, `*.ckpt`, `runs/`
et `checkpoints/` sont ignorés par Git. Il faut donc entraîner un checkpoint ou en fournir
un explicitement avant de jouer :

```powershell
# Deux échantillons intégrés vérifient le pipeline de bout en bout.
deepdeck-train v12 --smoke --epochs 2 --output runs/v12-smoke

# Inférence locale avec le checkpoint produit.
deepdeck-example v12 --target local --checkpoint runs/v12-smoke
```

`--allow-untrained` autorise des poids aléatoires uniquement pour tester le protocole et
la connexion. Sans checkpoint, la commande refuse normalement de démarrer V11/V12.

Les exemples conservent les idées structurantes des versions internes :

- V11 encode séparément l'état visible et les changements depuis la décision précédente,
  maintient une mémoire GRU, attribue un logit à chaque action légale dynamique et prédit
  jusqu'à quatre valeurs de joueurs;
- V12 utilise la même politique, mais produit une seule valeur à somme nulle `V(s)` et
  expose `[V(s), -V(s)]`; il est donc réservé ici aux parties Legacy à deux joueurs.

L'encodeur public utilise un feature hashing compact afin de ne dépendre d'aucun vocabulaire
ou fichier privé. Il s'agit d'un point de départ entraînable, pas d'une copie compatible
avec les checkpoints privés de production et pas d'une promesse de force de jeu.

Le format JSON Lines, les objectifs d'entraînement, la reprise d'un checkpoint et les
points d'extension sont détaillés dans
[`docs/deep-learning.md`](docs/deep-learning.md).

## Deep Deck League public

Après avoir créé l'agent et généré sa clé dans **Account → Autonomous agents** :

```powershell
Copy-Item .env.example .env
# Renseignez la clé, le slug/version de l'agent et les trois UUID de matchmaking.
deepdeck-example alexios --target ddl --speed 1s
```

Le fichier `.env` est chargé automatiquement; il n'est pas nécessaire d'exporter chaque
variable dans PowerShell.

Ne copiez jamais la clé globale du moteur dans ce dépôt. Une clé publique de signature
peut identifier une version officielle, mais chaque personne doit se connecter avec son
propre compte DDL et sa propre clé révocable. Le `agent_id` de la configuration doit
correspondre au slug de l'agent auquel la clé est liée.

La commande publique connecte d'abord le WebSocket, entre ensuite dans la file, prend les
décisions et se remet en file après chaque match. Elle peut donc tourner comme service en
arrière-plan sans navigateur et sans `gcloud auth login`. Ajoutez `--once` pour l'arrêter
après une seule partie.

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
