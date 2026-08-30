from __future__ import annotations

from dataclasses import dataclass

import torch
from torch import nn

from oracle_ai.encoding_v2 import StructuredTokens
from oracle_ai.encoding_v6 import SEMANTIC_VOCABULARY_SIZE, TokenTypeV6
from oracle_ai.model_v5 import MagicTransformerActorCriticV5, ModelConfigV5


@dataclass(frozen=True)
class ModelConfigV6(ModelConfigV5):
    word_vocab_size: int = SEMANTIC_VOCABULARY_SIZE
    max_words: int = 96
    token_type_count: int = len(TokenTypeV6)
    semantic_dim: int = 96
    semantic_layers: int = 2
    semantic_heads: int = 4
    max_events: int = 48
    root_search_simulations: int = 32
    puct_coefficient: float = 1.5
    search_temperature: float = 1.0
    root_value_weight: float = 0.25
    root_dirichlet_alpha: float = 0.3
    root_exploration_fraction: float = 0.25


class ContextualSemanticEncoder(nn.Module):
    def __init__(self, config: ModelConfigV6) -> None:
        super().__init__()
        if config.semantic_dim % config.semantic_heads != 0:
            raise ValueError("semantic_dim must be divisible by semantic_heads")
        self.max_words = config.max_words
        self.token_embedding = nn.Embedding(
            config.word_vocab_size,
            config.semantic_dim,
            padding_idx=0,
        )
        self.position_embedding = nn.Embedding(config.max_words, config.semantic_dim)
        layer = nn.TransformerEncoderLayer(
            d_model=config.semantic_dim,
            nhead=config.semantic_heads,
            dim_feedforward=config.semantic_dim * 4,
            dropout=config.dropout,
            activation="gelu",
            batch_first=True,
            norm_first=True,
        )
        self.encoder = nn.TransformerEncoder(layer, num_layers=config.semantic_layers)
        self.output_norm = nn.LayerNorm(config.semantic_dim)
        self.output_projection = nn.Linear(config.semantic_dim, config.d_model)

    def forward(self, token_ids: torch.Tensor) -> torch.Tensor:
        if token_ids.ndim != 2:
            raise ValueError("semantic token ids must have shape [tokens, sequence]")
        sequence_length = token_ids.shape[1]
        if sequence_length > self.max_words:
            raise ValueError("semantic sequence exceeds configured maximum")
        positions = torch.arange(sequence_length, device=token_ids.device)
        padding = token_ids.eq(0)
        has_content = token_ids.ne(0).any(dim=1)
        padding = padding.clone()
        padding[~has_content, 0] = False
        embedded = self.token_embedding(token_ids)
        embedded = embedded + self.position_embedding(positions).unsqueeze(0)
        encoded = self.encoder(embedded, src_key_padding_mask=padding)
        pooled = self.output_projection(self.output_norm(encoded[:, 0]))
        return pooled * has_content.unsqueeze(-1)


class MagicTransformerActorCriticV6(MagicTransformerActorCriticV5):
    model_family = "structured-v6"
    observation_schema = "structured-observation/v6"

    def __init__(self, config: ModelConfigV6) -> None:
        if config.root_search_simulations <= 0:
            raise ValueError("root_search_simulations must be positive")
        if config.root_dirichlet_alpha <= 0.0:
            raise ValueError("root_dirichlet_alpha must be positive")
        if not 0.0 <= config.root_exploration_fraction <= 1.0:
            raise ValueError("root_exploration_fraction must be between 0 and 1")
        super().__init__(config)
        self.word_encoder = ContextualSemanticEncoder(config)
        self.action_value_head = nn.Sequential(
            nn.LayerNorm(config.d_model),
            nn.Linear(config.d_model, config.d_model),
            nn.GELU(),
            nn.Linear(config.d_model, 1),
        )
        self.action_value_gate = nn.Parameter(torch.zeros(()))

    def _condition_actions_v6(
        self,
        state_tokens: StructuredTokens,
        action_tokens: StructuredTokens,
        *,
        need_weights: bool,
    ) -> tuple[
        torch.Tensor,
        torch.Tensor,
        torch.Tensor,
        list[torch.Tensor],
        list[torch.Tensor],
    ]:
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
        conditioned_actions = action_keys + self.policy_query(decision).unsqueeze(1)
        attentions = []
        activation_norms = [conditioned_actions.norm(dim=-1)]
        for block in self.action_conditioning:
            conditioned_actions, attention = block(
                conditioned_actions,
                encoded_state,
                need_weights=need_weights,
            )
            activation_norms.append(conditioned_actions.norm(dim=-1))
            if attention is not None:
                attentions.append(attention)
        return conditioned_actions, pooled, base_logits, attentions, activation_norms

    def evaluate_actions(
        self,
        state_tokens: StructuredTokens,
        action_tokens: StructuredTokens,
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        actions, pooled, base_logits, _, _ = self._condition_actions_v6(
            state_tokens,
            action_tokens,
            need_weights=False,
        )
        normalized_actions = self.action_output_norm(actions)
        contextual_logits = self.policy_score(normalized_actions).squeeze(-1)
        action_values = self.action_value_head(normalized_actions).squeeze(-1)
        centered_action_values = action_values - action_values.mean(dim=-1, keepdim=True)
        logits = (
            base_logits
            + self.context_gate * contextual_logits
            + self.action_value_gate * centered_action_values
        )
        value = self.value_head(pooled).squeeze(-1)
        return logits, value, action_values

    def forward(
        self,
        state_tokens: StructuredTokens,
        action_tokens: StructuredTokens,
    ) -> tuple[torch.Tensor, torch.Tensor]:
        logits, value, _ = self.evaluate_actions(state_tokens, action_tokens)
        return logits, value

    @torch.no_grad()
    def improve_policy(
        self,
        logits: torch.Tensor,
        action_values: torch.Tensor,
        masked_action_indices: tuple[int, ...] = (),
        *,
        add_exploration_noise: bool = False,
    ) -> torch.Tensor:
        policy_logits = logits.squeeze(0).clone()
        values = self.config.root_value_weight * torch.tanh(action_values.squeeze(0))
        if masked_action_indices:
            policy_logits[list(masked_action_indices)] = -torch.inf
            values[list(masked_action_indices)] = -torch.inf
        priors = torch.softmax(policy_logits, dim=-1)
        if add_exploration_noise and self.config.root_exploration_fraction > 0.0:
            legal = torch.isfinite(policy_logits)
            noise = torch.zeros_like(priors)
            concentration = torch.full_like(
                priors[legal],
                self.config.root_dirichlet_alpha,
            )
            noise[legal] = torch.distributions.Dirichlet(concentration).sample()
            fraction = self.config.root_exploration_fraction
            priors = (1.0 - fraction) * priors + fraction * noise
        visits = torch.zeros_like(priors)
        for _ in range(self.config.root_search_simulations):
            exploration = (
                self.config.puct_coefficient
                * priors
                * torch.sqrt(visits.sum() + 1.0)
                / (1.0 + visits)
            )
            selected = torch.argmax(values + exploration)
            visits[selected] += 1.0
        temperature = max(float(self.config.search_temperature), 1e-6)
        visit_policy = visits.pow(1.0 / temperature)
        return visit_policy / visit_policy.sum().clamp_min(1.0)

    @torch.no_grad()
    def analyze(
        self,
        state_tokens: StructuredTokens,
        action_tokens: StructuredTokens,
    ) -> dict[str, torch.Tensor]:
        actions, pooled, base_logits, attentions, activation_norms = (
            self._condition_actions_v6(
                state_tokens,
                action_tokens,
                need_weights=True,
            )
        )
        normalized_actions = self.action_output_norm(actions)
        contextual_logits = self.policy_score(normalized_actions).squeeze(-1)
        action_values = self.action_value_head(normalized_actions).squeeze(-1)
        centered_action_values = action_values - action_values.mean(dim=-1, keepdim=True)
        logits = (
            base_logits
            + self.context_gate * contextual_logits
            + self.action_value_gate * centered_action_values
        )
        probabilities = torch.softmax(logits, dim=-1)
        entropy = -(probabilities * probabilities.clamp_min(1e-12).log()).sum(dim=-1)
        result = {
            "logits": logits,
            "probabilities": probabilities,
            "entropy": entropy,
            "value": self.value_head(pooled).squeeze(-1),
            "action_values": action_values,
            "action_activation_norms": torch.stack(activation_norms),
        }
        if attentions:
            result["attention"] = attentions[-1]
            result["attention_layers"] = torch.stack(attentions)
        return result
