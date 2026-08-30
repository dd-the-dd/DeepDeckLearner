from __future__ import annotations

from dataclasses import dataclass

import torch
from torch import nn

from oracle_ai.encoding_v2 import StructuredTokens
from oracle_ai.model_v11 import MagicTransformerActorCriticV11, ModelConfigV11


@dataclass(frozen=True)
class ModelConfigV12(ModelConfigV11):
    """Two-player AlphaStar configuration with an antisymmetric value head."""

    multiplayer_value_slots: int = 2


class MagicTransformerActorCriticV12(MagicTransformerActorCriticV11):
    """V11 state/difference memory with one zero-sum two-player value V(s)."""

    model_family = "structured-v12"
    observation_schema = "structured-observation/v12"

    def __init__(self, config: ModelConfigV12) -> None:
        if config.multiplayer_value_slots != 2:
            raise ValueError("V12 requires exactly two value slots")
        super().__init__(config)
        self.multiplayer_value_head = nn.Sequential(
            nn.LayerNorm(config.d_model * 5),
            nn.Linear(config.d_model * 5, config.feedforward_dim),
            nn.GELU(),
            nn.Dropout(config.dropout),
            nn.Linear(config.feedforward_dim, 1),
            nn.Tanh(),
        )

    def evaluate_actions_with_memory(
        self,
        state_tokens: StructuredTokens,
        action_tokens: StructuredTokens,
        previous_plan: torch.Tensor | None = None,
        *,
        need_weights: bool = False,
    ) -> dict[str, torch.Tensor]:
        result = super().evaluate_actions_with_memory(
            state_tokens,
            action_tokens,
            previous_plan,
            need_weights=need_weights,
        )
        value = result["value"]
        result["player_values"] = torch.stack((value, -value), dim=-1)
        return result
