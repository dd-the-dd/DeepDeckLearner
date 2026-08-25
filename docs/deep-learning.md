# Entraîner les exemples V11 et V12

Ce guide fournit un pipeline minimal et entièrement public : observation visible, actions
légales fournies par Rust, encodage déterministe, réseau PyTorch, entraînement supervisé,
checkpoint et inférence avec le SDK. Aucun poids, historique de partie privé ou secret
d'infrastructure n'est distribué.

## Installation

Depuis le dépôt `DeepDeckAgentExamples` placé à côté du SDK :

```powershell
python -m pip install -e "../DeepDeckAgent[dev]"
python -m pip install -e ".[deep-learning]"
```

Une machine CPU suffit pour le smoke test. Pour une expérience réelle, choisissez une
version de PyTorch adaptée à votre GPU en suivant sa documentation officielle.

## Ce que les deux modèles apprennent

Le réseau ne génère jamais une commande libre. Rust envoie la liste exacte des actions
légales; le modèle calcule un logit par entrée et le SDK retourne l'identifiant choisi.

V11 possède :

- un Transformer pour les entités de l'état visible;
- un Transformer distinct pour les différences et événements depuis la décision précédente;
- une mémoire `GRUCell` transportée entre les décisions d'une partie;
- un score dynamique par action légale;
- une tête de valeur multijoueur de quatre positions.

V12 conserve la politique V11, mais sa tête de valeur prédit un scalaire borné `V(s)`.
Les valeurs retournées sont `V(s)` pour le joueur courant et `-V(s)` pour l'adversaire.
Cette hypothèse à somme nulle rend l'exemple V12 strictement adapté à deux joueurs.

Le code est volontairement plus petit que le système de production. Le feature hashing
public remplace son vocabulaire et ses encodeurs versionnés; les checkpoints ne sont donc
pas interchangeables.

## Vérifier tout le pipeline sans données

```powershell
deepdeck-train v11 --smoke --epochs 2 --output runs/v11-smoke
deepdeck-train v12 --smoke --epochs 2 --output runs/v12-smoke
```

Chaque répertoire contient :

```text
runs/v12-smoke/
├── config.json  # famille, schéma et hyperparamètres
└── model.pt     # state_dict PyTorch seulement
```

Le chargeur utilise `torch.load(..., weights_only=True)`. Ne publiez pas vos poids par
accident : les formats usuels et les répertoires de sortie sont dans `.gitignore`.

## Préparer ses propres décisions en JSON Lines

Une ligne représente une décision. Voici le plus petit document accepté :

```json
{"observation":{"turnNumber":1,"step":"precombatMain","players":[{"id":"p1","life":20},{"id":"p2","life":20}]},"legalActions":[{"id":"land","kind":"playLand"},{"id":"pass","kind":"passPriority"}],"chosenActionId":"land","valueTargets":[1.0,-1.0]}
```

Champs :

- `observation` : projection visible reçue du moteur;
- `legalActions`, ou `decision.options` : actions exactes proposées par Rust;
- `chosenActionId` : action cible, obligatoirement présente dans cette liste;
- `previousObservation` : observation visible à la décision précédente, facultative;
- `knownDeck` : définitions connues du deck propre au début de la partie, facultatives;
- `valueTargets` : résultats ou valeurs cibles par position relative, facultatifs.

Ne placez jamais les cartes cachées d'un adversaire dans le dataset. Un export destiné à
la recherche doit respecter le niveau de partage et le consentement associés à la partie.

Entraînement :

```powershell
deepdeck-train v11 `
  --dataset data/mes-decisions.jsonl `
  --epochs 10 `
  --learning-rate 0.0003 `
  --output runs/mon-v11
```

Reprendre un modèle existant écrit un nouveau checkpoint et ne modifie pas la source :

```powershell
deepdeck-train v11 `
  --dataset data/nouvelles-decisions.jsonl `
  --resume runs/mon-v11 `
  --output runs/mon-v11-suite
```

Le trainer fourni fait du behavior cloning sur `chosenActionId` et une régression MSE sur
`valueTargets`. C'est une baseline, pas une obligation. Pour PPO, recherche arborescente,
self-play ou apprentissage hors politique, réutilisez les interfaces suivantes :

- `DecisionEncoder.encode(...)` pour produire les trois flux de tenseurs;
- `PolicyV11.forward(...)` ou `PolicyV12.forward(...)` pour obtenir logits, valeurs et mémoire;
- `save_checkpoint(...)` et `load_checkpoint(...)` pour conserver le contrat d'inférence;
- `DeepLearningAgent` pour connecter le modèle entraîné au moteur local ou public.

Jouer une partie avec `deepdeck-example` effectue seulement de l'inférence et ne modifie
jamais les poids. Un projet qui veut apprendre en ligne doit enregistrer ses transitions,
définir ses récompenses et appeler explicitement son propre trainer.

## Jouer localement

Pour simplement écouter le moteur local :

```powershell
deepdeck-example v11 --target local --checkpoint runs/mon-v11
```

Pour que l'exemple crée aussi sa partie :

```powershell
$env:DEEPDECK_LOCAL_DECK_SESSION_ID = "mon-deck"
$env:DEEPDECK_LOCAL_OPPONENT_DECK_SESSION_ID = "opponent-deck"
deepdeck-example v11 `
  --target local `
  --checkpoint runs/mon-v11 `
  --start-local-game `
  --local-format commander
```

Les deux decks doivent déjà exister dans le catalogue du moteur local.

## Jouer sur Deep Deck League

Après avoir généré une clé liée à la même version d'agent que le ticket :

```powershell
$env:DEEPDECK_API_KEY = "ddl_agent_..."
$env:DEEPDECK_COMPETITION_VERSION_ID = "..."
$env:DEEPDECK_AGENT_VERSION_ID = "..."
$env:DEEPDECK_DECK_VERSION_ID = "..."
deepdeck-example v12 --target ddl --checkpoint runs/mon-v12 --once
```

V11/V12 refusent un démarrage sans checkpoint. L'option `--allow-untrained` existe pour
un test de câblage local explicite; elle ne doit pas servir à évaluer la qualité du modèle.
