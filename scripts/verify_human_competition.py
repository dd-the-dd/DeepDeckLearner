"""Real multi-human competition protocol/recovery check in a fresh source profile.

The opening hands are exercised through Pixi in Edge. After an intentional stop,
the current SDK graph acts as an automated test driver through the HUMAN HTTP
action endpoint to complete real Engine matches. Results are not fixtures or an
AI-strength benchmark; no League deployment, LLM or user profile is changed.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import verify_player_app as browser
from deepdeck_agent import Decision, Game
from verify_desktop import wait_instance
from verify_hotseat import check_browser

from deepdeck_examples.forge_reference import ForgeReferenceAgent
from deepdeck_examples.local_batch import _terminal
from deepdeck_learner.runtime import configure_local_http_loop

ROOT = Path(__file__).resolve().parents[1]
ENGINE = "http://127.0.0.1:8787"


async def drive_human_actions(
    session_id: str,
    *,
    seconds: int = 600,
    human_ids: set[str] | None = None,
    action_timeout_seconds: float = 30,
) -> dict:
    agents = {}
    combat_states = {}
    submitted = None
    actions = 0
    deadline = time.monotonic() + seconds
    async with httpx.AsyncClient(base_url=ENGINE, timeout=30, trust_env=False) as engine:
        while time.monotonic() < deadline and actions < 8000:
            response = await engine.get(f"/game/sessions/{session_id}")
            response.raise_for_status()
            view = response.json()
            if view.get("error"):
                raise RuntimeError(f"Owned Engine test failed: {view['error']}")
            if _terminal(view):
                return {"actions": actions, "view": view}
            choice = view.get("decision")
            if not choice:
                await asyncio.sleep(0.03)
                continue
            key = (choice["id"], view["revision"])
            if key == submitted:
                # Observation only: never repeat an accepted mutation after a slow update.
                await asyncio.sleep(0.03)
                continue
            actor = choice["playerId"]
            if human_ids is not None and actor not in human_ids:
                # Mixed tables belong to their registered SDK agents on AI seats.
                await asyncio.sleep(0.03)
                continue
            agent = agents.setdefault(actor, ForgeReferenceAgent("balanced"))
            answer = await agent.make_decision(
                Decision(choice["id"], actor, choice, Game(view["state"], actor))
            )
            body = answer.model_dump(by_alias=True, exclude_none=True)
            if not body.get("actionId"):
                body["actionId"] = choice["options"][0]["id"]
            if choice["kind"] in {"attackers", "blockers"}:
                # The human interface toggles individual creatures then commits;
                # these are not the SDK's whole-selection combat callbacks.
                signature = json.dumps(
                    [
                        choice["id"],
                        view["state"].get("combat"),
                        [action["id"] for action in choice["options"]],
                    ],
                    sort_keys=True,
                )
                combat_states[signature] = combat_states.get(signature, 0) + 1
                if combat_states[signature] > 3:
                    raise RuntimeError(
                        "Human test driver repeated a combat state without committing."
                    )
                declare = "declareAttacker" if choice["kind"] == "attackers" else "declareBlocker"
                finish = "finishAttackers" if choice["kind"] == "attackers" else "finishBlockers"
                action = next((a for a in choice["options"] if a["kind"] == declare), None)
                action = action or next((a for a in choice["options"] if a["kind"] == finish), None)
                if not action:
                    raise RuntimeError("No forward combat choice offered to the human test driver.")
                body = {"actionId": action["id"]}
            if (choice.get("choice") or {}).get("kind") == "cardNameSelection":
                # The human UI has an explicit cardName field absent from the
                # current SDK DecisionResponse. Use an offered real suggestion;
                # this is protocol verification, not an agent-strength score.
                suggestions = choice["choice"].get("suggestions") or []
                if not suggestions:
                    raise RuntimeError("No offered card name for the human test driver.")
                body = {"actionId": body["actionId"], "cardName": suggestions[0]}
            body.update(decisionId=choice["id"], revision=view["revision"])
            accepted = await engine.post(
                f"/game/sessions/{session_id}/actions",
                json=body,
                timeout=action_timeout_seconds,
            )
            if not accepted.is_success:
                raise RuntimeError(
                    f"Human action rejected ({accepted.status_code}): {accepted.text[:1000]}"
                )
            submitted = key
            actions += 1
            if actions % 200 == 0:
                print(
                    f"Accepted {actions} human-endpoint test actions in {session_id}; "
                    f"turn {view['state'].get('turnNumber')}.",
                    flush=True,
                )
    raise TimeoutError("The owned human-action check exceeded its time/action budget.")


def competition(client, event_id):
    result = client.get(f"/api/v1/local-competitions/{event_id}")
    result.raise_for_status()
    return result.json()


def wait_active_job(client, event_id):
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        event = competition(client, event_id)
        if event["status"] != "running":
            raise RuntimeError(f"Owned competition did not remain running: {event.get('error')}")
        if event.get("activeJobId"):
            job = client.get(f"/api/v1/jobs/{event['activeJobId']}").json()
            if job.get("details", {}).get("sessionId"):
                return event, job
        time.sleep(0.2)
    raise TimeoutError("The owned competition did not open a table.")


def main():
    configure_local_http_loop()
    httpx.get(ENGINE + "/health").raise_for_status()
    parent = ROOT / ".deepdeck/validation"
    parent.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix="human-competition-", dir=parent))
    profile = scratch / "profile"
    shutil.copytree(ROOT / "apps/learner-web/dist", profile / "apps/learner-web/dist")
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(
            (
                "DEEPDECK",
                "DDL_",
                "OPENAI_",
                "ANTHROPIC_",
                "GEMINI_",
                "GOOGLE_API_",
                "MISTRAL_",
                "DEEPSEEK_",
            )
        )
    }
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "deepdeck_learner.desktop",
            "--headless",
            "--data-dir",
            str(profile),
            "--port",
            "0",
            "--engine-port",
            "8787",
        ],
        cwd=scratch,
        env=environment,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    report = {
        "profile": str(profile),
        "success": False,
        "sessions": [],
        "driver": (
            "SDK graph priority choices plus human card-name/combat interaction adapters; "
            "not real humans, SDK transport verification or a strength benchmark"
        ),
        "productionWrites": False,
        "paidCalls": False,
    }
    print(f"Isolated human competition QA: {scratch}", flush=True)
    try:
        instance = wait_instance(process, profile)
        with httpx.Client(base_url=instance["url"], timeout=240, trust_env=False) as client:
            client.headers["X-DeepDeck-Token"] = client.get("/api/v1/session").json()["token"]
            assert client.get("/api/v1/agent-studio/llm-settings").json()["configured"] is False
            items = client.get(
                "/api/v1/catalog/decks", params={"format": "legacy", "search": "Reanimator"}
            ).json()["items"]
            deck = next(item for item in items if item["name"] == "Reanimator")
            client.post(f"/api/v1/catalog/decks/{deck['id']}/download").raise_for_status()
            created = client.post(
                "/api/v1/local-competitions",
                json={
                    "name": "Shared-screen recovery QA",
                    "type": "round_robin",
                    "format": "legacy",
                    "legs": 2,
                    "seed": 58422,
                    "seeding": "registration",
                    "starting_seat": 1,
                    "max_turns": 80,
                    "match_timeout_seconds": None,
                    "engine_url": ENGINE,
                    "entrants": [
                        {"kind": "human", "name": name, "deck_id": deck["id"]}
                        for name in ["QA Ada", "QA Ben"]
                    ],
                },
            )
            created.raise_for_status()
            event = created.json()
            report["eventId"] = event["id"]
            identities = {p["id"]: p["player_id"] for p in event["entrants"]}
            event, first = wait_active_job(client, event["id"])
            report["sessions"].append(first["details"]["sessionId"])

            async def opening(debug_url):
                await check_browser(
                    debug_url,
                    instance["url"],
                    scratch,
                    2,
                    competition=True,
                    expected_names=["QA Ada", "QA Ben"],
                )

            browser.verify = opening
            browser.main()
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                event = competition(client, event["id"])
                if event["status"] == "paused":
                    break
                time.sleep(0.2)
            assert event["status"] == "paused" and event["champion"] is None
            assert all(row["played"] == 0 for row in event["standings"])
            partial = next(m for m in event["matches"] if m["attempts"])["attempts"][0]
            assert partial.get("replayId")
            assert client.get("/api/v1/statistics/local").json()["summary"]["seatResults"] == 0
            client.post(f"/api/v1/local-competitions/{event['id']}/resume").raise_for_status()
            report["recoveredPartialReplay"] = partial["replayId"]
            seen_jobs = set()
            deadline = time.monotonic() + 1500
            while time.monotonic() < deadline:
                event = competition(client, event["id"])
                if event["status"] == "completed":
                    break
                if event["status"] != "running":
                    raise RuntimeError(f"Owned competition paused: {event.get('error')}")
                assert {p["id"]: p["player_id"] for p in event["entrants"]} == identities
                job_id = event.get("activeJobId")
                if not job_id or job_id in seen_jobs:
                    time.sleep(0.2)
                    continue
                job = client.get(f"/api/v1/jobs/{job_id}").json()
                session_id = job.get("details", {}).get("sessionId")
                if not session_id:
                    time.sleep(0.2)
                    continue
                report["sessions"].append(session_id)
                result = asyncio.run(drive_human_actions(session_id))
                seen_jobs.add(job_id)
                report.setdefault("matches", []).append(
                    {
                        "sessionId": session_id,
                        "actions": result["actions"],
                        "matchState": result["view"].get("matchState"),
                        "outcome": result["view"]["state"].get("outcome"),
                    }
                )
                print(
                    f"Real Engine match ended after {result['actions']} accepted actions.",
                    flush=True,
                )
            assert event["status"] == "completed"
            assert len([m for m in event["matches"] if m["status"] == "done"]) == 2
            assert all(row["played"] == 2 for row in event["standings"])
            statistics = client.get("/api/v1/statistics/local").json()
            assert statistics["summary"]["matches"] == 2
            assert statistics["summary"]["seatResults"] == 4
            assert {row["id"] for row in statistics["players"]} == set(identities.values())
            for match in event["matches"]:
                replay = client.get(f"/api/v1/models/local-tables/replays/{match['replayId']}")
                replay.raise_for_status()
                saved = replay.json()
                assert saved["metadata"]["competitionId"] == event["id"]
                assert saved["metadata"]["gameStatus"] not in {"interrupted", "error", "timeout"}
                assert len(saved["frames"]) > 10
                expected_ids = [identities[person] for person in match["players"]]
                assert [p["agentId"] for p in saved["metadata"]["participants"]] == expected_ids
            report.update(
                success=True, standings=event["standings"], statistics=statistics["summary"]
            )
    finally:
        # Profile shutdown stops only its own jobs; never stop the shared Engine.
        if process.poll() is None:
            (profile / ".deepdeck/desktop-stop.request").touch()
            process.wait(timeout=30)
        for session_id in set(report["sessions"]):
            response = httpx.delete(f"{ENGINE}/game/sessions/{session_id}", timeout=10)
            assert response.status_code in {200, 204, 404}
        report["processExitCode"] = process.returncode
        report["success"] = report["success"] and process.returncode == 0
        (scratch / "verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    assert report["success"]
    print(
        f"PASS: interruption/resume, real matches, rotated identities and stats: {scratch}",
        flush=True,
    )


if __name__ == "__main__":
    main()
