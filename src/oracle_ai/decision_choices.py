from __future__ import annotations

from typing import Any


def expand_policy_actions(decision: dict[str, Any]) -> list[dict[str, Any]]:
    actions = [dict(action) for action in decision.get("options", [])]
    choice = decision.get("choice")
    if not isinstance(choice, dict) or choice.get("kind") != "numberSelection":
        return actions
    if len(actions) != 1:
        raise ValueError("number selection must expose exactly one engine action")
    minimum = int(choice.get("minimum", 0))
    maximum = int(choice.get("maximum", minimum))
    if maximum < minimum or maximum - minimum > 1_000:
        raise ValueError("number selection range is invalid or too large")
    decision_id = str(choice.get("decisionId", "numberValue"))
    engine_action = actions[0]
    return [
        {
            **engine_action,
            "id": f"{engine_action['id']}:value:{number_value}",
            "label": f"{engine_action.get('label', 'Choose a number')}: {number_value}",
            "decisions": {
                **dict(engine_action.get("decisions") or {}),
                decision_id: number_value,
            },
            "_engineActionId": engine_action["id"],
            "_numberValue": number_value,
        }
        for number_value in range(minimum, maximum + 1)
    ]
