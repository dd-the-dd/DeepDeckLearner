from __future__ import annotations

from dataclasses import dataclass

import torch
from torch import nn

from oracle_ai.encoding_v2 import StructuredTokens
from oracle_ai.encoding_v7 import TokenTypeV7
from oracle_ai.model_v6 import MagicTransformerActorCriticV6, ModelConfigV6


@dataclass(frozen=True)
class ModelConfigV7(ModelConfigV6):
    token_type_count: int = len(TokenTypeV7)
    max_deck_cards: int = 128
    plan_layers: int = 2


class PlanRefinementBlock(nn.Module):
    def __init__(self, config: ModelConfigV7) -> None:
        super().__init__()
        self.norm = nn.LayerNorm(config.d_model)
        self.network = nn.Sequential(
            nn.Linear(config.d_model, config.feedforward_dim),
            nn.GELU(),
            nn.Dropout(config.dropout),
            nn.Linear(config.feedforward_dim, config.d_model),
            nn.Dropout(config.dropout),
        )

    def forward(self, plan: torch.Tensor) -> torch.Tensor:
        return plan + self.network(self.norm(plan))


class MagicTransformerActorCriticV7(MagicTransformerActorCriticV6):
    model_family = "structured-v7"
    observation_schema = "structured-observation/v7"

    def __init__(self, config: ModelConfigV7) -> None:
        if config.plan_layers <= 0:
            raise ValueError("plan_layers must be positive")
        super().__init__(config)
        self.deck_query = nn.Parameter(torch.zeros(1, 1, config.d_model))
        self.no_deck_embedding = nn.Parameter(torch.zeros(1, config.d_model))
        self.deck_attention = nn.MultiheadAttention(
            config.d_model,
            config.heads,
            dropout=config.dropout,
            batch_first=True,
        )
        self.plan_initializer = nn.Sequential(
            nn.LayerNorm(config.d_model * 3),
            nn.Linear(config.d_model * 3, config.d_model),
            nn.GELU(),
        )
        self.plan_refinement = nn.ModuleList(
            PlanRefinementBlock(config) for _ in range(config.plan_layers)
        )
        self.plan_norm = nn.LayerNorm(config.d_model)
        self.plan_action_projection = nn.Linear(
            config.d_model,
            config.d_model,
            bias=False,
        )
        self.plan_value_head = nn.Sequential(
            nn.LayerNorm(config.d_model * 2),
            nn.Linear(config.d_model * 2, config.d_model),
            nn.GELU(),
            nn.Linear(config.d_model, 1),
        )

    def _strategic_context(
        self,
        state_tokens: StructuredTokens,
        *,
        need_weights: bool,
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor | None]:
        state = self._embed_tokens(state_tokens).unsqueeze(0)
        encoded_state = self.encoder(torch.cat([self.state_marker, state], dim=1))
        encoded_state = self.final_norm(encoded_state)
        pooled = encoded_state[:, 0]
        decision = encoded_state[:, 1] if encoded_state.shape[1] > 1 else pooled
        deck_mask = state_tokens.token_types.eq(int(TokenTypeV7.DECK_CARD))
        if bool(deck_mask.any()):
            deck_state = encoded_state[:, 1:][:, deck_mask]
            deck_latent, deck_attention = self.deck_attention(
                self.deck_query.expand(encoded_state.shape[0], -1, -1),
                deck_state,
                deck_state,
                need_weights=need_weights,
                average_attn_weights=False,
            )
            deck_latent = deck_latent[:, 0]
        else:
            deck_latent = self.no_deck_embedding.expand(encoded_state.shape[0], -1)
            deck_attention = None
        plan = self.plan_initializer(torch.cat((pooled, decision, deck_latent), dim=-1))
        for block in self.plan_refinement:
            plan = block(plan)
        return encoded_state, pooled, self.plan_norm(plan), deck_attention

    def _condition_actions_v7(
        self,
        state_tokens: StructuredTokens,
        action_tokens: StructuredTokens,
        *,
        need_weights: bool,
    ):
        encoded_state, pooled, plan, deck_attention = self._strategic_context(
            state_tokens,
            need_weights=need_weights,
        )
        actions = self._embed_tokens(action_tokens).unsqueeze(0)
        decision = encoded_state[:, 1] if encoded_state.shape[1] > 1 else pooled
        action_keys = self.policy_key(actions)
        strategic_query = self.policy_query(pooled + plan).unsqueeze(1)
        base_logits = (strategic_query * action_keys).sum(dim=-1) / (
            self.config.d_model**0.5
        )
        conditioned_actions = (
            action_keys
            + self.policy_query(decision).unsqueeze(1)
            + self.plan_action_projection(plan).unsqueeze(1)
        )
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
        return (
            conditioned_actions,
            pooled,
            plan,
            base_logits,
            attentions,
            activation_norms,
            deck_attention,
        )

    def evaluate_actions(
        self,
        state_tokens: StructuredTokens,
        action_tokens: StructuredTokens,
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        actions, pooled, plan, base_logits, _, _, _ = self._condition_actions_v7(
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
        value = self.plan_value_head(torch.cat((pooled, plan), dim=-1)).squeeze(-1)
        return logits, value, action_values

    def forward(
        self,
        state_tokens: StructuredTokens,
        action_tokens: StructuredTokens,
    ) -> tuple[torch.Tensor, torch.Tensor]:
        logits, value, _ = self.evaluate_actions(state_tokens, action_tokens)
        return logits, value

    @torch.no_grad()
    def analyze(
        self,
        state_tokens: StructuredTokens,
        action_tokens: StructuredTokens,
    ) -> dict[str, torch.Tensor]:
        (
            actions,
            pooled,
            plan,
            base_logits,
            attentions,
            activation_norms,
            deck_attention,
        ) = self._condition_actions_v7(
            state_tokens,
            action_tokens,
            need_weights=True,
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
        result = {
            "logits": logits,
            "probabilities": probabilities,
            "entropy": -(
                probabilities * probabilities.clamp_min(1e-12).log()
            ).sum(dim=-1),
            "value": self.plan_value_head(torch.cat((pooled, plan), dim=-1)).squeeze(-1),
            "action_values": action_values,
            "action_activation_norms": torch.stack(activation_norms),
            "strategic_plan": plan,
            "strategic_plan_norm": plan.norm(dim=-1),
        }
        if deck_attention is not None:
            result["deck_attention"] = deck_attention
        if attentions:
            result["attention"] = attentions[-1]
            result["attention_layers"] = torch.stack(attentions)
        return result
