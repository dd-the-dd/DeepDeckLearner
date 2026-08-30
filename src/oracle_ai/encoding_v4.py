from __future__ import annotations

from typing import Any

from oracle_ai.encoding_v2 import (
    _definition,
    _rules,
    _semantic_fragments,
)
from oracle_ai.encoding_v3 import CyclicStructuredObservationEncoder


class OracleStructuredObservationEncoder(CyclicStructuredObservationEncoder):
    def _card_words(self, card: dict[str, Any], zone: str) -> str:
        definition = _definition(card)
        state_words = [
            zone,
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

    def _oracle_identity_words(self, definition: dict[str, Any]) -> str:
        return " ".join(
            (
                "oracle rule",
                str(definition.get("typeLine", "")),
                str(definition.get("manaCost", "")),
            )
        )

    def _action_words(self, action: dict[str, Any]) -> list[str]:
        return [
            str(action.get("kind", "")),
            *_semantic_fragments(action.get("decisions", {})),
        ]

    def _action_decision_words(self, action: dict[str, Any]) -> tuple[str, ...]:
        return ()

    def _action_entity_words(
        self,
        card: dict[str, Any],
        role: str,
    ) -> list[str]:
        definition = _definition(card)
        words = [
            role,
            str(definition.get("typeLine", "")),
            str(definition.get("manaCost", "")),
        ]
        for rule in _rules(card):
            words.extend(_semantic_fragments(rule))
        if card.get("tapped"):
            words.append("tapped")
        if card.get("summoningSick"):
            words.extend(("summoning", "sick"))
        return words
