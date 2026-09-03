from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

from deepdeck_agent import Agent, Decision, DecisionResponse, Game

from oracle_ai.decision_choices import expand_policy_actions


class OracleCheckpointAgent(Agent):
    """Expose a trained Oracle V11/V12 checkpoint through the public SDK agent API."""

    def __init__(self, checkpoint: str | Path, device: str = "cpu") -> None:
        checkpoint_path = Path(checkpoint)
        if not (checkpoint_path / "manifest.json").is_file() or not (
            checkpoint_path / "checkpoint.pt"
        ).is_file():
            raise ValueError("Oracle checkpoint requires manifest.json and checkpoint.pt")
        previous = {
            key: os.environ.get(key)
            for key in ("ORACLE_AI_POLICY", "ORACLE_AI_CHECKPOINT", "ORACLE_AI_DEVICE")
        }
        try:
            os.environ["ORACLE_AI_POLICY"] = "model"
            os.environ["ORACLE_AI_CHECKPOINT"] = str(checkpoint_path)
            os.environ["ORACLE_AI_DEVICE"] = device
            from oracle_ai import app as oracle_app

            self.runtime = oracle_app.runtime
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        self._known_decks: dict[str, list[dict[str, Any]]] = {}

    async def on_game_start(self, game: Game, known_deck: list[dict[str, object]]) -> None:
        game_id = str(game.raw.get("gameId", "local-playtest"))
        self._known_decks[game_id] = [copy.deepcopy(card) for card in known_deck]
        if len(self._known_decks) > 128:
            self._known_decks.pop(next(iter(self._known_decks)))

    async def on_game_end(self, outcome: dict[str, object]) -> None:
        del outcome

    async def make_decision(self, decision: Decision) -> DecisionResponse:
        choice_kind = str((decision.choice or {}).get("kind", ""))
        if choice_kind in {"numberSelection", "cardSelection", "cardOrder"}:
            return await super().make_decision(decision)
        actions = expand_policy_actions(decision.raw)
        if not actions:
            return await super().make_decision(decision)
        state = copy.deepcopy(decision.game.raw)
        game_id = str(state.get("gameId", "local-playtest"))
        known_deck = self._known_decks.get(game_id, [])
        if known_deck:
            state["_pregameDeck"] = copy.deepcopy(known_deck)
        context = state.get("_decisionContext")
        context = dict(context) if isinstance(context, dict) else {}
        context.update({
            "id": decision.request_id,
            "playerId": decision.player_id,
            "kind": decision.kind,
        })
        state["_decisionContext"] = context
        selected, _, _ = self.runtime.choose_with_model(
            state,
            actions,
            deterministic=True,
            request_id=decision.request_id,
            context_id=game_id,
        )
        chosen = actions[selected]
        return DecisionResponse(
            action_id=str(chosen.get("_engineActionId", chosen["id"])),
            number_value=chosen.get("_numberValue"),
        )


def is_oracle_checkpoint(path: str | Path | None) -> bool:
    if path is None:
        return False
    checkpoint = Path(path)
    return (checkpoint / "manifest.json").is_file() and (checkpoint / "checkpoint.pt").is_file()
