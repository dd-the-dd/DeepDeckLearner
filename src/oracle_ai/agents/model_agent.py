from __future__ import annotations

from copy import deepcopy

from oracle_ai.agents.base import MagicAgent
from oracle_ai.agents.protocol import (
    DecisionRequest,
    DecisionResponse,
    GameEndedRequest,
    StartingSituationRequest,
)
from oracle_ai.app import PolicyRuntime
from oracle_ai.decision_choices import expand_policy_actions


class OracleModelAgent(MagicAgent):
    """WebSocket adapter for the existing trainable V11 and V12 policies."""

    def __init__(self, runtime: PolicyRuntime) -> None:
        family = getattr(runtime.model, "model_family", None)
        if family not in {"structured-v11", "structured-v12"}:
            raise ValueError("OracleModelAgent requires a V11 or V12 model")
        self.runtime = runtime
        self._pregame_by_context: dict[str, dict] = {}
        self._known_deck_by_context: dict[str, list[dict]] = {}

    async def analyze_starting_situation(self, request: StartingSituationRequest) -> None:
        self._pregame_by_context[request.context_id] = deepcopy(request.observation)
        self._known_deck_by_context[request.context_id] = deepcopy(request.known_deck)

    async def game_ended(self, request: GameEndedRequest) -> None:
        self._pregame_by_context.pop(request.context_id, None)
        self._known_deck_by_context.pop(request.context_id, None)

    async def make_decision(self, request: DecisionRequest) -> DecisionResponse:
        state = deepcopy(request.observation)
        pregame = self._pregame_by_context.get(request.context_id)
        known_deck = self._known_deck_by_context.get(request.context_id, [])
        if known_deck:
            state["_pregameDeck"] = deepcopy(known_deck)
        if pregame is not None:
            players = pregame.get("players", [])
            own = next(
                (player for player in players if str(player.get("id")) == request.player_id),
                None,
            )
            if own is not None and not known_deck:
                state["_pregameDeck"] = [
                    card.get("definition", card)
                    for zone in (
                        "library",
                        "hand",
                        "battlefield",
                        "graveyard",
                        "exile",
                        "commandZone",
                    )
                    for card in own.get(zone, [])
                    if isinstance(card, dict)
                    and card.get("definition", card).get("id") != "hidden-card"
                ]
        decision = dict(request.decision)
        actions = expand_policy_actions(decision)
        decision_context = state.get("_decisionContext")
        decision_context = dict(decision_context) if isinstance(decision_context, dict) else {}
        decision_context.update(
            {
                "id": request.request_id,
                "playerId": request.player_id,
                "kind": decision.get("kind"),
            }
        )
        state["_decisionContext"] = decision_context
        selected, _, _ = self.runtime.choose_with_model(
            state,
            actions,
            deterministic=True,
            request_id=request.request_id,
            context_id=request.context_id,
        )
        chosen = actions[selected]
        return DecisionResponse(
            action_id=str(chosen.get("_engineActionId", chosen["id"])),
            number_value=chosen.get("_numberValue"),
        )
