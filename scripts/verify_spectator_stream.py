"""Verify real local Engine spectator capture without touching the user's profile.

Creates two tiny AI-only tables and temporary recordings; no LLM or League call.
Run against the already-running local Engine. It never restarts the Engine.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from deepdeck_agent import Agent, AgentRunner, Game, PlaySpeed, ServerTarget

from deepdeck_examples.configuration import random_config
from deepdeck_examples.local_batch import DecisionRecorder, RecordingAgent, _one_game


class PrivacyProbe(Agent):
    def __init__(self) -> None:
        self.observations = 0
        self.leaks = 0

    async def on_observation(self, game: Game) -> None:
        self.observations += 1
        for player in game.raw.get("players", []):
            if player.get("id") != game.player_id:
                self.leaks += sum(
                    card.get("definition", {}).get("name") == "Wastes"
                    for card in player.get("hand", [])
                    if isinstance(card, dict)
                )


async def verify(engine_url: str, root: Path) -> None:
    seats: list[dict[str, Any]] = []
    for name in ("observer-a", "observer-b"):
        cards = [
            {
                "id": f"{name}-{index}",
                "name": "Wastes",
                "typeLine": "Basic Land",
                "manaCost": "",
                "rules": [],
                "isSideboard": index >= 60,
            }
            for index in range(62)
        ]
        seats.append(
            {
                "kind": "agent",
                "agentId": name,
                "name": name,
                "decks": [{"id": name, "name": name, "cards": cards}],
            }
        )
    recorder = DecisionRecorder()
    probes = {seat["agentId"]: PrivacyProbe() for seat in seats}
    runners = {
        key: AgentRunner(
            agent=RecordingAgent(probe, recorder),
            config=replace(random_config(), agent_id=f"com.deepdeckleague.qa.{root.name}.{key}"),
            target=ServerTarget.local(engine_url),
            speed=PlaySpeed.MS_100,
        )
        for key, probe in probes.items()
    }
    tasks = [asyncio.create_task(runner.serve()) for runner in runners.values()]
    try:
        await asyncio.gather(
            *(runner.wait_until_connected(timeout=10) for runner in runners.values())
        )
        await verify_matches(engine_url, root, seats, runners, recorder)
        assert all(probe.observations > 0 and probe.leaks == 0 for probe in probes.values())
        assert recorder.summary()["handlerErrors"] == 0
        print("SDK observations verified: opponents' hands stayed hidden.", flush=True)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def verify_matches(
    engine_url: str,
    root: Path,
    seats: list[dict[str, Any]],
    runners: dict[str, AgentRunner],
    recorder: DecisionRecorder,
) -> None:
    async with httpx.AsyncClient(base_url=engine_url, timeout=10) as client:
        for index in (1, 2):
            result = await asyncio.wait_for(
                _one_game(
                    client,
                    root,
                    "spectator-contract",
                    index,
                    {
                        "format": "legacy",
                        "seed": 1940,
                        "seats": seats,
                        "maxTurns": 2,
                        "gameTimeoutSeconds": 15,
                    },
                    runners,
                    random.Random(1940),
                    recorder,
                ),
                timeout=25,
            )
            path = root / ".deepdeck" / "agents" / "observer-a" / "replays"
            replay = json.loads((path / f"{result['replayId']}.json").read_text("utf-8"))
            assert replay["metadata"]["spectatorCapture"] == "all-hands", replay["metadata"]
            assert len(replay["frames"]) > 3, "The match lost its intermediate decisions."
            assert any(frame["state"].get("events") for frame in replay["frames"]), (
                "Visual/audio events were lost during recording."
            )
            assert any(frame.get("agentDecision") for frame in replay["frames"]), (
                "The spectator recording lost SDK decision diagnostics."
            )
            sequences = [frame["spectatorSequence"] for frame in replay["frames"]]
            assert sequences == sorted(set(sequences)), "Duplicate/out-of-order spectator frames."
            first = replay["frames"][0]["state"]["players"]
            assert len(first) == 2 and all(player["hand"] for player in first)
            assert all(card.get("name") == "Wastes" for p in first for card in p["hand"])
            for frame in replay["frames"]:
                for player in frame["state"]["players"]:
                    for zone in ("library", "sideboard"):
                        assert all(card is None for card in player.get(zone, [])), (
                            f"A spectator frame exposed {zone} identities: "
                            f"{frame['spectatorSequence']}"
                        )
            # The batch owns and retires only its own Engine session after saving.
            assert (await client.get(f"/game/sessions/{result['sessionId']}")).status_code == 404
            print(
                json.dumps(
                    {
                        "match": index,
                        "frames": len(sequences),
                        "bothHandsRecorded": True,
                        "privateZonesConcealed": True,
                        "sessionReleased": True,
                    }
                ),
                flush=True,
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine-url", default="http://127.0.0.1:8787")
    args = parser.parse_args()
    parsed = urlparse(args.engine_url)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username
        or parsed.password
    ):
        parser.error("Only an explicit local Engine URL is allowed.")
    scratch = (Path(__file__).resolve().parents[1] / ".deepdeck" / "validation").resolve()
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="spectator-", dir=scratch) as directory:
        root = Path(directory).resolve()
        # Validate this fresh task-owned directory before TemporaryDirectory cleanup.
        assert root.parent == scratch and root.name.startswith("spectator-")
        asyncio.run(verify(args.engine_url, root))


if __name__ == "__main__":
    main()
