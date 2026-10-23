"""Bounded, declarative extensions to the reusable strategy graph; never Python eval."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

SIGNALS = {
    "need.develop_board": "Need more board presence",
    "need.draw_cards": "Need more cards",
    "need.remove_permanent": "Need to answer a permanent",
    "need.answer_stack": "Opposing spell on the stack",
    "need.protect_advantage": "Advantage worth protecting",
    "need.stabilize_life": "Danger from the opposing clock",
    "need.advance_win_condition": "Need to advance our win condition",
    "need.preserve_resources": "Need to preserve resources",
    "state.our_turn": "Our turn",
    "state.stack_busy": "Stack is not empty",
    "state.our_life": "Our life / 20",
    "state.our_clock": "Our clock urgency (1 / turns)",
    "state.opposing_clock": "Opposing clock urgency (1 / turns)",
    "state.material": "Board material advantage (signed)",
    "hand.lands": "Lands in our hand / 5",
    "hand.size": "Cards in our hand / 7",
}
ACTION_KINDS = (
    "castSpell",
    "activateAbility",
    "specialAction",
    "playLand",
    "passPriority",
    "declareAttacker",
    "finishAttackers",
    "declareBlocker",
    "finishBlockers",
    "keepHand",
    "takeMulligan",
    "bottomCard",
)


def _number(raw: Any, default: float, low: float, high: float, field: str) -> float:
    value = default if raw is None else raw
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number.")
    if not low <= value <= high or not math.isfinite(value):
        raise ValueError(f"{field} must be between {low} and {high}.")
    return float(value)


def normalize_custom_nodes(
    raw: Any, base_ids: Sequence[str] | frozenset[str]
) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > 32:
        raise ValueError("customNodes must contain at most 32 decision nodes.")
    result: list[dict[str, Any]] = []
    identifiers: set[str] = set()
    string_fields = {"actionKinds", "sourceCards", "sourceTags", "targetCards", "targetTags"}
    for item in raw:
        if not isinstance(item, dict) or set(item) - {
            "id",
            "name",
            "enabled",
            "weight",
            "bias",
            "when",
            "inputs",
        }:
            raise ValueError("Custom nodes must use only the documented fields.")
        key = item.get("id")
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", key):
            raise ValueError("Custom node ids use 1–64 letters, numbers, dashes or underscores.")
        if key in identifiers or key in base_ids:
            raise ValueError("Custom node ids must be unique and distinct from base nodes.")
        identifiers.add(key)
        name = item.get("name", key)
        if not isinstance(name, str) or not name.strip() or len(name) > 100:
            raise ValueError("Custom node names need 1–100 characters.")
        enabled = item.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ValueError("Custom node enabled must be a boolean.")
        when = item.get("when", {})
        if not isinstance(when, dict) or set(when) - string_fields - {
            "turn",
            "role",
            "targetController",
        }:
            raise ValueError("Unknown custom node condition.")
        clean_when: dict[str, Any] = {}
        for field in sorted(string_fields):
            values = when.get(field, [])
            if (
                not isinstance(values, list)
                or len(values) > 30
                or any(not isinstance(v, str) or not v.strip() or len(v) > 160 for v in values)
            ):
                raise ValueError(f"Node {field} needs at most 30 nonempty short strings.")
            clean_when[field] = list(dict.fromkeys(v.strip() for v in values))
        if set(clean_when["actionKinds"]) - set(ACTION_KINDS):
            raise ValueError("Unknown custom node action kind.")
        for field, choices in {
            "turn": ("any", "ours", "opponent"),
            "role": ("any", "beatdown", "control"),
            "targetController": ("any", "self", "opponent"),
        }.items():
            value = when.get(field, "any")
            if value not in choices:
                raise ValueError(f"Unknown node {field} condition.")
            clean_when[field] = value
        inputs = item.get("inputs", [])
        if not isinstance(inputs, list) or len(inputs) > 12:
            raise ValueError("A node accepts at most 12 weighted inputs.")
        clean_inputs = []
        seen = set()
        for edge in inputs:
            if (
                not isinstance(edge, dict)
                or set(edge) - {"from", "weight"}
                or not isinstance(edge.get("from"), str)
            ):
                raise ValueError("Node inputs need a signal name and a weight.")
            if edge["from"] in seen:
                raise ValueError("A node cannot repeat an input.")
            seen.add(edge["from"])
            clean_inputs.append(
                {
                    "from": edge["from"],
                    "weight": _number(edge.get("weight"), 1, -2, 2, "Input weight"),
                }
            )
        result.append(
            {
                "id": key,
                "name": name.strip(),
                "enabled": enabled,
                "weight": _number(item.get("weight"), 1, 0, 2, "Node weight"),
                "bias": _number(item.get("bias"), 0, -1, 1, "Node bias"),
                "when": clean_when,
                "inputs": clean_inputs,
            }
        )
    allowed = (
        set(SIGNALS) | {f"base.{key}" for key in base_ids} | {f"node.{key}" for key in identifiers}
    )
    for item in result:
        if any(edge["from"] not in allowed for edge in item["inputs"]):
            raise ValueError(
                "Unknown node input; use a catalog signal, base concern or custom node."
            )
    return ordered_nodes(result)


def ordered_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {node["id"]: node for node in nodes}
    visiting: set[str] = set()
    done: set[str] = set()
    result = []

    def visit(key: str) -> None:
        if key in visiting:
            raise ValueError("Custom decision graph contains a cycle.")
        if key in done:
            return
        visiting.add(key)
        for edge in by_id[key]["inputs"]:
            if edge["from"].startswith("node."):
                visit(edge["from"][5:])
        visiting.remove(key)
        done.add(key)
        result.append(by_id[key])

    for key in by_id:
        visit(key)
    return result


def evaluate_nodes(
    nodes: Sequence[Mapping[str, Any]], signals: Mapping[str, float], context: Mapping[str, Any]
) -> list[tuple[str, float, float, str]]:
    """Return (id, raw score, weight, reason), in validated topological order."""
    outputs = dict(signals)
    result = []
    for node in nodes:
        when = node["when"]
        active = node["enabled"] and node["weight"] > 0
        active = active and (
            not when["actionKinds"] or context["actionKind"] in when["actionKinds"]
        )
        active = active and (when["turn"] == "any" or when["turn"] == context["turn"])
        active = active and (when["role"] == "any" or when["role"] == context["role"])
        for prefix in ("source", "target"):
            names, tags = when[f"{prefix}Cards"], when[f"{prefix}Tags"]
            relation = when["targetController"] if prefix == "target" else "any"
            if names or tags or relation != "any":
                active = active and any(
                    (not names or card["name"].casefold() in {n.casefold() for n in names})
                    and set(tags).issubset(card["tags"])
                    and (relation == "any" or card["controller"] == relation)
                    for card in context[f"{prefix}Cards"]
                )
        value = (
            max(
                -1.0,
                min(
                    1.0,
                    node["bias"]
                    + sum(
                        outputs.get(edge["from"], 0.0) * edge["weight"] for edge in node["inputs"]
                    ),
                ),
            )
            if active
            else 0.0
        )
        outputs[f"node.{node['id']}"] = max(-1.0, min(1.0, value * node["weight"]))
        if value:
            result.append(
                (
                    f"custom.{node['id']}",
                    value * 100,
                    node["weight"],
                    f"{node['name']}: matching conditions; weighted inputs and bias, bounded to ±1.",
                )
            )
    return result
