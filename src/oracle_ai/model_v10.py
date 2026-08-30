from __future__ import annotations

from dataclasses import dataclass

import torch
from torch import nn

from oracle_ai.encoding_v2 import StructuredTokens
from oracle_ai.model_v9 import MagicTransformerActorCriticV9, ModelConfigV9


@dataclass(frozen=True)
class ModelConfigV10(ModelConfigV9):
    event_codebook_size: int = 128
    event_latent_dim: int = 96
    latent_action_value_weight: float = 0.35
    multiplayer_value_slots: int = 4
    isolate_prediction_gradients: bool = True


class MagicTransformerActorCriticV10(MagicTransformerActorCriticV9):
    """Concrete-action policy with learned, context-dependent event sharing.

    Rust remains authoritative for legal actions.  Each concrete action receives a
    VQ event code inferred from the current observation, the action, and V9's
    predicted consequence.  Policy/value estimates and root search statistics are
    shared by actions that quantize to the same code without collapsing the
    concrete actions themselves.
    """

    model_family = "structured-v10"
    observation_schema = "structured-observation/v10"

    def __init__(self, config: ModelConfigV10) -> None:
        if config.event_codebook_size <= 1:
            raise ValueError("event_codebook_size must be greater than one")
        if config.event_latent_dim <= 0:
            raise ValueError("event_latent_dim must be positive")
        if not 0.0 <= config.latent_action_value_weight <= 1.0:
            raise ValueError("latent_action_value_weight must be between zero and one")
        if config.multiplayer_value_slots <= 1:
            raise ValueError("multiplayer_value_slots must include opponents")
        super().__init__(config)
        event_context_dim = config.d_model * 4
        self.event_encoder = nn.Sequential(
            nn.LayerNorm(event_context_dim),
            nn.Linear(event_context_dim, config.feedforward_dim),
            nn.GELU(),
            nn.Dropout(config.dropout),
            nn.Linear(config.feedforward_dim, config.event_latent_dim),
        )
        self.event_codebook = nn.Embedding(
            config.event_codebook_size,
            config.event_latent_dim,
        )
        nn.init.uniform_(
            self.event_codebook.weight,
            -1.0 / config.event_codebook_size,
            1.0 / config.event_codebook_size,
        )
        future_values = (
            config.future_horizons
            * config.future_player_slots
            * config.future_feature_dim
        )
        self.event_decoder = nn.Sequential(
            nn.LayerNorm(config.event_latent_dim + config.d_model * 2),
            nn.Linear(
                config.event_latent_dim + config.d_model * 2,
                config.feedforward_dim,
            ),
            nn.GELU(),
            nn.Dropout(config.dropout),
            nn.Linear(config.feedforward_dim, future_values),
        )
        self.latent_policy_head = nn.Sequential(
            nn.LayerNorm(config.event_latent_dim),
            nn.Linear(config.event_latent_dim, 1),
        )
        self.latent_action_value_head = nn.Sequential(
            nn.LayerNorm(config.event_latent_dim),
            nn.Linear(config.event_latent_dim, 1),
        )
        self.latent_policy_gate = nn.Parameter(torch.zeros(()))
        self.multiplayer_value_head = nn.Sequential(
            nn.LayerNorm(config.d_model * 4),
            nn.Linear(config.d_model * 4, config.d_model),
            nn.GELU(),
            nn.Linear(config.d_model, config.multiplayer_value_slots),
            nn.Tanh(),
        )

    def _quantize_events(
        self,
        event_input: torch.Tensor,
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
        prequantized = self.event_encoder(event_input)
        codebook = self.event_codebook.weight
        distances = (
            prequantized.pow(2).sum(dim=-1, keepdim=True)
            + codebook.pow(2).sum(dim=-1)
            - 2.0 * prequantized @ codebook.transpose(0, 1)
        )
        code_indices = distances.argmin(dim=-1)
        quantized = self.event_codebook(code_indices)
        straight_through = prequantized + (quantized - prequantized).detach()
        return prequantized, quantized, straight_through, code_indices

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

        pooled = result["state_embedding"]
        plan = result["strategic_plan"]
        action_count = action_tokens.numeric.shape[0]
        action_keys = result["normalized_actions"]
        future_embedding = result["future_embedding"]
        event_input = torch.cat(
            (
                action_keys,
                future_embedding,
                pooled.unsqueeze(1).expand(-1, action_count, -1),
                plan.unsqueeze(1).expand(-1, action_count, -1),
            ),
            dim=-1,
        )
        if self.config.isolate_prediction_gradients:
            # Event reconstruction may learn from action/context features without
            # sending its auxiliary loss back through the policy representation.
            event_input = event_input.detach()
        prequantized, quantized, event_latents, code_indices = self._quantize_events(
            event_input
        )

        decoder_pooled = (
            pooled.detach() if self.config.isolate_prediction_gradients else pooled
        )
        decoder_plan = (
            plan.detach() if self.config.isolate_prediction_gradients else plan
        )
        decoder_context = torch.cat(
            (
                event_latents,
                decoder_pooled.unsqueeze(1).expand(-1, action_count, -1),
                decoder_plan.unsqueeze(1).expand(-1, action_count, -1),
            ),
            dim=-1,
        )
        reconstructed_future = -torch.sigmoid(
            self.event_decoder(decoder_context)
        ).reshape(
            1,
            action_count,
            self.config.future_horizons,
            self.config.future_player_slots,
            self.config.future_feature_dim,
        )
        decision_event_latents = (
            event_latents.detach()
            if self.config.isolate_prediction_gradients
            else event_latents
        )
        latent_policy = self.latent_policy_head(decision_event_latents).squeeze(-1)
        latent_action_values = self.latent_action_value_head(
            decision_event_latents
        ).squeeze(-1)
        concrete_action_values = result["action_values"]
        latent_weight = self.config.latent_action_value_weight
        shared_action_values = (
            1.0 - latent_weight
        ) * concrete_action_values + latent_weight * latent_action_values
        centered_latent_policy = latent_policy - latent_policy.mean(
            dim=-1,
            keepdim=True,
        )
        result["logits"] = result["logits"] + (
            self.latent_policy_gate * centered_latent_policy
        )
        multiplayer_values = self.multiplayer_value_head(
            torch.cat(
                (
                    pooled,
                    result["deck_latent"],
                    result["opponent_belief"],
                    plan,
                ),
                dim=-1,
            )
        )
        result.update(
            {
                "value": multiplayer_values[:, 0],
                "player_values": multiplayer_values,
                "action_values": shared_action_values,
                "concrete_action_values": concrete_action_values,
                "latent_action_values": latent_action_values,
                "event_prequantized": prequantized,
                "event_quantized": quantized,
                "event_latents": event_latents,
                "event_code_indices": code_indices,
                "event_reconstructed_future": reconstructed_future,
            }
        )
        return result

    @torch.no_grad()
    def improve_policy(
        self,
        logits: torch.Tensor,
        action_values: torch.Tensor,
        masked_action_indices: tuple[int, ...] = (),
        *,
        add_exploration_noise: bool = False,
        event_code_indices: torch.Tensor | None = None,
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
        codes = (
            event_code_indices.squeeze(0)
            if event_code_indices is not None
            else torch.arange(priors.numel(), device=priors.device)
        )
        code_count = max(self.config.event_codebook_size, int(codes.max().item()) + 1)
        for _ in range(self.config.root_search_simulations):
            code_visits = torch.zeros(
                code_count, device=visits.device, dtype=visits.dtype
            )
            code_visits.scatter_add_(0, codes, visits)
            concrete_exploration = (
                self.config.puct_coefficient
                * priors
                * torch.sqrt(visits.sum() + 1.0)
                / (1.0 + visits)
            )
            latent_exploration = (
                self.config.puct_coefficient
                * priors
                * torch.sqrt(visits.sum() + 1.0)
                / (1.0 + code_visits[codes])
            )
            latent_weight = self.config.latent_action_value_weight
            exploration = (
                1.0 - latent_weight
            ) * concrete_exploration + latent_weight * latent_exploration
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
        code_histogram = torch.bincount(
            result["event_code_indices"].flatten(),
            minlength=self.config.event_codebook_size,
        ).to(torch.float32)
        code_probabilities = code_histogram / code_histogram.sum().clamp_min(1.0)
        result["event_code_perplexity"] = torch.exp(
            -(code_probabilities * code_probabilities.clamp_min(1e-12).log()).sum()
        )
        return result
