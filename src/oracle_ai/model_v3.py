from __future__ import annotations

import math
from dataclasses import dataclass

import torch
from torch import nn

from oracle_ai.encoding_v2 import StructuredTokens
from oracle_ai.encoding_v3 import DEFAULT_PLAYER_ANGLE_STEPS
from oracle_ai.model_v2 import MagicTransformerActorCriticV2, ModelConfigV2


@dataclass(frozen=True)
class ModelConfigV3(ModelConfigV2):
    player_angle_steps: int = DEFAULT_PLAYER_ANGLE_STEPS


class MagicTransformerActorCriticV3(MagicTransformerActorCriticV2):
    model_family = "structured-v3"
    observation_schema = "structured-observation/v3"

    def __init__(self, config: ModelConfigV3) -> None:
        if config.player_angle_steps <= 0:
            raise ValueError("player_angle_steps must be positive")
        super().__init__(config)
        del self.relative_player_embedding
        self.relative_player_projection = nn.Linear(
            2,
            config.d_model,
            bias=False,
        )
        self.no_player_embedding = nn.Parameter(torch.zeros(1, config.d_model))

    def _embed_tokens(self, tokens: StructuredTokens) -> torch.Tensor:
        numeric_content = self.numeric_projection(tokens.numeric)
        numeric_content = numeric_content * tokens.numeric_mask.unsqueeze(-1)
        word_content = self.word_encoder(tokens.word_ids)
        if torch.any(tokens.relative_players > self.config.player_angle_steps):
            raise ValueError("cyclic relative-player position is out of range")
        has_player = tokens.relative_players.ne(self.config.player_angle_steps)
        angles = (
            tokens.relative_players.clamp_max(self.config.player_angle_steps - 1)
            .to(torch.float32)
            * (2.0 * math.pi / self.config.player_angle_steps)
        )
        coordinates = torch.stack((torch.sin(angles), torch.cos(angles)), dim=-1)
        relative_player = self.relative_player_projection(coordinates)
        relative_player = torch.where(
            has_player.unsqueeze(-1),
            relative_player,
            self.no_player_embedding.expand_as(relative_player),
        )
        token_type = self.token_type_embedding(tokens.token_types)
        return self.token_norm(
            numeric_content + word_content + relative_player + token_type
        )
