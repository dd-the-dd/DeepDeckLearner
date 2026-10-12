from __future__ import annotations

import json
from pathlib import Path

import pytest
from deepdeck_agent import Decision, Game

from deepdeck_examples import HeuristicAgent, HeuristicProfile, heuristic_config


def card(
    instance_id: str,
    name: str,
    type_line: str,
    *,
    controller: str = "p1",
    mana_cost: str = "",
    power: int = 0,
    toughness: int = 0,
    rules: list[object] | None = None,
) -> dict[str, object]:
    return {
        "instanceId": instance_id,
        "owner": controller,
        "controller": controller,
        "definition": {
            "id": name.casefold().replace(" ", "-"),
            "name": name,
            "typeLine": type_line,
            "manaCost": mana_cost,
            "power": str(power) if power else None,
            "toughness": str(toughness) if toughness else None,
            "rules": rules or [],
        },
        "tapped": False,
        "counters": {},
    }


def state(
    *,
    hand: list[dict[str, object]] | None = None,
    battlefield: list[dict[str, object]] | None = None,
    sideboard: list[dict[str, object]] | None = None,
    opponent_battlefield: list[dict[str, object]] | None = None,
    opponent_graveyard: list[dict[str, object]] | None = None,
    active_player: int = 0,
    step: str = "precombatMain",
    stack: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "turnNumber": 3,
        "activePlayer": active_player,
        "step": step,
        "players": [
            {
                "id": "p1",
                "name": "Heuristic pilot",
                "life": 20,
                "hand": hand or [],
                "battlefield": battlefield or [],
                "commandZone": [],
                "library": [],
                "graveyard": [],
                "exile": [],
                "sideboard": sideboard or [],
                "manaPool": [],
            },
            {
                "id": "p2",
                "name": "Opponent",
                "life": 8,
                "hand": [],
                "battlefield": opponent_battlefield or [],
                "commandZone": [],
                "library": [],
                "graveyard": opponent_graveyard or [],
                "exile": [],
                "sideboard": [],
                "manaPool": [],
            },
        ],
        "stack": stack or [],
        "combat": {"attackers": [], "blockers": []},
        "events": [],
    }


def option(
    action_id: str,
    kind: str,
    *,
    source: str | None = None,
    payments: list[str] | None = None,
    targets: dict[str, object] | None = None,
    attacker: str | None = None,
    blocker: str | None = None,
    decisions: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "id": action_id,
        "kind": kind,
        "playerId": "p1",
        "label": action_id,
        "cardInstanceId": source,
        "paymentSources": payments or [],
        "targets": targets or {},
        "attackerId": attacker,
        "blockerId": blocker,
        "decisions": decisions or {},
    }


def decision(
    kind: str,
    options: list[dict[str, object]],
    current_state: dict[str, object],
    *,
    choice: dict[str, object] | None = None,
) -> Decision:
    raw: dict[str, object] = {"kind": kind, "options": options}
    if choice is not None:
        raw["choice"] = choice
    return Decision("decision", "p1", raw, Game(current_state, "p1"))


def permanent_target(instance_id: str) -> dict[str, object]:
    return {"target": {"kind": "permanent", "instanceId": instance_id}}


def player_target(player_id: str) -> dict[str, object]:
    return {"target": {"kind": "player", "playerId": player_id}}


def test_profile_is_bounded_and_loads_mcp_envelope(tmp_path: Path) -> None:
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps({"profile": {"aggression": 7}}), encoding="utf-8")
    assert HeuristicProfile.from_json_file(profile_path).aggression == 7
    with pytest.raises(ValueError, match="unknown"):
        HeuristicProfile.from_mapping({"invented": 1})
    with pytest.raises(ValueError, match="between"):
        HeuristicProfile.from_mapping({"reserve_mana": 11})


def test_heuristic_manifest_formats_follow_the_blueprint_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEEPDECK_AGENT_FORMATS", "commander")
    assert heuristic_config().formats == ("commander",)
    monkeypatch.setenv("DEEPDECK_AGENT_FORMATS", "vintage")
    with pytest.raises(ValueError, match="legacy and commander"):
        heuristic_config()


@pytest.mark.asyncio
async def test_opening_hand_prefers_a_needed_land() -> None:
    spell = card("spell", "Bear", "Creature", mana_cost="{1}{G}", power=2, toughness=2)
    forest = card("forest", "Forest", "Basic Land - Forest")
    expensive = card("dragon", "Dragon", "Creature", mana_cost="{6}{R}", power=6, toughness=6)
    current_state = state(hand=[spell])
    pick_forest = option(
        "forest-pick",
        "selectOpeningHandCard",
        decisions={"openingHandCandidate": forest},
    )
    pick_dragon = option(
        "dragon-pick",
        "selectOpeningHandCard",
        decisions={"openingHandCandidate": expensive},
    )
    result = await HeuristicAgent().make_decision(
        decision("openingHandSelection", [pick_dragon, pick_forest], current_state)
    )
    assert result.action_id == "forest-pick"


@pytest.mark.asyncio
async def test_mulligan_values_playability_colors_and_short_hand_pressure() -> None:
    bad_hand = [
        card(f"spell-{index}", f"Spell {index}", "Sorcery", mana_cost="{4}{U}")
        for index in range(7)
    ]
    choices = [option("keep", "keepHand"), option("mulligan", "takeMulligan")]
    agent = HeuristicAgent()
    bad = await agent.make_decision(decision("mulligan", choices, state(hand=bad_hand)))
    assert bad.action_id == "mulligan"

    smaller_balanced = [
        card("island-a", "Island", "Basic Land - Island"),
        card("island-b", "Island", "Basic Land - Island"),
        card("island-c", "Island", "Basic Land - Island"),
        card("cantrip", "Cantrip", "Sorcery", mana_cost="{U}"),
        card("bear", "Blue Bear", "Creature", mana_cost="{1}{U}", power=2, toughness=2),
    ]
    good = await agent.make_decision(
        decision("mulligan", choices, state(hand=smaller_balanced))
    )
    assert good.action_id == "keep"
    assert agent.trace_mapping() is not None

    wrong_colors = [
        card(f"forest-{index}", "Forest", "Basic Land - Forest") for index in range(3)
    ] + [
        card(f"blue-{index}", f"Blue Spell {index}", "Sorcery", mana_cost="{U}")
        for index in range(4)
    ]
    mismatched = await agent.make_decision(
        decision("mulligan", choices, state(hand=wrong_colors))
    )
    assert mismatched.action_id == "mulligan"


@pytest.mark.asyncio
async def test_mulligan_bottom_and_discard_preserve_needed_lands() -> None:
    lands = [card(f"land-{index}", "Forest", "Basic Land - Forest") for index in range(2)]
    expensive = card("expensive", "Colossus", "Artifact Creature", mana_cost="{9}", power=9)
    cards = [*lands, expensive]
    options = [
        option(str(item["instanceId"]), "bottomCard", source=str(item["instanceId"]))
        for item in cards
    ]
    agent = HeuristicAgent()
    bottom = await agent.make_decision(decision("mulliganBottom", options, state(hand=cards)))
    assert bottom.action_id == "expensive"

    discard_options = [
        option(str(item["instanceId"]), "discard", source=str(item["instanceId"]))
        for item in cards
    ]
    discard = await agent.make_decision(
        decision("discard", discard_options, state(hand=cards))
    )
    assert discard.action_id == "expensive"


@pytest.mark.asyncio
async def test_priority_plays_land_and_removal_targets_largest_threat() -> None:
    land = card("land", "Mountain", "Basic Land - Mountain")
    removal = card(
        "bolt",
        "Removal",
        "Instant",
        mana_cost="{R}",
        rules=[{"effects": [{"kind": "destroyPermanent"}]}],
    )
    small = card("small", "Small", "Creature", controller="p2", power=1, toughness=1)
    large = card("large", "Large", "Creature", controller="p2", power=6, toughness=6)
    agent = HeuristicAgent()
    land_result = await agent.make_decision(
        decision(
            "priority",
            [option("pass", "passPriority"), option("play", "playLand", source="land")],
            state(hand=[land]),
        )
    )
    assert land_result.action_id == "play"

    remove_result = await agent.make_decision(
        decision(
            "priority",
            [
                option("small-hit", "castSpell", source="bolt", targets=permanent_target("small")),
                option("large-hit", "castSpell", source="bolt", targets=permanent_target("large")),
                option("pass", "passPriority"),
            ],
            state(hand=[removal], opponent_battlefield=[small, large]),
        )
    )
    assert remove_result.action_id == "large-hit"


@pytest.mark.asyncio
async def test_priority_can_hold_an_unnecessary_response() -> None:
    trick = card("trick", "Vanilla Trick", "Instant", mana_cost="{4}{U}")
    result = await HeuristicAgent().make_decision(
        decision(
            "priority",
            [option("cast", "castSpell", source="trick"), option("pass", "passPriority")],
            state(hand=[trick], active_player=1, step="upkeep"),
        )
    )
    assert result.action_id == "pass"


@pytest.mark.asyncio
async def test_combat_attacks_for_lethal_and_chooses_a_favorable_block() -> None:
    attacker = card("attacker", "Attacker", "Creature", power=8, toughness=8)
    enemy = card("enemy", "Enemy", "Creature", controller="p2", power=4, toughness=3)
    blocker = card("blocker", "Blocker", "Creature", power=3, toughness=5)
    agent = HeuristicAgent()
    attack = await agent.make_decision(
        decision(
            "attackers",
            [
                option("finish", "finishAttackers"),
                option(
                    "attack",
                    "declareAttacker",
                    attacker="attacker",
                    targets=player_target("p2"),
                ),
            ],
            state(battlefield=[attacker]),
        )
    )
    assert attack.action_id == "attack"

    block = await agent.make_decision(
        decision(
            "blockers",
            [
                option("finish", "finishBlockers"),
                option("block", "declareBlocker", attacker="enemy", blocker="blocker"),
            ],
            state(battlefield=[blocker], opponent_battlefield=[enemy]),
        )
    )
    assert block.action_id == "block"


@pytest.mark.asyncio
async def test_combat_damage_and_commander_replacement_are_scored() -> None:
    blocker = card("blocker", "Blocker", "Creature", controller="p2", toughness=3)
    damage = await HeuristicAgent().make_decision(
        decision(
            "combatDamage",
            [
                option(
                    "one",
                    "assignCombatDamage",
                    blocker="blocker",
                    decisions={"combatDamageAmount": 1},
                ),
                option(
                    "three",
                    "assignCombatDamage",
                    blocker="blocker",
                    decisions={"combatDamageAmount": 3},
                ),
            ],
            state(opponent_battlefield=[blocker]),
        )
    )
    assert damage.action_id == "three"

    replacement = await HeuristicAgent().make_decision(
        decision(
            "replacementChoice",
            [
                option("graveyard", "declineReplacement"),
                option("command", "moveCommanderToCommandZone"),
            ],
            state(),
        )
    )
    assert replacement.action_id == "command"


@pytest.mark.asyncio
async def test_resolution_choices_respect_bounds_and_card_value() -> None:
    number = await HeuristicAgent().make_decision(
        decision(
            "resolutionChoice",
            [option("resolve", "submitChoice")],
            state(),
            choice={"kind": "numberSelection", "minimum": 1, "maximum": 4},
        )
    )
    assert number.number_value == 4

    land = card("land", "Forest", "Basic Land - Forest")
    colossus = card("colossus", "Colossus", "Artifact Creature", mana_cost="{9}", power=9)
    selected = await HeuristicAgent().make_decision(
        decision(
            "resolutionChoice",
            [option("resolve", "submitChoice")],
            state(hand=[land, colossus]),
            choice={
                "kind": "cardSelection",
                "prompt": "Choose a card to discard",
                "candidateCardInstanceIds": ["land", "colossus"],
                "minimum": 1,
                "maximum": 1,
            },
        )
    )
    assert selected.card_instance_ids == ["colossus"]


@pytest.mark.asyncio
async def test_sideboard_uses_observed_matchup_and_preserves_deck_size() -> None:
    main = card("p1:main-card:0", "Main Card", "Sorcery", mana_cost="{2}")
    hate = card(
        "p1:hate-card:0",
        "Hate Card",
        "Instant",
        mana_cost="{1}",
        rules=[{"effects": [{"kind": "destroyPermanent"}]}],
    )
    enemies = [
        card(f"enemy-{index}", f"Enemy {index}", "Creature", controller="p2", power=3)
        for index in range(6)
    ]
    agent = HeuristicAgent(HeuristicProfile(sideboard_change_penalty=0))
    await agent.on_game_start(Game(state(opponent_battlefield=enemies), "p1"), [main, hate])
    result = await agent.make_decision(
        decision(
            "sideboarding",
            [
                option(
                    "submit",
                    "submitSideboard",
                    decisions={"initialMainDeckIds": ["p1:main-card:0"]},
                )
            ],
            state(sideboard=[hate], hand=[main], opponent_battlefield=enemies),
            choice={
                "kind": "cardSelection",
                "candidateCardInstanceIds": ["p1:main-card:0", "p1:hate-card:0"],
                "minimum": 1,
                "maximum": 1,
            },
        )
    )
    assert result.card_instance_ids == ["p1:hate-card:0"]


@pytest.mark.asyncio
async def test_equal_scores_use_stable_action_id_tie_breaking() -> None:
    result = await HeuristicAgent().make_decision(
        decision(
            "priority",
            [option("z-pass", "passPriority"), option("a-pass", "passPriority")],
            state(),
        )
    )
    assert result.action_id == "a-pass"
