from __future__ import annotations

import json
import os
import time
from pathlib import Path

import httpx

from deepdeck_examples.run import _league_match_snapshot
from deepdeck_learner.experience import enforce_replay_quota, replay_storage_used
from deepdeck_learner.league_replays import LeagueReplayCollector, replay_for_player
from deepdeck_learner.training_platform import list_saved_replays, load_saved_replay


def _agent(root: Path) -> Path:
    run = root / ".deepdeck" / "agents" / "pilot"
    run.mkdir(parents=True)
    (run / "local-model.json").write_text(json.dumps({"id": "pilot"}), encoding="utf-8")
    return run


def _trace() -> dict:
    return {
        "schemaVersion": "mtg-pixi-replay/v2",
        "spectator": False,
        "game": {"id": "game-1", "status": "complete", "number": 1},
        "initialObservation": {"turnNumber": 1, "players": [{"id": "a", "life": 20}]},
        "frames": [
            {"sequenceNumber": 5, "seat": 0, "observationDelta": {"patch": {"turnNumber": 2}}},
            {"sequenceNumber": 1, "seat": 0, "observationDelta": {"patch": {}}},
            {"sequenceNumber": 8, "seat": 1, "observationDelta": {"patch": {}}},
            {"sequenceNumber": 9, "seat": 1, "observationDelta": {"patch": {"temporary": None}}},
        ],
        "checkpoints": [
            {
                "sequenceNumber": 8,
                "observation": {
                    "turnNumber": 4,
                    "temporary": True,
                    "players": [{"id": "b", "life": 3}],
                },
            }
        ],
        "terminalObservation": {
            "turnNumber": 5,
            "players": [{"id": "b", "life": 0}],
            "outcome": {"winner": "a"},
        },
        "cardCatalog": {
            "card-art:island": {"name": "Island", "imageUrl": "https://example.test/island.jpg"}
        },
    }


def _match() -> dict:
    return {
        "matchId": "match-1",
        "status": "complete",
        "format": "legacy",
        "games": [{"id": "game-1", "status": "complete", "number": 1}],
        "decks": [{"versionId": "deck-1", "name": "Tempo"}],
    }


def test_replay_adapter_reconstructs_deltas_checkpoints_and_terminal_without_aliases() -> None:
    trace = _trace()
    result = replay_for_player(
        {"schemaVersion": "deepdeck-league-replay/v1", "id": "test", "trace": trace}
    )
    frames = result["frames"]
    assert [frame["state"]["turnNumber"] for frame in frames] == [1, 2, 4, 4, 5]
    assert frames[0]["state"]["players"][0]["life"] == 20
    assert frames[2]["state"]["players"][0]["id"] == "b"
    assert frames[2]["perspectiveSeat"] == 1
    assert "temporary" not in frames[3]["state"]
    assert frames[2]["state"]["temporary"] is True
    assert frames[-1]["state"]["outcome"]["winner"] == "a"
    assert result["cardCatalog"] == trace["cardCatalog"]
    assert trace["initialObservation"]["turnNumber"] == 1


def test_collection_retries_missing_history_survives_restart_and_does_not_redownload_evicted_games(
    tmp_path: Path,
) -> None:
    run = _agent(tmp_path)
    collector = LeagueReplayCollector(tmp_path, "pilot")
    assert collector.record_match(_match()) is True
    assert collector.record_match(_match()) is False
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(404 if len(requests) == 1 else 200, json={"data": _trace()})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        assert collector.collect(client=client)["pending"] == 1
        assert collector.status()["lastError"]
        collector = LeagueReplayCollector(tmp_path, "pilot")
        status = collector.collect(client=client)
        assert status["collected"] == 1
        assert status["pending"] == 0
        path = run / "replays" / "league-game-1.json"
        cached = json.loads(path.read_text(encoding="utf-8"))
        assert cached["schemaVersion"] == "deepdeck-league-replay/v1"
        assert "frames" not in cached
        assert cached["trace"]["initialObservation"]["turnNumber"] == 1
        assert list_saved_replays(tmp_path, "pilot")[0]["frameCount"] == 5
        replay = load_saved_replay(tmp_path, "pilot", "league-game-1")
        assert replay["schemaVersion"] == "deepdeck-replay/v1"
        assert replay["metadata"]["deckNames"] == ["Tempo"]
        path.unlink()  # Simulates an old game retired by quota.
        collector.collect(client=client)
    assert len(requests) == 2


def test_collector_waits_for_final_history_and_honors_disabled_collection(tmp_path: Path) -> None:
    run = _agent(tmp_path)
    collector = LeagueReplayCollector(tmp_path, "pilot")
    collector.record_match(_match())
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"data": {"spectator": True}})
        )
    ) as client:
        assert collector.collect(client=client)["pending"] == 1
    assert not list((run / "replays").glob("*.json"))
    (tmp_path / ".deepdeck" / "experience.json").write_text(
        json.dumps({"autoCollectLeagueReplays": False}), encoding="utf-8"
    )
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _: (_ for _ in ()).throw(AssertionError("No request expected"))
        )
    ) as client:
        assert collector.collect(client=client)["enabled"] is False


def test_quota_removes_oldest_games_but_preserves_pins_leases_and_collector_state(
    tmp_path: Path,
) -> None:
    run = _agent(tmp_path)
    replays = run / "replays"
    saved = run / "saved-replays"
    replays.mkdir()
    saved.mkdir()
    old = replays / "old.json"
    viewed = replays / "viewed.json"
    recent = replays / "recent.json"
    pinned = saved / "pinned.json"
    for index, path in enumerate((old, viewed, recent, pinned)):
        path.write_text("x" * 100, encoding="utf-8")
        os.utime(path, (index + 1, index + 1))
    lease = replays / ".leases" / "viewed"
    lease.mkdir(parents=True)
    (lease / "viewer.lease").write_text(str(int(time.time()) + 60), encoding="utf-8")
    state = tmp_path / ".deepdeck" / "league-replays" / "state.json"
    state.parent.mkdir()
    state.write_text("do not remove", encoding="utf-8")
    (tmp_path / ".deepdeck" / "experience.json").write_text(
        json.dumps({"replayStorageLimitBytes": 200}), encoding="utf-8"
    )
    assert replay_storage_used(tmp_path) == 400
    result = enforce_replay_quota(tmp_path)
    assert result["usedBytes"] == 200
    assert viewed.is_file() and pinned.is_file() and state.is_file()
    assert not old.exists() and not recent.exists()


def test_match_snapshot_keeps_every_game_in_best_of_match() -> None:
    match = {
        "summary": {"status": "running", "participants": [], "format": "legacy"},
        "games": [{"id": "one", "status": "complete"}, {"id": "two", "status": "running"}],
    }
    snapshot = _league_match_snapshot("match-1", match, None)
    assert snapshot["watchUrl"].startswith("https://www.deepdeckleague.com/")
    assert snapshot["gameId"] == "two"
    assert snapshot["games"][0]["id"] == "one"
    assert snapshot["format"] == "legacy"
