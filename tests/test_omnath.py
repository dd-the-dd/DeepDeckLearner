from __future__ import annotations

import pytest
from deepdeck_agent import Decision, Game

from deepdeck_examples import OmnathAgent


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
            "power": str(power) if power else None,
            "toughness": str(toughness) if toughness else None,
            "rules": rules or [],
        },
        "tapped": False,
        "counters": {},
    }


def state(
    *,
    hand: list[dict] | None = None,
    battlefield: list[dict] | None = None,
    command_zone: list[dict] | None = None,
    opponent_battlefield: list[dict] | None = None,
    opponent_life: int = 20,
    opponent_library: list[dict] | None = None,
    step: str = "precombatMain",
    stack: list[dict] | None = None,
    my_turn: bool = True,
) -> dict:
    return {
        "turnNumber": 6,
        "activePlayer": 0 if my_turn else 1,
        "step": step,
        "players": [
            {
                "id": "p1",
                "name": "Omnath pilot",
                "life": 40,
                "hand": hand or [],
                "battlefield": battlefield or [],
                "commandZone": command_zone or [],
                "library": [],
                "graveyard": [],
                "exile": [],
                "sideboard": [],
                "manaPool": [],
            },
            {
                "id": "p2",
                "name": "Opponent",
                "life": opponent_life,
                "hand": [],
                "battlefield": opponent_battlefield or [],
                "commandZone": [],
                "library": opponent_library or [],
                "graveyard": [],
                "exile": [],
                "sideboard": [],
                "manaPool": [],
            },
        ],
        "stack": stack or [],
        "combat": {"attackers": [], "blockers": []},
        "events": [],
    }


def choice(kind: str, options: list[dict], current_state: dict) -> Decision:
    return Decision("decision", "p1", {"kind": kind, "options": options}, Game(current_state, "p1"))


def option(
    action_id: str,
    kind: str,
    *,
    source: str | None = None,
    x_value: int | None = None,
    payments: list[str] | None = None,
    targets: dict | None = None,
) -> dict:
    decisions = {} if x_value is None else {"xValue": x_value}
    return {
        "id": action_id,
        "kind": kind,
        "playerId": "p1",
        "label": action_id,
        "cardInstanceId": source,
        "paymentSources": payments or [],
        "targets": targets or {},
        "decisions": decisions,
    }


def player_target(player_id: str) -> dict:
    return {"target": {"kind": "player", "playerId": player_id}}


def permanent_target(instance_id: str) -> dict:
    return {"target": {"kind": "permanent", "instanceId": instance_id}}


def stack_target(stack_id: str) -> dict:
    return {"target": {"kind": "stackObject", "stackId": stack_id}}


def omnath_card(zone_id: str = "omnath") -> dict:
    return card(
        zone_id,
        "Omnath, Locus of All",
        "Legendary Creature - Phyrexian Elemental",
        mana_cost="{W}{U}{B/P}{R}{G}",
        power=4,
        toughness=4,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("with_ramp, expected", [(False, "mulligan"), (True, "keep")])
async def test_two_land_hand_requires_cheap_ramp(with_ramp: bool, expected: str) -> None:
    hand = [
        card("land-0", "Breeding Pool", "Land - Forest Island"),
        card("land-1", "Temple Garden", "Land - Forest Plains"),
    ]
    if with_ramp:
        hand.append(
            card(
                "signet",
                "Arcane Signet",
                "Artifact",
                mana_cost="{2}",
                rules=[{"effect": {"kind": "addMana"}}],
            )
        )
    decision = choice(
        "mulligan",
        [option("keep", "keepHand"), option("mulligan", "takeMulligan")],
        state(hand=hand),
    )
    assert (await OmnathAgent().make_decision(decision)).action_id == expected


@pytest.mark.asyncio
async def test_mulligans_three_lands_without_enough_omnath_colors() -> None:
    hand = [card(f"land-{index}", "Forest", "Basic Land - Forest") for index in range(3)]
    decision = choice(
        "mulligan",
        [option("keep", "keepHand"), option("mulligan", "takeMulligan")],
        state(hand=hand),
    )
    assert (await OmnathAgent().make_decision(decision)).action_id == "mulligan"


@pytest.mark.asyncio
async def test_casts_omnath_before_unrelated_spell() -> None:
    filler = card("filler", "Filler", "Enchantment", mana_cost="{2}")
    decision = choice(
        "priority",
        [
            option("cast-filler", "castSpell", source="filler"),
            option("cast-omnath", "castSpell", source="omnath-zone"),
            option("pass", "passPriority"),
        ],
        state(hand=[filler], command_zone=[omnath_card("omnath-zone")]),
    )
    assert (await OmnathAgent().make_decision(decision)).action_id == "cast-omnath"


@pytest.mark.asyncio
async def test_accelerates_mana_when_omnath_is_not_yet_castable() -> None:
    ring = card(
        "ring",
        "Sol Ring",
        "Artifact",
        mana_cost="{1}",
        rules=[{"effect": {"kind": "addMana"}}],
    )
    filler = card("filler", "Filler", "Creature", mana_cost="{1}")
    decision = choice(
        "priority",
        [
            option("cast-filler", "castSpell", source="filler"),
            option("cast-ring", "castSpell", source="ring"),
            option("pass", "passPriority"),
        ],
        state(hand=[ring, filler], command_zone=[omnath_card("omnath-zone")]),
    )
    assert (await OmnathAgent().make_decision(decision)).action_id == "cast-ring"


@pytest.mark.asyncio
async def test_holds_instant_x_while_omnath_is_safe() -> None:
    explosion = card(
        "explosion",
        "Expansion // Explosion",
        "Instant // Instant",
        mana_cost="{X}{U}{U}{R}{R}",
        rules=[{"effects": [{"kind": "dealDamage"}, {"kind": "drawCards"}]}],
    )
    decision = choice(
        "priority",
        [
            option(
                "explosion-x8",
                "castSpell",
                source="explosion",
                x_value=8,
                targets=player_target("p2"),
            ),
            option("pass", "passPriority"),
        ],
        state(hand=[explosion], battlefield=[omnath_card()], my_turn=False),
    )
    assert (await OmnathAgent().make_decision(decision)).action_id == "pass"


@pytest.mark.asyncio
async def test_spends_instant_x_when_omnath_is_targeted() -> None:
    explosion = card(
        "explosion",
        "Expansion // Explosion",
        "Instant // Instant",
        mana_cost="{X}{U}{U}{R}{R}",
    )
    removal_on_stack = {
        "id": "removal-stack",
        "controller": "p2",
        "targets": permanent_target("omnath"),
    }
    decision = choice(
        "priority",
        [
            option(
                "explosion-x4",
                "castSpell",
                source="explosion",
                x_value=4,
                targets=player_target("p2"),
            ),
            option(
                "explosion-x8",
                "castSpell",
                source="explosion",
                x_value=8,
                targets=player_target("p2"),
            ),
            option("pass", "passPriority"),
        ],
        state(
            hand=[explosion],
            battlefield=[omnath_card()],
            stack=[removal_on_stack],
            my_turn=False,
        ),
    )
    assert (await OmnathAgent().make_decision(decision)).action_id == "explosion-x8"


@pytest.mark.asyncio
async def test_spends_instant_x_in_response_to_a_board_wipe() -> None:
    revelation = card(
        "revelation",
        "Sphinx's Revelation",
        "Instant",
        mana_cost="{X}{W}{U}{U}",
        rules=[{"effects": [{"kind": "drawCards"}, {"kind": "gainLife"}]}],
    )
    wipe = card(
        "wipe",
        "Wrath",
        "Sorcery",
        controller="p2",
        rules=[{"effect": {"kind": "destroyPermanent"}}],
    )
    decision = choice(
        "priority",
        [
            option(
                "revelation-x7",
                "castSpell",
                source="revelation",
                x_value=7,
                targets=player_target("p1"),
            ),
            option("pass", "passPriority"),
        ],
        state(
            hand=[revelation],
            battlefield=[omnath_card()],
            stack=[{"id": "wipe-stack", "controller": "p2", "card": wipe, "targets": {}}],
            my_turn=False,
        ),
    )
    assert (await OmnathAgent().make_decision(decision)).action_id == "revelation-x7"


@pytest.mark.asyncio
async def test_protects_omnath_before_cashing_in_an_x_spell() -> None:
    counter = card(
        "counter",
        "To the Crystal Tower",
        "Instant",
        mana_cost="{1}{U}{U}{U}",
        rules=[{"effect": {"kind": "counterSpell"}}],
    )
    explosion = card(
        "explosion",
        "Expansion // Explosion",
        "Instant // Instant",
        mana_cost="{X}{U}{U}{R}{R}",
    )
    removal_on_stack = {
        "id": "removal-stack",
        "controller": "p2",
        "targets": permanent_target("omnath"),
    }
    decision = choice(
        "priority",
        [
            option(
                "counter-removal",
                "castSpell",
                source="counter",
                targets=stack_target("removal-stack"),
            ),
            option(
                "explosion-x8",
                "castSpell",
                source="explosion",
                x_value=8,
                targets=player_target("p2"),
            ),
            option("pass", "passPriority"),
        ],
        state(
            hand=[counter, explosion],
            battlefield=[omnath_card()],
            stack=[removal_on_stack],
            my_turn=False,
        ),
    )
    assert (await OmnathAgent().make_decision(decision)).action_id == "counter-removal"


@pytest.mark.asyncio
async def test_casts_sorcery_x_for_lethal() -> None:
    lavalanche = card(
        "lavalanche",
        "Lavalanche",
        "Sorcery",
        mana_cost="{X}{B}{R}{G}",
        rules=[{"effect": {"kind": "dealDamage"}}],
    )
    decision = choice(
        "priority",
        [
            option(
                "lava-x4",
                "castSpell",
                source="lavalanche",
                x_value=4,
                targets=player_target("p2"),
            ),
            option(
                "lava-x7",
                "castSpell",
                source="lavalanche",
                x_value=7,
                targets=player_target("p2"),
            ),
            option("pass", "passPriority"),
        ],
        state(hand=[lavalanche], battlefield=[omnath_card()], opponent_life=7),
    )
    assert (await OmnathAgent().make_decision(decision)).action_id == "lava-x7"


@pytest.mark.asyncio
async def test_casts_large_sorcery_x_for_cards_but_not_small_one() -> None:
    firemind = card(
        "firemind",
        "Invoke the Firemind",
        "Sorcery",
        mana_cost="{X}{U}{U}{R}",
        rules=[{"effect": {"kind": "drawCards"}}],
    )
    decision = choice(
        "priority",
        [
            option(
                "draw-x3",
                "castSpell",
                source="firemind",
                x_value=3,
                targets=player_target("p1"),
            ),
            option(
                "draw-x6",
                "castSpell",
                source="firemind",
                x_value=6,
                targets=player_target("p1"),
            ),
            option("pass", "passPriority"),
        ],
        state(hand=[firemind], battlefield=[omnath_card()]),
    )
    assert (await OmnathAgent().make_decision(decision)).action_id == "draw-x6"


@pytest.mark.asyncio
async def test_targets_the_most_threatening_player_with_lavalanche() -> None:
    lavalanche = card(
        "lavalanche",
        "Lavalanche",
        "Sorcery",
        mana_cost="{X}{B}{R}{G}",
        rules=[{"effect": {"kind": "dealDamage"}}],
    )
    threat = card(
        "threat",
        "Threat",
        "Creature",
        controller="p2",
        mana_cost="{6}",
        power=8,
        toughness=8,
    )
    decision = choice(
        "priority",
        [
            option(
                "lava-threat",
                "castSpell",
                source="lavalanche",
                x_value=5,
                targets=player_target("p2"),
            ),
            option("pass", "passPriority"),
        ],
        state(hand=[lavalanche], battlefield=[omnath_card()], opponent_battlefield=[threat]),
    )
    assert (await OmnathAgent().make_decision(decision)).action_id == "lava-threat"
