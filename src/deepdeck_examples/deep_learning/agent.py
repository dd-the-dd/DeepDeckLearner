from __future__ import annotations

import copy
import logging
from pathlib import Path
from typing import Any

import torch
from deepdeck_agent import Agent, Decision, DecisionResponse, Game

from .checkpoint import load_checkpoint
from .encoding import DecisionEncoder, EncoderConfig
from .models import PolicyV11, build_model

LOGGER = logging.getLogger(__name__)


class DeepLearningAgent(Agent):
    """Use a V11/V12 example model while Rust remains authoritative for legality."""

    def __init__(
        self,
        model: PolicyV11,
        *,
        encoder: DecisionEncoder | None = None,
        device: torch.device | str = "cpu",
        deterministic: bool = True,
    ) -> None:
        self.device = torch.device(device)
        self.model = model.to(self.device)
        self.model.eval()
        self.encoder = encoder or DecisionEncoder()
        self.deterministic = deterministic
        self._known_deck: list[dict[str, Any]] = []
        self._previous_observation: dict[str, Any] | None = None
        self._memory: torch.Tensor | None = None

    async def analyze_starting_situation(
        self,
        observation: dict[str, object],
        known_deck: list[dict[str, object]],
    ) -> None:
        del observation
        self._known_deck = [copy.deepcopy(card) for card in known_deck]

    async def on_game_start(self, game: Game, known_deck: list[dict[str, object]]) -> None:
        del game
        self._previous_observation = None
        self._memory = None
        if known_deck:
            self._known_deck = [copy.deepcopy(card) for card in known_deck]

    async def on_game_end(self, outcome: dict[str, object]) -> None:
        del outcome
        self._previous_observation = None
        self._memory = None
        self._known_deck = []

    async def make_decision(self, decision: Decision) -> DecisionResponse:
        choice_kind = str((decision.choice or {}).get("kind", ""))
        if choice_kind in {"numberSelection", "cardSelection", "cardOrder"}:
            return await super().make_decision(decision)
        actions = decision.actions
        if not actions:
            return await super().make_decision(decision)
        observation = copy.deepcopy(decision.game.raw)
        encoded = self.encoder.encode(
            observation,
            [action.raw for action in actions],
            previous_observation=self._previous_observation,
            known_deck=self._known_deck,
        ).to(self.device)
        with torch.no_grad():
            output = self.model(encoded, self._memory)
            if self.deterministic:
                selected = int(output.logits.argmax().item())
            else:
                distribution = torch.distributions.Categorical(logits=output.logits)
                selected = int(distribution.sample().item())
        self._memory = output.memory.detach()
        self._previous_observation = observation
        return decision.choose(actions[selected])


def build_deep_learning_agent(
    version: str,
    *,
    checkpoint: str | Path | None,
    device: torch.device | str = "cpu",
    allow_untrained: bool = False,
    seed: int = 1,
) -> DeepLearningAgent:
    torch.manual_seed(seed)
    if checkpoint is not None:
        model = load_checkpoint(checkpoint, device=device)
        expected_family = f"example-{version.casefold()}"
        if model.family != expected_family:
            raise ValueError(
                f"checkpoint contains {model.family}, but {expected_family} was requested"
            )
    elif allow_untrained:
        LOGGER.warning("using randomly initialized %s weights", version.upper())
        model = build_model(version)
    else:
        raise ValueError(
            "V11/V12 requires --checkpoint; use --allow-untrained only for a connectivity demo"
        )
    encoder = DecisionEncoder(EncoderConfig(feature_size=model.config.feature_size))
    return DeepLearningAgent(model, encoder=encoder, device=device)
