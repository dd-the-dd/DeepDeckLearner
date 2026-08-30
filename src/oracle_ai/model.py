from __future__ import annotations

from dataclasses import asdict, dataclass

import torch
from torch import nn
from torch.distributions import Categorical


@dataclass(frozen=True)
class ModelConfig:
    feature_dim: int = 256
    d_model: int = 256
    layers: int = 4
    heads: int = 8
    feedforward_dim: int = 1024
    dropout: float = 0.1


class MagicTransformerActorCritic(nn.Module):
    """Scores the legal actions supplied by Rust and estimates state value."""

    def __init__(self, config: ModelConfig) -> None:
        super().__init__()
        self.config = config
        self.state_projection = nn.Linear(config.feature_dim, config.d_model)
        self.action_projection = nn.Linear(config.feature_dim, config.d_model)
        self.state_marker = nn.Parameter(torch.zeros(1, 1, config.d_model))
        layer = nn.TransformerEncoderLayer(
            d_model=config.d_model,
            nhead=config.heads,
            dim_feedforward=config.feedforward_dim,
            dropout=config.dropout,
            activation="gelu",
            batch_first=True,
            norm_first=True,
        )
        self.encoder = nn.TransformerEncoder(layer, num_layers=config.layers)
        self.policy_query = nn.Linear(config.d_model, config.d_model, bias=False)
        self.policy_key = nn.Linear(config.d_model, config.d_model, bias=False)
        self.value_head = nn.Sequential(
            nn.LayerNorm(config.d_model),
            nn.Linear(config.d_model, config.d_model),
            nn.GELU(),
            nn.Linear(config.d_model, 1),
        )
        self.final_norm = nn.LayerNorm(config.d_model)

    def export_config(self) -> dict[str, object]:
        return asdict(self.config)

    def forward(
        self,
        state_tokens: torch.Tensor,
        action_tokens: torch.Tensor,
        state_padding_mask: torch.Tensor | None = None,
    ) -> tuple[torch.Tensor, torch.Tensor]:
        if state_tokens.ndim == 2:
            state_tokens = state_tokens.unsqueeze(0)
        if action_tokens.ndim == 2:
            action_tokens = action_tokens.unsqueeze(0)
        batch = state_tokens.shape[0]
        marker = self.state_marker.expand(batch, -1, -1)
        state = self.state_projection(state_tokens)
        encoded = self.encoder(
            torch.cat([marker, state], dim=1),
            src_key_padding_mask=None,
        )
        pooled = self.final_norm(encoded[:, 0])
        actions = self.action_projection(action_tokens)
        query = self.policy_query(pooled).unsqueeze(1)
        keys = self.policy_key(actions)
        logits = (query * keys).sum(dim=-1) / (self.config.d_model**0.5)
        value = self.value_head(pooled).squeeze(-1)
        return logits, value

    @torch.no_grad()
    def act(
        self,
        state_tokens: torch.Tensor,
        action_tokens: torch.Tensor,
        deterministic: bool = False,
    ) -> tuple[int, float, float]:
        logits, value = self(state_tokens, action_tokens)
        distribution = Categorical(logits=logits.squeeze(0))
        action = torch.argmax(logits, dim=-1).squeeze(0) if deterministic else distribution.sample()
        log_probability = distribution.log_prob(action)
        return int(action.item()), float(log_probability.item()), float(value.squeeze(0).item())
