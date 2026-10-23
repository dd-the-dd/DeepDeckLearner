from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from deepdeck_learner.local_statistics import local_statistics


def _results(root: Path, directory: str, identity: str, name: str, records: list[dict]) -> None:
    target = root / ".deepdeck" / directory / identity
    target.mkdir(parents=True)
    (target / "local-model.json").write_text(json.dumps({"name": name}), encoding="utf-8")
    (target / "local-results.jsonl").write_text(
        "\n".join(json.dumps(row) for row in records) + "\ninvalid partial line", encoding="utf-8"
    )


def test_local_statistics_count_shared_matches_once_and_include_human_results(
    tmp_path: Path,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    common = {"replayId": "shared", "createdAt": now, "format": "legacy", "deckVersionId": "tempo"}
    _results(tmp_path, "agents", "pilot", "Tempo Pilot", [{**common, "result": "loss"}])
    _results(tmp_path, "players", "local-player", "You", [{**common, "result": "win"}])
    deck = tmp_path / ".deepdeck" / "decks" / "tempo.json"
    deck.parent.mkdir()
    deck.write_text(json.dumps({"name": "Tempo"}), encoding="utf-8")
    result = local_statistics(tmp_path)
    assert result["summary"] == {
        "matches": 1,
        "seatResults": 2,
        "agents": 1,
        "players": 1,
        "decks": 1,
    }
    assert result["agents"][0]["name"] == "Tempo Pilot"
    assert result["agents"][0]["winRate"] == 0
    assert result["players"][0]["wins"] == 1
    assert result["decks"][0]["name"] == "Tempo"
    assert result["decks"][0]["winRate"] == 0.5
    assert result["recentMatches"][0]["winner"] == "You"
    assert len(result["recentMatches"][0]["participants"]) == 2
    assert 0 <= result["agents"][0]["confidence95"][0] < result["agents"][0]["confidence95"][1] <= 1


def test_statistics_period_filters_old_records_and_rejects_unsupported_windows(
    tmp_path: Path,
) -> None:
    _results(
        tmp_path,
        "agents",
        "pilot",
        "Pilot",
        [
            {
                "replayId": "old",
                "createdAt": (datetime.now(timezone.utc) - timedelta(days=40)).isoformat(),
                "result": "draw",
                "deckVersionId": "one",
            }
        ],
    )
    assert local_statistics(tmp_path)["summary"]["matches"] == 1
    assert local_statistics(tmp_path, 30)["summary"]["matches"] == 0
    with pytest.raises(ValueError):
        local_statistics(tmp_path, -1)
