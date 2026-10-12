from __future__ import annotations

import re
from collections.abc import Callable, Mapping

from deepdeck_agent import Action, Card, Decision, DecisionResult

from .heuristic import HeuristicAgent, _produced_colors, mana_value

OMNATH_NAME = "omnath, locus of all"
MAIN_PHASES = {"precombatMain", "postcombatMain"}

MANA_ENGINE_NAMES = {
    "animist's awakening",
    "arcane signet",
    "awakening",
    "bender's waterskin",
    "boundless realms",
    "burgeoning",
    "chromatic lantern",
    "doubling cube",
    "earthbender ascension",
    "entish restoration",
    "exploration",
    "mana reflection",
    "mirari's wake",
    "nature's lore",
    "newfound adventure",
    "reshape the earth",
    "sol ring",
    "springleaf parade",
    "tectonic split",
    "three visits",
    "tome of the guildpact",
}
X_RAMP_NAMES = {"animist's awakening", "springleaf parade"}
X_DRAW_NAMES = {
    "blue sun's zenith",
    "drown in dreams",
    "expansion // explosion",
    "invoke the firemind",
    "sphinx's revelation",
}
X_DAMAGE_NAMES = {
    "debt to the deathless",
    "expansion // explosion",
    "invoke the firemind",
    "lavalanche",
}
X_REMOVAL_NAMES = {"klauth's will", "lavalanche"}
REMOVAL_KINDS = (
    "dealDamage",
    "destroyPermanent",
    "exilePermanent",
    "moveToExile",
    "moveToGraveyard",
)
X_ADVANTAGE_NAMES = {
    "doppelgang",
    "mind grind",
    "storm king's thunder",
    "villainous wealth",
}
AMPLIFIER_NAMES = {
    "fated firepower",
    "threefold signal",
    "unbound flourishing",
    "whirlwind of thought",
}
ANY_COLOR_SOURCES = {
    "arcane signet",
    "bender's waterskin",
    "chromatic lantern",
    "command tower",
    "the world tree",
}
PROTECTION_KINDS = (
    "changeTargets",
    "chooseNewTargets",
    "counterSpell",
    "hexproof",
    "indestructible",
    "phaseOut",
    "protection",
    "returnToHand",
    "shroud",
)


def _is_omnath(card: Card | None) -> bool:
    return card is not None and card.name.casefold() == OMNATH_NAME


def _source(decision: Decision, action: Action) -> Card | None:
    return decision.card_for(action)


def _source_name(decision: Decision, action: Action) -> str:
    card = _source(decision, action)
    return card.name.casefold() if card is not None else ""


def _x_value(action: Action) -> int:
    decisions = action.raw.get("decisions", {})
    if isinstance(decisions, Mapping):
        value = decisions.get("xValue")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return max(0, int(value))
    match = re.search(r"\bX\s*=\s*(\d+)", action.label, re.IGNORECASE)
    return int(match.group(1)) if match else 0


def _is_x_spell(card: Card | None) -> bool:
    return card is not None and "{X}" in card.mana_cost.upper()


def _is_instant(card: Card | None) -> bool:
    return card is not None and card.is_type("Instant")


def _is_sorcery(card: Card | None) -> bool:
    return card is not None and card.is_type("Sorcery") and not card.is_type("Instant")


def _is_protection(card: Card | None) -> bool:
    return card is not None and (
        card.rules_contain(*PROTECTION_KINDS)
        or card.name.casefold()
        in {"lightning greaves", "silver shroud costume", "to the crystal tower"}
    )


def _is_mana_engine(card: Card | None) -> bool:
    if card is None:
        return False
    name = card.name.casefold()
    return name in MANA_ENGINE_NAMES or card.rules_contain(
        "addMana",
        "additional land",
        "additionalLand",
        "doubleMana",
        "putLand",
        "searchLibrary",
    )


def _targets_instance(value: object, instance_id: str) -> bool:
    if isinstance(value, Mapping):
        if value.get("instanceId") == instance_id:
            return True
        return any(_targets_instance(child, instance_id) for child in value.values())
    if isinstance(value, list):
        return any(_targets_instance(child, instance_id) for child in value)
    return False


def _opponent_threat(decision: Decision, player_id: str) -> int:
    player = decision.game.player(player_id)
    if player is None:
        return -1
    return sum(
        max(0, permanent.power) + max(0, permanent.toughness) + mana_value(permanent.mana_cost)
        for permanent in player.battlefield
    )


def _target_threat(decision: Decision, action: Action) -> int:
    opponent_ids = {player.id for player in decision.game.opponents}
    permanent_threat = sum(
        max(0, permanent.power) + max(0, permanent.toughness) + mana_value(permanent.mana_cost)
        for permanent in decision.target_permanents(action)
        if permanent.controller in opponent_ids
    )
    player_threat = max(
        (_opponent_threat(decision, player_id) for player_id in action.target_player_ids),
        default=0,
    )
    return permanent_threat + player_threat


class OmnathAgent(HeuristicAgent):
    """Mana-first Commander policy for Omnath, Locus of All and X payoffs."""

    def _omnath(self, decision: Decision) -> Card | None:
        return next(
            (card for card in decision.game.me.battlefield if _is_omnath(card)),
            None,
        )

    def _stack_threatens(self, decision: Decision, omnath: Card) -> bool:
        for item in decision.game.stack:
            if item.get("controller") == decision.player_id:
                continue
            targets = item.get("targets", {})
            if _targets_instance(targets, omnath.id):
                return True
            stack_card = item.get("card")
            if (
                not targets
                and isinstance(stack_card, dict)
                and Card(stack_card).rules_contain(*REMOVAL_KINDS)
            ):
                return True
        return False

    def _protection(self, decision: Decision, omnath: Card | None) -> Action | None:
        if omnath is None or not self._stack_threatens(decision, omnath):
            return None
        candidates = [
            action
            for action in decision.actions
            if action.kind in {"castSpell", "activateAbility"}
            and _is_protection(_source(decision, action))
            and (action.target_stack_ids or action.targets_permanent(omnath.id))
        ]
        return self._best(candidates, lambda action: -len(action.payment_sources))

    @staticmethod
    def _best(actions: list[Action], score: Callable[[Action], int]) -> Action | None:
        if not actions:
            return None
        return min(
            actions,
            key=lambda action: (-score(action), action.id),
        )

    def _is_lethal_x(self, decision: Decision, action: Action) -> bool:
        card = _source(decision, action)
        if card is None:
            return False
        name = card.name.casefold()
        x_value = _x_value(action)
        if x_value <= 0:
            return False
        if name == "debt to the deathless":
            return bool(decision.game.opponents) and all(
                player.life <= 2 * x_value for player in decision.game.opponents
            )
        if name in X_DAMAGE_NAMES:
            return any(
                (player := decision.game.player(player_id)) is not None
                and player.id != decision.player_id
                and player.life <= x_value
                for player_id in action.target_player_ids
            )
        if name in {"blue sun's zenith", "drown in dreams", "mind grind"}:
            multiplier = 2 if name == "drown in dreams" else 1
            return any(
                (player := decision.game.player(player_id)) is not None
                and player.id != decision.player_id
                and len(player.zone("library")) <= multiplier * x_value
                for player_id in action.target_player_ids
            )
        return False

    def _x_score(self, decision: Decision, action: Action) -> int:
        card = _source(decision, action)
        if card is None:
            return -1
        name = card.name.casefold()
        x_value = _x_value(action)
        score = x_value * 4 + _target_threat(decision, action)
        if self._is_lethal_x(decision, action):
            score += 10_000
        if decision.player_id in action.target_player_ids and name in X_DRAW_NAMES:
            score += 100
        if name in X_DAMAGE_NAMES:
            score += 40
        if name in X_REMOVAL_NAMES:
            score += 30
        if name in X_ADVANTAGE_NAMES:
            score += 20
        return score

    def _instant_x(self, decision: Decision, *, threatened: bool) -> Action | None:
        cash_out_names = X_DRAW_NAMES | X_DAMAGE_NAMES | X_REMOVAL_NAMES
        candidates = [
            action
            for action in decision.actions_of("castSpell")
            if _is_x_spell(_source(decision, action))
            and _is_instant(_source(decision, action))
            and _source_name(decision, action) in cash_out_names
            and (threatened or self._is_lethal_x(decision, action))
        ]
        return self._best(candidates, lambda action: self._x_score(decision, action))

    def _sorcery_x(self, decision: Decision) -> Action | None:
        candidates: list[Action] = []
        for action in decision.actions_of("castSpell"):
            card = _source(decision, action)
            if not _is_x_spell(card) or not _is_sorcery(card):
                continue
            name = card.name.casefold() if card else ""
            x_value = _x_value(action)
            targets_self = decision.player_id in action.target_player_ids
            high_value = (
                self._is_lethal_x(decision, action)
                or (name in X_DRAW_NAMES and targets_self and x_value >= 4)
                or (name in X_RAMP_NAMES and x_value >= 2)
                or (name in X_ADVANTAGE_NAMES and x_value >= 4)
                or (
                    name in X_REMOVAL_NAMES
                    and x_value >= 3
                    and _target_threat(decision, action) > 0
                )
                or (
                    name == "debt to the deathless"
                    and x_value >= 4
                    and any(player.life <= 2 * x_value for player in decision.game.opponents)
                )
            )
            if high_value:
                candidates.append(action)
        return self._best(candidates, lambda action: self._x_score(decision, action))

    def _mana_action(self, decision: Decision) -> Action | None:
        candidates = [
            action
            for action in decision.actions
            if action.kind in {"castSpell", "activateAbility"}
            and _is_mana_engine(_source(decision, action))
            and (
                not _is_x_spell(_source(decision, action))
                or _source_name(decision, action) in X_RAMP_NAMES
            )
            and (_x_value(action) >= 2 or not _is_x_spell(_source(decision, action)))
        ]

        def score(action: Action) -> int:
            card = _source(decision, action)
            name = card.name.casefold() if card else ""
            impact = {
                "mana reflection": 12,
                "mirari's wake": 11,
                "doubling cube": 10,
                "tectonic split": 10,
                "boundless realms": 9,
                "reshape the earth": 9,
                "sol ring": 8,
                "exploration": 7,
                "burgeoning": 7,
            }.get(name, 5)
            return impact * 10 + _x_value(action) * 3 - len(action.payment_sources)

        return self._best(candidates, score)

    async def choose_mulligan(self, decision: Decision) -> DecisionResult:
        hand = decision.game.me.hand
        lands = sum(card.is_type("Land") for card in hand)
        cheap_ramp = any(_is_mana_engine(card) and mana_value(card.mana_cost) <= 2 for card in hand)
        available_colors = {
            color for card in hand if card.is_type("Land") for color in _produced_colors(card)
        }
        any_color = any(card.name.casefold() in ANY_COLOR_SOURCES for card in hand)
        color_ready = any_color or len(available_colors & set("WURG")) >= 3
        keep = color_ready and (3 <= lands <= 5 or (lands == 2 and cheap_ramp))
        desired = decision.first("keepHand" if keep else "takeMulligan")
        return desired or self._safe_default(decision)

    async def choose_mulligan_bottom(self, decision: Decision) -> DecisionResult:
        lands = sum(card.is_type("Land") for card in decision.game.me.hand)
        candidates = decision.actions_of("bottomCard")

        def discard_score(action: Action) -> tuple[int, int, str]:
            card = _source(decision, action)
            if card is None:
                return (0, 0, action.id)
            protected = int(
                _is_omnath(card)
                or _is_mana_engine(card)
                or _is_x_spell(card)
                or (card.is_type("Land") and lands <= 3)
            )
            return (protected, -mana_value(card.mana_cost), action.id)

        return min(candidates, key=discard_score) if candidates else self._safe_default(decision)

    async def choose_priority(self, decision: Decision) -> DecisionResult:
        omnath = self._omnath(decision)
        threatened = omnath is not None and self._stack_threatens(decision, omnath)
        if protection := self._protection(decision, omnath):
            return protection
        if instant_x := self._instant_x(decision, threatened=threatened):
            return instant_x

        if decision.game.is_my_turn and decision.game.step in MAIN_PHASES:
            if land := decision.first("playLand"):
                return land
            commander = next(
                (
                    action
                    for action in decision.actions_of("castSpell")
                    if _is_omnath(_source(decision, action))
                ),
                None,
            )
            if commander:
                return commander
            payoff = self._sorcery_x(decision) if omnath is not None else None
            if payoff is not None and self._is_lethal_x(decision, payoff):
                return payoff
            if mana := self._mana_action(decision):
                return mana
            if omnath is not None:
                if payoff is not None:
                    return payoff
                amplifier = self._best(
                    [
                        action
                        for action in decision.actions_of("castSpell")
                        if _source_name(decision, action) in AMPLIFIER_NAMES
                    ],
                    lambda action: _x_value(action) * 10 - len(action.payment_sources),
                )
                if amplifier:
                    return amplifier

            safe_actions = [
                action for action in decision.actions if not _is_x_spell(_source(decision, action))
            ]
            if safe_actions:
                return min(
                    safe_actions,
                    key=lambda action: (-self._action_score(decision, action).score, action.id),
                )

        return decision.pass_action or self._safe_default(decision)

    async def choose_discard(self, decision: Decision) -> DecisionResult:
        candidates = decision.actions_of("discard")

        def keep_value(action: Action) -> tuple[int, int, str]:
            card = _source(decision, action)
            if card is None:
                return (0, 0, action.id)
            protected = int(
                _is_omnath(card)
                or _is_mana_engine(card)
                or _is_x_spell(card)
                or _is_protection(card)
            )
            return (protected, mana_value(card.mana_cost), action.id)

        return min(candidates, key=keep_value) if candidates else self._safe_default(decision)
