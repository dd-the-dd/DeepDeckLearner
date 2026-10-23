from __future__ import annotations

import pytest
from deepdeck_agent import Decision, Game

from deepdeck_examples.forge_reference import ForgeReferenceAgent
from deepdeck_examples.strategic_graph import StrategyPolicy, WeightedDecisionGraph, assess_board


def _card(instance_id, name, controller, text, mana="{U}"):
    return {
        "instanceId": instance_id,
        "owner": controller,
        "controller": controller,
        "definition": {
            "id": instance_id,
            "name": name,
            "typeLine": "Instant" if name != "Show and Tell" else "Sorcery",
            "manaCost": mana,
            "oracleText": text,
            "rules": [{"text": text}],
        },
    }


def _stack(stack_id, controller, card, *, targets=None, **extra):
    return {
        "id": stack_id,
        "controller": controller,
        "card": card,
        "targets": targets or {},
        **extra,
    }


def _target(stack_id):
    # Match the actual Engine enum serialization, not only SDK convenience data.
    return {"target": {"stackObject": {"stackId": stack_id}}}


def _decision(stack, *, source=None, target_ids=None, request_id="priority-test"):
    source = source or _card("force", "Force of Will", "p1", "Counter target spell.", "{3}{U}{U}")
    players = [
        {
            "id": player,
            "life": 20,
            "hand": [source] if player == "p1" else [],
            "battlefield": [],
            "library": [],
            "graveyard": [],
            "exile": [],
        }
        for player in ("p1", "p2", "p3")
    ]
    view = Game(
        {
            "turnNumber": 4,
            "activePlayer": 0,
            "step": "postcombatMain",
            "players": players,
            "stack": stack,
        },
        "p1",
    )
    options = [
        {
            "id": f"cast:{stack_id}",
            "kind": "castSpell",
            "cardInstanceId": source["instanceId"],
            "targets": _target(stack_id),
        }
        for stack_id in (target_ids or [item["id"] for item in stack])
    ]
    options.append({"id": "pass", "kind": "passPriority"})
    return Decision(request_id, "p1", {"kind": "priority", "options": options}, view)


def _show():
    return _card(
        "show",
        "Show and Tell",
        "p1",
        "Each player may put an artifact, creature, enchantment, or land card from their hand onto the battlefield.",
        "{2}{U}",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("archetype", ["aggro", "tempo", "midrange", "control", "combo"])
@pytest.mark.parametrize("temperature", [0, 0.05, 1])
async def test_does_not_force_of_will_its_own_show_and_tell_even_with_noise_and_preference(
    archetype, temperature
):
    agent = ForgeReferenceAgent(
        prefer_cards=["Force of Will"],
        strategy_graph={
            "archetypeMix": {archetype: 1},
            "deckAdaptation": 0,
            "temperature": temperature,
        },
    )
    for seed in range(5):
        choice = _decision([_stack("our-show", "p1", _show())], request_id=f"priority-{seed}")
        assert (await agent.make_decision(choice)).action_id == "pass"


@pytest.mark.asyncio
async def test_counters_the_opposing_counter_instead_of_our_combo_spell():
    opponent_counter = _card(
        "enemy-counter", "Counterspell", "p2", "Counter target spell.", "{U}{U}"
    )
    choice = _decision(
        [
            _stack("our-show", "p1", _show()),
            _stack("enemy-counter", "p2", opponent_counter, targets=_target("our-show")),
        ]
    )
    agent = ForgeReferenceAgent(strategy_graph={"archetypeMix": {"combo": 1}})
    assert (await agent.make_decision(choice)).action_id == "cast:enemy-counter"


@pytest.mark.asyncio
async def test_stack_controller_not_card_owner_determines_the_side():
    # A stolen/copy-controlled object may have an unrelated printed-card owner.
    choice = _decision([_stack("stolen-show", "p2", _show())])
    assert (await ForgeReferenceAgent().make_decision(choice)).action_id == "cast:stolen-show"


@pytest.mark.asyncio
async def test_does_not_reward_countering_an_unknown_or_uncounterable_object():
    enemy_spell = _card("enemy", "Enemy Engine", "p2", "Draw three cards.", "{4}{U}")
    for choice in [
        _decision([_stack("enemy-stack", "p2", enemy_spell, cantBeCountered=True)]),
        _decision([_stack("our-show", "p1", _show())], target_ids=["not-visible"]),
        _decision([_stack("unknown", "", enemy_spell)]),
    ]:
        assert (await ForgeReferenceAgent().make_decision(choice)).action_id == "pass"


@pytest.mark.asyncio
async def test_friendly_copy_is_not_mistaken_for_a_counterspell():
    copy = _card(
        "copy",
        "Twincast",
        "p1",
        "Copy target instant or sorcery spell. You may choose new targets for the copy.",
        "{U}{U}",
    )
    choice = _decision([_stack("our-show", "p1", _show())], source=copy)
    assert (await ForgeReferenceAgent().make_decision(choice)).action_id == "cast:our-show"


def test_stack_needs_distinguish_our_spell_from_an_opposing_response():
    own = _decision([_stack("our-show", "p1", _show())])
    assert assess_board(own.game, StrategyPolicy()).needs["answer_stack"] == 0
    enemy = _decision([_stack("opposing-show", "p3", _show())])
    assert assess_board(enemy.game, StrategyPolicy()).needs["answer_stack"] == 1


def test_counter_score_explains_the_resource_loss_and_applies_card_tag_rules_to_stack_targets():
    policy = StrategyPolicy.from_mapping(
        {
            "patterns": [
                {
                    "id": "protect-show",
                    "cardNames": ["Show and Tell"],
                    "nodeAdjustments": {"resource_preservation": 0.8},
                }
            ]
        }
    )
    choice = _decision([_stack("our-show", "p1", _show())])
    score = WeightedDecisionGraph(policy).score_priority(choice, choice.actions[0])
    assert any(
        item.node == "resource_preservation" and item.raw < 0 and "our own" in item.reason
        for item in score.contributions
    )
    assert not any(item.node == "interaction" and item.raw > 0 for item in score.contributions)


@pytest.mark.asyncio
async def test_patient_combo_saves_its_last_counter_instead_of_countering_a_cantrip():
    ponder = _card(
        "ponder", "Ponder", "p2", "Look at the top three cards of your library. Draw a card."
    )
    choice = _decision([_stack("opposing-ponder", "p2", ponder)])
    agent = ForgeReferenceAgent(
        "cautious", strategy_graph={"archetypeMix": {"combo": 0.8, "control": 0.2}}
    )
    assert (await agent.make_decision(choice)).action_id == "pass"


@pytest.mark.asyncio
async def test_target_tag_can_mark_a_specific_opposing_card_as_must_answer():
    enabler = _card("cantrip", "Custom Combo Enabler", "p2", "Draw a card.")
    choice = _decision([_stack("enabler", "p2", enabler)])
    ordinary = ForgeReferenceAgent("cautious", strategy_graph={"archetypeMix": {"combo": 1}})
    customized = ForgeReferenceAgent(
        "cautious",
        strategy_graph={
            "archetypeMix": {"combo": 1},
            "patterns": [
                {
                    "id": "stop-their-engine",
                    "cardNames": ["Custom Combo Enabler"],
                    "addTags": ["must_answer"],
                }
            ],
        },
    )
    assert (await ordinary.make_decision(choice)).action_id == "pass"
    assert (await customized.make_decision(choice)).action_id == "cast:enabler"


def test_spare_counters_reduce_the_reservation_cost():
    ponder = _card("ponder", "Ponder", "p2", "Draw a card.")
    one = _decision([_stack("ponder", "p2", ponder)])
    many = _decision([_stack("ponder", "p2", ponder)])
    many.game.raw["players"][0]["hand"].append(
        _card("backup", "Counterspell", "p1", "Counter target spell.")
    )
    policy = StrategyPolicy.from_mapping({"archetypeMix": {"combo": 1}})

    def cost(choice):
        score = WeightedDecisionGraph(policy).score_priority(choice, choice.actions[0])
        return sum(
            item.score for item in score.contributions if "scarce countermagic" in item.reason
        )

    assert cost(one) < cost(many) < 0
