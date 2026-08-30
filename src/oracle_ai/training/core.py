from __future__ import annotations

import random
import threading
import time
import zlib
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import torch
from torch import nn
from torch.distributions import Categorical

from oracle_ai.architectures import PolicyEncoder, PolicyModel
from oracle_ai.checkpoints import save_checkpoint
from oracle_ai.encoding_v2 import StructuredTokens, TokenType
from oracle_ai.training.behavior import (
    DecisionTrace,
    build_decision_trace,
    dominated_action_indices,
)
from oracle_ai.training.future_features import (
    FUTURE_FEATURE_NAMES,
    FutureFeatureSnapshot,
    FutureFeatureTracker,
    future_feature_targets_from_snapshots,
)


@dataclass(frozen=True)
class DecisionStep:
    state: dict
    actions: list[dict]
    reward: float
    done: bool
    player_id: str | None = None
    rewards_by_player: dict[str, float] | None = None


class EpisodeEnvironment(Protocol):
    def reset(self, matchup_id: str, seed: int, seat_swap: bool) -> DecisionStep:
        ...

    def step(self, action_index: int) -> DecisionStep:
        ...


@dataclass(frozen=True)
class SelfPlayJob:
    environment: EpisodeEnvironment
    matchup_id: str
    seed: int
    learner_player_ids: frozenset[str] | None = None
    external_action_selector: Callable[[EpisodeEnvironment, DecisionStep], int] | None = None


@dataclass(frozen=True)
class PackedTensor:
    shape: tuple[int, ...]
    payload: bytes

    @classmethod
    def pack(cls, tensor: torch.Tensor) -> PackedTensor:
        compact = tensor.detach().cpu().to(torch.float16).contiguous()
        return cls(
            tuple(compact.shape),
            zlib.compress(compact.numpy().tobytes(), level=1),
        )

    def unpack(self, device: torch.device) -> torch.Tensor:
        buffer = bytearray(zlib.decompress(self.payload))
        return (
            torch.frombuffer(buffer, dtype=torch.float16)
            .reshape(self.shape)
            .to(device=device, dtype=torch.float32)
        )


@dataclass(frozen=True)
class PackedStructuredTokens:
    numeric: PackedTensor
    word_ids_shape: tuple[int, ...]
    word_ids_payload: bytes
    relative_players_shape: tuple[int, ...]
    relative_players_payload: bytes
    token_types_shape: tuple[int, ...]
    token_types_payload: bytes
    numeric_mask_shape: tuple[int, ...]
    numeric_mask_payload: bytes

    @staticmethod
    def _pack_bytes(tensor: torch.Tensor, dtype: torch.dtype) -> bytes:
        compact = tensor.detach().cpu().to(dtype).contiguous()
        return zlib.compress(compact.numpy().tobytes(), level=1)

    @staticmethod
    def _unpack_bytes(
        payload: bytes,
        shape: tuple[int, ...],
        dtype: torch.dtype,
        device: torch.device,
    ) -> torch.Tensor:
        buffer = bytearray(zlib.decompress(payload))
        return torch.frombuffer(buffer, dtype=dtype).reshape(shape).to(device)

    @classmethod
    def pack(cls, tokens: StructuredTokens) -> PackedStructuredTokens:
        return cls(
            numeric=PackedTensor.pack(tokens.numeric),
            word_ids_shape=tuple(tokens.word_ids.shape),
            word_ids_payload=cls._pack_bytes(tokens.word_ids, torch.int32),
            relative_players_shape=tuple(tokens.relative_players.shape),
            relative_players_payload=cls._pack_bytes(
                tokens.relative_players,
                torch.int16,
            ),
            token_types_shape=tuple(tokens.token_types.shape),
            token_types_payload=cls._pack_bytes(tokens.token_types, torch.uint8),
            numeric_mask_shape=tuple(tokens.numeric_mask.shape),
            numeric_mask_payload=cls._pack_bytes(tokens.numeric_mask, torch.uint8),
        )

    def unpack(self, device: torch.device) -> StructuredTokens:
        return StructuredTokens(
            numeric=self.numeric.unpack(device),
            word_ids=self._unpack_bytes(
                self.word_ids_payload,
                self.word_ids_shape,
                torch.int32,
                device,
            ).to(torch.long),
            relative_players=self._unpack_bytes(
                self.relative_players_payload,
                self.relative_players_shape,
                torch.int16,
                device,
            ).to(torch.long),
            token_types=self._unpack_bytes(
                self.token_types_payload,
                self.token_types_shape,
                torch.uint8,
                device,
            ).to(torch.long),
            numeric_mask=self._unpack_bytes(
                self.numeric_mask_payload,
                self.numeric_mask_shape,
                torch.uint8,
                device,
            ).to(torch.bool),
        )


@dataclass
class Transition:
    state_tokens: torch.Tensor | PackedTensor | StructuredTokens | PackedStructuredTokens
    action_tokens: torch.Tensor | PackedTensor | StructuredTokens | PackedStructuredTokens
    action_index: int
    old_log_probability: float
    reward: float
    value: float
    done: bool
    player_id: str | None = None
    decision_trace: DecisionTrace | None = None
    masked_action_indices: tuple[int, ...] = ()
    search_policy: tuple[float, ...] = ()
    apply_observation_dropout: bool = False
    previous_plan: PackedTensor | None = None
    plan_target: PackedTensor | None = None
    future_targets: PackedTensor | None = None
    future_mask: PackedTensor | None = None
    value_player_ids: tuple[str, ...] = ()
    player_value_targets: PackedTensor | None = None


@dataclass(frozen=True)
class SelfPlayCollection:
    job: SelfPlayJob
    trajectory: list[Transition]
    terminal: DecisionStep | None
    collection_seconds: float
    error: Exception | None = None


@dataclass(frozen=True)
class PPOConfig:
    gamma: float = 0.99
    gae_lambda: float = 0.95
    clip_ratio: float = 0.2
    value_coefficient: float = 0.5
    entropy_coefficient: float = 0.01
    learning_rate: float = 3e-4
    epochs: int = 4
    max_grad_norm: float = 1.0
    minibatch_size: int = 8
    observation_token_dropout: float = 0.0
    observation_word_dropout: float = 0.0
    observation_numeric_dropout: float = 0.0
    action_value_coefficient: float = 0.0
    value_clip_ratio: float = 0.0
    target_kl: float = 0.0
    ppo_policy_coefficient: float = 1.0
    search_policy_coefficient: float = 0.0
    future_prediction_coefficient: float = 0.0
    belief_coefficient: float = 0.0
    plan_coefficient: float = 0.0
    multiplayer_value_coefficient: float = 0.0
    event_reconstruction_coefficient: float = 0.0
    event_codebook_coefficient: float = 0.0
    event_commitment_coefficient: float = 0.0
    latent_value_coefficient: float = 0.0


class PPOLearner:
    def __init__(
        self,
        model: PolicyModel,
        encoder: PolicyEncoder,
        config: PPOConfig,
        device: torch.device,
    ) -> None:
        self.model = model.to(device)
        self.encoder = encoder
        self.config = config
        self.device = device
        for name in (
            "observation_token_dropout",
            "observation_word_dropout",
            "observation_numeric_dropout",
        ):
            rate = getattr(config, name)
            if not 0.0 <= rate <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")
        self.optimizer = torch.optim.AdamW(model.parameters(), lr=config.learning_rate)
        self.training_step = 0
        # Parallel games share one frozen policy snapshot. CUDA/PyTorch inference
        # remains serialized while Rust session advancement and HTTP I/O overlap.
        self._inference_lock = threading.Lock()

    @torch.no_grad()
    def _select_action(
        self,
        state_tokens: torch.Tensor | StructuredTokens,
        action_tokens: torch.Tensor | StructuredTokens,
        state: dict,
        actions: list[dict],
        *,
        add_exploration_noise: bool = False,
        previous_plan: torch.Tensor | None = None,
    ) -> tuple[
        int,
        float,
        float,
        float,
        float,
        tuple[float, ...],
        dict[str, torch.Tensor],
    ]:
        evaluation = self._evaluate_model_detailed(
            state_tokens.to(self.device),
            action_tokens.to(self.device),
            previous_plan,
        )
        logits = evaluation["logits"]
        value = evaluation["value"]
        action_values = evaluation.get("action_values")
        policy_logits = logits.squeeze(0).clone()
        dominated = dominated_action_indices(state, actions)
        if dominated:
            policy_logits[list(dominated)] = -torch.inf
        network_distribution = Categorical(logits=policy_logits)
        improve_policy = getattr(self.model, "improve_policy", None)
        if callable(improve_policy) and action_values is not None:
            search_arguments = {
                "add_exploration_noise": add_exploration_noise,
            }
            if "event_code_indices" in evaluation:
                search_arguments["event_code_indices"] = evaluation[
                    "event_code_indices"
                ]
            probabilities = improve_policy(
                logits,
                action_values,
                dominated,
                **search_arguments,
            )
            behavior_distribution = Categorical(probs=probabilities)
            search_policy = tuple(
                float(probability)
                for probability in probabilities.detach().cpu().tolist()
            )
        else:
            probabilities = network_distribution.probs
            behavior_distribution = network_distribution
            search_policy = ()
        action = behavior_distribution.sample()
        return (
            int(action.item()),
            float(network_distribution.log_prob(action).item()),
            float(value.squeeze(0).item()),
            float(probabilities[action].item()),
            float(behavior_distribution.entropy().item()),
            search_policy,
            evaluation,
        )

    def _evaluate_model_detailed(
        self,
        state_tokens: torch.Tensor | StructuredTokens,
        action_tokens: torch.Tensor | StructuredTokens,
        previous_plan: torch.Tensor | None = None,
    ) -> dict[str, torch.Tensor]:
        evaluate_with_memory = getattr(self.model, "evaluate_actions_with_memory", None)
        if callable(evaluate_with_memory):
            return evaluate_with_memory(
                state_tokens,
                action_tokens,
                previous_plan,
            )
        logits, value, action_values = self._evaluate_model(
            state_tokens,
            action_tokens,
        )
        result = {"logits": logits, "value": value}
        if action_values is not None:
            result["action_values"] = action_values
        return result

    def _evaluate_model(
        self,
        state_tokens: torch.Tensor | StructuredTokens,
        action_tokens: torch.Tensor | StructuredTokens,
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor | None]:
        evaluate_actions = getattr(self.model, "evaluate_actions", None)
        if callable(evaluate_actions):
            return evaluate_actions(state_tokens, action_tokens)
        logits, value = self.model(state_tokens, action_tokens)
        return logits, value, None

    def collect_episode(
        self,
        environment: EpisodeEnvironment,
        matchup_id: str,
        seed: int,
        seat_swap: bool,
    ) -> list[Transition]:
        was_training = self.model.training
        self.model.eval()
        try:
            step = environment.reset(matchup_id, seed, seat_swap)
            trajectory: list[Transition] = []
            state_history: list[FutureFeatureSnapshot] = []
            feature_tracker = FutureFeatureTracker()
            plans: dict[str, torch.Tensor] = {}
            while not step.done:
                if not step.actions:
                    raise RuntimeError(
                        "environment produced a decision without legal actions"
                    )
                encoded = self.encoder.encode(step.state, step.actions)
                state_history.append(feature_tracker.snapshot(step.state))
                state_tokens = self._apply_observation_dropout(
                    encoded.state_tokens,
                    enabled=was_training,
                )
                plan_key = step.player_id or "__single_agent__"
                previous_plan = plans.get(plan_key)
                (
                    action,
                    log_probability,
                    value,
                    confidence,
                    entropy,
                    search_policy,
                    evaluation,
                ) = self._select_action(
                    state_tokens,
                    encoded.action_tokens,
                    step.state,
                    step.actions,
                    add_exploration_noise=was_training,
                    previous_plan=previous_plan,
                )
                next_step = environment.step(action)
                strategic_plan = evaluation.get("strategic_plan")
                if strategic_plan is not None:
                    plans[plan_key] = strategic_plan.detach()
                trajectory.append(
                    Transition(
                        self._pack_tokens(state_tokens),
                        self._pack_tokens(encoded.action_tokens),
                        action,
                        log_probability,
                        next_step.reward,
                        value,
                        next_step.done,
                        step.player_id,
                        build_decision_trace(
                            step.state,
                            step.actions,
                            action,
                            confidence=confidence,
                            entropy=entropy,
                        ),
                        dominated_action_indices(step.state, step.actions),
                        search_policy,
                        previous_plan=(
                            PackedTensor.pack(previous_plan)
                            if previous_plan is not None
                            else None
                        ),
                        plan_target=(
                            PackedTensor.pack(strategic_plan)
                            if strategic_plan is not None
                            else None
                        ),
                        value_player_ids=self._relative_value_player_ids(
                            step.state,
                            step.player_id,
                        ),
                    )
                )
                step = next_step
            self._attach_future_targets(
                trajectory,
                state_history,
                feature_tracker.snapshot(step.state),
            )
            return trajectory
        finally:
            self.model.train(was_training)

    def collect_self_play_episode(
        self,
        environment: EpisodeEnvironment,
        matchup_id: str,
        seed: int,
    ) -> tuple[list[Transition], DecisionStep]:
        was_training = self.model.training
        self.model.eval()
        try:
            return self._collect_self_play_episode(
                environment,
                matchup_id,
                seed,
                training_mode=was_training,
            )
        finally:
            self.model.train(was_training)

    def collect_self_play_batch(
        self,
        jobs: list[SelfPlayJob],
        *,
        max_workers: int,
    ) -> list[SelfPlayCollection]:
        if not jobs:
            raise ValueError("cannot collect an empty self-play batch")
        if max_workers <= 0:
            raise ValueError("max_workers must be positive")
        was_training = self.model.training
        self.model.eval()

        def collect(job: SelfPlayJob) -> SelfPlayCollection:
            started = time.perf_counter()
            try:
                trajectory, terminal = self._collect_self_play_episode(
                    job.environment,
                    job.matchup_id,
                    job.seed,
                    training_mode=was_training,
                    learner_player_ids=job.learner_player_ids,
                    external_action_selector=job.external_action_selector,
                )
                return SelfPlayCollection(
                    job,
                    trajectory,
                    terminal,
                    time.perf_counter() - started,
                )
            except Exception as error:
                return SelfPlayCollection(
                    job,
                    [],
                    None,
                    time.perf_counter() - started,
                    error,
                )

        try:
            ordered: list[SelfPlayCollection | None] = [None] * len(jobs)
            with ThreadPoolExecutor(
                max_workers=min(max_workers, len(jobs)),
                thread_name_prefix="oracle-self-play",
            ) as executor:
                future_indices = {
                    executor.submit(collect, job): index
                    for index, job in enumerate(jobs)
                }
                for future in as_completed(future_indices):
                    ordered[future_indices[future]] = future.result()
            return [result for result in ordered if result is not None]
        finally:
            self.model.train(was_training)

    def _collect_self_play_episode(
        self,
        environment: EpisodeEnvironment,
        matchup_id: str,
        seed: int,
        *,
        training_mode: bool,
        learner_player_ids: frozenset[str] | None = None,
        external_action_selector: Callable[[EpisodeEnvironment, DecisionStep], int] | None = None,
    ) -> tuple[list[Transition], DecisionStep]:
        step = environment.reset(matchup_id, seed, seat_swap=False)
        trajectory: list[Transition] = []
        state_history: list[FutureFeatureSnapshot] = []
        feature_tracker = FutureFeatureTracker()
        plans: dict[str, torch.Tensor] = {}
        while not step.done:
            if not step.actions:
                raise RuntimeError(
                    "environment produced a decision without legal actions"
                )
            if step.player_id is None:
                raise RuntimeError("self-play decision is missing its acting player")
            if learner_player_ids is not None and step.player_id not in learner_player_ids:
                if external_action_selector is None:
                    raise RuntimeError(
                        f"no external policy controls player {step.player_id}"
                    )
                step = environment.step(external_action_selector(environment, step))
                continue
            encoded = self.encoder.encode(step.state, step.actions)
            state_history.append(feature_tracker.snapshot(step.state))
            state_tokens = encoded.state_tokens
            previous_plan = plans.get(step.player_id)
            with self._inference_lock:
                (
                    action,
                    log_probability,
                    value,
                    confidence,
                    entropy,
                    search_policy,
                    evaluation,
                ) = self._select_action(
                    state_tokens,
                    encoded.action_tokens,
                    step.state,
                    step.actions,
                    add_exploration_noise=training_mode,
                    previous_plan=previous_plan,
                )
            next_step = environment.step(action)
            strategic_plan = evaluation.get("strategic_plan")
            if strategic_plan is not None:
                plans[step.player_id] = strategic_plan.detach()
            trajectory.append(
                Transition(
                    self._pack_tokens(state_tokens),
                    self._pack_tokens(encoded.action_tokens),
                    action,
                    log_probability,
                    0.0,
                    value,
                    False,
                    step.player_id,
                    build_decision_trace(
                        step.state,
                        step.actions,
                        action,
                        confidence=confidence,
                        entropy=entropy,
                    ),
                    dominated_action_indices(step.state, step.actions),
                    search_policy,
                    apply_observation_dropout=training_mode,
                    previous_plan=(
                        PackedTensor.pack(previous_plan)
                        if previous_plan is not None
                        else None
                    ),
                    plan_target=(
                        PackedTensor.pack(strategic_plan)
                        if strategic_plan is not None
                        else None
                    ),
                    value_player_ids=self._relative_value_player_ids(
                        step.state,
                        step.player_id,
                    ),
                )
            )
            if next_step.rewards_by_player and not next_step.done:
                self._apply_incremental_self_play_rewards(
                    trajectory,
                    next_step.rewards_by_player,
                )
            step = next_step
        self._apply_terminal_self_play_rewards(
            trajectory,
            step.rewards_by_player or {},
        )
        self._attach_future_targets(
            trajectory,
            state_history,
            feature_tracker.snapshot(step.state),
        )
        return trajectory, step

    def _apply_observation_dropout(
        self,
        tokens: torch.Tensor | StructuredTokens,
        *,
        enabled: bool | None = None,
    ) -> torch.Tensor | StructuredTokens:
        if enabled is None:
            enabled = self.model.training
        if not enabled or not isinstance(tokens, StructuredTokens):
            return tokens
        if not any(
            (
                self.config.observation_token_dropout,
                self.config.observation_word_dropout,
                self.config.observation_numeric_dropout,
            )
        ):
            return tokens

        numeric = tokens.numeric
        word_ids = tokens.word_ids
        relative_players = tokens.relative_players
        token_types = tokens.token_types
        numeric_mask = tokens.numeric_mask

        if self.config.observation_token_dropout:
            protected = token_types.eq(
                int(TokenType.GAME_CONFIGURATION)
            ) | token_types.eq(int(TokenType.GAME_PHASE))
            keep = protected | torch.rand(
                token_types.shape,
                device=token_types.device,
            ).ge(self.config.observation_token_dropout)
            numeric = numeric[keep]
            word_ids = word_ids[keep]
            relative_players = relative_players[keep]
            token_types = token_types[keep]
            numeric_mask = numeric_mask[keep]

        if self.config.observation_word_dropout:
            hidden_words = torch.rand(
                word_ids.shape,
                device=word_ids.device,
            ).lt(
                self.config.observation_word_dropout
            ) & word_ids.ne(0)
            word_ids = word_ids.masked_fill(hidden_words, 0)

        if self.config.observation_numeric_dropout:
            hidden_numeric = torch.rand(
                numeric.shape,
                device=numeric.device,
            ).lt(self.config.observation_numeric_dropout)
            hidden_numeric &= numeric_mask.unsqueeze(-1)
            numeric = numeric.masked_fill(hidden_numeric, -1.0)

        return StructuredTokens(
            numeric=numeric,
            word_ids=word_ids,
            relative_players=relative_players,
            token_types=token_types,
            numeric_mask=numeric_mask,
        )

    @staticmethod
    def _apply_incremental_self_play_rewards(
        trajectory: list[Transition],
        rewards_by_player: dict[str, float],
    ) -> None:
        final_transition_by_player: dict[str, Transition] = {}
        for transition in trajectory:
            if transition.player_id is not None:
                final_transition_by_player[transition.player_id] = transition
        for player_id, reward in rewards_by_player.items():
            transition = final_transition_by_player.get(player_id)
            if transition is not None:
                transition.reward += float(reward)

    @staticmethod
    def _apply_terminal_self_play_rewards(
        trajectory: list[Transition],
        rewards_by_player: dict[str, float],
    ) -> None:
        final_transition_by_player: dict[str, Transition] = {}
        for transition in trajectory:
            if transition.player_id is not None:
                final_transition_by_player[transition.player_id] = transition
            if transition.value_player_ids:
                transition.player_value_targets = PackedTensor.pack(
                    torch.tensor(
                        [
                            rewards_by_player.get(player_id, 0.0)
                            for player_id in transition.value_player_ids
                        ],
                        dtype=torch.float32,
                    )
                )
        for player_id, transition in final_transition_by_player.items():
            transition.reward += rewards_by_player.get(player_id, 0.0)
            transition.done = True

    @staticmethod
    def _relative_value_player_ids(
        state: dict,
        acting_player_id: str | None,
    ) -> tuple[str, ...]:
        players = state.get("players")
        player_ids = (
            tuple(
                str(player.get("id", ""))
                for player in players
                if isinstance(player, dict) and player.get("id") is not None
            )
            if isinstance(players, list)
            else ()
        )
        if acting_player_id is None or acting_player_id not in player_ids:
            return player_ids
        actor_index = player_ids.index(acting_player_id)
        return player_ids[actor_index:] + player_ids[:actor_index]

    def _attach_future_targets(
        self,
        trajectory: list[Transition],
        state_history: list[FutureFeatureSnapshot],
        terminal_state: FutureFeatureSnapshot,
    ) -> None:
        horizons = int(getattr(self.model.config, "future_horizons", 0))
        player_slots = int(getattr(self.model.config, "future_player_slots", 0))
        feature_dim = int(getattr(self.model.config, "future_feature_dim", 0))
        if not horizons or not player_slots or not feature_dim:
            return
        if feature_dim != len(FUTURE_FEATURE_NAMES):
            raise ValueError(
                f"model expects {feature_dim} future features, "
                f"but the target schema contains {len(FUTURE_FEATURE_NAMES)}"
            )
        all_states = [*state_history, terminal_state]
        for index, transition in enumerate(trajectory):
            if transition.player_id is None:
                player_id = (
                    state_history[index].player_ids[0]
                    if state_history[index].player_ids
                    else ""
                )
            else:
                player_id = transition.player_id
            future_states = all_states[index + 1 : index + 1 + horizons]
            if not future_states:
                future_states = [terminal_state]
            targets, mask = future_feature_targets_from_snapshots(
                state_history[index],
                future_states,
                player_id,
                player_slots=player_slots,
                horizons=horizons,
            )
            transition.future_targets = PackedTensor.pack(targets)
            transition.future_mask = PackedTensor.pack(mask.to(torch.float32))

    def _advantages(
        self, trajectory: list[Transition]
    ) -> tuple[torch.Tensor, torch.Tensor]:
        grouped_indices: dict[str, list[int]] = {}
        for index, transition in enumerate(trajectory):
            group = transition.player_id or "__single_agent__"
            grouped_indices.setdefault(group, []).append(index)
        advantages = [0.0] * len(trajectory)
        for indices in grouped_indices.values():
            gae = 0.0
            next_value = 0.0
            for index in reversed(indices):
                transition = trajectory[index]
                nonterminal = 0.0 if transition.done else 1.0
                delta = (
                    transition.reward
                    + self.config.gamma * next_value * nonterminal
                    - transition.value
                )
                gae = (
                    delta
                    + self.config.gamma * self.config.gae_lambda * nonterminal * gae
                )
                advantages[index] = gae
                next_value = transition.value
        advantage_tensor = torch.tensor(
            advantages, dtype=torch.float32, device=self.device
        )
        returns = advantage_tensor + torch.tensor(
            [transition.value for transition in trajectory],
            dtype=torch.float32,
            device=self.device,
        )
        advantage_tensor = (advantage_tensor - advantage_tensor.mean()) / (
            advantage_tensor.std(unbiased=False) + 1e-8
        )
        return advantage_tensor, returns

    def update(self, trajectory: list[Transition]) -> dict[str, float]:
        if not trajectory:
            raise ValueError("cannot train on an empty trajectory")
        self.model.train()
        advantages, returns = self._advantages(trajectory)
        metric_totals = {
            "loss": 0.0,
            "policy_loss": 0.0,
            "search_policy_loss": 0.0,
            "value_loss": 0.0,
            "action_value_loss": 0.0,
            "future_prediction_loss": 0.0,
            "belief_loss": 0.0,
            "plan_loss": 0.0,
            "multiplayer_value_loss": 0.0,
            "event_reconstruction_loss": 0.0,
            "event_codebook_loss": 0.0,
            "event_commitment_loss": 0.0,
            "latent_value_loss": 0.0,
            "event_code_perplexity": 0.0,
            "entropy": 0.0,
            "approx_kl": 0.0,
        }
        metric_weight = 0
        stop_early = False
        for _ in range(self.config.epochs):
            indices = torch.randperm(len(trajectory)).tolist()
            for offset in range(0, len(indices), self.config.minibatch_size):
                batch_indices = indices[offset : offset + self.config.minibatch_size]
                policy_losses: list[torch.Tensor] = []
                search_policy_losses: list[torch.Tensor] = []
                value_losses: list[torch.Tensor] = []
                action_value_losses: list[torch.Tensor] = []
                future_prediction_losses: list[torch.Tensor] = []
                belief_losses: list[torch.Tensor] = []
                plan_losses: list[torch.Tensor] = []
                multiplayer_value_losses: list[torch.Tensor] = []
                event_reconstruction_losses: list[torch.Tensor] = []
                event_codebook_losses: list[torch.Tensor] = []
                event_commitment_losses: list[torch.Tensor] = []
                latent_value_losses: list[torch.Tensor] = []
                selected_event_codes: list[torch.Tensor] = []
                entropies: list[torch.Tensor] = []
                approximate_kls: list[torch.Tensor] = []
                for index in batch_indices:
                    transition = trajectory[index]
                    state_tokens = self._to_device(transition.state_tokens)
                    if transition.apply_observation_dropout:
                        state_tokens = self._apply_observation_dropout(
                            state_tokens,
                            enabled=True,
                        )
                    evaluation = self._evaluate_model_detailed(
                        state_tokens,
                        self._to_device(transition.action_tokens),
                        (
                            transition.previous_plan.unpack(self.device)
                            if transition.previous_plan is not None
                            else None
                        ),
                    )
                    logits = evaluation["logits"]
                    value = evaluation["value"]
                    action_values = evaluation.get("action_values")
                    policy_logits = logits.squeeze(0).clone()
                    if transition.masked_action_indices:
                        policy_logits[
                            list(transition.masked_action_indices)
                        ] = -torch.inf
                    distribution = Categorical(logits=policy_logits)
                    action = torch.tensor(transition.action_index, device=self.device)
                    log_probability = distribution.log_prob(action)
                    log_ratio = log_probability - transition.old_log_probability
                    ratio = torch.exp(log_ratio)
                    unclipped = ratio * advantages[index]
                    clipped = (
                        torch.clamp(
                            ratio,
                            1.0 - self.config.clip_ratio,
                            1.0 + self.config.clip_ratio,
                        )
                        * advantages[index]
                    )
                    policy_losses.append(-torch.minimum(unclipped, clipped))
                    if transition.search_policy:
                        target_policy = torch.tensor(
                            transition.search_policy,
                            dtype=torch.float32,
                            device=self.device,
                        )
                        positive = target_policy.gt(0.0)
                        log_policy = torch.log_softmax(policy_logits, dim=-1)
                        search_policy_losses.append(
                            -(target_policy[positive] * log_policy[positive]).sum()
                        )
                    predicted_value = value.squeeze(0)
                    if self.config.value_clip_ratio > 0.0:
                        old_value = torch.tensor(transition.value, device=self.device)
                        clipped_value = old_value + torch.clamp(
                            predicted_value - old_value,
                            -self.config.value_clip_ratio,
                            self.config.value_clip_ratio,
                        )
                        value_losses.append(
                            torch.maximum(
                                (predicted_value - returns[index]).pow(2),
                                (clipped_value - returns[index]).pow(2),
                            )
                        )
                    else:
                        value_losses.append((predicted_value - returns[index]).pow(2))
                    if action_values is not None:
                        selected_action_value = action_values.squeeze(0)[action]
                        action_value_losses.append(
                            (selected_action_value - returns[index]).pow(2)
                        )
                    if (
                        transition.future_targets is not None
                        and transition.future_mask is not None
                        and "future_mean" in evaluation
                        and "future_log_variance" in evaluation
                    ):
                        target = transition.future_targets.unpack(self.device)
                        mask = transition.future_mask.unpack(self.device).gt(0.5)
                        predicted_mean = evaluation["future_mean"][0, action]
                        predicted_log_variance = evaluation["future_log_variance"][
                            0, action
                        ]
                        if bool(mask.any()):
                            nll = 0.5 * (
                                torch.exp(-predicted_log_variance)
                                * (target - predicted_mean).pow(2)
                                + predicted_log_variance
                            )
                            future_prediction_losses.append(nll[mask].mean())
                        belief_mask = mask[0].clone()
                        belief_mask[0] = False
                        if (
                            bool(belief_mask.any())
                            and "belief_prediction" in evaluation
                        ):
                            belief_prediction = evaluation["belief_prediction"][0]
                            belief_losses.append(
                                (belief_prediction - target[0])
                                .pow(2)[belief_mask]
                                .mean()
                            )
                    if (
                        transition.plan_target is not None
                        and "strategic_plan" in evaluation
                    ):
                        target_plan = transition.plan_target.unpack(self.device)
                        plan_losses.append(
                            (evaluation["strategic_plan"] - target_plan).pow(2).mean()
                        )
                    if (
                        transition.player_value_targets is not None
                        and "player_values" in evaluation
                    ):
                        target_values = transition.player_value_targets.unpack(
                            self.device
                        )
                        predicted_values = evaluation["player_values"].squeeze(0)
                        value_count = min(
                            target_values.numel(),
                            predicted_values.numel(),
                        )
                        multiplayer_value_losses.append(
                            (
                                predicted_values[:value_count]
                                - target_values[:value_count]
                            )
                            .pow(2)
                            .mean()
                        )
                    if "event_code_indices" in evaluation:
                        selected_event_codes.append(
                            evaluation["event_code_indices"].reshape(-1)[action]
                        )
                        prequantized = evaluation["event_prequantized"][0, action]
                        quantized = evaluation["event_quantized"][0, action]
                        event_codebook_losses.append(
                            (quantized - prequantized.detach()).pow(2).mean()
                        )
                        event_commitment_losses.append(
                            (prequantized - quantized.detach()).pow(2).mean()
                        )
                        if (
                            transition.future_targets is not None
                            and transition.future_mask is not None
                            and "event_reconstructed_future" in evaluation
                        ):
                            target = transition.future_targets.unpack(self.device)
                            mask = transition.future_mask.unpack(self.device).gt(0.5)
                            reconstruction = evaluation["event_reconstructed_future"][
                                0, action
                            ]
                            if bool(mask.any()):
                                event_reconstruction_losses.append(
                                    (reconstruction - target).pow(2)[mask].mean()
                                )
                        if "latent_action_values" in evaluation:
                            latent_value_losses.append(
                                (
                                    evaluation["latent_action_values"][0, action]
                                    - returns[index]
                                ).pow(2)
                            )
                    entropies.append(distribution.entropy())
                    approximate_kls.append((ratio - 1.0) - log_ratio)
                policy_loss = torch.stack(policy_losses).mean()
                search_policy_loss = (
                    torch.stack(search_policy_losses).mean()
                    if search_policy_losses
                    else torch.zeros((), device=self.device)
                )
                value_loss = torch.stack(value_losses).mean()
                action_value_loss = (
                    torch.stack(action_value_losses).mean()
                    if action_value_losses
                    else torch.zeros((), device=self.device)
                )
                future_prediction_loss = (
                    torch.stack(future_prediction_losses).mean()
                    if future_prediction_losses
                    else torch.zeros((), device=self.device)
                )
                belief_loss = (
                    torch.stack(belief_losses).mean()
                    if belief_losses
                    else torch.zeros((), device=self.device)
                )
                plan_loss = (
                    torch.stack(plan_losses).mean()
                    if plan_losses
                    else torch.zeros((), device=self.device)
                )
                multiplayer_value_loss = (
                    torch.stack(multiplayer_value_losses).mean()
                    if multiplayer_value_losses
                    else torch.zeros((), device=self.device)
                )
                event_reconstruction_loss = (
                    torch.stack(event_reconstruction_losses).mean()
                    if event_reconstruction_losses
                    else torch.zeros((), device=self.device)
                )
                event_codebook_loss = (
                    torch.stack(event_codebook_losses).mean()
                    if event_codebook_losses
                    else torch.zeros((), device=self.device)
                )
                event_commitment_loss = (
                    torch.stack(event_commitment_losses).mean()
                    if event_commitment_losses
                    else torch.zeros((), device=self.device)
                )
                latent_value_loss = (
                    torch.stack(latent_value_losses).mean()
                    if latent_value_losses
                    else torch.zeros((), device=self.device)
                )
                if selected_event_codes:
                    codes = torch.stack(selected_event_codes)
                    code_count = int(
                        getattr(self.model.config, "event_codebook_size", 1)
                    )
                    histogram = torch.bincount(codes, minlength=code_count).float()
                    code_probabilities = histogram / histogram.sum().clamp_min(1.0)
                    event_code_perplexity = torch.exp(
                        -(
                            code_probabilities
                            * code_probabilities.clamp_min(1e-12).log()
                        ).sum()
                    )
                else:
                    event_code_perplexity = torch.zeros((), device=self.device)
                entropy = torch.stack(entropies).mean()
                approximate_kl = torch.stack(approximate_kls).mean()
                loss = (
                    self.config.ppo_policy_coefficient * policy_loss
                    + self.config.search_policy_coefficient * search_policy_loss
                    + self.config.value_coefficient * value_loss
                    + self.config.action_value_coefficient * action_value_loss
                    + self.config.future_prediction_coefficient * future_prediction_loss
                    + self.config.belief_coefficient * belief_loss
                    + self.config.plan_coefficient * plan_loss
                    + self.config.multiplayer_value_coefficient * multiplayer_value_loss
                    + self.config.event_reconstruction_coefficient
                    * event_reconstruction_loss
                    + self.config.event_codebook_coefficient * event_codebook_loss
                    + self.config.event_commitment_coefficient * event_commitment_loss
                    + self.config.latent_value_coefficient * latent_value_loss
                    - self.config.entropy_coefficient * entropy
                )
                self.optimizer.zero_grad(set_to_none=True)
                loss.backward()
                nn.utils.clip_grad_norm_(
                    self.model.parameters(), self.config.max_grad_norm
                )
                self.optimizer.step()
                weight = len(batch_indices)
                metric_weight += weight
                metric_totals["loss"] += float(loss.detach().cpu()) * weight
                metric_totals["policy_loss"] += (
                    float(policy_loss.detach().cpu()) * weight
                )
                metric_totals["search_policy_loss"] += (
                    float(search_policy_loss.detach().cpu()) * weight
                )
                metric_totals["value_loss"] += float(value_loss.detach().cpu()) * weight
                metric_totals["action_value_loss"] += (
                    float(action_value_loss.detach().cpu()) * weight
                )
                metric_totals["future_prediction_loss"] += (
                    float(future_prediction_loss.detach().cpu()) * weight
                )
                metric_totals["belief_loss"] += (
                    float(belief_loss.detach().cpu()) * weight
                )
                metric_totals["plan_loss"] += float(plan_loss.detach().cpu()) * weight
                metric_totals["multiplayer_value_loss"] += (
                    float(multiplayer_value_loss.detach().cpu()) * weight
                )
                metric_totals["event_reconstruction_loss"] += (
                    float(event_reconstruction_loss.detach().cpu()) * weight
                )
                metric_totals["event_codebook_loss"] += (
                    float(event_codebook_loss.detach().cpu()) * weight
                )
                metric_totals["event_commitment_loss"] += (
                    float(event_commitment_loss.detach().cpu()) * weight
                )
                metric_totals["latent_value_loss"] += (
                    float(latent_value_loss.detach().cpu()) * weight
                )
                metric_totals["event_code_perplexity"] += (
                    float(event_code_perplexity.detach().cpu()) * weight
                )
                metric_totals["entropy"] += float(entropy.detach().cpu()) * weight
                metric_totals["approx_kl"] += (
                    float(approximate_kl.detach().cpu()) * weight
                )
                if (
                    self.config.target_kl > 0.0
                    and float(approximate_kl.detach().cpu())
                    > 1.5 * self.config.target_kl
                ):
                    stop_early = True
                    break
            if stop_early:
                break
        self.training_step += len(trajectory)
        return {
            metric: total / metric_weight for metric, total in metric_totals.items()
        }

    @staticmethod
    def _pack_tokens(
        tokens: torch.Tensor | StructuredTokens,
    ) -> PackedTensor | PackedStructuredTokens:
        if isinstance(tokens, StructuredTokens):
            return PackedStructuredTokens.pack(tokens)
        return PackedTensor.pack(tokens)

    def _to_device(
        self,
        tokens: torch.Tensor | PackedTensor | StructuredTokens | PackedStructuredTokens,
    ) -> torch.Tensor | StructuredTokens:
        if isinstance(tokens, (PackedTensor, PackedStructuredTokens)):
            return tokens.unpack(self.device)
        return tokens.to(self.device)

    def train(
        self,
        environment: EpisodeEnvironment,
        matchup_ids: list[str],
        episodes: int,
        seed: int,
        checkpoint_dir: Path,
        checkpoint_every: int = 100,
    ) -> None:
        randomizer = random.Random(seed)
        for episode in range(1, episodes + 1):
            matchup_id = randomizer.choice(matchup_ids)
            episode_seed = randomizer.randrange(0, 2**63)
            trajectory = self.collect_episode(
                environment,
                matchup_id,
                episode_seed,
                seat_swap=bool(episode % 2),
            )
            self.update(trajectory)
            if episode % checkpoint_every == 0 or episode == episodes:
                save_checkpoint(
                    checkpoint_dir / f"step-{self.training_step}",
                    self.model,
                    self.optimizer,
                    self.training_step,
                    matchup_ids,
                )
