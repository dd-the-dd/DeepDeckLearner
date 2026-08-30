from __future__ import annotations

from enum import IntEnum
from typing import Any

import torch

from oracle_ai.encoding_v2 import (
    StructuredEncodedDecision,
    TokenType,
    _card_numeric,
    _semantic_fragments,
    _Token,
)
from oracle_ai.encoding_v4 import OracleStructuredObservationEncoder


class TokenTypeV6(IntEnum):
    GAME_CONFIGURATION = int(TokenType.GAME_CONFIGURATION)
    GAME_PHASE = int(TokenType.GAME_PHASE)
    PLAYER_STATS = int(TokenType.PLAYER_STATS)
    CARD_STATS = int(TokenType.CARD_STATS)
    PERMANENT_STATS = int(TokenType.PERMANENT_STATS)
    ORACLE_TEXT = int(TokenType.ORACLE_TEXT)
    LEGAL_ACTION = int(TokenType.LEGAL_ACTION)
    GRAVEYARD_CARD = 7
    GAME_EVENT = 8


_STRUCTURAL_VOCABULARY = tuple(
    dict.fromkeys(
        """
        action active activated additional after all and any artifact as at attach attack
        attacker attackers battlefield before beginning block blocker blockers card cards
        cast choice choose color combat command commander configuration control controller
        cost counter counters creature damage decision defending discard draw effect end event
        exile fight first from game graveyard hand if instead kind land leave life library main
        mana may mode move next nonland number object opponent oracle owner pay permanent phase
        player priority put red replacement resolve resolution return reveal rule sacrifice source
        spell stack step target targets tapped token toughness trigger turn type untap upkeep value
        when whenever white blue black green colorless generic x you your
        passpriority playland castspell activateability keephand takemulligan bottomcard
        declareattacker finishattackers declareblocker finishblockers assigncombatdamage paylife
        declinepayment chooseresolution movecommandertocommandzone leavecommanderinzone
        precombatmain declareattackers declareblockers combatdamage postcombatmain endstep cleanup
        amount condition controllerof count create destroy dies enters leaves modify prevent
        replacementeffect search shuffle tap transform until zone add remove copy mill
        """.split()
    )
)

PAD_ID = 0
CLS_ID = 1
SEP_ID = 2
WORD_BOUNDARY_ID = 3
BYTE_ID_OFFSET = 4
BYTE_VOCABULARY_SIZE = 256
STRUCTURAL_ID_OFFSET = BYTE_ID_OFFSET + BYTE_VOCABULARY_SIZE
STRUCTURAL_WORD_IDS = {
    word: STRUCTURAL_ID_OFFSET + index
    for index, word in enumerate(_STRUCTURAL_VOCABULARY)
}
SEMANTIC_VOCABULARY_SIZE = STRUCTURAL_ID_OFFSET + len(_STRUCTURAL_VOCABULARY)


class PlanningObservationEncoder(OracleStructuredObservationEncoder):
    def __init__(
        self,
        *,
        word_vocab_size: int = SEMANTIC_VOCABULARY_SIZE,
        max_words: int = 96,
        max_relative_players: int = 8,
        max_state_tokens: int = 640,
        player_angle_steps: int = 840,
        max_events: int = 48,
    ) -> None:
        if word_vocab_size != SEMANTIC_VOCABULARY_SIZE:
            raise ValueError(
                "V6 semantic vocabulary size must be "
                f"{SEMANTIC_VOCABULARY_SIZE}, received {word_vocab_size}"
            )
        super().__init__(
            word_vocab_size=word_vocab_size,
            max_words=max_words,
            max_relative_players=max_relative_players,
            max_state_tokens=max_state_tokens,
            player_angle_steps=player_angle_steps,
        )
        self.max_events = max_events

    def _word_ids(self, text: str) -> torch.Tensor:
        words = [word.casefold() for word in text.split() if word.strip()]
        if not words:
            return torch.zeros(self.max_words, dtype=torch.long)
        identifiers = [CLS_ID]
        for word in words:
            normalized = word.strip(".,:;!?()[]{}\"'")
            structural_id = STRUCTURAL_WORD_IDS.get(normalized)
            encoded = (
                [structural_id]
                if structural_id is not None
                else [BYTE_ID_OFFSET + byte for byte in normalized.encode("utf-8")]
            )
            required = len(encoded) + (1 if len(identifiers) > 1 else 0) + 1
            if len(identifiers) + required > self.max_words:
                break
            if len(identifiers) > 1:
                identifiers.append(WORD_BOUNDARY_ID)
            identifiers.extend(encoded)
        identifiers.append(SEP_ID)
        identifiers = identifiers[: self.max_words]
        identifiers.extend([PAD_ID] * (self.max_words - len(identifiers)))
        return torch.tensor(identifiers, dtype=torch.long)

    def _visible_entities(
        self,
        state: dict[str, Any],
        acting_player_id: str | None,
        positions: dict[str, int],
    ) -> tuple[list[_Token], list[_Token], dict[str, tuple[dict[str, Any], int]]]:
        stat_tokens, oracle_tokens, entities = super()._visible_entities(
            state,
            acting_player_id,
            positions,
        )
        players = state.get("players")
        for player in players if isinstance(players, list) else []:
            player_id = str(player.get("id", ""))
            relative_player = positions.get(player_id, self.no_player_position)
            graveyard = player.get("graveyard")
            for card in graveyard if isinstance(graveyard, list) else []:
                stat_tokens.append(
                    _Token(
                        numeric=_card_numeric(card),
                        words=self._card_words(card, "graveyard card"),
                        relative_player=relative_player,
                        token_type=TokenTypeV6.GRAVEYARD_CARD,
                        has_numeric=True,
                    )
                )
                oracle_tokens.extend(self._oracle_tokens(card, relative_player))
                instance_id = card.get("instanceId")
                if instance_id:
                    entities[str(instance_id)] = (card, relative_player)
        return stat_tokens, oracle_tokens, entities

    def _event_tokens(
        self,
        state: dict[str, Any],
        positions: dict[str, int],
    ) -> list[_Token]:
        events = state.get("events")
        recent = list(events)[-self.max_events :] if isinstance(events, list) else []
        result = []
        for event in recent:
            if not isinstance(event, dict):
                continue
            player_id = event.get("playerId", event.get("controller", ""))
            result.append(
                _Token(
                    numeric={},
                    words=" ".join(("game event", *_semantic_fragments(event))),
                    relative_player=positions.get(
                        str(player_id),
                        self.no_player_position,
                    ),
                    token_type=TokenTypeV6.GAME_EVENT,
                    has_numeric=False,
                )
            )
        return result

    @staticmethod
    def _labels(tokens: list[_Token]) -> tuple[str, ...]:
        return tuple(
            f"{token.token_type.name.lower()}: {token.words[:200]}" for token in tokens
        )

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
        players = list(state.get("players", [])) if isinstance(state.get("players"), list) else []
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
            *self._event_tokens(state, positions),
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
            state_token_labels=self._labels(state_tokens),
            action_token_labels=self._labels(action_tokens),
        )
