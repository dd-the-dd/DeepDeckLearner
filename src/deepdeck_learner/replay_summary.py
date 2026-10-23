"""Small match facts for replay cards, including older recordings and League deltas."""

from __future__ import annotations

from collections.abc import Iterator
from copy import deepcopy
from typing import Any

SUMMARY_VERSION = "deepdeck-replay-match-summary/v1"


def _object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _integer(value: Any, minimum: int = 0) -> int | None:
    return value if type(value) is int and value >= minimum else None


def _rows(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _project(observation: dict[str, Any]) -> dict[str, Any]:
    """Never expand hand/card arrays while reading a gallery's turn counters."""
    source = _object(observation.get("state", observation))
    state = {key: source[key] for key in ("turnNumber", "status", "outcome") if key in source}
    if isinstance(source.get("players"), list):
        state["players"] = [
            {"id": player.get("id")} for player in source["players"] if isinstance(player, dict)
        ]
    result: dict[str, Any] = {"state": state}
    if "matchState" in observation:
        result["matchState"] = deepcopy(observation["matchState"])
    return result


def _merge(target: Any, patch: Any) -> Any:
    if not isinstance(patch, dict):
        return deepcopy(patch)
    result = deepcopy(target) if isinstance(target, dict) else {}
    for key, value in patch.items():
        if value is None:
            result.pop(key, None)
        else:
            result[key] = _merge(result.get(key), value)
    return result


def _observations(value: dict[str, Any]) -> Iterator[dict[str, Any]]:
    if value.get("schemaVersion") != "deepdeck-league-replay/v1":
        yield from _rows(value.get("frames"))
        return
    trace = _object(value.get("trace"))
    observation = _project(_object(trace.get("initialObservation")))
    yield observation
    checkpoints = {
        item.get("sequenceNumber"): item["observation"]
        for item in _rows(trace.get("checkpoints"))
        if _integer(item.get("sequenceNumber")) is not None
        and isinstance(item.get("observation"), dict)
    }
    frames = _rows(trace.get("frames"))
    for item in sorted(frames, key=lambda item: _integer(item.get("sequenceNumber")) or 0):
        checkpoint = checkpoints.get(_integer(item.get("sequenceNumber")))
        if checkpoint is not None:
            observation = _project(checkpoint)
        else:
            delta = _object(item.get("observationDelta"))
            patch = delta.get("patch", item.get("patch", delta))
            observation = _merge(observation, _project(_object(patch)))
        yield observation
    if not frames:
        for item in _rows(trace.get("decisions")):
            if isinstance(item, dict):
                yield _project(_object(item.get("observableState")))
    if isinstance(trace.get("terminalObservation"), dict):
        yield _project(trace["terminalObservation"])


def replay_match_summary(value: dict[str, Any]) -> dict[str, Any]:
    metadata = _object(value.get("metadata"))
    cached = metadata.get("matchSummary")
    if isinstance(cached, dict) and cached.get("version") == SUMMARY_VERSION:
        return cached
    scope = "game" if value.get("schemaVersion") == "deepdeck-league-replay/v1" else "match"
    player_ids: list[str] = []
    participants = metadata.get("participants")
    for participant in participants if isinstance(participants, list) else []:
        player_id = _object(participant).get("playerId")
        if isinstance(player_id, str) and player_id not in player_ids:
            player_ids.append(player_id)
    rounds: dict[int, dict[str, Any]] = {}
    current_round, previous_turn = 1, 0
    wins: dict[str, int] | None = None
    games_to_win = None
    latest_outcome: dict[str, Any] = {}

    def record_score(match: dict[str, Any]) -> None:
        nonlocal wins, games_to_win
        raw = match.get("winsByPlayerId")
        if (
            isinstance(raw, dict)
            and raw
            and all(
                isinstance(key, str) and _integer(count) is not None for key, count in raw.items()
            )
        ):
            wins = dict(raw)
        if _integer(match.get("gamesToWin"), 1) is not None:
            games_to_win = match["gamesToWin"]

    for frame in _observations(value):
        state = _object(frame.get("state"))
        players = state.get("players")
        for player in players if isinstance(players, list) else []:
            player_id = _object(player).get("id")
            if isinstance(player_id, str) and player_id not in player_ids:
                player_ids.append(player_id)
        match = _object(frame.get("matchState"))
        explicit = _integer(match.get("gameNumber"), 1)
        turn = _integer(state.get("turnNumber"), 1)
        if explicit is not None:
            if explicit < current_round:
                continue  # An older polled view must not move the trace backwards.
            current_round = explicit
        elif turn == 1 and previous_turn > 1:
            current_round += 1
        record_score(match)
        if turn is None:
            continue
        previous_turn = turn
        round_key = (
            (_integer(metadata.get("roundNumber"), 1) or 1) if scope == "game" else current_round
        )
        row = rounds.setdefault(round_key, {"round": round_key, "turns": 0, "complete": False})
        row["turns"] = max(row["turns"], turn)
        outcome = _object(state.get("outcome"))
        if outcome:
            latest_outcome = outcome
        row["complete"] = (
            row["complete"]
            or bool(outcome)
            or state.get("status") in {"complete", "completed", "gameOver"}
        )
    record_score(_object(metadata.get("matchState")))
    expected_rounds = _integer(metadata.get("roundNumber"), 1) if scope == "match" else 1
    if wins is None and (scope == "game" or (expected_rounds or len(rounds)) == 1):
        outcome = _object(metadata.get("outcome")) or latest_outcome
        winner = outcome.get("winner")
        if winner in player_ids and player_ids:
            wins = {player_id: int(player_id == winner) for player_id in player_ids}
    if wins is not None:
        for player_id in player_ids:
            wins.setdefault(player_id, 0)
        player_ids.extend(key for key in wins if key not in player_ids)
    ordered_rounds = [rounds[key] for key in sorted(rounds)]
    return {
        "version": SUMMARY_VERSION,
        "scope": scope,
        "winsByPlayerId": wins,
        "playerIds": player_ids,
        "gamesToWin": games_to_win,
        "roundTurns": ordered_rounds,
        "totalTurns": sum(row["turns"] for row in ordered_rounds) if ordered_rounds else None,
        "turnsExact": bool(ordered_rounds)
        and all(row["complete"] for row in ordered_rounds)
        and len(ordered_rounds) == (expected_rounds or len(ordered_rounds)),
    }
