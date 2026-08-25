from __future__ import annotations

import argparse
import json
import logging
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import torch
from torch import nn

from .checkpoint import load_checkpoint, save_checkpoint
from .encoding import DecisionEncoder, EncoderConfig
from .models import ModelConfig, PolicyV11, build_model

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class DecisionSample:
    observation: dict[str, Any]
    actions: list[dict[str, Any]]
    chosen_action_id: str
    previous_observation: dict[str, Any] | None = None
    known_deck: list[dict[str, Any]] | None = None
    value_targets: tuple[float, ...] = ()

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> DecisionSample:
        observation = raw.get("observation")
        decision = raw.get("decision")
        actions = raw.get("legalActions")
        if actions is None and isinstance(decision, dict):
            actions = decision.get("options")
        chosen = raw.get("chosenActionId")
        if not isinstance(observation, dict):
            raise ValueError("sample.observation must be an object")
        if not isinstance(actions, list) or not all(isinstance(action, dict) for action in actions):
            raise ValueError("sample legal actions must be an array of objects")
        if not isinstance(chosen, str) or not chosen:
            raise ValueError("sample.chosenActionId must be a non-empty string")
        if chosen not in {str(action.get("id", "")) for action in actions}:
            raise ValueError(f"chosen action {chosen!r} is not present in legal actions")
        previous = raw.get("previousObservation")
        known_deck = raw.get("knownDeck")
        targets = raw.get("valueTargets", [])
        if previous is not None and not isinstance(previous, dict):
            raise ValueError("sample.previousObservation must be an object")
        if known_deck is not None and (
            not isinstance(known_deck, list)
            or not all(isinstance(card, dict) for card in known_deck)
        ):
            raise ValueError("sample.knownDeck must be an array of objects")
        if not isinstance(targets, list) or not all(
            isinstance(value, (int, float)) for value in targets
        ):
            raise ValueError("sample.valueTargets must be an array of numbers")
        return cls(
            observation=observation,
            actions=actions,
            chosen_action_id=chosen,
            previous_observation=previous,
            known_deck=known_deck,
            value_targets=tuple(float(value) for value in targets),
        )


def load_jsonl(path: str | Path) -> list[DecisionSample]:
    source = Path(path)
    samples: list[DecisionSample] = []
    for line_number, line in enumerate(source.read_text("utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
            if not isinstance(raw, dict):
                raise ValueError("sample must be an object")
            samples.append(DecisionSample.from_dict(raw))
        except (json.JSONDecodeError, ValueError) as error:
            raise ValueError(f"{source}:{line_number}: {error}") from error
    if not samples:
        raise ValueError(f"{source} contains no training samples")
    return samples


def smoke_samples(version: str) -> list[DecisionSample]:
    player_count = 2 if version == "v12" else 4
    players: list[dict[str, Any]] = [
        {
            "id": f"p{index + 1}",
            "life": 20 if version == "v12" else 40,
            "hand": [],
            "battlefield": [],
            "library": [{}] * (53 - index),
            "graveyard": [],
            "exile": [],
        }
        for index in range(player_count)
    ]
    observation = {
        "turnNumber": 1,
        "activePlayer": 0,
        "step": "precombatMain",
        "gameMode": "legacy" if version == "v12" else "commander",
        "players": players,
        "stack": [],
        "events": [],
    }
    actions = [
        {"id": "play-land", "kind": "playLand", "cardInstanceId": "mountain"},
        {"id": "pass", "kind": "passPriority"},
    ]
    return [
        DecisionSample(
            observation=observation,
            actions=actions,
            chosen_action_id="play-land",
            value_targets=(1.0, -1.0) if version == "v12" else (1.0, 0.2, -0.4, -0.8),
        ),
        DecisionSample(
            observation={**observation, "turnNumber": 2, "step": "endStep"},
            previous_observation=observation,
            actions=[
                {"id": "cast", "kind": "castSpell", "cardInstanceId": "bolt"},
                {"id": "pass", "kind": "passPriority"},
            ],
            chosen_action_id="cast",
            value_targets=(0.8, -0.8) if version == "v12" else (0.8, 0.1, -0.3, -0.6),
        ),
    ]


def train(
    model: PolicyV11,
    samples: list[DecisionSample],
    *,
    epochs: int = 3,
    learning_rate: float = 3e-4,
    value_coefficient: float = 0.5,
    device: torch.device | str = "cpu",
    seed: int = 1,
) -> dict[str, float]:
    if epochs <= 0:
        raise ValueError("epochs must be positive")
    if learning_rate <= 0:
        raise ValueError("learning_rate must be positive")
    resolved_device = torch.device(device)
    model.to(resolved_device)
    model.train()
    encoder = DecisionEncoder(EncoderConfig(feature_size=model.config.feature_size))
    optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate)
    generator = random.Random(seed)
    totals = {"loss": 0.0, "policy_loss": 0.0, "value_loss": 0.0, "updates": 0.0}
    order = list(range(len(samples)))
    for _ in range(epochs):
        generator.shuffle(order)
        for index in order:
            sample = samples[index]
            encoded = encoder.encode(
                sample.observation,
                sample.actions,
                previous_observation=sample.previous_observation,
                known_deck=sample.known_deck,
            ).to(resolved_device)
            output = model(encoded)
            selected = next(
                action_index
                for action_index, action in enumerate(sample.actions)
                if str(action.get("id")) == sample.chosen_action_id
            )
            policy_loss = nn.functional.cross_entropy(
                output.logits.unsqueeze(0),
                torch.tensor([selected], device=resolved_device),
            )
            value_loss = torch.zeros((), device=resolved_device)
            if sample.value_targets:
                count = len(sample.value_targets)
                if count > output.player_values.numel():
                    raise ValueError(
                        f"sample has {count} value targets, but {model.family} exposes "
                        f"{output.player_values.numel()} slots"
                    )
                targets = torch.tensor(
                    sample.value_targets,
                    dtype=torch.float32,
                    device=resolved_device,
                )
                value_loss = nn.functional.mse_loss(output.player_values[:count], targets)
            loss = policy_loss + value_coefficient * value_loss
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
            optimizer.step()
            totals["loss"] += float(loss.detach().cpu())
            totals["policy_loss"] += float(policy_loss.detach().cpu())
            totals["value_loss"] += float(value_loss.detach().cpu())
            totals["updates"] += 1
    updates = max(1.0, totals["updates"])
    model.eval()
    return {
        "loss": totals["loss"] / updates,
        "policy_loss": totals["policy_loss"] / updates,
        "value_loss": totals["value_loss"] / updates,
        "updates": totals["updates"],
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Train the public V11/V12 starter policy.")
    result.add_argument("version", choices=("v11", "v12"))
    source = result.add_mutually_exclusive_group(required=True)
    source.add_argument("--dataset", type=Path, help="JSON Lines decision dataset.")
    source.add_argument("--smoke", action="store_true", help="Use two built-in test samples.")
    result.add_argument("--output", type=Path, required=True, help="New checkpoint directory.")
    result.add_argument("--resume", type=Path, help="Continue from an existing checkpoint.")
    result.add_argument("--epochs", type=int, default=3)
    result.add_argument("--learning-rate", type=float, default=3e-4)
    result.add_argument("--value-coefficient", type=float, default=0.5)
    result.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    result.add_argument("--seed", type=int, default=1)
    return result


def main() -> None:
    arguments = parser().parse_args()
    torch.manual_seed(arguments.seed)
    samples = smoke_samples(arguments.version) if arguments.smoke else load_jsonl(arguments.dataset)
    model = (
        load_checkpoint(arguments.resume, device=arguments.device)
        if arguments.resume
        else build_model(
            arguments.version,
            ModelConfig(multiplayer_value_slots=2 if arguments.version == "v12" else 4),
        )
    )
    if model.family != f"example-{arguments.version}":
        raise SystemExit(f"resume checkpoint contains {model.family}, not {arguments.version}")
    metrics = train(
        model,
        samples,
        epochs=arguments.epochs,
        learning_rate=arguments.learning_rate,
        value_coefficient=arguments.value_coefficient,
        device=arguments.device,
        seed=arguments.seed,
    )
    target = save_checkpoint(arguments.output, model)
    LOGGER.info("saved %s after %d updates", target, int(metrics["updates"]))
    print(json.dumps({"checkpoint": str(target), **metrics}, sort_keys=True))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
