from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

import torch

from oracle_ai.encoding_v2 import normalize_nonnegative

FUTURE_FEATURE_NAMES = (
    "life",
    "retained_hand_count",
    "new_hand_count",
    "total_hand_count",
    "library_count",
    "graveyard_count",
    "exile_count",
    "land_count",
    "mana_available",
    "creature_count",
    "permanent_count",
    "enchantment_count",
    "artifact_count",
    "total_power",
    "total_toughness",
    "battlefield_mana_value",
    "poison_counters",
    "commander_damage_received",
    "cards_drawn",
    "spells_cast",
    "creatures_died",
    "damage_dealt",
    "mana_produced",
)

_MANA_PATTERN = re.compile(r"\{([^}]+)\}")


def _number(value: Any) -> float:
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return 0.0


def _signed_number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _definition(card: dict[str, Any]) -> dict[str, Any]:
    definition = card.get("definition")
    return definition if isinstance(definition, dict) else card


def _type_line(card: dict[str, Any]) -> str:
    return str(_definition(card).get("typeLine", "")).casefold()


def _has_type(card: dict[str, Any], card_type: str) -> bool:
    words = re.findall(r"[a-z]+", _type_line(card))
    return card_type.casefold() in words


def _mana_value(card: dict[str, Any]) -> float:
    total = 0.0
    for symbol in _MANA_PATTERN.findall(str(_definition(card).get("manaCost", ""))):
        normalized = symbol.upper()
        if normalized.isdigit():
            total += int(normalized)
        elif normalized in {"X", "Y", "Z"}:
            continue
        elif "/" in normalized:
            components = normalized.split("/")
            numeric = next(
                (int(value) for value in components if value.isdigit()), None
            )
            total += numeric if numeric is not None else 1.0
        else:
            total += 1.0
    return total


def _creature_stat(card: dict[str, Any], name: str) -> float:
    definition = _definition(card)
    base = _number(definition.get(name))
    modifier = _signed_number(card.get(f"{name}Modifier"))
    counters = card.get("counters")
    if not isinstance(counters, dict):
        counters = {}
    positive = _number(counters.get("+1/+1"))
    negative = _number(counters.get("-1/-1"))
    return max(0.0, base + modifier + positive - negative)


def _mana_pool_count(player: dict[str, Any]) -> float:
    mana_pool = player.get("manaPool")
    if not isinstance(mana_pool, list):
        return 0.0
    return sum(max(1.0, _number(mana.get("amount", 1))) for mana in mana_pool)


def _hand_card_identity(card: Any) -> str:
    if not isinstance(card, dict):
        return f"value:{card!r}"
    for key in ("instanceId", "cardInstanceId"):
        value = card.get(key)
        if value is not None and str(value):
            return f"instance:{value}"
    # Engine snapshots normally expose instanceId. The stable fallback keeps
    # legacy/test snapshots useful and Counter preserves duplicate quantities.
    definition = _definition(card)
    return "definition:" + json.dumps(
        definition,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _hand_card_identities(player: dict[str, Any]) -> tuple[str, ...]:
    hand = player.get("hand")
    if not isinstance(hand, list):
        return ()
    return tuple(_hand_card_identity(card) for card in hand)


def _player_base_features(player: dict[str, Any]) -> list[float]:
    battlefield = player.get("battlefield")
    if not isinstance(battlefield, list):
        battlefield = []
    creatures = [card for card in battlefield if _has_type(card, "creature")]
    counters = player.get("counters")
    if not isinstance(counters, dict):
        counters = {}
    commander_damage = player.get("commanderDamage")
    if not isinstance(commander_damage, list):
        commander_damage = []
    return [
        _number(player.get("life")),
        len(player.get("library", [])),
        len(player.get("graveyard", [])),
        len(player.get("exile", [])),
        sum(_has_type(card, "land") for card in battlefield),
        _mana_pool_count(player),
        len(creatures),
        len(battlefield),
        sum(_has_type(card, "enchantment") for card in battlefield),
        sum(_has_type(card, "artifact") for card in battlefield),
        sum(_creature_stat(card, "power") for card in creatures),
        sum(_creature_stat(card, "toughness") for card in creatures),
        sum(_mana_value(card) for card in battlefield),
        _number(counters.get("poison")),
        sum(_number(entry.get("amount")) for entry in commander_damage),
    ]


def _event_amount(event: dict[str, Any], key: str = "amount") -> float:
    detail = event.get("detail")
    if not isinstance(detail, dict):
        return 1.0
    amount = _number(detail.get(key))
    return amount if amount > 0.0 else 1.0


@dataclass(frozen=True)
class FutureFeatureSnapshot:
    player_ids: tuple[str, ...]
    base_by_player: dict[str, tuple[float, ...]]
    hand_card_ids_by_player: dict[str, tuple[str, ...]]
    cumulative_flow_by_player: dict[str, tuple[float, ...]]


class FutureFeatureTracker:
    def __init__(self) -> None:
        self.last_sequence = -1
        self.source_controllers: dict[str, str] = {}
        self.cumulative_flows: dict[str, list[float]] = {}

    def _values(self, player_id: str) -> list[float]:
        return self.cumulative_flows.setdefault(player_id, [0.0] * 5)

    def _observe_event(self, event: dict[str, Any]) -> None:
        player_id = event.get("playerId")
        card_id = event.get("cardInstanceId")
        if player_id and card_id:
            self.source_controllers[str(card_id)] = str(player_id)
        kind = str(event.get("kind", ""))
        if kind == "cardDrawn" and player_id:
            self._values(str(player_id))[0] += 1.0
        elif kind == "spellCast" and player_id:
            self._values(str(player_id))[1] += 1.0
        elif (
            kind == "permanentDied"
            and player_id
            and isinstance(event.get("detail"), dict)
            and event["detail"].get("wasCreature") is True
        ):
            self._values(str(player_id))[2] += 1.0
        elif kind == "damageDealt":
            detail = event.get("detail")
            detail = detail if isinstance(detail, dict) else {}
            source = detail.get("sourceControllerId")
            if source is None and detail.get("source") is not None:
                source = self.source_controllers.get(str(detail["source"]))
            if source:
                self._values(str(source))[3] += _event_amount(event)
        elif kind == "manaAdded" and player_id:
            detail = event.get("detail")
            if isinstance(detail, dict) and isinstance(detail.get("mana"), list):
                amount = len(detail["mana"])
            elif isinstance(detail, dict) and detail.get("mana") is not None:
                amount = 1
            else:
                amount = _event_amount(event)
            self._values(str(player_id))[4] += amount

    def snapshot(self, state: dict[str, Any]) -> FutureFeatureSnapshot:
        events = state.get("events")
        if isinstance(events, list):
            for event in events:
                if not isinstance(event, dict):
                    continue
                sequence = int(event.get("sequence", -1))
                if sequence <= self.last_sequence:
                    continue
                self._observe_event(event)
                self.last_sequence = max(self.last_sequence, sequence)
        players = state.get("players")
        players = players if isinstance(players, list) else []
        player_ids = tuple(str(player.get("id", "")) for player in players)
        return FutureFeatureSnapshot(
            player_ids=player_ids,
            base_by_player={
                str(player.get("id", "")): tuple(_player_base_features(player))
                for player in players
                if isinstance(player, dict)
            },
            hand_card_ids_by_player={
                str(player.get("id", "")): _hand_card_identities(player)
                for player in players
                if isinstance(player, dict)
            },
            cumulative_flow_by_player={
                player_id: tuple(values)
                for player_id, values in self.cumulative_flows.items()
            },
        )


def future_feature_targets_from_snapshots(
    current: FutureFeatureSnapshot,
    futures: list[FutureFeatureSnapshot],
    acting_player_id: str,
    *,
    player_slots: int,
    horizons: int,
) -> tuple[torch.Tensor, torch.Tensor]:
    player_ids = list(current.player_ids)
    acting_index = (
        player_ids.index(acting_player_id) if acting_player_id in player_ids else 0
    )
    relative_ids = (
        player_ids[acting_index:] + player_ids[:acting_index] if player_ids else []
    )[:player_slots]
    targets = torch.full(
        (horizons, player_slots, len(FUTURE_FEATURE_NAMES)),
        -1.0,
        dtype=torch.float32,
    )
    mask = torch.zeros_like(targets, dtype=torch.bool)
    for horizon in range(horizons):
        if not futures:
            break
        future = futures[min(horizon, len(futures) - 1)]
        for slot, player_id in enumerate(relative_ids):
            base = future.base_by_player.get(player_id)
            if base is None:
                continue
            current_flow = current.cumulative_flow_by_player.get(player_id, (0.0,) * 5)
            future_flow = future.cumulative_flow_by_player.get(player_id, (0.0,) * 5)
            flow = [
                max(0.0, future_value - current_value)
                for current_value, future_value in zip(current_flow, future_flow)
            ]
            current_hand = Counter(current.hand_card_ids_by_player.get(player_id, ()))
            future_hand = Counter(future.hand_card_ids_by_player.get(player_id, ()))
            retained_hand_count = sum((current_hand & future_hand).values())
            total_hand_count = sum(future_hand.values())
            new_hand_count = total_hand_count - retained_hand_count
            raw = [
                base[0],
                retained_hand_count,
                new_hand_count,
                total_hand_count,
                *base[1:],
                *flow,
            ]
            targets[horizon, slot] = torch.tensor(
                [normalize_nonnegative(value) for value in raw],
                dtype=torch.float32,
            )
            mask[horizon, slot] = True
    return targets, mask


def future_feature_targets(
    current_state: dict[str, Any],
    future_states: list[dict[str, Any]],
    acting_player_id: str,
    *,
    player_slots: int,
    horizons: int,
) -> tuple[torch.Tensor, torch.Tensor]:
    if len(FUTURE_FEATURE_NAMES) == 0:
        raise RuntimeError("future feature schema cannot be empty")
    tracker = FutureFeatureTracker()
    current = tracker.snapshot(current_state)
    futures = [tracker.snapshot(state) for state in future_states]
    return future_feature_targets_from_snapshots(
        current,
        futures,
        acting_player_id,
        player_slots=player_slots,
        horizons=horizons,
    )
