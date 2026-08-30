import pytest

from oracle_ai.decision_choices import expand_policy_actions


def test_number_selection_expands_only_inside_the_policy_boundary() -> None:
    actions = expand_policy_actions({
        "choice": {
            "kind": "numberSelection",
            "decisionId": "loopIterations",
            "minimum": 0,
            "maximum": 2,
        },
        "options": [{
            "id": "choose-number:loopIterations",
            "kind": "chooseResolution",
            "playerId": "player-1",
        }],
    })

    assert [action["_numberValue"] for action in actions] == [0, 1, 2]
    assert {action["_engineActionId"] for action in actions} == {
        "choose-number:loopIterations"
    }
    assert [action["decisions"]["loopIterations"] for action in actions] == [0, 1, 2]


def test_number_selection_rejects_an_unbounded_policy_expansion() -> None:
    with pytest.raises(ValueError, match="too large"):
        expand_policy_actions({
            "choice": {
                "kind": "numberSelection",
                "minimum": 0,
                "maximum": 1_001,
            },
            "options": [{"id": "choose-number"}],
        })
