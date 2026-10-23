from __future__ import annotations

import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from deepdeck_learner.competitions import (
    CompetitionService,
    create_competition,
    record_result,
    standings,
)


def event(kind: str, n: int, **extra: Any) -> dict[str, Any]:
    return create_competition(
        {
            "type": kind,
            "seed": 0,
            "seeding": "registration",
            "entrants": [{"agent_id": f"agent-{i}", "name": f"Player {i}"} for i in range(n)],
            **extra,
        }
    )


def finish(c: dict[str, Any], *, lower_wins: bool = False) -> list[dict[str, Any]]:
    played = []
    while c["status"] != "completed":
        ready = [m for m in c["matches"] if m["status"] == "ready"]
        assert ready, c
        m = ready[0]
        seat = 2 if lower_wins and m["stage"] == "final" else 1
        record_result(
            c,
            m["id"],
            {
                "winner": f"batch-seat-{seat}",
                "replayId": m["id"],
                "winsByPlayerId": {
                    "batch-seat-1": 2 if seat == 1 else 1,
                    "batch-seat-2": 2 if seat == 2 else 1,
                },
            },
        )
        played.append(m)
        assert len(played) <= 2000
    return played


@pytest.mark.parametrize("n", range(2, 34))
def test_single_elimination_byes_and_no_fake_matches(n: int) -> None:
    c = event("single", n)
    matches = finish(c)
    assert len(matches) == n - 1
    assert c["champion"] == "p1"
    losses = Counter(m["loser"] for m in matches)
    assert losses[c["champion"]] == 0
    assert all(value == 1 for value in losses.values())
    assert sum(r["played"] for r in standings(c)) == 2 * (n - 1)


@pytest.mark.parametrize("n", range(2, 34))
@pytest.mark.parametrize("reset", [False, True])
def test_true_double_elimination_and_final_reset(n: int, reset: bool) -> None:
    c = event("double", n)
    matches = finish(c, lower_wins=reset)
    assert len(matches) == 2 * n - 2 + int(reset)
    losses = Counter(m["loser"] for m in matches)
    assert losses[c["champion"]] == int(reset)
    assert all(losses[p["id"]] == 2 for p in c["entrants"] if p["id"] != c["champion"])
    reset_match = next(m for m in c["matches"] if m.get("resetAfter"))
    assert reset_match["status"] == ("done" if reset else "skipped")


@pytest.mark.parametrize("n", [2, 3, 4, 5, 10, 17])
@pytest.mark.parametrize("legs", [1, 2, 3])
def test_round_robin_every_pair_once_each_leg(n: int, legs: int) -> None:
    c = event("round_robin", n, legs=legs)
    matches = finish(c)
    assert len(matches) == n * (n - 1) // 2 * legs
    assert set(Counter(frozenset(m["players"]) for m in matches).values()) == {legs}
    for number in {m["round"] for m in c["matches"]}:
        players = [p for m in c["matches"] if m["round"] == number for p in m["players"] if p]
        assert len(players) == len(set(players)) == n
    assert all(r["byes"] == (legs if n % 2 else 0) for r in standings(c))


@pytest.mark.parametrize("n,cut", [(3, 2), (5, 3), (10, 4), (10, 8), (17, 7)])
def test_round_robin_seeds_playoffs(n: int, cut: int) -> None:
    c = event("round_robin_cut", n, cut=cut)
    played = finish(c)
    assert len(played) == n * (n - 1) // 2 + cut - 1
    assert c["playoffSeeds"] == [r["id"] for r in standings(c, "round_robin")[:cut]]
    first = [m for m in c["matches"] if m["stage"] == "winners" and m["round"] == 1]
    assert set(p for m in first for p in m["players"] if p) == set(c["playoffSeeds"])


@pytest.mark.parametrize("n", [2, 3, 4, 5, 8, 10, 17])
def test_swiss_rounds_byes_and_no_avoidable_repeats(n: int) -> None:
    rounds = min(3, n - 1)
    c = event("swiss", n, rounds=rounds)
    played = finish(c)
    assert len(played) == (n // 2) * rounds
    assert max(m["round"] for m in c["matches"]) == rounds
    assert len({frozenset(m["players"]) for m in played}) == len(played)
    assert all(r["byes"] <= 1 for r in standings(c))


@pytest.mark.parametrize("n", [2, 3, 5, 10])
@pytest.mark.parametrize("losses", [1, 2, 3, 5])
def test_loss_limit_exactly_eliminates_at_threshold(n: int, losses: int) -> None:
    c = event("loss_limit", n, losses=losses)
    played = finish(c)
    actual = Counter(m["loser"] for m in played)
    assert actual[c["champion"]] < losses
    assert all(actual[p["id"]] == losses for p in c["entrants"] if p["id"] != c["champion"])


def test_swiss_draw_is_not_a_fake_loss_but_elimination_draw_requires_retry() -> None:
    c = event("swiss", 2, rounds=1)
    record_result(
        c, "m1", {"winner": None, "winsByPlayerId": {"batch-seat-1": 1, "batch-seat-2": 1}}
    )
    assert [r["points"] for r in standings(c)] == [1, 1]
    c = event("single", 2)
    with pytest.raises(ValueError, match="draw"):
        record_result(c, "m1", {"winner": None, "replayId": "draw-replay"})
    assert c["matches"][0]["winner"] is None
    assert c["matches"][0]["attempts"][0]["replayId"] == "draw-replay"


def test_seed_zero_reproduces_field_and_invalid_inputs_fail() -> None:
    payload = {"type": "single", "seed": 0, "entrants": [{"agent_id": str(i)} for i in range(10)]}
    assert create_competition(payload)["entrants"] == create_competition(payload)["entrants"]
    for extra in [
        {"format": "commander"},
        {"type": "unknown"},
        {"entrants": []},
        {"rounds": 0},
        {"engine_url": "https://example.org"},
    ]:
        with pytest.raises(ValueError):
            create_competition({**payload, **extra})


class FakeManager:
    def __init__(self) -> None:
        self.jobs: dict[str, Any] = {}
        self.requests: list[dict[str, Any]] = []

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(payload)
        key = str(len(self.requests))
        self.jobs[key] = {"id": key, "status": "running", "details": {}}
        return self.jobs[key]

    def get(self, key: str) -> dict[str, Any] | None:
        return self.jobs.get(key)

    def stop(self, key: str) -> dict[str, Any]:
        self.jobs[key]["status"] = "stopped"
        return self.jobs[key]


def service(tmp_path: Path) -> tuple[CompetitionService, FakeManager]:
    (tmp_path / ".deepdeck").mkdir(exist_ok=True)
    manager = FakeManager()
    return CompetitionService(tmp_path, manager), manager


def test_real_dispatch_contract_uses_batch_results_and_preserves_failure(tmp_path: Path) -> None:
    store, manager = service(tmp_path)
    c = event("single", 3)
    store._save(c)
    store.tick(c)
    assert manager.requests[0]["kind"] == "playtest.batch"
    assert manager.requests[0]["games"] == 1
    assert manager.requests[0]["competition_id"] == c["id"]
    first = c["activeMatchId"]
    manager.jobs["1"].update(
        status="completed",
        details={
            "completed": 1,
            "winner": "batch-seat-1",
            "replayId": "replay-1",
            "winsByPlayerId": {"batch-seat-1": 2, "batch-seat-2": 0},
        },
    )
    store.tick(c)
    assert next(m for m in c["matches"] if m["id"] == first)["replayId"] == "replay-1"
    manager.jobs["2"].update(status="failed", logs=["Engine failed"])
    store.tick(c)
    assert c["status"] == "paused"
    assert sum(m["status"] == "done" for m in c["matches"]) == 1
    assert c["champion"] is None
    assert c["activeJobId"] is None
    assert store.get(c["id"])["error"] == "Engine failed"


def test_restart_does_not_autostart_and_reattaches_existing_job(tmp_path: Path) -> None:
    store, manager = service(tmp_path)
    c = event("single", 2)
    store._save(c)
    store.tick(c)
    restarted = CompetitionService(tmp_path, manager)
    assert restarted.get(c["id"])["status"] == "paused"
    c = restarted.get(c["id"])
    c["status"] = "running"
    restarted.tick(c)
    assert len(manager.requests) == 1
    manager.jobs["1"].update(status="completed", details={"completed": 1, "winner": "batch-seat-2"})
    restarted.tick(c)
    assert c["status"] == "completed"
    assert c["champion"] == "p2"


def test_pause_finishes_match_without_dispatching_next(tmp_path: Path) -> None:
    store, manager = service(tmp_path)
    c = event("round_robin", 3)
    store._save(c)
    store.tick(c)
    c["status"] = "pausing"
    manager.jobs["1"].update(status="completed", details={"completed": 1, "winner": "batch-seat-1"})
    store.tick(c)
    assert c["status"] == "paused"
    assert len(manager.requests) == 1


def test_parent_deadline_pauses_a_worker_that_cannot_yield(tmp_path: Path) -> None:
    store, manager = service(tmp_path)
    c = event("single", 2)
    store._save(c)
    store.tick(c)
    manager.jobs["1"]["started_at"] = "2000-01-01T00:00:00+00:00"
    store.tick(c)
    assert manager.jobs["1"]["status"] == "stopped"
    assert c["status"] == "paused"
    assert c["champion"] is None
    assert c["activeJobId"] is None
    assert "safety limit" in c["error"]


def test_api_history_protected_create_and_resume(tmp_path: Path, monkeypatch: Any) -> None:
    from fastapi.testclient import TestClient

    import deepdeck_learner.agent_studio as studio
    from deepdeck_learner.app import create_app

    monkeypatch.setattr(CompetitionService, "_start", lambda self: None)
    monkeypatch.setattr(
        studio,
        "find_agent",
        lambda root, key: (tmp_path, {"format": "legacy", "deckVersionIds": ["deck"]}),
    )
    client = TestClient(create_app(tmp_path))
    path = "/api/v1/local-competitions"
    payload = {"type": "double", "entrants": [{"agent_id": "a"}, {"agent_id": "b"}]}
    assert client.post(path, json=payload).status_code == 403
    headers = {"X-DeepDeck-Token": client.get("/api/v1/session").json()["token"]}
    response = client.post(path, json=payload, headers=headers)
    assert response.status_code == 202
    key = response.json()["id"]
    assert len(client.get(path).json()["items"]) == 1
    assert client.get(f"{path}/{key}").json()["standings"][0]["points"] == 0
    assert client.post(f"{path}/{key}/pause", headers=headers).json()["status"] == "paused"
    assert client.post(f"{path}/{key}/resume", headers=headers).json()["status"] == "running"
    assert client.get(f"{path}/unknown").status_code == 404
    assert (
        client.post(path, json={**payload, "format": "commander"}, headers=headers).status_code
        == 422
    )


def test_resume_recovers_child_created_in_dispatch_crash_window(
    tmp_path: Path, monkeypatch: Any
) -> None:
    store, manager = service(tmp_path)
    c = event("single", 2)
    c["status"] = "paused"
    c["matches"][0].update(status="dispatching", dispatchId="intent-1", seed=0)
    store._save(c)
    with sqlite3.connect(store.path) as db:
        db.execute("CREATE TABLE jobs (id TEXT, payload TEXT)")
        db.execute(
            "INSERT INTO jobs VALUES (?, ?)",
            (
                "existing-child",
                json.dumps(
                    {"id": "existing-child", "details": {"competitionDispatchId": "intent-1"}}
                ),
            ),
        )
    monkeypatch.setattr(store, "_start", lambda: None)
    resumed = store.control(c["id"], "resume")
    assert resumed["activeJobId"] == "existing-child"
    assert resumed["activeMatchId"] == "m1"
    assert resumed["matches"][0]["status"] == "playing"
    assert not manager.requests
