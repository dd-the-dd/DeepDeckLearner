from __future__ import annotations

from oracle_ai.agents.protocol import (
    DecisionRequest,
    DecisionResolvedRequest,
    DecisionResponse,
    FullObservation,
    GameEndedRequest,
    GameEventRequest,
    ObservationDelta,
    StartingSituationRequest,
)


class MagicAgent:
    """Typed callback surface implemented by rule-based or learned agents."""

    async def analyze_starting_situation(self, request: StartingSituationRequest) -> None:
        pass

    async def receive_full_observation(self, request: FullObservation) -> None:
        pass

    async def apply_observation_delta(self, request: ObservationDelta) -> None:
        pass

    async def receive_game_event(self, request: GameEventRequest) -> None:
        pass

    async def make_decision(self, request: DecisionRequest) -> DecisionResponse:
        raise NotImplementedError

    async def decision_resolved(self, request: DecisionResolvedRequest) -> None:
        pass

    async def game_ended(self, request: GameEndedRequest) -> None:
        pass
