from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, cast

import torch

from .models import ModelConfig, PolicyV11, build_model

CHECKPOINT_SCHEMA = "deepdeck-example-policy/v1"


def save_checkpoint(directory: str | Path, model: PolicyV11) -> Path:
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    metadata = {
        "schema": CHECKPOINT_SCHEMA,
        "family": model.family,
        "config": asdict(model.config),
    }
    (target / "config.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    torch.save(model.state_dict(), target / "model.pt")
    return target


def load_checkpoint(
    directory: str | Path,
    *,
    device: torch.device | str = "cpu",
) -> PolicyV11:
    source = Path(directory)
    metadata = cast(dict[str, Any], json.loads((source / "config.json").read_text("utf-8")))
    if metadata.get("schema") != CHECKPOINT_SCHEMA:
        raise ValueError("unsupported checkpoint schema")
    family = str(metadata.get("family", ""))
    version = {"example-v11": "v11", "example-v12": "v12"}.get(family)
    if version is None:
        raise ValueError(f"unsupported model family: {family}")
    raw_config = metadata.get("config", {})
    if not isinstance(raw_config, dict):
        raise ValueError("checkpoint config must be an object")
    model = build_model(version, ModelConfig(**raw_config))
    model.to(device)
    # weights_only prevents a checkpoint from constructing arbitrary Python objects.
    state = torch.load(source / "model.pt", map_location=device, weights_only=True)
    model.load_state_dict(state)
    return model
