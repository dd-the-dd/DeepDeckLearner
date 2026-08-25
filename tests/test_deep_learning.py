# ruff: noqa: E402, I001

from __future__ import annotations

import copy

import pytest

torch = pytest.importorskip("torch")

from deepdeck_agent import Decision, Game
from deepdeck_examples.deep_learning import (
    DecisionEncoder,
    DeepLearningAgent,
    ModelConfig,
    PolicyV11,
    PolicyV12,
    build_deep_learning_agent,
    load_checkpoint,
    save_checkpoint,
)
from deepdeck_examples.deep_learning.training import smoke_samples, train


def observation(player_count: int = 2) -> dict:
    return {
        "turnNumber": 2,
        "activePlayer": 0,
        "step": "precombatMain",
        "players": [
            {
                "id": f"p{index + 1}",
                "life": 20 - index,
                "hand": [],
                "battlefield": [],
                "library": [{}] * 50,
                "graveyard": [],
                "exile": [],
            }
            for index in range(player_count)
        ],
        "stack": [],
        "events": [],
    }


def legal_actions() -> list[dict]:
    return [
        {"id": "cast", "kind": "castSpell", "cardInstanceId": "spell"},
        {"id": "pass", "kind": "passPriority"},
    ]


def small_config(*, players: int) -> ModelConfig:
    return ModelConfig(
        model_size=32,
        heads=4,
        feedforward_size=48,
        state_layers=1,
        difference_layers=1,
        dropout=0,
        multiplayer_value_slots=players,
    )


def test_encoder_is_deterministic_and_keeps_one_token_per_action() -> None:
    encoder = DecisionEncoder()
    first = encoder.encode(observation(), legal_actions())
    second = encoder.encode(copy.deepcopy(observation()), copy.deepcopy(legal_actions()))
    assert torch.equal(first.state_tokens, second.state_tokens)
    assert torch.equal(first.action_tokens, second.action_tokens)
    assert first.action_tokens.shape == (2, 64)


def test_v11_and_v12_produce_dynamic_legal_logits_and_expected_values() -> None:
    encoded = DecisionEncoder().encode(observation(4), legal_actions())
    v11 = PolicyV11(small_config(players=4)).eval()(encoded)
    assert v11.logits.shape == (2,)
    assert v11.player_values.shape == (4,)
    v12_encoded = DecisionEncoder().encode(observation(2), legal_actions())
    v12 = PolicyV12(small_config(players=2)).eval()(v12_encoded)
    assert v12.logits.shape == (2,)
    assert v12.player_values.shape == (2,)
    assert torch.allclose(v12.player_values[0], -v12.player_values[1])


def test_training_writes_a_reloadable_weight_checkpoint(tmp_path) -> None:
    model = PolicyV12(small_config(players=2))
    metrics = train(model, smoke_samples("v12"), epochs=1, learning_rate=1e-3)
    checkpoint = save_checkpoint(tmp_path / "checkpoint", model)
    loaded = load_checkpoint(checkpoint)
    assert loaded.family == "example-v12"
    assert metrics["updates"] == 2
    assert (checkpoint / "config.json").exists()
    assert (checkpoint / "model.pt").exists()


def test_checkpoint_restores_the_encoder_feature_size(tmp_path) -> None:
    config = ModelConfig(
        feature_size=32,
        model_size=32,
        heads=4,
        feedforward_size=48,
        state_layers=1,
        difference_layers=1,
        multiplayer_value_slots=2,
    )
    checkpoint = save_checkpoint(tmp_path / "custom", PolicyV12(config))
    agent = build_deep_learning_agent("v12", checkpoint=checkpoint)
    assert agent.encoder.config.feature_size == 32


@pytest.mark.asyncio
async def test_agent_always_returns_an_exact_legal_action() -> None:
    current_observation = observation()
    current = Decision(
        "decision-1",
        "p1",
        {"kind": "priority", "options": legal_actions()},
        Game(current_observation, "p1"),
    )
    agent = DeepLearningAgent(PolicyV12(small_config(players=2)))
    response = await agent.make_decision(current)
    assert response.action_id in {"cast", "pass"}
