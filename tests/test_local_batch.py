from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from deepdeck_agent import Agent, Decision, Game

from deepdeck_examples import local_batch
from deepdeck_examples.local_batch import (
    DecisionRecorder,
    RecordingAgent,
    _one_game,
    _selected_match_spec,
    _spectator_event_summaries,
    _terminal,
    compact_view,
)
from deepdeck_learner.agent_studio import create_agent
from deepdeck_learner.jobs import Job, JobManager, JobValidationError
from deepdeck_learner.models import deck_statistics


def test_random_agent_pool_freezes_all_eligible_mastered_decks_and_varies_per_match(
    tmp_path, monkeypatch
):
    import random

    for deck_id in ("a", "b", "c"):
        _deck(tmp_path, deck_id, deck_id)
    agents = [
        create_agent(
            tmp_path, {"name": f"Pilot {deck_id}", "format": "legacy", "deckVersionIds": [deck_id]}
        )
        for deck_id in ("a", "b", "c")
    ]
    manager = JobManager(tmp_path)
    monkeypatch.setattr(manager, "_refresh_playtest_deck_rules", lambda _, __, cards: cards)
    request = {
        "format": "legacy",
        "games": 20,
        "seed": 42,
        "seats": [{"kind": "agent", "agent_id": "random", "deck_id": "random"}] * 2,
    }
    _, _, artifact = manager._batch_command(request)
    spec = json.loads((artifact / "batch-spec.json").read_text())
    assert spec["selectionVersion"] == "per-match/v1" and spec["firstSeed"] == 42
    assert len(spec["seats"][0]["agentPool"]) == 3
    for candidate in spec["seats"][0]["agentPool"]:
        policy = next(agent for agent in agents if agent["id"] == candidate["agentId"])
        assert [deck["id"] for deck in candidate["decks"]] == policy["deckVersionIds"]

    def draws():
        return [
            tuple(
                seat["agentId"]
                for seat in _selected_match_spec(spec, random.Random(42 + index))["seats"]
            )
            for index in range(20)
        ]

    assert draws() == draws() and len(set(draws())) > 1
    assert set().union(*(set(pair) for pair in draws())) == {agent["id"] for agent in agents}
    assert set(request["_job_details"]["seats"][0]["agentCandidates"]) == {
        agent["id"] for agent in agents
    }


@pytest.mark.asyncio
async def test_random_batch_worker_draws_each_match_and_connects_only_used_agents_once(
    tmp_path, monkeypatch
):
    directory = tmp_path / ".deepdeck" / "batches" / "abcdef0123456789"
    directory.mkdir(parents=True)
    pool = [
        {
            "agentId": name,
            "name": name,
            "policyPath": name,
            "randomDeck": True,
            "decks": [{"id": name}],
        }
        for name in ("one", "two", "three")
    ]
    spec = {
        "id": directory.name,
        "seed": 42,
        "firstSeed": 42,
        "selectionVersion": "per-match/v1",
        "format": "legacy",
        "games": 12,
        "seats": [{**pool[0], "agentPool": pool}] * 2,
    }
    spec_path = directory / "batch-spec.json"
    spec_path.write_text(json.dumps(spec))
    connected, played = [], []

    async def serve():
        await asyncio.Event().wait()

    async def ready(**_kwargs):
        pass

    def runner(path, *_args, **_kwargs):
        connected.append(str(path))
        return SimpleNamespace(serve=serve, wait_until_connected=ready)

    async def one_game(_client, _root, _batch, index, match_spec, runners, rng, _recorder):
        selected = tuple(seat["agentId"] for seat in match_spec["seats"])
        assert all(agent_id in runners for agent_id in selected)
        played.append(selected)
        return {
            "replayId": f"recording-{index}",
            "decks": [deck["id"] for deck in local_batch._selected_decks(match_spec, rng)],
        }

    monkeypatch.setattr(local_batch, "_runner", runner)
    monkeypatch.setattr(local_batch, "_one_game", one_game)
    await local_batch.run(
        argparse.Namespace(root=tmp_path, spec=spec_path, engine_url="http://127.0.0.1:8787")
    )
    assert len(played) == 12 and len(set(played)) > 1
    assert sorted(connected) == ["one", "three", "two"]
    assert json.loads((directory / "summary.json").read_text())["completed"] == 12


def test_closing_a_local_table_stops_only_its_own_job(tmp_path, monkeypatch) -> None:
    manager = JobManager(tmp_path)
    monkeypatch.setattr(
        manager,
        "games",
        lambda: [
            {
                "id": "owned",
                "sessionId": "owned",
                "source": "local",
                "jobId": "job",
                "canCancel": True,
            }
        ],
    )
    stopped = []
    monkeypatch.setattr(manager, "stop", lambda job_id: stopped.append(job_id))
    deleted = []
    monkeypatch.setattr(httpx, "delete", lambda *args, **kwargs: deleted.append(args))
    assert manager.cancel_game("owned")["status"] == "cancelled"
    assert stopped == ["job"] and deleted == []


def test_cancelling_an_unattached_session_uses_only_the_configured_local_engine(
    tmp_path, monkeypatch
) -> None:
    manager = JobManager(tmp_path)
    game = {"id": "owned", "sessionId": "owned", "source": "training", "canCancel": True}
    monkeypatch.setattr(manager, "games", lambda: [game])
    monkeypatch.setenv("DEEPDECK_LOCAL_ENGINE_URL", "http://127.0.0.1:8897/")
    deleted = []

    def delete(url, **_kwargs):
        deleted.append(url)
        return httpx.Response(200)

    monkeypatch.setattr(httpx, "delete", delete)
    assert manager.cancel_game("owned")["canCancel"] is False
    assert deleted == ["http://127.0.0.1:8897/game/sessions/owned"]
    for update in (
        {"source": "league"},
        {"canCancel": False},
        {"engineUrl": "https://example.test"},
    ):
        monkeypatch.setattr(manager, "games", lambda update=update: [{**game, **update}])
        with pytest.raises(JobValidationError):
            manager.cancel_game("owned")
    assert len(deleted) == 1


def _deck(root: Path, deck_id: str, name: str, game_format: str = "legacy") -> None:
    path = root / ".deepdeck" / "decks" / f"{deck_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "id": deck_id,
                "name": name,
                "version": 1,
                "format": game_format,
                "colors": ["U"],
                "cards": [
                    {
                        "cardId": f"{deck_id}-island",
                        "name": "Island",
                        "typeLine": "Basic Land — Island",
                        "quantity": 60,
                        "section": "main",
                        "rules": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def test_batch_command_materializes_every_agent_seat(tmp_path: Path, monkeypatch) -> None:
    _deck(tmp_path, "tempo-v1", "Tempo")
    _deck(tmp_path, "control-v1", "Control")
    tempo = create_agent(
        tmp_path,
        {"name": "Tempo Pilot", "format": "legacy", "deckVersionIds": ["tempo-v1"]},
    )
    control = create_agent(
        tmp_path,
        {"name": "Control Pilot", "format": "legacy", "deckVersionIds": ["control-v1"]},
    )
    manager = JobManager(tmp_path)
    monkeypatch.setattr(manager, "_refresh_playtest_deck_rules", lambda _, __, cards: cards)
    raw = {
        "kind": "playtest.batch",
        "format": "legacy",
        "games": 12,
        "engine_url": "http://127.0.0.1:8787",
        "seats": [
            {"agent_id": tempo["id"], "deck_id": "random"},
            {"agent_id": control["id"], "deck_id": "control-v1"},
        ],
    }

    argv, label, artifact = manager._batch_command(raw)  # noqa: SLF001

    spec = json.loads((artifact / "batch-spec.json").read_text(encoding="utf-8"))
    assert argv[:3] == [argv[0], "-m", "deepdeck_examples.local_batch"]
    assert label == "12 game legacy AI batch"
    assert spec["games"] == 12
    assert spec["seats"][0]["randomDeck"] is True
    assert spec["seats"][1]["decks"][0]["id"] == "control-v1"
    assert len(spec["seats"][0]["decks"][0]["cards"]) == 60
    assert raw["_job_details"]["total"] == 12
    assert spec["gameTimeoutSeconds"] == 600
    assert spec["seats"][0]["policyRevision"] == tempo["revision"]


def test_commander_table_prepares_one_human_and_three_agent_seats(
    tmp_path: Path, monkeypatch
) -> None:
    _deck(tmp_path, "commander", "Commander", "commander")
    agent = create_agent(
        tmp_path,
        {"name": "Commander Pilot", "format": "commander", "deckVersionIds": ["commander"]},
    )
    manager = JobManager(tmp_path)
    monkeypatch.setattr(manager, "_refresh_playtest_deck_rules", lambda _, __, cards: cards)
    raw = {
        "format": "commander",
        "seats": [{"kind": "human", "deck_id": "commander"}]
        + [{"kind": "agent", "agent_id": agent["id"], "deck_id": "random"} for _ in range(3)],
    }
    _, label, artifact = manager._batch_command(raw, interactive=True)  # noqa: SLF001
    spec = json.loads((artifact / "batch-spec.json").read_text(encoding="utf-8"))
    assert label == "Local commander table"
    assert spec["interactive"] is True and spec["games"] == 1
    assert spec["gameTimeoutSeconds"] is None
    assert spec["seats"][0]["kind"] == "human"
    assert spec["seats"][0]["agentId"] == "local-player"
    assert all(seat["agentId"] == agent["id"] for seat in spec["seats"][1:])
    assert not (tmp_path / ".deepdeck" / "agents" / "local-player").exists()


@pytest.mark.parametrize("game_format,count", [("legacy", 2), ("commander", 4)])
def test_all_human_tables_prepare_unique_seats_without_an_agent(
    tmp_path, monkeypatch, game_format, count
):
    _deck(tmp_path, "local-deck", "Local deck", game_format)
    manager = JobManager(tmp_path)
    refreshed = []

    def refresh(_engine_url, deck_id, cards):
        refreshed.append(deck_id)
        return cards

    monkeypatch.setattr(manager, "_refresh_playtest_deck_rules", refresh)
    raw = {
        "format": game_format,
        "seats": [{"kind": "human", "deck_id": "local-deck"} for _ in range(count)],
    }
    _, _, artifact = manager._batch_command(raw, interactive=True)
    spec = json.loads((artifact / "batch-spec.json").read_text())
    assert len({seat["agentId"] for seat in spec["seats"]}) == count
    assert [seat["name"] for seat in spec["seats"]] == [f"Player {i}" for i in range(1, count + 1)]
    assert spec["gameTimeoutSeconds"] is None
    assert not (tmp_path / ".deepdeck" / "agents").exists()
    assert refreshed == ["local-deck"]
    with pytest.raises(JobValidationError, match="AI batches require only AI"):
        manager._batch_command(raw, interactive=False)


@pytest.mark.parametrize("game_format, seat_count", [("legacy", 2), ("commander", 4)])
def test_old_programmatic_playtest_request_uses_the_recorded_table_runtime(
    tmp_path, monkeypatch, game_format, seat_count
) -> None:
    _deck(tmp_path, "ai-deck", "AI deck", game_format)
    _deck(tmp_path, "human-deck", "Player deck outside the AI pool", game_format)
    agent = create_agent(
        tmp_path, {"name": "Old link pilot", "format": game_format, "deckVersionIds": ["ai-deck"]}
    )
    manager = JobManager(tmp_path)
    monkeypatch.setattr(manager, "_refresh_playtest_deck_rules", lambda _, __, cards: cards)
    monkeypatch.setattr(manager, "_run", lambda job: None)
    job = manager.create(
        {
            "kind": "playtest.agent",
            "model_id": agent["id"],
            "format": game_format,
            "deck_version_id": "human-deck",
            "opponent_deck_version_id": "random",
        }
    )
    assert job["kind"] == "playtest.table"
    assert "deepdeck_examples.local_batch" in job["argv"]
    spec = json.loads((Path(job["artifact_path"]) / "batch-spec.json").read_text())
    assert spec["interactive"] is True and len(spec["seats"]) == seat_count
    assert spec["seats"][0]["decks"][0]["id"] == "human-deck"
    assert all([deck["id"] for deck in seat["decks"]] == ["ai-deck"] for seat in spec["seats"][1:])


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["stop", "timeout", "network"])
async def test_partial_replays_are_saved_without_fabricating_results(tmp_path, failure) -> None:
    import random

    batch_id = "abc123"
    directory = tmp_path / ".deepdeck" / "batches" / batch_id
    directory.mkdir(parents=True)
    if failure == "stop":
        (directory / "stop.request").touch()
    seats = [
        {
            "kind": "human",
            "agentId": "local-player",
            "name": "You",
            "decks": [{"id": "human-deck", "name": "Human deck", "cards": []}],
        },
        {
            "kind": "agent",
            "agentId": "pilot",
            "name": "Pilot",
            "decks": [{"id": "ai-deck", "name": "AI deck", "cards": []}],
        },
    ]
    # One round won is not a won match. Preserve the actual board but keep the
    # unfinished replay's overall winner empty and write no statistics rows.
    view = {
        "sessionId": "owned",
        "revision": 8,
        "state": {"turnNumber": 5, "players": [], "outcome": {"winner": "local-human"}},
        "matchState": {"phase": "sideboarding", "winnerPlayerId": None},
    }
    requests = []

    def respond(request):
        requests.append(request)
        if request.method == "GET" and failure == "network":
            raise httpx.ConnectError("The Engine disconnected", request=request)
        return httpx.Response(200, json=view)

    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:8787", transport=httpx.MockTransport(respond)
    ) as client:
        game = _one_game(
            client,
            tmp_path,
            batch_id,
            1,
            {
                "format": "legacy",
                "interactive": True,
                "seed": 1,
                "seats": seats,
                "gameTimeoutSeconds": 0 if failure == "timeout" else 10,
            },
            {"pilot": SimpleNamespace(controller_id="pilot-controller")},
            random.Random(1),
        )
        if failure == "stop":
            result = await game
            assert result["status"] == "stopped" and result["winner"] is None
            assert requests[-1].method == "DELETE"
        else:
            with pytest.raises(TimeoutError if failure == "timeout" else httpx.ConnectError):
                await game
    replay = json.loads(
        next((tmp_path / ".deepdeck" / "agents" / "pilot" / "replays").glob("*.json")).read_text()
    )
    assert (
        replay["metadata"]["gameStatus"]
        == {"stop": "interrupted", "timeout": "timeout", "network": "error"}[failure]
    )
    assert replay["metadata"]["outcome"]["winner"] is None
    if failure != "stop":
        assert replay["metadata"]["captureError"] == (
            "The local table reached its configured match safety limit."
            if failure == "timeout"
            else "The Engine disconnected"
        )
    assert replay["frames"][0]["state"]["turnNumber"] == 5
    assert not list((tmp_path / ".deepdeck").rglob("local-results.jsonl"))


@pytest.mark.asyncio
async def test_interactive_session_routes_controllers_records_both_sides_and_keeps_final_board(
    tmp_path: Path,
) -> None:
    import random

    requests = []
    seats = [
        {
            "kind": "human",
            "agentId": "local-player",
            "name": "You",
            "decks": [{"id": "human-deck", "name": "Human", "cards": []}],
        },
        {
            "kind": "agent",
            "agentId": "pilot",
            "name": "Pilot",
            "decks": [{"id": "ai-deck", "name": "AI", "cards": []}],
        },
    ]
    final = {
        "sessionId": "session:1",
        "revision": 8,
        "state": {
            "status": "complete",
            "turnNumber": 5,
            "players": [],
            "outcome": {"winner": "batch-seat-2"},
        },
    }

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json=final)

    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:8787", transport=httpx.MockTransport(respond)
    ) as client:
        result = await _one_game(
            client,
            tmp_path,
            "interactive",
            1,
            {"format": "commander", "interactive": True, "seed": 1, "seats": seats},
            {"pilot": SimpleNamespace(controller_id="agent:pilot")},
            random.Random(1),
        )
    payload = json.loads(requests[0].content)
    assert payload["humanPlayerIds"] == ["local-human"]
    assert payload["recordAnalytics"] is False
    assert payload["aiControllerByPlayerId"] == {"batch-seat-2": "agent:pilot"}
    assert [request.method for request in requests] == ["POST", "GET"]
    assert requests[-1].url.path.endswith("/spectator/0")
    # Spectator capture is read-only; the board remains open until the user closes it.
    assert result["winner"] == "batch-seat-2"
    from deepdeck_learner.statistics_store import stored_rows

    rows = stored_rows(tmp_path)
    human_result = next(row for row in rows if row["actorKind"] == "player")
    ai_result = next(row for row in rows if row["actorKind"] == "agent")
    assert len(rows) == 2
    assert not list((tmp_path / ".deepdeck").rglob("local-results.jsonl"))
    assert human_result["result"] == "loss" and human_result["actorKind"] == "player"
    assert ai_result["result"] == "win"
    replay = json.loads(
        next((tmp_path / ".deepdeck" / "agents" / "pilot" / "replays").glob("*.json")).read_text()
    )
    assert replay["id"] == result["replayId"]
    assert replay["metadata"]["source"] == "local-table"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["complete", "gap", "unsupported", "malformed", "offline"])
async def test_authoritative_spectator_capture_is_read_only_and_keeps_all_hands(tmp_path, mode):
    import random

    seats = [
        {
            "kind": "agent",
            "agentId": key,
            "name": key,
            "decks": [{"id": key, "name": key, "cards": []}],
        }
        for key in ("alpha", "beta")
    ]
    final = {
        "sessionId": "owned-spectator",
        "revision": 9,
        "state": {
            "status": "complete",
            "turnNumber": 3,
            "players": [
                {
                    "id": "batch-seat-1",
                    "life": 20,
                    "library": [
                        {
                            "instanceId": "private-top",
                            "definition": {"id": "secret", "name": "Secret library card"},
                        }
                    ],
                    "sideboard": [
                        {"instanceId": "private-sideboard", "name": "Secret sideboard card"}
                    ],
                }
            ],
            "outcome": {"winner": "batch-seat-1"},
        },
    }
    calls = []

    def respond(request):
        calls.append(request)
        if "/spectator/" in request.url.path:
            if mode == "unsupported":
                return httpx.Response(404)
            if mode == "offline":
                raise httpx.ConnectError("Observer unavailable", request=request)
            if mode == "malformed":
                return httpx.Response(200, json=[])
            sequence = int(request.url.path.rsplit("/", 1)[-1]) + 1
            frame = {
                "sequence": sequence,
                "state": {
                    "turnNumber": sequence,
                    "players": [
                        {
                            "id": "batch-seat-1",
                            "hand": [
                                {
                                    "instanceId": "a",
                                    "definition": {"id": "ponder", "name": "Ponder"},
                                }
                            ],
                        },
                        {
                            "id": "batch-seat-2",
                            "hand": [
                                {"instanceId": "b", "definition": {"id": "daze", "name": "Daze"}}
                            ],
                        },
                    ],
                },
                "selectedAction": {"kind": "castSpell"},
            }
            return httpx.Response(
                200, json={"frames": [frame], "hasMore": sequence == 1, "truncated": mode == "gap"}
            )
        return httpx.Response(200, json=final)

    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:8787", transport=httpx.MockTransport(respond)
    ) as client:
        result = await _one_game(
            client,
            tmp_path,
            "capture",
            1,
            {"format": "legacy", "seed": 1, "seats": seats},
            {key: SimpleNamespace(controller_id=key) for key in ("alpha", "beta")},
            random.Random(1),
        )
    replay = json.loads(
        next((tmp_path / ".deepdeck" / "agents" / "alpha" / "replays").glob("*.json")).read_text()
    )
    assert result["winner"] == "batch-seat-1"
    assert calls[0].method == "POST" and calls[-1].method == "DELETE"
    assert all(request.method == "GET" for request in calls[1:-1])
    assert "spectator" not in json.loads(calls[0].content)  # No policy-input/schema change.
    if mode in {"complete", "gap"}:
        assert replay["metadata"]["spectatorCapture"] == (
            "partial" if mode == "gap" else "all-hands"
        )
        assert [f["spectatorSequence"] for f in replay["frames"]] == [1, 2, 3]
        assert [p["hand"][0]["name"] for p in replay["frames"][0]["state"]["players"]] == [
            "Ponder",
            "Daze",
        ]
        assert replay["frames"][0]["selectedAction"]["kind"] == "castSpell"
        for frame in replay["frames"]:
            for player in frame["state"]["players"]:
                assert all(card is None for card in player.get("library", []))
                assert all(card is None for card in player.get("sideboard", []))
    else:
        assert replay["metadata"]["spectatorCapture"] == "player-observations"
    assert replay["frames"][-1]["state"]["outcome"]["winner"] == "batch-seat-1"


@pytest.mark.asyncio
async def test_table_uses_actual_engine_inactivity_field_and_records_setup(tmp_path) -> None:
    import random

    seats = [
        {
            "kind": "human",
            "agentId": "local-player",
            "name": "You",
            "decks": [{"id": "human", "name": "Human", "cards": []}],
        },
        {
            "kind": "agent",
            "agentId": "pilot",
            "name": "Test pilot",
            "policyRevision": 7,
            "decks": [{"id": "ai", "name": "AI", "cards": []}],
        },
    ]
    final = {
        "sessionId": "recorded",
        "revision": 1,
        "state": {"status": "complete", "outcome": {"winner": "batch-seat-2"}, "players": []},
    }
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json=final)

    settings = {
        "firstSeed": 42,
        "startingSeat": 2,
        "maxTurns": 100,
        "humanDecisionTimeoutSeconds": 180,
        "matchTimeoutSeconds": None,
    }
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:8787", transport=httpx.MockTransport(respond)
    ) as client:
        await _one_game(
            client,
            tmp_path,
            "settings",
            1,
            {
                "format": "legacy",
                "seed": 42,
                "firstSeed": 42,
                "startingSeat": 2,
                "maxTurns": 100,
                "gameTimeoutSeconds": None,
                "humanDecisionTimeoutSeconds": 180,
                "playSettings": settings,
                "seats": seats,
            },
            {"pilot": SimpleNamespace(controller_id="controller")},
            random.Random(42),
        )
    payload = json.loads(requests[0].content)
    assert payload["seed"] == 42 and payload["setup"]["startingPlayer"] == 1
    assert payload["humanDecisionTimeoutMs"] == 180_000 and "decisionClock" not in payload
    replay = json.loads(
        next((tmp_path / ".deepdeck" / "agents" / "pilot" / "replays").glob("*.json")).read_text()
    )
    assert replay["metadata"]["playSettings"] == settings
    assert replay["metadata"]["seed"] == 42 and replay["metadata"]["startingSeat"] == 2
    assert replay["metadata"]["agentVersions"] == [
        {"agentId": "pilot", "name": "Test pilot", "revision": 7}
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("storage_available", [True, False])
async def test_engine_error_after_a_round_never_records_a_match_winner(
    tmp_path, capsys, monkeypatch, storage_available
) -> None:
    import random

    seats = [
        {
            "kind": "agent",
            "agentId": "pilot",
            "name": "Test pilot",
            "decks": [{"id": "deck", "name": "Deck", "cards": []}],
        }
    ] * 2
    final = {
        "sessionId": "error",
        "revision": 2,
        "error": "human decision timed out",
        "state": {"players": [], "outcome": {"winner": "batch-seat-1"}},
    }
    if not storage_available:
        from deepdeck_learner.experience import ReplayStorageError

        def full(*_args):
            raise ReplayStorageError("Favorites fill the recording quota.")

        monkeypatch.setattr("deepdeck_examples.local_batch.write_replay", full)
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:8787",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=final)),
    ) as client:
        expected_error = "replay saved" if storage_available else "could not be saved"
        with pytest.raises(RuntimeError, match=expected_error):
            await _one_game(
                client,
                tmp_path,
                "failed",
                1,
                {"format": "legacy", "seed": 1, "seats": seats},
                {"pilot": SimpleNamespace(controller_id="controller")},
                random.Random(1),
            )
    messages = capsys.readouterr().out.splitlines()
    marker = next(line for line in messages if line.startswith("DEEPDECK_RECORDING_STATUS "))
    status = json.loads(marker.removeprefix("DEEPDECK_RECORDING_STATUS "))
    assert status["saved"] is storage_available
    assert status["replayId"] == ("batch-failed-0001" if storage_available else None)
    assert status["gameStatus"] == "error"
    assert not any(line.startswith("DEEPDECK_BATCH_PROGRESS ") for line in messages)
    assert not list((tmp_path / ".deepdeck/statistics/matches").glob("*.json"))
    if not storage_available:
        assert not list((tmp_path / ".deepdeck/agents").rglob("*.json"))
        return
    replay = json.loads(
        next((tmp_path / ".deepdeck" / "agents" / "pilot" / "replays").glob("*.json")).read_text()
    )
    assert replay["metadata"]["outcome"]["winner"] is None
    assert replay["metadata"]["captureError"] == "human decision timed out"
    assert not list((tmp_path / ".deepdeck").rglob("local-results.jsonl"))


def test_batch_progress_marker_updates_public_job_details(tmp_path: Path) -> None:
    manager = JobManager(tmp_path)
    job = Job(
        id="batch-job",
        kind="playtest.batch",
        label="Batch",
        argv=[],
        details={"completed": 0, "total": 3},
    )

    manager._consume_process_marker(  # noqa: SLF001
        job,
        'DEEPDECK_BATCH_PROGRESS {"batchId":"abc","completed":1,"total":3,"winner":"batch-seat-1"}',
    )

    assert job.details is not None
    assert job.details["completed"] == 1
    assert job.details["recentGames"][0]["winner"] == "batch-seat-1"


@pytest.mark.asyncio
async def test_stopping_a_batch_keeps_its_completed_count_honest(tmp_path, monkeypatch) -> None:
    directory = tmp_path / ".deepdeck" / "batches" / "abcdef0123456789"
    directory.mkdir(parents=True)
    spec_path = directory / "batch-spec.json"
    spec_path.write_text(
        json.dumps(
            {
                "id": directory.name,
                "seed": 1,
                "games": 3,
                "format": "legacy",
                "seats": [{"agentId": "pilot", "policyPath": "unused"}] * 2,
            }
        )
    )

    async def serve():
        await asyncio.Event().wait()

    async def ready(**_kwargs):
        pass

    async def stopped_game(*args, **kwargs):
        (directory / "stop.request").touch()
        return {"sessionId": "owned", "replayId": "partial", "status": "stopped", "winner": None}

    monkeypatch.setattr(
        local_batch,
        "_runner",
        lambda *args, **kwargs: SimpleNamespace(serve=serve, wait_until_connected=ready),
    )
    monkeypatch.setattr(local_batch, "_one_game", stopped_game)
    await local_batch.run(
        argparse.Namespace(root=tmp_path, spec=spec_path, engine_url="http://127.0.0.1:8787")
    )
    summary = json.loads((directory / "summary.json").read_text())
    assert summary["status"] == "stopped" and summary["completed"] == 0
    assert summary["games"] == [] and summary["interruptedGame"]["replayId"] == "partial"


def test_batch_views_are_compact_and_terminal() -> None:
    live = {
        "schemaVersion": "mtg-game-session/v1",
        "revision": 7,
        "decision": {"id": "priority", "kind": "priority", "playerId": "seat-1"},
        "state": {
            "status": "playing",
            "turnNumber": 3,
            "players": [
                {
                    "id": "seat-1",
                    "name": "Tempo",
                    "life": 18,
                    "hand": [
                        {
                            "instanceId": "card-1",
                            "definition": {
                                "id": "brainstorm",
                                "name": "Brainstorm",
                                "oracleText": "Large text omitted from compact replay.",
                            },
                        }
                    ],
                }
            ],
            "events": [{"large": "history"}],
        },
    }

    compact = compact_view(live)
    assert compact["state"]["players"][0]["hand"][0]["name"] == "Brainstorm"
    assert "events" not in compact["state"]
    assert _terminal(live) is False
    live["decision"] = None
    live["state"]["status"] = "turnLimitReached"
    assert _terminal(live) is True


def test_spectator_event_summaries_keep_sound_and_stack_cursors_without_private_payloads():
    state = {
        "events": [
            {
                "sequence": 1,
                "kind": "cardDrawn",
                "cardInstanceId": "a",
                "detail": {"libraryOrder": ["secret"]},
            },
            {
                "sequence": 2,
                "kind": "triggeredAbilityPutOnStack",
                "playerId": "p1",
                "detail": {"stackId": "stack:2", "privateCards": ["secret"]},
            },
        ]
    }
    fresh = _spectator_event_summaries(state, 1)
    assert fresh == [
        {
            "sequence": 2,
            "kind": "triggeredAbilityPutOnStack",
            "playerId": "p1",
            "detail": {"stackId": "stack:2"},
        }
    ]
    assert _spectator_event_summaries(state, 2) == fresh  # Quiet frame retains cursor.
    fresh[0]["detail"]["stackId"] = "changed"
    assert state["events"][1]["detail"]["stackId"] == "stack:2"
    assert len(_spectator_event_summaries(state, 0)) == 2


def test_bare_copy_token_is_an_auxiliary_piece_not_a_commander_deck_card() -> None:
    assert JobManager._auxiliary_game_piece_flags(
        {"name": "Copy", "typeLine": "Token", "section": "main"}
    ) == (True, True)  # noqa: SLF001


def test_legacy_batch_waits_for_the_full_match_including_sideboarding() -> None:
    view = {
        "state": {"outcome": {"winner": "seat-1"}},
        "decision": None,
        "matchState": {"phase": "sideboarding", "winnerPlayerId": None},
    }
    assert _terminal(view) is False
    view["matchState"]["phase"] = "game"
    assert _terminal(view) is False
    view["matchState"].update(phase="complete", winnerPlayerId="seat-1")
    assert _terminal(view) is True


@pytest.mark.asyncio
async def test_sdk_recorder_captures_every_choice_with_independent_board_snapshots() -> None:
    recorder = DecisionRecorder()
    agent = RecordingAgent(Agent(), recorder)
    state = {
        "turnNumber": 1,
        "players": [{"id": "p1", "life": 20, "hand": [], "library": [None, None]}],
    }
    decision = Decision(
        "request-1",
        "p1",
        {
            "kind": "priority",
            "options": [{"id": "pass", "kind": "passPriority", "label": "Pass priority"}],
        },
        Game(state, "p1"),
    )
    response = await agent.make_decision(decision)
    assert response.action_id == "pass"
    state["turnNumber"] = 2
    await agent.make_decision(decision)
    assert [frame["state"]["turnNumber"] for frame in recorder.frames] == [1, 2]
    assert recorder.frames[0]["selectedAction"]["label"] == "Pass priority"
    assert len(recorder.frames[0]["state"]["players"][0]["library"]) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("interval,policy_duration", [(1, 0), (10, 0.002), (100, 0.2)])
async def test_local_decision_pacing_includes_computation_without_imposing_tiny_deadline(
    monkeypatch, interval, policy_duration
):
    from unittest.mock import AsyncMock

    ticks = iter(
        [
            0,
            0,
            policy_duration,
            policy_duration,
            policy_duration,
            max(interval / 1000, policy_duration),
        ]
    )
    monkeypatch.setattr(local_batch.time, "perf_counter", lambda: next(ticks))
    sleep = AsyncMock()
    monkeypatch.setattr(local_batch.asyncio, "sleep", sleep)
    recorder = DecisionRecorder()
    agent = RecordingAgent(Agent(), recorder, decision_interval_ms=interval)
    decision = Decision(
        "pacing",
        "p1",
        {"kind": "priority", "options": [{"id": "pass", "kind": "passPriority"}]},
        Game({"players": [{"id": "p1", "life": 20}]}, "p1"),
    )
    assert (await agent.make_decision(decision)).action_id == "pass"
    if policy_duration < interval / 1000:
        sleep.assert_awaited_once_with(pytest.approx(interval / 1000 - policy_duration))
    else:
        sleep.assert_not_awaited()
    assert recorder.frames[0]["agentDecision"]["status"] == "returned"


def test_programmatic_statistics_include_local_batch_results(tmp_path: Path) -> None:
    _deck(tmp_path, "tempo-v1", "Tempo")
    agent = create_agent(
        tmp_path,
        {"name": "Tempo Pilot", "format": "legacy", "deckVersionIds": ["tempo-v1"]},
    )
    result_path = tmp_path / ".deepdeck" / "agents" / agent["id"] / "local-results.jsonl"
    result_path.write_text(
        "\n".join(
            json.dumps({"deckVersionId": "tempo-v1", "result": result})
            for result in ("win", "win", "loss", "draw")
        )
        + "\n",
        encoding="utf-8",
    )

    statistic = deck_statistics(tmp_path)[0]
    assert statistic["ratingSystem"] == "local-results"
    assert statistic["matches"] == 4
    assert statistic["gameWins"] == 2
    assert statistic["gameLosses"] == 1
    assert statistic["draws"] == 1
