from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from deepdeck_learner.local_statistics import local_statistics
from deepdeck_learner.statistics_metrics import ProfileCollector, _snapshot, collect_profiles
from deepdeck_learner.statistics_store import (
    MIN_LIMIT,
    StatisticsStorageError,
    advanced_statistics,
    save_settings,
    storage_status,
    stored_rows,
    tracking_targets,
    write_match_results,
    write_result,
)


def frame(turn=1, round=1, events=None):
    state = {
        "turnNumber": turn,
        "players": [
            {
                "id": "p1",
                "life": 20,
                "hand": [None] * 7,
                "battlefield": [{"instanceId": "land", "typeLine": "Land", "tapped": False}],
            }
        ],
    }
    if events is not None:
        state["events"] = events
    return {"state": state, "matchState": {"gameNumber": round}}


def test_snapshot_handles_flat_nested_hidden_and_unknown_without_fake_counts():
    assert _snapshot(frame()["state"]["players"][0])["lands"] == 1
    assert _snapshot(
        {"battlefield": [{"definition": {"typeLine": "Artifact Creature — Golem"}}]}
    ) == {"lands": 0.0, "untappedLands": 0.0, "creatures": 1.0, "nonlandPermanents": 1.0}
    assert _snapshot({"battlefield": [{}]}) == {}
    assert "untappedLands" not in _snapshot({"battlefield": [{"typeLine": "Land"}]})


def test_observed_events_deduplicate_and_credit_damage_to_source_not_victim():
    targets = [
        {"playerId": "p1", "groups": ["resources", "board", "combat", "opening", "pace"]},
        {"playerId": "p2", "groups": ["combat"]},
    ]
    events = [
        {"sequence": 1, "kind": "cardDrawn", "playerId": "p1"},
        {"sequence": 2, "kind": "manaAdded", "playerId": "p1", "detail": {"mana": "U"}},
        {
            "sequence": 3,
            "kind": "damageDealt",
            "playerId": "p2",
            "detail": {"sourceControllerId": "p1", "amount": 3, "combat": False},
        },
        {"sequence": 4, "kind": "mulliganTaken", "playerId": "p1"},
    ]
    result = collect_profiles(
        [
            frame(events=events),
            frame(events=events),
            frame(5, events=events),
            frame(1, 2, events=events[:1]),
            frame(4, 1, events=events),
        ],
        targets,
        12,
    )
    totals = result["p1"]["totals"]
    assert totals["draws"] == 2
    assert totals["manaProduced"] == 1
    assert totals["damage"] == 3 and totals["nonCombatDamage"] == 3
    assert result["p2"]["totals"]["damage"] == 0
    assert totals["mulligans"] == 1
    assert totals["turnsObserved"] == 6
    assert len(result["p1"]["turns"]) == 3
    assert "hand" not in json.dumps(result)


def test_sampling_is_per_turn_not_ui_refresh_and_is_bounded():
    collector = ProfileCollector([{"playerId": "p1", "groups": ["board"]}])
    for _ in range(20):
        collector.observe(frame())
    assert len(collector.samples["p1"]) == 1
    for turn in range(2, 520):
        collector.observe(frame(turn))
    result = collector.finish(None)["p1"]
    assert len(result["turns"]) == 512
    assert result["droppedTurnSamples"] == 7


def test_no_missing_event_stream_zeroes_or_unconfirmed_choice_successes():
    target = [{"playerId": "p1", "groups": ["combat", "decisions"]}]
    f = frame()
    f.update(
        decision={"id": "d1", "playerId": "p1"},
        selectedAction={"kind": "castSpell"},
        agentDecision={"resolution": "fallback"},
    )
    profile = collect_profiles([f], target, None)["p1"]
    assert "damage" not in profile["totals"]
    assert "castChoices" not in profile["totals"]
    assert profile["totals"]["unconfirmedChoices"] == 1


def test_pending_poll_does_not_hide_later_confirmed_sdk_feedback():
    pending = frame()
    pending["decision"] = {"id": "choice", "playerId": "p1"}
    confirmed = {
        **pending,
        "agentDecision": {"resolution": "confirmed"},
        "selectedAction": {"kind": "castSpell"},
    }
    collector = ProfileCollector([{"playerId": "p1", "groups": ["decisions"]}])
    collector.observe(pending)
    result = collector.finish(None, [pending, confirmed])
    assert result["p1"]["totals"] == {"castChoices": 1}
    assert collector.finish(None, [confirmed]) == result


def row(match="one", player="p1", result="win"):
    return {
        "replayId": match,
        "playerId": player,
        "createdAt": "2026-10-19T00:00:00+00:00",
        "result": result,
        "deckVersionId": "deck",
        "actorName": player,
    }


def test_storage_off_by_default_rules_union_and_basic_results_work(tmp_path):
    seats = [{"agentId": "a", "kind": "agent"}, {"agentId": "local-player", "kind": "human"}]
    decks = [{"id": "deck"}, {"id": "other"}]
    assert tracking_targets(tmp_path, seats, decks) == []
    save_settings(
        tmp_path,
        {
            "storageLimitBytes": MIN_LIMIT,
            "rules": [
                {"kind": "agent", "id": "a", "groups": ["combat"]},
                {"kind": "deck", "id": "deck", "groups": ["resources"]},
                {"kind": "player", "id": "local-player", "groups": ["opening"]},
            ],
        },
    )
    assert tracking_targets(tmp_path, seats, decks) == [
        {"playerId": "batch-seat-1", "groups": ["combat", "resources"]},
        {"playerId": "local-human", "groups": ["opening"]},
    ]
    write_result(tmp_path, seats[0], row())
    write_result(tmp_path, seats[1], row(player="p2", result="loss"))
    assert storage_status(tmp_path)["matchFiles"] == 1
    assert len(stored_rows(tmp_path)) == 2
    assert local_statistics(tmp_path)["summary"]["matches"] == 1
    assert local_statistics(tmp_path)["players"][0]["losses"] == 1


def test_whole_match_oldest_eviction_and_oversized_reject_without_data_loss(tmp_path):
    save_settings(tmp_path, {"storageLimitBytes": MIN_LIMIT, "rules": []})
    seat = {"agentId": "a"}
    write_result(tmp_path, seat, {**row("old"), "padding": "x" * 600000})
    old = next((tmp_path / ".deepdeck/statistics/matches").glob("*.json"))
    os.utime(old, (1, 1))
    write_result(tmp_path, seat, {**row("new"), "padding": "x" * 600000})
    assert [r["replayId"] for r in stored_rows(tmp_path)] == ["new"]
    assert storage_status(tmp_path)["usedBytes"] <= MIN_LIMIT
    with pytest.raises(StatisticsStorageError):
        write_result(tmp_path, seat, {**row("too-large"), "padding": "x" * (MIN_LIMIT + 1)})
    assert len(stored_rows(tmp_path)) == 1
    assert stored_rows(tmp_path)[0]["replayId"] == "new"


def test_match_transaction_rejects_both_seats_together_when_space_cannot_fit(tmp_path):
    save_settings(tmp_path, {"storageLimitBytes": MIN_LIMIT})
    legacy = tmp_path / ".deepdeck/agents/old/local-results.jsonl"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("x" * (MIN_LIMIT - 1500))
    with pytest.raises(StatisticsStorageError):
        write_match_results(
            tmp_path,
            [
                ({"agentId": "a"}, {**row(player=p), "padding": "x" * 1000}, None)
                for p in ("p1", "p2")
            ],
        )
    assert stored_rows(tmp_path) == []
    assert legacy.exists()


def _parallel_seat_write(root: str, player: str):
    write_result(Path(root), {"agentId": "same-agent"}, row("mirror", player))


def test_separate_worker_processes_do_not_lose_mirror_seats(tmp_path):
    import multiprocessing

    context = multiprocessing.get_context("spawn")
    processes = [
        context.Process(target=_parallel_seat_write, args=(str(tmp_path), p)) for p in ("p1", "p2")
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(20)
        if process.is_alive():
            process.terminate()
            pytest.fail("Statistics writer did not finish")
        assert process.exitcode == 0
    assert {r["playerId"] for r in stored_rows(tmp_path)} == {"p1", "p2"}


def test_profile_storage_does_not_depend_on_replay_and_unknown_groups_absent(tmp_path):
    profile = collect_profiles([frame()], [{"playerId": "p1", "groups": ["board", "pace"]}], 10)[
        "p1"
    ]
    write_result(tmp_path, {"agentId": "a"}, row(), profile)
    result = advanced_statistics(tmp_path, "agent", "a")
    assert result["trackedSeatResults"] == 1
    assert result["totals"]["durationSeconds"]["mean"] == 10
    assert result["snapshots"]["life"]["mean"] == 20
    assert "cardsInHand" not in result["snapshots"]
    assert advanced_statistics(tmp_path, "deck", "deck")["trackedSeatResults"] == 1


def test_legacy_results_count_toward_quota_but_are_not_silently_deleted(tmp_path):
    path = tmp_path / ".deepdeck/agents/a/local-results.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("x" * (MIN_LIMIT + 100))
    with pytest.raises(StatisticsStorageError):
        save_settings(tmp_path, {"storageLimitBytes": MIN_LIMIT})
    assert path.stat().st_size == MIN_LIMIT + 100
    assert storage_status(tmp_path)["legacyBytes"] == MIN_LIMIT + 100


def test_historical_engine_files_count_in_budget_and_are_never_evicted(tmp_path, monkeypatch):
    first = tmp_path / "runs/deck-analytics.json"
    second = tmp_path / "external/deepdeck-engine/runs/deck-analytics.json"
    for path in (first, second):
        path.parent.mkdir(parents=True)
        path.write_text('{"history":"kept"}')
    monkeypatch.setenv("MTG_DECK_ANALYTICS_PATH", str(first))
    status = storage_status(tmp_path)
    assert status["legacyEngineBytes"] == first.stat().st_size + second.stat().st_size
    assert status["usedBytes"] == status["legacyEngineBytes"]
    save_settings(tmp_path, {"storageLimitBytes": MIN_LIMIT})
    assert first.read_text() == '{"history":"kept"}'
    assert second.exists()


def test_legacy_opt_out_verification_survives_success_and_failure_markers(tmp_path):
    from deepdeck_learner.statistics_store import note_engine_analytics, note_storage_error

    note_engine_analytics(tmp_path, False)
    note_storage_error(tmp_path, OSError("disk full"))
    assert storage_status(tmp_path)["engineAnalyticsDisabled"] is False
    write_result(tmp_path, {"agentId": "a"}, row())
    status = storage_status(tmp_path)
    assert status["engineAnalyticsDisabled"] is False
    assert status["lastError"] is None
    note_engine_analytics(tmp_path, True)
    assert storage_status(tmp_path)["engineAnalyticsDisabled"] is True


def test_settings_routes_are_protected_and_reject_unknown_metrics(tmp_path):
    from fastapi.testclient import TestClient

    from deepdeck_learner.app import create_app

    client = TestClient(create_app(tmp_path))
    route = "/api/v1/statistics/storage"
    assert client.get(route).json()["rules"] == []
    assert client.put(route, json={"storageLimitBytes": MIN_LIMIT}).status_code == 403
    token = client.get("/api/v1/session").json()["token"]
    headers = {"X-DeepDeck-Token": token}
    assert (
        client.put(
            route, headers=headers, json={"storageLimitBytes": MIN_LIMIT, "rules": []}
        ).status_code
        == 200
    )
    assert (
        client.put(
            route,
            headers=headers,
            json={"rules": [{"kind": "agent", "id": "a", "groups": ["guessed-mana"]}]},
        ).status_code
        == 422
    )
