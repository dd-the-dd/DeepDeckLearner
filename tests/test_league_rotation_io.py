import json

import pytest

from deepdeck_learner.league_rotation_io import (
    read_result,
    stop_requested,
    stop_through,
    turn_paths,
    write_result,
)


def test_stop_watermark_fences_old_turns_without_stopping_new_turns(tmp_path):
    stop, receipt = turn_paths(tmp_path, "a" * 32, "b" * 32, "c" * 32)
    assert not stop_requested(stop, 1)
    stop_through(stop, 3)
    stop_through(stop, 2)
    assert stop_requested(stop, 1) and stop_requested(stop, 3)
    assert not stop_requested(stop, 4)
    assert read_result(receipt, "c" * 32) is None
    stop.write_text("broken", encoding="utf-8")
    assert stop_requested(stop, 4)


@pytest.mark.parametrize("identifier", ["../outside", "", "A" * 32, None])
def test_private_turn_paths_reject_untrusted_identifiers(tmp_path, identifier):
    with pytest.raises(ValueError):
        turn_paths(tmp_path, identifier, "b" * 32, "c" * 32)


@pytest.mark.parametrize(
    "field,value",
    [
        ("runId", "wrong"),
        ("completedEntries", True),
        ("completedEntries", 2),
        ("elapsedSeconds", float("nan")),
        ("elapsedSeconds", 10**400),
        ("outcome", []),
        ("outcome", "failed"),
        ("matchId", {}),
        ("matchId", ""),
    ],
)
def test_turn_receipts_reject_invalid_or_mismatched_data(tmp_path, field, value):
    _, receipt = turn_paths(tmp_path, "a" * 32, "b" * 32, "c" * 32)
    write_result(
        receipt, run_id="c" * 32, completed=1, elapsed=1.5, outcome="complete", match_id="match"
    )
    data = json.loads(receipt.read_text())
    data[field] = value
    receipt.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError):
        read_result(receipt, "c" * 32)


def test_successful_receipt_is_bound_to_dispatch(tmp_path):
    _, receipt = turn_paths(tmp_path, "a" * 32, "b" * 32, "c" * 32)
    write_result(
        receipt, run_id="c" * 32, completed=1, elapsed=12.5, outcome="complete", match_id="match"
    )
    assert read_result(receipt, "c" * 32) == {
        "completedEntries": 1,
        "elapsedSeconds": 12.5,
        "outcome": "complete",
        "matchId": "match",
    }
