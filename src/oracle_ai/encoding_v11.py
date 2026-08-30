from __future__ import annotations

from enum import IntEnum
from typing import Any

import torch

from oracle_ai.encoding_v2 import (
    StructuredEncodedDecision,
    StructuredTokens,
    _card_numeric,
    _definition,
    _semantic_fragments,
    _Token,
)
from oracle_ai.encoding_v6 import PlanningObservationEncoder
from oracle_ai.encoding_v7 import StrategicPlanningObservationEncoder, TokenTypeV7


class TokenTypeV11(IntEnum):
    GAME_CONFIGURATION = 0
    GAME_PHASE = 1
    PLAYER_STATS = 2
    CARD_STATS = 3
    PERMANENT_STATS = 4
    ORACLE_TEXT = 5
    LEGAL_ACTION = 6
    GRAVEYARD_CARD = 7
    GAME_EVENT = 8
    PREGAME_DECK_CARD = int(TokenTypeV7.DECK_CARD)
    COMMANDER = 10
    STATE_DELTA = 11
    DECISION_EVENT = 12


_ZONES = (
    "library",
    "hand",
    "battlefield",
    "graveyard",
    "exile",
    "commandZone",
)


def _players_by_id(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    players = state.get("players")
    if not isinstance(players, list):
        return {}
    return {
        str(player.get("id")): player
        for player in players
        if isinstance(player, dict) and player.get("id") is not None
    }


def _zone_by_instance(player: dict[str, Any]) -> dict[str, tuple[str, dict[str, Any]]]:
    result: dict[str, tuple[str, dict[str, Any]]] = {}
    for zone in _ZONES:
        cards = player.get(zone)
        for card in cards if isinstance(cards, list) else []:
            if not isinstance(card, dict):
                continue
            instance_id = str(card.get("instanceId", ""))
            if instance_id:
                result[instance_id] = (zone, card)
    return result


class AlphaStarObservationEncoder(StrategicPlanningObservationEncoder):
    """V11 observation with immutable pre-game knowledge and state differences.

    The caller supplies `_pregameDeck` and `_pregameCommanders` from a snapshot
    captured before the shuffled game advances.  Only card definitions are used;
    library instance order is never encoded.  `_previousObservation` is scoped to
    the same acting player and produces a separate stream of delta/event tokens.
    """

    def __init__(
        self,
        *,
        max_delta_tokens: int = 96,
        max_commander_cards: int = 8,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        if max_delta_tokens <= 0:
            raise ValueError("max_delta_tokens must be positive")
        if max_commander_cards <= 0:
            raise ValueError("max_commander_cards must be positive")
        self.max_delta_tokens = max_delta_tokens
        self.max_commander_cards = max_commander_cards

    def _pregame_deck(self, state: dict[str, Any], player_id: str | None) -> list[dict[str, Any]]:
        supplied = state.get("_pregameDeck")
        if isinstance(supplied, list):
            return sorted(
                (card for card in supplied if isinstance(card, dict)),
                key=self._deck_sort_key,
            )[: self.max_deck_cards]
        return self._known_deck(state, player_id)

    def _commander_tokens(
        self,
        state: dict[str, Any],
        positions: dict[str, int],
    ) -> list[_Token]:
        supplied = state.get("_pregameCommanders")
        commanders: list[tuple[str, dict[str, Any]]] = []
        if isinstance(supplied, list):
            for item in supplied:
                if not isinstance(item, dict):
                    continue
                player_id = str(item.get("playerId", ""))
                card = item.get("card")
                if isinstance(card, dict):
                    commanders.append((player_id, card))
        if not commanders:
            for player_id, player in _players_by_id(state).items():
                seen: set[str] = set()
                for _, card in _zone_by_instance(player).values():
                    definition = _definition(card)
                    identity = str(definition.get("id", definition.get("name", "")))
                    if definition.get("isCommander") and identity not in seen:
                        seen.add(identity)
                        commanders.append((player_id, definition))
        commanders.sort(
            key=lambda item: (
                positions.get(item[0], self.no_player_position),
                self._deck_sort_key(item[1]),
            )
        )
        return [
            _Token(
                numeric=_card_numeric(card),
                words=" ".join(
                    (
                        self._card_words(card, "pregame commander"),
                        *(fragment for rule in (card.get("rules") or []) for fragment in _semantic_fragments(rule)),
                    )
                ),
                relative_player=positions.get(player_id, self.no_player_position),
                token_type=TokenTypeV11.COMMANDER,
                has_numeric=True,
            )
            for player_id, card in commanders[: self.max_commander_cards]
        ]

    @staticmethod
    def _event_sequence(event: dict[str, Any]) -> int:
        try:
            return int(event.get("sequence", -1))
        except (TypeError, ValueError):
            return -1

    def _delta_tokens(
        self,
        state: dict[str, Any],
        positions: dict[str, int],
    ) -> list[_Token]:
        previous = state.get("_previousObservation")
        if not isinstance(previous, dict):
            return []
        current_players = _players_by_id(state)
        previous_players = _players_by_id(previous)
        tokens: list[_Token] = []
        scalar_fields = (
            ("life", "life"),
            ("landPlaysRemaining", "land plays remaining"),
        )
        for player_id, current in current_players.items():
            old = previous_players.get(player_id, {})
            relative_player = positions.get(player_id, self.no_player_position)
            changes: list[str] = []
            numeric: dict[str, float] = {}
            for field, label in scalar_fields:
                try:
                    delta = float(current.get(field, 0)) - float(old.get(field, 0))
                except (TypeError, ValueError):
                    delta = 0.0
                if delta:
                    direction = "increased" if delta > 0 else "decreased"
                    changes.append(f"{label} {direction} by {abs(delta):g}")
                    if field == "life":
                        numeric["life"] = abs(delta)
                    elif field == "landPlaysRemaining":
                        numeric["land_plays_remaining"] = abs(delta)
            for zone in _ZONES:
                current_count = len(current.get(zone, [])) if isinstance(current.get(zone), list) else 0
                old_count = len(old.get(zone, [])) if isinstance(old.get(zone), list) else 0
                delta = current_count - old_count
                if delta:
                    direction = "gained" if delta > 0 else "lost"
                    changes.append(f"{zone} {direction} {abs(delta)} cards")
                    feature = {
                        "library": "library_count",
                        "hand": "hand_count",
                        "battlefield": "battlefield_count",
                        "graveyard": "graveyard_count",
                        "exile": "exile_count",
                    }.get(zone)
                    if feature:
                        numeric[feature] = abs(delta)
            if changes:
                tokens.append(
                    _Token(
                        numeric=numeric,
                        words="state difference " + " ".join(changes),
                        relative_player=relative_player,
                        token_type=TokenTypeV11.STATE_DELTA,
                        has_numeric=True,
                    )
                )

            current_zones = _zone_by_instance(current)
            previous_zones = _zone_by_instance(old)
            for instance_id in sorted(current_zones.keys() | previous_zones.keys()):
                before = previous_zones.get(instance_id)
                after = current_zones.get(instance_id)
                before_zone = before[0] if before else "outside"
                after_zone = after[0] if after else "outside"
                if before_zone == after_zone:
                    continue
                card = (after or before)[1]
                tokens.append(
                    _Token(
                        numeric=_card_numeric(card),
                        words=(
                            f"state difference card moved from {before_zone} to {after_zone} "
                            f"{self._card_words(card, after_zone)}"
                        ),
                        relative_player=relative_player,
                        token_type=TokenTypeV11.STATE_DELTA,
                        has_numeric=True,
                    )
                )

        previous_events = previous.get("events")
        previous_max = max(
            (
                self._event_sequence(event)
                for event in previous_events
                if isinstance(event, dict)
            ),
            default=-1,
        ) if isinstance(previous_events, list) else -1
        events = state.get("events")
        for event in events if isinstance(events, list) else []:
            if not isinstance(event, dict) or self._event_sequence(event) <= previous_max:
                continue
            player_id = str(event.get("playerId", event.get("controller", "")))
            tokens.append(
                _Token(
                    numeric={},
                    words=" ".join(("decision or effect since last decision", *_semantic_fragments(event))),
                    relative_player=positions.get(player_id, self.no_player_position),
                    token_type=TokenTypeV11.DECISION_EVENT,
                    has_numeric=False,
                )
            )
        return tokens[-self.max_delta_tokens :]

    def encode(
        self,
        state: dict[str, Any],
        actions: list[dict[str, Any]],
    ) -> StructuredEncodedDecision:
        context = state.get("_decisionContext")
        acting_player_id = (
            str(context.get("playerId"))
            if isinstance(context, dict) and context.get("playerId") is not None
            else None
        )
        players = list(state.get("players", [])) if isinstance(state.get("players"), list) else []
        positions = self._decision_player_positions(state, players, acting_player_id)
        relative_player = positions.get(acting_player_id or "", self.no_player_position)
        pregame_tokens = [
            _Token(
                numeric=_card_numeric(card),
                words=self._deck_card_words(card).replace("known deck card", "pregame deck card", 1),
                relative_player=relative_player,
                token_type=TokenTypeV11.PREGAME_DECK_CARD,
                has_numeric=True,
            )
            for card in self._pregame_deck(state, acting_player_id)
        ]
        additions = [
            *pregame_tokens,
            *self._commander_tokens(state, positions),
            *self._delta_tokens(state, positions),
        ]
        state_limit = self.max_state_tokens
        self.max_state_tokens = max(1, state_limit - len(additions))
        try:
            encoded = PlanningObservationEncoder.encode(self, state, actions)
        finally:
            self.max_state_tokens = state_limit
        available = max(0, state_limit - encoded.state_tokens.numeric.shape[0])
        additions = additions[:available]
        if not additions:
            return encoded
        packed = self._pack(additions)
        return StructuredEncodedDecision(
            state_tokens=StructuredTokens(
                numeric=torch.cat((encoded.state_tokens.numeric, packed.numeric)),
                word_ids=torch.cat((encoded.state_tokens.word_ids, packed.word_ids)),
                relative_players=torch.cat((encoded.state_tokens.relative_players, packed.relative_players)),
                token_types=torch.cat((encoded.state_tokens.token_types, packed.token_types)),
                numeric_mask=torch.cat((encoded.state_tokens.numeric_mask, packed.numeric_mask)),
            ),
            action_tokens=encoded.action_tokens,
            state_padding_mask=encoded.state_padding_mask,
            state_token_labels=(*encoded.state_token_labels, *self._labels(additions)),
            action_token_labels=encoded.action_token_labels,
        )
