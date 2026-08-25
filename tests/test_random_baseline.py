import pytest
from deepdeck_agent import Decision, Game

from deepdeck_examples import build_random_agent


@pytest.mark.asyncio
async def test_public_random_baseline_only_returns_a_legal_id() -> None:
    decision = Decision(
        "d1",
        "p1",
        {
            "kind": "priority",
            "options": [
                {"id": "cast", "kind": "castSpell"},
                {"id": "pass", "kind": "passPriority"},
            ],
        },
        Game({"players": [{"id": "p1"}]}, "p1"),
    )
    response = await build_random_agent(seed=9).make_decision(decision)
    assert response.action_id in {"cast", "pass"}

