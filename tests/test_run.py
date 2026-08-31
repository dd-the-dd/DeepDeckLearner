from __future__ import annotations

import asyncio
from argparse import Namespace

import pytest
from deepdeck_agent import AgentConfig

from deepdeck_examples.run import _serve_local, parser


def test_same_demo_command_exposes_local_and_public_targets() -> None:
    local = parser().parse_args(["alexios", "--target", "local"])
    public = parser().parse_args(["alexios", "--target", "ddl"])
    assert local.target == "local"
    assert public.target == "ddl"


def test_v11_and_v12_are_available_but_untrained_weights_require_opt_in() -> None:
    v11 = parser().parse_args(["v11", "--target", "local", "--allow-untrained"])
    v12 = parser().parse_args(["v12", "--target", "ddl", "--checkpoint", "checkpoint"])
    assert v11.allow_untrained is True
    assert v12.checkpoint == "checkpoint"


@pytest.mark.asyncio
async def test_local_target_can_create_a_game_after_agent_registration() -> None:
    class FakeRunner:
        def __init__(self) -> None:
            self.config = AgentConfig(
                agent_id="example",
                name="Example",
                version="1",
                author="Tester",
                formats=("commander",),
            )
            self.started = asyncio.Event()
            self.game = None

        async def serve(self) -> None:
            await asyncio.Event().wait()

        async def wait_until_connected(self, *, timeout: float) -> str:
            assert timeout == 30
            return "agent:example"

        async def start_local_game(self, game):
            self.game = game
            self.started.set()
            return {"session": {"id": "local-game"}}

    runner = FakeRunner()
    arguments = Namespace(
        start_local_game=True,
        local_deck_session_id="our-deck",
        local_opponent_deck_session_id="their-deck",
        local_opponent_controller="ai-random",
        local_format="commander",
        local_max_turns=80,
        seed=7,
    )
    task = asyncio.create_task(_serve_local(runner, arguments))  # type: ignore[arg-type]
    await asyncio.wait_for(runner.started.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert runner.game is not None
    payload = runner.game.payload()
    assert payload["gameMode"] == "commander"
    assert payload["aiControllerByPlayerId"] == {
        "example-agent": "agent:example",
        "local-opponent": "ai-random",
    }
