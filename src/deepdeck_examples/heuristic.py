from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from deepdeck_agent import Action, Agent, Card, Decision, DecisionResult, Game

COLORS = frozenset("WUBRG")
REMOVAL_WORDS = (
    "destroy",
    "exile",
    "counterspell",
    "countertarget",
    "movetograveyard",
    "dealdamage",
    "-x/-x",
)
DRAW_WORDS = ("drawcard", "drawcards", "draw ")
RAMP_WORDS = ("addmana", "searchlibrary", "putland")
PROTECTION_WORDS = (
    "hexproof",
    "indestructible",
    "protection",
    "changetargets",
    "preventdamage",
)
GRAVEYARD_HATE_WORDS = ("exilegraveyard", "exileallcards", "cards in graveyards")


def _bounded_number(name: str, value: object, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    result = float(value)
    if not minimum <= result <= maximum:
        raise ValueError(f"{name} must be between {minimum:g} and {maximum:g}")
    return result


@dataclass(frozen=True)
class HeuristicProfile:
    """Bounded, serializable controls for the generic programmatic policy."""

    land_target: float = 3.0
    land_balance_weight: float = 2.5
    playable_card_weight: float = 2.0
    color_coverage_weight: float = 2.5
    short_hand_keep_weight: float = 2.0
    keep_threshold: float = 2.0
    play_land_bonus: float = 12.0
    spell_base_score: float = 3.0
    mana_efficiency_weight: float = 1.0
    reserve_mana: float = 1.0
    reaction_weight: float = 6.0
    removal_weight: float = 5.0
    card_draw_weight: float = 3.0
    ramp_weight: float = 2.0
    protection_weight: float = 4.0
    aggression: float = 1.0
    lethal_bonus: float = 30.0
    attack_risk_weight: float = 2.0
    block_trade_weight: float = 4.0
    life_preservation_weight: float = 1.0
    sideboard_change_penalty: float = 3.0

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object] | None) -> HeuristicProfile:
        if raw is None:
            return cls()
        allowed = {field.name for field in fields(cls)}
        unknown = sorted(set(raw) - allowed)
        if unknown:
            raise ValueError(f"unknown heuristic profile fields: {', '.join(unknown)}")
        bounds = {
            "land_target": (0.0, 7.0),
            "reserve_mana": (0.0, 10.0),
        }
        values = {
            name: _bounded_number(name, value, *bounds.get(name, (-50.0, 50.0)))
            for name, value in raw.items()
        }
        return cls(**values)

    @classmethod
    def from_json_file(cls, path: str | Path) -> HeuristicProfile:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("the heuristic profile must be a JSON object")
        profile = payload.get("profile", payload)
        if not isinstance(profile, dict):
            raise ValueError("profile must be a JSON object")
        return cls.from_mapping(profile)

    def to_mapping(self) -> dict[str, float]:
        return {name: float(value) for name, value in asdict(self).items()}


@dataclass(frozen=True)
class ScoredChoice:
    choice_id: str
    score: float
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class DecisionTrace:
    request_id: str
    decision_kind: str
    selected_id: str
    choices: tuple[ScoredChoice, ...]


def _walk_strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        result: list[str] = []
        for child in value.values():
            result.extend(_walk_strings(child))
        return result
    if isinstance(value, list):
        result = []
        for child in value:
            result.extend(_walk_strings(child))
        return result
    return []


def _rules_text(card: Card | None) -> str:
    if card is None:
        return ""
    return " ".join(_walk_strings(card.rules)).casefold()


def _contains_any(value: str, needles: tuple[str, ...]) -> bool:
    return any(needle.casefold() in value for needle in needles)


def mana_value(mana_cost: str) -> int:
    total = 0
    for symbol in re.findall(r"\{([^}]+)\}", mana_cost):
        normalized = symbol.upper()
        if normalized.isdigit():
            total += int(normalized)
        elif normalized in {"X", "Y", "Z", "T", "Q"}:
            continue
        elif "/" in normalized:
            total += 1
        else:
            total += 1
    return total


def _required_colors(card: Card) -> set[str]:
    return {
        color
        for symbol in re.findall(r"\{([^}]+)\}", card.mana_cost.upper())
        for color in COLORS
        if color in symbol
    }


def _produced_colors(card: Card) -> set[str]:
    result: set[str] = set()
    type_line = card.type_line.casefold()
    names = {
        "plains": "W",
        "island": "U",
        "swamp": "B",
        "mountain": "R",
        "forest": "G",
    }
    for name, color in names.items():
        if name in type_line or name in card.name.casefold():
            result.add(color)
    rules = " ".join(_walk_strings(card.rules)).upper()
    if "ADDMANA" in rules or card.is_type("Land"):
        result.update(color for color in COLORS if color in rules)
    identity = card.definition.get("colorIdentity", card.definition.get("colors", []))
    if isinstance(identity, list):
        result.update(str(color).upper() for color in identity if str(color).upper() in COLORS)
    return result


def _card_from_action(decision: Decision, action: Action) -> Card | None:
    card = decision.card_for(action)
    if card is not None:
        return card
    candidate = action.raw.get("decisions", {})
    if isinstance(candidate, dict):
        candidate = candidate.get("openingHandCandidate")
        if isinstance(candidate, dict):
            return Card(candidate)
    return None


def _card_value(card: Card | None) -> float:
    if card is None:
        return 0.0
    value = 1.0
    mv = mana_value(card.mana_cost)
    if card.is_type("Land"):
        value += 1.5
    if card.is_type("Creature"):
        value += 1.5 + max(0, card.power) * 0.35 + max(0, card.toughness) * 0.2
    if card.is_type("Planeswalker"):
        value += 3.0
    if card.is_type("Artifact") or card.is_type("Enchantment"):
        value += 1.0
    rules = _rules_text(card)
    if _contains_any(rules, REMOVAL_WORDS):
        value += 2.5
    if _contains_any(rules, DRAW_WORDS):
        value += 2.0
    if _contains_any(rules, RAMP_WORDS):
        value += 1.5
    if _contains_any(rules, PROTECTION_WORDS):
        value += 1.5
    return value + min(mv, 7) * 0.15


def _threat_value(card: Card | None) -> float:
    if card is None:
        return 0.0
    rules = _rules_text(card)
    value = max(0, card.power) + max(0, card.toughness) * 0.5 + mana_value(card.mana_cost)
    if _contains_any(rules, ("flying", "trample", "double strike", "deathtouch")):
        value += 2.0
    if _contains_any(rules, DRAW_WORDS + REMOVAL_WORDS):
        value += 2.0
    return value


class HeuristicAgent(Agent):
    """Generic deterministic baseline that scores only engine-provided legal choices."""

    def __init__(self, profile: HeuristicProfile | None = None) -> None:
        self.profile = profile or HeuristicProfile()
        self.last_trace: DecisionTrace | None = None
        self.known_cards: dict[str, Card] = {}
        self.opponent_traits: dict[str, float] = {
            "creatures": 0.0,
            "artifacts": 0.0,
            "enchantments": 0.0,
            "graveyard": 0.0,
        }

    async def analyze_starting_situation(
        self,
        observation: dict[str, object],
        known_deck: list[dict[str, object]],
    ) -> None:
        self._remember_known_deck(known_deck)

    async def on_game_start(
        self,
        game: Game,
        known_deck: list[dict[str, object]],
    ) -> None:
        self._remember_known_deck(known_deck)
        self._observe_opponents(game)

    async def on_observation(self, game: Game) -> None:
        self._observe_opponents(game)

    def _remember_known_deck(self, known_deck: list[dict[str, object]]) -> None:
        for raw in known_deck:
            definition = raw.get("definition", raw)
            if not isinstance(definition, dict):
                continue
            identifier = str(definition.get("id") or raw.get("id") or "")
            if identifier:
                self.known_cards[identifier] = Card({"definition": definition})

    def _observe_opponents(self, game: Game) -> None:
        for opponent in game.opponents:
            graveyard = opponent.zone("graveyard")
            visible = [*opponent.battlefield, *graveyard, *opponent.zone("exile")]
            self.opponent_traits["creatures"] = max(
                self.opponent_traits["creatures"],
                float(sum(card.is_type("Creature") for card in visible)),
            )
            self.opponent_traits["artifacts"] = max(
                self.opponent_traits["artifacts"],
                float(sum(card.is_type("Artifact") for card in visible)),
            )
            self.opponent_traits["enchantments"] = max(
                self.opponent_traits["enchantments"],
                float(sum(card.is_type("Enchantment") for card in visible)),
            )
            self.opponent_traits["graveyard"] = max(
                self.opponent_traits["graveyard"], float(len(graveyard))
            )

    def _record(self, decision: Decision, selected: str, choices: list[ScoredChoice]) -> None:
        self.last_trace = DecisionTrace(
            request_id=decision.request_id,
            decision_kind=decision.kind,
            selected_id=selected,
            choices=tuple(sorted(choices, key=lambda item: (-item.score, item.choice_id))),
        )

    def _choose_scored(
        self,
        decision: Decision,
        scores: list[ScoredChoice],
    ) -> DecisionResult:
        if not scores:
            return self._safe_default(decision)
        best = min(scores, key=lambda item: (-item.score, item.choice_id))
        self._record(decision, best.choice_id, scores)
        action = next(
            (candidate for candidate in decision.actions if candidate.id == best.choice_id),
            None,
        )
        return action or self._safe_default(decision)

    def _hand_score(self, cards: list[Card]) -> tuple[float, tuple[str, ...]]:
        lands = [card for card in cards if card.is_type("Land")]
        spells = [card for card in cards if not card.is_type("Land")]
        available_colors = {color for land in lands for color in _produced_colors(land)}
        required_colors = {color for spell in spells for color in _required_colors(spell)}
        playable = sum(
            mana_value(card.mana_cost) <= max(1, len(lands))
            and _required_colors(card).issubset(available_colors)
            for card in spells
        )
        coverage = (
            len(required_colors & available_colors) / len(required_colors)
            if required_colors
            else 1.0
        )
        land_score = -abs(len(lands) - self.profile.land_target) * self.profile.land_balance_weight
        playable_score = playable * self.profile.playable_card_weight
        color_score = coverage * self.profile.color_coverage_weight
        short_score = max(0, 7 - len(cards)) * self.profile.short_hand_keep_weight
        total = land_score + playable_score + color_score + short_score
        reasons = (
            f"lands={len(lands)} contributes {land_score:.2f}",
            f"playable={playable} contributes {playable_score:.2f}",
            f"colorCoverage={coverage:.2f} contributes {color_score:.2f}",
            f"shortHandPressure contributes {short_score:.2f}",
        )
        return total, reasons

    async def choose_opening_hand(self, decision: Decision) -> DecisionResult:
        current = list(decision.game.me.hand)
        scores = []
        for action in decision.actions:
            card = _card_from_action(decision, action)
            score, reasons = self._hand_score([*current, *([card] if card else [])])
            scores.append(ScoredChoice(action.id, score, reasons))
        return self._choose_scored(decision, scores)

    async def choose_mulligan(self, decision: Decision) -> DecisionResult:
        hand_score, reasons = self._hand_score(decision.game.me.hand)
        keep = decision.first("keepHand")
        mulligan = decision.first("takeMulligan")
        scores = []
        if keep:
            scores.append(ScoredChoice(keep.id, hand_score, reasons))
        if mulligan:
            scores.append(
                ScoredChoice(
                    mulligan.id,
                    self.profile.keep_threshold,
                    (f"mulligan baseline={self.profile.keep_threshold:.2f}",),
                )
            )
        return self._choose_scored(decision, scores)

    def _keep_value(self, card: Card | None, game: Game) -> float:
        if card is None:
            return 0.0
        value = _card_value(card)
        lands = sum(candidate.is_type("Land") for candidate in game.me.hand)
        if card.is_type("Land"):
            value += 4.0 if lands <= self.profile.land_target else -2.0
        else:
            available_colors = {
                color
                for land in game.me.hand
                if land.is_type("Land")
                for color in _produced_colors(land)
            }
            if _required_colors(card).issubset(available_colors):
                value += 1.0
            value -= max(0, mana_value(card.mana_cost) - lands) * 0.5
        return value

    async def choose_mulligan_bottom(self, decision: Decision) -> DecisionResult:
        scores = [
            ScoredChoice(
                action.id,
                -self._keep_value(_card_from_action(decision, action), decision.game),
                ("bottom the lowest keep-value card",),
            )
            for action in decision.actions_of("bottomCard")
        ]
        return self._choose_scored(decision, scores)

    def _action_score(self, decision: Decision, action: Action) -> ScoredChoice:
        card = _card_from_action(decision, action)
        reasons: list[str] = []
        if action.kind == "passPriority":
            score = 0.0
            reasons.append("neutral priority pass")
            return ScoredChoice(action.id, score, tuple(reasons))
        if action.kind == "playLand":
            return ScoredChoice(
                action.id,
                self.profile.play_land_bonus,
                (f"land drop bonus={self.profile.play_land_bonus:.2f}",),
            )

        cost = len(set(action.payment_sources)) or mana_value(card.mana_cost if card else "")
        score = self.profile.spell_base_score
        score += (
            min(cost, decision.game.me.available_mana_count)
            * self.profile.mana_efficiency_weight
        )
        reasons.append(f"base and mana efficiency={score:.2f}")

        remaining = decision.game.me.available_mana_count - cost
        if remaining < self.profile.reserve_mana:
            penalty = (self.profile.reserve_mana - remaining) * self.profile.mana_efficiency_weight
            score -= penalty
            reasons.append(f"reserved mana penalty=-{penalty:.2f}")

        rules = _rules_text(card)
        is_removal = _contains_any(rules, REMOVAL_WORDS)
        if is_removal:
            score += self.profile.removal_weight
            reasons.append(f"removal={self.profile.removal_weight:.2f}")
        if _contains_any(rules, DRAW_WORDS):
            score += self.profile.card_draw_weight
            reasons.append(f"card draw={self.profile.card_draw_weight:.2f}")
        if _contains_any(rules, RAMP_WORDS):
            score += self.profile.ramp_weight
            reasons.append(f"ramp={self.profile.ramp_weight:.2f}")
        if _contains_any(rules, PROTECTION_WORDS):
            score += self.profile.protection_weight
            reasons.append(f"protection={self.profile.protection_weight:.2f}")

        opponent_ids = {player.id for player in decision.game.opponents}
        target_threat = sum(
            _threat_value(target)
            for target in decision.target_permanents(action)
            if target.controller in opponent_ids
        )
        if target_threat:
            score += target_threat
            reasons.append(f"opponent target threat={target_threat:.2f}")

        stack_is_live = bool(decision.game.stack)
        targets_stack = bool(action.target_stack_ids)
        targets_ours = any(
            target.controller == decision.player_id
            for target in decision.target_permanents(action)
        )
        if not decision.game.is_my_turn and (targets_stack or targets_ours or is_removal):
            score += self.profile.reaction_weight
            reasons.append(f"reaction timing={self.profile.reaction_weight:.2f}")
        elif not decision.game.is_my_turn and not stack_is_live:
            score -= self.profile.reaction_weight
            reasons.append(f"unnecessary response=-{self.profile.reaction_weight:.2f}")

        if decision.game.is_my_turn and decision.game.step in {"precombatMain", "postcombatMain"}:
            score += 1.0
            reasons.append("main-phase timing=1.00")
        return ScoredChoice(action.id, score, tuple(reasons))

    async def choose_priority(self, decision: Decision) -> DecisionResult:
        return self._choose_scored(
            decision,
            [self._action_score(decision, action) for action in decision.actions],
        )

    async def choose_discard(self, decision: Decision) -> DecisionResult:
        scores = [
            ScoredChoice(
                action.id,
                -self._keep_value(_card_from_action(decision, action), decision.game),
                ("discard the lowest keep-value card",),
            )
            for action in decision.actions_of("discard")
        ]
        return self._choose_scored(decision, scores)

    async def choose_attackers(self, decision: Decision) -> DecisionResult:
        scores: list[ScoredChoice] = []
        opposing_blockers = [
            card
            for opponent in decision.game.opponents
            for card in opponent.battlefield
            if card.is_type("Creature") and not card.tapped
        ]
        for action in decision.actions:
            if action.kind == "finishAttackers":
                scores.append(ScoredChoice(action.id, 0.0, ("finish attacks",)))
                continue
            attacker = decision.game.permanent(action.attacker_id)
            if attacker is None:
                scores.append(ScoredChoice(action.id, -50.0, ("unknown attacker",)))
                continue
            target_life = min(
                (
                    player.life
                    for player_id in action.target_player_ids
                    if (player := decision.game.player(player_id)) is not None
                ),
                default=min((opponent.life for opponent in decision.game.opponents), default=20),
            )
            lethal = attacker.power >= target_life
            dangerous_blockers = sum(
                blocker.power >= attacker.toughness for blocker in opposing_blockers
            )
            score = attacker.power * self.profile.aggression
            score += self.profile.lethal_bonus if lethal else 0.0
            score -= dangerous_blockers * self.profile.attack_risk_weight
            reasons = (
                f"pressure={attacker.power * self.profile.aggression:.2f}",
                f"lethal={self.profile.lethal_bonus if lethal else 0.0:.2f}",
                f"visible blocker risk={-dangerous_blockers * self.profile.attack_risk_weight:.2f}",
            )
            scores.append(ScoredChoice(action.id, score, reasons))
        return self._choose_scored(decision, scores)

    async def choose_blockers(self, decision: Decision) -> DecisionResult:
        scores: list[ScoredChoice] = []
        for action in decision.actions:
            if action.kind == "finishBlockers":
                scores.append(ScoredChoice(action.id, 0.0, ("accept incoming damage",)))
                continue
            blocker = decision.game.permanent(action.blocker_id)
            attacker = decision.game.permanent(action.attacker_id)
            if blocker is None or attacker is None:
                scores.append(ScoredChoice(action.id, -50.0, ("unknown combatant",)))
                continue
            kills = blocker.power >= attacker.toughness
            survives = attacker.power < blocker.toughness
            prevented = max(0, attacker.power) * self.profile.life_preservation_weight
            trade = (
                (self.profile.block_trade_weight if kills else 0.0)
                + (self.profile.block_trade_weight if survives else 0.0)
                - (0.0 if survives else _card_value(blocker))
            )
            scores.append(
                ScoredChoice(
                    action.id,
                    prevented + trade,
                    (
                        f"prevented damage={prevented:.2f}",
                        f"combat trade={trade:.2f}",
                    ),
                )
            )
        return self._choose_scored(decision, scores)

    async def choose_combat_damage(self, decision: Decision) -> DecisionResult:
        scores = []
        for action in decision.actions_of("assignCombatDamage"):
            decisions = action.raw.get("decisions", {})
            amount = (
                decisions.get("combatDamageAmount", 0)
                if isinstance(decisions, dict)
                else 0
            )
            amount = int(amount) if isinstance(amount, (int, float)) else 0
            blocker = decision.game.permanent(action.blocker_id)
            lethal = blocker is not None and amount >= max(1, blocker.toughness)
            score = float(amount) + (self.profile.block_trade_weight if lethal else 0.0)
            scores.append(
                ScoredChoice(
                    action.id,
                    score,
                    (f"assigned damage={amount}", f"lethal blocker damage={lethal}"),
                )
            )
        return self._choose_scored(decision, scores)

    async def choose_replacement(self, decision: Decision) -> DecisionResult:
        scores = []
        for action in decision.actions:
            score = 10.0 if action.kind == "moveCommanderToCommandZone" else 0.0
            scores.append(
                ScoredChoice(
                    action.id,
                    score,
                    ("preserve commander access" if score else "neutral replacement",),
                )
            )
        return self._choose_scored(decision, scores)

    def _candidate_card(self, decision: Decision, instance_id: str) -> Card | None:
        return decision.game.card(instance_id) or self._known_card_from_instance(instance_id)

    def _known_card_from_instance(self, instance_id: str) -> Card | None:
        parts = instance_id.split(":")
        definition_id = ":".join(parts[1:-1]) if len(parts) >= 3 else instance_id
        return self.known_cards.get(definition_id)

    async def choose_resolution(self, decision: Decision) -> DecisionResult:
        choice = decision.choice or {}
        kind = choice.get("kind")
        if kind == "numberSelection":
            value = int(choice.get("maximum", choice.get("minimum", 0)))
            self._record(
                decision,
                str(value),
                [ScoredChoice(str(value), float(value), ("prefer the largest legal amount",))],
            )
            return decision.choose_number(value)
        if kind == "cardSelection":
            candidates = [str(value) for value in choice.get("candidateCardInstanceIds", [])]
            minimum = int(choice.get("minimum", 0))
            maximum = int(choice.get("maximum", len(candidates)))
            prompt = str(choice.get("prompt", "")).casefold()
            harmful = any(word in prompt for word in ("discard", "sacrifice", "exile", "bottom"))
            scored = sorted(
                (
                    (
                        self._keep_value(
                            self._candidate_card(decision, item), decision.game
                        ),
                        item,
                    )
                    for item in candidates
                ),
                key=lambda pair: (pair[0], pair[1]) if harmful else (-pair[0], pair[1]),
            )
            count = minimum if harmful else maximum
            selected = [item for _, item in scored[:count]]
            self._record(
                decision,
                ",".join(selected),
                [ScoredChoice(item, score, ("card selection value",)) for score, item in scored],
            )
            return decision.choose_cards(*selected)
        if kind == "cardOrder":
            candidates = [str(value) for value in choice.get("cardInstanceIds", [])]
            ordered = sorted(
                candidates,
                key=lambda item: (
                    -self._keep_value(self._candidate_card(decision, item), decision.game),
                    item,
                ),
            )
            self._record(
                decision,
                ",".join(ordered),
                [
                    ScoredChoice(item, float(-index), ("higher-value card first",))
                    for index, item in enumerate(ordered)
                ],
            )
            return decision.choose_cards(*ordered)
        return self._choose_scored(
            decision,
            [self._action_score(decision, action) for action in decision.actions],
        )

    def _sideboard_value(self, card: Card | None, already_main: bool) -> float:
        if card is None:
            return -100.0
        score = _card_value(card)
        rules = _rules_text(card)
        if _contains_any(rules, REMOVAL_WORDS):
            score += min(self.opponent_traits["creatures"], 6.0) * 0.75
        if _contains_any(rules, GRAVEYARD_HATE_WORDS):
            score += min(self.opponent_traits["graveyard"], 10.0) * 0.5
        if already_main:
            score += self.profile.sideboard_change_penalty
        return score

    async def choose_sideboarding(self, decision: Decision) -> DecisionResult:
        choice = decision.choice or {}
        candidates = [str(value) for value in choice.get("candidateCardInstanceIds", [])]
        minimum = int(choice.get("minimum", 0))
        maximum = int(choice.get("maximum", len(candidates)))
        action = decision.first()
        action_decisions = action.raw.get("decisions", {}) if action else {}
        initial = (
            {str(value) for value in action_decisions.get("initialMainDeckIds", [])}
            if isinstance(action_decisions, dict)
            else set()
        )
        target_size = min(maximum, max(minimum, len(initial)))
        scored = [
            ScoredChoice(
                candidate,
                self._sideboard_value(
                    self._candidate_card(decision, candidate), candidate in initial
                ),
                ("matchup value plus main-deck stability",),
            )
            for candidate in candidates
        ]
        selected = [
            item.choice_id
            for item in sorted(scored, key=lambda item: (-item.score, item.choice_id))[:target_size]
        ]
        self._record(decision, ",".join(selected), scored)
        return decision.choose_cards(*selected)

    def trace_mapping(self) -> dict[str, object] | None:
        if self.last_trace is None:
            return None
        return {
            "requestId": self.last_trace.request_id,
            "decisionKind": self.last_trace.decision_kind,
            "selectedId": self.last_trace.selected_id,
            "choices": [
                {
                    "choiceId": choice.choice_id,
                    "score": choice.score,
                    "reasons": list(choice.reasons),
                }
                for choice in self.last_trace.choices
            ],
        }
