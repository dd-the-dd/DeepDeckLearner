from __future__ import annotations

from copy import deepcopy

import pytest
from deepdeck_agent import Action, Decision, Game

from deepdeck_examples.engine_targets import compatible_action, compatible_decision
from deepdeck_examples.forge_reference import ForgeReferenceAgent
from deepdeck_examples.strategic_graph import (
    StrategyPolicy,
    WeightedDecisionGraph,
    available_colors,
)


def card(
    identifier, name, controller, *, text="", type_line="Land", mana="", rules=None, tapped=False
):
    return {
        "instanceId": identifier,
        "owner": controller,
        "controller": controller,
        "tapped": tapped,
        "definition": {
            "id": identifier,
            "name": name,
            "typeLine": type_line,
            "manaCost": mana,
            "oracleText": text,
            "rules": rules or [],
        },
    }


def wasteland():
    return card(
        "waste",
        "Wasteland",
        "p1",
        text="{T}: Add {C}. {T}, Sacrifice Wasteland: Destroy target nonbasic land.",
        rules=[
            {"kind": "manaAbility", "effects": [{"kind": "addMana", "mana": "{C}"}]},
            {
                "kind": "activatedAbility",
                "costs": [
                    {"kind": "tap", "object": {"kind": "self"}},
                    {"kind": "sacrificePermanent", "permanent": {"kind": "self"}},
                ],
                "effects": [
                    {
                        "kind": "destroyPermanent",
                        "permanent": {"kind": "chosenTarget", "id": "targetPermanent"},
                    }
                ],
                "declaration": {
                    "decisions": [
                        {
                            "kind": "chooseTargets",
                            "candidates": {
                                "kind": "permanents",
                                "where": {"kind": "cardTypeContains", "value": "Land"},
                            },
                        }
                    ]
                },
            },
        ],
    )


def choice(
    targets, *, own_extra=(), enemy_extra=(), own_hand=(), enemy_hand=(), public=(), options=None
):
    source = wasteland()
    players = [
        {
            "id": "p1",
            "life": 20,
            "battlefield": [source, *own_extra],
            "hand": list(own_hand),
            "graveyard": [],
            "exile": [],
            "library": [],
        },
        {
            "id": "p2",
            "life": 20,
            "battlefield": [*targets, *enemy_extra],
            "hand": list(enemy_hand),
            "graveyard": list(public),
            "exile": [],
            "library": [],
        },
    ]
    actions = (
        options
        if options is not None
        else [
            {
                "id": f"activate:{target['instanceId']}",
                "kind": "activateAbility",
                "cardInstanceId": "waste",
                "targets": {"targetPermanent": {"permanent": {"instanceId": target["instanceId"]}}},
                "decisions": {
                    "abilityRuleIndex": 1,
                    "tapSource": True,
                    "targetPermanent": [{"permanent": {"instanceId": target["instanceId"]}}],
                },
            }
            for target in [source, *targets]
        ]
    )
    return Decision(
        "priority:4:1:0",
        "p1",
        {"kind": "priority", "options": [*actions, {"id": "pass", "kind": "passPriority"}]},
        Game(
            {
                "turnNumber": 4,
                "step": "postcombatMain",
                "activePlayer": 0,
                "players": players,
                "stack": [],
            },
            "p1",
        ),
    )


@pytest.mark.parametrize("shape", ["current-engine", "flat-sdk"])
def test_target_adapter_reads_permanents_players_stack_and_cards_without_changing_legal_actions(
    shape,
):
    entries = {
        "land": ("permanent", "instanceId", "sea"),
        "pilot": ("player", "playerId", "p2"),
        "spell": ("stackObject", "stackId", "stack:3"),
        "grave": ("card", "instanceId", "card:4"),
    }
    targets = {
        key: {kind: {field: value}} if shape == "current-engine" else {"kind": kind, field: value}
        for key, (kind, field, value) in entries.items()
    }
    raw = {"id": "unchanged-legal-action", "targets": targets}
    original = deepcopy(raw)
    action = compatible_action(Action(raw))
    assert action.target_permanent_ids == ("sea",)
    assert action.target_player_ids == ("p2",)
    assert action.target_stack_ids == ("stack:3",)
    assert action.raw == original and action.id == raw["id"]


@pytest.mark.asyncio
@pytest.mark.parametrize("archetype", ["aggro", "tempo", "midrange", "control", "combo"])
@pytest.mark.parametrize("temperature", [0, 0.05, 1])
async def test_does_not_sacrifice_wasteland_targeting_itself_when_no_opposing_land_is_available(
    archetype, temperature
):
    decision = choice([])
    original = deepcopy(decision.raw)
    agent = ForgeReferenceAgent(
        strategy_graph={"archetypeMix": {archetype: 1}, "temperature": temperature}
    )
    result = await agent.make_decision(decision)
    assert result.action_id == "pass"
    assert decision.raw == original


@pytest.mark.asyncio
async def test_color_denial_prefers_the_only_blue_source_over_redundant_black_mana():
    sea = card("sea", "Underground Sea", "p2", type_line="Land — Island Swamp")
    black = card("black", "Black source", "p2", text="{T}: Add {B}.")
    basic = card("swamp", "Swamp", "p2", type_line="Basic Land — Swamp")
    demand = card("public-spell", "Revealed blue spell", "p2", type_line="Instant", mana="{U}{U}")
    decision = choice(
        [sea, black],
        enemy_extra=[basic],
        public=[demand],
        own_extra=[card("our-island", "Island", "p1", type_line="Basic Land — Island")] * 3,
    )
    agent = ForgeReferenceAgent(strategy_graph={"archetypeMix": {"tempo": 1}})
    assert (await agent.make_decision(decision)).action_id == "activate:sea"
    assert any(
        "cut colors U" in item["reason"]
        for item in agent.last_decision_explanation["selected"]["contributions"]
    )


@pytest.mark.asyncio
async def test_combo_land_pattern_can_outweigh_generic_color_denial():
    depths = card("depths", "My combo land", "p2")
    sea = card("sea", "Underground Sea", "p2", type_line="Land — Island Swamp")
    decision = choice(
        [depths, sea],
        own_extra=[card("island", "Island", "p1", type_line="Basic Land — Island")] * 3,
    )
    agent = ForgeReferenceAgent(
        strategy_graph={
            "patterns": [
                {
                    "id": "stop-combo-land",
                    "cardNames": ["My combo land"],
                    "addTags": ["combo_enabler"],
                    "nodeAdjustments": {"combo_disruption": 1},
                }
            ]
        }
    )
    assert (await agent.make_decision(decision)).action_id == "activate:depths"


def test_utility_and_untapped_mana_are_scored_but_hidden_opposing_hand_is_not_used():
    utility = card("draw", "Drawing land", "p2", text="{T}: Draw a card.")
    plain = card("plain", "Plain nonbasic", "p2")
    decision = choice([utility, plain], own_extra=[card("island", "Island", "p1")] * 3)
    policy = StrategyPolicy.from_mapping({})
    scores = {
        action.id: WeightedDecisionGraph(policy).score_priority(decision, action).total
        for action in decision.actions
    }
    assert scores["activate:draw"] > scores["activate:plain"]
    hidden = choice(
        [utility, plain],
        own_extra=[card("island", "Island", "p1")] * 3,
        enemy_hand=[card("hidden", "Secret spell", "p2", mana="{W}{W}{W}", type_line="Sorcery")],
    )
    assert scores == {
        action.id: WeightedDecisionGraph(policy).score_priority(hidden, action).total
        for action in hidden.actions
    }


@pytest.mark.asyncio
async def test_preserves_our_only_mana_source_against_redundant_opposing_lands():
    targets = [
        card(f"enemy-{index}", "Redundant land", "p2", text="{T}: Add {B}.") for index in range(5)
    ]
    spell = card("our-spell", "One mana play", "p1", type_line="Creature", mana="{1}")
    assert (
        await ForgeReferenceAgent().make_decision(choice(targets, own_hand=[spell]))
    ).action_id == "pass"


def test_typed_fetch_does_not_offer_all_five_colors_and_activation_cost_is_not_production():
    assert available_colors(
        card(
            "fetch", "Polluted Delta", "p2", text="Search your library for an Island or Swamp card."
        )
    ) == frozenset("UB")
    assert (
        available_colors(
            card(
                "utility",
                "Utility land",
                "p2",
                text="{T}: Add {C}. {R}{G}: Target creature gets +1/+0.",
            )
        )
        == frozenset()
    )


@pytest.mark.asyncio
async def test_wasteland_mana_ability_is_not_mistaken_for_land_destruction_or_floated_for_nothing():
    mana = {
        "id": "mana:waste",
        "kind": "activateAbility",
        "cardInstanceId": "waste",
        "decisions": {"manaAbility": True, "abilityRuleIndex": 0, "manaProduced": ["C"]},
    }
    decision = choice([], options=[mana])
    agent = ForgeReferenceAgent()
    assert (await agent.make_decision(decision)).action_id == "pass"


def test_sdk_view_can_select_and_normalize_the_original_legal_action_id():
    original = choice([card("sea", "Underground Sea", "p2")])
    adapted = compatible_decision(original)
    action = next(action for action in adapted.actions if action.id == "activate:sea")
    assert adapted.target_permanents(action)[0].name == "Underground Sea"
    assert adapted.normalize(action).action_id == "activate:sea"


def test_compiled_big_mana_land_is_more_valuable_than_a_redundant_single_source():
    tomb = card(
        "tomb",
        "Big mana land",
        "p2",
        rules=[{"kind": "manaAbility", "effects": [{"kind": "addMana", "mana": "{C}{C}"}]}],
    )
    plain = card(
        "plain",
        "Plain nonbasic",
        "p2",
        rules=[{"kind": "manaAbility", "effects": [{"kind": "addMana", "mana": "{C}"}]}],
    )
    decision = choice([tomb, plain], own_extra=[card("island", "Island", "p1")] * 3)
    policy = StrategyPolicy.from_mapping({})
    scores = {
        action.id: WeightedDecisionGraph(policy).score_priority(decision, action)
        for action in decision.actions
    }
    assert scores["activate:tomb"].total > scores["activate:plain"].total
    assert any(
        "more than one mana" in contribution.reason
        for contribution in scores["activate:tomb"].contributions
    )
