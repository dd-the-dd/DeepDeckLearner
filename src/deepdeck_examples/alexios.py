from __future__ import annotations

import re

from deepdeck_agent import Action, Agent, Card, Decision, DecisionResult

ALEXIOS_NAME = "alexios, deimos of kosmos"
REDIRECTION_KINDS = ("changeTargets", "chooseNewTargets", "redirect")
REMOVAL_KINDS = (
    "destroyPermanent",
    "exilePermanent",
    "moveToGraveyard",
    "moveToExile",
    "dealDamage",
)
BOOST_KINDS = ("modifyPowerToughness", "powerModifier", "getsPowerToughness")


def _is_alexios(card: Card | None) -> bool:
    return card is not None and card.name.casefold() == ALEXIOS_NAME


def _mana_value(mana_cost: str) -> int:
    total = 0
    for symbol in re.findall(r"\{([^}]+)\}", mana_cost):
        if symbol.isdigit():
            total += int(symbol)
        elif symbol.upper() not in {"X", "Y", "Z"}:
            total += 1
    return total


def _source(decision: Decision, action: Action) -> Card | None:
    return decision.card_for(action)


def _is_equipment(card: Card | None) -> bool:
    return card is not None and card.is_type("Equipment")


def _is_creature(card: Card | None) -> bool:
    return card is not None and card.is_type("Creature")


def _is_permanent(card: Card | None) -> bool:
    return card is not None and any(
        card.is_type(card_type)
        for card_type in ("Artifact", "Battle", "Creature", "Enchantment", "Planeswalker")
    )


def _is_redirect(card: Card | None) -> bool:
    return card is not None and (
        card.rules_contain(*REDIRECTION_KINDS)
        or "choose new targets" in card.name.casefold()
        or card.name.casefold() == "deflecting swat"
    )


def _is_boost(card: Card | None) -> bool:
    return card is not None and card.is_type("Instant") and card.rules_contain(*BOOST_KINDS)


def _is_removal(card: Card | None) -> bool:
    return card is not None and card.rules_contain(*REMOVAL_KINDS)


def _is_goad(action: Action, card: Card | None) -> bool:
    return "goad" in action.label.casefold() or (card is not None and card.rules_contain("goad"))


def _is_food_or_clue(card: Card | None) -> bool:
    return card is not None and (
        card.is_type("Food")
        or card.is_type("Clue")
        or card.name.casefold() in {"food", "clue"}
    )


def _action_cost(action: Action, card: Card | None) -> int:
    if action.payment_sources:
        return len(set(action.payment_sources))
    return _mana_value(card.mana_cost) if action.kind == "castSpell" and card is not None else 0


def _opponent_target_power(decision: Decision, action: Action) -> int:
    opponent_ids = {player.id for player in decision.game.opponents}
    return max(
        (
            permanent.power
            for permanent in decision.target_permanents(action)
            if permanent.controller in opponent_ids and permanent.is_type("Creature")
        ),
        default=-1,
    )


class AlexiosAgent(Agent):
    """A transparent, intentionally simple Alexios equipment policy."""

    async def choose_mulligan(self, decision: Decision) -> DecisionResult:
        land_count = sum(card.is_type("Land") for card in decision.game.me.hand)
        if land_count >= 3:
            return decision.first("keepHand") or self._safe_default(decision)
        return decision.first("takeMulligan") or self._safe_default(decision)

    async def choose_mulligan_bottom(self, decision: Decision) -> DecisionResult:
        candidates = [
            (action, _source(decision, action))
            for action in decision.actions
            if action.kind == "bottomCard"
        ]
        if not candidates:
            return self._safe_default(decision)
        lands_in_hand = sum(card.is_type("Land") for card in decision.game.me.hand)
        if lands_in_hand > 3:
            land = next(
                (action for action, card in candidates if card and card.is_type("Land")),
                None,
            )
            if land:
                return land
        return max(candidates, key=lambda pair: _mana_value(pair[1].mana_cost) if pair[1] else 0)[0]

    def _alexios(self, decision: Decision) -> Card | None:
        return next(
            (
                permanent
                for player in decision.game.players
                for permanent in player.battlefield
                if _is_alexios(permanent)
            ),
            None,
        )

    def _stack_threatens(self, decision: Decision, alexios: Card) -> bool:
        for stack_object in decision.game.stack:
            if stack_object.get("controller") == decision.player_id:
                continue
            targets = stack_object.get("targets", {})
            if isinstance(targets, dict) and any(
                isinstance(target, dict) and target.get("instanceId") == alexios.id
                for target in targets.values()
            ):
                return True
        return False

    def _reserve(self, decision: Decision, alexios: Card | None) -> int:
        redirect_costs = [
            _mana_value(card.mana_cost) for card in decision.game.me.hand if _is_redirect(card)
        ]
        reserve = min(redirect_costs) if redirect_costs else 0
        if alexios is None:
            return reserve
        defending_ids = {
            str(attacker.get("defendingPlayerId"))
            for attacker in decision.game.raw.get("combat", {}).get("attackers", [])
            if isinstance(attacker, dict) and attacker.get("attackerId") == alexios.id
        }
        defending_life = min(
            (
                player.life
                for player_id in defending_ids
                if (player := decision.game.player(player_id)) is not None
            ),
            default=10**6,
        )
        if defending_life <= alexios.power + 4:
            boost_costs = [
                _mana_value(card.mana_cost) for card in decision.game.me.hand if _is_boost(card)
            ]
            if boost_costs:
                reserve += min(boost_costs)
        return reserve

    def _can_spend(self, decision: Decision, action: Action, reserve: int) -> bool:
        return (
            decision.game.me.available_mana_count
            - _action_cost(action, _source(decision, action))
            >= reserve
        )

    def _reactive_protection(self, decision: Decision, alexios: Card | None) -> Action | None:
        if alexios is None or not self._stack_threatens(decision, alexios):
            return None
        return next(
            (
                action
                for action in decision.actions_of("castSpell")
                if _is_redirect(_source(decision, action)) and action.target_stack_ids
            ),
            None,
        )

    def _lethal_boost(self, decision: Decision, alexios: Card | None) -> Action | None:
        combat_steps = {"declareAttackers", "declareBlockers", "combatDamage"}
        if alexios is None or decision.game.step not in combat_steps:
            return None
        defending_ids = {
            str(attacker.get("defendingPlayerId"))
            for attacker in decision.game.raw.get("combat", {}).get("attackers", [])
            if isinstance(attacker, dict) and attacker.get("attackerId") == alexios.id
        }
        if not any(
            (player := decision.game.player(player_id)) is not None
            and player.life <= alexios.power + 4
            for player_id in defending_ids
        ):
            return None
        return next(
            (
                action
                for action in decision.actions_of("castSpell")
                if _is_boost(_source(decision, action)) and action.targets_permanent(alexios.id)
            ),
            None,
        )

    def _dangerous_blocker_removal(
        self,
        decision: Decision,
        alexios: Card | None,
        reserve: int,
    ) -> Action | None:
        if alexios is None:
            return None
        candidates = [
            action
            for action in decision.actions
            if action.kind in {"castSpell", "activateAbility"}
            and _is_removal(_source(decision, action))
            and _opponent_target_power(decision, action) >= alexios.toughness
            and self._can_spend(decision, action, reserve)
        ]
        return max(
            candidates,
            key=lambda action: _opponent_target_power(decision, action),
            default=None,
        )

    def _highest_goad(self, decision: Decision, reserve: int) -> Action | None:
        candidates = [
            action
            for action in decision.actions
            if _is_goad(action, _source(decision, action))
            and _opponent_target_power(decision, action) >= 0
            and self._can_spend(decision, action, reserve)
        ]
        return max(
            candidates,
            key=lambda action: _opponent_target_power(decision, action),
            default=None,
        )

    async def choose_priority(self, decision: Decision) -> DecisionResult:
        alexios = self._alexios(decision)
        if protection := self._reactive_protection(decision, alexios):
            return protection
        if boost := self._lethal_boost(decision, alexios):
            return boost

        reserve = self._reserve(decision, alexios)
        if decision.game.is_my_turn and decision.game.step in {"precombatMain", "postcombatMain"}:
            if land := decision.first("playLand"):
                return land
            alexios_cast = next(
                (
                    action
                    for action in decision.actions_of("castSpell")
                    if _is_alexios(_source(decision, action))
                ),
                None,
            )
            if alexios_cast:
                return alexios_cast

            if alexios is not None:
                equip = next(
                    (
                        action
                        for action in decision.actions_of("activateAbility")
                        if _is_equipment(_source(decision, action))
                        and action.targets_permanent(alexios.id)
                        and self._can_spend(decision, action, reserve)
                    ),
                    None,
                )
                if equip:
                    return equip
                if removal := self._dangerous_blocker_removal(decision, alexios, reserve):
                    return removal

            equipment = next(
                (
                    action
                    for action in decision.actions_of("castSpell")
                    if _is_equipment(_source(decision, action))
                    and self._can_spend(decision, action, reserve)
                ),
                None,
            )
            if equipment:
                return equipment
            if goad := self._highest_goad(decision, reserve):
                return goad
            creature = next(
                (
                    action
                    for action in decision.actions_of("castSpell")
                    if _is_creature(_source(decision, action))
                    and self._can_spend(decision, action, reserve)
                ),
                None,
            )
            if creature:
                return creature
            permanent = next(
                (
                    action
                    for action in decision.actions_of("castSpell")
                    if _is_permanent(_source(decision, action))
                    and self._can_spend(decision, action, reserve)
                ),
                None,
            )
            if permanent:
                return permanent
            removal = max(
                (
                    action
                    for action in decision.actions
                    if action.kind in {"castSpell", "activateAbility"}
                    and _is_removal(_source(decision, action))
                    and _opponent_target_power(decision, action) >= 0
                    and self._can_spend(decision, action, reserve)
                ),
                key=lambda action: _opponent_target_power(decision, action),
                default=None,
            )
            if removal:
                return removal

        food_or_clue = next(
            (
                action
                for action in decision.actions_of("activateAbility")
                if _is_food_or_clue(_source(decision, action))
                and self._can_spend(decision, action, reserve)
            ),
            None,
        )
        return food_or_clue or decision.pass_action or self._safe_default(decision)

    async def choose_attackers(self, decision: Decision) -> DecisionResult:
        attacks = decision.actions_of("declareAttacker")
        if not attacks:
            return decision.first("finishAttackers") or self._safe_default(decision)

        def score(action: Action) -> tuple[int, int, int]:
            attacker = decision.game.permanent(action.attacker_id)
            target_life = min(
                (
                    player.life
                    for player_id in action.target_player_ids
                    if (player := decision.game.player(player_id)) is not None
                ),
                default=10**6,
            )
            return (
                1 if _is_alexios(attacker) else 0,
                attacker.power if attacker else 0,
                -target_life,
            )

        return max(attacks, key=score)

    async def choose_blockers(self, decision: Decision) -> DecisionResult:
        blocks = decision.actions_of("declareBlocker")
        if not blocks:
            return decision.first("finishBlockers") or self._safe_default(decision)

        def score(action: Action) -> tuple[int, int, int]:
            blocker = decision.game.permanent(action.blocker_id)
            attacker = decision.game.permanent(action.attacker_id)
            if blocker is None or attacker is None:
                return (0, 0, 0)
            kills = int(blocker.power >= attacker.toughness)
            survives = int(attacker.power < blocker.toughness)
            return (kills + survives, attacker.power, -blocker.power)

        best = max(blocks, key=score)
        return best if score(best)[0] > 0 else decision.first("finishBlockers") or best

    async def choose_discard(self, decision: Decision) -> DecisionResult:
        discards = decision.actions_of("discard")
        if not discards:
            return self._safe_default(decision)

        def keep_value(action: Action) -> tuple[int, int]:
            card = _source(decision, action)
            if card is None:
                return (0, 0)
            protected = int(_is_alexios(card) or _is_redirect(card) or _is_equipment(card))
            return (protected, -_mana_value(card.mana_cost))

        return min(discards, key=keep_value)

    async def choose_resolution(self, decision: Decision) -> DecisionResult:
        if goad := self._highest_goad(decision, reserve=0):
            return goad
        return await super().choose_resolution(decision)
