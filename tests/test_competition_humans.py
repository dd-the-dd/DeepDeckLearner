from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from deepdeck_learner.competitions import CompetitionService, create_competition, standings
from deepdeck_learner.jobs import JobManager, JobValidationError
from deepdeck_learner.local_seats import competition_player_id, local_player_ids
from deepdeck_learner.local_statistics import local_statistics
from deepdeck_learner.statistics_store import write_match_results


class Manager:
    def __init__(self):
        self.requests = []
        self.jobs = {}

    def create(self, payload):
        self.requests.append(payload)
        key = str(len(self.requests))
        self.jobs[key] = {"id": key, "status": "running", "details": {}}
        return self.jobs[key]

    def get(self, key):
        return self.jobs[key]


@pytest.fixture(autouse=True)
def local_data_directory(tmp_path):
    (tmp_path / ".deepdeck").mkdir(exist_ok=True)


def payload(count=2, game_format="legacy", **extra):
    return {
        "type": "round_robin" if game_format == "legacy" else "random_season",
        "format": game_format,
        "rounds": 2,
        "seeding": "registration",
        "seed": 0,
        "entrants": [
            {"kind": "human", "name": f"Player {i}", "deck_id": "deck"} for i in range(count)
        ],
        **extra,
    }


@pytest.mark.parametrize("count,game_format", [(4, "legacy"), (8, "commander")])
def test_human_rounds_keep_identity_name_and_engine_winner_mapping(tmp_path, count, game_format):
    manager = Manager()
    service = CompetitionService(tmp_path, manager)
    competition = create_competition(payload(count, game_format))
    service._save(competition)
    expected = {p["id"]: (p["player_id"], p["name"]) for p in competition["entrants"]}
    observed = {p["id"]: set() for p in competition["entrants"]}
    for _ in range(50):
        service.tick(competition)
        if competition["status"] == "completed":
            break
        match = next(m for m in competition["matches"] if m["id"] == competition["activeMatchId"])
        request = manager.requests[-1]
        assert request["kind"] == "playtest.table"
        for participant, seat in zip(match["players"], request["seats"], strict=True):
            assert (seat["player_id"], seat["player_name"]) == expected[participant]
            observed[participant].add(seat["player_id"])
        engine_ids = local_player_ids(s["kind"] for s in request["seats"])
        assert len(set(engine_ids)) == len(request["seats"])
        job = manager.jobs[competition["activeJobId"]]
        job.update(
            status="completed",
            details={
                "completed": 1,
                "winner": engine_ids[-1],
                "winsByPlayerId": {engine_ids[-1]: 2},
                "replayId": f"fixture-{job['id']}",
            },
        )
    assert competition["status"] == "completed"
    assert all(len(values) == 1 for values in observed.values())
    assert len({values.pop() for values in observed.values()}) == count
    assert sum(row["played"] for row in standings(competition)) >= count
    assert len(manager.requests) > 1


def test_player_identity_survives_serialization_and_does_not_merge_same_named_entrants():
    same_names = payload(2)
    for person in same_names["entrants"]:
        person["name"] = "Alex"
    original = create_competition(same_names)
    restored = json.loads(json.dumps(original))
    for person in restored["entrants"]:
        assert person["player_id"] == competition_player_id(restored["id"], person["id"])
    assert len({person["player_id"] for person in restored["entrants"]}) == 2
    other = create_competition(payload(2))
    assert {p["player_id"] for p in restored["entrants"]}.isdisjoint(
        p["player_id"] for p in other["entrants"]
    )
    assert len({competition_player_id("old-event", f"p{i}") for i in range(4)}) == 4


def deck(tmp_path):
    directory = tmp_path / ".deepdeck" / "decks"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "deck.json").write_text(
        json.dumps(
            {
                "name": "Deck",
                "format": "legacy",
                "cards": [
                    {
                        "cardId": "island",
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


def test_preparation_and_statistics_follow_named_people_when_seats_reverse(tmp_path, monkeypatch):
    deck(tmp_path)
    manager = JobManager(tmp_path)
    monkeypatch.setattr(manager, "_refresh_playtest_deck_rules", lambda _, __, cards: cards)
    people = create_competition(payload(2))["entrants"]
    for match_index, ordered in enumerate([people, list(reversed(people))]):
        raw = {
            "format": "legacy",
            "seats": [
                {
                    "kind": "human",
                    "deck_id": "deck",
                    "player_id": p["player_id"],
                    "player_name": p["name"],
                }
                for p in ordered
            ],
        }
        _, _, artifact = manager._batch_command(raw, interactive=True)
        spec = json.loads((artifact / "batch-spec.json").read_text())
        engine_ids = local_player_ids(s["kind"] for s in spec["seats"])
        entries = []
        for index, seat in enumerate(spec["seats"]):
            person = ordered[index]
            assert seat["agentId"] == person["player_id"] and seat["name"] == person["name"]
            row = {
                "replayId": f"fixture-{match_index}",
                "playerId": engine_ids[index],
                "createdAt": datetime.now(timezone.utc).isoformat(),
                "deckVersionId": "deck",
                "actorName": seat["name"],
                "result": "win" if person == people[0] else "loss",
            }
            entries.append((seat, row, None))
        write_match_results(tmp_path, entries)
    statistics = local_statistics(tmp_path)
    assert statistics["summary"]["matches"] == 2 and statistics["summary"]["players"] == 2
    by_id = {p["id"]: p for p in statistics["players"]}
    assert by_id[people[0]["player_id"]]["wins"] == 2
    assert by_id[people[1]["player_id"]]["losses"] == 2
    assert not (tmp_path / ".deepdeck" / "agents").exists()


@pytest.mark.parametrize(
    "identity,name",
    [
        ("../escape", "Alex"),
        (123, "Alex"),
        ("local-player-" + "a" * 32, "private\nname"),
        ("local-player-" + "a" * 32, " " * 4),
    ],
)
def test_invalid_player_identity_or_name_is_rejected_before_dispatch(
    tmp_path, monkeypatch, identity, name
):
    deck(tmp_path)
    manager = JobManager(tmp_path)
    monkeypatch.setattr(manager, "_refresh_playtest_deck_rules", lambda _, __, cards: cards)
    with pytest.raises(JobValidationError):
        manager._batch_command(
            {
                "format": "legacy",
                "seats": [
                    {
                        "kind": "human",
                        "deck_id": "deck",
                        "player_id": identity,
                        "player_name": name,
                    },
                    {"kind": "human", "deck_id": "deck"},
                ],
            },
            interactive=True,
        )
    assert not (tmp_path / ".deepdeck" / "batches").exists()


def test_stopped_human_match_pauses_without_result_and_resume_reuses_people(tmp_path, monkeypatch):
    manager = Manager()
    service = CompetitionService(tmp_path, manager)
    monkeypatch.setattr(service, "_start", lambda: None)
    competition = create_competition(payload(3))
    service._save(competition)
    service.tick(competition)
    original = manager.requests[-1]["seats"]
    manager.jobs[competition["activeJobId"]].update(
        status="stopped", logs=["The table was closed."], details={"replayId": "partial-fixture"}
    )
    service.tick(competition)
    assert competition["status"] == "paused" and competition["champion"] is None
    assert all(row["played"] == 0 for row in standings(competition))
    assert len(manager.requests) == 1
    restarted = service.control(competition["id"], "resume")
    service.tick(restarted)
    assert manager.requests[-1]["seats"] == original
    match = next(m for m in restarted["matches"] if m["status"] == "playing")
    assert match["attempts"][0]["replayId"] == "partial-fixture"


def test_older_competition_without_identity_fields_gets_stable_dispatch_ids(tmp_path):
    manager = Manager()
    service = CompetitionService(tmp_path, manager)
    competition = create_competition(payload(2))
    expected = [p.pop("player_id") for p in competition["entrants"]]
    service._save(competition)
    service.tick(competition)
    assert [seat["player_id"] for seat in manager.requests[0]["seats"]] == expected


def test_duplicate_player_identity_is_rejected_instead_of_merging_results(tmp_path, monkeypatch):
    deck(tmp_path)
    manager = JobManager(tmp_path)
    monkeypatch.setattr(manager, "_refresh_playtest_deck_rules", lambda _, __, cards: cards)
    with pytest.raises(JobValidationError, match="different local player identity"):
        manager._batch_command(
            {
                "format": "legacy",
                "seats": [
                    {"kind": "human", "deck_id": "deck", "player_id": "local-player-" + "a" * 32}
                    for _ in range(2)
                ],
            },
            interactive=True,
        )
