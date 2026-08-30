import json
import math
from pathlib import Path
from types import SimpleNamespace

import torch
from fastapi.testclient import TestClient

from oracle_ai.app import SCHEMA_VERSION, PolicyRuntime, app
from oracle_ai.checkpoints import save_checkpoint
from oracle_ai.model import MagicTransformerActorCritic, ModelConfig
from oracle_ai.model_v2 import MagicTransformerActorCriticV2, ModelConfigV2
from oracle_ai.model_v9 import MagicTransformerActorCriticV9, ModelConfigV9

client = TestClient(app)


def test_deterministic_inference_uses_the_search_improved_policy(monkeypatch) -> None:
    class StubEncoder:
        def encode(self, _state, _actions):
            return SimpleNamespace(
                state_tokens=torch.zeros((1, 1, 1)),
                action_tokens=torch.zeros((1, 2, 1)),
                state_token_labels=(),
            )

    class SearchPolicy(torch.nn.Module):
        def analyze(self, _state_tokens, _action_tokens):
            return {
                "logits": torch.tensor([[4.0, 1.0]]),
                "value": torch.tensor([0.0]),
                "probabilities": torch.tensor([[0.9, 0.1]]),
                "entropy": torch.tensor([0.0]),
                "attention": None,
                "action_values": torch.tensor([[0.0, 4.0]]),
                "action_activation_norms": None,
            }

        def improve_policy(self, _logits, _action_values, _masked_indices):
            return torch.tensor([0.125, 0.875])

    monkeypatch.delenv("ORACLE_AI_MODEL_REGISTRY", raising=False)
    monkeypatch.delenv("ORACLE_AI_CHECKPOINT", raising=False)
    monkeypatch.setenv("ORACLE_AI_POLICY", "model")
    monkeypatch.setenv("ORACLE_AI_DEVICE", "cpu")
    runtime = PolicyRuntime()
    runtime.model = SearchPolicy()
    runtime.encoder = StubEncoder()

    selected, _, _, diagnostics = runtime.choose_with_diagnostics(
        {"turnNumber": 1, "players": []},
        [
            {"id": "raw-logit-winner", "kind": "passPriority"},
            {"id": "search-winner", "kind": "playLand"},
        ],
        True,
        "decision:search-policy",
    )

    assert selected == 1
    assert diagnostics["actionProbabilities"] == [0.125, 0.875]


def test_v10_inference_passes_event_codes_to_search(monkeypatch) -> None:
    event_codes = torch.tensor([[3, 3]])
    received: dict[str, torch.Tensor | None] = {}

    class StubEncoder:
        def encode(self, _state, _actions):
            return SimpleNamespace(
                state_tokens=torch.zeros((1, 1, 1)),
                action_tokens=torch.zeros((1, 2, 1)),
                state_token_labels=(),
            )

    class SearchPolicy(torch.nn.Module):
        def analyze(self, _state_tokens, _action_tokens):
            return {
                "logits": torch.tensor([[1.0, 1.0]]),
                "value": torch.tensor([0.0]),
                "probabilities": torch.tensor([[0.5, 0.5]]),
                "entropy": torch.tensor([math.log(2.0)]),
                "attention": None,
                "action_values": torch.tensor([[0.0, 1.0]]),
                "action_activation_norms": None,
                "event_code_indices": event_codes,
            }

        def improve_policy(
            self,
            _logits,
            _action_values,
            _masked_indices,
            *,
            event_code_indices=None,
        ):
            received["eventCodeIndices"] = event_code_indices
            return torch.tensor([0.25, 0.75])

    monkeypatch.delenv("ORACLE_AI_MODEL_REGISTRY", raising=False)
    monkeypatch.delenv("ORACLE_AI_CHECKPOINT", raising=False)
    monkeypatch.setenv("ORACLE_AI_POLICY", "model")
    monkeypatch.setenv("ORACLE_AI_DEVICE", "cpu")
    runtime = PolicyRuntime()
    runtime.model = SearchPolicy()
    runtime.encoder = StubEncoder()

    selected, _, _, _ = runtime.choose_with_diagnostics(
        {"turnNumber": 1, "players": []},
        [
            {"id": "first", "kind": "passPriority"},
            {"id": "second", "kind": "playLand"},
        ],
        True,
        "decision:event-codes",
    )

    assert selected == 1
    assert received["eventCodeIndices"] is event_codes


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["device"] in {"cpu", "cuda"}
    assert response.json()["schemaVersion"] == SCHEMA_VERSION
    assert response.json()["model"].startswith("oracle-transformer")
    assert response.json()["modelFamily"] == "hashing-v1"
    assert response.json()["trainingStep"] == 0


def test_model_catalog_exposes_the_served_model() -> None:
    response = client.get("/v1/models")

    assert response.status_code == 200
    assert response.json() == {
        "schemaVersion": "oracle-ai-model-catalog/v1",
        "models": [
            {
                "available": True,
                "id": response.json()["models"][0]["id"],
                "trainingStep": 0,
            }
        ],
    }


def test_registry_lists_every_version_without_loading_all_models(
    monkeypatch,
    tmp_path,
) -> None:
    registry = tmp_path / "model-registry.json"
    registry.write_text(
        json.dumps(
            {
                "schemaVersion": "oracle-ai-model-registry/v1",
                "models": [
                    {
                        "id": "ia-gt-0",
                        "checkpoint": str(tmp_path / "gt-0"),
                        "trainingStep": 0,
                    },
                    {
                        "id": "ia-gt-1",
                        "checkpoint": str(tmp_path / "gt-1"),
                        "trainingStep": 900,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ORACLE_AI_MODEL_REGISTRY", str(registry))
    monkeypatch.setenv("ORACLE_AI_MODEL_NAME", "ia-gt-1")
    monkeypatch.setenv("ORACLE_AI_DEVICE", "cpu")

    registry_runtime = PolicyRuntime()

    assert registry_runtime.name == "ia-gt-1"
    assert registry_runtime.training_step == 900
    assert [model["id"] for model in registry_runtime.available_models()] == [
        "ia-gt-0",
        "ia-gt-1",
    ]
    assert registry_runtime.model_cache == {}


def test_registry_status_rejects_an_incompatible_checkpoint_without_loading_weights(
    monkeypatch,
    tmp_path: Path,
) -> None:
    checkpoint = tmp_path / "incompatible"
    checkpoint.mkdir()
    (checkpoint / "checkpoint.pt").write_bytes(b"not loaded during validation")
    (checkpoint / "manifest.json").write_text(
        json.dumps({
            "schema_version": "oracle-ai-checkpoint/v1",
            "model_family": "structured-v10",
            "model_config": {"removed_option": True},
        }),
        encoding="utf-8",
    )
    registry = tmp_path / "model-registry.json"
    registry.write_text(
        json.dumps({
            "schemaVersion": "oracle-ai-model-registry/v1",
            "models": [{
                "id": "ia-v10-old",
                "checkpoint": str(checkpoint),
                "trainingStep": 42,
            }],
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("ORACLE_AI_MODEL_REGISTRY", str(registry))
    monkeypatch.setenv("ORACLE_AI_MODEL_NAME", "ia-v10-old")
    monkeypatch.setenv("ORACLE_AI_DEVICE", "cpu")

    status = PolicyRuntime().model_statuses()[0]

    assert status["id"] == "ia-v10-old"
    assert status["available"] is False
    assert "removed_option" in status["error"]


def test_model_policy_hot_reloads_its_checkpoint(
    monkeypatch,
    tmp_path: Path,
) -> None:
    checkpoint = tmp_path / "live"
    model = MagicTransformerActorCritic(
        ModelConfig(feature_dim=16, d_model=16, layers=1, heads=4, feedforward_dim=32)
    )
    optimizer = torch.optim.AdamW(model.parameters())
    save_checkpoint(checkpoint, model, optimizer, 3, ["smoke"])
    monkeypatch.delenv("ORACLE_AI_MODEL_REGISTRY", raising=False)
    monkeypatch.setenv("ORACLE_AI_CHECKPOINT", str(checkpoint))
    monkeypatch.setenv("ORACLE_AI_DEVICE", "cpu")
    runtime = PolicyRuntime()

    save_checkpoint(checkpoint, model, optimizer, 9, ["smoke"])

    assert runtime.training_step == 3
    assert runtime.reload_checkpoint() == 9
    assert runtime.training_step == 9


def test_v9_policy_keeps_separate_plans_per_game_context(
    monkeypatch,
    tmp_path: Path,
) -> None:
    checkpoint = tmp_path / "v9-live"
    model = MagicTransformerActorCriticV9(
        ModelConfigV9(
            d_model=32,
            layers=1,
            heads=4,
            feedforward_dim=64,
            action_layers=1,
            plan_layers=1,
            max_words=16,
            max_relative_players=4,
            semantic_dim=16,
            semantic_layers=1,
            semantic_heads=4,
        )
    )
    save_checkpoint(
        checkpoint,
        model,
        torch.optim.AdamW(model.parameters()),
        0,
        ["v9-smoke"],
    )
    monkeypatch.delenv("ORACLE_AI_MODEL_REGISTRY", raising=False)
    monkeypatch.setenv("ORACLE_AI_CHECKPOINT", str(checkpoint))
    monkeypatch.setenv("ORACLE_AI_MODEL_NAME", "ia-v9-in-training")
    monkeypatch.setenv("ORACLE_AI_DEVICE", "cpu")
    runtime = PolicyRuntime()
    state = {
        "turnNumber": 1,
        "activePlayer": 0,
        "step": "upkeep",
        "players": [{"id": "player-1", "life": 20}],
        "_decisionContext": {"playerId": "player-1", "kind": "priority"},
    }
    actions = [{"id": "pass", "kind": "passPriority", "playerId": "player-1"}]

    runtime.choose_with_diagnostics(
        state,
        actions,
        True,
        "decision:1",
        context_id="game:1:player-1",
    )
    first_plan = runtime.plan_cache[
        "ia-v9-in-training:game:1:player-1"
    ].clone()
    runtime.choose_with_diagnostics(
        state,
        actions,
        True,
        "decision:2",
        context_id="game:1:player-1",
    )
    runtime.choose_with_diagnostics(
        state,
        actions,
        True,
        "decision:3",
        context_id="game:2:player-1",
    )

    assert len(runtime.plan_cache) == 2
    assert not torch.equal(
        first_plan,
        runtime.plan_cache["ia-v9-in-training:game:1:player-1"],
    )
    save_checkpoint(
        checkpoint,
        model,
        torch.optim.AdamW(model.parameters()),
        1,
        ["v9-smoke"],
    )

    assert runtime.reload_checkpoint() == 1
    assert "ia-v9-in-training:game:1:player-1" in runtime.plan_cache


def test_registry_serves_unchanged_v1_and_structured_v2(
    monkeypatch,
    tmp_path: Path,
) -> None:
    v1_checkpoint = tmp_path / "ia-gt-0"
    v1_model = MagicTransformerActorCritic(
        ModelConfig(feature_dim=16, d_model=16, layers=1, heads=4, feedforward_dim=32)
    )
    save_checkpoint(
        v1_checkpoint,
        v1_model,
        torch.optim.AdamW(v1_model.parameters()),
        0,
        ["mixed"],
    )
    v2_checkpoint = tmp_path / "ia-gt-1"
    v2_model = MagicTransformerActorCriticV2(
        ModelConfigV2(
            d_model=16,
            layers=1,
            heads=4,
            feedforward_dim=32,
            word_vocab_size=128,
            max_words=8,
            max_relative_players=4,
        )
    )
    save_checkpoint(
        v2_checkpoint,
        v2_model,
        torch.optim.AdamW(v2_model.parameters()),
        12,
        ["mixed"],
    )
    registry = tmp_path / "model-registry.json"
    registry.write_text(
        json.dumps(
            {
                "schemaVersion": "oracle-ai-model-registry/v1",
                "models": [
                    {
                        "id": "ia-gt-0",
                        "checkpoint": str(v1_checkpoint),
                        "trainingStep": 0,
                    },
                    {
                        "id": "ia-gt-1",
                        "checkpoint": str(v2_checkpoint),
                        "trainingStep": 12,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ORACLE_AI_MODEL_REGISTRY", str(registry))
    monkeypatch.setenv("ORACLE_AI_MODEL_NAME", "ia-gt-1")
    monkeypatch.setenv("ORACLE_AI_DEVICE", "cpu")
    runtime = PolicyRuntime()
    state = {
        "turnNumber": 1,
        "activePlayer": 0,
        "step": "upkeep",
        "players": [],
        "_decisionContext": {"playerId": "player-1", "kind": "priority"},
    }
    actions = [{"id": "pass", "kind": "passPriority", "playerId": "player-1"}]

    _, _, selected_v1 = runtime.choose_with_model(
        state,
        actions,
        True,
        "mixed:v1",
        "ia-gt-0",
    )
    _, _, selected_v2 = runtime.choose_with_model(
        state,
        actions,
        True,
        "mixed:v2",
        "ia-gt-1",
    )

    assert selected_v1 == "ia-gt-0"
    assert selected_v2 == "ia-gt-1"
    assert isinstance(runtime.model_cache["ia-gt-0"][0], MagicTransformerActorCritic)
    assert isinstance(runtime.model_cache["ia-gt-1"][0], MagicTransformerActorCriticV2)


def test_decision_returns_only_offered_action() -> None:
    offered = {"action:a", "action:b"}
    response = client.post(
        "/v1/decisions",
        json={
            "schemaVersion": SCHEMA_VERSION,
            "requestId": "decision:1",
            "playerId": "player:2",
            "state": {"turn": 3, "players": [{"id": "player:2", "life": 20}]},
            "decision": {
                "id": "decision:1",
                "playerId": "player:2",
                "options": [
                    {"id": "action:a", "kind": "passPriority"},
                    {"id": "action:b", "kind": "castSpell"},
                ],
            },
        },
    )
    assert response.status_code == 200
    assert response.json()["actionId"] in offered
    assert isinstance(response.json()["value"], float)
    assert 0.0 <= response.json()["confidence"] <= 1.0
    assert response.json()["policyEntropy"] >= 0.0
    assert len(response.json()["actionProbabilities"]) == 2


def test_number_decision_returns_one_integer_for_the_engine_action() -> None:
    response = client.post(
        "/v1/decisions",
        json={
            "schemaVersion": SCHEMA_VERSION,
            "requestId": "loop-iterations:1",
            "playerId": "player:2",
            "state": {"turnNumber": 3, "players": [{"id": "player:2", "life": 20}]},
            "decision": {
                "id": "loop-iterations:1",
                "kind": "resolutionChoice",
                "playerId": "player:2",
                "choice": {
                    "kind": "numberSelection",
                    "decisionId": "loopIterations",
                    "minimum": 0,
                    "maximum": 3,
                    "prompt": "Choose a finite loop count",
                },
                "options": [
                    {
                        "id": "choose-number:loopIterations",
                        "kind": "chooseResolution",
                        "playerId": "player:2",
                        "label": "Choose a finite loop count",
                    }
                ],
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["actionId"] == "choose-number:loopIterations"
    assert response.json()["numberValue"] in range(4)
    assert len(response.json()["actionProbabilities"]) == 4


def test_decision_rejects_empty_options() -> None:
    response = client.post(
        "/v1/decisions",
        json={
            "schemaVersion": SCHEMA_VERSION,
            "requestId": "decision:1",
            "playerId": "player:2",
            "state": {},
            "decision": {
                "id": "decision:1",
                "playerId": "player:2",
                "options": [],
            },
        },
    )
    assert response.status_code == 422


def test_random_policy_is_stable_for_a_fixed_decision(monkeypatch) -> None:
    monkeypatch.setenv("ORACLE_AI_POLICY", "random")
    monkeypatch.setenv("ORACLE_AI_DEVICE", "cpu")
    monkeypatch.setenv("ORACLE_AI_RANDOM_SEED", "41")
    runtime = PolicyRuntime()
    actions = [{"id": "a"}, {"id": "b"}, {"id": "c"}]

    first, first_value = runtime.choose({}, actions, True, "decision:fixed")
    second, second_value = runtime.choose({}, actions, True, "decision:fixed")

    assert first == second
    assert first_value is None
    assert second_value is None


def test_model_policy_replaces_non_finite_outputs_with_neutral_values(
    monkeypatch,
) -> None:
    monkeypatch.delenv("ORACLE_AI_CHECKPOINT", raising=False)
    monkeypatch.delenv("ORACLE_AI_MODEL_REGISTRY", raising=False)
    monkeypatch.setenv("ORACLE_AI_POLICY", "model")
    monkeypatch.setenv("ORACLE_AI_DEVICE", "cpu")
    runtime = PolicyRuntime()
    assert runtime.model is not None
    with torch.no_grad():
        for parameter in runtime.model.parameters():
            parameter.fill_(float("nan"))

    _, value, _, diagnostics = runtime.choose_with_diagnostics(
        {"turnNumber": 1, "players": []},
        [{"id": "pass", "kind": "passPriority"}],
        True,
        "decision:non-finite",
    )

    assert value == 0.0
    assert math.isfinite(diagnostics["confidence"])
    assert math.isfinite(diagnostics["policyEntropy"])
    assert all(math.isfinite(value) for value in diagnostics["actionProbabilities"])
