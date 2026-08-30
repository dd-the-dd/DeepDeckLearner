from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import torch


@dataclass(frozen=True)
class EncodedDecision:
    state_tokens: torch.Tensor
    action_tokens: torch.Tensor
    state_padding_mask: torch.Tensor


def _stable_bucket(value: str, buckets: int) -> int:
    digest = hashlib.blake2b(value.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "little") % buckets


def _numeric_value(value: int | float | bool) -> float:
    if isinstance(value, bool):
        return float(value)
    value = float(value)
    return value / (abs(value) + 20.0)


def _flatten(value: Any, path: str = "root") -> Iterable[tuple[str, float]]:
    if value is None:
        yield f"{path}=null", 1.0
    elif isinstance(value, (bool, int, float)):
        yield path, _numeric_value(value)
    elif isinstance(value, str):
        yield f"{path}={value}", 1.0
    elif isinstance(value, list):
        yield f"{path}.length", _numeric_value(len(value))
        for index, item in enumerate(value):
            yield from _flatten(item, f"{path}[{index}]")
    elif isinstance(value, dict):
        for key in sorted(value):
            yield from _flatten(value[key], f"{path}.{key}")
    else:
        yield f"{path}={json.dumps(value, default=str, sort_keys=True)}", 1.0


class HashingObservationEncoder:
    """Deterministic V1 encoder for arbitrary versioned engine JSON.

    It is intentionally schema-tolerant so the first trainable loop can run while
    Rust's richer `for_model` representation is implemented. Stable feature hashing
    avoids Python's randomized hash and makes checkpoints reproducible.
    """

    def __init__(self, feature_dim: int = 256, max_state_tokens: int = 512) -> None:
        self.feature_dim = feature_dim
        self.max_state_tokens = max_state_tokens

    def _encode_object(self, value: Any, prefix: str) -> torch.Tensor:
        vector = torch.zeros(self.feature_dim, dtype=torch.float32)
        for key, numeric in _flatten(value, prefix):
            bucket = _stable_bucket(key, self.feature_dim)
            sign = -1.0 if _stable_bucket(f"sign:{key}", 2) else 1.0
            vector[bucket] += sign * numeric
        norm = vector.norm(p=2)
        return vector / norm if norm > 0 else vector

    def _state_objects(self, state: dict[str, Any]) -> list[tuple[str, Any]]:
        objects: list[tuple[str, Any]] = []
        for key in sorted(state):
            value = state[key]
            if isinstance(value, list):
                objects.extend((f"state.{key}[{index}]", item) for index, item in enumerate(value))
            elif isinstance(value, dict):
                objects.extend((f"state.{key}.{child}", value[child]) for child in sorted(value))
            else:
                objects.append((f"state.{key}", value))
        return objects[: self.max_state_tokens] or [("state.empty", {})]

    def encode(self, state: dict[str, Any], actions: list[dict[str, Any]]) -> EncodedDecision:
        state_tokens = torch.stack(
            [self._encode_object(value, path) for path, value in self._state_objects(state)]
        )
        action_tokens = torch.stack(
            [self._encode_object(action, "action") for action in actions]
        )
        padding_mask = torch.zeros(state_tokens.shape[0], dtype=torch.bool)
        return EncodedDecision(state_tokens, action_tokens, padding_mask)
