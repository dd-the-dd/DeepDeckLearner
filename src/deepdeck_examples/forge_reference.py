from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum

from deepdeck_agent import Action, Agent, Card, Decision, DecisionResponse, DecisionResult, Game

from .engine_targets import compatible_decision
from .strategic_graph import (
    STRATEGY_GRAPH_IMPLEMENTATION,
    ActionScore,
    ScoreContribution,
    StrategyPolicy,
    WeightedDecisionGraph,
    analyze_deck,
    available_colors,
    card_tags,
    mulligan_bottom_value,
    opening_hand_score,
    permanent_value,
)


class ForgeReferenceProfile(str, Enum):
    """Clean-room policy profiles for the Forge-inspired reference agent."""

    CAUTIOUS = "cautious"
    BALANCED = "balanced"
    AGGRESSIVE = "aggressive"


@dataclass(frozen=True)
class _Profile:
    attack_margin: int
    trade_bonus: int
    instant_threshold: int


_PROFILES = {
    ForgeReferenceProfile.CAUTIOUS: _Profile(
        attack_margin=2,
        trade_bonus=-15,
        instant_threshold=145,
    ),
    ForgeReferenceProfile.BALANCED: _Profile(
        attack_margin=0,
        trade_bonus=10,
        instant_threshold=115,
    ),
    ForgeReferenceProfile.AGGRESSIVE: _Profile(
        attack_margin=-2,
        trade_bonus=35,
        instant_threshold=90,
    ),
}

_PERMANENT_TYPES = ("Artifact", "Battle", "Creature", "Enchantment", "Planeswalker")
_REMOVAL_EFFECTS = (
    "destroyPermanent",
    "exilePermanent",
    "moveToGraveyard",
    "moveToExile",
    "returnToHand",
    "dealDamage",
)
_VALUE_EFFECTS = (
    "addCounter",
    "createToken",
    "drawCards",
    "gainLife",
    "searchLibrary",
)


def _mana_value(card: Card | None) -> int:
    if card is None:
        return 0
    result = 0
    for symbol in re.findall(r"\{([^}]+)\}", card.mana_cost):
        if symbol.isdigit():
            result += int(symbol)
        elif symbol.upper() not in {"X", "Y", "Z"}:
            result += 1
    return result


def _is_permanent(card: Card | None) -> bool:
    return card is not None and any(card.is_type(card_type) for card_type in _PERMANENT_TYPES)


def _card_value(card: Card | None) -> int:
    if card is None:
        return 0
    value = 35 + 8 * _mana_value(card)
    if card.is_type("Land"):
        value = 55
    if card.is_type("Creature"):
        value += 10 * max(card.power, 0) + 6 * max(card.toughness, 0)
    if card.is_type("Planeswalker"):
        value += 35
    if card.rules_contain(*_VALUE_EFFECTS):
        value += 25
    if card.rules_contain(*_REMOVAL_EFFECTS):
        value += 30
    return value


def _opponent_target_value(decision: Decision, action: Action) -> int:
    opponents = {player.id for player in decision.game.opponents}
    return sum(
        _card_value(permanent)
        for permanent in decision.target_permanents(action)
        if permanent.controller in opponents
    )


def _friendly_target_value(decision: Decision, action: Action) -> int:
    return sum(
        _card_value(permanent)
        for permanent in decision.target_permanents(action)
        if permanent.controller == decision.player_id
    )


class ForgeReferenceAgent(Agent):
    """A transparent, clean-room heuristic agent inspired by Forge's AI shape.

    No Forge source code is copied. DeepDeck's engine remains responsible for
    rules and legality; this agent only ranks the exact legal actions it receives.
    """

    def __init__(
        self,
        profile: ForgeReferenceProfile | str = ForgeReferenceProfile.BALANCED,
        *,
        weights: Mapping[str, float] | None = None,
        prefer_cards: Sequence[str] = (),
        avoid_cards: Sequence[str] = (),
        hold_for_interaction: Sequence[str] = (),
        strategy_graph: Mapping[str, object] | None = None,
    ):
        self.profile = ForgeReferenceProfile(profile)
        self._legacy_weights = dict(weights or {})
        self._strategy_graph_config = dict(strategy_graph or {})
        self._base_policy = StrategyPolicy.from_mapping(
            self._strategy_graph_config,
            profile=self.profile.value,
            legacy_weights=self._legacy_weights,
        )
        self._policies_by_player_id: dict[str, StrategyPolicy] = {}
        self._prefer_cards = {name.casefold() for name in prefer_cards}
        self._avoid_cards = {name.casefold() for name in avoid_cards}
        self._hold_for_interaction = {name.casefold() for name in hold_for_interaction}
        self.last_decision_explanation: dict[str, object] | None = None

    async def on_game_start(self, game: Game, known_deck: list[dict[str, object]]) -> None:
        policy = self._base_policy
        if known_deck:
            analysis = analyze_deck(known_deck, policy.patterns)
            policy = policy.with_detected_mix(analysis.archetype_mix)
        self._policies_by_player_id[game.player_id] = policy

    def _policy_for(self, decision: Decision) -> StrategyPolicy:
        return self._policies_by_player_id.get(decision.player_id, self._base_policy)

    async def make_decision(self, decision: Decision) -> DecisionResponse:
        return await super().make_decision(compatible_decision(decision))

    @staticmethod
    def _mulligan_count(decision: Decision) -> int:
        try:
            return max(0, int(decision.request_id.rsplit(":", 1)[-1]))
        except ValueError:
            return 0

    async def choose_mulligan(self, decision: Decision) -> DecisionResult:
        policy = self._policy_for(decision)
        mulligans_taken = self._mulligan_count(decision)
        free_mulligans = (
            1
            if str(decision.game.raw.get("gameMode", "")).casefold()
            in {"commander", "duelcommander"}
            else 0
        )
        score, contributions = opening_hand_score(
            decision.game.me.hand,
            policy,
        )
        graph = WeightedDecisionGraph(policy)
        custom: list[ScoreContribution] = []
        for action in decision.actions:
            if action.kind in {"keepHand", "takeMulligan"}:
                direction = 1 if action.kind == "keepHand" else -1
                custom.extend(
                    ScoreContribution(c.node, direction * c.raw, c.weight, c.reason)
                    for c in graph.custom_contributions(decision, action, contributions)
                )
        score += sum(c.score for c in custom)
        contributions = (*contributions, *custom)
        paid_mulligans = max(0, mulligans_taken - free_mulligans)
        threshold = max(8.0, 45.0 - 18.0 * paid_mulligans)
        # A four-card hand can still execute a coherent plan. Going lower is
        # almost never worth another attempt and made linear decks over-mulligan.
        keep = paid_mulligans >= 3 or score >= threshold
        preferred = "keepHand" if keep else "takeMulligan"
        self.last_decision_explanation = {
            "modelVersion": policy.version,
            "kind": "mulligan",
            "score": round(score, 3),
            "threshold": threshold,
            "choice": preferred,
            "contributions": [
                {
                    "node": item.node,
                    "score": round(item.score, 3),
                    "reason": item.reason,
                }
                for item in contributions
            ],
        }
        return decision.first(preferred) or self._safe_default(decision)

    async def choose_mulligan_bottom(self, decision: Decision) -> DecisionResult:
        policy = self._policy_for(decision)
        candidates = decision.actions_of("bottomCard")
        if not candidates:
            return self._safe_default(decision)
        graph = WeightedDecisionGraph(policy)
        ranked = [
            (
                mulligan_bottom_value(card, decision.game.me.hand, policy)
                if (card := decision.card_for(action)) is not None
                else 0.0,
                action,
            )
            for action in candidates
        ]
        ranked = [
            (value - sum(c.score for c in graph.custom_contributions(decision, action)), action)
            for value, action in ranked
        ]
        value, selected = min(ranked, key=lambda item: (item[0], item[1].id))
        self.last_decision_explanation = {
            "modelVersion": policy.version,
            "kind": "mulliganBottom",
            "choice": selected.id,
            "keepValue": round(value, 3),
            "contributions": [
                {"node": c.node, "score": round(c.score, 3), "reason": c.reason}
                for c in graph.custom_contributions(decision, selected)
            ],
        }
        return selected

    def _apply_preferences(self, decision: Decision, score: ActionScore) -> ActionScore:
        policy = self._policy_for(decision)
        action = next((item for item in decision.actions if item.id == score.action_id), None)
        source = decision.card_for(action) if action is not None else None
        if source is None:
            return score
        name = source.name.casefold()
        additions: list[ScoreContribution] = []
        if name in self._prefer_cards:
            additions.append(ScoreContribution("card_value", 90, 1.0, "User-preferred card."))
        if name in self._avoid_cards:
            additions.append(ScoreContribution("card_value", -130, 1.0, "User-avoided card."))
        if decision.game.is_my_turn and name in self._hold_for_interaction:
            additions.append(
                ScoreContribution(
                    "timing_patience",
                    -105,
                    policy.weight("timing_patience"),
                    "User asked to hold this card for interaction.",
                )
            )
        if not additions:
            return score
        return ActionScore(
            score.action_id,
            score.total + sum(item.score for item in additions),
            (*score.contributions, *additions),
            score.tags,
        )

    @staticmethod
    def _payment_cost(decision: Decision, action: Action) -> float:
        cost = 0.0
        for instance_id in set(action.payment_sources):
            source = decision.game.permanent(instance_id)
            if source is None:
                continue
            tags = card_tags(source)
            cost += permanent_value(source)
            cost += 4.0 * len(available_colors(source))
            if tags & {"interaction", "combo_enabler", "alternate_clock", "card_draw"}:
                cost += 24.0
        return cost

    @classmethod
    def _strategic_actions(cls, decision: Decision) -> list[Action]:
        actions = decision.actions
        if len(actions) <= 256:
            return actions
        # The Engine can expose thousands of payment-source permutations for one
        # spell/target/mode. This model does not value individual mana sources yet,
        # so score one stable representative per semantic action and mana count.
        representatives: dict[str, Action] = {}
        for action in actions:
            semantic = {
                key: value
                for key, value in action.raw.items()
                if key not in {"id", "label", "paymentSources"}
            }
            semantic["paymentCount"] = len(set(action.payment_sources))
            key = json.dumps(semantic, sort_keys=True, separators=(",", ":"))
            current = representatives.get(key)
            if current is None or (
                cls._payment_cost(decision, action),
                action.id,
            ) < (
                cls._payment_cost(decision, current),
                current.id,
            ):
                representatives[key] = action
        return list(representatives.values())

    def _choose_scored(
        self, decision: Decision, scores: Sequence[ActionScore], *, kind: str
    ) -> Action:
        policy = self._policy_for(decision)
        selected = WeightedDecisionGraph(policy).choose(decision, scores)
        ordered = sorted(scores, key=lambda value: (value.total, value.action_id), reverse=True)
        self.last_decision_explanation = {
            "modelVersion": policy.version,
            "kind": kind,
            "implementationRevision": STRATEGY_GRAPH_IMPLEMENTATION,
            "selected": selected.explanation(),
            "alternatives": [item.explanation() for item in ordered if item != selected][:3],
            "archetypeMix": dict(policy.archetype_mix),
            "temperature": policy.temperature,
        }
        return next(action for action in decision.actions if action.id == selected.action_id)

    async def choose_priority(self, decision: Decision) -> DecisionResult:
        graph = WeightedDecisionGraph(self._policy_for(decision))
        scores = [
            self._apply_preferences(decision, graph.score_priority(decision, action))
            for action in self._strategic_actions(decision)
        ]
        if not scores:
            return self._safe_default(decision)
        return self._choose_scored(decision, scores, kind="priority")

    async def choose_attackers(self, decision: Decision) -> DecisionResult:
        graph = WeightedDecisionGraph(self._policy_for(decision))
        scores = [graph.score_attack(decision, action) for action in decision.actions]
        if not scores:
            return self._safe_default(decision)
        return self._choose_scored(decision, scores, kind="attackers")

    async def choose_blockers(self, decision: Decision) -> DecisionResult:
        graph = WeightedDecisionGraph(self._policy_for(decision))
        scores = [graph.score_block(decision, action) for action in decision.actions]
        if not scores:
            return self._safe_default(decision)
        return self._choose_scored(decision, scores, kind="blockers")

    async def choose_resolution(self, decision: Decision) -> DecisionResult:
        choice = decision.choice or {}
        choice_kind = choice.get("kind")
        if choice_kind not in {"cardSelection", "cardOrder"}:
            return await super().choose_resolution(decision)
        raw_ids = choice.get(
            "candidateCardInstanceIds" if choice_kind == "cardSelection" else "cardInstanceIds",
            [],
        )
        candidate_ids = [str(value) for value in raw_ids] if isinstance(raw_ids, list) else []
        cards = [
            (instance_id, card)
            for instance_id in candidate_ids
            if (card := decision.game.card(instance_id)) is not None
        ]
        if len(cards) != len(candidate_ids):
            return await super().choose_resolution(decision)
        policy = self._policy_for(decision)
        ranked = sorted(
            cards,
            key=lambda item: (
                mulligan_bottom_value(item[1], decision.game.me.hand, policy),
                item[0],
            ),
        )
        if choice_kind == "cardOrder":
            selected = [instance_id for instance_id, _ in reversed(ranked)]
        else:
            minimum = max(0, int(choice.get("minimum", 0)))
            maximum = max(minimum, int(choice.get("maximum", len(ranked))))
            context = f"{decision.request_id} {choice}".casefold()
            low_value_destination = any(
                marker in context
                for marker in (
                    "discard",
                    "cardsforlibrary",
                    "bottom",
                    "sacrifice",
                    "surveilmove",
                )
            )
            if "surveilmove" in context and policy.archetype_mix.get("combo", 0.0) > 0.35:
                graveyard_plan = [
                    item
                    for item in reversed(ranked)
                    if "combo_payoff" in card_tags(item[1], policy.patterns)
                ]
                remainder = [item for item in ranked if item not in graveyard_plan]
                ordered = [*graveyard_plan, *remainder]
                count = min(maximum, max(minimum, len(graveyard_plan)))
            else:
                ordered = ranked if low_value_destination else list(reversed(ranked))
                count = minimum if low_value_destination else maximum
            selected = [instance_id for instance_id, _ in ordered[:count]]
        self.last_decision_explanation = {
            "modelVersion": policy.version,
            "kind": "resolutionChoice",
            "choice": choice_kind,
            "selectedCardIds": selected,
            "selectionContext": decision.request_id,
        }
        return decision.choose_cards(*selected)

    async def choose_sideboarding(self, decision: Decision) -> DecisionResult:
        choice = decision.choice or {}
        if choice.get("kind") != "cardSelection":
            return await super().choose_sideboarding(decision)
        candidates = list(
            dict.fromkeys(str(value) for value in choice.get("candidateCardInstanceIds", []))
        )
        minimum = max(0, int(choice.get("minimum", 0)))
        maximum = min(len(candidates), int(choice.get("maximum", len(candidates))))
        selected: list[str] = []
        for action in decision.actions:
            hints = action.raw.get("decisions", {})
            initial = hints.get("initialMainDeckIds") if isinstance(hints, dict) else None
            if isinstance(initial, list):
                proposed = list(dict.fromkeys(str(value) for value in initial))
                if minimum <= len(proposed) <= maximum and all(
                    value in candidates for value in proposed
                ):
                    selected = proposed
                    break
        keeps_main = bool(selected) or minimum == 0
        if not keeps_main:
            # Older/custom engines may omit the current-main hint. Still send
            # a valid typed selection, never a bare confirm action that causes
            # Engine to silently substitute its own decision.
            selected = candidates[:minimum]
        self.last_decision_explanation = {
            "modelVersion": self._policy_for(decision).version,
            "handlerVersion": "sideboard-selection/v1",
            "kind": "sideboarding",
            "choice": "keep-current-main" if keeps_main else "limited-information-selection",
            "selectedCardIds": selected,
            "matchupAdaptation": False,
        }
        return decision.choose_cards(*selected)

    async def choose_discard(self, decision: Decision) -> DecisionResult:
        policy = self._policy_for(decision)
        discards = decision.actions_of("discard")
        if not discards:
            return self._safe_default(decision)
        ranked = [
            (
                mulligan_bottom_value(card, decision.game.me.hand, policy)
                if (card := decision.card_for(action)) is not None
                else 0.0,
                action,
            )
            for action in discards
        ]
        value, selected = min(ranked, key=lambda item: (item[0], item[1].id))
        self.last_decision_explanation = {
            "modelVersion": policy.version,
            "kind": "discard",
            "choice": selected.id,
            "keepValue": round(value, 3),
        }
        return selected


def build_forge_reference_agent(
    profile: ForgeReferenceProfile | str = ForgeReferenceProfile.BALANCED,
    *,
    weights: Mapping[str, float] | None = None,
    prefer_cards: Sequence[str] = (),
    avoid_cards: Sequence[str] = (),
    hold_for_interaction: Sequence[str] = (),
    strategy_graph: Mapping[str, object] | None = None,
) -> ForgeReferenceAgent:
    return ForgeReferenceAgent(
        profile,
        weights=weights,
        prefer_cards=prefer_cards,
        avoid_cards=avoid_cards,
        hold_for_interaction=hold_for_interaction,
        strategy_graph=strategy_graph,
    )
