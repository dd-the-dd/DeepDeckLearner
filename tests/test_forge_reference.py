from __future__ import annotations

import pytest
from deepdeck_agent import Decision, Game

from deepdeck_examples import ForgeReferenceAgent, ForgeReferenceProfile
from deepdeck_examples.strategic_graph import (
    ActionScore,
    StrategyPolicy,
    WeightedDecisionGraph,
    analyze_deck,
    assess_board,
    opening_hand_score,
)


def card(
    instance_id: str,
    name: str,
    type_line: str,
    *,
    controller: str = "p1",
    mana_cost: str = "",
    power: int = 0,
    toughness: int = 0,
    rules: list | None = None,
    tapped: bool = False,
) -> dict:
    return {
        "instanceId": instance_id,
        "owner": controller,
        "controller": controller,
        "definition": {
            "id": name.casefold().replace(" ", "-"),
            "name": name,
            "typeLine": type_line,
            "manaCost": mana_cost,
            "power": str(power),
            "toughness": str(toughness),
            "rules": rules or [],
        },
        "tapped": tapped,
        "counters": {},
    }


def game(
    *,
    hand: list[dict] | None = None,
    battlefield: list[dict] | None = None,
    opponent_battlefield: list[dict] | None = None,
    life: int = 20,
    opponent_life: int = 20,
    active_player: int = 0,
    step: str = "precombatMain",
    stack: list[dict] | None = None,
    perspective: str = "p1",
) -> Game:
    return Game(
        {
            "turnNumber": 4,
            "activePlayer": active_player,
            "step": step,
            "players": [
                {
                    "id": "p1",
                    "life": life,
                    "hand": hand or [],
                    "battlefield": battlefield or [],
                    "library": [],
                    "graveyard": [],
                    "exile": [],
                    "sideboard": [],
                    "commandZone": [],
                    "manaPool": [],
                },
                {
                    "id": "p2",
                    "life": opponent_life,
                    "hand": [],
                    "battlefield": opponent_battlefield or [],
                    "library": [],
                    "graveyard": [],
                    "exile": [],
                    "sideboard": [],
                    "commandZone": [],
                    "manaPool": [],
                },
            ],
            "stack": stack or [],
        },
        perspective,
    )


def decision(kind: str, options: list[dict], current_game: Game) -> Decision:
    return Decision("request", "p1", {"kind": kind, "options": options}, current_game)


@pytest.mark.asyncio
async def test_keeps_a_functional_opening_hand_and_mulligans_one_land() -> None:
    keep_hand = [card(f"land-{index}", "Forest", "Basic Land") for index in range(3)]
    keep_hand.append(card("spell", "Bear", "Creature", mana_cost="{1}{G}", power=2, toughness=2))
    options = [{"id": "keep", "kind": "keepHand"}, {"id": "mull", "kind": "takeMulligan"}]
    agent = ForgeReferenceAgent()

    kept = await agent.make_decision(decision("mulligan", options, game(hand=keep_hand)))
    mulliganed = await agent.make_decision(decision("mulligan", options, game(hand=keep_hand[2:])))

    assert kept.action_id == "keep"
    assert mulliganed.action_id == "mull"


@pytest.mark.asyncio
async def test_never_mulligans_below_four_cards() -> None:
    bad_hand = [
        card(f"spell-{index}", f"Expensive {index}", "Creature", mana_cost="{6}{G}")
        for index in range(7)
    ]
    options = [{"id": "keep:p1:3", "kind": "keepHand"}, {"id": "mull:p1:3", "kind": "takeMulligan"}]
    choice = Decision(
        "mulligan:p1:3",
        "p1",
        {"kind": "mulligan", "options": options},
        game(hand=bad_hand),
    )

    assert (await ForgeReferenceAgent().make_decision(choice)).action_id == "keep:p1:3"


@pytest.mark.asyncio
async def test_plays_a_land_before_casting_a_spell() -> None:
    land = card("land", "Forest", "Basic Land")
    bear = card("bear", "Bear", "Creature", mana_cost="{1}{G}", power=2, toughness=2)
    choice = decision(
        "priority",
        [
            {"id": "cast", "kind": "castSpell", "cardInstanceId": "bear"},
            {"id": "land", "kind": "playLand", "cardInstanceId": "land"},
            {"id": "pass", "kind": "passPriority"},
        ],
        game(hand=[land, bear]),
    )

    assert (await ForgeReferenceAgent().make_decision(choice)).action_id == "land"


@pytest.mark.asyncio
async def test_land_drop_supplies_the_color_needed_by_the_hand() -> None:
    island = card("island", "Island", "Basic Land — Island")
    wasteland = card(
        "wasteland",
        "Wasteland",
        "Land",
        rules=[{"text": "Sacrifice Wasteland: Destroy target nonbasic land."}],
    )
    blue_spell = card("blue", "Blue Spell", "Sorcery", mana_cost="{U}")
    choice = decision(
        "priority",
        [
            {"id": "play-island", "kind": "playLand", "cardInstanceId": "island"},
            {"id": "play-wasteland", "kind": "playLand", "cardInstanceId": "wasteland"},
        ],
        game(hand=[island, wasteland, blue_spell]),
    )

    assert (await ForgeReferenceAgent().make_decision(choice)).action_id == "play-island"


@pytest.mark.asyncio
async def test_removal_prefers_the_most_valuable_opposing_target() -> None:
    removal = card(
        "removal",
        "Clean Removal",
        "Instant",
        rules=[{"effects": [{"kind": "destroyPermanent"}]}],
    )
    small = card("small", "Small", "Creature", controller="p2", power=1, toughness=1)
    large = card("large", "Large", "Creature", controller="p2", power=6, toughness=6)
    choice = decision(
        "priority",
        [
            {
                "id": "small-target",
                "kind": "castSpell",
                "cardInstanceId": "removal",
                "targets": {"target": {"kind": "permanent", "instanceId": "small"}},
            },
            {
                "id": "large-target",
                "kind": "castSpell",
                "cardInstanceId": "removal",
                "targets": {"target": {"kind": "permanent", "instanceId": "large"}},
            },
            {"id": "pass", "kind": "passPriority"},
        ],
        game(hand=[removal], opponent_battlefield=[small, large]),
    )

    assert (await ForgeReferenceAgent().make_decision(choice)).action_id == "large-target"


@pytest.mark.asyncio
async def test_aggressive_profile_attacks_where_cautious_profile_stops() -> None:
    attacker = card("attacker", "Attacker", "Creature", power=3, toughness=2)
    blocker = card("blocker", "Blocker", "Creature", controller="p2", power=2, toughness=3)
    options = [
        {
            "id": "attack",
            "kind": "declareAttacker",
            "attackerId": "attacker",
            "targets": {"defender": {"kind": "player", "playerId": "p2"}},
        },
        {"id": "finish", "kind": "finishAttackers"},
    ]
    choice = decision(
        "attackers",
        options,
        game(battlefield=[attacker], opponent_battlefield=[blocker]),
    )

    cautious = ForgeReferenceAgent(ForgeReferenceProfile.CAUTIOUS)
    aggressive = ForgeReferenceAgent(ForgeReferenceProfile.AGGRESSIVE)

    assert (await cautious.make_decision(choice)).action_id == "finish"
    assert (await aggressive.make_decision(choice)).action_id == "attack"


@pytest.mark.asyncio
async def test_blocks_lethal_damage_even_with_an_unfavorable_trade() -> None:
    blocker = card("blocker", "Blocker", "Creature", power=1, toughness=1)
    attacker = card("attacker", "Attacker", "Creature", controller="p2", power=5, toughness=5)
    choice = decision(
        "blockers",
        [
            {
                "id": "block",
                "kind": "declareBlocker",
                "attackerId": "attacker",
                "blockerId": "blocker",
            },
            {"id": "finish", "kind": "finishBlockers"},
        ],
        game(battlefield=[blocker], opponent_battlefield=[attacker], life=5),
    )

    assert (await ForgeReferenceAgent().make_decision(choice)).action_id == "block"


@pytest.mark.asyncio
async def test_discards_excess_land_before_a_spell() -> None:
    lands = [card(f"land-{index}", "Mountain", "Basic Land") for index in range(5)]
    spell = card("spell", "Dragon", "Creature", mana_cost="{4}{R}", power=5, toughness=5)
    choice = decision(
        "discard",
        [
            {"id": "discard-land", "kind": "discard", "cardInstanceId": "land-0"},
            {"id": "discard-spell", "kind": "discard", "cardInstanceId": "spell"},
        ],
        game(hand=[*lands, spell]),
    )

    assert (await ForgeReferenceAgent().make_decision(choice)).action_id == "discard-land"


@pytest.mark.asyncio
async def test_brainstorm_style_resolution_puts_low_value_cards_back() -> None:
    lands = [card(f"land-{index}", "Island", "Basic Land — Island") for index in range(5)]
    threat = card("threat", "Efficient Threat", "Creature", mana_cost="{U}", power=3, toughness=2)
    current_game = game(hand=[*lands, threat])
    choice = Decision(
        "resolution:cardsForLibrary",
        "p1",
        {
            "kind": "resolutionChoice",
            "choice": {
                "kind": "cardSelection",
                "candidateCardInstanceIds": ["land-0", "land-1", "threat"],
                "minimum": 2,
                "maximum": 2,
            },
            "options": [{"id": "resolve", "kind": "resolveChoice"}],
        },
        current_game,
    )

    result = await ForgeReferenceAgent().make_decision(choice)

    assert result.card_instance_ids == ["land-0", "land-1"]


@pytest.mark.asyncio
async def test_holds_an_instant_until_the_opponents_end_step() -> None:
    instant = card("instant", "Flexible Instant", "Instant", mana_cost="{U}")
    options = [
        {"id": "cast", "kind": "castSpell", "cardInstanceId": "instant"},
        {"id": "pass", "kind": "passPriority"},
    ]
    own_main = decision(
        "priority",
        options,
        game(hand=[instant], active_player=0, step="precombatMain"),
    )
    opponent_end = decision(
        "priority",
        options,
        game(hand=[instant], active_player=1, step="endStep"),
    )
    agent = ForgeReferenceAgent(ForgeReferenceProfile.CAUTIOUS)

    assert (await agent.make_decision(own_main)).action_id == "pass"
    assert (await agent.make_decision(opponent_end)).action_id == "cast"


@pytest.mark.asyncio
async def test_card_pattern_prioritizes_a_known_combo_piece() -> None:
    removal = card(
        "removal",
        "Clean Removal",
        "Instant",
        rules=[{"effects": [{"kind": "destroyPermanent"}]}],
    )
    engine = card("engine", "Loop Engine", "Artifact", controller="p2", mana_cost="{2}")
    decoy = card("decoy", "Fair Relic", "Artifact", controller="p2", mana_cost="{2}")
    choice = decision(
        "priority",
        [
            {
                "id": "engine-target",
                "kind": "castSpell",
                "cardInstanceId": "removal",
                "targets": {"target": {"kind": "permanent", "instanceId": "engine"}},
            },
            {
                "id": "decoy-target",
                "kind": "castSpell",
                "cardInstanceId": "removal",
                "targets": {"target": {"kind": "permanent", "instanceId": "decoy"}},
            },
        ],
        game(hand=[removal], opponent_battlefield=[engine, decoy]),
    )
    agent = ForgeReferenceAgent(
        strategy_graph={
            "patterns": [
                {
                    "id": "loop-engine-is-a-combo-piece",
                    "cardNames": ["Loop Engine"],
                    "addTags": ["combo_payoff"],
                    "nodeAdjustments": {"combo_disruption": 1.0},
                }
            ]
        }
    )

    assert (await agent.make_decision(choice)).action_id == "engine-target"


def test_deck_analysis_produces_soft_archetype_blends() -> None:
    tempo_deck = [
        {
            "name": "Island",
            "typeLine": "Basic Land",
            "quantity": 20,
            "section": "main",
        },
        {
            "name": "Quick Threat",
            "typeLine": "Creature",
            "manaCost": "{U}",
            "quantity": 12,
            "section": "main",
        },
        {
            "name": "Permission",
            "typeLine": "Instant",
            "manaCost": "{U}",
            "oracleText": "Counter target spell.",
            "quantity": 12,
            "section": "main",
        },
        {
            "name": "Selection",
            "typeLine": "Instant",
            "manaCost": "{U}",
            "oracleText": "Scry 2, then draw a card.",
            "quantity": 16,
            "section": "main",
        },
    ]

    analysis = analyze_deck(tempo_deck)

    assert analysis.primary_archetype == "tempo"
    assert 0.99 <= sum(analysis.archetype_mix.values()) <= 1.01
    assert analysis.archetype_mix["tempo"] > analysis.archetype_mix["aggro"]


def test_user_weights_multiply_the_archetype_baseline() -> None:
    baseline = StrategyPolicy.from_mapping({"archetypeMix": {"aggro": 1.0}}, profile="balanced")
    tuned = StrategyPolicy.from_mapping(
        {
            "archetypeMix": {"aggro": 1.0},
            "weights": {"combat_pressure": 0.5},
        },
        profile="balanced",
    )

    assert tuned.weight("combat_pressure") == pytest.approx(
        baseline.weight("combat_pressure") * 0.5
    )


def test_role_assignment_turns_control_into_beatdown_when_it_has_the_clock() -> None:
    control = StrategyPolicy.from_mapping({"archetypeMix": {"control": 1.0}})
    empty = assess_board(game(), control)
    finisher = card("finisher", "Finisher", "Creature", power=5, toughness=5)
    ahead = assess_board(game(battlefield=[finisher]), control)

    assert empty.role == "control"
    assert ahead.role == "beatdown"


def test_board_assessment_recognizes_a_noncombat_mill_clock() -> None:
    mill_engine = card(
        "mill-engine",
        "Mill Engine",
        "Artifact",
        rules=[{"text": "At the beginning of your upkeep, target opponent mills four cards."}],
    )
    assessment = assess_board(game(battlefield=[mill_engine]), StrategyPolicy.from_mapping(None))

    assert assessment.our_clock.kind == "mill"
    assert assessment.our_clock.turns < 99
    assert assessment.our_clock.sources == ("Mill Engine",)


def test_opening_hand_scores_plan_and_early_action_not_only_land_count() -> None:
    policy = StrategyPolicy.from_mapping({"archetypeMix": {"aggro": 1.0}})
    functional = game(
        hand=[
            card("land-a", "Mountain", "Basic Land"),
            card("land-b", "Mountain", "Basic Land"),
            card("one-a", "One Drop A", "Creature", mana_cost="{R}", power=2, toughness=1),
            card("one-b", "One Drop B", "Creature", mana_cost="{R}", power=2, toughness=1),
        ]
    ).me.hand
    stranded = game(
        hand=[
            card("land-c", "Mountain", "Basic Land"),
            card("land-d", "Mountain", "Basic Land"),
            card("six-a", "Six Drop A", "Creature", mana_cost="{5}{R}", power=6, toughness=6),
            card("six-b", "Six Drop B", "Creature", mana_cost="{5}{R}", power=6, toughness=6),
        ]
    ).me.hand

    functional_score = opening_hand_score(functional, policy)[0]
    stranded_score = opening_hand_score(stranded, policy)[0]

    assert functional_score > stranded_score + 80


def test_opening_hand_checks_early_color_access() -> None:
    policy = StrategyPolicy.from_mapping({"archetypeMix": {"tempo": 1.0}})
    spells = [
        card("threat-blue", "Blue Threat", "Creature", mana_cost="{U}", power=2, toughness=1),
        card(
            "answer-blue",
            "Blue Answer",
            "Instant",
            mana_cost="{U}",
            rules=[{"text": "Counter target spell."}],
        ),
    ]
    blue_hand = game(
        hand=[
            card("island-a", "Island", "Basic Land — Island"),
            card("island-b", "Island", "Basic Land — Island"),
            *spells,
        ]
    ).me.hand
    wrong_color_hand = game(
        hand=[
            card("mountain-a", "Mountain", "Basic Land — Mountain"),
            card("mountain-b", "Mountain", "Basic Land — Mountain"),
            *spells,
        ]
    ).me.hand

    blue_score = opening_hand_score(blue_hand, policy)[0]
    wrong_color_score = opening_hand_score(wrong_color_hand, policy)[0]

    assert blue_score > wrong_color_score + 60


def test_decision_noise_is_reproducible_and_bounded_to_plausible_actions() -> None:
    policy = StrategyPolicy.from_mapping({"temperature": 1.0, "seed": 90210})
    graph = WeightedDecisionGraph(policy)
    choice = decision(
        "priority",
        [
            {"id": "best", "kind": "passPriority"},
            {"id": "close", "kind": "passPriority"},
            {"id": "tactical-blunder", "kind": "passPriority"},
        ],
        game(),
    )
    scores = [
        ActionScore("best", 100.0, ()),
        ActionScore("close", 82.0, ()),
        ActionScore("tactical-blunder", -1_000.0, ()),
    ]

    first = graph.choose(choice, scores)
    second = WeightedDecisionGraph(policy).choose(choice, scores)

    assert first.action_id == second.action_id
    assert first.action_id in {"best", "close"}


@pytest.mark.asyncio
@pytest.mark.parametrize("temperature", [0, 1])
async def test_equipment_does_not_repeat_equip_on_its_current_bearer(temperature) -> None:
    equipment = card("equipment", "Lightning Greaves", "Artifact — Equipment")
    equipment["attachedTo"] = "bearer"
    bearer = card("bearer", "Commander", "Legendary Creature", power=4, toughness=4)
    choice = decision(
        "priority",
        [
            {
                "id": "equip",
                "kind": "activateAbility",
                "cardInstanceId": "equipment",
                "decisions": {"equip": True},
                "targets": {"equipTarget": {"permanent": {"instanceId": "bearer"}}},
            },
            {"id": "pass", "kind": "passPriority"},
        ],
        game(battlefield=[equipment, bearer], step="postcombatMain"),
    )
    policy = StrategyPolicy.from_mapping({"temperature": temperature})
    graph = WeightedDecisionGraph(policy)
    scores = [graph.score_priority(choice, action) for action in choice.actions]
    assert graph.choose(choice, scores).action_id == "pass"
    assert "redundant_equip" in scores[0].tags
    chosen = await ForgeReferenceAgent("balanced").choose_priority(choice)
    assert chosen.id == "pass"


def test_equipment_can_still_equip_a_new_bearer() -> None:
    equipment = card("equipment", "Lightning Greaves", "Artifact — Equipment")
    equipment["attachedTo"] = "old-bearer"
    bearer = card("bearer", "Commander", "Legendary Creature", power=4, toughness=4)
    choice = decision(
        "priority",
        [
            {
                "id": "equip",
                "kind": "activateAbility",
                "cardInstanceId": "equipment",
                "decisions": {"equip": True},
                "targets": {"equipTarget": {"permanent": {"instanceId": "bearer"}}},
            },
        ],
        game(battlefield=[equipment, bearer]),
    )
    score = WeightedDecisionGraph(StrategyPolicy.from_mapping({})).score_priority(
        choice,
        choice.actions[0],
    )
    assert "redundant_equip" not in score.tags
    assert score.total > -1_000


@pytest.mark.asyncio
async def test_control_preserves_removal_for_a_relevant_threat() -> None:
    removal = card(
        "removal",
        "Clean Removal",
        "Instant",
        rules=[{"effects": [{"kind": "destroyPermanent"}]}],
    )
    harmless = card("harmless", "Harmless", "Creature", controller="p2", power=1, toughness=1)
    threat = card("threat", "Threat", "Creature", controller="p2", power=7, toughness=7)

    def removal_decision(target: dict) -> Decision:
        return decision(
            "priority",
            [
                {
                    "id": "remove",
                    "kind": "castSpell",
                    "cardInstanceId": "removal",
                    "targets": {
                        "target": {"kind": "permanent", "instanceId": target["instanceId"]}
                    },
                },
                {"id": "pass", "kind": "passPriority"},
            ],
            game(
                hand=[removal],
                opponent_battlefield=[target],
                life=7,
                active_player=1,
                step="endStep",
            ),
        )

    agent = ForgeReferenceAgent(ForgeReferenceProfile.CAUTIOUS)

    assert (await agent.make_decision(removal_decision(harmless))).action_id == "pass"
    assert (await agent.make_decision(removal_decision(threat))).action_id == "remove"


@pytest.mark.asyncio
async def test_control_deploys_a_blocker_before_drawing_under_a_lethal_clock() -> None:
    blocker = card(
        "blocker",
        "Timely Blocker",
        "Creature",
        mana_cost="{1}{U}",
        power=2,
        toughness=3,
    )
    draw_spell = card(
        "draw",
        "Slow Insight",
        "Sorcery",
        mana_cost="{1}{U}",
        rules=[{"text": "Draw two cards."}],
    )
    threat = card("threat", "Lethal Threat", "Creature", controller="p2", power=5, toughness=5)
    choice = decision(
        "priority",
        [
            {"id": "cast-blocker", "kind": "castSpell", "cardInstanceId": "blocker"},
            {"id": "cast-draw", "kind": "castSpell", "cardInstanceId": "draw"},
            {"id": "pass", "kind": "passPriority"},
        ],
        game(
            hand=[blocker, draw_spell],
            opponent_battlefield=[threat],
            life=5,
            active_player=0,
            step="postcombatMain",
        ),
    )

    assert (
        await ForgeReferenceAgent(ForgeReferenceProfile.CAUTIOUS).make_decision(choice)
    ).action_id == "cast-blocker"


@pytest.mark.asyncio
async def test_payment_permutations_are_collapsed_before_strategic_scoring() -> None:
    spell = card("spell", "Test Spell", "Sorcery", mana_cost="{1}{U}")
    options = [
        {
            "id": f"cast-{index}",
            "kind": "castSpell",
            "cardInstanceId": "spell",
            "paymentSources": [f"source-a-{index}", f"source-b-{index}"],
        }
        for index in range(1_000)
    ]
    options.append({"id": "pass", "kind": "passPriority"})
    choice = decision("priority", options, game(hand=[spell]))
    agent = ForgeReferenceAgent()

    selected = await agent.make_decision(choice)

    assert len(agent._strategic_actions(choice)) == 2  # noqa: SLF001
    assert selected.action_id in {option["id"] for option in options}


@pytest.mark.asyncio
async def test_deck_adaptation_is_isolated_between_concurrent_players() -> None:
    aggro_deck = [
        {
            "name": "Quick Threat",
            "typeLine": "Creature",
            "manaCost": "{R}",
            "quantity": 40,
        },
        {"name": "Mountain", "typeLine": "Basic Land — Mountain", "quantity": 20},
    ]
    combo_deck = [
        {
            "name": "Combo Engine",
            "typeLine": "Enchantment",
            "manaCost": "{2}{U}",
            "oracleText": "You may cast spells without paying their mana cost.",
            "quantity": 20,
        },
        {
            "name": "Combo Payoff",
            "typeLine": "Creature",
            "manaCost": "{7}{U}",
            "quantity": 20,
        },
        {"name": "Island", "typeLine": "Basic Land — Island", "quantity": 20},
    ]
    agent = ForgeReferenceAgent(strategy_graph={"deckAdaptation": 1.0})

    await agent.on_game_start(game(perspective="p1"), aggro_deck)
    first_mix = dict(agent._policies_by_player_id["p1"].archetype_mix)  # noqa: SLF001
    await agent.on_game_start(game(perspective="p2"), combo_deck)

    assert first_mix == agent._policies_by_player_id["p1"].archetype_mix  # noqa: SLF001
    assert agent._policies_by_player_id["p2"].archetype_mix["combo"] > 0.5  # noqa: SLF001
