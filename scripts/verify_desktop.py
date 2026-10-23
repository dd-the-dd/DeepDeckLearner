"""Exercise the actual extracted player with no Python/Git/Node/Rust on PATH."""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import psutil


def port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def wait_instance(process: subprocess.Popen, root: Path) -> dict:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        path = root / ".deepdeck" / "desktop-instance.json"
        if path.is_file():
            return json.loads(path.read_text("utf-8"))
        if process.poll() is not None:
            log = root / ".deepdeck" / "logs" / "desktop.log"
            raise RuntimeError(
                f"Desktop exited {process.returncode}: {log.read_text('utf-8') if log.is_file() else 'no log'}"
            )
        time.sleep(0.2)
    raise TimeoutError("The extracted desktop did not report readiness.")


def verify_local_copilot(client: httpx.Client, agent: dict, engine_url: str) -> dict:
    """Real frozen SDK tool loop; the provider is a loopback fixture, not a paid model."""
    requests = []

    class Provider(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(payload)
            turn = len(requests)
            if turn <= 3 or turn in {5, 6}:
                name = (
                    ("get_agent", "update_agent", "start_playtest")[turn - 1]
                    if turn <= 3
                    else "get_agent"
                    if turn == 5
                    else "update_agent"
                )
                arguments = {"agent_id": agent["id"]}
                if turn in {2, 6}:
                    arguments.update(
                        {
                            "expected_revision": agent["revision"] + (1 if turn == 6 else 0),
                            "heuristics": {"protect_advantage": 1.8 if turn == 6 else 1.7},
                            "revision_reason": "Standalone copilot verification",
                        }
                    )
                if turn == 3:
                    arguments.update(
                        {
                            "player_deck_version_id": agent["deckVersionIds"][0],
                            "agent_deck_version_id": "random",
                            "seed": 42,
                            "starting_seat": 1,
                            "max_turns": 60,
                        }
                    )
                message = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": f"desktop-call-{turn}",
                            "type": "function",
                            "function": {"name": name, "arguments": json.dumps(arguments)},
                        }
                    ],
                }
                reason = "tool_calls"
            else:
                message, reason = (
                    {
                        "role": "assistant",
                        "content": "Protection priority updated; your untimed local table is ready.",
                    },
                    "stop",
                )
            response = json.dumps(
                {
                    "id": f"desktop-{turn}",
                    "object": "chat.completion",
                    "created": 1,
                    "model": "desktop-fixture",
                    "choices": [{"index": 0, "message": message, "finish_reason": reason}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                }
            ).encode()
            if turn >= 7:
                response = json.dumps(
                    {
                        "error": {
                            "message": "Fixture provider interrupted after save; local-fixture",
                            "type": "server_error",
                        }
                    }
                ).encode()
            self.send_response(503 if turn >= 7 else 200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        configured = client.put(
            "/api/v1/agent-studio/llm-settings",
            json={
                "providerName": "Standalone loopback fixture",
                "baseUrl": f"http://127.0.0.1:{server.server_port}/v1",
                "model": "desktop-fixture",
                "apiKey": "local-fixture",
                "leagueMcpEnabled": False,
            },
        )
        configured.raise_for_status()
        response = client.post(
            "/api/v1/agent-studio/chat",
            json={
                "context": "agent",
                "activeAgentId": agent["id"],
                "messages": [
                    {
                        "role": "user",
                        "content": "Increase protection priority to 1.7, save a version, then let me play against it now with seed 42, me going first and a 60-turn limit. No timer.",
                    }
                ],
            },
        )
        response.raise_for_status()
        events = response.json()["toolEvents"]
        assert [event["tool"] for event in events] == [
            "get_agent",
            "update_agent",
            "start_playtest",
        ], events
        assert all(event["ok"] for event in events), events
        saved = client.get(f"/api/v1/agent-studio/agents/{agent['id']}").json()
        assert (
            saved["revision"] == agent["revision"] + 1
            and saved["heuristics"]["protect_advantage"] == 1.7
        )
        table = events[-1]["result"]
        settings = table["details"]["playSettings"]
        assert settings["firstSeed"] == 42 and settings["startingSeat"] == 1
        assert settings["maxTurns"] == 60
        assert (
            settings["humanDecisionTimeoutSeconds"] is None
            and settings["matchTimeoutSeconds"] is None
        )
        with httpx.Client(base_url=engine_url, timeout=10) as engine:
            deadline = time.monotonic() + 60
            while True:
                table = client.get(f"/api/v1/jobs/{table['id']}").json()
                assert table["status"] in {"queued", "running"}, table
                session_id = table["details"].get("sessionId")
                if session_id:
                    view = engine.get(f"/game/sessions/{session_id}").json()
                    if (view.get("decision") or {}).get("playerId") == "local-human":
                        break
                if time.monotonic() > deadline:
                    raise TimeoutError("The chat-created human table did not reach a decision.")
                time.sleep(0.25)
            assert table["details"]["engineUrl"] == engine_url
            print(
                "Chat-created table uses its dedicated Engine; checking untimed human play beyond 30 seconds.",
                flush=True,
            )
            started_waiting = time.monotonic()
            while time.monotonic() - started_waiting < 32:
                time.sleep(1)
                view = engine.get(f"/game/sessions/{session_id}").json()
                assert (
                    not view.get("error")
                    and (view.get("decision") or {}).get("playerId") == "local-human"
                ), view
                assert client.get(f"/api/v1/jobs/{table['id']}").json()["status"] == "running"
            waited_seconds = round(time.monotonic() - started_waiting, 1)
            closed = client.post(f"/api/v1/games/{session_id}/stop")
            closed.raise_for_status()
            assert closed.json()["status"] == "cancelled"
            assert engine.get(f"/game/sessions/{session_id}").status_code == 404
        rows = client.get(f"/api/v1/models/{agent['id']}/replays").json()["items"]
        partial = next(row for row in rows if row.get("gameStatus") == "interrupted")
        replay = client.get(f"/api/v1/models/{agent['id']}/replays/{partial['id']}")
        replay.raise_for_status()
        metadata = replay.json()["metadata"]
        assert metadata["seed"] == 42 and metadata["startingSeat"] == 1
        assert metadata["playSettings"] == settings and metadata["outcome"]["winner"] is None
        assert metadata["agentVersions"] == [
            {"agentId": agent["id"], "name": agent["name"], "revision": saved["revision"]}
        ]
        assert client.get("/api/v1/statistics/local").json()["summary"]["matches"] == 2
        partial_reply = client.post(
            "/api/v1/agent-studio/chat",
            json={
                "context": "agent",
                "activeAgentId": agent["id"],
                "messages": [
                    {"role": "user", "content": "Increase protection priority to 1.8 and save it."}
                ],
            },
        )
        partial_reply.raise_for_status()
        partial_result = partial_reply.json()
        assert "Some actions completed" in partial_result["message"]
        assert (
            "local-fixture" not in partial_result["message"]
            and "[redacted]" in partial_result["message"]
        )
        assert [event["tool"] for event in partial_result["toolEvents"]] == [
            "get_agent",
            "update_agent",
        ]
        assert all(event["ok"] for event in partial_result["toolEvents"])
        saved = client.get(f"/api/v1/agent-studio/agents/{agent['id']}").json()
        assert (
            saved["revision"] == agent["revision"] + 2
            and saved["heuristics"]["protect_advantage"] == 1.8
        )
        return {
            "provider": "loopback-fixture",
            "realModelCalls": 0,
            "sdkHttpRequests": len(requests),
            "tools": [event["tool"] for event in events],
            "revision": saved["revision"],
            "chatPlaytestEngine": table["details"]["engineUrl"],
            "untimedHumanWaitSeconds": waited_seconds,
            "partialReplay": partial["id"],
            "partialAddedResult": False,
            "modelFailurePreservedSavedActions": True,
        }
    finally:
        server.shutdown()
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--native", action="store_true")
    parser.add_argument(
        "--browser",
        action="store_true",
        help="Exercise the real player launch/Pixi/exit flow in an isolated browser profile.",
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        help="Parent for a fresh isolated installation/profile; useful when the system drive is low on space.",
    )
    arguments = parser.parse_args()
    archive = arguments.archive.resolve()
    scratch_root = arguments.work_dir.resolve() if arguments.work_dir else None
    if scratch_root is not None:
        scratch_root.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix="deepdeck-desktop-qa-", dir=scratch_root))
    installation, profile = scratch / "installation", scratch / "profile"
    with zipfile.ZipFile(archive) as package:
        for name in package.namelist():
            if Path(name).is_absolute() or ".." in Path(name).parts:
                raise ValueError("The application archive contains an unsafe path.")
            if Path(name).name in {".env", "secrets.json", "league-account.json", "agent.json"}:
                raise AssertionError(f"Private workspace data appeared in the package: {name}")
        package.extractall(installation)
    executable = installation / "DeepDeck" / "DeepDeck.exe"
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("DEEPDECK", "DDL_", "PYTHON", "CONDA", "MTG_"))
    }
    environment["PATH"] = str(Path(os.environ["SYSTEMROOT"]) / "System32")
    environment["PYTHONNOUSERSITE"] = "1"
    temporary = scratch / "temporary"
    temporary.mkdir()
    environment.update({"TEMP": str(temporary), "TMP": str(temporary)})
    engine_port = port()
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    report = {"archive": str(archive), "scratch": str(scratch), "toolsOnPath": False}
    process = subprocess.Popen(
        [
            str(executable),
            "--data-dir",
            str(profile),
            "--port",
            "0",
            "--engine-port",
            str(engine_port),
            "--headless",
        ],
        cwd=scratch,
        env=environment,
        **flags,
    )
    print(f"Extracted standalone check: {scratch}", flush=True)
    try:
        instance = wait_instance(process, profile)
        assert instance["frozen"] is True
        with httpx.Client(base_url=instance["url"], timeout=60) as client:
            index = client.get("/")
            index.raise_for_status()
            script = re.search(r'src="([^"]+\.js)"', index.text)
            assert script and client.get(script[1]).status_code == 200
            status = client.get("/api/v1/status").json()
            assert (
                status["engine"]["healthy"]
                and status["engine"]["built"]
                and status["pixi"]["built"]
            )
            assert status["engine"]["url"] == instance["engineUrl"]
            assert client.get("/api/v1/agent-studio/agents").json()["items"] == []
            assert client.get("/api/v1/agent-studio/llm-settings").json()["configured"] is False
            report["isolatedProfile"] = True
            report["engineRevision"] = status["engine"]["revision"]
            report["engineHealthy"] = True
            # Real public HTTPS through the frozen HTTP client/certificate bundle.
            catalog = client.get(
                "/api/v1/catalog/decks", params={"format": "legacy", "search": "Delver"}
            )
            catalog.raise_for_status()
            assert catalog.json()["items"], catalog.text[:500]
            report["publicHttpsCatalog"] = True
            client.headers["X-DeepDeck-Token"] = client.get("/api/v1/session").json()["token"]
            # Import an actual public deck into this fresh QA profile. Do not
            # copy any private workspace files from the developer checkout.
            deck = next(item for item in catalog.json()["items"] if item["name"] == "Izzet Delver")
            deck_id = str(deck["id"])
            imported = client.post(f"/api/v1/catalog/decks/{deck_id}/download")
            imported.raise_for_status()
            report["publicDeckImported"] = deck_id
            response = client.post(
                "/api/v1/agent-studio/agents",
                json={
                    "name": "Standalone QA pilot",
                    "format": "legacy",
                    "deckVersionIds": [deck_id],
                },
            )
            response.raise_for_status()
            agent = response.json()
            batch = client.post(
                "/api/v1/jobs",
                json={
                    "kind": "playtest.batch",
                    "format": "legacy",
                    "games": 2,
                    "seed": 202452518,
                    "max_turns": 60,
                    "match_timeout_seconds": 600,
                    "engine_url": instance["engineUrl"],
                    "seats": [{"kind": "agent", "agent_id": agent["id"], "deck_id": deck_id}] * 2,
                },
            )
            batch.raise_for_status()
            job = batch.json()
            assert Path(job["argv"][0]).name == "DeepDeckWorker.exe"
            print("Frozen controller and public HTTPS ready; actual SDK batch started.", flush=True)
            # Observe the actual per-match safety budgets. A four-minute QA
            # stopwatch must not interrupt a still-running, valid slow match.
            deadline = time.monotonic() + 2 * 600 + 120
            last_completed = -1
            while job["status"] in {"queued", "running"}:
                if time.monotonic() > deadline:
                    raise TimeoutError(f"Frozen SDK batch did not finish: {job['logs'][-3:]}")
                completed = (job.get("details") or {}).get("completed", 0)
                if completed != last_completed:
                    print(f"Standalone batch: {completed}/2 complete", flush=True)
                    last_completed = completed
                time.sleep(0.5)
                job = client.get(f"/api/v1/jobs/{job['id']}").json()
            assert job["status"] == "completed", job["logs"]
            assert job["details"]["completed"] == 2
            replays = client.get(f"/api/v1/models/{agent['id']}/replays").json()["items"]
            assert len(replays) == 2 and all(row["frameCount"] > 1 for row in replays)
            stats = client.get("/api/v1/statistics/local").json()["summary"]
            assert stats["matches"] == 2 and stats["seatResults"] == 4, stats
            report.update(
                {
                    "completedMatches": 2,
                    "recordedReplays": len(replays),
                    "seatResults": stats["seatResults"],
                }
            )
            report["copilot"] = verify_local_copilot(client, agent, instance["engineUrl"])
            report["recordedReplays"] = len(
                client.get(f"/api/v1/models/{agent['id']}/replays").json()["items"]
            )
            print(
                "Frozen SDK chat: saved a strategy version, launched an untimed table on its dedicated Engine, then stopped it with a partial replay and no fabricated result.",
                flush=True,
            )
            timed = client.post(
                "/api/v1/jobs",
                json={
                    "kind": "playtest.table",
                    "format": "legacy",
                    "engine_url": instance["engineUrl"],
                    "seed": 43,
                    "starting_seat": 1,
                    "max_turns": 60,
                    "human_decision_timeout_seconds": 5,
                    "seats": [
                        {"kind": "human", "deck_id": deck_id},
                        {"kind": "agent", "agent_id": agent["id"], "deck_id": deck_id},
                    ],
                },
            )
            timed.raise_for_status()
            timed_job = timed.json()
            deadline = time.monotonic() + 60
            while timed_job["status"] in {"queued", "running"}:
                if time.monotonic() > deadline:
                    raise TimeoutError(
                        "The explicit five-second human inactivity limit was not enforced."
                    )
                time.sleep(0.25)
                timed_job = client.get(f"/api/v1/jobs/{timed_job['id']}").json()
            assert timed_job["status"] == "failed", timed_job
            rows = client.get(f"/api/v1/models/{agent['id']}/replays").json()["items"]
            timed_replay = next(row for row in rows if row["seed"] == 43)
            replay = client.get(f"/api/v1/models/{agent['id']}/replays/{timed_replay['id']}")
            replay.raise_for_status()
            metadata = replay.json()["metadata"]
            assert metadata["gameStatus"] == "error" and metadata["outcome"]["winner"] is None
            assert "timed out after 5 seconds" in metadata["captureError"], metadata
            assert metadata["playSettings"]["humanDecisionTimeoutSeconds"] == 5
            stats = client.get("/api/v1/statistics/local").json()["summary"]
            assert stats["matches"] == 2 and stats["seatResults"] == 4
            session_id = (timed_job.get("details") or {}).get("sessionId")
            if session_id:
                httpx.delete(
                    f"{instance['engineUrl']}/game/sessions/{session_id}", timeout=10
                ).raise_for_status()
            report["humanInactivityLimitSeconds"] = 5
            report["recordedReplays"] = len(rows)
            print(
                "Explicit human inactivity limit: enforced by the real Engine, trace saved, no false win/loss.",
                flush=True,
            )
            if arguments.browser:
                browser_env = {
                    **environment,
                    "DEEPDECK_QA_APP_URL": instance["url"],
                    "DEEPDECK_QA_LOCAL_PLAY": "1",
                    "DEEPDECK_QA_LOCAL_DIAGNOSTICS": "1",
                    "DEEPDECK_QA_GLOBAL_COPILOT": "1",
                }
                subprocess.run(
                    [sys.executable, str(Path(__file__).with_name("verify_player_app.py"))],
                    cwd=Path(__file__).resolve().parents[1],
                    env=browser_env,
                    check=True,
                    timeout=180,
                )
                report["browserPlayerFlow"] = True
                report["browserProtectedLocalJournal"] = True
                report["browserGlobalCopilotAndFavicon"] = True
                report["recordedReplays"] = len(
                    client.get(f"/api/v1/models/{agent['id']}/replays").json()["items"]
                )
            # Exercise the player's recovery button without touching a shared
            # Engine: terminate only the child inside this fresh extraction.
            owned_engines = [
                child
                for child in psutil.Process(instance["pid"]).children(recursive=True)
                if child.name().casefold() == "mtg-engine-server.exe"
                and Path(child.exe()).resolve().is_relative_to(installation.resolve())
            ]
            assert len(owned_engines) == 1
            owned_engines[0].terminate()
            owned_engines[0].wait(timeout=10)
            assert client.get("/api/v1/status").json()["engine"]["healthy"] is False
            restarted = client.post("/api/v1/jobs", json={"kind": "dependency.engine.start"})
            restarted.raise_for_status()
            restart_job = restarted.json()
            assert Path(restart_job["argv"][0]).is_relative_to(installation)
            deadline = time.monotonic() + 30
            while not client.get("/api/v1/status").json()["engine"]["healthy"]:
                if time.monotonic() > deadline:
                    raise TimeoutError("The player could not restart its dedicated bundled Engine.")
                time.sleep(0.25)
            assert client.get(f"/api/v1/jobs/{restart_job['id']}").json()["status"] == "running"
            report["bundledEngineRecovery"] = True
            print(
                "Player recovery: bundled Engine restarted on its dedicated port without Git or Rust.",
                flush=True,
            )
    finally:
        if process.poll() is None:
            (profile / ".deepdeck" / "desktop-stop.request").touch()
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired as error:
                process.terminate()
                raise RuntimeError(
                    "The owned standalone process did not shut down cleanly."
                ) from error
    assert process.returncode == 0
    if arguments.native:
        native = subprocess.Popen(
            [
                str(executable),
                "--data-dir",
                str(profile),
                "--port",
                "0",
                "--engine-port",
                str(engine_port),
                "--native-smoke-test",
            ],
            cwd=scratch,
            env=environment,
            **flags,
        )
        try:
            native.wait(timeout=90)
        except subprocess.TimeoutExpired as error:
            native.terminate()
            raise RuntimeError("The hidden native-window check timed out.") from error
        checked = json.loads((profile / ".deepdeck" / "native-check.json").read_text("utf-8"))
        assert native.returncode == 0 and checked["rendered"] and checked["tabs"] == 7, checked
        assert checked["tabLabels"] == [
            "Play",
            "Decks",
            "Agents",
            "League",
            "Competitions",
            "Replays",
            "Insights",
        ], checked
        report["nativeWindow"] = checked
    result_path = archive.parent / "verification.json"
    result_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    main()
