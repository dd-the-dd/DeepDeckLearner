"""Real two-human/two-SDK-agent Commander, isolated from user data and League.

Edge exercises named-seat privacy and interrupted playback first. The second table
uses the existing human HTTP test driver only on human IDs; AI seats are controlled
by actual registered SDK agents. Not a strategy-strength benchmark or paid LLM run.
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
from verify_commander_desktop import public_deck
from verify_desktop import wait_instance
from verify_hotseat import check_browser
from verify_human_competition import drive_human_actions

from deepdeck_learner.runtime import configure_local_http_loop

ROOT = Path(__file__).resolve().parents[1]
ENGINE = "http://127.0.0.1:8787"


def wait_session(client, job):
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/jobs/{job['id']}")
        response.raise_for_status()
        job = response.json()
        if job.get("details", {}).get("sessionId"):
            return job
        if job["status"] not in {"queued", "running"}:
            raise RuntimeError(f"Owned mixed table did not open: {job['logs'][-4:]}")
        time.sleep(0.2)
    raise TimeoutError("The owned mixed Commander table did not open.")


def main():
    configure_local_http_loop()
    httpx.get(ENGINE + "/health").raise_for_status()
    parent = ROOT / ".deepdeck/validation"
    parent.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix="mixed-commander-", dir=parent))
    profile = scratch / "profile"
    shutil.copytree(ROOT / "apps/learner-web/dist", profile / "apps/learner-web/dist")
    environment = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(
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
        "success": False,
        "profile": str(profile),
        "sessions": [],
        "paidCalls": False,
        "productionWrites": False,
        "driver": "Automated human-HTTP driver on human IDs only; real SDK agents on AI seats",
    }
    print(f"Isolated mixed Commander QA: {scratch}", flush=True)
    try:
        instance = wait_instance(process, profile)
        with httpx.Client(base_url=instance["url"], timeout=300, trust_env=False) as client:
            client.headers["X-DeepDeck-Token"] = client.get("/api/v1/session").json()["token"]
            assert client.get("/api/v1/agent-studio/llm-settings").json()["configured"] is False
            imported = {}
            seats = []
            for index, name in enumerate(["Yenna", "Tatyova", "Alexios", "Yenna"]):
                if name not in imported:
                    imported[name] = public_deck(client, name)
                    client.post(
                        f"/api/v1/catalog/decks/{imported[name]['id']}/download"
                    ).raise_for_status()
                deck = imported[name]
                if index < 2:
                    seats.append(
                        {
                            "kind": "human",
                            "deck_id": deck["id"],
                            "player_id": "local-player-" + f"{index + 1:032x}",
                            "player_name": f"QA human {index + 1}",
                        }
                    )
                else:
                    response = client.post(
                        "/api/v1/agent-studio/agents",
                        json={
                            "name": f"QA SDK seat {index + 1}",
                            "format": "commander",
                            "deckVersionIds": [deck["id"]],
                        },
                    )
                    response.raise_for_status()
                    seats.append(
                        {"kind": "agent", "agent_id": response.json()["id"], "deck_id": deck["id"]}
                    )
            payload = {
                "kind": "playtest.table",
                "format": "commander",
                "seats": seats,
                "engine_url": ENGINE,
                "seed": 194052,
                "starting_seat": 1,
                "max_turns": 200,
                "ai_speed": "100ms",
                "match_timeout_seconds": None,
            }
            response = client.post("/api/v1/jobs", json=payload)
            response.raise_for_status()
            first = wait_session(client, response.json())
            report["sessions"].append(first["details"]["sessionId"])
            assert first["details"]["humanPlayerIds"] == ["local-human", "local-human-2"]

            async def opening(debug_url):
                await check_browser(
                    debug_url,
                    instance["url"],
                    scratch,
                    2,
                    expected_names=["QA human 1", "QA human 2"],
                )

            browser.verify = opening
            browser.main()
            assert client.get(f"/api/v1/jobs/{first['id']}").json()["status"] == "stopped"
            assert client.get("/api/v1/statistics/local").json()["summary"]["seatResults"] == 0
            response = client.post("/api/v1/jobs", json=payload)
            response.raise_for_status()
            job = wait_session(client, response.json())
            session_id = job["details"]["sessionId"]
            report["sessions"].append(session_id)
            result = asyncio.run(
                drive_human_actions(
                    session_id,
                    human_ids=set(job["details"]["humanPlayerIds"]),
                    seconds=900,
                    # An action can run AI turns until the next human decision.
                    # Do not time out at the human-only driver's 30-second limit
                    # and never retry an ambiguously accepted action.
                    action_timeout_seconds=180,
                )
            )
            deadline = time.monotonic() + 30
            while job["status"] in {"queued", "running"} and time.monotonic() < deadline:
                time.sleep(0.2)
                job = client.get(f"/api/v1/jobs/{job['id']}").json()
            assert job["status"] == "completed" and job["details"]["completed"] == 1, job
            replay_ids = set()
            for seat in seats[2:]:
                rows = client.get(f"/api/v1/models/{seat['agent_id']}/replays").json()["items"]
                completed = [row for row in rows if row["id"] == job["details"]["replayId"]]
                assert len(completed) == 1
                replay_ids.add(completed[0]["id"])
            assert len(replay_ids) == 1
            saved = client.get(
                f"/api/v1/models/{seats[2]['agent_id']}/replays/{job['details']['replayId']}"
            ).json()
            assert saved["metadata"]["spectatorCapture"] == "all-hands"
            timing = saved["metadata"]["agentTiming"]
            assert len(timing) == 2 and len({row["controllerId"] for row in timing}) == 2
            assert len(saved["frames"]) > 10
            for frame in saved["frames"]:
                for player in frame["state"]["players"]:
                    assert all(
                        card is None
                        for zone in ["library", "sideboard"]
                        for card in player.get(zone, [])
                    )
            stats = client.get("/api/v1/statistics/local").json()
            assert stats["summary"]["matches"] == 1 and stats["summary"]["seatResults"] == 4
            assert stats["summary"]["agents"] == 2 and stats["summary"]["players"] == 2
            assert {row["id"] for row in stats["players"]} == {
                seat["player_id"] for seat in seats[:2]
            }
            report.update(
                success=True,
                summary=stats["summary"],
                humanActions=result["actions"],
                winner=saved["metadata"]["outcome"]["winner"],
                turns=max(frame["state"].get("turnNumber", 0) for frame in saved["frames"]),
                frames=len(saved["frames"]),
                agentTiming=timing,
                decisionAudit=saved["metadata"].get("decisionAudit"),
                replayId=job["details"]["replayId"],
                jobId=job["id"],
            )
    except Exception as error:
        report["failureType"] = type(error).__name__
        raise
    finally:
        if process.poll() is None:
            (profile / ".deepdeck/desktop-stop.request").touch()
            process.wait(timeout=30)
        for session in set(report["sessions"]):
            deleted = httpx.delete(f"{ENGINE}/game/sessions/{session}", timeout=10)
            assert deleted.status_code in {200, 204, 404}
        report["processExitCode"] = process.returncode
        report["success"] = report["success"] and process.returncode == 0
        (scratch / "verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    assert report["success"]
    print(
        f"PASS: mixed Commander privacy, actual SDK seats, full replay and stats: {scratch}",
        flush=True,
    )


if __name__ == "__main__":
    main()
