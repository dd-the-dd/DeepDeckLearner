"""Play a real four-seat Commander game in an isolated portable player profile.

Imports public decks, creates SDK graph agents, checks recordings and local
statistics. No League deployment, private profile, model account or LLM call.
Leaves a report and diagnostic profile in the task-owned validation directory.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from uuid import UUID

import httpx
from verify_desktop import port, wait_instance

ROOT = Path(__file__).resolve().parents[1]


def public_deck(client: httpx.Client, name: str) -> dict:
    response = client.get("/api/v1/catalog/decks", params={"format": "commander", "search": name})
    response.raise_for_status()
    matches = [item for item in response.json()["items"] if item["name"] == name]
    if len(matches) != 1:
        raise ValueError(f"Expected one public Commander deck named {name!r}; found {len(matches)}")
    return {**matches[0], "id": str(UUID(matches[0]["id"]))}


def verify(
    client: httpx.Client, engine_url: str, names: list[str], seed: int, max_turns: int, timeout: int
) -> dict:
    assert client.get("/api/v1/agent-studio/agents").json()["items"] == []
    assert client.get("/api/v1/agent-studio/llm-settings").json()["configured"] is False
    client.headers["X-DeepDeck-Token"] = client.get("/api/v1/session").json()["token"]
    imported: dict[str, dict] = {}
    seats = []
    for index, name in enumerate(names):
        if name not in imported:
            deck = public_deck(client, name)
            response = client.post(f"/api/v1/catalog/decks/{deck['id']}/download")
            response.raise_for_status()
            imported[name] = deck
        deck = imported[name]
        response = client.post(
            "/api/v1/agent-studio/agents",
            json={
                "name": f"Commander QA seat {index + 1}",
                "format": "commander",
                "deckVersionIds": [deck["id"]],
            },
        )
        response.raise_for_status()
        agent = response.json()
        seats.append({"kind": "agent", "agent_id": agent["id"], "deck_id": deck["id"]})
    print(f"Public decks imported; four graph agents ready: {', '.join(names)}", flush=True)
    response = client.post(
        "/api/v1/jobs",
        json={
            "kind": "playtest.batch",
            "format": "commander",
            "games": 1,
            "seed": seed,
            "max_turns": max_turns,
            "match_timeout_seconds": timeout,
            "ai_speed": "100ms",
            "engine_url": engine_url,
            "seats": seats,
        },
    )
    response.raise_for_status()
    job = response.json()
    deadline = time.monotonic() + timeout + 90
    while job["status"] in {"running", "queued"}:
        if time.monotonic() > deadline:
            raise TimeoutError(f"Commander job exceeded its safety budget: {job['id']}")
        time.sleep(0.5)
        job = client.get(f"/api/v1/jobs/{job['id']}").json()
    if job["status"] != "completed":
        # This worker has no account credentials; bounded diagnostics are local QA only.
        raise RuntimeError(f"Commander job {job['id']} ended {job['status']}: {job['logs'][-6:]}")
    assert job["details"]["completed"] == 1
    replay_counts = []
    first_replay = None
    for seat in seats:
        replays = client.get(f"/api/v1/models/{seat['agent_id']}/replays").json()["items"]
        assert len(replays) == 1 and replays[0]["frameCount"] > 1, replays
        replay_counts.append(len(replays))
        if first_replay is None:
            response = client.get(f"/api/v1/models/{seat['agent_id']}/replays/{replays[0]['id']}")
            response.raise_for_status()
            first_replay = response.json()
    assert first_replay is not None
    assert first_replay["metadata"]["spectatorCapture"] == "all-hands"
    frames = first_replay["frames"]
    initial = frames[0]["state"]["players"]
    assert len(initial) == 4 and all(player["hand"] for player in initial)
    assert all(
        card.get("name") and card["name"].lower() != "hidden"
        for player in initial
        for card in player["hand"]
    )
    for frame in frames:
        for player in frame["state"]["players"]:
            assert all(
                isinstance(card, dict) and card.get("name") and card["name"].lower() != "hidden"
                for card in player["hand"]
            ), "A spectator frame lost a hand's card identities"
            for zone in ("library", "sideboard"):
                assert all(card is None for card in player.get(zone, [])), "Private zone exposed"
    summary = client.get("/api/v1/statistics/local").json()["summary"]
    assert summary["matches"] == 1 and summary["seatResults"] == 4, summary
    return {
        "format": "commander",
        "seats": 4,
        "deckNames": names,
        "seed": seed,
        "maxTurns": max_turns,
        "jobId": job["id"],
        "completedMatches": 1,
        "replaysPerAgent": replay_counts,
        "frames": len(frames),
        "observedTurns": max(frame["state"].get("turnNumber", 0) for frame in frames),
        "winner": (first_replay["metadata"].get("outcome") or {}).get("winner"),
        "allHandsRecorded": True,
        "privateZonesConcealed": True,
        "statisticMatches": summary["matches"],
        "statisticSeatResults": summary["seatResults"],
        "llmCalls": 0,
        "leagueDeployments": 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("installation", type=Path, help="Extracted portable DeepDeck folder")
    parser.add_argument("--deck", action="append", dest="decks")
    parser.add_argument("--seed", type=int, default=194052)
    parser.add_argument("--max-turns", type=int, default=200)
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()
    names = args.decks or ["Alexios", "Yenna", "Tatyova", "Tatyova"]
    if len(names) != 4 or not 20 <= args.max_turns <= 2000 or not 30 <= args.timeout <= 3600:
        raise SystemExit("Choose exactly four decks and valid positive turn/time budgets.")
    executable = args.installation.resolve() / "DeepDeck.exe"
    if not executable.is_file():
        raise SystemExit("The portable DeepDeck.exe is missing.")
    parent = ROOT / ".deepdeck" / "validation"
    parent.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix="commander-desktop-", dir=parent))
    profile = scratch / "profile"
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("DEEPDECK", "DDL_", "PYTHON", "CONDA", "MTG_"))
    }
    environment["PATH"] = str(Path(os.environ["SYSTEMROOT"]) / "System32")
    environment["PYTHONNOUSERSITE"] = "1"
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    process = subprocess.Popen(
        [
            str(executable),
            "--data-dir",
            str(profile),
            "--port",
            "0",
            "--engine-port",
            str(port()),
            "--headless",
        ],
        cwd=scratch,
        env=environment,
        **flags,
    )
    print(f"Isolated Commander verification: {scratch}", flush=True)
    try:
        instance = wait_instance(process, profile)
        with httpx.Client(base_url=instance["url"], timeout=60) as client:
            report = verify(
                client, instance["engineUrl"], names, args.seed, args.max_turns, args.timeout
            )
        (scratch / "verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2), flush=True)
    finally:
        if process.poll() is None:
            (profile / ".deepdeck" / "desktop-stop.request").touch()
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired as error:
                process.terminate()
                raise RuntimeError("The owned Commander player did not stop cleanly.") from error
    assert process.returncode == 0


if __name__ == "__main__":
    main()
