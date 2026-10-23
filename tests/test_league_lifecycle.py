from __future__ import annotations

import asyncio
import importlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from deepdeck_learner import league_lifecycle as lifecycle


@pytest.mark.parametrize(
    "payload",
    [
        True,
        [],
        {"mode": "unknown"},
        {"mode": "continuous", "matches": 1},
        {"mode": "matches", "matches": True},
        {"mode": "matches", "matches": 1.5},
        {"mode": "matches", "matches": "10"},
        {"mode": "matches", "matches": 0},
        {"mode": "matches", "matches": 10001},
        {"mode": "duration", "minutes": 0},
        {"mode": "duration", "minutes": 10081},
        {"mode": ["matches"]},
    ],
)
def test_invalid_run_policies_are_rejected(payload):
    with pytest.raises(ValueError):
        lifecycle.LeagueRunLimits.from_payload(payload)


@pytest.mark.parametrize("status", [400, 401, 403, 409, 503])
def test_league_failure_reports_server_reason_without_transport_secrets(status):
    response = httpx.Response(
        status,
        request=httpx.Request("POST", "https://example.test"),
        json={
            "error": {
                "message": "Deck unavailable. ddl_agent_private "
                "Bearer private-token ddl_session=cookie https://private.test/key"
            }
        },
    )
    error = httpx.HTTPStatusError(
        "secret raw transport error", request=response.request, response=response
    )
    wrapper = RuntimeError("SDK failed")
    wrapper.__cause__ = error
    message = lifecycle.league_failure_message(wrapper)
    assert f"HTTP {status}" in message and "Deck unavailable" in message
    assert not any(
        secret in message
        for secret in (
            "ddl_agent_private",
            "private-token",
            "cookie",
            "private.test",
            "raw transport",
        )
    )


def test_unknown_league_failure_does_not_expose_exception_text():
    assert "secret" not in lifecycle.league_failure_message(RuntimeError("secret"))
    assert "not confirmed" in lifecycle.league_failure_message(TimeoutError("secret"))


def test_streamed_http_error_keeps_status_without_trying_to_expose_unread_content():
    response = httpx.Response(
        400,
        request=httpx.Request("POST", "https://example.test"),
        stream=httpx.ByteStream(b"private unconsumed body"),
    )
    error = httpx.HTTPStatusError("secret", request=response.request, response=response)
    message = lifecycle.league_failure_message(error)
    assert "HTTP 400" in message and "private" not in message and "secret" not in message


def test_policy_argv_does_not_invent_server_capacity():
    assert lifecycle.LeagueRunLimits.from_payload(None).argv() == []
    assert lifecycle.LeagueRunLimits.from_payload({"mode": "matches", "matches": 10}).argv() == [
        "--league-max-matches",
        "10",
    ]
    assert lifecycle.LeagueRunLimits.from_payload({"mode": "duration", "minutes": 2}).argv() == [
        "--league-duration-seconds",
        "120",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 409])
async def test_real_worker_reports_denial_without_retry_or_false_result(
    monkeypatch, capsys, status
):
    worker = importlib.import_module("deepdeck_examples.run")
    monkeypatch.setattr(
        worker,
        "_agent_and_config",
        lambda _: (
            object(),
            SimpleNamespace(name="Fixture", agent_id="fixture", formats=("legacy",)),
        ),
    )

    class Runner:
        def __init__(self, **kwargs):
            self.target = kwargs["target"]
            self.client = SimpleNamespace(registration=None)
            self.joins = 0
            self.league = self

        async def find_event(self, entry):
            return {"id": "00000000-0000-0000-0000-000000000001", "ownEntry": None}

        async def serve(self):
            await asyncio.Event().wait()

        async def wait_until_connected(self, **kwargs):
            return "connected"

        async def join(self, event_id, entry):
            self.joins += 1
            assert self.joins == 1
            response = httpx.Response(
                status,
                request=httpx.Request("POST", "https://example.test"),
                json={"error": {"message": "Deck is not in this competition"}},
            )
            raise httpx.HTTPStatusError("denied", request=response.request, response=response)

    monkeypatch.setattr(worker, "AgentRunner", Runner)
    args = worker.parser().parse_args(
        [
            "alexios",
            "--target",
            "ddl",
            "--competition-version-id",
            "00000000-0000-0000-0000-000000000002",
            "--deck-version-id",
            "00000000-0000-0000-0000-000000000003",
            "--once",
        ]
    )
    with pytest.raises(httpx.HTTPStatusError):
        await asyncio.wait_for(worker.run(args), timeout=2)
    output = capsys.readouterr().out
    reports = [
        json.loads(line.split(" ", 1)[1])
        for line in output.splitlines()
        if line.startswith("DEEPDECK_LEAGUE_RUN ")
    ]
    assert f"HTTP {status}" in reports[-1]["lastError"]
    assert "Deck is not in this competition" in reports[-1]["lastError"]
    assert reports[-1]["completedEntries"] == 0 and reports[-1]["activeEntries"] == 0
    assert "DEEPDECK_LEAGUE_MATCH " not in output


def test_stop_predicate_prevents_new_reservations_but_keeps_completed_result():
    stopped = [False]
    budget = lifecycle.LeagueRunBudget(
        lifecycle.LeagueRunLimits(), stop_requested=lambda: stopped[0]
    )
    assert budget.reserve()
    stopped[0] = True
    assert budget.expired() and not budget.reserve()
    budget.finish("complete")
    assert budget.completed == 1 and budget.reserved == 0


@pytest.mark.asyncio
async def test_stopped_turn_never_loads_agent_or_connects(monkeypatch, tmp_path):
    from deepdeck_learner.league_rotation_io import read_result, stop_through

    worker = importlib.import_module("deepdeck_examples.run")
    stop, receipt = tmp_path / "stop.json", tmp_path / "result.json"
    stop_through(stop, 7)

    def unexpected(_):
        raise AssertionError("A fenced turn must not load or connect an agent.")

    monkeypatch.setattr(worker, "_agent_and_config", unexpected)
    arguments = worker.parser().parse_args(
        [
            "alexios",
            "--target",
            "ddl",
            "--league-run-id",
            "a" * 32,
            "--league-stop-file",
            str(stop),
            "--league-dispatch-sequence",
            "6",
            "--league-result-file",
            str(receipt),
        ]
    )
    await worker.run(arguments)
    assert read_result(receipt, "a" * 32) == {
        "completedEntries": 0,
        "elapsedSeconds": 0.0,
        "outcome": "cancelled",
        "matchId": None,
    }


def test_parallel_reservations_share_the_limit_and_failures_do_not_count():
    budget = lifecycle.LeagueRunBudget(lifecycle.LeagueRunLimits(max_matches=2))
    assert budget.reserve() and budget.reserve()
    assert not budget.reserve()
    budget.finish("failed")
    assert budget.completed == 0 and budget.reserve()
    budget.finish("complete")
    budget.finish("complete")
    assert budget.completed == 2 and budget.reserved == 0 and not budget.reserve()
    with pytest.raises(RuntimeError):
        budget.finish("complete")


def status_error(status):
    response = httpx.Response(
        status, request=httpx.Request("DELETE", "https://example.invalid/ticket")
    )
    return httpx.HTTPStatusError("Fixture status", request=response.request, response=response)


def expired_budget():
    clock = [0.0]
    budget = lifecycle.LeagueRunBudget(
        lifecycle.LeagueRunLimits(duration_seconds=1), clock=lambda: clock[0]
    )
    clock[0] = 2.0
    return budget


@pytest.mark.asyncio
async def test_expired_waiting_ticket_is_cancelled_but_an_assigned_match_is_served():
    runner = SimpleNamespace(
        cancel_matchmaking_ticket=AsyncMock(), get_matchmaking_ticket=AsyncMock()
    )
    assert (
        await lifecycle.wait_for_limited_match(
            runner, {"id": "ticket", "status": "queued"}, expired_budget()
        )
        is None
    )
    runner.cancel_matchmaking_ticket.assert_awaited_once_with("ticket")
    runner.cancel_matchmaking_ticket.reset_mock()
    assert (
        await lifecycle.wait_for_limited_match(
            runner, {"id": "ticket", "status": "matched", "matchId": "assigned"}, expired_budget()
        )
        == "assigned"
    )
    runner.cancel_matchmaking_ticket.assert_not_awaited()


@pytest.mark.asyncio
async def test_assignment_racing_cancellation_is_reconciled_not_disconnected():
    runner = SimpleNamespace(
        cancel_matchmaking_ticket=AsyncMock(side_effect=status_error(409)),
        get_matchmaking_ticket=AsyncMock(
            return_value={"id": "ticket", "status": "matched", "matchId": "raced"}
        ),
    )
    assert (
        await lifecycle.wait_for_limited_match(
            runner, {"id": "ticket", "status": "queued"}, expired_budget()
        )
        == "raced"
    )
    runner.get_matchmaking_ticket.assert_awaited_once_with("ticket")


@pytest.mark.asyncio
async def test_unconfirmed_cancellation_is_an_error_not_clean_completion():
    runner = SimpleNamespace(
        cancel_matchmaking_ticket=AsyncMock(side_effect=status_error(409)),
        get_matchmaking_ticket=AsyncMock(return_value={"id": "ticket", "status": "queued"}),
    )
    with pytest.raises(RuntimeError, match="Could not confirm"):
        await lifecycle.wait_for_limited_match(
            runner, {"id": "ticket", "status": "queued"}, expired_budget()
        )


@pytest.mark.asyncio
async def test_queued_polling_does_not_overrun_deadline_before_attempting_cancel(monkeypatch):
    clock = [0.0]
    budget = lifecycle.LeagueRunBudget(
        lifecycle.LeagueRunLimits(duration_seconds=1), clock=lambda: clock[0]
    )
    waits = []

    async def sleep(delay):
        waits.append(delay)
        clock[0] += delay

    monkeypatch.setattr(lifecycle.asyncio, "sleep", sleep)
    runner = SimpleNamespace(
        cancel_matchmaking_ticket=AsyncMock(),
        get_matchmaking_ticket=AsyncMock(return_value={"id": "ticket", "status": "queued"}),
    )
    assert (
        await lifecycle.wait_for_limited_match(runner, {"id": "ticket", "status": "queued"}, budget)
        is None
    )
    assert waits == [1.0]


@pytest.mark.asyncio
async def test_registration_errors_cannot_turn_into_false_success_at_deadline(monkeypatch):
    clock = [0.0]
    budget = lifecycle.LeagueRunBudget(
        lifecycle.LeagueRunLimits(duration_seconds=1), clock=lambda: clock[0]
    )

    async def sleep(delay):
        clock[0] += delay

    monkeypatch.setattr(lifecycle.asyncio, "sleep", sleep)
    runner = SimpleNamespace(join_matchmaking=AsyncMock(side_effect=status_error(503)))
    with pytest.raises(RuntimeError, match="outcome could not be confirmed"):
        await lifecycle.join_with_limits(runner, object(), budget, once=False)
    assert runner.join_matchmaking.await_count == 1


@pytest.mark.asyncio
async def test_permanent_join_errors_and_once_failures_are_not_retried():
    runner = SimpleNamespace(join_matchmaking=AsyncMock(side_effect=status_error(403)))
    with pytest.raises(httpx.HTTPStatusError):
        await lifecycle.join_with_limits(
            runner, object(), lifecycle.LeagueRunBudget(lifecycle.LeagueRunLimits()), once=False
        )
    assert runner.join_matchmaking.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode,expected_joins,expected_complete",
    [
        ("count", 3, 2),
        ("parallel", 2, 2),
        ("waiting", 1, 0),
        ("race", 1, 1),
        ("registration", 2, 2),
    ],
)
async def test_actual_worker_enforces_limits_and_closes_only_after_serving_matches(
    monkeypatch, capsys, mode, expected_joins, expected_complete
):
    worker = importlib.import_module("deepdeck_examples.run")
    clock = [0.0]
    real_sleep = asyncio.sleep

    async def fast_sleep(delay):
        clock[0] += delay
        await real_sleep(0)

    monkeypatch.setattr(worker.asyncio, "sleep", fast_sleep)

    async def idle_monitor(*_):
        await asyncio.Event().wait()

    if mode != "registration":
        monkeypatch.setattr(worker, "watch_registration", idle_monitor)
    monkeypatch.setattr(
        worker,
        "_agent_and_config",
        lambda _: (
            object(),
            SimpleNamespace(name="Fixture", agent_id="fixture", formats=("legacy",)),
        ),
    )
    monkeypatch.setattr(
        worker,
        "LeagueRunBudget",
        lambda limits, **kwargs: lifecycle.LeagueRunBudget(
            limits, clock=lambda: clock[0], **kwargs
        ),
    )
    instances = []

    class Runner:
        def __init__(self, **kwargs):
            self.target = kwargs["target"]
            self.joins = 0
            self.cancelled = []
            self.matches = []
            self.closed = False
            self.client = SimpleNamespace(registration=None)
            self.league = self
            self.agent_version_id = "00000000-0000-0000-0000-000000000004"
            instances.append(self)

        async def serve(self):
            try:
                self.client.registration = SimpleNamespace(
                    agent_version_id="registered-platform-version"
                )
                await asyncio.Event().wait()
            finally:
                self.client.registration = None
                self.closed = True

        async def wait_until_connected(self, **_):
            return "connected"

        async def find_event(self, _):
            return {"id": str(__import__("uuid").UUID(int=self.joins + 10)), "ownEntry": None}

        def snapshot(self, phase):
            status = "failed" if mode == "count" and self.joins == 1 else "complete"
            matches = (
                []
                if phase == "registration" or mode == "waiting"
                else [
                    {
                        "matchId": str(__import__("uuid").UUID(int=self.joins + 100)),
                        "status": status,
                        "stage": "season",
                    }
                ]
            )
            return {
                "id": str(__import__("uuid").UUID(int=self.joins + 9)),
                "phase": phase,
                "ownEntry": {
                    "agentVersionId": self.agent_version_id,
                    "deckVersionId": "00000000-0000-0000-0000-000000000003",
                },
                "ownMatches": matches,
            }

        async def join(self, event_id, entry):
            self.joins += 1
            return self.snapshot("registration")

        async def event(self, event_id):
            assert not self.closed
            return self.snapshot("cancelled" if mode == "waiting" else "complete")

        async def join_matchmaking(self, _):
            raise AssertionError("Retired tickets must never be called.")

        async def match(self, match):
            self.matches.append(match)
            return {"summary": {"status": "complete"}}

    monkeypatch.setattr(worker, "AgentRunner", Runner)
    args = [
        "alexios",
        "--target",
        "ddl",
        "--competition-version-id",
        "00000000-0000-0000-0000-000000000002",
        "--deck-version-id",
        "00000000-0000-0000-0000-000000000003",
    ]
    args += (
        ["--league-duration-seconds", "1"]
        if mode in {"waiting", "race"}
        else ["--league-max-matches", "2"]
    )
    if mode == "parallel":
        args += ["--matchmaking-concurrency", "3"]
    await asyncio.wait_for(worker.run(worker.parser().parse_args(args)), timeout=2)
    runner = instances[0]
    assert runner.joins == expected_joins and runner.closed
    reports = [
        json.loads(line.split(" ", 1)[1])
        for line in capsys.readouterr().out.splitlines()
        if line.startswith("DEEPDECK_LEAGUE_RUN ")
    ]
    assert reports[-1]["completedEntries"] == expected_complete
    assert reports[-1]["activeEntries"] == 0
    if mode == "waiting":
        assert runner.cancelled == [] and runner.matches == []
    if mode == "race":
        assert len(runner.matches) == 1
        assert runner.cancelled == []
    if mode == "registration":
        assert any(report["serverRegistered"] for report in reports)
        assert reports[-1]["registrationGeneration"] >= 1


@pytest.mark.asyncio
async def test_registration_reports_sdk_acceptance_and_reconnect_generations():
    runner = SimpleNamespace(client=SimpleNamespace(registration=None))
    changes = asyncio.Queue()
    task = asyncio.create_task(
        lifecycle.watch_registration(
            runner,
            lambda connected, generation: changes.put_nowait((connected, generation)),
            poll_seconds=0,
        )
    )
    try:
        assert await asyncio.wait_for(changes.get(), 1) == (False, 1)
        runner.client.registration = SimpleNamespace(agent_version_id=None)
        assert await asyncio.wait_for(changes.get(), 1) == (False, 2)
        runner.client.registration = SimpleNamespace(agent_version_id="platform-agent-version")
        assert await asyncio.wait_for(changes.get(), 1) == (True, 3)
        # Same platform identity, but a fresh SDK registration object. A brief
        # disconnect must invalidate an earlier capacity-accounting generation.
        runner.client.registration = SimpleNamespace(agent_version_id="platform-agent-version")
        assert await asyncio.wait_for(changes.get(), 1) == (True, 4)
        runner.client.registration = None
        assert await asyncio.wait_for(changes.get(), 1) == (False, 5)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
