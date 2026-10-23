from __future__ import annotations

import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest

from deepdeck_learner.jobs import JobManager, JobValidationError
from deepdeck_learner.league_replays import LeagueReplayCollector, replay_inbox_status


def agent(root: Path):
    run = root / ".deepdeck" / "agents" / "pilot"
    run.mkdir(parents=True)
    (run / "local-model.json").write_text(
        json.dumps({"id": "pilot", "name": "Tempo pilot"}), encoding="utf-8"
    )
    return run


def match(game="game"):
    return {
        "matchId": "match",
        "status": "complete",
        "format": "legacy",
        "games": [{"id": game, "status": "complete"}],
    }


def trace(game="game"):
    return {
        "game": {"id": game, "status": "complete"},
        "decisions": [{"observableState": {"turnNumber": 1}}],
    }


@pytest.fixture
def inbox(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPDECK_PLATFORM_URL", "https://league.example.test/api/v1")
    monkeypatch.setenv("DEEPDECK_API_KEY", "fixture-only-key")
    run = agent(tmp_path)
    collector = LeagueReplayCollector(tmp_path, "pilot")
    collector.record_match(match())
    return collector, run


def test_status_is_read_only_and_independent_of_job_diagnostics(inbox, monkeypatch):
    collector, run = inbox
    before = collector.database.read_bytes()

    def no_network(*_args, **_kwargs):
        raise AssertionError("Status must not connect or deploy.")

    monkeypatch.setattr(httpx, "Client", no_network)
    report = replay_inbox_status(collector.root)["items"][0]
    assert report["pending"] == report["currentServerPending"] == 1 and report["collected"] == 0
    assert report["name"] == "Tempo pilot" and report["lastAttemptAtUnixMs"] is None
    assert collector.database.read_bytes() == before
    (run / "local-model.json").unlink()
    assert replay_inbox_status(collector.root)["items"][0]["available"] is False


def test_missing_inbox_is_not_created_by_read(tmp_path):
    assert replay_inbox_status(tmp_path)["items"] == []
    assert not (tmp_path / ".deepdeck").exists()


def test_backlog_attempt_order_does_not_starve_newer_replays(inbox):
    collector, _ = inbox
    for i in range(1, 51):
        collector.record_match(match(f"game-{i}"))
    requests = []

    def respond(request):
        requests.append(request.url.path)
        return httpx.Response(404)

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        collector.collect(client=client)
        assert len(requests) == 50
        collector.collect(client=client, limit=1)
    assert requests[-1].endswith("/game-50/replay")


def test_claim_prevents_simultaneous_collectors_from_downloading_same_game(inbox):
    collector, _ = inbox
    second = LeagueReplayCollector(collector.root, "pilot")
    started, release = threading.Event(), threading.Event()
    requests = []

    def respond(request):
        requests.append(request)
        started.set()
        assert release.wait(10)
        return httpx.Response(200, json={"data": trace()})

    with (
        httpx.Client(transport=httpx.MockTransport(respond)) as client,
        ThreadPoolExecutor(max_workers=1) as executor,
    ):
        first = executor.submit(collector.collect, client=client)
        assert started.wait(10)
        try:
            assert second.collect(client=client)["pending"] == 1
            assert len(requests) == 1
        finally:
            release.set()
        assert first.result(10)["collected"] == 1


def test_crashed_claim_expires_and_success_compacts_snapshot_without_redownloading_evicted_game(
    inbox,
):
    collector, run = inbox
    with sqlite3.connect(collector.database) as db:
        db.execute("UPDATE replay_inbox SET claim_token='crashed',claim_expires_at=1")
    requests = []
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: requests.append(request) or httpx.Response(200, json={"data": trace()})
        )
    ) as client:
        assert collector.collect(client=client)["collected"] == 1
        with sqlite3.connect(collector.database) as db:
            assert db.execute("SELECT snapshot,claim_token FROM replay_inbox").fetchone() == (
                "{}",
                None,
            )
        (run / "replays" / "league-game.json").unlink()
        collector.collect(client=client)
    assert len(requests) == 1
    assert replay_inbox_status(collector.root)["items"][0]["collected"] == 1


def test_wrong_server_never_gets_the_original_replay_request_or_overwrites_identity(
    inbox, monkeypatch
):
    collector, _ = inbox
    monkeypatch.setenv("DEEPDECK_PLATFORM_URL", "https://other.example.test/api/v1")
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _: (_ for _ in ()).throw(AssertionError("Wrong server requested"))
        )
    ) as client:
        assert collector.collect(client=client)["pending"] == 1
    item = replay_inbox_status(collector.root)["items"][0]
    assert item["currentServerPending"] == 0 and item["otherServerPending"] == 1
    with pytest.raises(ValueError, match="different League"):
        collector.record_match(match())


def test_legacy_schema_migration_infers_only_recorded_server_and_keeps_unknown_origins_unassigned(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("DEEPDECK_PLATFORM_URL", "https://league.example.test/api/v1")
    agent(tmp_path)
    path = tmp_path / ".deepdeck" / "league-replays" / "collector.db"
    path.parent.mkdir()
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE replay_inbox (model_id TEXT,game_id TEXT,snapshot TEXT,status TEXT,last_error TEXT,collected_at INTEGER,PRIMARY KEY(model_id,game_id))"
        )
        db.execute(
            "INSERT INTO replay_inbox VALUES (?,?,?,'pending',NULL,NULL)",
            (
                "pilot",
                "known",
                json.dumps(
                    {**match("known"), "watchUrl": "https://league.example.test/matches?match=old"}
                ),
            ),
        )
        db.execute(
            "INSERT INTO replay_inbox VALUES (?,?,?,'pending',NULL,NULL)",
            ("pilot", "unknown", json.dumps(match("unknown"))),
        )
    before = path.read_bytes()
    item = replay_inbox_status(tmp_path)["items"][0]
    assert (
        item["currentServerPending"] == 1
        and item["unknownServerPending"] == 1
        and path.read_bytes() == before
    )
    collector = LeagueReplayCollector(tmp_path, "pilot")
    with sqlite3.connect(collector.database) as db:
        assert (
            db.execute("SELECT source_url FROM replay_inbox WHERE game_id='unknown'").fetchone()[0]
            is None
        )


def test_recovery_job_collects_without_a_subprocess_or_agent_connection(inbox, monkeypatch):
    collector, run = inbox
    manager = JobManager(collector.root)
    original = LeagueReplayCollector.collect
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"data": trace()}))
    ) as client:
        monkeypatch.setattr(
            LeagueReplayCollector,
            "collect",
            lambda self, **kwargs: original(self, client=client, **kwargs),
        )
        monkeypatch.setattr(manager, "_run", lambda job: None)
        request = manager.create({"kind": "replays.collect", "model_id": "pilot"})
        assert request["argv"] == [] and request["worker_slots"] == 0
        with pytest.raises(JobValidationError, match="already running"):
            manager.create({"kind": "replays.collect", "model_id": "pilot"})
        job = manager._jobs[request["id"]]
        JobManager._run(manager, job)
    assert job.status == "completed" and job.details["replaySync"]["pending"] == 0
    assert (run / "replays" / "league-game.json").is_file()
    assert all(item["kind"] == "replays.collect" for item in manager.list_jobs())


def test_recovery_requires_enabled_collection_and_authenticated_current_server(inbox, monkeypatch):
    collector, _ = inbox
    manager = JobManager(collector.root)
    monkeypatch.delenv("DEEPDECK_API_KEY")
    with pytest.raises(JobValidationError, match="Connect"):
        manager.create({"kind": "replays.collect", "model_id": "pilot"})
    monkeypatch.setenv("DEEPDECK_API_KEY", "fixture")
    from deepdeck_learner.experience import save_experience_settings

    save_experience_settings(collector.root, {"autoCollectLeagueReplays": False})
    with pytest.raises(JobValidationError, match="Resume"):
        manager.create({"kind": "replays.collect", "model_id": "pilot"})


def test_inbox_http_status_and_recovery_authorization(inbox, monkeypatch):
    from fastapi.testclient import TestClient

    from deepdeck_learner.app import create_app

    collector, _ = inbox
    app = create_app(collector.root)
    monkeypatch.setattr(app.state.manager, "_run", lambda job: None)
    client = TestClient(app)
    assert client.get("/api/v1/replay-inbox").json()["items"][0]["pending"] == 1
    route = "/api/v1/replay-inbox/pilot/collect"
    assert client.post(route).status_code == 403
    token = {"X-DeepDeck-Token": client.get("/api/v1/session").json()["token"]}
    response = client.post(route, headers=token)
    assert response.status_code == 202 and response.json()["kind"] == "replays.collect"
    assert response.json()["argv"] == []


def test_graceful_recovery_stop_finishes_only_current_download(inbox):
    collector, _ = inbox
    collector.record_match(match("next-game"))
    stopped = [False]
    requests = []

    def respond(request):
        requests.append(request)
        stopped[0] = True
        return httpx.Response(200, json={"data": trace()})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        report = collector.collect(client=client, stop_requested=lambda: stopped[0])
    assert len(requests) == 1 and report["collected"] == 1 and report["pending"] == 1


def test_live_process_claim_is_not_stolen_when_download_outlasts_lease(inbox):
    import os

    import psutil

    collector, _ = inbox
    with sqlite3.connect(collector.database) as db:
        db.execute(
            "UPDATE replay_inbox SET claim_token='slow-live',claim_expires_at=1,claim_owner_pid=?,claim_owner_started=?",
            (os.getpid(), psutil.Process().create_time()),
        )
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _: (_ for _ in ()).throw(AssertionError("Live download was stolen"))
        )
    ) as client:
        assert collector.collect(client=client)["pending"] == 1
