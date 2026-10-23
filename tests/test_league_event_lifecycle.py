"""Learner's active League flow uses cycles, not retired one-game tickets."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import httpx
import pytest
from deepdeck_agent import (
    LeagueCompatibilityError,
    LeagueEntryConflict,
    LeagueEventEntry,
    LeagueMutationUncertain,
    LeagueUnavailableError,
)

from deepdeck_learner.league_lifecycle import (
    LeagueRunBudget,
    LeagueRunLimits,
    league_failure_message,
    serve_event_cycles,
)
from deepdeck_learner.league_rotation_io import read_result, write_result


def uid(number):
    return str(UUID(int=number))


def state(phase, *, count=0, event=1, own=True):
    return {
        "id": uid(event),
        "phase": phase,
        "ownEntry": {"agentVersionId": uid(2), "deckVersionId": uid(3)} if own else None,
        "ownMatches": [
            {"matchId": uid(100 + i), "status": "complete", "stage": "season"} for i in range(count)
        ],
    }


def runner_for(states, *, selected=None, join_error=None):
    league = SimpleNamespace(
        agent_version_id=uid(2),
        find_event=AsyncMock(return_value=selected or state("registration", own=False)),
        join=AsyncMock(return_value=states[0], side_effect=join_error),
        event=AsyncMock(side_effect=states[1:]),
        withdraw=AsyncMock(side_effect=AssertionError("No implicit withdrawals.")),
    )
    return SimpleNamespace(league=league, wait_until_connected=AsyncMock())


@pytest.mark.asyncio
@pytest.mark.parametrize("stop_mode", ["matches", "duration", "stop"])
async def test_limits_drain_entire_registered_cycle_including_playoffs(stop_mode):
    stopped, clock = [False], [0.0]
    limits = (
        LeagueRunLimits(max_matches=1)
        if stop_mode == "matches"
        else (LeagueRunLimits(duration_seconds=1) if stop_mode == "duration" else LeagueRunLimits())
    )
    budget = LeagueRunBudget(limits, clock=lambda: clock[0], stop_requested=lambda: stopped[0])
    states = [
        state("registration"),
        state("season", count=1),
        state("playoffs", count=2),
        state("complete", count=3),
    ]
    runner = runner_for(states)
    seen = []

    async def observe(event):
        seen.append(event["phase"])
        clock[0] = 2.0
        stopped[0] = stop_mode == "stop"

    await serve_event_cycles(
        runner,
        LeagueEventEntry("legacy", deck_version_id=uid(3)),
        budget,
        once=False,
        on_event=observe,
        poll_seconds=0,
    )
    assert seen == ["registration", "season", "playoffs", "complete"]
    assert budget.completed == 3 and budget.reserved == 0
    assert len(budget.completed_match_ids) == 3
    runner.league.join.assert_awaited_once()
    runner.league.find_event.assert_awaited_once()
    runner.league.withdraw.assert_not_awaited()


@pytest.mark.asyncio
async def test_resume_active_entry_even_when_local_stop_has_been_requested():
    current = state("season", count=1)
    runner = runner_for([current, state("complete", count=2)], selected=current)
    budget = LeagueRunBudget(LeagueRunLimits(), stop_requested=lambda: True)
    await serve_event_cycles(
        runner,
        LeagueEventEntry("commander", deck_version_id=uid(3)),
        budget,
        once=False,
        on_event=AsyncMock(),
        poll_seconds=0,
    )
    assert budget.completed == 2
    runner.league.join.assert_not_awaited()


@pytest.mark.asyncio
async def test_stopped_worker_must_not_start_fresh_enrollment():
    runner = runner_for([state("registration")])
    budget = LeagueRunBudget(LeagueRunLimits(), stop_requested=lambda: True)
    await serve_event_cycles(
        runner,
        LeagueEventEntry("legacy", deck_version_id=uid(3)),
        budget,
        once=False,
        on_event=AsyncMock(),
        poll_seconds=0,
    )
    runner.league.join.assert_not_awaited()


@pytest.mark.asyncio
async def test_uncertain_enrollment_only_reconciles_reads_and_keeps_serving_cycle():
    error = LeagueMutationUncertain("private text must not reach the UI")
    runner = runner_for(
        [state("registration", own=False), state("season", count=1), state("complete", count=2)],
        join_error=error,
    )
    notices = []
    await serve_event_cycles(
        runner,
        LeagueEventEntry("legacy", deck_version_id=uid(3)),
        LeagueRunBudget(LeagueRunLimits()),
        once=True,
        on_event=AsyncMock(),
        on_uncertain=notices.append,
        poll_seconds=0,
    )
    runner.league.join.assert_awaited_once()
    assert runner.league.event.await_count == 2 and notices == [error]


@pytest.mark.asyncio
async def test_uncertain_not_enrolled_after_deadline_is_not_success_or_reposted():
    error = LeagueMutationUncertain("unconfirmed")
    runner = runner_for(
        [state("registration", own=False), state("season", own=False)], join_error=error
    )
    with pytest.raises(LeagueMutationUncertain):
        await serve_event_cycles(
            runner,
            LeagueEventEntry("legacy", deck_version_id=uid(3)),
            LeagueRunBudget(LeagueRunLimits()),
            once=False,
            on_event=AsyncMock(),
            poll_seconds=0,
        )
    runner.league.join.assert_awaited_once()


@pytest.mark.asyncio
async def test_changed_deck_mid_cycle_is_not_silently_accepted():
    changed = state("season")
    changed["ownEntry"]["deckVersionId"] = uid(4)
    runner = runner_for([state("registration"), changed])
    with pytest.raises(LeagueEntryConflict):
        await serve_event_cycles(
            runner,
            LeagueEventEntry("legacy", deck_version_ids=(uid(3), uid(4))),
            LeagueRunBudget(LeagueRunLimits()),
            once=False,
            on_event=AsyncMock(),
            poll_seconds=0,
        )
    runner.league.join.assert_awaited_once()


def test_unique_completed_match_ids_only_no_byepoints_or_running_matches():
    budget = LeagueRunBudget(LeagueRunLimits())
    event = state("season", count=1)
    event["ownMatches"] += [
        deepcopy(event["ownMatches"][0]),
        {"matchId": uid(500), "status": "running"},
        {"matchId": uid(501), "status": "failed"},
    ]
    budget.observe_event(event)
    budget.observe_event(event)
    assert budget.completed == 1
    event["ownMatches"].append({"matchId": "not-a-uuid", "status": "complete"})
    with pytest.raises(LeagueCompatibilityError):
        budget.observe_event(event)


@pytest.mark.parametrize(
    "kind,expected",
    [
        (LeagueMutationUncertain, "could not be confirmed"),
        (LeagueEntryConflict, "already has a different"),
        (LeagueCompatibilityError, "incompatible"),
        (LeagueUnavailableError, "not enabled"),
    ],
)
def test_sdk_error_outcome_wins_over_http_cause_and_never_exposes_details(kind, expected):
    response = httpx.Response(503, request=httpx.Request("POST", "https://private.test/key"))
    error = kind("ddl_agent_secret https://private.test Bearer private")
    error.__cause__ = httpx.HTTPStatusError("private", request=response.request, response=response)
    wrapper = RuntimeError("private")
    wrapper.__cause__ = error
    message = league_failure_message(wrapper)
    assert expected in message
    assert "HTTP 503" not in message and "private" not in message and "ddl_agent_" not in message


def test_v2_receipt_counts_every_unique_completed_match_and_v1_still_reads(tmp_path):
    path = tmp_path / "receipt.json"
    write_result(
        path,
        run_id="a" * 32,
        completed=3,
        elapsed=10.0,
        outcome="complete",
        match_id=uid(102),
        completed_match_ids=[uid(100), uid(101), uid(102)],
    )
    assert read_result(path, "a" * 32)["completedEntries"] == 3
    write_result(
        path, run_id="a" * 32, completed=1, elapsed=2.0, outcome="complete", match_id="old-id"
    )
    assert read_result(path, "a" * 32)["completedEntries"] == 1


@pytest.mark.parametrize(
    "ids,count,last",
    [
        ([uid(1), uid(1)], 2, uid(1)),
        ([uid(1)], 2, uid(1)),
        (["bad"], 1, "bad"),
        ([uid(1)], 1, uid(2)),
    ],
)
def test_invalid_multimatch_receipt_is_rejected(tmp_path, ids, count, last):
    path = tmp_path / "receipt.json"
    write_result(
        path,
        run_id="a" * 32,
        completed=count,
        elapsed=1.0,
        outcome="complete",
        match_id=last,
        completed_match_ids=ids,
    )
    with pytest.raises(ValueError):
        read_result(path, "a" * 32)


@pytest.mark.asyncio
@pytest.mark.parametrize("game_format", ["legacy", "commander"])
async def test_actual_worker_uses_real_sdk_event_rest_and_only_collects_finals(
    monkeypatch,
    tmp_path,
    capsys,
    game_format,
):
    import asyncio
    import importlib
    import json

    from deepdeck_agent import LeagueClient

    worker = importlib.import_module("deepdeck_examples.run")
    monkeypatch.setenv("DEEPDECK_API_KEY", "ddl_agent_fixture_only_not_a_real_key")
    monkeypatch.setattr(
        worker,
        "_agent_and_config",
        lambda _: (
            object(),
            SimpleNamespace(name="Fixture", agent_id="fixture", formats=(game_format,)),
        ),
    )
    actual_sleep = asyncio.sleep

    async def fast_sleep(_):
        await actual_sleep(0)

    monkeypatch.setattr(worker.asyncio, "sleep", fast_sleep)
    requests, recorded, connected = [], [], [False]
    enrolled, reads = [False], [0]

    def detail(phase, count=0):
        result = state(phase, count=count, own=enrolled[0])
        result.update(
            format=game_format,
            competitionVersionId=uid(9),
            executionEnabled=True,
            serverNow="2026-10-22T12:10:00Z",
            registrationOpensAt="2026-10-22T12:00:00Z",
            registrationClosesAt="2026-10-22T12:30:00Z",
        )
        for i, match in enumerate(result["ownMatches"]):
            match["stage"] = "season" if i == 0 else "playoffs"
        return result

    def handler(request):
        requests.append((request.method, request.url.path))
        assert "/matchmaking/" not in request.url.path
        assert request.headers["Authorization"] == "Bearer ddl_agent_fixture_only_not_a_real_key"
        if request.method == "POST":
            assert connected[0] and request.url.path.endswith("/entry")
            assert json.loads(request.content) == {
                "agentVersionId": uid(2),
                "deckVersionId": uid(3),
            }
            enrolled[0] = True
            return httpx.Response(200, json={"data": {"accepted": True}})
        if request.url.path.endswith("/events"):
            return httpx.Response(
                200,
                json={
                    "data": {
                        "items": [detail("registration")],
                        "executionEnabled": True,
                        "total": 1,
                        "pageSize": 20,
                    }
                },
            )
        if enrolled[0]:
            reads[0] += 1
        phase, count = [
            ("registration", 0),
            ("registration", 0),
            ("season", 1),
            ("playoffs", 2),
            ("complete", 3),
        ][min(reads[0], 4)]
        return httpx.Response(200, json={"data": detail(phase, count)})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:

        class Runner:
            def __init__(self, **kwargs):
                self.target = kwargs["target"]
                self.client = SimpleNamespace(registration=None)
                self.league = LeagueClient(self.target, agent_version_id=uid(2), http_client=client)

            async def serve(self):
                connected[0] = True
                self.client.registration = SimpleNamespace(agent_version_id=uid(2))
                try:
                    await asyncio.Event().wait()
                finally:
                    connected[0] = False

            async def wait_until_connected(self, **_):
                assert connected[0]

            async def match(self, match_id):
                assert connected[0]
                return {"summary": {"status": "complete"}}

        class Collector:
            def __init__(self, root, _):
                self.root = root

            def collect(self):
                return {"collected": 0}

            def record_match(self, snapshot):
                recorded.append(snapshot)
                return False

        monkeypatch.setattr(worker, "AgentRunner", Runner)
        monkeypatch.setattr("deepdeck_learner.league_replays.LeagueReplayCollector", Collector)
        receipt = tmp_path / "receipt.json"
        args = worker.parser().parse_args(
            [
                "random",
                "--target",
                "ddl",
                "--league-format",
                game_format,
                "--competition-version-id",
                uid(9),
                "--deck-version-id",
                uid(3),
                "--matchmaking-concurrency",
                "10",
                "--league-max-matches",
                "1",
                "--league-result-file",
                str(receipt),
                "--league-run-id",
                "a" * 32,
                "--replay-root",
                str(tmp_path),
                "--replay-model-id",
                "fixture",
            ]
        )
        await asyncio.wait_for(worker.run(args), 2)
    assert not connected[0]
    assert sum(method == "POST" for method, _ in requests) == 1
    assert read_result(receipt, "a" * 32)["completedEntries"] == 3
    assert {snapshot["matchId"] for snapshot in recorded} == {uid(101), uid(102)}
    assert all(snapshot["stage"] == "playoffs" for snapshot in recorded)
    reports = [
        json.loads(line.split(" ", 1)[1])
        for line in capsys.readouterr().out.splitlines()
        if line.startswith("DEEPDECK_LEAGUE_RUN ")
    ]
    assert reports[-1]["completedEntries"] == 3 and reports[-1]["activeEntries"] == 0
    assert any(report.get("phase") == "season" and report["draining"] for report in reports)


@pytest.mark.asyncio
async def test_multiformat_worker_requires_explicit_format_before_connection(monkeypatch):
    import importlib

    worker = importlib.import_module("deepdeck_examples.run")
    monkeypatch.setattr(
        worker,
        "_agent_and_config",
        lambda _: (
            object(),
            SimpleNamespace(name="Fixture", agent_id="fixture", formats=("legacy", "commander")),
        ),
    )
    serve = AsyncMock(side_effect=AssertionError("No ambiguous connection."))
    monkeypatch.setattr(worker, "AgentRunner", lambda **kwargs: SimpleNamespace(serve=serve))
    args = worker.parser().parse_args(["random", "--target", "ddl", "--deck-version-id", uid(3)])
    with pytest.raises(ValueError, match="league-format"):
        await worker.run(args)
    serve.assert_not_awaited()
