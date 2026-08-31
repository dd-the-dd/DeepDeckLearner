from __future__ import annotations

import math
from collections.abc import Mapping


def plackett_luce_matchmaking_weight(
    stat: Mapping[str, float | int | None],
    *,
    target_ordinal: float | None,
    minimum_games: int,
    random_floor: float,
    rating_scale: float,
    underplayed_strength: float,
    game_prior: float,
) -> float:
    """Weight a deck by rating proximity and lack of prior exposure."""
    ordinal = float(stat.get("ordinal", 0.0) or 0.0)
    games = max(0, int(stat.get("games", 0) or 0))
    exposure = ((minimum_games + game_prior) / (games + game_prior)) ** underplayed_strength
    proximity = (
        1.0
        if target_ordinal is None
        else math.exp(-abs(ordinal - target_ordinal) / rating_scale)
    )
    quality = proximity * exposure
    return max(random_floor + (1.0 - random_floor) * quality, 1e-9)
