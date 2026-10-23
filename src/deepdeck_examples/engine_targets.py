"""Read current Engine target references through the Agent SDK's public views.

The Engine serializes enum references as {"permanent": {"instanceId": ...}};
older SDK convenience properties expect {"kind": "permanent", ...}. Keep the
original legal action untouched and adapt only the read-only helper view.
"""

from __future__ import annotations

from typing import Any

from deepdeck_agent import Action, Decision


def target_references(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for key, reference in value.items():
        if not isinstance(reference, dict):
            continue
        if reference.get("kind") in {"permanent", "player", "card", "stackObject"}:
            result[key] = reference
            continue
        for kind, identifier in (
            ("permanent", "instanceId"),
            ("player", "playerId"),
            ("card", "instanceId"),
            ("stackObject", "stackId"),
        ):
            nested = reference.get(kind)
            if isinstance(nested, dict) and isinstance(nested.get(identifier), str):
                result[key] = {"kind": kind, identifier: nested[identifier]}
                break
    return result


class EngineAction(Action):
    @property
    def targets(self) -> dict[str, dict[str, Any]]:
        return target_references(self.raw.get("targets"))


class EngineDecision(Decision):
    @property
    def actions(self) -> list[Action]:
        return [EngineAction(action.raw) for action in super().actions]


def compatible_action(action: Action) -> Action:
    return action if isinstance(action, EngineAction) else EngineAction(action.raw)


def compatible_decision(decision: Decision) -> Decision:
    return (
        decision
        if isinstance(decision, EngineDecision)
        else EngineDecision(decision.request_id, decision.player_id, decision.raw, decision.game)
    )
