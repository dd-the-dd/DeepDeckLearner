"""Entity labels and launch-time selection, separate from card strategy tags."""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any


def labels(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 30:
        raise ValueError("Tags and collections must be lists of at most 30 labels.")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not 1 <= len(item.strip()) <= 64:
            raise ValueError("Each tag or collection must contain 1 to 64 characters.")
        label = item.strip().casefold()
        if label not in result:
            result.append(label)
    return result


def matches(entity: dict[str, Any], seat: dict[str, Any], kind: str) -> bool:
    return all(
        set(labels(seat.get(f"{kind}_{field}"))).issubset(labels(entity.get(field)))
        for field in ("tags", "collections")
    )


def deck_catalog(root: Path, game_format: str) -> dict[str, dict[str, Any]]:
    result = {}
    for path in sorted((root / ".deepdeck" / "decks").glob("*.json")):
        try:
            deck = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(deck, dict) and deck.get("format") == game_format:
                result[path.stem] = deck
        except (OSError, ValueError):
            continue
    return result


def resolve_seats(
    root: Path, seats: list[dict[str, Any]], game_format: str, seed: int
) -> list[dict[str, Any]]:
    """Freeze candidates before execution; never widen an empty filtered pool."""
    from .agent_studio import list_agents

    rng = random.Random(seed)
    decks = deck_catalog(root, game_format)
    agents = sorted(list_agents(root), key=lambda agent: agent["id"])
    resolved = []
    for index, source in enumerate(seats, 1):
        if not isinstance(source, dict):
            raise ValueError(f"Seat {index} is invalid.")
        seat = dict(source)
        deck_id = str(seat.get("deck_id", "random")) or "random"

        def eligible_decks(
            agent: dict[str, Any] | None,
            deck_id: str = deck_id,
            seat: dict[str, Any] = seat,
        ) -> list[str]:
            mastered = set(agent.get("deckVersionIds", [])) if agent else set(decks)
            return sorted(
                key
                for key, deck in decks.items()
                if key in mastered
                and (deck_id == "random" or key == deck_id)
                and matches(deck, seat, "deck")
            )

        if seat.get("kind") == "human":
            human_candidates = eligible_decks(None)
            if not human_candidates:
                raise ValueError(
                    f"Seat {index}: no local/imported deck matches the selected tags or collection."
                )
            seat["deck_id"] = rng.choice(human_candidates) if deck_id == "random" else deck_id
            seat["_selection"] = {"deckCandidates": human_candidates, "requestedDeck": deck_id}
        else:
            agent_id = str(seat.get("agent_id", ""))
            candidates = [
                agent
                for agent in agents
                if agent.get("format") == game_format
                and (agent_id == "random" or agent["id"] == agent_id)
                and matches(agent, seat, "agent")
                and eligible_decks(agent)
            ]
            if not candidates:
                raise ValueError(
                    f"Seat {index}: no agent and mastered deck match the selected tags or collection."
                )
            agent = rng.choice(candidates) if agent_id == "random" else candidates[0]
            seat["agent_id"] = agent["id"]
            seat["_deck_candidates"] = eligible_decks(agent)
            seat["_selection"] = {
                "agentCandidates": [item["id"] for item in candidates],
                "deckCandidates": seat["_deck_candidates"],
                "requestedAgent": agent_id,
                "requestedDeck": deck_id,
            }
            if agent_id == "random":
                seat["_agent_candidates"] = [
                    {"agent_id": item["id"], "deck_ids": eligible_decks(item)}
                    for item in candidates
                ]
        resolved.append(seat)
    return resolved
