from __future__ import annotations

import statistics
from collections import Counter
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class DecisionTrace:
    player_id: str | None
    decision_id: str | None
    decision_kind: str
    selected_action_kind: str
    legal_action_kinds: tuple[str, ...]
    turn_number: int | None
    hand_count: int | None
    mulligan_count: int | None
    projected_hand_size: int | None
    confidence: float | None
    entropy: float | None
    anomalies: tuple[str, ...]


def _player_hand_count(state: dict[str, Any], player_id: str | None) -> int | None:
    if player_id is None:
        return None
    for player in state.get("players", []):
        if player.get("id") == player_id:
            hand = player.get("hand")
            return len(hand) if isinstance(hand, list) else None
    return None


def _mulligan_count(decision_id: str | None) -> int | None:
    if not decision_id:
        return None
    try:
        return int(decision_id.rsplit(":", 1)[-1])
    except ValueError:
        return None


def build_decision_trace(
    state: dict[str, Any],
    actions: list[dict[str, Any]],
    selected_index: int,
    *,
    decision: dict[str, Any] | None = None,
    confidence: float | None = None,
    entropy: float | None = None,
    free_mulligans: int | None = None,
) -> DecisionTrace:
    context = decision or state.get("_decisionContext") or {}
    player_id = context.get("playerId")
    decision_id = context.get("id")
    decision_kind = str(context.get("kind") or "unknown")
    selected = actions[selected_index]
    selected_kind = str(selected.get("kind") or "unknown")
    legal_kinds = tuple(str(action.get("kind") or "unknown") for action in actions)
    hand_count = _player_hand_count(state, player_id)
    mulligan_count = _mulligan_count(decision_id)
    if free_mulligans is None:
        free_mulligans = int(context.get("freeMulligans") or 0)
    projected_hand_size = None
    anomalies: list[str] = []
    if decision_kind.casefold() == "mulligan" and hand_count is not None:
        current_count = mulligan_count or 0
        resulting_count = current_count + (selected_kind == "takeMulligan")
        projected_hand_size = max(
            0,
            hand_count - max(resulting_count - free_mulligans, 0),
        )
        if selected_kind == "takeMulligan" and projected_hand_size <= 1:
            anomalies.append("criticalMulliganToOneOrLess")
        if selected_kind == "takeMulligan" and projected_hand_size == 0:
            anomalies.append("mulliganToZero")
    if (
        decision_kind.casefold() == "attackers"
        and selected_kind == "finishAttackers"
        and "declareAttacker" in legal_kinds
    ):
        anomalies.append("declinedAvailableAttack")
    return DecisionTrace(
        player_id=str(player_id) if player_id is not None else None,
        decision_id=str(decision_id) if decision_id is not None else None,
        decision_kind=decision_kind,
        selected_action_kind=selected_kind,
        legal_action_kinds=legal_kinds,
        turn_number=(
            int(state["turnNumber"])
            if state.get("turnNumber") is not None
            else None
        ),
        hand_count=hand_count,
        mulligan_count=mulligan_count,
        projected_hand_size=projected_hand_size,
        confidence=confidence,
        entropy=entropy,
        anomalies=tuple(anomalies),
    )


def dominated_action_indices(
    state: dict[str, Any],
    actions: list[dict[str, Any]],
) -> tuple[int, ...]:
    context = state.get("_decisionContext")
    if not isinstance(context, dict):
        return ()
    dominated: set[int] = set()
    if "freeMulligans" in context:
        for index, action in enumerate(actions):
            if action.get("kind") != "takeMulligan":
                continue
            trace = build_decision_trace(state, actions, index)
            if trace.projected_hand_size is not None and trace.projected_hand_size <= 1:
                dominated.add(index)

    players = state.get("players")
    active_player = state.get("activePlayer")
    if isinstance(active_player, int) and isinstance(players, list):
        active_player = (
            players[active_player].get("id")
            if 0 <= active_player < len(players)
            and isinstance(players[active_player], dict)
            else None
        )
    if (
        str(context.get("kind", "")).casefold() == "priority"
        and state.get("step") == "postcombatMain"
        and str(context.get("playerId", "")) == str(active_player)
        and any(action.get("kind") == "playLand" for action in actions)
    ):
        dominated.update(
            index
            for index, action in enumerate(actions)
            if action.get("kind") == "passPriority"
        )
    return tuple(sorted(dominated))


def summarize_decision_traces(
    traces: Iterable[DecisionTrace],
    *,
    sample_limit: int = 20,
) -> dict[str, Any]:
    rows = list(traces)
    decision_counts = Counter(row.decision_kind for row in rows)
    action_counts = Counter(row.selected_action_kind for row in rows)
    anomaly_counts = Counter(
        anomaly for row in rows for anomaly in row.anomalies
    )
    by_decision_kind: dict[str, dict[str, Any]] = {}
    for kind in sorted(decision_counts):
        matching = [row for row in rows if row.decision_kind == kind]
        confidences = [row.confidence for row in matching if row.confidence is not None]
        entropies = [row.entropy for row in matching if row.entropy is not None]
        by_decision_kind[kind] = {
            "decisions": len(matching),
            "meanConfidence": statistics.fmean(confidences) if confidences else None,
            "meanEntropy": statistics.fmean(entropies) if entropies else None,
        }
    mulligans = [row for row in rows if row.decision_kind.casefold() == "mulligan"]
    priority = [row for row in rows if row.decision_kind.casefold() == "priority"]
    land_opportunities = [row for row in priority if "playLand" in row.legal_action_kinds]
    cast_opportunities = [row for row in priority if "castSpell" in row.legal_action_kinds]
    attacks = [row for row in rows if row.decision_kind.casefold() == "attackers"]
    attack_opportunities = [
        row for row in attacks if "declareAttacker" in row.legal_action_kinds
    ]
    confidences = [row.confidence for row in rows if row.confidence is not None]
    entropies = [row.entropy for row in rows if row.entropy is not None]
    land_turns: dict[tuple[str | None, int | None], list[DecisionTrace]] = {}
    for row in land_opportunities:
        land_turns.setdefault((row.player_id, row.turn_number), []).append(row)
    missed_land_turns = [
        turn_rows
        for turn_rows in land_turns.values()
        if not any(row.selected_action_kind == "playLand" for row in turn_rows)
    ]
    anomaly_counts["missedLandPlayTurn"] += len(missed_land_turns)
    anomaly_samples = [asdict(row) for row in rows if row.anomalies][:sample_limit]
    for turn_rows in missed_land_turns:
        if len(anomaly_samples) >= sample_limit:
            break
        sample = asdict(turn_rows[-1])
        sample["anomalies"] = [*sample["anomalies"], "missedLandPlayTurn"]
        anomaly_samples.append(sample)
    return {
        "totalDecisions": len(rows),
        "meanConfidence": statistics.fmean(confidences) if confidences else None,
        "meanEntropy": statistics.fmean(entropies) if entropies else None,
        "decisionKinds": dict(sorted(decision_counts.items())),
        "selectedActionKinds": dict(sorted(action_counts.items())),
        "byDecisionKind": by_decision_kind,
        "mulligan": {
            "decisions": len(mulligans),
            "taken": sum(row.selected_action_kind == "takeMulligan" for row in mulligans),
            "kept": sum(row.selected_action_kind == "keepHand" for row in mulligans),
            "minimumProjectedHandSize": min(
                (
                    row.projected_hand_size
                    for row in mulligans
                    if row.projected_hand_size is not None
                ),
                default=None,
            ),
            "criticalToOneOrLess": anomaly_counts["criticalMulliganToOneOrLess"],
            "toZero": anomaly_counts["mulliganToZero"],
        },
        "priority": {
            "landPlayOpportunities": len(land_opportunities),
            "landPlaysChosen": sum(
                row.selected_action_kind == "playLand" for row in land_opportunities
            ),
            "landPlayTurns": len(land_turns),
            "landPlayedTurns": len(land_turns) - len(missed_land_turns),
            "missedLandPlayTurns": len(missed_land_turns),
            "castOpportunities": len(cast_opportunities),
            "spellsCast": sum(
                row.selected_action_kind == "castSpell" for row in cast_opportunities
            ),
        },
        "combat": {
            "attackOpportunities": len(attack_opportunities),
            "attackersDeclared": sum(
                row.selected_action_kind == "declareAttacker"
                for row in attack_opportunities
            ),
        },
        "anomalies": {
            "total": sum(anomaly_counts.values()),
            "rate": (
                sum(anomaly_counts.values()) / len(rows) if rows else 0.0
            ),
            "counts": dict(sorted(anomaly_counts.items())),
            "samples": anomaly_samples,
        },
    }
