from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import IntEnum
from typing import Any

import torch


class TokenType(IntEnum):
    GAME_CONFIGURATION = 0
    GAME_PHASE = 1
    PLAYER_STATS = 2
    CARD_STATS = 3
    PERMANENT_STATS = 4
    ORACLE_TEXT = 5
    LEGAL_ACTION = 6


NUMERIC_FEATURE_NAMES = (
    "life",
    "hand_count",
    "library_count",
    "battlefield_count",
    "graveyard_count",
    "exile_count",
    "sideboard_count",
    "mana_white",
    "mana_blue",
    "mana_black",
    "mana_red",
    "mana_green",
    "mana_colorless",
    "mana_other",
    "land_plays_remaining",
    "max_hand_size",
    "permanent_count",
    "creature_count",
    "land_count",
    "artifact_count",
    "enchantment_count",
    "planeswalker_count",
    "battle_count",
    "tapped_count",
    "summoning_sick_count",
    "token_count",
    "stack_spell_count",
    "power",
    "toughness",
    "damage_marked",
    "power_modifier",
    "toughness_modifier",
    "counter_count",
    "mana_generic_cost",
    "mana_white_cost",
    "mana_blue_cost",
    "mana_black_cost",
    "mana_red_cost",
    "mana_green_cost",
    "mana_colorless_cost",
    "mana_x_cost",
    "player_count",
    "turn_number",
    "stack_count",
    "attacker_count",
    "blocker_count",
    "event_count",
    "permission_count",
    "rule_modifier_count",
    "phase_sin",
    "phase_cos",
    "payment_source_count",
    "target_count",
    "decision_count",
    "has_card",
    "has_attacker",
    "has_blocker",
)
NUMERIC_FEATURE_INDEX = {
    name: index for index, name in enumerate(NUMERIC_FEATURE_NAMES)
}
NUMERIC_FEATURE_DIM = len(NUMERIC_FEATURE_NAMES)

_WORD_PATTERN = re.compile(r"[\w]+(?:['’][\w]+)?", re.UNICODE)
_MANA_PATTERN = re.compile(r"\{([^}]+)\}")
_VOLATILE_TEXT_KEYS = {
    "id",
    "instanceId",
    "cardInstanceId",
    "sourceCardInstanceId",
    "attackerId",
    "blockerId",
}
_CYCLIC_FEATURES = {"phase_sin", "phase_cos"}
_GAME_PHASES = (
    "untap",
    "upkeep",
    "draw",
    "precombatmain",
    "declareattackers",
    "declareblockers",
    "combatdamage",
    "postcombatmain",
    "endstep",
    "cleanup",
)


def normalize_nonnegative(value: int | float | bool) -> float:
    numeric = float(value)
    if numeric <= 0.0:
        return -1.0
    return numeric / (numeric + 20.0) - 1.0


@dataclass(frozen=True)
class StructuredTokens:
    numeric: torch.Tensor
    word_ids: torch.Tensor
    relative_players: torch.Tensor
    token_types: torch.Tensor
    numeric_mask: torch.Tensor

    def to(self, device: torch.device) -> StructuredTokens:
        return StructuredTokens(
            numeric=self.numeric.to(device),
            word_ids=self.word_ids.to(device),
            relative_players=self.relative_players.to(device),
            token_types=self.token_types.to(device),
            numeric_mask=self.numeric_mask.to(device),
        )


@dataclass(frozen=True)
class StructuredEncodedDecision:
    state_tokens: StructuredTokens
    action_tokens: StructuredTokens
    state_padding_mask: None = None
    state_token_labels: tuple[str, ...] = ()
    action_token_labels: tuple[str, ...] = ()


@dataclass(frozen=True)
class _Token:
    numeric: dict[str, float]
    words: str
    relative_player: int
    token_type: TokenType
    has_numeric: bool


def _definition(card: dict[str, Any]) -> dict[str, Any]:
    definition = card.get("definition")
    return definition if isinstance(definition, dict) else card


def _parse_number(value: Any) -> float:
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return 0.0


def _mana_values(mana_cost: Any) -> dict[str, float]:
    values = {
        "mana_generic_cost": 0.0,
        "mana_white_cost": 0.0,
        "mana_blue_cost": 0.0,
        "mana_black_cost": 0.0,
        "mana_red_cost": 0.0,
        "mana_green_cost": 0.0,
        "mana_colorless_cost": 0.0,
        "mana_x_cost": 0.0,
    }
    symbols = _MANA_PATTERN.findall(str(mana_cost or "").upper())
    for symbol in symbols:
        if symbol.isdigit():
            values["mana_generic_cost"] += int(symbol)
            continue
        if "X" in symbol:
            values["mana_x_cost"] += 1
        for color, feature in (
            ("W", "mana_white_cost"),
            ("U", "mana_blue_cost"),
            ("B", "mana_black_cost"),
            ("R", "mana_red_cost"),
            ("G", "mana_green_cost"),
            ("C", "mana_colorless_cost"),
        ):
            if color in symbol:
                values[feature] += 1
    return values


def _card_numeric(card: dict[str, Any]) -> dict[str, float]:
    definition = _definition(card)
    counters = card.get("counters")
    counter_total = (
        sum(max(0.0, _parse_number(value)) for value in counters.values())
        if isinstance(counters, dict)
        else 0.0
    )
    values = {
        "power": _parse_number(definition.get("power")),
        "toughness": _parse_number(definition.get("toughness")),
        "damage_marked": _parse_number(card.get("damageMarked")),
        "power_modifier": _parse_number(card.get("powerModifier")),
        "toughness_modifier": _parse_number(card.get("toughnessModifier")),
        "counter_count": counter_total,
        "has_card": 1.0,
    }
    values.update(_mana_values(definition.get("manaCost")))
    return values


def _card_words(card: dict[str, Any], zone: str) -> str:
    definition = _definition(card)
    state_words = [
        zone,
        str(definition.get("name", "")),
        str(definition.get("typeLine", "")),
        str(definition.get("manaCost", "")),
    ]
    if card.get("tapped"):
        state_words.append("tapped")
    if card.get("summoningSick"):
        state_words.append("summoning sick")
    if definition.get("isToken"):
        state_words.append("token")
    return " ".join(state_words)


def _rules(card: dict[str, Any]) -> list[Any]:
    rules = _definition(card).get("rules")
    return list(rules) if isinstance(rules, list) else []


def _semantic_fragments(value: Any) -> Iterable[str]:
    if value is None:
        yield "null"
    elif isinstance(value, dict):
        for key in sorted(value):
            if key in _VOLATILE_TEXT_KEYS:
                continue
            yield key
            yield from _semantic_fragments(value[key])
    elif isinstance(value, list):
        for item in value:
            yield from _semantic_fragments(item)
    elif isinstance(value, (str, int, float, bool)):
        yield str(value)
    else:
        yield json.dumps(value, default=str, sort_keys=True)


def _type_counts(permanents: list[dict[str, Any]]) -> dict[str, float]:
    values = {
        "permanent_count": float(len(permanents)),
        "creature_count": 0.0,
        "land_count": 0.0,
        "artifact_count": 0.0,
        "enchantment_count": 0.0,
        "planeswalker_count": 0.0,
        "battle_count": 0.0,
        "tapped_count": 0.0,
        "summoning_sick_count": 0.0,
        "token_count": 0.0,
    }
    for permanent in permanents:
        definition = _definition(permanent)
        type_line = str(definition.get("typeLine", "")).casefold()
        for type_name, feature in (
            ("creature", "creature_count"),
            ("land", "land_count"),
            ("artifact", "artifact_count"),
            ("enchantment", "enchantment_count"),
            ("planeswalker", "planeswalker_count"),
            ("battle", "battle_count"),
        ):
            if type_name in type_line:
                values[feature] += 1
        values["tapped_count"] += float(bool(permanent.get("tapped")))
        values["summoning_sick_count"] += float(
            bool(permanent.get("summoningSick"))
        )
        values["token_count"] += float(bool(definition.get("isToken")))
    return values


def _mana_pool_values(mana_pool: Any) -> dict[str, float]:
    values = {
        "mana_white": 0.0,
        "mana_blue": 0.0,
        "mana_black": 0.0,
        "mana_red": 0.0,
        "mana_green": 0.0,
        "mana_colorless": 0.0,
        "mana_other": 0.0,
    }
    feature_by_symbol = {
        "W": "mana_white",
        "U": "mana_blue",
        "B": "mana_black",
        "R": "mana_red",
        "G": "mana_green",
        "C": "mana_colorless",
    }
    for mana in mana_pool if isinstance(mana_pool, list) else []:
        symbol = str(mana.get("symbol", "")).upper() if isinstance(mana, dict) else ""
        values[feature_by_symbol.get(symbol, "mana_other")] += 1
    return values


class StructuredObservationEncoder:
    def __init__(
        self,
        *,
        word_vocab_size: int = 32768,
        max_words: int = 32,
        max_relative_players: int = 8,
        max_state_tokens: int = 512,
    ) -> None:
        if word_vocab_size < 2:
            raise ValueError("word_vocab_size must reserve padding plus one word")
        self.word_vocab_size = word_vocab_size
        self.max_words = max_words
        self.max_relative_players = max_relative_players
        self.max_state_tokens = max_state_tokens
        self.no_player_position = max_relative_players

    def _card_words(self, card: dict[str, Any], zone: str) -> str:
        return _card_words(card, zone)

    def _oracle_identity_words(self, definition: dict[str, Any]) -> str:
        return " ".join(
            (
                str(definition.get("name", "")),
                str(definition.get("typeLine", "")),
            )
        )

    def _action_words(self, action: dict[str, Any]) -> list[str]:
        return [
            str(action.get("kind", "")),
            str(action.get("label", "")),
        ]

    def _action_decision_words(self, action: dict[str, Any]) -> Iterable[str]:
        return _semantic_fragments(action.get("decisions", {}))

    def _action_entity_words(
        self,
        card: dict[str, Any],
        role: str,
    ) -> list[str]:
        return [self._card_words(card, role)]

    def _word_ids(self, text: str) -> torch.Tensor:
        identifiers = []
        for word in _WORD_PATTERN.findall(text.casefold())[: self.max_words]:
            digest = hashlib.blake2b(word.encode("utf-8"), digest_size=8).digest()
            identifiers.append(
                1 + int.from_bytes(digest, "little") % (self.word_vocab_size - 1)
            )
        identifiers.extend([0] * (self.max_words - len(identifiers)))
        return torch.tensor(identifiers, dtype=torch.long)

    def _numeric(self, values: dict[str, float]) -> torch.Tensor:
        return torch.tensor(
            [
                float(values.get(name, 0.0))
                if name in _CYCLIC_FEATURES
                else normalize_nonnegative(values.get(name, 0.0))
                for name in NUMERIC_FEATURE_NAMES
            ],
            dtype=torch.float32,
        )

    def _pack(self, tokens: list[_Token]) -> StructuredTokens:
        if not tokens:
            raise ValueError("structured encoding requires at least one token")
        return StructuredTokens(
            numeric=torch.stack([self._numeric(token.numeric) for token in tokens]),
            word_ids=torch.stack([self._word_ids(token.words) for token in tokens]),
            relative_players=torch.tensor(
                [token.relative_player for token in tokens],
                dtype=torch.long,
            ),
            token_types=torch.tensor(
                [int(token.token_type) for token in tokens],
                dtype=torch.long,
            ),
            numeric_mask=torch.tensor(
                [token.has_numeric for token in tokens],
                dtype=torch.bool,
            ),
        )

    def _player_positions(
        self,
        players: list[dict[str, Any]],
        active_player_index: int,
    ) -> dict[str, int]:
        player_count = max(1, len(players))
        active_player_index %= player_count
        return {
            str(player.get("id")): min(
                (index - active_player_index) % player_count,
                self.max_relative_players - 1,
            )
            for index, player in enumerate(players)
        }

    def _decision_player_positions(
        self,
        state: dict[str, Any],
        players: list[dict[str, Any]],
        acting_player_id: str | None,
    ) -> dict[str, int]:
        active_player_index = int(state.get("activePlayer", 0) or 0)
        return self._player_positions(players, active_player_index)

    def _oracle_tokens(
        self,
        card: dict[str, Any],
        relative_player: int,
    ) -> list[_Token]:
        definition = _definition(card)
        identity = self._oracle_identity_words(definition)
        return [
            _Token(
                numeric={},
                words=" ".join((identity, *_semantic_fragments(rule))),
                relative_player=relative_player,
                token_type=TokenType.ORACLE_TEXT,
                has_numeric=False,
            )
            for rule in _rules(card)
        ]

    def _visible_entities(
        self,
        state: dict[str, Any],
        acting_player_id: str | None,
        positions: dict[str, int],
    ) -> tuple[list[_Token], list[_Token], dict[str, tuple[dict[str, Any], int]]]:
        stat_tokens: list[_Token] = []
        oracle_tokens: list[_Token] = []
        entities: dict[str, tuple[dict[str, Any], int]] = {}
        players = state.get("players")
        for player in players if isinstance(players, list) else []:
            player_id = str(player.get("id", ""))
            relative_player = positions.get(player_id, self.no_player_position)
            battlefield = (
                list(player.get("battlefield", []))
                if isinstance(player.get("battlefield"), list)
                else []
            )
            for permanent in battlefield:
                stat_tokens.append(
                    _Token(
                        numeric=_card_numeric(permanent),
                        words=self._card_words(permanent, "battlefield permanent"),
                        relative_player=relative_player,
                        token_type=TokenType.PERMANENT_STATS,
                        has_numeric=True,
                    )
                )
                oracle_tokens.extend(
                    self._oracle_tokens(permanent, relative_player)
                )
                instance_id = permanent.get("instanceId")
                if instance_id:
                    entities[str(instance_id)] = (permanent, relative_player)

            for zone_name, zone_label in (
                ("exile", "exile card"),
                ("commandZone", "command zone card"),
            ):
                zone_cards = player.get(zone_name)
                for card in zone_cards if isinstance(zone_cards, list) else []:
                    stat_tokens.append(
                        _Token(
                            numeric=_card_numeric(card),
                            words=self._card_words(card, zone_label),
                            relative_player=relative_player,
                            token_type=TokenType.CARD_STATS,
                            has_numeric=True,
                        )
                    )
                    oracle_tokens.extend(self._oracle_tokens(card, relative_player))
                    instance_id = card.get("instanceId")
                    if instance_id:
                        entities[str(instance_id)] = (card, relative_player)

            if player_id != acting_player_id:
                continue
            hand = (
                list(player.get("hand", []))
                if isinstance(player.get("hand"), list)
                else []
            )
            for card in hand:
                stat_tokens.append(
                    _Token(
                        numeric=_card_numeric(card),
                        words=self._card_words(card, "hand card"),
                        relative_player=relative_player,
                        token_type=TokenType.CARD_STATS,
                        has_numeric=True,
                    )
                )
                oracle_tokens.extend(self._oracle_tokens(card, relative_player))
                instance_id = card.get("instanceId")
                if instance_id:
                    entities[str(instance_id)] = (card, relative_player)

        stack = state.get("stack")
        for stack_object in stack if isinstance(stack, list) else []:
            card = stack_object.get("card")
            if not isinstance(card, dict):
                continue
            controller = str(stack_object.get("controller", ""))
            relative_player = positions.get(controller, self.no_player_position)
            stat_tokens.append(
                _Token(
                    numeric=_card_numeric(card),
                    words=self._card_words(card, "stack spell ability"),
                    relative_player=relative_player,
                    token_type=TokenType.CARD_STATS,
                    has_numeric=True,
                )
            )
            oracle_tokens.extend(self._oracle_tokens(card, relative_player))
            instance_id = card.get("instanceId")
            if instance_id:
                entities[str(instance_id)] = (card, relative_player)
        return stat_tokens, oracle_tokens, entities

    def _player_tokens(
        self,
        state: dict[str, Any],
        positions: dict[str, int],
    ) -> list[_Token]:
        stack = state.get("stack")
        stack_objects = list(stack) if isinstance(stack, list) else []
        players = state.get("players")
        result: list[_Token] = []
        for player in players if isinstance(players, list) else []:
            player_id = str(player.get("id", ""))
            battlefield = (
                list(player.get("battlefield", []))
                if isinstance(player.get("battlefield"), list)
                else []
            )
            values = {
                "life": _parse_number(player.get("life")),
                "hand_count": len(player.get("hand", [])),
                "library_count": len(player.get("library", [])),
                "battlefield_count": len(battlefield),
                "graveyard_count": len(player.get("graveyard", [])),
                "exile_count": len(player.get("exile", [])),
                "sideboard_count": len(player.get("sideboard", [])),
                "land_plays_remaining": _parse_number(
                    player.get("landPlaysRemaining")
                ),
                "max_hand_size": _parse_number(player.get("maxHandSize")),
                "stack_spell_count": sum(
                    1
                    for stack_object in stack_objects
                    if stack_object.get("controller") == player_id
                ),
            }
            values.update(_type_counts(battlefield))
            values.update(_mana_pool_values(player.get("manaPool")))
            result.append(
                _Token(
                    numeric=values,
                    words="lost" if player.get("hasLost") else "",
                    relative_player=positions.get(
                        player_id,
                        self.no_player_position,
                    ),
                    token_type=TokenType.PLAYER_STATS,
                    has_numeric=True,
                )
            )
        return result

    def _configuration_token(
        self,
        state: dict[str, Any],
        player_count: int,
    ) -> _Token:
        combat = state.get("combat")
        combat = combat if isinstance(combat, dict) else {}
        decision_context = state.get("_decisionContext")
        decision_context = (
            decision_context if isinstance(decision_context, dict) else {}
        )
        outcome = state.get("outcome")
        outcome = outcome if isinstance(outcome, dict) else {}
        values = {
            "player_count": player_count,
            "turn_number": _parse_number(
                state.get("turnNumber", state.get("turn", 0))
            ),
            "stack_count": len(state.get("stack", [])),
            "attacker_count": len(combat.get("attackers", [])),
            "blocker_count": len(combat.get("blockers", [])),
            "event_count": len(state.get("events", [])),
            "permission_count": len(state.get("permissions", [])),
            "rule_modifier_count": len(state.get("ruleModifiers", [])),
        }
        words = " ".join(
            str(value)
            for value in (
                state.get("schemaVersion", ""),
                state.get("status", ""),
                state.get("step", ""),
                decision_context.get("kind", ""),
                outcome.get("reason", ""),
                "game",
                "mode",
                state.get("gameMode", decision_context.get("gameMode", "")),
            )
        )
        return _Token(
            numeric=values,
            words=words,
            relative_player=self.no_player_position,
            token_type=TokenType.GAME_CONFIGURATION,
            has_numeric=True,
        )

    def _mulligan_configuration_tokens(self, state: dict[str, Any]) -> list[_Token]:
        context = state.get("_decisionContext")
        if not isinstance(context, dict):
            return []
        enabled = 1 if context.get("mulliganEnabled") else 0
        maximum = context.get("maxMulligans")
        maximum = "none" if maximum is None else maximum
        rules = " ".join(
            str(value)
            for value in (
                "mulligan",
                "rules",
                "enabled",
                enabled,
                "openinghand",
                context.get("openingHandSize"),
                "free",
                context.get("freeMulligans"),
                "maximum",
                maximum,
            )
        )
        progress = " ".join(
            str(value)
            for value in (
                "mulligan",
                "progress",
                "taken",
                context.get("mulligansTaken"),
                "freeremaining",
                context.get("freeMulligansRemaining"),
                "paid",
                context.get("paidMulligansTaken"),
                "remaining",
                context.get("mulligansRemaining"),
            )
        )
        return [
            _Token(
                numeric={},
                words=words,
                relative_player=self.no_player_position,
                token_type=TokenType.GAME_CONFIGURATION,
                has_numeric=False,
            )
            for words in (rules, progress)
        ]

    def _phase_token(self, state: dict[str, Any]) -> _Token:
        phase = str(state.get("step", "")).replace("_", "").casefold()
        try:
            phase_index = _GAME_PHASES.index(phase)
            angle = 2.0 * math.pi * phase_index / len(_GAME_PHASES)
            phase_sin = math.sin(angle)
            phase_cos = math.cos(angle)
        except ValueError:
            phase_sin = 0.0
            phase_cos = 0.0
        return _Token(
            numeric={
                "phase_sin": phase_sin,
                "phase_cos": phase_cos,
            },
            words=phase,
            relative_player=0,
            token_type=TokenType.GAME_PHASE,
            has_numeric=True,
        )

    def _action_tokens(
        self,
        actions: list[dict[str, Any]],
        acting_player_id: str | None,
        positions: dict[str, int],
        entities: dict[str, tuple[dict[str, Any], int]],
    ) -> list[_Token]:
        result: list[_Token] = []
        for action in actions:
            source_id = action.get("cardInstanceId")
            source = entities.get(str(source_id)) if source_id is not None else None
            numeric = {
                "payment_source_count": len(action.get("paymentSources", [])),
                "target_count": len(action.get("targets", {})),
                "decision_count": len(action.get("decisions", {})),
                "has_card": float(source is not None),
                "has_attacker": float(bool(action.get("attackerId"))),
                "has_blocker": float(bool(action.get("blockerId"))),
            }
            words = self._action_words(action)
            relative_player = positions.get(
                str(action.get("playerId", acting_player_id or "")),
                0,
            )
            if source is not None:
                source_card, source_position = source
                numeric.update(_card_numeric(source_card))
                words.extend(
                    self._action_entity_words(source_card, "action source")
                )
                relative_player = source_position
            words.extend(self._action_decision_words(action))
            for target in action.get("targets", {}).values():
                if not isinstance(target, dict):
                    continue
                target_instance = target.get("instanceId")
                target_entity = (
                    entities.get(str(target_instance))
                    if target_instance is not None
                    else None
                )
                if target_entity is not None:
                    words.extend(
                        self._action_entity_words(target_entity[0], "target")
                    )
                else:
                    words.extend(_semantic_fragments(target))
            result.append(
                _Token(
                    numeric=numeric,
                    words=" ".join(words),
                    relative_player=relative_player,
                    token_type=TokenType.LEGAL_ACTION,
                    has_numeric=True,
                )
            )
        return result

    def encode(
        self,
        state: dict[str, Any],
        actions: list[dict[str, Any]],
    ) -> StructuredEncodedDecision:
        decision_context = state.get("_decisionContext")
        acting_player_id = (
            str(decision_context.get("playerId"))
            if isinstance(decision_context, dict)
            and decision_context.get("playerId") is not None
            else None
        )
        players = (
            list(state.get("players", []))
            if isinstance(state.get("players"), list)
            else []
        )
        positions = self._decision_player_positions(
            state,
            players,
            acting_player_id,
        )
        entity_tokens, oracle_tokens, entities = self._visible_entities(
            state,
            acting_player_id,
            positions,
        )
        primary_tokens = [
            self._configuration_token(state, len(players)),
            self._phase_token(state),
            *self._mulligan_configuration_tokens(state),
            *self._player_tokens(state, positions),
            *entity_tokens,
        ]
        state_tokens = (primary_tokens + oracle_tokens)[: self.max_state_tokens]
        action_tokens = self._action_tokens(
            actions,
            acting_player_id,
            positions,
            entities,
        )
        return StructuredEncodedDecision(
            state_tokens=self._pack(state_tokens),
            action_tokens=self._pack(action_tokens),
            state_token_labels=tuple(
                f"{token.token_type.name.lower()}: {token.words[:160]}"
                for token in state_tokens
            ),
            action_token_labels=tuple(
                f"{token.token_type.name.lower()}: {token.words[:160]}"
                for token in action_tokens
            ),
        )
