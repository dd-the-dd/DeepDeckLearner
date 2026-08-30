from __future__ import annotations

from typing import Any

SCHEMA_VERSION = "oracle-ai/training-gameplay-hourly/v2"
PLAYER_METRIC_KEYS = (
    "startingHandCards",
    "startingHandLands",
    "manaValueFirstFiveTurns",
    "permanentsExisted",
    "attacks",
    "suicidalAttacks",
)
METRIC_KEYS = (*PLAYER_METRIC_KEYS, "gameRounds")


def round_number(
    setup: dict[str, Any],
    global_turn_number: int,
) -> int:
    if global_turn_number <= 0:
        return 0
    player_count = max(1, len(setup.get("players", [])))
    return 1 + (global_turn_number - 1) // player_count


def summarize_gameplay_metrics(
    state: dict[str, Any],
    setup: dict[str, Any],
) -> dict[str, Any]:
    player_ids = [
        str(player.get("id"))
        for player in setup.get("players", [])
        if player.get("id")
    ]
    by_player = {
        player_id: {key: 0.0 for key in PLAYER_METRIC_KEYS}
        for player_id in player_ids
    }
    opening_hand_players: set[str] = set()
    events = state.get("events", [])
    if not isinstance(events, list):
        events = []
    for event in events:
        if not isinstance(event, dict):
            continue
        player_id = event.get("playerId")
        if player_id not in by_player:
            continue
        kind = event.get("kind")
        detail = event.get("detail") if isinstance(event.get("detail"), dict) else {}
        if kind == "openingHandFinalized":
            by_player[player_id]["startingHandCards"] = max(
                0.0, float(detail.get("handSize", 0))
            )
            by_player[player_id]["startingHandLands"] = max(
                0.0, float(detail.get("landCount", 0))
            )
            opening_hand_players.add(player_id)
        elif kind == "spellCast" and round_number(
            setup,
            int(event.get("turnNumber", 0)),
        ) <= 5:
            by_player[player_id]["manaValueFirstFiveTurns"] += max(
                0.0, float(detail.get("manaValue", 0))
            )
        elif kind == "permanentEnteredBattlefield":
            by_player[player_id]["permanentsExisted"] += 1.0
        elif kind == "attackerDeclared":
            by_player[player_id]["attacks"] += 1.0
        elif kind == "attackResolved" and detail.get("suicidal") is True:
            by_player[player_id]["suicidalAttacks"] += 1.0

    sums = {
        key: sum(player[key] for player in by_player.values())
        for key in PLAYER_METRIC_KEYS
    }
    samples = {key: len(player_ids) for key in PLAYER_METRIC_KEYS}
    samples["startingHandCards"] = len(opening_hand_players)
    samples["startingHandLands"] = len(opening_hand_players)
    try:
        game_rounds = float(round_number(setup, int(state.get("turnNumber", 0))))
        game_round_samples = 1
    except (TypeError, ValueError):
        game_rounds = 0.0
        game_round_samples = 0
    sums["gameRounds"] = game_rounds
    samples["gameRounds"] = game_round_samples
    return {
        "playerSamples": len(player_ids),
        "sums": sums,
        "samples": samples,
        "byPlayer": by_player,
    }


def update_hourly_gameplay_metrics(
    payload: dict[str, Any] | None,
    episode: dict[str, Any],
    training_hour: int,
) -> dict[str, Any]:
    result = (
        dict(payload or {})
        if (payload or {}).get("schemaVersion") == SCHEMA_VERSION
        else {}
    )
    result["schemaVersion"] = SCHEMA_VERSION
    hours = [dict(hour) for hour in result.get("hours", [])]
    hour = next(
        (entry for entry in hours if int(entry.get("trainingHour", 0)) == training_hour),
        None,
    )
    if hour is None:
        hour = {
            "trainingHour": training_hour,
            "games": 0,
            "playerSamples": 0,
            "sums": {key: 0.0 for key in METRIC_KEYS},
            "samples": {key: 0 for key in METRIC_KEYS},
        }
        hours.append(hour)
    hour["games"] = int(hour.get("games", 0)) + 1
    hour["playerSamples"] = int(hour.get("playerSamples", 0)) + int(
        episode.get("playerSamples", 0)
    )
    sums = hour.setdefault("sums", {})
    samples = hour.setdefault("samples", {})
    episode_sums = episode.get("sums", {})
    episode_samples = episode.get("samples", {})
    for key in METRIC_KEYS:
        sums[key] = float(sums.get(key, 0.0)) + float(episode_sums.get(key, 0.0))
        samples[key] = int(samples.get(key, 0)) + int(episode_samples.get(key, 0))
    hour["averages"] = {
        key: (
            float(sums[key]) / int(samples[key])
            if int(samples[key]) > 0
            else None
        )
        for key in METRIC_KEYS
    }
    attacks = float(sums.get("attacks", 0.0))
    hour["suicidalAttackRate"] = (
        float(sums.get("suicidalAttacks", 0.0)) / attacks
        if attacks > 0
        else 0.0
    )
    result["hours"] = sorted(hours, key=lambda entry: int(entry["trainingHour"]))
    return result
