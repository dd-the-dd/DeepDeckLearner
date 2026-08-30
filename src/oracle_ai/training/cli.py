from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import torch
import yaml

from oracle_ai.architectures import build_model, encoder_for_model
from oracle_ai.training.core import PPOConfig, PPOLearner
from oracle_ai.training.environments import Matchup, RustSessionEnvironment, TinySelfPlayEnvironment


def _load_config(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _matchups(config: dict[str, Any]) -> dict[str, Matchup]:
    result: dict[str, Matchup] = {}
    for item in config.get("matchups", []):
        result[item["id"]] = Matchup(
            id=item["id"],
            setup=item["setup"],
            learner_player_id=item["learnerPlayerId"],
            opponent_player_id=item["opponentPlayerId"],
            max_turns=int(item.get("maxTurns", 200)),
            mulligan_enabled=bool(item.get("mulliganEnabled", False)),
            free_mulligans=int(item.get("freeMulligans", 0)),
        )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Train the Oracle Transformer policy with PPO")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--smoke", action="store_true", help="use the tiny in-process environment")
    args = parser.parse_args()

    config = _load_config(args.config)
    seed = int(config.get("seed", 20260729))
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    device = torch.device(config.get("device", "cuda" if torch.cuda.is_available() else "cpu"))

    model = build_model(config.get("model", {}))
    encoder = encoder_for_model(
        model,
        max_state_tokens=int(config.get("maxStateTokens", 512)),
    )
    learner = PPOLearner(
        model,
        encoder,
        PPOConfig(**config.get("ppo", {})),
        device,
    )

    matchup_map = _matchups(config)
    matchup_ids = list(matchup_map) or ["tiny-self-play"]
    if args.smoke:
        environment = TinySelfPlayEnvironment(horizon=int(config.get("smokeHorizon", 8)))
    else:
        if not matchup_map:
            raise ValueError("at least one matchup is required outside --smoke mode")
        environment = RustSessionEnvironment(
            base_url=config.get("engineUrl", "http://127.0.0.1:8787"),
            matchups=matchup_map,
            timeout_seconds=float(config.get("engineTimeoutSeconds", 30)),
        )

    output = Path(config.get("outputDir", "runs/oracle-ai-v1"))
    output.mkdir(parents=True, exist_ok=True)
    (output / "resolved-config.json").write_text(
        json.dumps(config, indent=2, sort_keys=True), encoding="utf-8"
    )
    try:
        learner.train(
            environment=environment,
            matchup_ids=matchup_ids,
            episodes=int(config.get("episodes", 1000)),
            seed=seed,
            checkpoint_dir=output / "checkpoints",
            checkpoint_every=int(config.get("checkpointEvery", 100)),
        )
    finally:
        close = getattr(environment, "close", None)
        if close is not None:
            close()


if __name__ == "__main__":
    main()
