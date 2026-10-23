from __future__ import annotations

import pytest
from deepdeck_agent import Decision, Game

from deepdeck_examples import AlexiosAgent


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
    step: str = "precombatMain",
    stack: list[dict] | None = None,
    combat: dict | None = None,
) -> dict:
    return {
        "turnNumber": 3,
        "activePlayer": 0,
        "step": step,
        "players": [
            {
                "id": "p1",
                "name": "Alexios pilot",
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
                "life": 12,
                "hand": [],
                "battlefield": opponent_battlefield or [],
                "commandZone": [],
                "library": [],
                "graveyard": [],
                "exile": [],
                "sideboard": [],
                "manaPool": [],
            },
        ],
        "stack": stack or [],
        "combat": combat or {"attackers": [], "blockers": []},
        "events": [],
    }


def choice(kind: str, options: list[dict], current_state: dict) -> Decision:
    return Decision("decision", "p1", {"kind": kind, "options": options}, Game(current_state, "p1"))


def option(
    action_id: str,
    kind: str,
    *,
    source: str | None = None,
    payments: list[str] | None = None,
    targets: dict | None = None,
    label: str = "",
    attacker: str | None = None,
) -> dict:
    return {
        "id": action_id,
        "kind": kind,
        "playerId": "p1",
        "label": label or action_id,
        "cardInstanceId": source,
        "paymentSources": payments or [],
        "targets": targets or {},
        "attackerId": attacker,
    }


def permanent_target(instance_id: str) -> dict:
    return {"target": {"kind": "permanent", "instanceId": instance_id}}


@pytest.mark.asyncio
@pytest.mark.parametrize("lands, expected", [(2, "mulligan"), (3, "keep")])
async def test_mulligan_requires_at_least_three_lands(lands: int, expected: str) -> None:
    hand = [card(f"land-{index}", "Mountain", "Basic Land") for index in range(lands)]
    hand += [card("spell", "Sword", "Artifact - Equipment")]
    decision = choice(
        "mulligan",
        [option("keep", "keepHand"), option("mulligan", "takeMulligan")],
        state(hand=hand),
    )
    assert (await AlexiosAgent().make_decision(decision)).action_id == expected


@pytest.mark.asyncio
async def test_casts_alexios_before_other_permanents() -> None:
    alexios = card(
        "alexios-zone",
        "Alexios, Deimos of Kosmos",
        "Legendary Creature - Human Berserker",
        mana_cost="{3}{R}",
        power=4,
        toughness=4,
    )
    equipment = card("sword-hand", "Test Sword", "Artifact - Equipment", mana_cost="{2}")
    decision = choice(
        "priority",
        [
            option("cast-sword", "castSpell", source="sword-hand"),
            option("cast-alexios", "castSpell", source="alexios-zone"),
            option("pass", "passPriority"),
        ],
        state(hand=[equipment], command_zone=[alexios]),
    )
    assert (await AlexiosAgent().make_decision(decision)).action_id == "cast-alexios"


@pytest.mark.asyncio
async def test_equips_as_much_as_possible_without_spending_reserved_redirect_mana() -> None:
    alexios = card(
        "alexios",
        "Alexios, Deimos of Kosmos",
        "Legendary Creature",
        power=4,
        toughness=4,
    )
    sword = card("sword", "Test Sword", "Artifact - Equipment")
    lands = [card(f"land-{index}", "Mountain", "Basic Land") for index in range(4)]
    redirect = card(
        "redirect",
        "Friendly Redirect",
        "Instant",
        mana_cost="{R}",
        rules=[{"effects": [{"kind": "changeTargets"}]}],
    )
    decision = choice(
        "priority",
        [
            option(
                "equip-too-expensive",
                "activateAbility",
                source="sword",
                payments=["land-0", "land-1", "land-2", "land-3"],
                targets=permanent_target("alexios"),
            ),
            option(
                "equip-safe",
                "activateAbility",
                source="sword",
                payments=["land-0", "land-1", "land-2"],
                targets=permanent_target("alexios"),
            ),
            option("pass", "passPriority"),
        ],
        state(hand=[redirect], battlefield=[alexios, sword, *lands]),
    )
    assert (await AlexiosAgent().make_decision(decision)).action_id == "equip-safe"


@pytest.mark.asyncio
async def test_removes_a_blocker_that_can_kill_alexios() -> None:
    alexios = card(
        "alexios",
        "Alexios, Deimos of Kosmos",
        "Legendary Creature",
        power=4,
        toughness=4,
    )
    removal = card(
        "removal",
        "Clean Removal",
        "Instant",
        rules=[{"effects": [{"kind": "destroyPermanent"}]}],
    )
    small = card("small", "Small", "Creature", controller="p2", power=2, toughness=2)
    danger = card("danger", "Danger", "Creature", controller="p2", power=5, toughness=5)
    decision = choice(
        "priority",
        [
            option(
                "remove-small",
                "castSpell",
                source="removal",
                targets=permanent_target("small"),
            ),
            option(
                "remove-danger",
                "castSpell",
                source="removal",
                targets=permanent_target("danger"),
            ),
            option("pass", "passPriority"),
        ],
        state(hand=[removal], battlefield=[alexios], opponent_battlefield=[small, danger]),
    )
    assert (await AlexiosAgent().make_decision(decision)).action_id == "remove-danger"


@pytest.mark.asyncio
async def test_goads_the_highest_power_opposing_creature() -> None:
    goad = card(
        "goad-source",
        "Taunt",
        "Sorcery",
        rules=[{"effects": [{"kind": "goadTargetCreature"}]}],
    )
    small = card("small", "Small", "Creature", controller="p2", power=2, toughness=2)
    large = card("large", "Large", "Creature", controller="p2", power=8, toughness=8)
    decision = choice(
        "priority",
        [
            option(
                "goad-small",
                "castSpell",
                source="goad-source",
                targets=permanent_target("small"),
            ),
            option(
                "goad-large",
                "castSpell",
                source="goad-source",
                targets=permanent_target("large"),
            ),
            option("pass", "passPriority"),
        ],
        state(hand=[goad], opponent_battlefield=[small, large]),
    )
    assert (await AlexiosAgent().make_decision(decision)).action_id == "goad-large"


@pytest.mark.asyncio
async def test_cracks_a_clue_only_as_the_last_utility_action() -> None:
    clue = card("clue", "Clue", "Token Artifact - Clue")
    decision = choice(
        "priority",
        [option("crack", "activateAbility", source="clue"), option("pass", "passPriority")],
        state(battlefield=[clue], step="endStep"),
    )
    assert (await AlexiosAgent().make_decision(decision)).action_id == "crack"


@pytest.mark.asyncio
async def test_does_not_crack_a_clue_with_redirect_mana_reserved() -> None:
    alexios = card(
        "alexios",
        "Alexios, Deimos of Kosmos",
        "Legendary Creature",
        power=4,
        toughness=4,
    )
    clue = card("clue", "Clue", "Token Artifact - Clue")
    mountain = card("mountain", "Mountain", "Basic Land")
    redirect = card(
        "redirect",
        "Friendly Redirect",
        "Instant",
        mana_cost="{R}",
        rules=[{"effects": [{"kind": "changeTargets"}]}],
    )
    decision = choice(
        "priority",
        [
            option("crack", "activateAbility", source="clue", payments=["mountain"]),
            option("pass", "passPriority"),
        ],
        state(hand=[redirect], battlefield=[alexios, clue, mountain], step="endStep"),
    )
    assert (await AlexiosAgent().make_decision(decision)).action_id == "pass"
