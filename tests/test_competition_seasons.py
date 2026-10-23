from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from deepdeck_learner.competitions import (
    CompetitionService,
    create_competition,
    record_result,
    standings,
)


def event(kind: str, count: int, **extra: Any) -> dict[str, Any]:
    return create_competition(
        {
            "type": kind,
            "seed": 0,
            "seeding": "registration",
            "entrants": [{"agent_id": f"a{i}", "name": f"Player {i}"} for i in range(count)],
            **extra,
        }
    )


def result(c: dict[str, Any], match: dict[str, Any], seat: int = 0) -> None:
    humans = {p["id"] for p in c["entrants"] if p.get("kind") == "human"}
    ids = [
        "local-human" if p in humans else f"batch-seat-{i}"
        for i, p in enumerate(match["players"], 1)
    ]
    record_result(
        c,
        match["id"],
        {
            "winner": ids[seat],
            "winsByPlayerId": {
                p: (2 if c["format"] == "legacy" else 1) if i == seat else 0
                for i, p in enumerate(ids)
            },
            "replayId": f"replay-{len(match['attempts'])}",
        },
    )


def finish(c: dict[str, Any]) -> None:
    for _ in range(5000):
        if c["status"] == "completed":
            return
        match = next(m for m in c["matches"] if m["status"] == "ready")
        result(c, match)
    pytest.fail("Competition did not finish")


@pytest.mark.parametrize("count,groups,qualifiers", [(4, 2, 1), (5, 2, 2), (10, 3, 2), (12, 3, 2)])
def test_groups_are_balanced_and_only_qualifiers_enter_playoffs(
    count: int, groups: int, qualifiers: int
) -> None:
    c = event("groups_cut", count, groups=groups, qualifiers_per_group=qualifiers, reseed=True)
    sizes = [len(g["players"]) for g in c["groups"]]
    assert max(sizes) - min(sizes) <= 1
    finish(c)
    expected = [
        p
        for g in c["groups"]
        for p in [r["id"] for r in standings(c, "groups", group=g["id"])[:qualifiers]]
    ]
    assert set(c["playoffSeeds"]) == set(expected)
    assert (
        len([m for m in c["matches"] if m["stage"] == "winners" and m["status"] == "done"])
        == groups * qualifiers - 1
    )
    assert c["champion"] in expected


def test_reseeding_pairs_best_remaining_against_worst_after_upsets() -> None:
    c = event("single", 8, reseed=True)
    for match in list(c["matches"]):
        result(c, match, 1 if match["players"] == ["p1", "p8"] else 0)
    second = [set(m["players"]) for m in c["matches"] if m["round"] == 2]
    assert {"p2", "p8"} in second
    assert {"p3", "p4"} in second
    finish(c)


def test_first_to_four_series_keeps_seven_independent_matches_and_replays() -> None:
    c = event("single", 2, playoff_wins=4)
    match = c["matches"][0]
    for i, seat in enumerate([0, 1, 0, 1, 0, 1, 0]):
        result(c, match, seat)
        assert match["status"] == ("done" if i == 6 else "ready")
        if i != 6:
            assert c["champion"] is None
    assert match["seriesWins"] == [4, 3]
    assert match["score"] == [8, 6]
    assert len(match["seriesGames"]) == 7
    assert len({g["replayId"] for g in match["seriesGames"]}) == 7
    assert c["champion"] == "p1"


@pytest.mark.parametrize("count", [2, 3, 5, 8])
def test_random_season_has_fixed_rounds_balanced_byes_and_reproducible_schedule(count: int) -> None:
    first = event("random_season", count, rounds=5)
    second = event("random_season", count, rounds=5)
    finish(first)
    finish(second)
    assert [m["players"] for m in first["matches"]] == [m["players"] for m in second["matches"]]
    assert max(m["round"] for m in first["matches"]) == 5
    for number in range(1, 6):
        entrants = [p for m in first["matches"] if m["round"] == number for p in m["players"] if p]
        assert len(entrants) == len(set(entrants)) == count
    byes = [r["byes"] for r in standings(first)]
    assert max(byes) - min(byes) <= 1


def test_random_season_cut_freezes_season_seeds() -> None:
    c = event("random_season_cut", 8, rounds=4, cut=4, reseed=True, playoff_wins=2)
    finish(c)
    assert c["playoffSeeds"] == [r["id"] for r in standings(c, "season")[:4]]
    assert c["champion"] in c["playoffSeeds"]


@pytest.mark.parametrize(
    "kind,count,extra",
    [
        ("single", 4, {}),
        ("single", 16, {}),
        ("random_season", 8, {"rounds": 3}),
        ("random_season", 5, {"rounds": 4}),
        ("random_season", 6, {"rounds": 2}),
        ("random_season", 7, {"rounds": 4}),
        ("random_season_cut", 12, {"rounds": 3, "cut": 4}),
        ("groups_cut", 8, {"groups": 2, "qualifiers_per_group": 2}),
    ],
)
def test_commander_never_dispatches_an_incomplete_table(
    kind: str, count: int, extra: dict[str, Any]
) -> None:
    c = event(kind, count, format="commander", reseed=True, playoff_wins=2, **extra)
    finish(c)
    assert all(len(m["players"]) == 4 and len(set(m["players"])) == 4 for m in c["matches"])
    assert c["champion"] is not None
    assert sum(r["wins"] for r in standings(c)) == len(c["matches"])
    assert sum(r["losses"] for r in standings(c)) == len(c["matches"]) * 3


@pytest.mark.parametrize(
    "extra", [{"groups": 5}, {"playoff_wins": 0}, {"reseed": "yes"}, {"qualifiers_per_group": 5}]
)
def test_invalid_group_and_series_parameters_fail_before_scheduling(extra: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        event("groups_cut", 8, **extra)


@pytest.mark.parametrize(
    "kind,count", [("single", 8), ("double", 4), ("random_season", 3), ("swiss", 8)]
)
def test_unsupported_commander_structures_are_explicit(kind: str, count: int) -> None:
    with pytest.raises(ValueError):
        event(kind, count, format="commander")


def test_five_commander_entrants_play_four_matches_each_with_each_different_player_missing():
    c = event("random_season", 5, rounds=4, format="commander")
    finish(c)
    assert len(c["matches"]) == 5
    assert [r["played"] for r in standings(c)] == [4] * 5
    assert not any(r["byes"] for r in standings(c))
    everyone = {p["id"] for p in c["entrants"]}
    omitted = [next(iter(everyone - set(m["players"]))) for m in c["matches"]]
    assert set(omitted) == everyone


def test_impossible_equal_quota_is_explicit_but_optional_byes_fill_it_without_played_wins():
    with pytest.raises(ValueError, match="multiple of 4"):
        event("random_season", 5, rounds=3, format="commander")
    c = event("random_season_cut", 5, rounds=3, cut=4, format="commander", allow_season_byes=True)
    initial = standings(c, "season")
    assert sum(r["byes"] for r in initial) == 3
    assert sum(r["points"] for r in initial) == 9
    assert not any(r["played"] or r["wins"] or r["gameWins"] for r in initial)
    finish(c)
    season = standings(c, "season")
    assert all(r["played"] + r["byes"] == 3 for r in season)
    assert sum(r["played"] for r in season) == 12
    assert sum(r["wins"] for r in season) == 3
    assert sum(r["points"] for r in season) == 18
    assert len(c["playoffSeeds"]) == 4
    assert all(len(m["players"]) == 4 for m in c["matches"] if m["status"] != "bye")


def test_old_commander_season_records_keep_their_round_based_scheduler():
    c = event("random_season", 8, rounds=3, format="commander")
    c["matches"] = [m for m in c["matches"] if m["round"] == 1]
    for key in ("seasonScheduleVersion", "seasonGamesPerPlayer", "seasonAllowByes"):
        c["parameters"].pop(key)
    finish(c)
    assert len(c["matches"]) == 6
    assert all(r["played"] == 3 for r in standings(c))


class Manager:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(payload)
        return {"id": str(len(self.requests)), "status": "running"}


def test_human_uses_interactive_job_and_real_human_result(tmp_path: Path) -> None:
    (tmp_path / ".deepdeck").mkdir()
    manager = Manager()
    store = CompetitionService(tmp_path, manager)
    c = event(
        "single",
        2,
        seed_advantage=True,
        playoff_wins=2,
        entrants=[{"kind": "human", "name": "You", "deck_id": "local-deck"}, {"agent_id": "a1"}],
    )
    store._save(c)
    store.tick(c)
    assert manager.requests[0]["kind"] == "playtest.table"
    assert manager.requests[0]["starting_seat"] == 1
    result(c, c["matches"][0])
    assert c["matches"][0]["seriesWins"] == [1, 0]
    # Resume a serialized series without erasing the already won match.
    c = json.loads(json.dumps(c))
    c.update(activeJobId=None, activeMatchId=None)
    store.tick(c)
    assert manager.requests[1]["starting_seat"] == 2
    result(c, c["matches"][0])
    assert c["champion"] == "p1"
