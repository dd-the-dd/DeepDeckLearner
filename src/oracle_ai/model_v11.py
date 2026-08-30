from __future__ import annotations

from dataclasses import dataclass

import torch
from torch import nn

from oracle_ai.encoding_v2 import StructuredTokens
from oracle_ai.encoding_v11 import TokenTypeV11
from oracle_ai.model_v9 import MagicTransformerActorCriticV9, ModelConfigV9


@dataclass(frozen=True)
class ModelConfigV11(ModelConfigV9):
    token_type_count: int = len(TokenTypeV11)
    multiplayer_value_slots: int = 4
    difference_layers: int = 2
    max_delta_tokens: int = 96
    max_commander_cards: int = 8
    isolate_prediction_gradients: bool = True


def _select_tokens(tokens: StructuredTokens, mask: torch.Tensor) -> StructuredTokens:
    return StructuredTokens(
        numeric=tokens.numeric[mask],
        word_ids=tokens.word_ids[mask],
        relative_players=tokens.relative_players[mask],
        token_types=tokens.token_types[mask],
        numeric_mask=tokens.numeric_mask[mask],
    )


class MagicTransformerActorCriticV11(MagicTransformerActorCriticV9):
    """AlphaStar-style recurrent actor-critic for Magic trajectories.

    Legal choices remain authoritative in Rust.  V11 has two observation paths:
    the normal entity/state transformer and an independent transformer containing
    only changes and events since this player's previous decision.  A recurrent
    strategic memory carries their combination through the game.  The critic
    predicts one value per relative player, which is required for multiplayer
    Plackett-Luce returns.
    """

    model_family = "structured-v11"
    observation_schema = "structured-observation/v11"
    improve_policy = None

    def __init__(self, config: ModelConfigV11) -> None:
        if config.multiplayer_value_slots <= 1:
            raise ValueError("multiplayer_value_slots must include opponents")
        if config.difference_layers <= 0:
            raise ValueError("difference_layers must be positive")
        super().__init__(config)
        difference_layer = nn.TransformerEncoderLayer(
            d_model=config.d_model,
            nhead=config.heads,
            dim_feedforward=config.feedforward_dim,
            dropout=config.dropout,
            activation="gelu",
            batch_first=True,
            norm_first=True,
        )
        self.difference_marker = nn.Parameter(torch.zeros(1, 1, config.d_model))
        self.difference_encoder = nn.TransformerEncoder(
            difference_layer,
            num_layers=config.difference_layers,
        )
        self.difference_norm = nn.LayerNorm(config.d_model)
        self.no_difference_embedding = nn.Parameter(torch.zeros(1, config.d_model))
        self.memory_input = nn.Sequential(
            nn.LayerNorm(config.d_model * 2),
            nn.Linear(config.d_model * 2, config.d_model),
            nn.GELU(),
        )
        self.recurrent_core = nn.GRUCell(config.d_model, config.d_model)
        self.memory_norm = nn.LayerNorm(config.d_model)
        self.difference_action_projection = nn.Linear(
            config.d_model,
            config.d_model,
            bias=False,
        )
        self.memory_action_projection = nn.Linear(
            config.d_model,
            config.d_model,
            bias=False,
        )
        self.difference_policy_gate = nn.Parameter(torch.zeros(()))
        self.memory_policy_gate = nn.Parameter(torch.zeros(()))
        self.multiplayer_value_head = nn.Sequential(
            nn.LayerNorm(config.d_model * 5),
            nn.Linear(config.d_model * 5, config.feedforward_dim),
            nn.GELU(),
            nn.Dropout(config.dropout),
            nn.Linear(config.feedforward_dim, config.multiplayer_value_slots),
            nn.Tanh(),
        )

    @staticmethod
    def _difference_mask(state_tokens: StructuredTokens) -> torch.Tensor:
        return state_tokens.token_types.eq(int(TokenTypeV11.STATE_DELTA)) | state_tokens.token_types.eq(
            int(TokenTypeV11.DECISION_EVENT)
        )

    def _difference_latent(self, state_tokens: StructuredTokens) -> torch.Tensor:
        mask = self._difference_mask(state_tokens)
        if not bool(mask.any()):
            return self.no_difference_embedding
        difference_tokens = _select_tokens(state_tokens, mask)
        embedded = self._embed_tokens(difference_tokens).unsqueeze(0)
        encoded = self.difference_encoder(
            torch.cat((self.difference_marker, embedded), dim=1)
        )
        return self.difference_norm(encoded[:, 0])

    def evaluate_actions_with_memory(
        self,
        state_tokens: StructuredTokens,
        action_tokens: StructuredTokens,
        previous_plan: torch.Tensor | None = None,
        *,
        need_weights: bool = False,
    ) -> dict[str, torch.Tensor]:
        difference_mask = self._difference_mask(state_tokens)
        base_mask = ~difference_mask
        # The normal encoder never sees delta tokens; the streams only meet in
        # the recurrent core and the policy/value fusion below.
        base_tokens = _select_tokens(state_tokens, base_mask)
        result = super().evaluate_actions_with_memory(
            base_tokens,
            action_tokens,
            previous_plan,
            need_weights=need_weights,
        )
        difference = self._difference_latent(state_tokens)
        recurrent_input = self.memory_input(
            torch.cat((result["state_embedding"], difference), dim=-1)
        )
        memory = self.recurrent_core(recurrent_input, result["strategic_plan"])
        memory = self.memory_norm(memory)
        action_embeddings = result["normalized_actions"]
        scale = self.config.d_model**0.5
        difference_logits = (
            self.difference_action_projection(difference).unsqueeze(1)
            * action_embeddings
        ).sum(dim=-1) / scale
        memory_logits = (
            self.memory_action_projection(memory).unsqueeze(1) * action_embeddings
        ).sum(dim=-1) / scale
        result["logits"] = (
            result["logits"]
            + self.difference_policy_gate * difference_logits
            + self.memory_policy_gate * memory_logits
        )
        player_values = self.multiplayer_value_head(
            torch.cat(
                (
                    result["state_embedding"],
                    result["deck_latent"],
                    result["opponent_belief"],
                    difference,
                    memory,
                ),
                dim=-1,
            )
        )
        result.update(
            {
                "value": player_values[:, 0],
                "player_values": player_values,
                "strategic_plan": memory,
                "difference_embedding": difference,
                "difference_token_count": difference_mask.sum().reshape(1),
            }
        )
        return result
