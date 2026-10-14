from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from deepdeck_agent import Action, Card, Decision, DecisionResult, Game

from .heuristic import (
    PROTECTION_WORDS,
    HeuristicAgent,
    HeuristicProfile,
    ScoredChoice,
    _card_from_action,
    _card_value,
    _contains_any,
    _rules_text,
    _walk_strings,
    mana_value,
)

MAIN_PHASES = frozenset({"precombatMain", "postcombatMain"})
THREAT_WORDS = (
    "destroy",
    "exile",
    "damage",
    "sacrifice",
    "return",
    "counter",
    "-x/-x",
)


@dataclass(frozen=True)
class CommanderPlan:
    key: str
    name: str
    commander_names: tuple[str, ...]
    description: str
    synergy_types: tuple[str, ...] = ()
    synergy_words: tuple[str, ...] = ()
    attack_with_commander: bool = False
    combat_spell_bonus: float = 0.0
    outside_hand_bonus: float = 0.0
    landfall_bonus: float = 0.0
    commander_ability_bonus: float = 0.0
    sacrifice_bonus: float = 0.0


COMMANDER_PLANS: dict[str, CommanderPlan] = {
    "aang": CommanderPlan(
        key="aang",
        name="Aang four elements",
        commander_names=("Avatar Aang", "Aang, Master of Elements"),
        description=(
            "Builds a diverse Ally board, advances the four bending themes, and attacks "
            "with Aang when a legal attack is available."
        ),
        synergy_types=("Ally",),
        synergy_words=("airbend", "earthbend", "firebending", "waterbend", "exhaust"),
        attack_with_commander=True,
    ),
    "azula": CommanderPlan(
        key="azula",
        name="Azula combat spells",
        commander_names=("Fire Lord Azula",),
        description=(
            "Attacks with Azula, then converts firebending mana into compact spells that "
            "can be copied during combat."
        ),
        synergy_types=("Instant", "Sorcery"),
        synergy_words=("firebending", "copy", "whenever you cast", "flash"),
        attack_with_commander=True,
        combat_spell_bonus=28.0,
    ),
    "katara": CommanderPlan(
        key="katara",
        name="Katara Ally triggers",
        commander_names=("Katara, the Fearless",),
        description=(
            "Establishes Katara early and prioritizes Allies whose triggered abilities are "
            "amplified by her static ability."
        ),
        synergy_types=("Ally",),
        synergy_words=("triggered ability", "whenever", "waterbend"),
    ),
    "kellan": CommanderPlan(
        key="kellan",
        name="Kellan outside-hand value",
        commander_names=("Kellan, the Kid",),
        description=(
            "Casts from exile, graveyard, Adventure, plot, foretell, suspend, and cascade "
            "to unlock Kellan's free permanent or land trigger."
        ),
        synergy_words=(
            "adventure",
            "cascade",
            "foretell",
            "from exile",
            "from your graveyard",
            "plot",
            "suspend",
            "without paying",
        ),
        outside_hand_bonus=32.0,
    ),
    "tatyova": CommanderPlan(
        key="tatyova",
        name="Tatyova landfall engine",
        commander_names=("Tatyova, Benthic Druid",),
        description=(
            "Ramps into Tatyova, preserves land drops, and prioritizes extra-land and "
            "landfall actions after she resolves."
        ),
        synergy_types=("Land",),
        synergy_words=(
            "additional land",
            "landfall",
            "putland",
            "put a land",
            "searchlibrary",
            "search your library for a land",
        ),
        landfall_bonus=24.0,
    ),
    "toph": CommanderPlan(
        key="toph",
        name="Toph metal and earth",
        commander_names=("Toph, the First Metalbender",),
        description=(
            "Builds the artifact-land engine around Toph and favors earthbend activations "
            "that turn the mana base into pressure."
        ),
        synergy_types=("Artifact", "Land"),
        synergy_words=("earthbend", "metalbending"),
        commander_ability_bonus=22.0,
    ),
    "yenna": CommanderPlan(
        key="yenna",
        name="Yenna enchantment copies",
        commander_names=("Yenna, Redtooth Regent",),
        description=(
            "Develops enchantments before low-value creatures and activates Yenna on the "
            "highest-value legal enchantment target."
        ),
        synergy_types=("Enchantment", "Aura"),
        synergy_words=("constellation", "copy", "token"),
        commander_ability_bonus=30.0,
    ),
    "sektuar": CommanderPlan(
        key="sektuar",
        name="Sek'Kuar sacrifice value",
        commander_names=("Sek'Kuar, Deathkeeper",),
        description=(
            "Builds a creature board, establishes Sek'Kuar, and turns expendable nontoken "
            "creatures into hasty Graveborn pressure through sacrifice outlets."
        ),
        synergy_types=("Creature",),
        synergy_words=("dies", "graveyard", "graveborn", "sacrifice", "token"),
        sacrifice_bonus=26.0,
    ),
}


def commander_plan(key: str) -> CommanderPlan:
    normalized = key.casefold().replace("'", "").replace("-", "")
    aliases = {"sekkuar": "sektuar", "kellanthekid": "kellan"}
    normalized = aliases.get(normalized.replace(" ", ""), normalized)
    try:
        return COMMANDER_PLANS[normalized]
    except KeyError as error:
        supported = ", ".join(sorted(COMMANDER_PLANS))
        raise ValueError(f"unknown Commander policy {key!r}; supported: {supported}") from error


def _is_plan_commander(card: Card | None, plan: CommanderPlan) -> bool:
    if card is None:
        return False
    name = card.name.casefold()
    return any(candidate.casefold() in name for candidate in plan.commander_names)


def _source_zone(decision: Decision, card: Card | None) -> str | None:
    if card is None:
        return None
    for zone in decision.game.ZONES:
        if any(candidate.id == card.id for candidate in decision.game.me.zone(zone)):
            return zone
    return None


def _commander_is_attacking(decision: Decision, commander: Card | None) -> bool:
    if commander is None:
        return False
    combat = decision.game.raw.get("combat", {})
    attackers = combat.get("attackers", []) if isinstance(combat, dict) else []
    return any(
        isinstance(attacker, dict) and attacker.get("attackerId") == commander.id
        for attacker in attackers
    )


class CommanderHeuristicAgent(HeuristicAgent):
    """Reusable Commander-first policy with a small, explicit deck plan."""

    def __init__(
        self,
        plan: CommanderPlan,
        profile: HeuristicProfile | None = None,
    ) -> None:
        super().__init__(
            profile
            or HeuristicProfile(
                land_target=3.0,
                ramp_weight=4.0,
                protection_weight=6.0,
                aggression=1.25,
            )
        )
        self.plan = plan

    def _commander(self, decision: Decision) -> Card | None:
        return next(
            (
                card
                for card in decision.game.me.battlefield
                if _is_plan_commander(card, self.plan)
            ),
            None,
        )

    def _synergy_score(self, card: Card | None) -> float:
        if card is None:
            return 0.0
        text = f"{card.name} {card.type_line} {_rules_text(card)}".casefold()
        type_hits = sum(card.is_type(card_type) for card_type in self.plan.synergy_types)
        word_hits = sum(word.casefold() in text for word in self.plan.synergy_words)
        score = min(type_hits, 2) * 5.0 + min(word_hits, 3) * 4.0
        if self.plan.key == "azula" and (
            card.is_type("Instant") or card.is_type("Sorcery")
        ):
            score += max(0, 4 - mana_value(card.mana_cost)) * 1.5
        return score

    def _keep_value(self, card: Card | None, game: Game) -> float:
        value = super()._keep_value(card, game)
        commander_bonus = 20.0 if _is_plan_commander(card, self.plan) else 0.0
        return value + self._synergy_score(card) + commander_bonus

    def _stack_threatens_commander(self, decision: Decision, commander: Card) -> bool:
        for item in decision.game.stack:
            strings = [value.casefold() for value in _walk_strings(item)]
            if commander.id.casefold() not in strings:
                continue
            if any(word in value for word in THREAT_WORDS for value in strings):
                return True
        return False

    def _is_protection_action(
        self,
        decision: Decision,
        action: Action,
        commander: Card,
    ) -> bool:
        card = _card_from_action(decision, action)
        rules = _rules_text(card)
        return _contains_any(rules, PROTECTION_WORDS) and (
            bool(action.target_stack_ids) or action.targets_permanent(commander.id)
        )

    def _strategy_bonus(self, decision: Decision, action: Action) -> tuple[float, list[str]]:
        card = _card_from_action(decision, action)
        commander = self._commander(decision)
        bonus = self._synergy_score(card)
        reasons = [f"{self.plan.key} synergy={bonus:.2f}"] if bonus else []

        if action.kind == "castSpell" and _is_plan_commander(card, self.plan):
            bonus += 80.0
            reasons.append("cast the commander=80.00")

        if (
            commander is not None
            and self._stack_threatens_commander(decision, commander)
            and self._is_protection_action(decision, action, commander)
        ):
            bonus += 120.0
            reasons.append("protect threatened commander=120.00")

        if (
            self.plan.combat_spell_bonus
            and action.kind == "castSpell"
            and _commander_is_attacking(decision, commander)
        ):
            bonus += self.plan.combat_spell_bonus
            reasons.append(f"spell during commander attack={self.plan.combat_spell_bonus:.2f}")

        if self.plan.outside_hand_bonus and action.kind == "castSpell":
            zone = _source_zone(decision, card)
            if zone is not None and zone != "hand":
                bonus += self.plan.outside_hand_bonus
                reasons.append(f"cast from {zone}={self.plan.outside_hand_bonus:.2f}")

        if (
            self.plan.landfall_bonus
            and commander is not None
            and (
                action.kind == "playLand"
                or _contains_any(_rules_text(card), ("putland", "put a land", "landfall"))
            )
        ):
            bonus += self.plan.landfall_bonus
            reasons.append(f"landfall with commander={self.plan.landfall_bonus:.2f}")

        if (
            self.plan.commander_ability_bonus
            and commander is not None
            and action.kind == "activateAbility"
            and card is not None
            and card.id == commander.id
        ):
            bonus += self.plan.commander_ability_bonus
            reasons.append(f"commander engine={self.plan.commander_ability_bonus:.2f}")
            if self.plan.key == "yenna":
                targets = decision.target_permanents(action)
                bonus += sum(
                    _card_value(target)
                    for target in targets
                    if target.controller == decision.player_id
                )

        if (
            self.plan.sacrifice_bonus
            and commander is not None
            and _contains_any(_rules_text(card), ("sacrifice",))
        ):
            bonus += self.plan.sacrifice_bonus
            reasons.append(f"Sek'Kuar death trigger={self.plan.sacrifice_bonus:.2f}")

        return bonus, reasons

    def _action_score(self, decision: Decision, action: Action) -> ScoredChoice:
        base = super()._action_score(decision, action)
        bonus, reasons = self._strategy_bonus(decision, action)
        return ScoredChoice(
            choice_id=base.choice_id,
            score=base.score + bonus,
            reasons=(*base.reasons, *reasons),
        )

    async def choose_priority(self, decision: Decision) -> DecisionResult:
        if decision.game.is_my_turn and decision.game.step in MAIN_PHASES:
            land = decision.first("playLand")
            if land is not None:
                scores = [self._action_score(decision, action) for action in decision.actions]
                boosted = [
                    ScoredChoice(
                        score.choice_id,
                        score.score + (100.0 if score.choice_id == land.id else 0.0),
                        (*score.reasons, "make the land drop before spending mana=100.00")
                        if score.choice_id == land.id
                        else score.reasons,
                    )
                    for score in scores
                ]
                return self._choose_scored(decision, boosted)
        return await super().choose_priority(decision)

    async def choose_attackers(self, decision: Decision) -> DecisionResult:
        if self.plan.attack_with_commander:
            attacks = [
                action
                for action in decision.actions_of("declareAttacker")
                if _is_plan_commander(decision.game.permanent(action.attacker_id), self.plan)
            ]
            if attacks:
                return min(
                    attacks,
                    key=lambda action: (
                        min(
                            (
                                player.life
                                for player_id in action.target_player_ids
                                if (player := decision.game.player(player_id)) is not None
                            ),
                            default=10**6,
                        ),
                        action.id,
                    ),
                )
        return await super().choose_attackers(decision)

    async def choose_resolution(self, decision: Decision) -> DecisionResult:
        choice = decision.choice or {}
        if choice.get("kind") == "cardSelection":
            candidates = [str(value) for value in choice.get("candidateCardInstanceIds", [])]
            minimum = int(choice.get("minimum", 0))
            maximum = int(choice.get("maximum", len(candidates)))
            prompt = str(choice.get("prompt", "")).casefold()
            harmful = any(word in prompt for word in ("discard", "sacrifice", "exile", "bottom"))
            scored = [
                (
                    self._keep_value(self._candidate_card(decision, candidate), decision.game),
                    candidate,
                )
                for candidate in candidates
            ]
            scored.sort(key=lambda item: (item[0], item[1]) if harmful else (-item[0], item[1]))
            count = minimum if harmful else maximum
            selected = [candidate for _, candidate in scored[:count]]
            self._record(
                decision,
                ",".join(selected),
                [
                    ScoredChoice(candidate, score, (f"{self.plan.key} card value",))
                    for score, candidate in scored
                ],
            )
            return decision.choose_cards(*selected)
        return await super().choose_resolution(decision)


class AangAgent(CommanderHeuristicAgent):
    def __init__(self) -> None:
        super().__init__(COMMANDER_PLANS["aang"])


class AzulaAgent(CommanderHeuristicAgent):
    def __init__(self) -> None:
        super().__init__(COMMANDER_PLANS["azula"])


class KataraAgent(CommanderHeuristicAgent):
    def __init__(self) -> None:
        super().__init__(COMMANDER_PLANS["katara"])


class KellanAgent(CommanderHeuristicAgent):
    def __init__(self) -> None:
        super().__init__(COMMANDER_PLANS["kellan"])


class TatyovaAgent(CommanderHeuristicAgent):
    def __init__(self) -> None:
        super().__init__(COMMANDER_PLANS["tatyova"])


class TophAgent(CommanderHeuristicAgent):
    def __init__(self) -> None:
        super().__init__(COMMANDER_PLANS["toph"])


class YennaAgent(CommanderHeuristicAgent):
    def __init__(self) -> None:
        super().__init__(COMMANDER_PLANS["yenna"])


class SekKuarAgent(CommanderHeuristicAgent):
    def __init__(self) -> None:
        super().__init__(COMMANDER_PLANS["sektuar"])


COMMANDER_AGENT_TYPES: dict[str, Callable[[], CommanderHeuristicAgent]] = {
    "aang": AangAgent,
    "azula": AzulaAgent,
    "katara": KataraAgent,
    "kellan": KellanAgent,
    "tatyova": TatyovaAgent,
    "toph": TophAgent,
    "yenna": YennaAgent,
    "sektuar": SekKuarAgent,
}


def build_commander_agent(key: str) -> CommanderHeuristicAgent:
    plan = commander_plan(key)
    return COMMANDER_AGENT_TYPES[plan.key]()
