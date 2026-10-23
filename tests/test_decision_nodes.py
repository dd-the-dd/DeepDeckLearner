from __future__ import annotations

import copy
import json

import pytest
from deepdeck_agent import Action, Decision, Game

from deepdeck_examples.decision_nodes import evaluate_nodes, normalize_custom_nodes
from deepdeck_examples.forge_reference import ForgeReferenceAgent
from deepdeck_examples.strategic_graph import (
    NODE_IDS,
    StrategyPolicy,
    WeightedDecisionGraph,
    policy_fingerprint,
)
from deepdeck_learner.agent_studio import create_agent, restore_agent_revision, update_agent
from deepdeck_learner.llm_gateway import _execute_tool, _tools


def node(key="refill", **values):
    return {"id": key, "bias": 0.5, **values}


def card(key, owner, name="Brainstorm", text="Draw three cards.", type_line="Instant"):
    return {
        "instanceId": key,
        "owner": owner,
        "controller": owner,
        "definition": {
            "id": key,
            "name": name,
            "manaCost": "{U}",
            "typeLine": type_line,
            "oracleText": text,
            "rules": [{"text": text}],
        },
    }


def decision(*, hand=None, stack=None, kind="priority", options=None):
    source = card("draw", "p1")
    game = Game(
        {
            "turnNumber": 4,
            "activePlayer": 0,
            "step": "postcombatMain",
            "stack": stack or [],
            "players": [
                {
                    "id": key,
                    "life": 20,
                    "hand": (hand if hand is not None else [source]) if key == "p1" else [],
                    "battlefield": [],
                    "library": [],
                    "graveyard": [],
                    "exile": [],
                }
                for key in ("p1", "p2")
            ],
        },
        "p1",
    )
    return Decision(
        "custom:0",
        "p1",
        {
            "kind": kind,
            "options": options
            or [
                {"id": "cast", "kind": "castSpell", "cardInstanceId": "draw"},
                {"id": "pass", "kind": "passPriority"},
            ],
        },
        game,
    )


@pytest.mark.parametrize(
    "bad",
    [
        [node("same"), node("same")],
        [node("card_value")],
        [node("bad.id")],
        [node(weight=float("nan"))],
        [node(bias=float("inf"))],
        [node(weight=True)],
        [node(weight=10**1000)],
        [node(enabled="yes")],
        [node(weight=3)],
        [node(bias=-2)],
        [node(when={"python": "print('no')"})],
        [node(code="pass")],
        [node(when={"actionKinds": ["executeCode"]})],
        [node(when={"sourceTags": [""]})],
        [node(inputs=[{"from": "node.missing"}])],
        [node(inputs=[{"from": "need.missing"}])],
        [node(inputs=[{"from": "base.missing"}])],
        [node(inputs=[{"from": "node.refill"}])],
        [node("a", inputs=[{"from": "node.b"}]), node("b", inputs=[{"from": "node.a"}])],
        [node(inputs=[{"from": "need.draw_cards"}, {"from": "need.draw_cards"}])],
        [node(str(i)) for i in range(33)],
    ],
)
def test_invalid_custom_graph_rejected(bad):
    with pytest.raises(ValueError):
        normalize_custom_nodes(bad, NODE_IDS)


def test_topological_weighted_edges_disable_propagation_and_bound_outputs():
    nodes = normalize_custom_nodes(
        [
            node("child", bias=0, inputs=[{"from": "node.parent", "weight": 2}]),
            node("parent", bias=0.8, weight=2),
        ],
        NODE_IDS,
    )
    context = {
        "actionKind": "castSpell",
        "turn": "ours",
        "role": "control",
        "sourceCards": [],
        "targetCards": [],
    }
    result = evaluate_nodes(nodes, {}, context)
    assert [c[0] for c in result] == ["custom.parent", "custom.child"]
    assert result[0][1:3] == (80, 2)
    assert result[1][1] == 100
    nodes[0]["enabled"] = False
    assert evaluate_nodes(nodes, {}, context) == []


def test_live_board_need_source_filter_and_zero_weight_are_real_score_inputs():
    raw = {
        "deckAdaptation": 0,
        "customNodes": [
            node(
                bias=0,
                when={"actionKinds": ["castSpell"], "sourceTags": ["card_draw"]},
                inputs=[{"from": "need.draw_cards", "weight": 1}],
            )
        ],
    }
    d = decision()
    baseline = WeightedDecisionGraph(StrategyPolicy.from_mapping({})).score_priority(
        d, d.actions[0]
    )
    graph = WeightedDecisionGraph(StrategyPolicy.from_mapping(raw))
    result = graph.score_priority(d, d.actions[0])
    assert result.total - baseline.total == pytest.approx(75)
    assert result.contributions[-1].node == "custom.refill"
    assert not any(
        c.node.startswith("custom.") for c in graph.score_priority(d, d.actions[1]).contributions
    )
    raw["customNodes"][0]["weight"] = 0
    assert (
        WeightedDecisionGraph(StrategyPolicy.from_mapping(raw)).score_priority(d, d.actions[0])
        == baseline
    )


def test_stack_target_uses_stack_controller_and_all_target_filters_match_same_card():
    spell = card(
        "combo",
        "p1",
        name="Show and Tell",
        text="Each player may put a permanent onto the battlefield.",
    )
    source = card("draw", "p1", name="Force of Will", text="Counter target spell.")
    d = decision(
        hand=[source],
        stack=[{"id": "s1", "controller": "p2", "card": spell}],
        options=[
            {
                "id": "cast",
                "kind": "castSpell",
                "cardInstanceId": "draw",
                "targets": {"target": {"stackObject": {"stackId": "s1"}}},
            }
        ],
    )
    raw = {
        "customNodes": [
            node(when={"targetCards": ["Show and Tell"], "targetController": "opponent"})
        ]
    }
    graph = WeightedDecisionGraph(StrategyPolicy.from_mapping(raw))
    assert graph.custom_contributions(d, d.actions[0])[0].score == 50
    d.game.raw["stack"][0]["controller"] = "p1"
    assert not graph.custom_contributions(d, d.actions[0])


def test_old_policy_fingerprint_and_adaptation_preserve_extensions():
    old = StrategyPolicy.from_mapping({})
    custom = StrategyPolicy.from_mapping({"customNodes": [node()]})
    assert old.custom_nodes == ()
    assert policy_fingerprint(old) != policy_fingerprint(custom)
    assert custom.with_detected_mix({"tempo": 1}).custom_nodes == custom.custom_nodes


def test_target_conditions_cannot_mix_name_from_one_card_and_controller_from_another():
    nodes = normalize_custom_nodes(
        [node(when={"targetCards": ["Key piece"], "targetController": "opponent"})], NODE_IDS
    )
    context = {
        "actionKind": "castSpell",
        "turn": "ours",
        "role": "control",
        "sourceCards": [],
        "targetCards": [
            {"name": "Key piece", "controller": "self", "tags": []},
            {"name": "Other piece", "controller": "opponent", "tags": []},
        ],
    }
    assert evaluate_nodes(nodes, {}, context) == []


@pytest.mark.asyncio
async def test_combat_and_bottoming_use_custom_nodes_on_the_correct_source_card():
    creature = card("creature", "p1", name="Defender", text="", type_line="Creature")
    creature["definition"].update(power=2, toughness=2)
    d = decision(
        hand=[creature],
        options=[{"id": "bottom", "kind": "bottomCard", "cardInstanceId": "creature"}],
    )
    agent = ForgeReferenceAgent(
        strategy_graph={"customNodes": [node(when={"sourceCards": ["Defender"]}, bias=-1)]}
    )
    assert (await agent.choose_mulligan_bottom(d)).id == "bottom"
    assert agent.last_decision_explanation["contributions"][0]["score"] == -100
    d.game.raw["players"][0]["battlefield"] = [creature]
    action = Action({"id": "attack", "kind": "declareAttacker", "attackerId": "creature"})
    base_graph = WeightedDecisionGraph(StrategyPolicy.from_mapping({}))
    graph = WeightedDecisionGraph(
        StrategyPolicy.from_mapping(
            {
                "customNodes": [
                    node(
                        when={"sourceCards": ["Defender"], "actionKinds": ["declareAttacker"]},
                        bias=-1,
                    )
                ]
            }
        )
    )
    assert graph.score_attack(d, action).total - base_graph.score_attack(
        d, action
    ).total == pytest.approx(-100)


@pytest.mark.asyncio
async def test_custom_node_changes_mulligan_choice_without_bypassing_four_card_guard():
    hand = [
        card(f"land{i}", "p1", name="Island", text="", type_line="Basic Land — Island")
        for i in range(7)
    ]
    d = decision(
        hand=hand,
        kind="mulligan",
        options=[{"id": "keep", "kind": "keepHand"}, {"id": "mull", "kind": "takeMulligan"}],
    )
    agent = ForgeReferenceAgent(
        strategy_graph={"customNodes": [node(when={"actionKinds": ["keepHand"]}, bias=1, weight=2)]}
    )
    assert (await agent.choose_mulligan(d)).id == "keep"
    assert any(
        c["node"] == "custom.refill" for c in agent.last_decision_explanation["contributions"]
    )
    cautious = ForgeReferenceAgent(
        strategy_graph={
            "customNodes": [node(when={"actionKinds": ["takeMulligan"]}, bias=1, weight=2)]
        }
    )
    d.request_id = "custom:3"
    assert (await cautious.choose_mulligan(d)).id == "keep"


def test_sdk_tool_and_revisions_preserve_validate_and_restore_custom_graph(tmp_path):
    deck = tmp_path / ".deepdeck" / "decks" / "test.json"
    deck.parent.mkdir(parents=True)
    deck.write_text(
        json.dumps(
            {"id": "test", "name": "Test", "format": "legacy", "playableCardCount": 60, "cards": []}
        ),
        encoding="utf-8",
    )
    policy = create_agent(
        tmp_path, {"name": "Custom graph", "format": "legacy", "deckVersionIds": ["test"]}
    )
    schema = next(
        t["function"]["parameters"] for t in _tools() if t["function"]["name"] == "update_agent"
    )
    assert schema["properties"]["strategy_graph"]["properties"]["customNodes"]["maxItems"] == 32
    saved = _execute_tool(
        tmp_path,
        "update_agent",
        {
            "agent_id": policy["id"],
            "expected_revision": 1,
            "strategy_graph": {"customNodes": [node()]},
            "revision_reason": "Refill intentionally",
        },
        lambda _: {},
    )
    assert saved["strategyGraph"]["customNodes"][0]["id"] == "refill"
    preserved = update_agent(tmp_path, policy["id"], {"name": "Still custom"})
    assert preserved["strategyGraph"]["customNodes"] == saved["strategyGraph"]["customNodes"]
    invalid = copy.deepcopy(saved["strategyGraph"])
    invalid["customNodes"][0]["inputs"] = [{"from": "node.refill"}]
    with pytest.raises(ValueError, match="cycle"):
        update_agent(tmp_path, policy["id"], {"strategyGraph": invalid})
    restored = restore_agent_revision(tmp_path, policy["id"], 1, preserved["revision"])
    assert restored["strategyGraph"]["customNodes"] == []
