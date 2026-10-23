"""Combat facts must not be inferred from mentions of future/other abilities."""

from __future__ import annotations

import pytest
from deepdeck_agent import Card, Decision, Game

from deepdeck_examples.forge_reference import ForgeReferenceAgent
from deepdeck_examples.strategic_graph import (
    StrategyPolicy,
    WeightedDecisionGraph,
    _combat_exchange,
    assess_board,
    combat_keywords,
)


def creature(key="small", power=1, toughness=2, *, controller="p1", keywords=(), **extra):
    return {
        "instanceId": key,
        "owner": controller,
        "controller": controller,
        "tapped": False,
        "definition": {
            "id": key,
            "name": key,
            "typeLine": "Creature",
            "power": str(power),
            "toughness": str(toughness),
            "rules": [
                {"kind": "keywordAbility", "ability": {"kind": keyword}} for keyword in keywords
            ],
        },
        **extra,
    }


def combat(attacker=None, blockers=None, *, opponent_life=20, our_life=20, hand=()):
    attacker = attacker or creature()
    state = {
        "turnNumber": 6,
        "activePlayer": 0,
        "step": "declareAttackers",
        "stack": [],
        "players": [
            {"id": "p1", "life": our_life, "battlefield": [attacker], "hand": list(hand)},
            {"id": "p2", "life": opponent_life, "battlefield": blockers or [], "hand": []},
        ],
    }
    return Decision(
        "attack-regression",
        "p1",
        {
            "kind": "attackers",
            "options": [
                {
                    "id": "attack",
                    "kind": "declareAttacker",
                    "attackerId": attacker["instanceId"],
                    "targets": {"defender": {"player": {"playerId": "p2"}}},
                },
                {"id": "finish", "kind": "finishAttackers"},
            ],
        },
        Game(state, "p1"),
    )


def choose(choice, *, profile="balanced", temperature=0):
    graph = WeightedDecisionGraph(
        StrategyPolicy.from_mapping({"temperature": temperature}, profile=profile)
    )
    scores = [graph.score_attack(choice, action) for action in choice.actions]
    return graph.choose(choice, scores), scores


@pytest.mark.parametrize("profile", ["aggressive", "balanced", "cautious"])
@pytest.mark.parametrize("temperature", [0, 0.05, 1])
def test_does_not_feed_one_two_to_four_four_even_with_noise(profile, temperature):
    choice = combat(blockers=[creature("big", 4, 4, controller="p2")])
    selected, scores = choose(choice, profile=profile, temperature=temperature)
    assert selected.action_id == "finish"
    # Exploration cannot reach an obviously losing attack from ordinary profiles.
    assert scores[1].total - scores[0].total > 70


def guide_of_souls():
    guide = creature("Guide of Souls")
    guide["definition"]["oracleText"] = (
        "Whenever another creature you control enters, you gain 1 life and get {E}.\n"
        "Whenever you attack, you may pay {E}{E}{E}. When you do, put two +1/+1 counters "
        "and a flying counter on target attacking creature."
    )
    guide["definition"]["rules"] = [
        {
            "kind": "triggeredAbility",
            "event": {"kind": "controlledCreaturesAttacked", "minimum": 1},
            "effects": [
                {
                    "kind": "optionalPayCostCreateReflexiveTrigger",
                    "cost": {"kind": "payPlayerCounters"},
                    "ability": {"effects": [{"kind": "putCounters", "counter": "flying"}]},
                }
            ],
        }
    ]
    return guide


@pytest.mark.asyncio
async def test_real_guide_of_souls_regression_uses_sdk_agent_not_textual_flying():
    guide = guide_of_souls()
    assert "flying" not in combat_keywords(Card(guide))
    choice = combat(guide, [creature("Thought-Knot Seer", 4, 4, controller="p2")])
    agent = ForgeReferenceAgent(strategy_graph={"temperature": 0.05})
    assert (await agent.make_decision(choice)).action_id == "finish"
    attack = next(
        item
        for item in agent.last_decision_explanation["alternatives"]
        if item["actionId"] == "attack"
    )
    assert "evasion" not in attack["tags"]
    assert not any("likely to connect" in item["reason"] for item in attack["contributions"])


def test_actual_flying_counter_not_its_mention_enables_attack():
    guide = guide_of_souls()
    guide["counters"] = {"flying": 1}
    assert "flying" in combat_keywords(Card(guide))
    assert choose(combat(guide, [creature("big", 4, 4, controller="p2")]))[0].action_id == "attack"


@pytest.mark.parametrize("keyword", ["flying", "reach"])
def test_flying_does_not_evade_flying_or_reach_blocker(keyword):
    small = creature(keywords=["flying"])
    big = creature("big", 4, 4, controller="p2", keywords=[keyword])
    assert choose(combat(small, [big]))[0].action_id == "finish"


def test_trample_does_not_mean_unblockable_or_false_lethal():
    small = creature(keywords=["trample"])
    choice = combat(small, [creature("big", 4, 4, controller="p2")], opponent_life=1)
    selected, scores = choose(choice)
    assert selected.action_id == "finish"
    assert not any(c.raw >= 10000 for c in scores[0].contributions)


def test_menace_needs_two_blockers_but_can_be_blocked():
    small = creature(keywords=["menace"])
    big = creature("big", 4, 4, controller="p2")
    assert choose(combat(small, [big]))[0].action_id == "attack"
    second = creature("second", 2, 2, controller="p2")
    assert choose(combat(small, [big, second]))[0].action_id == "finish"


def test_rejects_double_block_that_wins_nothing():
    attacker = creature(power=3, toughness=3)
    blockers = [creature(f"wall-{i}", 2, 4, controller="p2") for i in range(2)]
    assert choose(combat(attacker, blockers))[0].action_id == "finish"


def test_tapped_blocker_does_not_stop_damage_or_lethal():
    big = creature("big", 4, 4, controller="p2", tapped=True)
    selected, scores = choose(combat(blockers=[big], opponent_life=1))
    assert selected.action_id == "attack"
    assert any(c.raw == 10000 for c in scores[0].contributions)


def test_blocks_on_other_players_board_do_not_protect_target_player():
    choice = combat()
    choice.game.raw["players"].append(
        {
            "id": "p3",
            "life": 20,
            "battlefield": [creature("other-player-big", 8, 8, controller="p3")],
        }
    )
    assert choose(choice)[0].action_id == "attack"


def test_instants_in_hand_or_open_mana_are_not_imaginary_combat_tricks():
    instant = {"instanceId": "instant", "definition": {"name": "Brainstorm", "typeLine": "Instant"}}
    choice = combat(blockers=[creature("big", 4, 4, controller="p2")], hand=[instant])
    choice.game.raw["players"][0]["manaPool"] = [{"symbol": "U"}] * 4
    assert choose(choice)[0].action_id == "finish"


def test_first_strike_and_deathtouch_are_real_combat_advantages():
    attacker = Card(creature(power=1, toughness=1, keywords=["firstStrike", "deathtouch"]))
    blocker = Card(creature("big", 4, 4, controller="p2"))
    exchange, damage, survives = _combat_exchange(attacker, [blocker])
    assert survives and exchange > 0 and damage == 0


def test_double_strike_and_marked_damage_are_counted():
    attacker = Card(creature(power=2, toughness=4, keywords=["doubleStrike"]))
    blocker = Card(creature("big", 3, 5, controller="p2", damageMarked=1))
    assert _combat_exchange(attacker, [blocker])[0] > 0


def test_direct_attack_draw_trigger_can_justify_spending_a_creature():
    attacker = creature(power=1, toughness=1)
    attacker["definition"]["rules"] = [
        {
            "kind": "triggeredAbility",
            "event": {"kind": "declaredAttacker", "object": {"kind": "self"}},
            "effects": [
                {
                    "kind": "drawCards",
                    "count": {"kind": "integer", "value": 2},
                    "player": {"kind": "controllerOf", "object": {"kind": "self"}},
                }
            ],
        }
    ]
    choice = combat(attacker, [creature("big", 4, 4, controller="p2")])
    # This test specifically weights the user's stated desire to collect cards.
    graph = WeightedDecisionGraph(
        StrategyPolicy.from_mapping({"weights": {"card_advantage": 2}}, profile="cautious")
    )
    scores = [graph.score_attack(choice, action) for action in choice.actions]
    assert graph.choose(choice, scores).action_id == "attack"


def test_custom_node_can_deliberately_override_bad_combat_without_changing_legality():
    choice = combat(blockers=[creature("big", 4, 4, controller="p2")])
    policy = StrategyPolicy.from_mapping(
        {
            "customNodes": [
                {
                    "id": "sacrifice-plan",
                    "bias": 1,
                    "weight": 2,
                    "when": {"actionKinds": ["declareAttacker"]},
                    "inputs": [],
                }
            ]
        }
    )
    graph = WeightedDecisionGraph(policy)
    scores = [graph.score_attack(choice, action) for action in choice.actions]
    assert graph.choose(choice, scores).action_id == "attack"


def test_smaller_and_larger_blocker_are_not_combined_into_an_imaginary_trade():
    choice = combat(
        creature(power=3, toughness=3),
        [
            creature("small-blocker", 1, 1, controller="p2"),
            creature("large-blocker", 4, 4, controller="p2"),
        ],
    )
    assert choose(choice, profile="aggressive")[0].action_id == "finish"


def test_surviving_a_block_without_gaining_anything_is_not_pressure():
    choice = combat(creature(power=1, toughness=5), [creature("wall", 4, 4, controller="p2")])
    assert choose(choice)[0].action_id == "finish"


def test_planeswalker_target_does_not_get_its_controllers_life_lethal_bonus():
    choice = combat(opponent_life=1)
    choice.game.raw["players"][1]["battlefield"] = [
        {
            "instanceId": "walker",
            "controller": "p2",
            "definition": {"name": "Walker", "typeLine": "Planeswalker"},
        }
    ]
    choice.raw["options"][0]["targets"] = {"defender": {"permanent": {"instanceId": "walker"}}}
    _, scores = choose(choice)
    assert not any(c.raw == 10000 for c in scores[0].contributions)


def test_vigilance_preserves_value_of_blocker_in_next_attack():
    enemies = [creature("big", 4, 4, controller="p2", tapped=True)]
    ordinary = combat(creature(power=2, toughness=2), enemies, our_life=4)
    vigilant = combat(creature(power=2, toughness=2, keywords=["vigilance"]), enemies, our_life=4)
    _, normal_scores = choose(ordinary)
    _, vigilant_scores = choose(vigilant)
    assert vigilant_scores[0].total > normal_scores[0].total + 50


def test_empty_board_still_invites_attacks():
    assert choose(combat())[0].action_id == "attack"


def test_board_clock_does_not_inherit_guide_of_souls_imaginary_flying():
    choice = combat(guide_of_souls(), [creature("big", 4, 4, controller="p2")])
    assessment = assess_board(choice.game, StrategyPolicy.from_mapping(None))
    assert assessment.our_clock.turns == 99
