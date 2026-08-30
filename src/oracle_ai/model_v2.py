from __future__ import annotations

from dataclasses import asdict, dataclass

import torch
from torch import nn
from torch.distributions import Categorical

from oracle_ai.encoding_v2 import (
    NUMERIC_FEATURE_DIM,
    StructuredTokens,
    TokenType,
)


@dataclass(frozen=True)
class ModelConfigV2:
    numeric_dim: int = NUMERIC_FEATURE_DIM
    d_model: int = 384
    layers: int = 6
    heads: int = 8
    feedforward_dim: int = 1536
    dropout: float = 0.0
    word_vocab_size: int = 32768
    max_words: int = 32
    max_relative_players: int = 8
    token_type_count: int = len(TokenType)


class WordSequenceEncoder(nn.Module):
    def __init__(self, config: ModelConfigV2) -> None:
        super().__init__()
        self.max_words = config.max_words
        self.word_embedding = nn.Embedding(
            config.word_vocab_size,
            config.d_model,
            padding_idx=0,
        )
        self.word_position_embedding = nn.Embedding(
            config.max_words,
            config.d_model,
        )
        self.attention = nn.Linear(config.d_model, 1, bias=False)
        self.output_norm = nn.LayerNorm(config.d_model)

    def forward(self, word_ids: torch.Tensor) -> torch.Tensor:
        if word_ids.ndim != 2:
            raise ValueError("word_ids must have shape [tokens, words]")
        word_count = word_ids.shape[1]
        if word_count > self.max_words:
            raise ValueError("word sequence exceeds configured maximum")
        positions = torch.arange(word_count, device=word_ids.device)
        encoded_words = self.word_embedding(word_ids)
        encoded_words = encoded_words + self.word_position_embedding(positions).unsqueeze(0)
        valid_words = word_ids.ne(0)
        scores = self.attention(torch.tanh(encoded_words)).squeeze(-1)
        scores = scores.masked_fill(~valid_words, -1e9)
        weights = torch.softmax(scores, dim=-1) * valid_words
        weights = weights / weights.sum(dim=-1, keepdim=True).clamp_min(1.0)
        pooled = (encoded_words * weights.unsqueeze(-1)).sum(dim=1)
        has_words = valid_words.any(dim=1, keepdim=True)
        return self.output_norm(pooled) * has_words


class MagicTransformerActorCriticV2(nn.Module):
    model_family = "structured-v2"
    observation_schema = "structured-observation/v2"

    def __init__(self, config: ModelConfigV2) -> None:
        super().__init__()
        if config.numeric_dim != NUMERIC_FEATURE_DIM:
            raise ValueError(
                f"V2 numeric_dim must be {NUMERIC_FEATURE_DIM}, received {config.numeric_dim}"
            )
        self.config = config
        self.numeric_projection = nn.Linear(config.numeric_dim, config.d_model)
        self.word_encoder = WordSequenceEncoder(config)
        self.relative_player_embedding = nn.Embedding(
            config.max_relative_players + 1,
            config.d_model,
        )
        self.token_type_embedding = nn.Embedding(
            config.token_type_count,
            config.d_model,
        )
        self.token_norm = nn.LayerNorm(config.d_model)
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

    def _embed_tokens(self, tokens: StructuredTokens) -> torch.Tensor:
        numeric_content = self.numeric_projection(tokens.numeric)
        numeric_content = numeric_content * tokens.numeric_mask.unsqueeze(-1)
        word_content = self.word_encoder(tokens.word_ids)
        relative_player = self.relative_player_embedding(tokens.relative_players)
        token_type = self.token_type_embedding(tokens.token_types)
        return self.token_norm(
            numeric_content + word_content + relative_player + token_type
        )

    def forward(
        self,
        state_tokens: StructuredTokens,
        action_tokens: StructuredTokens,
    ) -> tuple[torch.Tensor, torch.Tensor]:
        state = self._embed_tokens(state_tokens).unsqueeze(0)
        actions = self._embed_tokens(action_tokens).unsqueeze(0)
        marker = self.state_marker
        encoded = self.encoder(torch.cat([marker, state], dim=1))
        pooled = self.final_norm(encoded[:, 0])
        query = self.policy_query(pooled).unsqueeze(1)
        keys = self.policy_key(actions)
        logits = (query * keys).sum(dim=-1) / (self.config.d_model**0.5)
        value = self.value_head(pooled).squeeze(-1)
        return logits, value

    @torch.no_grad()
    def act(
        self,
        state_tokens: StructuredTokens,
        action_tokens: StructuredTokens,
        deterministic: bool = False,
    ) -> tuple[int, float, float]:
        logits, value = self(state_tokens, action_tokens)
        distribution = Categorical(logits=logits.squeeze(0))
        action = (
            torch.argmax(logits, dim=-1).squeeze(0)
            if deterministic
            else distribution.sample()
        )
        log_probability = distribution.log_prob(action)
        return (
            int(action.item()),
            float(log_probability.item()),
            float(value.squeeze(0).item()),
        )
