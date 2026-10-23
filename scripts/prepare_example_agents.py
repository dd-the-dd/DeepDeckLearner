"""Create editable local starter agents for the ten installed benchmark decks."""

from __future__ import annotations

import json
from pathlib import Path

from benchmark_strategy_graph import BENCHMARK_DECKS

from deepdeck_learner.agent_studio import create_agent, list_agents


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    existing = {agent["name"]: agent for agent in list_agents(root)}
    agents = []
    for deck_id, archetypes in BENCHMARK_DECKS:
        snapshot = root / ".deepdeck" / "decks" / f"{deck_id}.json"
        if not snapshot.is_file():
            continue
        deck = json.loads(snapshot.read_text(encoding="utf-8"))
        name = f"{deck['name']} Pilot"
        agent = existing.get(name)
        if agent is None:
            mix = {
                key: (1 / len(archetypes) if key in archetypes else 0)
                for key in ("aggro", "tempo", "midrange", "control", "combo")
            }
            agent = create_agent(
                root,
                {
                    "name": name,
                    "format": deck["format"],
                    "deckVersionIds": [deck_id],
                    "profile": "aggressive" if archetypes == ("aggro",) else "balanced",
                    "description": f"Editable starter for {deck['name']}. Tune it in Agents.",
                    "strategyGraph": {
                        "archetypeMix": mix,
                        "temperature": 0.05,
                        "deckAdaptation": 0.65,
                    },
                    "revisionReason": "Local starter created for playtesting the installed deck.",
                },
            )
        agents.append({"id": agent["id"], "name": agent["name"], "deckVersionId": deck_id})
    print(json.dumps({"agents": agents}, indent=2))


if __name__ == "__main__":
    main()
