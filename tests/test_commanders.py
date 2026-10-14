from __future__ import annotations

import pytest
from deepdeck_agent import Decision, Game

from deepdeck_examples import (
    AangAgent,
    AzulaAgent,
    KataraAgent,
    KellanAgent,
    SekKuarAgent,
    TatyovaAgent,
    TophAgent,
    YennaAgent,
    commander_config,
)


def card(
    instance_id: str,
    name: str,
    type_line: str,
    *,
    controller: str = "p1",
    mana_cost: str = "{2}",
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
            "power": "3" if "Creature" in type_line else None,
            "toughness": "3" if "Creature" in type_line else None,
            "rules": rules or [],
        },
        "tapped": False,
        "counters": {},
    }


def state(
    *,
    hand: list[dict[str, object]] | None = None,
    battlefield: list[dict[str, object]] | None = None,
    command_zone: list[dict[str, object]] | None = None,
    exile: list[dict[str, object]] | None = None,
    step: str = "precombatMain",
    combat: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "turnNumber": 4,
        "activePlayer": 0,
        "step": step,
        "players": [
            {
                "id": "p1",
                "name": "Commander policy",
                "life": 40,
                "hand": hand or [],
                "battlefield": battlefield or [],
                "commandZone": command_zone or [],
                "library": [],
                "graveyard": [],
                "exile": exile or [],
                "sideboard": [],
                "manaPool": [],
            },
            {
                "id": "p2",
                "name": "Opponent",
                "life": 30,
                "hand": [],
                "battlefield": [],
                "commandZone": [],
                "library": [],
                "graveyard": [],
                "exile": [],
                "sideboard": [],
                "manaPool": [],
            },
        ],
        "stack": [],
        "combat": combat or {"attackers": [], "blockers": []},
        "events": [],
    }


def option(
    action_id: str,
    kind: str,
    *,
    source: str | None = None,
    attacker: str | None = None,
    targets: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "id": action_id,
        "kind": kind,
        "playerId": "p1",
        "label": action_id,
        "cardInstanceId": source,
        "paymentSources": [],
        "targets": targets or {},
        "attackerId": attacker,
    }


def decision(options: list[dict[str, object]], current_state: dict[str, object]) -> Decision:
    return Decision(
        "decision",
        "p1",
        {"kind": "priority", "options": options},
        Game(current_state, "p1"),
    )


COMMANDERS = [
    (AangAgent, "Avatar Aang // Aang, Master of Elements"),
    (AzulaAgent, "Fire Lord Azula"),
    (KataraAgent, "Katara, the Fearless"),
    (KellanAgent, "Kellan, the Kid"),
    (TatyovaAgent, "Tatyova, Benthic Druid"),
    (TophAgent, "Toph, the First Metalbender"),
    (YennaAgent, "Yenna, Redtooth Regent"),
    (SekKuarAgent, "Sek'Kuar, Deathkeeper"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("agent_type", "commander_name"), COMMANDERS)
async def test_every_policy_casts_its_commander_before_filler(
    agent_type: type,
    commander_name: str,
) -> None:
    commander = card("commander", commander_name, "Legendary Creature")
    filler = card("filler", "Generic permanent", "Artifact")
    result = await agent_type().make_decision(
        decision(
            [
                option("filler", "castSpell", source="filler"),
                option("commander", "castSpell", source="commander"),
                option("pass", "passPriority"),
            ],
            state(hand=[filler], command_zone=[commander]),
        )
    )
    assert result.action_id == "commander"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("agent", "commander_name", "preferred"),
    [
        (AangAgent(), "Avatar Aang", card("preferred", "Air Nomad", "Creature - Ally")),
        (KataraAgent(), "Katara, the Fearless", card("preferred", "Water Ally", "Creature - Ally")),
        (TophAgent(), "Toph, the First Metalbender", card("preferred", "Metal", "Artifact")),
        (YennaAgent(), "Yenna, Redtooth Regent", card("preferred", "Blessing", "Enchantment")),
    ],
)
async def test_type_driven_plans_prefer_on_theme_permanents(
    agent: object,
    commander_name: str,
    preferred: dict[str, object],
) -> None:
    commander = card("commander", commander_name, "Legendary Creature")
    filler = card("filler", "Off-theme body", "Creature")
    result = await agent.make_decision(  # type: ignore[attr-defined]
        decision(
            [
                option("preferred", "castSpell", source="preferred"),
                option("filler", "castSpell", source="filler"),
                option("pass", "passPriority"),
            ],
            state(hand=[preferred, filler], battlefield=[commander]),
        )
    )
    assert result.action_id == "preferred"


@pytest.mark.asyncio
async def test_azula_attacks_then_casts_the_compact_combat_spell() -> None:
    azula = card("azula", "Fire Lord Azula", "Legendary Creature")
    instant = card("instant", "Combat trick", "Instant", mana_cost="{U}")
    permanent = card("permanent", "Slow engine", "Artifact", mana_cost="{1}")
    current = state(
        hand=[instant, permanent],
        battlefield=[azula],
        step="declareAttackers",
        combat={"attackers": [{"attackerId": "azula"}], "blockers": []},
    )
    result = await AzulaAgent().make_decision(
        decision(
            [
                option("instant", "castSpell", source="instant"),
                option("permanent", "castSpell", source="permanent"),
                option("pass", "passPriority"),
            ],
            current,
        )
    )
    assert result.action_id == "instant"


@pytest.mark.asyncio
async def test_kellan_prefers_a_cast_from_outside_the_hand() -> None:
    kellan = card("kellan", "Kellan, the Kid", "Legendary Creature")
    exiled = card("exiled", "Adventure return", "Creature")
    hand_card = card("hand", "Hand creature", "Creature")
    result = await KellanAgent().make_decision(
        decision(
            [
                option("from-exile", "castSpell", source="exiled"),
                option("from-hand", "castSpell", source="hand"),
                option("pass", "passPriority"),
            ],
            state(hand=[hand_card], exile=[exiled], battlefield=[kellan]),
        )
    )
    assert result.action_id == "from-exile"


@pytest.mark.asyncio
async def test_tatyova_prioritizes_a_landfall_ramp_action() -> None:
    tatyova = card("tatyova", "Tatyova, Benthic Druid", "Legendary Creature")
    ramp = card(
        "ramp",
        "Extra land",
        "Sorcery",
        rules=[{"kind": "putLandOntoBattlefield"}],
    )
    filler = card("filler", "Body", "Creature")
    result = await TatyovaAgent().make_decision(
        decision(
            [
                option("ramp", "castSpell", source="ramp"),
                option("filler", "castSpell", source="filler"),
                option("pass", "passPriority"),
            ],
            state(hand=[ramp, filler], battlefield=[tatyova]),
        )
    )
    assert result.action_id == "ramp"


@pytest.mark.asyncio
async def test_yenna_activates_her_copy_engine() -> None:
    yenna = card("yenna", "Yenna, Redtooth Regent", "Legendary Creature")
    aura = card("aura", "Valuable Aura", "Enchantment - Aura")
    filler = card("filler", "Body", "Creature")
    result = await YennaAgent().make_decision(
        decision(
            [
                option(
                    "copy-aura",
                    "activateAbility",
                    source="yenna",
                    targets={"target": {"kind": "permanent", "instanceId": "aura"}},
                ),
                option("filler", "castSpell", source="filler"),
                option("pass", "passPriority"),
            ],
            state(hand=[filler], battlefield=[yenna, aura]),
        )
    )
    assert result.action_id == "copy-aura"


@pytest.mark.asyncio
async def test_sek_kuar_uses_a_sacrifice_outlet_after_commander() -> None:
    sek = card("sek", "Sek'Kuar, Deathkeeper", "Legendary Creature")
    outlet = card(
        "outlet",
        "Sacrifice outlet",
        "Artifact",
        rules=[{"cost": "Sacrifice another creature"}],
    )
    filler = card("filler", "Body", "Creature")
    result = await SekKuarAgent().make_decision(
        decision(
            [
                option("sacrifice", "activateAbility", source="outlet"),
                option("filler", "castSpell", source="filler"),
                option("pass", "passPriority"),
            ],
            state(hand=[filler], battlefield=[sek, outlet]),
        )
    )
    assert result.action_id == "sacrifice"


@pytest.mark.parametrize(
    "key",
    ["aang", "azula", "katara", "kellan", "tatyova", "toph", "yenna", "sektuar"],
)
def test_commander_configs_are_scoped_to_one_commander_deck(key: str) -> None:
    config = commander_config(
        key,
        deck_id=f"{key}-version",
        agent_id=f"com.deepdeckleague.test.{key}",
        version="test",
    )
    manifest = config.manifest()
    assert manifest.compatibility.game_modes == ["commander"]
    assert manifest.compatibility.decks.deck_ids == [f"{key}-version"]
