from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any

import torch


@dataclass(frozen=True)
class EncoderConfig:
    """Small deterministic feature encoder suitable for an educational baseline."""

    feature_size: int = 64
    max_state_tokens: int = 192
    max_difference_tokens: int = 96

    def __post_init__(self) -> None:
        if self.feature_size < 16:
            raise ValueError("feature_size must be at least 16")
        if self.max_state_tokens <= 0 or self.max_difference_tokens <= 0:
            raise ValueError("token limits must be positive")


@dataclass(frozen=True)
class EncodedDecision:
    state_tokens: torch.Tensor
    difference_tokens: torch.Tensor
    action_tokens: torch.Tensor

    def to(self, device: torch.device | str) -> EncodedDecision:
        return EncodedDecision(
            state_tokens=self.state_tokens.to(device),
            difference_tokens=self.difference_tokens.to(device),
            action_tokens=self.action_tokens.to(device),
        )


def _stable_bucket(text: str, size: int) -> tuple[int, float]:
    digest = hashlib.blake2b(text.encode("utf-8"), digest_size=9).digest()
    bucket = int.from_bytes(digest[:8], "big") % size
    sign = 1.0 if digest[8] & 1 else -1.0
    return bucket, sign


def _scalar_features(value: Any, prefix: str = "root") -> list[tuple[str, float]]:
    features: list[tuple[str, float]] = []
    if isinstance(value, dict):
        for key in sorted(value):
            features.extend(_scalar_features(value[key], f"{prefix}.{key}"))
    elif isinstance(value, list):
        features.append((f"{prefix}.length", math.log1p(len(value))))
        for index, child in enumerate(value):
            features.extend(_scalar_features(child, f"{prefix}[]:{index < 4}"))
    elif isinstance(value, bool):
        features.append((f"{prefix}=bool", 1.0 if value else -1.0))
    elif isinstance(value, (int, float)) and math.isfinite(float(value)):
        features.append((f"{prefix}=number", math.copysign(math.log1p(abs(value)), value)))
    elif value is not None:
        features.append((f"{prefix}={str(value).casefold()}", 1.0))
    return features


def _vector(value: Any, size: int) -> torch.Tensor:
    result = torch.zeros(size, dtype=torch.float32)
    for label, amount in _scalar_features(value):
        bucket, sign = _stable_bucket(label, size)
        result[bucket] += sign * float(amount)
    norm = result.norm()
    return result / norm if float(norm) > 0 else result


def _player_tokens(observation: dict[str, Any]) -> list[Any]:
    players = observation.get("players", [])
    if not isinstance(players, list):
        return []
    tokens: list[Any] = []
    for player in players:
        if not isinstance(player, dict):
            continue
        summary = {
            key: value
            for key, value in player.items()
            if key not in {"library", "hand", "battlefield", "graveyard", "exile", "sideboard"}
        }
        tokens.append({"kind": "player", "value": summary})
        for zone in ("hand", "battlefield", "graveyard", "exile", "commandZone"):
            cards = player.get(zone, [])
            if not isinstance(cards, list):
                continue
            tokens.extend(
                {"kind": "card", "playerId": player.get("id"), "zone": zone, "value": card}
                for card in cards
                if isinstance(card, dict)
            )
        library = player.get("library", [])
        tokens.append(
            {
                "kind": "zone-count",
                "playerId": player.get("id"),
                "zone": "library",
                "count": len(library) if isinstance(library, list) else 0,
            }
        )
    return tokens


def _state_items(observation: dict[str, Any], known_deck: list[dict[str, Any]]) -> list[Any]:
    header = {
        key: observation.get(key)
        for key in ("turnNumber", "activePlayer", "step", "priorityPlayer", "gameMode")
    }
    stack = observation.get("stack", [])
    events = observation.get("events", [])
    visible_stack = stack if isinstance(stack, list) else []
    recent_events = events[-32:] if isinstance(events, list) else []
    return [
        {"kind": "game", "value": header},
        *_player_tokens(observation),
        *(
            {"kind": "stack", "value": item}
            for item in visible_stack
            if isinstance(item, dict)
        ),
        *(
            {"kind": "event", "value": event}
            for event in recent_events
            if isinstance(event, dict)
        ),
        *({"kind": "known-deck-card", "value": card} for card in known_deck),
    ]


def _flatten_map(value: Any, prefix: str = "root") -> dict[str, str]:
    result: dict[str, str] = {}
    if isinstance(value, dict):
        for key in sorted(value):
            result.update(_flatten_map(value[key], f"{prefix}.{key}"))
    elif isinstance(value, list):
        result[f"{prefix}.length"] = str(len(value))
        for index, child in enumerate(value):
            identity = child.get("instanceId", index) if isinstance(child, dict) else index
            result.update(_flatten_map(child, f"{prefix}[{identity}]"))
    else:
        result[prefix] = json.dumps(value, sort_keys=True, ensure_ascii=False)
    return result


def _difference_items(previous: dict[str, Any] | None, current: dict[str, Any]) -> list[Any]:
    if previous is None:
        return [{"kind": "first-observation"}]
    before = _flatten_map(previous)
    after = _flatten_map(current)
    changes = []
    for path in sorted(before.keys() | after.keys()):
        old = before.get(path)
        new = after.get(path)
        if old != new:
            changes.append({"kind": "difference", "path": path, "before": old, "after": new})
    return changes or [{"kind": "no-visible-change"}]


def _pack(items: list[Any], size: int, maximum: int) -> torch.Tensor:
    selected = items[-maximum:]
    if not selected:
        selected = [{"kind": "empty"}]
    return torch.stack([_vector(item, size) for item in selected])


class DecisionEncoder:
    """Convert visible JSON and exact legal actions to deterministic tensors.

    This deliberately uses feature hashing instead of a private vocabulary. Projects can
    replace this class while retaining the model and agent interfaces.
    """

    def __init__(self, config: EncoderConfig | None = None) -> None:
        self.config = config or EncoderConfig()

    def encode(
        self,
        observation: dict[str, Any],
        actions: list[dict[str, Any]],
        *,
        previous_observation: dict[str, Any] | None = None,
        known_deck: list[dict[str, Any]] | None = None,
    ) -> EncodedDecision:
        if not actions:
            raise ValueError("a decision must contain at least one legal action")
        deck = known_deck or []
        return EncodedDecision(
            state_tokens=_pack(
                _state_items(observation, deck),
                self.config.feature_size,
                self.config.max_state_tokens,
            ),
            difference_tokens=_pack(
                _difference_items(previous_observation, observation),
                self.config.feature_size,
                self.config.max_difference_tokens,
            ),
            action_tokens=torch.stack(
                [
                    _vector(
                        {"kind": "legal-action", "value": action},
                        self.config.feature_size,
                    )
                    for action in actions
                ]
            ),
        )
