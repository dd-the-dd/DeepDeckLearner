from __future__ import annotations

import hashlib
import json
import os
import threading
from collections import OrderedDict
from copy import deepcopy
from pathlib import Path
from typing import Any

import torch
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from torch.distributions import Categorical

from oracle_ai.architectures import PolicyEncoder, PolicyModel, encoder_for_model
from oracle_ai.checkpoints import load_checkpoint, validate_checkpoint
from oracle_ai.decision_choices import expand_policy_actions
from oracle_ai.model import MagicTransformerActorCritic, ModelConfig
from oracle_ai.training.behavior import dominated_action_indices

SCHEMA_VERSION = "ai-decision/v1"


class EngineAction(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str


class EngineDecision(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    player_id: str = Field(alias="playerId")
    options: list[EngineAction]
    choice: dict[str, Any] | None = None


class DecisionRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    schema_version: str = Field(alias="schemaVersion")
    request_id: str = Field(alias="requestId")
    player_id: str = Field(alias="playerId")
    state: dict[str, Any]
    decision: EngineDecision
    deterministic: bool = True
    controller_id: str | None = Field(default=None, alias="controllerId")
    context_id: str | None = Field(default=None, alias="contextId")


class DecisionResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    schema_version: str = Field(alias="schemaVersion")
    request_id: str = Field(alias="requestId")
    action_id: str = Field(alias="actionId")
    number_value: int | None = Field(default=None, alias="numberValue")
    model: str
    value: float | None = None
    confidence: float | None = None
    policy_entropy: float | None = Field(default=None, alias="policyEntropy")
    action_probabilities: list[float] | None = Field(
        default=None,
        alias="actionProbabilities",
    )
    attended_state_token_indices: list[int] | None = Field(
        default=None,
        alias="attendedStateTokenIndices",
    )
    attended_state_tokens: list[str] | None = Field(
        default=None,
        alias="attendedStateTokens",
    )
    action_value: float | None = Field(default=None, alias="actionValue")
    action_activation_norms: list[float] | None = Field(
        default=None,
        alias="actionActivationNorms",
    )


class PolicyRuntime:
    def __init__(self) -> None:
        self.policy = os.getenv("ORACLE_AI_POLICY", "model")
        self.training_step = 0
        self.registry_path = (
            Path(path) if (path := os.getenv("ORACLE_AI_MODEL_REGISTRY")) else None
        )
        self.model_cache_size = max(1, int(os.getenv("ORACLE_AI_MODEL_CACHE_SIZE", "2")))
        self.model_cache: OrderedDict[
            str,
            tuple[PolicyModel, PolicyEncoder, int],
        ] = OrderedDict()
        self.model_cache_lock = threading.Lock()
        self.plan_cache_size = max(1, int(os.getenv("ORACLE_AI_PLAN_CACHE_SIZE", "512")))
        self.plan_cache: OrderedDict[str, torch.Tensor] = OrderedDict()
        self.observation_cache: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self.pregame_cache: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self.checkpoint_path: Path | None = None
        self.device = torch.device(
            os.getenv("ORACLE_AI_DEVICE", "cuda" if torch.cuda.is_available() else "cpu")
        )
        if self.registry_path is not None:
            self.policy = "model-registry"
            self.model = None
            self.encoder = None
            models = self.available_models()
            if not models:
                raise ValueError("ORACLE_AI_MODEL_REGISTRY contains no models")
            self.name = os.getenv("ORACLE_AI_MODEL_NAME", models[-1]["id"])
            selected = next(
                (model for model in models if model["id"] == self.name),
                models[-1],
            )
            self.training_step = int(selected["trainingStep"])
            return
        if self.policy == "random":
            self.model = None
            self.encoder = None
            self.random_seed = os.getenv("ORACLE_AI_RANDOM_SEED", "0")
            self.name = os.getenv("ORACLE_AI_MODEL_NAME", "oracle-random-v0")
            return
        if self.policy != "model":
            raise ValueError(f"unsupported ORACLE_AI_POLICY: {self.policy}")
        checkpoint = os.getenv("ORACLE_AI_CHECKPOINT")
        if checkpoint:
            self.checkpoint_path = Path(checkpoint)
            self.model, payload = load_checkpoint(self.checkpoint_path, self.device)
            self.training_step = int(payload.get("training_step", 0))
            default_name = f"oracle-transformer-step-{self.training_step}"
        else:
            config = ModelConfig()
            self.model = MagicTransformerActorCritic(config).to(self.device)
            default_name = "oracle-transformer-untrained"
        self.name = os.getenv("ORACLE_AI_MODEL_NAME", default_name)
        self.model.eval()
        self.encoder = encoder_for_model(self.model)

    def reload_checkpoint(self) -> int:
        if self.checkpoint_path is None:
            raise ValueError("this policy service has no reloadable checkpoint")
        model, payload = load_checkpoint(self.checkpoint_path, self.device)
        model.eval()
        encoder = encoder_for_model(model)
        training_step = int(payload.get("training_step", 0))
        with self.model_cache_lock:
            preserve_plans = (
                self.model is not None
                and getattr(self.model, "model_family", None)
                == getattr(model, "model_family", None)
                and getattr(self.model.config, "d_model", None)
                == getattr(model.config, "d_model", None)
            )
            self.model = model
            self.encoder = encoder
            self.training_step = training_step
            if not preserve_plans:
                self.plan_cache.clear()
                self.observation_cache.clear()
                self.pregame_cache.clear()
        return training_step

    def available_models(self) -> list[dict[str, Any]]:
        if self.registry_path is None:
            return [{"id": self.name, "trainingStep": self.training_step}]
        registry = json.loads(self.registry_path.read_text(encoding="utf-8"))
        if registry.get("schemaVersion") != "oracle-ai-model-registry/v1":
            raise ValueError("unsupported Oracle AI model registry")
        models = registry.get("models", [])
        if not isinstance(models, list):
            raise ValueError("Oracle AI model registry models must be a list")
        return [
            {
                "id": str(model["id"]),
                "trainingStep": int(model.get("trainingStep", 0)),
                "checkpoint": str(model["checkpoint"]),
            }
            for model in models
        ]

    def model_statuses(self) -> list[dict[str, Any]]:
        statuses = []
        for descriptor in self.available_models():
            error = None
            try:
                checkpoint = descriptor.get("checkpoint")
                if checkpoint is not None:
                    validate_checkpoint(Path(checkpoint))
            except (KeyError, OSError, TypeError, ValueError) as exception:
                error = str(exception)
            statuses.append({
                "id": descriptor["id"],
                "trainingStep": descriptor["trainingStep"],
                "available": error is None,
                **({"error": error} if error is not None else {}),
            })
        return statuses

    def _registry_policy(
        self,
        model_id: str,
    ) -> tuple[PolicyModel, PolicyEncoder, int]:
        cached = self.model_cache.pop(model_id, None)
        if cached is not None:
            self.model_cache[model_id] = cached
            return cached
        descriptor = next(
            (model for model in self.available_models() if model["id"] == model_id),
            None,
        )
        if descriptor is None:
            raise ValueError(f"unknown Oracle AI model: {model_id}")
        model, payload = load_checkpoint(Path(descriptor["checkpoint"]), self.device)
        model.eval()
        policy = (
            model,
            encoder_for_model(model),
            int(payload.get("training_step", descriptor["trainingStep"])),
        )
        self.model_cache[model_id] = policy
        while len(self.model_cache) > self.model_cache_size:
            self.model_cache.popitem(last=False)
            if self.device.type == "cuda":
                torch.cuda.empty_cache()
        return policy

    @torch.inference_mode()
    def choose_with_diagnostics(
        self,
        state: dict[str, Any],
        actions: list[dict[str, Any]],
        deterministic: bool,
        request_id: str,
        controller_id: str | None = None,
        context_id: str | None = None,
    ) -> tuple[int, float | None, str, dict[str, Any]]:
        if self.policy == "random":
            payload = json.dumps(
                {
                    "seed": self.random_seed,
                    "requestId": request_id,
                    "actions": [action.get("id") for action in actions],
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            digest = hashlib.blake2b(payload.encode("utf-8"), digest_size=8).digest()
            action = int.from_bytes(digest, "little") % len(actions)
            probability = 1.0 / len(actions)
            return action, None, self.name, {
                "confidence": probability,
                "policyEntropy": float(torch.log(torch.tensor(float(len(actions))))),
                "actionProbabilities": [probability] * len(actions),
                "attendedStateTokenIndices": None,
                "attendedStateTokens": None,
                "actionValue": None,
                "actionActivationNorms": None,
            }

        def choose_loaded_model(
            model: PolicyModel,
            encoder: PolicyEncoder,
            model_identity: str,
        ) -> tuple[int, float, dict[str, Any]]:
            policy_state = state
            uses_alpha_star_observation = getattr(model, "model_family", "") in {
                "structured-v11",
                "structured-v12",
            }
            observation_key = (
                f"{model_identity}:{context_id}"
                if uses_alpha_star_observation and context_id
                else None
            )
            if observation_key is not None:
                policy_state = dict(state)
                pregame = self.pregame_cache.pop(observation_key, None)
                if pregame is None:
                    player_id = str(
                        (state.get("_decisionContext") or {}).get(
                            "playerId",
                            state.get("priorityPlayer", ""),
                        )
                    )
                    own_cards: list[dict[str, Any]] = []
                    commanders: list[dict[str, Any]] = []
                    for player in state.get("players", []):
                        if not isinstance(player, dict):
                            continue
                        current_player_id = str(player.get("id", ""))
                        seen: set[str] = set()
                        for zone in (
                            "library",
                            "hand",
                            "battlefield",
                            "graveyard",
                            "exile",
                            "commandZone",
                        ):
                            for card in player.get(zone, []):
                                if not isinstance(card, dict):
                                    continue
                                definition = card.get("definition")
                                definition = definition if isinstance(definition, dict) else card
                                identity = str(
                                    card.get(
                                        "instanceId",
                                        definition.get("id", definition.get("name", "")),
                                    )
                                )
                                if identity in seen:
                                    continue
                                seen.add(identity)
                                if current_player_id == player_id:
                                    own_cards.append(definition)
                                if bool(definition.get("isCommander")):
                                    commanders.append(
                                        {
                                            "playerId": current_player_id,
                                            "card": definition,
                                        }
                                    )
                    pregame = {
                        "deck": own_cards,
                        "commanders": commanders,
                    }
                self.pregame_cache[observation_key] = pregame
                previous = self.observation_cache.pop(observation_key, None)
                if previous is not None:
                    policy_state["_previousObservation"] = previous
                policy_state["_pregameDeck"] = pregame["deck"]
                policy_state["_pregameCommanders"] = pregame["commanders"]
                self.observation_cache[observation_key] = deepcopy(
                    {
                        key: value
                        for key, value in state.items()
                        if not str(key).startswith("_")
                    }
                )
                while len(self.observation_cache) > self.plan_cache_size:
                    self.observation_cache.popitem(last=False)
                while len(self.pregame_cache) > self.plan_cache_size:
                    self.pregame_cache.popitem(last=False)
            encoded = encoder.encode(policy_state, actions)
            state_tokens = encoded.state_tokens.to(self.device)
            action_tokens = encoded.action_tokens.to(self.device)
            analysis_method = getattr(model, "analyze", None)
            if callable(analysis_method):
                memory_method = getattr(model, "evaluate_actions_with_memory", None)
                plan_key = (
                    f"{model_identity}:{context_id}"
                    if context_id and callable(memory_method)
                    else None
                )
                previous_plan = (
                    self.plan_cache.pop(plan_key, None) if plan_key is not None else None
                )
                if callable(memory_method):
                    analysis = analysis_method(
                        state_tokens,
                        action_tokens,
                        previous_plan,
                    )
                else:
                    analysis = analysis_method(state_tokens, action_tokens)
                strategic_plan = analysis.get("strategic_plan")
                if plan_key is not None and strategic_plan is not None:
                    self.plan_cache[plan_key] = strategic_plan.detach()
                    while len(self.plan_cache) > self.plan_cache_size:
                        self.plan_cache.popitem(last=False)
                logits = analysis["logits"]
                value = analysis["value"]
                probabilities = analysis["probabilities"].squeeze(0)
                entropy = analysis["entropy"].squeeze(0)
                attention = analysis.get("attention")
                action_values = analysis.get("action_values")
                activation_norms = analysis.get("action_activation_norms")
            else:
                logits, value = model(state_tokens, action_tokens)
                attention = None
                action_values = None
                activation_norms = None
            logits = torch.nan_to_num(logits, nan=0.0, posinf=1e4, neginf=-1e4)
            value = torch.nan_to_num(value, nan=0.0, posinf=1.0, neginf=-1.0)
            if action_values is not None:
                action_values = torch.nan_to_num(
                    action_values,
                    nan=0.0,
                    posinf=1e4,
                    neginf=-1e4,
                )
            if attention is not None:
                attention = torch.nan_to_num(attention, nan=0.0)
            if activation_norms is not None:
                activation_norms = torch.nan_to_num(
                    activation_norms,
                    nan=0.0,
                    posinf=1e4,
                    neginf=0.0,
                )
            policy_logits = logits.squeeze(0).clone()
            dominated = dominated_action_indices(state, actions)
            if dominated:
                policy_logits[list(dominated)] = -torch.inf
            improve_policy = getattr(model, "improve_policy", None)
            if callable(improve_policy) and action_values is not None:
                search_arguments: dict[str, Any] = {}
                event_code_indices = (
                    analysis.get("event_code_indices")
                    if callable(analysis_method)
                    else None
                )
                if event_code_indices is not None:
                    search_arguments["event_code_indices"] = event_code_indices
                probabilities = improve_policy(
                    logits,
                    action_values,
                    dominated,
                    **search_arguments,
                )
                distribution = Categorical(probs=probabilities)
            else:
                distribution = Categorical(logits=policy_logits)
                probabilities = distribution.probs
            if not torch.isfinite(probabilities).all() or probabilities.sum() <= 0:
                probabilities = torch.ones_like(policy_logits)
                if dominated:
                    probabilities[list(dominated)] = 0.0
                probabilities /= probabilities.sum().clamp_min(1.0)
                distribution = Categorical(probs=probabilities)
            entropy = distribution.entropy()
            selected = (
                torch.argmax(probabilities, dim=-1)
                if deterministic
                else distribution.sample()
            )
            attended_indices = None
            attended_tokens = None
            if attention is not None:
                selected_attention = attention[0, :, selected, :].mean(dim=0)
                count = min(8, selected_attention.numel())
                attended_indices = torch.topk(selected_attention, count).indices.tolist()
                labels = ("state_marker", *encoded.state_token_labels)
                attended_tokens = [
                    labels[index] if index < len(labels) else f"state_token:{index}"
                    for index in attended_indices
                ]
            selected_index = int(selected.item())
            selected_action_value = (
                float(action_values[0, selected].item())
                if action_values is not None
                else None
            )
            selected_activation_norms = (
                activation_norms[:, 0, selected].detach().cpu().tolist()
                if activation_norms is not None
                else None
            )
            return selected_index, float(value.squeeze(0).item()), {
                "confidence": float(probabilities[selected].item()),
                "policyEntropy": float(entropy.item()),
                "actionProbabilities": probabilities.detach().cpu().tolist(),
                "attendedStateTokenIndices": attended_indices,
                "attendedStateTokens": attended_tokens,
                "actionValue": selected_action_value,
                "actionActivationNorms": selected_activation_norms,
            }

        if self.registry_path is not None:
            selected_model = controller_id or self.name
            with self.model_cache_lock:
                model, encoder, _ = self._registry_policy(selected_model)
                action, value, diagnostics = choose_loaded_model(
                    model,
                    encoder,
                    selected_model,
                )
            return action, value, selected_model, diagnostics
        with self.model_cache_lock:
            assert self.model is not None and self.encoder is not None
            action, value, diagnostics = choose_loaded_model(
                self.model,
                self.encoder,
                self.name,
            )
        return action, value, self.name, diagnostics

    @torch.inference_mode()
    def choose_with_model(
        self,
        state: dict[str, Any],
        actions: list[dict[str, Any]],
        deterministic: bool,
        request_id: str,
        controller_id: str | None = None,
        context_id: str | None = None,
    ) -> tuple[int, float | None, str]:
        action, value, model, _ = self.choose_with_diagnostics(
            state,
            actions,
            deterministic,
            request_id,
            controller_id,
            context_id,
        )
        return action, value, model

    def choose(
        self,
        state: dict[str, Any],
        actions: list[dict[str, Any]],
        deterministic: bool,
        request_id: str,
    ) -> tuple[int, float | None]:
        action, value, _ = self.choose_with_model(
            state,
            actions,
            deterministic,
            request_id,
        )
        return action, value


runtime = PolicyRuntime()
app = FastAPI(title="Oracle AI", version="0.2.0")


@app.get("/health")
def health() -> dict[str, str | int | None]:
    return {
        "checkpointPath": (
            str(runtime.checkpoint_path.resolve())
            if runtime.checkpoint_path is not None
            else None
        ),
        "device": str(runtime.device),
        "modelFamily": (
            getattr(runtime.model, "model_family", "hashing-v1")
            if runtime.model is not None
            else None
        ),
        "status": "ok",
        "schemaVersion": SCHEMA_VERSION,
        "model": runtime.name,
        "policy": runtime.policy,
        "registryPath": (
            str(runtime.registry_path.resolve())
            if runtime.registry_path is not None
            else None
        ),
        "trainingStep": runtime.training_step,
    }


@app.get("/v1/models")
def models() -> dict[str, Any]:
    return {
        "schemaVersion": "oracle-ai-model-catalog/v1",
        "models": runtime.model_statuses(),
    }


@app.post("/v1/reload")
def reload_checkpoint() -> dict[str, str | int | None]:
    try:
        runtime.reload_checkpoint()
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return health()


@app.post("/v1/decisions", response_model=DecisionResponse, response_model_by_alias=True)
def choose_action(payload: DecisionRequest) -> DecisionResponse:
    if payload.schema_version != SCHEMA_VERSION:
        raise HTTPException(status_code=409, detail="unsupported decision schema")
    if payload.request_id != payload.decision.id:
        raise HTTPException(status_code=422, detail="requestId must match decision.id")
    if payload.player_id != payload.decision.player_id:
        raise HTTPException(status_code=422, detail="playerId must match decision.playerId")
    if not payload.decision.options:
        raise HTTPException(status_code=422, detail="decision contains no legal actions")

    try:
        actions = expand_policy_actions(payload.decision.model_dump(by_alias=True))
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    state = dict(payload.state)
    decision_context = state.get("_decisionContext")
    decision_context = (
        dict(decision_context) if isinstance(decision_context, dict) else {}
    )
    decision_context.update({
        "id": payload.decision.id,
        "playerId": payload.decision.player_id,
        "kind": getattr(payload.decision, "kind", None),
    })
    state["_decisionContext"] = decision_context
    try:
        selected_index, value, model_name, diagnostics = runtime.choose_with_diagnostics(
            state,
            actions,
            payload.deterministic,
            payload.request_id,
            payload.controller_id,
            payload.context_id,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    selected = actions[selected_index]
    return DecisionResponse(
        schemaVersion=SCHEMA_VERSION,
        requestId=payload.request_id,
        actionId=str(selected.get("_engineActionId", selected["id"])),
        numberValue=selected.get("_numberValue"),
        model=model_name,
        value=value,
        confidence=diagnostics["confidence"],
        policyEntropy=diagnostics["policyEntropy"],
        actionProbabilities=diagnostics["actionProbabilities"],
        attendedStateTokenIndices=diagnostics["attendedStateTokenIndices"],
        attendedStateTokens=diagnostics["attendedStateTokens"],
        actionValue=diagnostics["actionValue"],
        actionActivationNorms=diagnostics["actionActivationNorms"],
    )
