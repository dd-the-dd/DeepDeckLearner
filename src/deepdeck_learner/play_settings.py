"""The supported local-session controls, separate from League time controls."""

from __future__ import annotations

import secrets
from typing import Any

MAX_SEED = 2**31 - 1


def _integer(value: Any, label: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{label} must be a whole number between {minimum} and {maximum}.")
    return int(value)


def local_play_settings(payload: dict[str, Any], *, seats: int, has_human: bool) -> dict[str, Any]:
    seed = payload.get("seed")
    if seed is None:
        seed = secrets.randbelow(MAX_SEED + 1)
    seed = _integer(seed, "Shuffle seed", 0, MAX_SEED)
    ai_speed = payload.get("ai_speed", "1ms")
    if ai_speed not in ("1ms", "10ms", "100ms", "1s", "10s"):
        raise ValueError("Choose an AI rhythm of 1ms, 10ms or 100ms.")
    starting = payload.get("starting_seat", "rotate")
    if starting not in ("rotate", "random"):
        starting = _integer(starting, "Opening seat", 1, seats)
    idle = payload.get("human_decision_timeout_seconds")
    if idle is not None:
        idle = _integer(idle, "Human inactivity limit in seconds", 5, 600)
        if not has_human:
            raise ValueError("A human inactivity limit needs a human seat.")
    limit = payload.get("match_timeout_seconds", None if has_human else 600)
    if limit is not None:
        limit = _integer(limit, "Match safety limit in seconds", 30, 43_200)
    elif not has_human:
        raise ValueError("AI-only matches need a match safety limit.")
    return {
        "version": "deepdeck-local-play/v1",
        "firstSeed": seed,
        "seedSource": "random" if payload.get("seed") is None else "explicit",
        "startingSeat": starting,
        "aiSpeed": ai_speed,
        "maxTurns": _integer(payload.get("max_turns", 60), "Turn limit", 20, 2_000),
        "humanDecisionTimeoutSeconds": idle,
        "matchTimeoutSeconds": limit,
    }


def game_seed(spec: dict[str, Any], game_index: int) -> int:
    # Existing v1 batch files used base+index. New specs make the first seed
    # explicit so a user-entered seed is the actual first game's shuffle seed.
    first = int(spec.get("firstSeed", int(spec["seed"]) + 1))
    return first + game_index - 1
