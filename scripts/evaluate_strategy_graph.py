from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from benchmark_strategy_graph import BENCHMARK_DECKS
from deepdeck_agent import AgentRunner, Decision, DecisionResponse, PlaySpeed, ServerTarget

from deepdeck_examples.configuration import programmatic_config
from deepdeck_examples.forge_reference import ForgeReferenceAgent
from deepdeck_examples.strategic_graph import STRATEGY_GRAPH_IMPLEMENTATION, STRATEGY_GRAPH_VERSION
from deepdeck_learner.dependencies import current_revision, engine_binary, engine_build_current
from deepdeck_learner.jobs import JobManager


class TracedStrategyAgent(ForgeReferenceAgent):
    def __init__(self) -> None:
        super().__init__(
            strategy_graph={
                "version": STRATEGY_GRAPH_VERSION,
                "deckAdaptation": 0.65,
                "temperature": 0.0,
            }
        )
        self.records: list[dict[str, Any]] = []

    async def make_decision(self, decision: Decision) -> DecisionResponse:
        result = await super().make_decision(decision)
        explanation = self.last_decision_explanation or {}
        selected = explanation.get("selected", {})
        alternatives = explanation.get("alternatives", [])
        selected_score = selected.get("score") if isinstance(selected, dict) else None
        runner_up = alternatives[0].get("score") if alternatives else None
        self.records.append(
            {
                "playerId": decision.player_id,
                "turn": decision.game.turn,
                "step": decision.game.step,
                "kind": decision.kind,
                "actionId": result.action_id,
                "score": selected_score,
                "margin": (
                    round(float(selected_score) - float(runner_up), 3)
                    if selected_score is not None and runner_up is not None
                    else None
                ),
            }
        )
        return result


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Play the strategic graph on all ten benchmark decks against Engine random."
    )
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--engine-url", default="http://127.0.0.1:8787")
    parser.add_argument("--games-per-deck", type=int, default=1)
    parser.add_argument("--max-turns", type=int, default=100)
    parser.add_argument("--timeout-seconds", type=float, default=240.0)
    parser.add_argument("--seed", type=int, default=20261017)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def _decision_summary(records: list[dict[str, Any]], player_id: str) -> dict[str, Any]:
    selected = [record for record in records if record["playerId"] == player_id]
    kinds = Counter(str(record["kind"]) for record in selected)
    action_prefixes = Counter(str(record["actionId"]).split(":", 1)[0] for record in selected)
    margins = [float(record["margin"]) for record in selected if record["margin"] is not None]
    return {
        "decisions": len(selected),
        "byKind": dict(sorted(kinds.items())),
        "byActionPrefix": dict(sorted(action_prefixes.items())),
        "averageScoreMargin": round(sum(margins) / len(margins), 3) if margins else None,
        "closeDecisions": sum(margin < 15 for margin in margins),
    }


async def _wait_for_match(
    client: httpx.AsyncClient,
    engine_url: str,
    session_id: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        response = await client.get(f"{engine_url}/game/sessions/{session_id}")
        response.raise_for_status()
        snapshot = response.json()
        state = snapshot.get("state", {})
        match_state = snapshot.get("matchState", {})
        if match_state.get("phase") == "complete" or state.get("status") in {
            "completed",
            "failed",
            "turnLimitReached",
            "cancelled",
        }:
            return snapshot
        await asyncio.sleep(0.25)
    with contextlib.suppress(httpx.HTTPError):
        await client.delete(f"{engine_url}/game/sessions/{session_id}")
    raise TimeoutError(f"Engine match {session_id} exceeded {timeout_seconds:g} seconds")


async def _run(arguments: argparse.Namespace) -> dict[str, Any]:
    root = arguments.root.resolve()
    engine_url = arguments.engine_url.rstrip("/")
    deck_ids = [item[0] for item in BENCHMARK_DECKS]
    manager = JobManager(root)
    decks: dict[str, tuple[str, list[dict[str, Any]]]] = {}
    for version_id in deck_ids:
        name, cards = manager._cached_playtest_deck(version_id, "legacy")  # noqa: SLF001
        cards = manager._refresh_playtest_deck_rules(  # noqa: SLF001
            engine_url, version_id, cards
        )
        decks[version_id] = (name, cards)

    policy = {
        "id": "com.deepdeck.strategy-graph.benchmark",
        "name": "Strategy graph benchmark",
        # Free uses the same rules execution for this evaluation but runs one
        # game instead of a full Legacy best-of-three match.
        "format": "free",
        "description": "Versioned strategic graph Engine evaluation.",
        "deckVersionIds": deck_ids,
        "revision": 1,
    }
    agent = TracedStrategyAgent()
    target = ServerTarget.local(engine_url)
    runner = AgentRunner(agent, programmatic_config(policy), target, speed=PlaySpeed.SECOND_1)
    serving = asyncio.create_task(runner.serve())
    results: list[dict[str, Any]] = []
    try:
        controller_id = await runner.wait_until_connected(timeout=30)
        headers = {"x-mtg-api-key": target.engine_api_key} if target.engine_api_key else None
        async with httpx.AsyncClient(timeout=30, headers=headers) as client:
            for deck_index, deck_id in enumerate(deck_ids):
                opponent_id = deck_ids[(deck_index + 1) % len(deck_ids)]
                deck_name, cards = decks[deck_id]
                opponent_name, opponent_cards = decks[opponent_id]
                for repetition in range(max(1, arguments.games_per_deck)):
                    sequence = deck_index * max(1, arguments.games_per_deck) + repetition
                    player_id = f"strategy-{sequence + 1}"
                    opponent_player_id = f"random-{sequence + 1}"
                    seed = arguments.seed + sequence
                    payload = {
                        "setup": {
                            "openingHandSize": 7,
                            "startingPlayer": sequence % 2,
                            "players": [
                                {
                                    "id": player_id,
                                    "name": deck_name,
                                    "startingLife": 20,
                                    "cards": cards,
                                },
                                {
                                    "id": opponent_player_id,
                                    "name": opponent_name,
                                    "startingLife": 20,
                                    "cards": opponent_cards,
                                },
                            ],
                        },
                        "seed": seed,
                        "gameMode": "free",
                        "maxTurns": max(20, arguments.max_turns),
                        "humanPlayerIds": [],
                        "aiControllerByPlayerId": {player_id: controller_id},
                        "analyticsDeckSessionByPlayerId": {
                            player_id: deck_id,
                            opponent_player_id: opponent_id,
                        },
                        "mulliganEnabled": True,
                        "freeMulligans": 0,
                        "waitTimeoutMs": 60_000,
                    }
                    response = await client.post(f"{engine_url}/game/sessions", json=payload)
                    response.raise_for_status()
                    bootstrap = response.json()
                    session_id = str(bootstrap.get("sessionId", ""))
                    snapshot = await _wait_for_match(
                        client,
                        engine_url,
                        session_id,
                        arguments.timeout_seconds,
                    )
                    match_state = snapshot.get("matchState", {})
                    state = snapshot.get("state", {})
                    outcome = state.get("outcome")
                    if not isinstance(outcome, dict):
                        outcome = {}
                    winner_player_id = match_state.get("winnerPlayerId") or outcome.get("winner")
                    wins = match_state.get("winsByPlayerId") or {
                        player_id: int(winner_player_id == player_id),
                        opponent_player_id: int(winner_player_id == opponent_player_id),
                    }
                    result = {
                        "deckVersionId": deck_id,
                        "deckName": deck_name,
                        "opponentDeckVersionId": opponent_id,
                        "opponentDeckName": opponent_name,
                        "seed": seed,
                        "sessionId": session_id,
                        "status": state.get("status"),
                        "winnerPlayerId": winner_player_id,
                        "won": winner_player_id == player_id,
                        "wins": wins,
                        "lastGameTurns": state.get("turnNumber"),
                        "lastGameOutcome": state.get("outcome"),
                        "error": snapshot.get("error"),
                        "decisions": _decision_summary(agent.records, player_id),
                    }
                    results.append(result)
                    outcome_label = (
                        "draw" if winner_player_id is None else "win" if result["won"] else "loss"
                    )
                    print(
                        f"[{len(results)}/{len(deck_ids) * max(1, arguments.games_per_deck)}] "
                        f"{deck_name}: {outcome_label} "
                        f"{result['wins']} ({session_id})",
                        flush=True,
                    )
    finally:
        serving.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await serving

    completed = [
        result for result in results if result["status"] in {"completed", "turnLimitReached"}
    ]
    wins = sum(bool(result["won"]) for result in completed)
    draws = sum(result["winnerPlayerId"] is None for result in completed)
    binary = engine_binary(root)
    binary_hash = hashlib.sha256(binary.read_bytes()).hexdigest() if binary.is_file() else None
    return {
        "schemaVersion": "deepdeck-strategy-engine-evaluation/v1",
        "modelVersion": STRATEGY_GRAPH_VERSION,
        "implementationRevision": STRATEGY_GRAPH_IMPLEMENTATION,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "engineUrl": engine_url,
        "engine": {
            "sourceRevision": current_revision(root, "engine"),
            "binaryPath": str(binary),
            "binarySha256": binary_hash,
            "binaryMatchesSource": engine_build_current(root),
        },
        "summary": {
            "requested": len(deck_ids) * max(1, arguments.games_per_deck),
            "completed": len(completed),
            "wins": wins,
            "draws": draws,
            "winRate": round(wins / len(completed), 4) if completed else 0.0,
        },
        "matches": results,
    }


def main() -> int:
    arguments = _arguments()
    report = asyncio.run(_run(arguments))
    output = arguments.output
    if output is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        output = arguments.root / ".deepdeck" / "strategy-evaluations" / f"{stamp}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), **report["summary"]}, sort_keys=True), flush=True)
    return 0 if report["summary"]["completed"] == report["summary"]["requested"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
