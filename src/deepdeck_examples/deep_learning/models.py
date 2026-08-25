from __future__ import annotations

from dataclasses import dataclass

import torch
from torch import nn

from .encoding import EncodedDecision


@dataclass(frozen=True)
class ModelConfig:
    feature_size: int = 64
    model_size: int = 128
    heads: int = 4
    feedforward_size: int = 256
    state_layers: int = 2
    difference_layers: int = 1
    dropout: float = 0.1
    multiplayer_value_slots: int = 4

    def __post_init__(self) -> None:
        if self.model_size % self.heads:
            raise ValueError("model_size must be divisible by heads")
        if self.multiplayer_value_slots < 2:
            raise ValueError("multiplayer_value_slots must include at least one opponent")


@dataclass(frozen=True)
class ModelOutput:
    logits: torch.Tensor
    player_values: torch.Tensor
    memory: torch.Tensor


class PolicyV11(nn.Module):  # type: ignore[misc]
    """Educational V11: state stream, difference stream, GRU memory and dynamic actions."""

    family = "example-v11"

    def __init__(self, config: ModelConfig | None = None) -> None:
        super().__init__()
        self.config = config or ModelConfig()
        state_layer = nn.TransformerEncoderLayer(
            d_model=self.config.model_size,
            nhead=self.config.heads,
            dim_feedforward=self.config.feedforward_size,
            dropout=self.config.dropout,
            activation="gelu",
            batch_first=True,
            norm_first=True,
        )
        difference_layer = nn.TransformerEncoderLayer(
            d_model=self.config.model_size,
            nhead=self.config.heads,
            dim_feedforward=self.config.feedforward_size,
            dropout=self.config.dropout,
            activation="gelu",
            batch_first=True,
            norm_first=True,
        )
        self.state_input = nn.Linear(self.config.feature_size, self.config.model_size)
        self.difference_input = nn.Linear(self.config.feature_size, self.config.model_size)
        self.action_input = nn.Sequential(
            nn.Linear(self.config.feature_size, self.config.model_size),
            nn.GELU(),
            nn.LayerNorm(self.config.model_size),
        )
        self.state_encoder = nn.TransformerEncoder(state_layer, self.config.state_layers)
        self.difference_encoder = nn.TransformerEncoder(
            difference_layer, self.config.difference_layers
        )
        self.memory_input = nn.Sequential(
            nn.Linear(self.config.model_size * 2, self.config.model_size),
            nn.GELU(),
            nn.LayerNorm(self.config.model_size),
        )
        self.recurrent_core = nn.GRUCell(self.config.model_size, self.config.model_size)
        self.policy_query = nn.Linear(self.config.model_size * 3, self.config.model_size)
        self.value_head = nn.Sequential(
            nn.Linear(self.config.model_size * 3, self.config.feedforward_size),
            nn.GELU(),
            nn.Linear(self.config.feedforward_size, self.config.multiplayer_value_slots),
            nn.Tanh(),
        )

    def initial_memory(self, device: torch.device | str = "cpu") -> torch.Tensor:
        return torch.zeros(1, self.config.model_size, device=device)

    def _encode_stream(
        self,
        tokens: torch.Tensor,
        projection: nn.Linear,
        encoder: nn.Module,
    ) -> torch.Tensor:
        encoded = encoder(projection(tokens).unsqueeze(0))
        return encoded.mean(dim=1)

    def forward(
        self,
        decision: EncodedDecision,
        previous_memory: torch.Tensor | None = None,
    ) -> ModelOutput:
        state = self._encode_stream(
            decision.state_tokens, self.state_input, self.state_encoder
        )
        difference = self._encode_stream(
            decision.difference_tokens,
            self.difference_input,
            self.difference_encoder,
        )
        memory = previous_memory
        if memory is None:
            memory = self.initial_memory(decision.state_tokens.device)
        memory = self.recurrent_core(
            self.memory_input(torch.cat((state, difference), dim=-1)),
            memory,
        )
        context = torch.cat((state, difference, memory), dim=-1)
        query = self.policy_query(context)
        actions = self.action_input(decision.action_tokens)
        logits = actions @ query.squeeze(0) / self.config.model_size**0.5
        return ModelOutput(
            logits=logits,
            player_values=self.value_head(context).squeeze(0),
            memory=memory,
        )


class PolicyV12(PolicyV11):
    """Educational V12: the V11 policy with one antisymmetric two-player value."""

    family = "example-v12"

    def __init__(self, config: ModelConfig | None = None) -> None:
        resolved = config or ModelConfig(multiplayer_value_slots=2)
        if resolved.multiplayer_value_slots != 2:
            raise ValueError("V12 requires exactly two multiplayer_value_slots")
        super().__init__(resolved)
        self.value_head = nn.Sequential(
            nn.Linear(self.config.model_size * 3, self.config.feedforward_size),
            nn.GELU(),
            nn.Linear(self.config.feedforward_size, 1),
            nn.Tanh(),
        )

    def forward(
        self,
        decision: EncodedDecision,
        previous_memory: torch.Tensor | None = None,
    ) -> ModelOutput:
        output = super().forward(decision, previous_memory)
        value = output.player_values.reshape(1)[0]
        return ModelOutput(
            logits=output.logits,
            player_values=torch.stack((value, -value)),
            memory=output.memory,
        )


def build_model(version: str, config: ModelConfig | None = None) -> PolicyV11:
    normalized = version.casefold()
    if normalized == "v11":
        return PolicyV11(config)
    if normalized == "v12":
        return PolicyV12(config)
    raise ValueError("model version must be v11 or v12")
