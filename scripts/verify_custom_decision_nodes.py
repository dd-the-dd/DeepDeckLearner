"""Verify custom scores against actual Engine legal actions in an isolated game.

No account, LLM, user policy, shared Engine or match statistics are touched.
This checks execution, not playing strength.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import httpx
from deepdeck_agent import Decision, Game

from deepdeck_examples.forge_reference import ForgeReferenceAgent
from deepdeck_examples.strategic_graph import StrategyPolicy, WeightedDecisionGraph
from deepdeck_learner.dependencies import engine_binary
from deepdeck_learner.runtime import hidden_process_options

ROOT = Path(__file__).resolve().parents[1]


def compile_card(client: httpx.Client, name: str, type_line: str, text: str, cost: str) -> dict:
    response = client.post(
        "/oracle/rules",
        json={
            "cardName": name,
            "typeLine": type_line,
            "oracleText": text,
            "manaCost": cost,
            "faces": [],
        },
    )
    response.raise_for_status()
    return {
        "id": name.casefold(),
        "name": name,
        "typeLine": type_line,
        "oracleText": text,
        "manaCost": cost,
        "rules": response.json()["rules"],
    }


async def exercise(client: httpx.Client) -> dict:
    island = compile_card(client, "Island", "Basic Land — Island", "{T}: Add {U}.", "")
    brainstorm = compile_card(
        client,
        "Brainstorm",
        "Instant",
        "Draw three cards, then put two cards from your hand on top of your library in any order.",
        "{U}",
    )
    graph_spec = {
        "deckAdaptation": 0,
        "customNodes": [
            {
                "id": "our-turn-draw",
                "name": "Play our draw spell on our turn",
                "bias": 0,
                "when": {
                    "actionKinds": ["castSpell"],
                    "sourceCards": ["Brainstorm"],
                    "sourceTags": ["card_draw"],
                    "turn": "ours",
                },
                "inputs": [{"from": "state.our_turn", "weight": 0.5}],
            }
        ],
    }
    baseline = WeightedDecisionGraph(StrategyPolicy.from_mapping({"deckAdaptation": 0}))
    custom = WeightedDecisionGraph(StrategyPolicy.from_mapping(graph_spec))
    agent = ForgeReferenceAgent(strategy_graph=graph_spec)
    for seed in range(1, 21):
        response = client.post(
            "/game/sessions",
            json={
                "setup": {
                    "openingHandSize": 7,
                    "startingPlayer": 0,
                    "players": [
                        {
                            "id": p,
                            "name": p,
                            "startingLife": 20,
                            "cards": [dict(brainstorm) for _ in range(4)]
                            + [dict(island) for _ in range(56)],
                        }
                        for p in ("p1", "p2")
                    ],
                },
                "seed": seed,
                "gameMode": "legacy",
                "maxTurns": 4,
                "humanPlayerIds": ["p1", "p2"],
                "mulliganEnabled": False,
                "recordAnalytics": False,
            },
        )
        response.raise_for_status()
        view = response.json()
        key = view["sessionId"]
        try:
            deadline = time.monotonic() + 30
            for _ in range(100):
                if time.monotonic() > deadline:
                    raise TimeoutError("Isolated custom-node check exceeded its deadline.")
                if view.get("error"):
                    raise RuntimeError(view["error"])
                raw = view.get("decision")
                if not raw:
                    if view.get("status") in {
                        "complete",
                        "completed",
                        "gameOver",
                        "turnLimitReached",
                    }:
                        break
                    await asyncio.sleep(0.01)
                    view = client.get(f"/game/sessions/{key}").json()
                    continue
                d = Decision(raw["id"], raw["playerId"], raw, Game(view["state"], raw["playerId"]))
                proof = None
                if raw["kind"] == "priority" and raw["playerId"] == "p1":
                    selected = await agent.make_decision(d)
                    action = next(a for a in d.actions if a.id == selected.action_id)
                    if (
                        action.kind == "castSpell"
                        and (source := d.card_for(action))
                        and source.name == "Brainstorm"
                    ):
                        a, b = baseline.score_priority(d, action), custom.score_priority(d, action)
                        contribution = next(
                            c for c in b.contributions if c.node == "custom.our-turn-draw"
                        )
                        assert (
                            math.isclose(b.total - a.total, 50, abs_tol=1e-8)
                            and contribution.score == 50
                        ), (a.total, b.total, contribution)
                        proof = {
                            "seed": seed,
                            "action": action.kind,
                            "card": source.name,
                            "scoreDelta": 50,
                            "contribution": {
                                "node": contribution.node,
                                "raw": contribution.raw,
                                "weight": contribution.weight,
                                "score": contribution.score,
                            },
                            "analyticsDisabled": view.get("analyticsRecording") is False,
                        }
                else:
                    action = d.pass_action or d.first()
                    if action is None:
                        break
                response = client.post(
                    f"/game/sessions/{key}/actions",
                    json={
                        "revision": view["revision"],
                        "decisionId": raw["id"],
                        "actionId": action.id,
                    },
                )
                response.raise_for_status()
                view = response.json()
                if proof:
                    # Actions are queued by the HTTP boundary. Wait for the
                    # worker's observation, not only its initial acknowledgement.
                    applied_deadline = time.monotonic() + 5
                    draw_observed = False
                    while True:
                        assert not view.get("error"), view.get("error")
                        state = view["state"]
                        player = next(p for p in state["players"] if p["id"] == "p1")
                        on_stack = any(
                            (item.get("card") or {}).get("instanceId") == source.id
                            for item in state["stack"]
                        )
                        in_graveyard = any(
                            item.get("instanceId") == source.id for item in player["graveyard"]
                        )
                        if in_graveyard:
                            return {
                                **proof,
                                "acceptedByEngine": True,
                                "spellOnStack": on_stack,
                                "spellInGraveyard": in_graveyard,
                                "drawThreeObserved": draw_observed,
                            }
                        pending = view.get("decision") or {}
                        if (
                            pending.get("kind") == "resolutionChoice"
                            and pending.get("sourceCardInstanceId") == source.id
                        ):
                            if not draw_observed:
                                assert len(player["hand"]) == len(d.game.me.hand) + 2
                                draw_observed = True
                            followup = Decision(
                                pending["id"],
                                pending["playerId"],
                                pending,
                                Game(state, pending["playerId"]),
                            )
                            selected = await agent.make_decision(followup)
                            # HTTP requires a legal action envelope even when
                            # the SDK's typed card selection carries the choice.
                            payload = selected.model_dump(by_alias=True, exclude_none=True)
                            payload.setdefault("actionId", followup.actions[0].id)
                            response = client.post(
                                f"/game/sessions/{key}/actions",
                                json={
                                    "revision": view["revision"],
                                    "decisionId": pending["id"],
                                    **payload,
                                },
                            )
                            response.raise_for_status()
                            view = response.json()
                            continue
                        if time.monotonic() > applied_deadline:
                            raise TimeoutError(
                                f"The scored spell did not finish resolving: {source.id}"
                            )
                        await asyncio.sleep(0.02)
                        response = client.get(f"/game/sessions/{key}")
                        response.raise_for_status()
                        view = response.json()
        finally:
            client.delete(f"/game/sessions/{key}").raise_for_status()
    raise RuntimeError("No verified draw-spell action within the bounded seeds.")


def main() -> None:
    scratch = Path(tempfile.mkdtemp(prefix="deepdeck-custom-node-qa-"))
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    # Explicit cwd and dotenv boundary; no inherited account/provider/database settings.
    (scratch / ".env").touch()
    environment = {
        k: v
        for k, v in os.environ.items()
        if not k.upper().startswith(
            ("MTG_", "DDL_", "DEEPDECK_", "DATABASE", "PG", "OPENAI_", "ANTHROPIC_")
        )
    }
    environment.update(MTG_ENGINE_ADDR=f"127.0.0.1:{port}", MTG_UI_ADDR="")
    with (scratch / "engine.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [str(engine_binary(ROOT))],
            cwd=scratch,
            env=environment,
            stdout=log,
            stderr=log,
            **hidden_process_options(),
        )
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=15) as client:
                deadline = time.monotonic() + 20
                while True:
                    if process.poll() is not None:
                        raise RuntimeError("Isolated Engine exited.")
                    try:
                        if client.get("/health").is_success:
                            break
                    except httpx.RequestError:
                        pass
                    if time.monotonic() > deadline:
                        raise TimeoutError("Engine did not start.")
                    time.sleep(0.05)
                report = asyncio.run(exercise(client))
                (scratch / "verification.json").write_text(
                    json.dumps(report, indent=2), encoding="utf-8"
                )
                print(json.dumps(report, indent=2))
                print(f"Report: {scratch / 'verification.json'}")
        finally:
            process.terminate()
            process.wait(timeout=10)


if __name__ == "__main__":
    main()
