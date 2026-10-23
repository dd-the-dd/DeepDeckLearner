"""Real eight-agent Commander groups and two-win playoff, in an owned source profile.

Uses the existing healthy loopback Engine and public decks only. No paid model,
League deployment, user statistics, synthetic result or production write.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
from verify_commander_desktop import public_deck
from verify_desktop import wait_instance

ROOT = Path(__file__).resolve().parents[1]
ENGINE = "http://127.0.0.1:8787"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resume-report", type=Path)
    args = parser.parse_args()
    httpx.get(ENGINE + "/health", timeout=10).raise_for_status()
    resume_event_id = None
    if args.resume_report:
        original = args.resume_report.resolve()
        validation = (ROOT / ".deepdeck/validation").resolve()
        if not original.is_relative_to(validation) or not original.parent.name.startswith(
            "grouped-commander-"
        ):
            raise ValueError("Resume only an owned grouped-Commander QA report.")
        prior = json.loads(original.read_text(encoding="utf-8"))
        scratch = original.parent
        profile = scratch / "profile"
        if Path(prior["profile"]).resolve() != profile or prior["success"]:
            raise ValueError("A failed owned QA profile is required for recovery verification.")
        if (profile / ".deepdeck/secrets.json").exists():
            raise ValueError("Do not load account credentials during QA recovery.")
        resume_event_id = prior["eventId"]
    else:
        scratch = Path(
            tempfile.mkdtemp(prefix="grouped-commander-", dir=ROOT / ".deepdeck/validation")
        )
        profile = scratch / "profile"
    shutil.copytree(
        ROOT / "apps/learner-web/dist", profile / "apps/learner-web/dist", dirs_exist_ok=True
    )
    report_name = (
        f"resume-verification-{time.time_ns()}.json" if resume_event_id else "verification.json"
    )
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
        "matches": [],
        "resumedFrom": str(args.resume_report.resolve()) if args.resume_report else None,
    }
    print(f"Owned Commander groups QA: {scratch}", flush=True)
    try:
        instance = wait_instance(process, profile)
        with httpx.Client(base_url=instance["url"], timeout=300, trust_env=False) as client:
            client.headers["X-DeepDeck-Token"] = client.get("/api/v1/session").json()["token"]
            assert not client.get("/api/v1/agent-studio/llm-settings").json()["configured"]
            baseline_jobs = {job["id"] for job in client.get("/api/v1/jobs").json()}
            completed_before = {}
            if resume_event_id:
                response = client.get(f"/api/v1/local-competitions/{resume_event_id}")
                response.raise_for_status()
                before = response.json()
                assert before["status"] == "paused" and before["champion"] is None
                completed_before = {
                    m["id"]: (m["winner"], m.get("replayId"))
                    for m in before["matches"]
                    if m["status"] == "done"
                }
                report["preservedCompletedMatchIds"] = list(completed_before)
            imported = {}
            entrants = []
            for index, name in enumerate(
                [] if resume_event_id else ["Alexios", "Yenna", "Tatyova", "Yenna"] * 2
            ):
                if name not in imported:
                    imported[name] = public_deck(client, name)
                    client.post(
                        f"/api/v1/catalog/decks/{imported[name]['id']}/download"
                    ).raise_for_status()
                deck = imported[name]
                created = client.post(
                    "/api/v1/agent-studio/agents",
                    json={
                        "name": f"Groups QA {index + 1} · {name}",
                        "format": "commander",
                        "deckVersionIds": [deck["id"]],
                    },
                )
                created.raise_for_status()
                entrants.append(
                    {
                        "kind": "agent",
                        "name": f"Groups QA {index + 1}",
                        "agent_id": created.json()["id"],
                        "deck_id": deck["id"],
                    }
                )
            created = client.post(
                f"/api/v1/local-competitions/{resume_event_id}/resume"
                if resume_event_id
                else "/api/v1/local-competitions",
                json={
                    "name": "Eight SDK agents · groups and playoff series",
                    "type": "groups_cut",
                    "format": "commander",
                    "seed": 194052,
                    "seeding": "registration",
                    "groups": 2,
                    "qualifiers_per_group": 2,
                    "legs": 1,
                    "playoff_wins": 2,
                    "reseed": True,
                    "seed_advantage": True,
                    "starting_seat": "rotate",
                    "ai_speed": "100ms",
                    "max_turns": 200,
                    "match_timeout_seconds": 600,
                    "engine_url": ENGINE,
                    "entrants": entrants,
                }
                if not resume_event_id
                else None,
            )
            created.raise_for_status()
            event_id = resume_event_id or created.json()["id"]
            report["eventId"] = event_id
            deadline = time.monotonic() + 2400
            previous = None
            while time.monotonic() < deadline:
                response = client.get(f"/api/v1/local-competitions/{event_id}")
                response.raise_for_status()
                event = response.json()
                report["lastOrganizerStatus"] = event["status"]
                report["completedBracketMatches"] = sum(
                    m["status"] == "done" for m in event["matches"]
                )
                jobs = client.get("/api/v1/jobs").json()
                for job in jobs:
                    if job["id"] in baseline_jobs:
                        # Engine session counters can restart. An old job's
                        # session ID must never authorize deleting a new table.
                        continue
                    session = job.get("details", {}).get("sessionId")
                    if session and session not in report["sessions"]:
                        report["sessions"].append(session)
                progress = (
                    event["status"],
                    event.get("activeJobId"),
                    sum(m["status"] == "done" for m in event["matches"]),
                )
                if progress != previous:
                    print(
                        f"Groups organizer: {progress[0]}, {progress[2]} bracket matches done; "
                        f"{len(report['sessions'])} actual tables opened.",
                        flush=True,
                    )
                    previous = progress
                if event["status"] == "completed":
                    break
                if event["status"] not in {"running", "pausing"}:
                    report["organizerError"] = event.get("error")
                    raise RuntimeError("Owned group event paused; inspect its retained profile.")
                time.sleep(0.5)
            assert event["status"] == "completed", "Owned competition exceeded its QA budget."
            assert all(
                next((m["winner"], m.get("replayId")) for m in event["matches"] if m["id"] == key)
                == value
                for key, value in completed_before.items()
            ), "Recovery rewrote a completed encounter."
            assert len(event["entrants"]) == 8 and len(event["groups"]) == 2
            assert all(len(g["players"]) == 4 for g in event["groups"])
            assert len(event["playoffSeeds"]) == 4
            expected_qualifiers = {
                row["id"] for g in event["groupStandings"] for row in g["standings"][:2]
            }
            assert set(event["playoffSeeds"]) == expected_qualifiers
            groups = [m for m in event["matches"] if m["stage"] == "groups"]
            playoffs = [m for m in event["matches"] if m["stage"] != "groups"]
            assert len(groups) == 2 and len(playoffs) == 1
            final = playoffs[0]
            assert final["winner"] == event["champion"] and final["status"] == "done"
            assert set(final["players"]) == expected_qualifiers
            assert max(final["seriesWins"]) == 2
            assert sum(final["seriesWins"]) == len(final["seriesGames"])
            assert len(final["seriesGames"]) >= 2
            people = {p["id"]: p for p in event["entrants"]}
            for match in groups + playoffs:
                recordings = match.get("seriesGames") or [{"replayId": match["replayId"]}]
                starting_seats = []
                for recording in recordings:
                    replay_id = recording["replayId"]
                    agent_id = people[match["players"][0]]["agent_id"]
                    response = client.get(f"/api/v1/models/{agent_id}/replays/{replay_id}")
                    response.raise_for_status()
                    saved = response.json()
                    metadata = saved["metadata"]
                    assert metadata["competitionId"] == event_id
                    assert metadata["competitionMatchId"] == match["id"]
                    assert metadata["gameStatus"] in {"completed", "complete", "gameOver"}
                    assert metadata["spectatorCapture"] == "all-hands"
                    assert len(metadata["agentTiming"]) == 4
                    assert len({p["controllerId"] for p in metadata["agentTiming"]}) == 4
                    actual = [p["agentId"] for p in metadata["participants"]]
                    assert actual == [people[p]["agent_id"] for p in match["players"]]
                    assert saved["frames"][-1]["state"].get("outcome", {}).get("winner")
                    for frame in saved["frames"]:
                        assert all(
                            card is None
                            for p in frame["state"]["players"]
                            for zone in ["library", "sideboard"]
                            for card in p.get(zone, [])
                        )
                    starting_seats.append(metadata["startingSeat"])
                    report["matches"].append(
                        {
                            "bracketMatch": match["id"],
                            "stage": match["stage"],
                            "replayId": replay_id,
                            "startingSeat": metadata["startingSeat"],
                            "seed": metadata["seed"],
                            "frames": len(saved["frames"]),
                            "outcome": saved["frames"][-1]["state"]["outcome"],
                            "decisionAudit": metadata.get("decisionAudit"),
                        }
                    )
                if match["stage"] != "groups":
                    best = min(
                        range(4), key=lambda i: event["playoffSeeds"].index(match["players"][i])
                    )
                    assert starting_seats == [
                        (best + i) % 4 + 1 for i in range(len(starting_seats))
                    ]
            stats = client.get("/api/v1/statistics/local").json()
            games = len(report["matches"])
            assert stats["summary"]["matches"] == games
            assert stats["summary"]["seatResults"] == games * 4
            assert stats["summary"]["agents"] == 8 and stats["summary"]["players"] == 0
            assert len({m["seed"] for m in report["matches"]}) == games
            report.update(
                success=True,
                champion=event["champion"],
                playoffSeeds=event["playoffSeeds"],
                groupStandings=event["groupStandings"],
                standings=event["standings"],
                statistics=stats["summary"],
                seriesWins=final["seriesWins"],
            )
    except Exception as error:
        report["failureType"] = type(error).__name__
        raise
    finally:
        if process.poll() is None:
            (profile / ".deepdeck/desktop-stop.request").touch()
            process.wait(timeout=30)
        for session in set(report["sessions"]):
            response = httpx.delete(f"{ENGINE}/game/sessions/{session}", timeout=10)
            assert response.status_code in {200, 204, 404}
        report["processExitCode"] = process.returncode
        report["success"] = report["success"] and process.returncode == 0
        (scratch / report_name).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(
        f"PASS: real eight-agent groups, qualification, series, replay and stats: {scratch}",
        flush=True,
    )


if __name__ == "__main__":
    main()
