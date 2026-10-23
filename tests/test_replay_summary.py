from __future__ import annotations

from copy import deepcopy

from deepdeck_learner.replay_summary import replay_match_summary


def frame(turn, *, game=None, wins=None, complete=False):
    result = {"state": {"turnNumber": turn, "players": [{"id": "p1"}, {"id": "p2"}]}}
    if complete:
        result["state"]["outcome"] = {"winner": "p1"}
    if game is not None:
        result["matchState"] = {
            "gameNumber": game,
            "gamesToWin": 2,
            "winsByPlayerId": wins or {"p1": 0, "p2": 0},
        }
    return result


def test_best_of_three_score_and_turns_come_from_every_game_not_last_frame():
    frames = [
        frame(1, game=1),
        frame(13, game=1, complete=True),
        frame(1),
        frame(24, game=2, wins={"p1": 1, "p2": 1}, complete=True),
        frame(1),
        frame(34, game=3, wins={"p1": 2, "p2": 1}, complete=True),
    ]
    value = {"metadata": {"roundNumber": 3}, "frames": frames + [frames[-1]]}
    before = deepcopy(value)
    summary = replay_match_summary(value)
    assert summary["totalTurns"] == 71 and summary["turnsExact"] is True
    assert summary["winsByPlayerId"] == {"p1": 2, "p2": 1}
    assert [row["turns"] for row in summary["roundTurns"]] == [13, 24, 34]
    assert value == before


def test_stale_poll_cannot_double_count_a_new_round():
    value = {
        "metadata": {"roundNumber": 2},
        "frames": [
            frame(12, game=1, complete=True),
            frame(1),
            frame(12, game=1, complete=True),
            frame(2),
            frame(9, game=2, wins={"p1": 2, "p2": 0}, complete=True),
        ],
    }
    result = replay_match_summary(value)
    assert result["totalTurns"] == 21 and len(result["roundTurns"]) == 2


def test_incomplete_round_history_is_not_labelled_an_exact_total():
    result = replay_match_summary(
        {
            "metadata": {"roundNumber": 3},
            "frames": [frame(20, game=3, wins={"p1": 2, "p2": 1}, complete=True)],
        }
    )
    assert result["totalTurns"] == 20 and result["turnsExact"] is False
    assert result["winsByPlayerId"] == {"p1": 2, "p2": 1}


def test_authoritative_final_match_score_can_be_saved_without_terminal_frame():
    result = replay_match_summary(
        {
            "metadata": {
                "roundNumber": 2,
                "matchState": {"winsByPlayerId": {"p1": 0, "p2": 2}, "gamesToWin": 2},
            },
            "frames": [frame(5)],
        }
    )
    assert result["winsByPlayerId"] == {"p1": 0, "p2": 2} and result["turnsExact"] is False


def test_league_deltas_and_checkpoints_remain_one_game_not_a_fake_full_match():
    value = {
        "schemaVersion": "deepdeck-league-replay/v1",
        "metadata": {"roundNumber": 2},
        "trace": {
            "initialObservation": {"turnNumber": 1, "players": [{"id": "p1"}, {"id": "p2"}]},
            "frames": [
                {"sequenceNumber": 1, "observationDelta": {"patch": {"turnNumber": 3}}},
                {"sequenceNumber": 2, "patch": {"turnNumber": 6}},
            ],
            "checkpoints": [
                {"sequenceNumber": 2, "observation": {"turnNumber": 7, "players": [{"id": "p2"}]}}
            ],
            "terminalObservation": {"turnNumber": 9, "outcome": {"winner": "p2"}},
        },
    }
    result = replay_match_summary(value)
    assert result["scope"] == "game" and result["totalTurns"] == 9 and result["turnsExact"] is True
    assert result["winsByPlayerId"] == {"p1": 0, "p2": 1}
    assert result["roundTurns"][0]["round"] == 2


def test_missing_or_malformed_history_remains_unknown():
    for value in ({}, {"frames": None}, {"frames": [None, {"state": {"turnNumber": True}}]}):
        result = replay_match_summary(value)
        assert result["totalTurns"] is None and result["winsByPlayerId"] is None


def test_legacy_single_game_winner_and_four_seat_order_are_preserved():
    result = replay_match_summary(
        {
            "metadata": {"roundNumber": 1},
            "frames": [
                {
                    "state": {
                        "turnNumber": 12,
                        "outcome": {"winner": "c"},
                        "players": [{"id": key} for key in ("a", "b", "c", "d")],
                    }
                }
            ],
        }
    )
    assert result["playerIds"] == ["a", "b", "c", "d"]
    assert result["winsByPlayerId"] == {"a": 0, "b": 0, "c": 1, "d": 0}


def test_cached_summary_reuses_compact_facts_without_loading_frame_objects():
    expected = replay_match_summary({"frames": [frame(4, complete=True)]})
    assert (
        replay_match_summary({"metadata": {"matchSummary": expected}, "frames": None}) == expected
    )
