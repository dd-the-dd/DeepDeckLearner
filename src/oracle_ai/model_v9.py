from __future__ import annotations

from dataclasses import dataclass

import torch
from torch import nn

from oracle_ai.encoding_v2 import StructuredTokens
from oracle_ai.encoding_v7 import TokenTypeV7
from oracle_ai.model_v7 import MagicTransformerActorCriticV7, ModelConfigV7


@dataclass(frozen=True)
class ModelConfigV9(ModelConfigV7):
    future_horizons: int = 4
    future_player_slots: int = 4
    future_feature_dim: int = 23
    minimum_log_variance: float = -6.0
    maximum_log_variance: float = 3.0


class MagicTransformerActorCriticV9(MagicTransformerActorCriticV7):
    model_family = "structured-v9"
    observation_schema = "structured-observation/v9"
    improve_policy = None

    def __init__(self, config: ModelConfigV9) -> None:
        if config.future_horizons <= 0:
            raise ValueError("future_horizons must be positive")
        if config.future_player_slots <= 1:
            raise ValueError("future_player_slots must include the actor and opponents")
        if config.future_feature_dim <= 0:
            raise ValueError("future_feature_dim must be positive")
        if config.minimum_log_variance >= config.maximum_log_variance:
            raise ValueError("minimum_log_variance must be below maximum_log_variance")
        super().__init__(config)
        self.opponent_query_projection = nn.Sequential(
            nn.LayerNorm(config.d_model * 2),
            nn.Linear(config.d_model * 2, config.d_model),
            nn.GELU(),
        )
        self.opponent_attention = nn.MultiheadAttention(
            config.d_model,
            config.heads,
            dropout=config.dropout,
            batch_first=True,
        )
        self.no_opponent_embedding = nn.Parameter(torch.zeros(1, config.d_model))
        self.plan_candidate = nn.Sequential(
            nn.LayerNorm(config.d_model * 3),
            nn.Linear(config.d_model * 3, config.feedforward_dim),
            nn.GELU(),
            nn.Dropout(config.dropout),
            nn.Linear(config.feedforward_dim, config.d_model),
        )
        self.plan_gate = nn.Sequential(
            nn.LayerNorm(config.d_model * 4),
            nn.Linear(config.d_model * 4, config.d_model),
            nn.Sigmoid(),
        )
        self.belief_action_projection = nn.Linear(
            config.d_model,
            config.d_model,
            bias=False,
        )
        future_values = (
            config.future_horizons
            * config.future_player_slots
            * config.future_feature_dim
        )
        self.consequence_head = nn.Sequential(
            nn.LayerNorm(config.d_model),
            nn.Linear(config.d_model, config.feedforward_dim),
            nn.GELU(),
            nn.Dropout(config.dropout),
            nn.Linear(config.feedforward_dim, future_values * 2),
        )
        self.future_encoder = nn.Sequential(
            nn.LayerNorm(future_values * 2),
            nn.Linear(future_values * 2, config.d_model),
            nn.GELU(),
            nn.Dropout(config.dropout),
            nn.Linear(config.d_model, config.d_model),
        )
        self.future_gate = nn.Parameter(torch.zeros(()))
        self.future_action_norm = nn.LayerNorm(config.d_model)
        self.belief_prediction_head = nn.Sequential(
            nn.LayerNorm(config.d_model),
            nn.Linear(
                config.d_model,
                config.future_player_slots * config.future_feature_dim,
            ),
        )
        self.strategic_value_head = nn.Sequential(
            nn.LayerNorm(config.d_model * 4),
            nn.Linear(config.d_model * 4, config.d_model),
            nn.GELU(),
            nn.Linear(config.d_model, 1),
        )

    def _deck_latent(
        self,
        encoded_state: torch.Tensor,
        state_tokens: StructuredTokens,
        *,
        need_weights: bool,
    ) -> tuple[torch.Tensor, torch.Tensor | None]:
        deck_mask = state_tokens.token_types.eq(int(TokenTypeV7.DECK_CARD))
        if not bool(deck_mask.any()):
            return self.no_deck_embedding.expand(encoded_state.shape[0], -1), None
        deck_state = encoded_state[:, 1:][:, deck_mask]
        latent, attention = self.deck_attention(
            self.deck_query.expand(encoded_state.shape[0], -1, -1),
            deck_state,
            deck_state,
            need_weights=need_weights,
            average_attn_weights=False,
        )
        return latent[:, 0], attention

    def _opponent_latent(
        self,
        encoded_state: torch.Tensor,
        state_tokens: StructuredTokens,
        pooled: torch.Tensor,
        deck: torch.Tensor,
        *,
        need_weights: bool,
    ) -> tuple[torch.Tensor, torch.Tensor | None]:
        opponent_mask = state_tokens.relative_players.ne(
            0
        ) & state_tokens.relative_players.lt(self.config.player_angle_steps)
        if not bool(opponent_mask.any()):
            return self.no_opponent_embedding.expand(encoded_state.shape[0], -1), None
        opponent_state = encoded_state[:, 1:][:, opponent_mask]
        query = self.opponent_query_projection(torch.cat((pooled, deck), dim=-1))
        latent, attention = self.opponent_attention(
            query.unsqueeze(1),
            opponent_state,
            opponent_state,
            need_weights=need_weights,
            average_attn_weights=False,
        )
        return latent[:, 0], attention

    def _updated_plan(
        self,
        pooled: torch.Tensor,
        decision: torch.Tensor,
        deck: torch.Tensor,
        opponent: torch.Tensor,
        previous_plan: torch.Tensor | None,
    ) -> tuple[torch.Tensor, torch.Tensor]:
        base_plan = self.plan_initializer(torch.cat((pooled, decision, deck), dim=-1))
        if previous_plan is None:
            previous_plan = torch.zeros_like(base_plan)
        elif previous_plan.ndim == 1:
            previous_plan = previous_plan.unsqueeze(0)
        previous_plan = previous_plan.to(device=pooled.device, dtype=pooled.dtype)
        if previous_plan.shape != base_plan.shape:
            raise ValueError(
                f"previous plan has shape {tuple(previous_plan.shape)}, "
                f"expected {tuple(base_plan.shape)}"
            )
        candidate = self.plan_candidate(
            torch.cat((base_plan, opponent, previous_plan), dim=-1)
        )
        for block in self.plan_refinement:
            candidate = block(candidate)
        gate = self.plan_gate(
            torch.cat((pooled, deck, opponent, previous_plan), dim=-1)
        )
        plan = gate * candidate + (1.0 - gate) * previous_plan
        return self.plan_norm(plan), gate

    def evaluate_actions_with_memory(
        self,
        state_tokens: StructuredTokens,
        action_tokens: StructuredTokens,
        previous_plan: torch.Tensor | None = None,
        *,
        need_weights: bool = False,
    ) -> dict[str, torch.Tensor]:
        state = self._embed_tokens(state_tokens).unsqueeze(0)
        encoded_state = self.encoder(torch.cat([self.state_marker, state], dim=1))
        encoded_state = self.final_norm(encoded_state)
        pooled = encoded_state[:, 0]
        decision = encoded_state[:, 1] if encoded_state.shape[1] > 1 else pooled
        deck, deck_attention = self._deck_latent(
            encoded_state,
            state_tokens,
            need_weights=need_weights,
        )
        opponent, opponent_attention = self._opponent_latent(
            encoded_state,
            state_tokens,
            pooled,
            deck,
            need_weights=need_weights,
        )
        plan, plan_gate = self._updated_plan(
            pooled,
            decision,
            deck,
            opponent,
            previous_plan,
        )

        actions = self._embed_tokens(action_tokens).unsqueeze(0)
        action_keys = self.policy_key(actions)
        strategic_query = self.policy_query(pooled + plan + opponent).unsqueeze(1)
        base_logits = (strategic_query * action_keys).sum(dim=-1) / (
            self.config.d_model**0.5
        )
        conditioned_actions = (
            action_keys
            + self.policy_query(decision).unsqueeze(1)
            + self.plan_action_projection(plan).unsqueeze(1)
            + self.belief_action_projection(opponent).unsqueeze(1)
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

        normalized_actions = self.action_output_norm(conditioned_actions)
        isolate_predictions = bool(
            getattr(self.config, "isolate_prediction_gradients", False)
        )
        # V10 treats consequence prediction as an auxiliary model. Its supervised
        # loss trains the predictor, while PPO trains the policy that consumes the
        # detached prediction. Neither objective can distort the other branch.
        prediction_actions = (
            normalized_actions.detach() if isolate_predictions else normalized_actions
        )
        consequence_parameters = self.consequence_head(prediction_actions)
        future_values = (
            self.config.future_horizons
            * self.config.future_player_slots
            * self.config.future_feature_dim
        )
        future_mean_raw, future_log_variance = consequence_parameters.split(
            future_values,
            dim=-1,
        )
        future_mean = -torch.sigmoid(future_mean_raw).reshape(
            1,
            action_tokens.numeric.shape[0],
            self.config.future_horizons,
            self.config.future_player_slots,
            self.config.future_feature_dim,
        )
        future_log_variance = future_log_variance.clamp(
            self.config.minimum_log_variance,
            self.config.maximum_log_variance,
        ).reshape_as(future_mean)
        decision_future_mean = (
            future_mean.detach() if isolate_predictions else future_mean
        )
        decision_future_log_variance = (
            future_log_variance.detach() if isolate_predictions else future_log_variance
        )
        future_embedding = self.future_encoder(
            torch.cat(
                (
                    decision_future_mean.flatten(start_dim=2),
                    decision_future_log_variance.flatten(start_dim=2),
                ),
                dim=-1,
            )
        )
        enriched_actions = self.future_action_norm(
            normalized_actions + self.future_gate * future_embedding
        )
        contextual_logits = self.policy_score(enriched_actions).squeeze(-1)
        action_values = self.action_value_head(enriched_actions).squeeze(-1)
        centered_action_values = action_values - action_values.mean(
            dim=-1, keepdim=True
        )
        logits = (
            base_logits
            + self.context_gate * contextual_logits
            + self.action_value_gate * centered_action_values
        )
        value = self.strategic_value_head(
            torch.cat((pooled, deck, opponent, plan), dim=-1)
        ).squeeze(-1)
        prediction_opponent = opponent.detach() if isolate_predictions else opponent
        belief_prediction = -torch.sigmoid(
            self.belief_prediction_head(prediction_opponent)
        ).reshape(
            1,
            self.config.future_player_slots,
            self.config.future_feature_dim,
        )
        result = {
            "logits": logits,
            "value": value,
            "action_values": action_values,
            "future_mean": future_mean,
            "future_log_variance": future_log_variance,
            "belief_prediction": belief_prediction,
            "strategic_plan": plan,
            "plan_gate": plan_gate,
            "deck_latent": deck,
            "opponent_belief": opponent,
            "state_embedding": pooled,
            "normalized_actions": normalized_actions,
            "future_embedding": future_embedding,
            "action_activation_norms": torch.stack(activation_norms),
        }
        if deck_attention is not None:
            result["deck_attention"] = deck_attention
        if opponent_attention is not None:
            result["opponent_attention"] = opponent_attention
        if attentions:
            result["attention"] = attentions[-1]
            result["attention_layers"] = torch.stack(attentions)
        return result

    def evaluate_actions(
        self,
        state_tokens: StructuredTokens,
        action_tokens: StructuredTokens,
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        result = self.evaluate_actions_with_memory(state_tokens, action_tokens)
        return result["logits"], result["value"], result["action_values"]

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
        previous_plan: torch.Tensor | None = None,
    ) -> dict[str, torch.Tensor]:
        result = self.evaluate_actions_with_memory(
            state_tokens,
            action_tokens,
            previous_plan,
            need_weights=True,
        )
        probabilities = torch.softmax(result["logits"], dim=-1)
        result["probabilities"] = probabilities
        result["entropy"] = -(probabilities * probabilities.clamp_min(1e-12).log()).sum(
            dim=-1
        )
        result["strategic_plan_norm"] = result["strategic_plan"].norm(dim=-1)
        result["future_uncertainty"] = result["future_log_variance"].exp().sqrt()
        return result
