from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import torch

from oracle_ai.architectures import PolicyModel, build_model, validate_model_config


@dataclass(frozen=True)
class CheckpointManifest:
    schema_version: str
    model_family: str
    model_config: dict[str, Any]
    training_step: int
    observation_schema: str
    action_schema: str
    matchup_ids: list[str]


def save_checkpoint(
    directory: Path,
    model: PolicyModel,
    optimizer: torch.optim.Optimizer,
    training_step: int,
    matchup_ids: list[str],
) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    checkpoint_path = directory / "checkpoint.pt"
    checkpoint_temporary = directory / "checkpoint.pt.tmp"
    checkpoint_temporary.unlink(missing_ok=True)
    torch.save(
        {
            "model": model.state_dict(),
            "optimizer": optimizer.state_dict(),
            "training_step": training_step,
        },
        checkpoint_temporary,
    )
    checkpoint_temporary.replace(checkpoint_path)
    manifest = CheckpointManifest(
        schema_version="oracle-ai-checkpoint/v1",
        model_family=getattr(model, "model_family", "hashing-v1"),
        model_config=model.export_config(),
        training_step=training_step,
        observation_schema=getattr(
            model,
            "observation_schema",
            "hashing-observation/v1",
        ),
        action_schema="ai-decision/v1",
        matchup_ids=matchup_ids,
    )
    manifest_path = directory / "manifest.json"
    manifest_temporary = directory / "manifest.json.tmp"
    manifest_temporary.write_text(
        json.dumps(asdict(manifest), indent=2, sort_keys=True), encoding="utf-8"
    )
    manifest_temporary.replace(manifest_path)


def load_checkpoint(
    directory: Path,
    device: torch.device,
) -> tuple[PolicyModel, dict[str, Any]]:
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema_version") != "oracle-ai-checkpoint/v1":
        raise ValueError("unsupported checkpoint schema")
    model = build_model(
        {
            "architecture": manifest.get("model_family", "hashing-v1"),
            **manifest["model_config"],
        }
    )
    payload = torch.load(directory / "checkpoint.pt", map_location=device, weights_only=False)
    model.load_state_dict(payload["model"])
    model.to(device)
    return model, payload


def validate_checkpoint(directory: Path) -> None:
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema_version") != "oracle-ai-checkpoint/v1":
        raise ValueError("unsupported checkpoint schema")
    validate_model_config(
        {
            "architecture": manifest.get("model_family", "hashing-v1"),
            **manifest["model_config"],
        }
    )
    if not (directory / "checkpoint.pt").is_file():
        raise ValueError("checkpoint weights are missing")
