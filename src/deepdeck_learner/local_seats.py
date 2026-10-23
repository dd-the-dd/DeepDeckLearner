"""Stable Engine seat identities shared by play, recordings and competitions."""

from __future__ import annotations

import uuid
from collections.abc import Iterable

LOCAL_TABLE_REPLAYS = "local-tables"


def competition_player_id(competition_id: str, entrant_id: str) -> str:
    """Local analytics identity, distinct from transient Engine seats and accounts."""
    identity = uuid.uuid5(
        uuid.NAMESPACE_URL, f"deepdeck-local-player:{competition_id}:{entrant_id}"
    )
    return f"local-player-{identity.hex}"


def local_player_ids(kinds: Iterable[str]) -> list[str]:
    """Preserve the historical first-human ID without aliasing later humans."""
    human_seen = False
    players = []
    for index, kind in enumerate(kinds, 1):
        if kind == "human":
            players.append(f"local-human-{index}" if human_seen else "local-human")
            human_seen = True
        else:
            players.append(f"batch-seat-{index}")
    return players
