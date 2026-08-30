from __future__ import annotations

from dataclasses import dataclass

import torch
from torch import nn

from oracle_ai.encoding_v2 import StructuredTokens
from oracle_ai.model_v4 import MagicTransformerActorCriticV4, ModelConfigV4


@dataclass(frozen=True)
class ModelConfigV5(ModelConfigV4):
    action_layers: int = 2


class ActionConditioningBlock(nn.Module):
    def __init__(self, config: ModelConfigV5) -> None:
        super().__init__()
        self.query_norm = nn.LayerNorm(config.d_model)
        self.state_norm = nn.LayerNorm(config.d_model)
        self.attention = nn.MultiheadAttention(
            config.d_model,
            config.heads,
            dropout=config.dropout,
            batch_first=True,
        )
        self.attention_dropout = nn.Dropout(config.dropout)
        self.feedforward_norm = nn.LayerNorm(config.d_model)
        self.feedforward = nn.Sequential(
            nn.Linear(config.d_model, config.feedforward_dim),
            nn.GELU(),
            nn.Dropout(config.dropout),
            nn.Linear(config.feedforward_dim, config.d_model),
            nn.Dropout(config.dropout),
        )

    def forward(
        self,
        actions: torch.Tensor,
        state: torch.Tensor,
        *,
        need_weights: bool,
    ) -> tuple[torch.Tensor, torch.Tensor | None]:
        attended, weights = self.attention(
            self.query_norm(actions),
            self.state_norm(state),
            self.state_norm(state),
            need_weights=need_weights,
            average_attn_weights=False,
        )
        actions = actions + self.attention_dropout(attended)
        actions = actions + self.feedforward(self.feedforward_norm(actions))
        return actions, weights


class MagicTransformerActorCriticV5(MagicTransformerActorCriticV4):
    model_family = "structured-v5"
    observation_schema = "structured-observation/v4"

    def __init__(self, config: ModelConfigV5) -> None:
        if config.action_layers <= 0:
            raise ValueError("action_layers must be positive")
        super().__init__(config)
        self.action_conditioning = nn.ModuleList(
            ActionConditioningBlock(config) for _ in range(config.action_layers)
        )
        self.action_output_norm = nn.LayerNorm(config.d_model)
        self.policy_score = nn.Linear(config.d_model, 1, bias=False)
        self.context_gate = nn.Parameter(torch.zeros(()))

    def _condition_actions(
        self,
        state_tokens: StructuredTokens,
        action_tokens: StructuredTokens,
        *,
        need_weights: bool,
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor | None]:
        state = self._embed_tokens(state_tokens).unsqueeze(0)
        actions = self._embed_tokens(action_tokens).unsqueeze(0)
        encoded_state = self.encoder(torch.cat([self.state_marker, state], dim=1))
        encoded_state = self.final_norm(encoded_state)
        pooled = encoded_state[:, 0]
        decision = encoded_state[:, 1] if encoded_state.shape[1] > 1 else pooled
        action_keys = self.policy_key(actions)
        base_query = self.policy_query(pooled).unsqueeze(1)
        base_logits = (base_query * action_keys).sum(dim=-1) / (
            self.config.d_model**0.5
        )
        conditioned_actions = action_keys
        conditioned_actions = conditioned_actions + self.policy_query(decision).unsqueeze(1)
        attention_weights: torch.Tensor | None = None
        for block in self.action_conditioning:
            conditioned_actions, attention_weights = block(
                conditioned_actions,
                encoded_state,
                need_weights=need_weights,
            )
        return conditioned_actions, pooled, base_logits, attention_weights

    def forward(
        self,
        state_tokens: StructuredTokens,
        action_tokens: StructuredTokens,
    ) -> tuple[torch.Tensor, torch.Tensor]:
        actions, pooled, base_logits, _ = self._condition_actions(
            state_tokens,
            action_tokens,
            need_weights=False,
        )
        contextual_logits = self.policy_score(
            self.action_output_norm(actions)
        ).squeeze(-1)
        logits = base_logits + self.context_gate * contextual_logits
        value = self.value_head(pooled).squeeze(-1)
        return logits, value

    @torch.no_grad()
    def analyze(
        self,
        state_tokens: StructuredTokens,
        action_tokens: StructuredTokens,
    ) -> dict[str, torch.Tensor]:
        actions, pooled, base_logits, attention = self._condition_actions(
            state_tokens,
            action_tokens,
            need_weights=True,
        )
        contextual_logits = self.policy_score(
            self.action_output_norm(actions)
        ).squeeze(-1)
        logits = base_logits + self.context_gate * contextual_logits
        probabilities = torch.softmax(logits, dim=-1)
        entropy = -(probabilities * probabilities.clamp_min(1e-12).log()).sum(dim=-1)
        result = {
            "logits": logits,
            "probabilities": probabilities,
            "entropy": entropy,
            "value": self.value_head(pooled).squeeze(-1),
        }
        if attention is not None:
            result["attention"] = attention
        return result
