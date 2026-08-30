from __future__ import annotations

import json
from enum import IntEnum
from typing import Any

import torch

from oracle_ai.encoding_v2 import (
    StructuredEncodedDecision,
    StructuredTokens,
    _card_numeric,
    _definition,
    _rules,
    _semantic_fragments,
    _Token,
)
from oracle_ai.encoding_v6 import PlanningObservationEncoder, TokenTypeV6


class TokenTypeV7(IntEnum):
    GAME_CONFIGURATION = int(TokenTypeV6.GAME_CONFIGURATION)
    GAME_PHASE = int(TokenTypeV6.GAME_PHASE)
    PLAYER_STATS = int(TokenTypeV6.PLAYER_STATS)
    CARD_STATS = int(TokenTypeV6.CARD_STATS)
    PERMANENT_STATS = int(TokenTypeV6.PERMANENT_STATS)
    ORACLE_TEXT = int(TokenTypeV6.ORACLE_TEXT)
    LEGAL_ACTION = int(TokenTypeV6.LEGAL_ACTION)
    GRAVEYARD_CARD = int(TokenTypeV6.GRAVEYARD_CARD)
    GAME_EVENT = int(TokenTypeV6.GAME_EVENT)
    DECK_CARD = 9


class StrategicPlanningObservationEncoder(PlanningObservationEncoder):
    def __init__(self, *, max_deck_cards: int = 128, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        if max_deck_cards <= 0:
            raise ValueError("max_deck_cards must be positive")
        self.max_deck_cards = max_deck_cards

    @staticmethod
    def _deck_sort_key(card: dict[str, Any]) -> tuple[str, str, str]:
        definition = _definition(card)
        return (
            str(definition.get("typeLine", "")).casefold(),
            str(definition.get("manaCost", "")).casefold(),
            json.dumps(_rules(card), sort_keys=True, default=str),
        )

    def _deck_card_words(self, card: dict[str, Any]) -> str:
        return " ".join(
            (
                self._card_words(card, "known deck card"),
                *(
                    fragment
                    for rule in _rules(card)
                    for fragment in _semantic_fragments(rule)
                ),
            )
        )

    def _known_deck(
        self,
        state: dict[str, Any],
        acting_player_id: str | None,
    ) -> list[dict[str, Any]]:
        supplied = state.get("_knownDeck")
        if isinstance(supplied, list):
            cards = supplied
        else:
            cards = []
            players = (
                state.get("players")
                if isinstance(state.get("players"), list)
                else []
            )
            acting_player = next(
                (
                    player
                    for player in players
                    if str(player.get("id", "")) == acting_player_id
                ),
                None,
            )
            if acting_player is not None:
                for zone in (
                    "library",
                    "hand",
                    "battlefield",
                    "graveyard",
                    "exile",
                    "commandZone",
                ):
                    zone_cards = acting_player.get(zone)
                    if isinstance(zone_cards, list):
                        cards.extend(zone_cards)
        return sorted(
            (
                card
                for card in cards
                if isinstance(card, dict)
                and not bool(_definition(card).get("isToken"))
                and not bool(card.get("isGamePiece"))
            ),
            key=self._deck_sort_key,
        )[: self.max_deck_cards]

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
        positions = self._decision_player_positions(
            state,
            list(state.get("players", []))
            if isinstance(state.get("players"), list)
            else [],
            acting_player_id,
        )
        relative_player = positions.get(
            acting_player_id or "",
            self.no_player_position,
        )
        deck_cards = self._known_deck(state, acting_player_id)
        deck_tokens = [
            _Token(
                numeric=_card_numeric(card),
                words=self._deck_card_words(card),
                relative_player=relative_player,
                token_type=TokenTypeV7.DECK_CARD,
                has_numeric=True,
            )
            for card in deck_cards
        ]
        state_token_limit = self.max_state_tokens
        self.max_state_tokens = max(1, state_token_limit - len(deck_tokens))
        try:
            encoded = super().encode(state, actions)
        finally:
            self.max_state_tokens = state_token_limit
        existing_count = encoded.state_tokens.numeric.shape[0]
        available = max(0, state_token_limit - existing_count)
        additions = deck_tokens[:available]
        if not additions:
            return encoded
        packed = self._pack(additions)
        state_tokens = StructuredTokens(
            numeric=torch.cat((encoded.state_tokens.numeric, packed.numeric)),
            word_ids=torch.cat((encoded.state_tokens.word_ids, packed.word_ids)),
            relative_players=torch.cat(
                (encoded.state_tokens.relative_players, packed.relative_players)
            ),
            token_types=torch.cat(
                (encoded.state_tokens.token_types, packed.token_types)
            ),
            numeric_mask=torch.cat(
                (encoded.state_tokens.numeric_mask, packed.numeric_mask)
            ),
        )
        return StructuredEncodedDecision(
            state_tokens=state_tokens,
            action_tokens=encoded.action_tokens,
            state_padding_mask=encoded.state_padding_mask,
            state_token_labels=(
                *encoded.state_token_labels,
                *self._labels(additions),
            ),
            action_token_labels=encoded.action_token_labels,
        )
