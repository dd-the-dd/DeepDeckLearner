from __future__ import annotations

import argparse
import json
import random
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

import httpx
import torch
import yaml
from torch.distributions import Categorical

from oracle_ai.encoding_v15 import GraphObservationEncoderV15
from oracle_ai.model_v15 import ModelConfigV15, PolicyBatchV15, TypedLatentWorldModelV15, v15_losses
from oracle_ai.training.environments import RustSelfPlayEnvironment
from oracle_ai.training.league import RandomTrainingMatchupSampler, _deck_catalog
from oracle_ai.training.world_model_v15 import _collate, load_replay_transitions


@dataclass(frozen=True)
class V15SelfPlayConfig:
    episodes: int = 10
    seed: int = 151_500
    max_decisions: int = 512
    updates_per_episode: int = 32
    batch_size: int = 8
    learning_rate: float = 1e-4
    delta_error_threshold: float = 0.01
    temperature: float = 1.0
    device: str = "auto"
    max_connection_retries: int = 60
    connection_retry_seconds: float = 5.0


def _policy_batch(
    encoder: GraphObservationEncoderV15,
    state: dict[str, Any],
    actions: list[dict[str, Any]],
    observer_id: str,
    device: torch.device,
) -> PolicyBatchV15:
    graph = encoder.encode(state, observer_id)
    action_features = encoder.encode_actions(actions, state, observer_id)
    return PolicyBatchV15(
        node_features=graph.node_features.unsqueeze(0).to(device),
        node_types=graph.node_types.unsqueeze(0).to(device),
        owners=graph.owners.unsqueeze(0).to(device),
        node_mask=torch.ones((1, len(graph.node_keys)), dtype=torch.bool, device=device),
        legal_action_features=action_features.unsqueeze(0).to(device),
        legal_action_mask=torch.ones((1, len(actions)), dtype=torch.bool, device=device),
        text_tokens=graph.text_tokens.unsqueeze(0).to(device),
        text_mask=graph.text_mask.unsqueeze(0).to(device),
        controllers=graph.controllers.unsqueeze(0).to(device),
        zones=graph.zones.unsqueeze(0).to(device),
        positions=graph.positions.unsqueeze(0).to(device),
    )


def _delta_parameters(model: TypedLatentWorldModelV15) -> list[torch.nn.Parameter]:
    for parameter in model.parameters():
        parameter.requires_grad_(False)
    modules = (
        model.transition_embedding,
        model.transition_operators,
        model.delta_gate,
        model.state_update_norm,
        model.effect_head,
        model.uncertainty_head,
    )
    parameters: list[torch.nn.Parameter] = []
    for module in modules:
        for parameter in module.parameters():
            parameter.requires_grad_(True)
            parameters.append(parameter)
    return parameters


def _save_checkpoint(
    output: Path,
    episode: int,
    model: TypedLatentWorldModelV15,
    model_config: ModelConfigV15,
    optimizer: torch.optim.Optimizer,
) -> Path:
    checkpoint = output / "selfplay-checkpoints" / f"episode-{episode:06d}"
    checkpoint.mkdir(parents=True, exist_ok=True)
    torch.save(
        {
            "model": model.state_dict(),
            "model_config": asdict(model_config),
            "observation_schema": model.observation_schema,
            "self_play_episode": episode,
            "optimizer": optimizer.state_dict(),
        },
        checkpoint / "v15-model.pt",
    )
    metadata_path = output / "local-model.json"
    if metadata_path.is_file():
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["checkpointPath"] = str(checkpoint.resolve())
        metadata["description"] = (
            "V15 typed-latent planner refined by Engine-authoritative V15 self-play."
        )
        pending = metadata_path.with_suffix(".pending")
        pending.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        pending.replace(metadata_path)
    return checkpoint


def _resume_checkpoint(output: Path, configured_checkpoint: Path) -> Path:
    checkpoints = sorted(
        path / "v15-model.pt"
        for path in (output / "selfplay-checkpoints").glob("episode-*")
        if (path / "v15-model.pt").is_file()
    )
    if checkpoints:
        return checkpoints[-1]
    if configured_checkpoint.is_dir():
        return configured_checkpoint / "v15-model.pt"
    return configured_checkpoint


def _write_state(state_path: Path, **values: Any) -> None:
    values["updatedAtUnixMs"] = int(time.time() * 1000)
    state_path.write_text(
        json.dumps(values, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def train_self_play(config_path: str | Path) -> Path:
    raw = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
    config = V15SelfPlayConfig(**raw.get("selfPlay", {}))
    output = Path(str(raw["outputDir"])).resolve()
    checkpoint = Path(str(raw["checkpoint"])).resolve()
    checkpoint_file = _resume_checkpoint(output, checkpoint)
    payload = torch.load(checkpoint_file, map_location="cpu", weights_only=False)
    model_config = ModelConfigV15(**cast(dict[str, Any], payload["model_config"]))
    device_name = config.device
    if device_name == "auto":
        device_name = "cuda" if torch.cuda.is_available() else "cpu"
    device = torch.device(device_name)
    model = TypedLatentWorldModelV15(model_config).to(device)
    model.load_state_dict(cast(dict[str, torch.Tensor], payload["model"]))
    optimizer = torch.optim.AdamW(_delta_parameters(model), lr=config.learning_rate)
    if "optimizer" in payload:
        optimizer.load_state_dict(cast(dict[str, Any], payload["optimizer"]))
    encoder = GraphObservationEncoderV15(model_config.node_feature_dim, model_config.action_dim)
    decks = _deck_catalog(raw)
    sampler = RandomTrainingMatchupSampler(
        raw.get(
            "trainingScenarioRandomizer",
            {"formats": ["free"], "playerCounts": [2], "maxTurns": 40},
        ),
        decks,
    )
    template = sampler.template()
    environment = RustSelfPlayEnvironment(
        str(raw.get("engineUrl", "http://127.0.0.1:8787")),
        {template.id: template},
        float(raw.get("engineTimeoutSeconds", 300)),
        learner_pilot_id="v15-self-play",
    )
    environment.capture_replay = True
    replay_dir = output / "selfplay-replays"
    replay_dir.mkdir(parents=True, exist_ok=True)
    metrics_path = output / "v15-selfplay-metrics.jsonl"
    state_path = output / "v15-selfplay-state.json"
    randomizer = random.Random(config.seed)
    latest_checkpoint = checkpoint_file.parent
    started = time.monotonic()
    completed_episodes = int(payload.get("self_play_episode", 0))
    connection_failures = 0

    episode = completed_episodes + 1
    while episode <= config.episodes:
        _write_state(
            state_path,
            status="running",
            trainingPhase="v15-self-play-delta-refinement",
            completed_episodes=episode - 1,
            trainingStep=episode - 1,
            currentEpisode=episode,
            resumedFrom=str(checkpoint_file),
        )
        matchup = sampler.sample(
            random.Random(config.seed + episode), participant_ids=("v15", "v15")
        )
        environment.matchups[matchup.id] = matchup
        try:
            step = environment.reset(matchup.id, config.seed + episode, False)
            decisions = 0
            while not step.done and decisions < config.max_decisions:
                if not step.actions:
                    raise RuntimeError(
                        "V15 self-play received a live decision without legal actions"
                    )
                observer_id = step.player_id or "player-1"
                batch = _policy_batch(encoder, step.state, step.actions, observer_id, device)
                model.eval()
                with torch.no_grad():
                    logits, _, _ = model.policy_value(batch)
                    scaled = logits / max(0.05, config.temperature)
                    action_index = int(Categorical(logits=scaled).sample().item())
                step = environment.step(action_index)
                decisions += 1
        except httpx.HTTPError as error:
            environment._discard_failed_session()
            connection_failures += 1
            _write_state(
                state_path,
                status="waiting-for-engine",
                trainingPhase="v15-self-play-delta-refinement",
                completed_episodes=episode - 1,
                trainingStep=episode - 1,
                currentEpisode=episode,
                connectionFailures=connection_failures,
                lastError=str(error),
            )
            print(
                json.dumps(
                    {
                        "episode": episode,
                        "event": "engine-connection-retry",
                        "attempt": connection_failures,
                        "error": str(error),
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
            if connection_failures >= config.max_connection_retries:
                raise
            time.sleep(config.connection_retry_seconds)
            continue
        connection_failures = 0
        if not step.done:
            environment._remove_session()

        replay_path = replay_dir / f"episode-{episode:06d}.json"
        replay_path.write_text(
            json.dumps(
                {
                    "schemaVersion": "v15-self-play-replay/v1",
                    "episode": episode,
                    "frames": environment.replay_frames,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        transitions = load_replay_transitions(
            [str(replay_path)], model_config, config.max_decisions
        )
        if not transitions:
            raise RuntimeError("V15 self-play produced no trainable Engine transitions")
        evaluation = _collate(
            randomizer.sample(transitions, min(len(transitions), config.batch_size)), device
        )
        model.eval()
        with torch.no_grad():
            before = float(
                v15_losses(evaluation, model(evaluation))["latent_transition_loss"].cpu()
            )

        updates = 0
        model.train()
        if before > config.delta_error_threshold:
            for _ in range(config.updates_per_episode):
                samples = randomizer.choices(
                    transitions, k=min(config.batch_size, len(transitions))
                )
                training_batch = _collate(samples, device)
                losses = v15_losses(training_batch, model(training_batch))
                delta_loss = (
                    losses["latent_transition_loss"]
                    + 0.5 * losses["effect_loss"]
                    + 0.1 * losses["uncertainty_loss"]
                )
                optimizer.zero_grad(set_to_none=True)
                delta_loss.backward()
                torch.nn.utils.clip_grad_norm_(_delta_parameters(model), 1.0)
                optimizer.step()
                updates += 1
        model.eval()
        with torch.no_grad():
            after = float(v15_losses(evaluation, model(evaluation))["latent_transition_loss"].cpu())
        latest_checkpoint = _save_checkpoint(output, episode, model, model_config, optimizer)
        record = {
            "episode": episode,
            "decisions": decisions,
            "transitions": len(transitions),
            "deltaErrorBefore": before,
            "deltaErrorAfter": after,
            "deltaUpdates": updates,
            "winner": (step.state.get("outcome") or {}).get("winner"),
            "dataSource": "engine-v15-self-play",
            "elapsedSeconds": time.monotonic() - started,
        }
        with metrics_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, sort_keys=True) + "\n")
        _write_state(
            state_path,
            status="running" if episode < config.episodes else "stopped",
            trainingPhase="v15-self-play-delta-refinement",
            completed_episodes=episode,
            trainingStep=episode,
            deltaErrorBefore=before,
            deltaErrorAfter=after,
        )
        print(json.dumps(record, sort_keys=True), flush=True)
        episode += 1
    return latest_checkpoint


def main() -> None:
    parser = argparse.ArgumentParser(description="Refine V15 delta-S through Engine self-play")
    parser.add_argument("--config", required=True)
    arguments = parser.parse_args()
    train_self_play(arguments.config)


if __name__ == "__main__":
    main()
