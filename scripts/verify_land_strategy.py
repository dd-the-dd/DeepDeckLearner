"""Exercise land targeting against an isolated real Engine, never a user table."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import httpx
from deepdeck_agent import Decision, Game

from deepdeck_examples.engine_targets import compatible_action
from deepdeck_examples.forge_reference import ForgeReferenceAgent
from deepdeck_examples.strategic_graph import STRATEGY_GRAPH_IMPLEMENTATION
from deepdeck_learner.dependencies import engine_binary
from deepdeck_learner.runtime import hidden_process_options

ROOT = Path(__file__).resolve().parents[1]


def definition(name: str, type_line: str, oracle: str, client: httpx.Client) -> dict:
    response = client.post(
        "/oracle/rules",
        json={
            "cardName": name,
            "typeLine": type_line,
            "oracleText": oracle,
            "manaCost": "",
            "faces": [],
        },
    )
    response.raise_for_status()
    return {
        "id": name.lower().replace(" ", "-"),
        "name": name,
        "typeLine": type_line,
        "manaCost": "",
        "rules": response.json()["rules"],
    }


async def exercise(client: httpx.Client) -> dict:
    waste = definition(
        "Wasteland",
        "Land",
        "{T}: Add {C}.\n{T}, Sacrifice Wasteland: Destroy target nonbasic land.",
        client,
    )
    island = definition("Island", "Basic Land — Island", "{T}: Add {U}.", client)
    plains = definition("Plains", "Basic Land — Plains", "{T}: Add {W}.", client)
    sea = definition("Underground Sea", "Land — Island Swamp", "", client)
    utility = definition("Otawara, Soaring City", "Legendary Land", "{T}: Add {U}.", client)
    agent = ForgeReferenceAgent(
        strategy_graph={"archetypeMix": {"tempo": 1}, "deckAdaptation": 0, "temperature": 0}
    )
    for seed in range(1, 21):
        response = client.post(
            "/game/sessions",
            json={
                "setup": {
                    "openingHandSize": 7,
                    "startingPlayer": 0,
                    "players": [
                        {
                            "id": "p1",
                            "name": "Isolated strategy QA",
                            "startingLife": 20,
                            "cards": [
                                *[dict(waste) for _ in range(4)],
                                *[dict(island) for _ in range(56)],
                            ],
                        },
                        {
                            "id": "p2",
                            "name": "Isolated target QA",
                            "startingLife": 20,
                            "cards": [
                                *[dict(sea) for _ in range(4)],
                                *[dict(utility) for _ in range(4)],
                                *[dict(plains) for _ in range(52)],
                            ],
                        },
                    ],
                },
                "seed": seed,
                "gameMode": "legacy",
                "maxTurns": 12,
                "humanPlayerIds": ["p1", "p2"],
                "mulliganEnabled": False,
            },
        )
        response.raise_for_status()
        view = response.json()
        session_id = view["sessionId"]
        selected_target = None
        self_target_avoided = False
        report = None
        try:
            for _ in range(240):
                if view.get("error"):
                    raise RuntimeError(view["error"])
                if selected_target:
                    enemy = next(
                        player for player in view["state"]["players"] if player["id"] == "p2"
                    )
                    if any(card["instanceId"] == selected_target for card in enemy["graveyard"]):
                        assert report is not None
                        report["destroyedByEngine"] = True
                        report["selfTargetAvoided"] = self_target_avoided
                        return report
                raw = view.get("decision")
                if not raw:
                    if (view.get("matchState") or {}).get("phase") == "complete":
                        break
                    await asyncio.sleep(0.01)
                    view = client.get(f"/game/sessions/{session_id}").json()
                    continue
                game = Game(view["state"], raw["playerId"])
                decision = Decision(raw["id"], raw["playerId"], raw, game)
                if raw["kind"] != "priority":
                    action = decision.pass_action or decision.first()
                    assert action is not None
                    action_id = action.id
                elif raw["playerId"] == "p1":
                    result = await agent.make_decision(decision)
                    action_id = result.action_id
                    action = next(item for item in decision.actions if item.id == action_id)
                    source = decision.card_for(action)
                    adapted = compatible_action(action)
                    # Independently inspect all self-target choices using the
                    # Engine's exact nested enum shape, not SDK convenience IDs.
                    self_actions = [
                        item
                        for item in decision.actions
                        if item.kind == "activateAbility"
                        and item.card_instance_id in compatible_action(item).target_permanent_ids
                    ]
                    if self_actions and action_id not in {item.id for item in self_actions}:
                        self_target_avoided = True
                    if (
                        source
                        and source.name == "Wasteland"
                        and action.kind == "activateAbility"
                        and not (action.raw.get("decisions") or {}).get("manaAbility")
                    ):
                        targets = [
                            game.permanent(identifier)
                            for identifier in adapted.target_permanent_ids
                        ]
                        assert targets and all(
                            target is not None and target.controller == "p2" for target in targets
                        ), "Wasteland did not choose an opposing land."
                        selected_target = targets[0].id
                        report = {
                            "implementationRevision": STRATEGY_GRAPH_IMPLEMENTATION,
                            "seed": seed,
                            "source": source.name,
                            "target": targets[0].name,
                            "oldSdkTargetIds": list(action.target_permanent_ids),
                            "adaptedTargetIds": list(adapted.target_permanent_ids),
                            "explanation": agent.last_decision_explanation,
                        }
                else:
                    lands = decision.actions_of("playLand") if not selected_target else []
                    action = next(
                        (
                            item
                            for item in lands
                            if (card := decision.card_for(item)) is not None
                            and not card.is_type("Basic")
                        ),
                        None,
                    )
                    action = action or (
                        lands[0] if lands else decision.pass_action or decision.first()
                    )
                    assert action is not None
                    action_id = action.id
                response = client.post(
                    f"/game/sessions/{session_id}/actions",
                    json={
                        "revision": view["revision"],
                        "decisionId": raw["id"],
                        "actionId": action_id,
                    },
                )
                response.raise_for_status()
                view = response.json()
        finally:
            client.delete(f"/game/sessions/{session_id}")
    raise RuntimeError("No completed land-destruction verification within the bounded seeds.")


def main() -> None:
    base = ROOT / ".deepdeck" / "strategy-evaluations"
    base.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix="land-denial-", dir=base))
    # Stop dotenv discovery here: never load the user's server credentials.
    (scratch / ".env").write_text("", encoding="utf-8")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith(
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
                        raise RuntimeError("The isolated Engine exited during startup.")
                    try:
                        if client.get("/health").is_success:
                            break
                    except httpx.RequestError:
                        pass
                    if time.monotonic() >= deadline:
                        raise TimeoutError("The isolated Engine did not become ready.")
                    time.sleep(0.05)
                report = asyncio.run(exercise(client))
                (scratch / "verification.json").write_text(
                    json.dumps(report, indent=2), encoding="utf-8"
                )
                print(
                    json.dumps(
                        {key: value for key, value in report.items() if key != "explanation"},
                        indent=2,
                    )
                )
                print(f"Report: {scratch / 'verification.json'}")
        finally:
            process.terminate()
            process.wait(timeout=10)


if __name__ == "__main__":
    main()
