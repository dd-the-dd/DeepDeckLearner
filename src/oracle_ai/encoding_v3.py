from __future__ import annotations

import math
from typing import Any

from oracle_ai.encoding_v2 import StructuredObservationEncoder

DEFAULT_PLAYER_ANGLE_STEPS = 840
MINIMUM_CYCLIC_MULTIPLAYER_SLOTS = 4


def cyclic_player_coordinates(
    position: int,
    angle_steps: int = DEFAULT_PLAYER_ANGLE_STEPS,
) -> tuple[float, float]:
    angle = 2.0 * math.pi * position / angle_steps
    return math.sin(angle), math.cos(angle)


class CyclicStructuredObservationEncoder(StructuredObservationEncoder):
    def __init__(
        self,
        *,
        word_vocab_size: int = 32768,
        max_words: int = 32,
        max_relative_players: int = 8,
        max_state_tokens: int = 512,
        player_angle_steps: int = DEFAULT_PLAYER_ANGLE_STEPS,
    ) -> None:
        super().__init__(
            word_vocab_size=word_vocab_size,
            max_words=max_words,
            max_relative_players=max_relative_players,
            max_state_tokens=max_state_tokens,
        )
        if player_angle_steps <= 0:
            raise ValueError("player_angle_steps must be positive")
        self.player_angle_steps = player_angle_steps
        self.no_player_position = player_angle_steps

    def _decision_player_positions(
        self,
        state: dict[str, Any],
        players: list[dict[str, Any]],
        acting_player_id: str | None,
    ) -> dict[str, int]:
        player_count = len(players)
        if player_count == 0:
            return {}
        # Cyclic coordinates do not use the discrete relative-player embedding.
        # Older two-player checkpoints can therefore represent all four engine
        # seats without changing checkpoint tensor shapes. Their policy remains
        # zero-shot for multiplayer, but every opponent stays distinguishable.
        supported_players = max(
            self.max_relative_players,
            MINIMUM_CYCLIC_MULTIPLAYER_SLOTS,
        )
        if player_count > supported_players:
            raise ValueError(
                f"cyclic player encoding supports at most "
                f"{supported_players} players"
            )
        if self.player_angle_steps % player_count != 0:
            raise ValueError(
                f"player_angle_steps {self.player_angle_steps} must be divisible "
                f"by player count {player_count}"
            )
        acting_player_index = next(
            (
                index
                for index, player in enumerate(players)
                if str(player.get("id", "")) == acting_player_id
            ),
            int(state.get("activePlayer", 0) or 0) % player_count,
        )
        angle_step = self.player_angle_steps // player_count
        return {
            str(player.get("id")): (
                (index - acting_player_index) % player_count
            )
            * angle_step
            for index, player in enumerate(players)
        }
