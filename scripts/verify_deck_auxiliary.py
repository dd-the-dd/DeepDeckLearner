"""Revalidate a failed local setup without creating or restarting any match."""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path

import httpx

from deepdeck_learner.card_kinds import auxiliary_flags
from deepdeck_learner.jobs import is_loopback_url


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--engine-url", default="http://127.0.0.1:8787")
    arguments = parser.parse_args()
    if not is_loopback_url(arguments.engine_url):
        raise ValueError("Only a local Engine can validate a private playtest setup.")
    spec = json.loads(arguments.spec.read_text(encoding="utf-8"))
    players = [
        {
            "id": f"seat-{index + 1}",
            "name": seat["name"],
            "startingLife": 20 if spec["format"] == "legacy" else 40,
            "cards": seat["decks"][0]["cards"],
        }
        for index, seat in enumerate(spec["seats"])
    ]
    payload = {
        "gameMode": spec["format"],
        "setup": {"openingHandSize": 7, "startingPlayer": 0, "players": players},
    }
    with httpx.Client(base_url=arguments.engine_url, timeout=30) as client:
        response = client.post("/game/setups/validate", json=payload)
        response.raise_for_status()
        before = response.json()
        repaired = deepcopy(payload)
        counts = []
        for player in repaired["setup"]["players"]:
            for card in player["cards"]:
                card["isToken"], card["isGamePiece"] = auxiliary_flags(card)
            counts.append(
                {
                    "pilot": player["name"],
                    "main": sum(
                        not card["isGamePiece"] and not card.get("isSideboard")
                        for card in player["cards"]
                    ),
                    "sideboard": sum(
                        not card["isGamePiece"] and bool(card.get("isSideboard"))
                        for card in player["cards"]
                    ),
                    "auxiliary": sum(card["isGamePiece"] for card in player["cards"]),
                }
            )
        response = client.post("/game/setups/validate", json=repaired)
        response.raise_for_status()
        after = response.json()
    print(
        json.dumps(
            {"before": before, "after": after, "counts": counts, "matchesStarted": 0},
            ensure_ascii=False,
            indent=2,
        )
    )
    if not after.get("valid"):
        raise RuntimeError("The setup still has Engine validation violations.")


if __name__ == "__main__":
    main()
