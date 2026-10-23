from __future__ import annotations

import hashlib
import json
import math
import random
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from itertools import combinations
from typing import Any

from deepdeck_agent import Action, Card, Decision, Game

from .decision_nodes import ACTION_KINDS, SIGNALS, evaluate_nodes, normalize_custom_nodes
from .engine_targets import compatible_action, target_references

STRATEGY_GRAPH_VERSION = "strategy-graph/v1"
STRATEGY_GRAPH_IMPLEMENTATION = "combat-value/2026-10-21"
ARCHETYPES = ("aggro", "tempo", "midrange", "control", "combo")


@dataclass(frozen=True)
class StrategyNode:
    id: str
    name: str
    description: str
    default: float = 1.0


STRATEGY_NODES = (
    StrategyNode("card_value", "Card quality", "Preserve and deploy intrinsically useful cards."),
    StrategyNode("mana_efficiency", "Mana efficiency", "Use mana without wasting key resources."),
    StrategyNode("board_development", "Board development", "Add relevant permanents to the board."),
    StrategyNode(
        "interaction", "General interaction", "Answer opposing threats and stack objects."
    ),
    StrategyNode("combat_pressure", "Combat pressure", "Advance the combat-damage clock."),
    StrategyNode("combat_survival", "Combat survival", "Preserve life and valuable blockers."),
    StrategyNode("card_advantage", "Card advantage", "Gain access to more useful cards."),
    StrategyNode("card_selection", "Card selection", "Improve draw quality and find key pieces."),
    StrategyNode(
        "clock_pressure", "Advance our clock", "Shorten the number of turns until victory."
    ),
    StrategyNode("clock_disruption", "Disrupt opposing clocks", "Delay credible paths to defeat."),
    StrategyNode(
        "combo_progress", "Assemble our plan", "Find and deploy synergistic combo pieces."
    ),
    StrategyNode("combo_disruption", "Remove combo pieces", "Prioritize enablers and payoffs."),
    StrategyNode(
        "protect_advantage", "Protect an advantage", "Keep a favorable board or clock intact."
    ),
    StrategyNode(
        "timing_patience", "Timing and patience", "Wait for a safer, more informative window."
    ),
    StrategyNode(
        "resource_preservation", "Preserve resources", "Avoid spending cards and mana early."
    ),
    StrategyNode(
        "mulligan_plan", "Functional opening hand", "Keep hands that execute the deck plan."
    ),
)

NODE_IDS = frozenset(node.id for node in STRATEGY_NODES)

ARCHETYPE_WEIGHTS: dict[str, dict[str, float]] = {
    "aggro": {
        "card_value": 0.75,
        "mana_efficiency": 1.45,
        "board_development": 1.65,
        "interaction": 0.65,
        "combat_pressure": 1.75,
        "combat_survival": 0.55,
        "card_advantage": 0.55,
        "card_selection": 0.85,
        "clock_pressure": 1.8,
        "clock_disruption": 0.7,
        "combo_progress": 0.5,
        "combo_disruption": 0.7,
        "protect_advantage": 0.9,
        "timing_patience": 0.55,
        "resource_preservation": 0.55,
        "mulligan_plan": 1.4,
    },
    "tempo": {
        "card_value": 0.95,
        "mana_efficiency": 1.5,
        "board_development": 1.15,
        "interaction": 1.35,
        "combat_pressure": 1.4,
        "combat_survival": 0.75,
        "card_advantage": 0.8,
        "card_selection": 1.35,
        "clock_pressure": 1.45,
        "clock_disruption": 1.25,
        "combo_progress": 0.65,
        "combo_disruption": 1.3,
        "protect_advantage": 1.55,
        "timing_patience": 1.35,
        "resource_preservation": 1.2,
        "mulligan_plan": 1.35,
    },
    "midrange": {
        "card_value": 1.35,
        "mana_efficiency": 1.1,
        "board_development": 1.25,
        "interaction": 1.25,
        "combat_pressure": 1.05,
        "combat_survival": 1.1,
        "card_advantage": 1.2,
        "card_selection": 0.95,
        "clock_pressure": 1.0,
        "clock_disruption": 1.2,
        "combo_progress": 0.65,
        "combo_disruption": 1.15,
        "protect_advantage": 1.15,
        "timing_patience": 1.05,
        "resource_preservation": 1.1,
        "mulligan_plan": 1.0,
    },
    "control": {
        "card_value": 1.4,
        "mana_efficiency": 0.9,
        "board_development": 0.65,
        "interaction": 1.55,
        "combat_pressure": 0.55,
        "combat_survival": 1.5,
        "card_advantage": 1.65,
        "card_selection": 1.15,
        "clock_pressure": 0.6,
        "clock_disruption": 1.6,
        "combo_progress": 0.55,
        "combo_disruption": 1.45,
        "protect_advantage": 1.35,
        "timing_patience": 1.65,
        "resource_preservation": 1.55,
        "mulligan_plan": 0.85,
    },
    "combo": {
        "card_value": 0.85,
        "mana_efficiency": 1.2,
        "board_development": 0.55,
        "interaction": 0.8,
        "combat_pressure": 0.55,
        "combat_survival": 0.65,
        "card_advantage": 1.05,
        "card_selection": 1.65,
        "clock_pressure": 1.45,
        "clock_disruption": 0.75,
        "combo_progress": 1.9,
        "combo_disruption": 0.8,
        "protect_advantage": 1.4,
        "timing_patience": 1.15,
        "resource_preservation": 1.3,
        "mulligan_plan": 1.75,
    },
}

PROFILE_MIXES: dict[str, dict[str, float]] = {
    "cautious": {"aggro": 0.05, "tempo": 0.15, "midrange": 0.25, "control": 0.5, "combo": 0.05},
    "balanced": {"aggro": 0.15, "tempo": 0.2, "midrange": 0.35, "control": 0.2, "combo": 0.1},
    "aggressive": {"aggro": 0.55, "tempo": 0.25, "midrange": 0.1, "control": 0.03, "combo": 0.07},
}


def _bounded_number(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return min(maximum, max(minimum, number))


def _normalize_mix(
    values: Mapping[str, Any] | None, fallback: Mapping[str, float]
) -> dict[str, float]:
    raw = {
        archetype: max(0.0, _bounded_number((values or {}).get(archetype), 0.0, 0.0, 1.0))
        for archetype in ARCHETYPES
    }
    total = sum(raw.values())
    if total <= 0:
        return dict(fallback)
    return {key: value / total for key, value in raw.items()}


def _walk_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for child in value.values():
            yield from _walk_strings(child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for child in value:
            yield from _walk_strings(child)


def _definition(value: Card | Mapping[str, Any]) -> Mapping[str, Any]:
    if isinstance(value, Card):
        return value.definition
    candidate = value.get("definition", value)
    return candidate if isinstance(candidate, Mapping) else value


def card_name(value: Card | Mapping[str, Any]) -> str:
    if isinstance(value, Card):
        return value.name
    definition = _definition(value)
    return str(definition.get("name") or value.get("name") or "Unknown card")


def card_type_line(value: Card | Mapping[str, Any]) -> str:
    if isinstance(value, Card):
        return value.type_line
    definition = _definition(value)
    return str(definition.get("typeLine") or value.get("typeLine") or "")


def card_mana_cost(value: Card | Mapping[str, Any]) -> str:
    if isinstance(value, Card):
        return value.mana_cost
    definition = _definition(value)
    return str(definition.get("manaCost") or value.get("manaCost") or "")


def mana_value(value: Card | Mapping[str, Any] | None) -> int:
    if value is None:
        return 0
    definition = _definition(value)
    explicit = definition.get("manaValue")
    if explicit is None and isinstance(value, Mapping):
        explicit = value.get("manaValue")
    if isinstance(explicit, (int, float)):
        return max(0, int(explicit))
    result = 0
    for symbol in re.findall(r"\{([^}]+)\}", card_mana_cost(value)):
        if symbol.isdigit():
            result += int(symbol)
        elif symbol.upper() not in {"X", "Y", "Z"}:
            hybrid_numbers = [int(part) for part in symbol.split("/") if part.isdigit()]
            result += max(hybrid_numbers, default=1)
    return result


def required_colors(value: Card | Mapping[str, Any]) -> frozenset[str]:
    colors: set[str] = set()
    for symbol in re.findall(r"\{([^}]+)\}", card_mana_cost(value).upper()):
        colors.update(part for part in symbol.split("/") if part in {"W", "U", "B", "R", "G"})
    return frozenset(colors)


def available_colors(value: Card | Mapping[str, Any]) -> frozenset[str]:
    if "land" not in card_type_line(value).casefold():
        return frozenset()
    text = card_text(value)
    colors: set[str] = set()
    basic_types = {
        "plains": "W",
        "island": "U",
        "swamp": "B",
        "mountain": "R",
        "forest": "G",
    }
    for basic_type, color in basic_types.items():
        if (
            re.search(rf"\b{basic_type}\b", card_type_line(value).casefold())
            or card_name(value).casefold() == basic_type
        ):
            colors.add(color)
    colors.update(produced_colors(value))
    if "search" in text and "library" in text:
        # A typed fetch has access only to its named land subtypes. A utility
        # land's colored activation cost is not a color it can produce.
        for basic_type, color in basic_types.items():
            if re.search(rf"\b{basic_type}\b", text):
                colors.add(color)
    if "search" in text and "basic land card" in text:
        colors.update(("W", "U", "B", "R", "G"))
    return frozenset(colors)


def produced_colors(value: Card | Mapping[str, Any]) -> frozenset[str]:
    definition = _definition(value)
    rules = value.rules if isinstance(value, Card) else definition.get("rules", [])
    fragments: list[str] = []
    found = False

    def visit(item: Any) -> None:
        nonlocal found
        if isinstance(item, Mapping):
            if str(item.get("kind", "")).casefold() == "addmana":
                found = True
                fragments.extend(_walk_strings(item.get("mana", item.get("colors", ""))))
            else:
                for child in item.values():
                    visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(rules)
    text = " ".join(fragments).upper()
    colors = set(re.findall(r"\{([WUBRG])\}", text))
    colors.update(fragment for fragment in fragments if fragment in {"W", "U", "B", "R", "G"})
    if not found:
        for addition in re.findall(r"\badd\s+((?:\{[^}]+\}\s*)+)", card_text(value)):
            colors.update(re.findall(r"\{([WUBRG])\}", addition.upper()))
    if "ANYCOLOR" in text.replace(" ", "") or "mana of any color" in card_text(value):
        colors.update("WUBRG")
    return frozenset(colors)


def land_mana_output(card: Card) -> int:
    """Static output, not colored activation costs elsewhere on the card."""
    output = 0

    def visit(item: Any) -> None:
        nonlocal output
        if isinstance(item, Mapping):
            if str(item.get("kind", "")).casefold() == "addmana":
                mana = item.get("mana")
                if isinstance(mana, str):
                    output = max(output, len(re.findall(r"\{[^}]+\}", mana)))
            else:
                for child in item.values():
                    visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(card.rules)
    for mana in re.findall(r"\badd\s+((?:\{[^}]+\}\s*)+)", card_text(card)):
        output = max(output, len(re.findall(r"\{[^}]+\}", mana)))
    return output


def card_text(value: Card | Mapping[str, Any]) -> str:
    definition = _definition(value)
    pieces = [card_name(value), card_type_line(value), str(definition.get("oracleText", ""))]
    if isinstance(value, Mapping):
        pieces.append(str(value.get("oracleText", "")))
    rules = (
        value.rules if isinstance(value, Card) else definition.get("rules", value.get("rules", []))
    )
    pieces.extend(_walk_strings(rules))
    return " ".join(piece for piece in pieces if piece).casefold()


@dataclass(frozen=True)
class PolicyPattern:
    id: str
    card_names: tuple[str, ...] = ()
    text_contains: tuple[str, ...] = ()
    require_tags: tuple[str, ...] = ()
    add_tags: tuple[str, ...] = ()
    node_adjustments: Mapping[str, float] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any], index: int = 0) -> PolicyPattern:
        def strings(key: str, maximum: int = 50) -> tuple[str, ...]:
            value = raw.get(key, [])
            if not isinstance(value, list):
                return ()
            return tuple(str(item).strip()[:160] for item in value[:maximum] if str(item).strip())

        adjustments = raw.get("nodeAdjustments", {})
        if not isinstance(adjustments, Mapping):
            adjustments = {}
        return cls(
            id=str(raw.get("id", f"pattern-{index + 1}"))[:80],
            card_names=strings("cardNames"),
            text_contains=strings("textContains", 20),
            require_tags=strings("requireTags", 20),
            add_tags=strings("addTags", 20),
            node_adjustments={
                str(key): _bounded_number(value, 0.0, -2.0, 2.0)
                for key, value in adjustments.items()
                if str(key) in NODE_IDS
            },
        )

    def matches(self, value: Card | Mapping[str, Any], tags: set[str]) -> bool:
        name = card_name(value).casefold()
        text = card_text(value)
        return (
            (not self.card_names or name in {item.casefold() for item in self.card_names})
            and (
                not self.text_contains
                or any(item.casefold() in text for item in self.text_contains)
            )
            and (not self.require_tags or set(self.require_tags) <= tags)
        )


@dataclass(frozen=True)
class StrategyPolicy:
    version: str = STRATEGY_GRAPH_VERSION
    archetype_mix: Mapping[str, float] = field(default_factory=lambda: PROFILE_MIXES["balanced"])
    weights: Mapping[str, float] = field(default_factory=dict)
    deck_adaptation: float = 0.65
    temperature: float = 0.0
    seed: int = 0
    patterns: tuple[PolicyPattern, ...] = ()
    custom_nodes: tuple[Mapping[str, Any], ...] = ()

    @classmethod
    def from_mapping(
        cls,
        raw: Mapping[str, Any] | None,
        *,
        profile: str = "balanced",
        legacy_weights: Mapping[str, Any] | None = None,
    ) -> StrategyPolicy:
        value = raw or {}
        fallback = PROFILE_MIXES.get(profile, PROFILE_MIXES["balanced"])
        weight_values = value.get("weights", {})
        if not isinstance(weight_values, Mapping):
            weight_values = {}
        combined = dict(legacy_weights or {})
        combined.update(weight_values)
        patterns = value.get("patterns", [])
        if not isinstance(patterns, list):
            patterns = []
        return cls(
            version=str(value.get("version", STRATEGY_GRAPH_VERSION)),
            archetype_mix=_normalize_mix(
                value.get("archetypeMix")
                if isinstance(value.get("archetypeMix"), Mapping)
                else None,
                fallback,
            ),
            weights={
                str(key): _bounded_number(item, 1.0, 0.0, 2.0)
                for key, item in combined.items()
                if str(key) in NODE_IDS
            },
            deck_adaptation=_bounded_number(value.get("deckAdaptation"), 0.65, 0.0, 1.0),
            temperature=_bounded_number(value.get("temperature"), 0.0, 0.0, 1.0),
            seed=int(_bounded_number(value.get("seed"), 0.0, 0.0, 2**31 - 1)),
            patterns=tuple(
                PolicyPattern.from_mapping(item, index)
                for index, item in enumerate(patterns[:100])
                if isinstance(item, Mapping)
            ),
            custom_nodes=tuple(normalize_custom_nodes(value.get("customNodes", []), NODE_IDS)),
        )

    def with_detected_mix(self, mix: Mapping[str, float]) -> StrategyPolicy:
        explicit = self.archetype_mix
        adaptation = self.deck_adaptation
        blended = {
            key: (1 - adaptation) * explicit.get(key, 0.0) + adaptation * mix.get(key, 0.0)
            for key in ARCHETYPES
        }
        return StrategyPolicy(
            version=self.version,
            archetype_mix=_normalize_mix(blended, PROFILE_MIXES["balanced"]),
            weights=self.weights,
            deck_adaptation=self.deck_adaptation,
            temperature=self.temperature,
            seed=self.seed,
            patterns=self.patterns,
            custom_nodes=self.custom_nodes,
        )

    def weight(self, node_id: str) -> float:
        archetype_weight = sum(
            self.archetype_mix.get(archetype, 0.0) * ARCHETYPE_WEIGHTS[archetype].get(node_id, 1.0)
            for archetype in ARCHETYPES
        )
        # User values are multipliers: 1 keeps the reusable archetype baseline,
        # 0 disables a concern, and 2 doubles its importance.
        return archetype_weight * self.weights.get(node_id, 1.0)


def _base_card_tags(value: Card | Mapping[str, Any]) -> set[str]:
    type_line = card_type_line(value).casefold()
    text = card_text(value)
    tags: set[str] = set()
    for card_type in (
        "land",
        "creature",
        "artifact",
        "enchantment",
        "planeswalker",
        "instant",
        "sorcery",
        "battle",
    ):
        if card_type in type_line:
            tags.add(card_type)
    if tags & {"creature", "planeswalker", "battle"}:
        tags.add("threat")
    if tags & {"artifact", "creature", "enchantment", "planeswalker", "battle"}:
        tags.add("permanent")
    if tags & {"instant", "sorcery"}:
        tags.add("spell")

    if any(
        token in text
        for token in ("drawcard", "drawcards", "draw a card", "draw two", "draw three")
    ):
        tags.add("card_draw")
    if any(token in text for token in ("scry", "surveil", "look at the top", "cardselection")):
        tags.add("card_selection")
    if "search" in text and "library" in text:
        tags.update(("tutor", "card_selection"))
    if any(
        token in text
        for token in (
            "destroypermanent",
            "exilepermanent",
            "destroy target",
            "exile target",
            "return target",
        )
    ):
        tags.add("removal")
    if any(token in text for token in ("counter target", "counterspell", "counterstackobject")):
        tags.update(("counterspell", "protection"))
    if "discard" in text:
        tags.add("discard")
    if any(token in text for token in ("all creatures", "each creature", "destroyall", "exileall")):
        tags.add("sweeper")
    if any(token in text for token in ("mill", "put the top", "cards of their library")):
        tags.update(("mill", "alternate_clock"))
    if any(token in text for token in ("deal damage", "dealdamage", "loses life", "loselife")):
        tags.add("burn")
    if any(token in text for token in ("gain life", "gainlife", "lifelink")):
        tags.add("life_gain")
    if any(
        token in text for token in ("indestructible", "hexproof", "protection from", "phase out")
    ):
        tags.add("protection")
    if any(token in text for token in ("create token", "createtoken")):
        tags.add("token_maker")
    if any(
        token in text
        for token in (
            "flying",
            "menace",
            "unblockable",
            "can't be blocked",
            "cantbeblocked",
            "trample",
        )
    ):
        tags.add("evasion")
    for keyword in (
        "haste",
        "vigilance",
        "deathtouch",
        "lifelink",
        "first strike",
        "double strike",
    ):
        if keyword in text:
            tags.add(keyword.replace(" ", "_"))
    if any(
        token in text
        for token in (
            "from a graveyard onto the battlefield",
            "from your graveyard to the battlefield",
            "reanimate",
        )
    ):
        tags.update(("reanimate", "combo_enabler"))
    if any(
        token in text
        for token in (
            "put an artifact, creature, enchantment, or land card",
            "without paying its mana cost",
            "becomes a copy of target land",
        )
    ):
        tags.add("combo_enabler")
    if "you win the game" in text or "loses the game" in text:
        tags.update(("alternate_clock", "combo_payoff"))
    if "addmana" in text or re.search(r"add \{[^}]+\}.*\{[^}]+\}", text):
        tags.add("mana_producer")
        if "land" not in tags:
            tags.add("fast_mana")
    if any(
        token in text
        for token in (
            "play an additional land",
            "put a land card",
            "add two mana",
            "add three mana",
        )
    ):
        tags.add("ramp")
    if any(
        token in text
        for token in (
            "can't cast",
            "costs {1} more",
            "cost more to cast",
            "enters the battlefield tapped",
        )
    ):
        tags.add("tax")
    if mana_value(value) >= 6 and "creature" in tags:
        tags.add("combo_payoff")
    if tags & {"removal", "counterspell", "discard", "tax"}:
        tags.add("interaction")
    if tags & {"card_draw", "card_selection"} and mana_value(value) <= 1:
        tags.add("cantrip")
    return tags


def card_tags(
    value: Card | Mapping[str, Any], patterns: Sequence[PolicyPattern] = ()
) -> frozenset[str]:
    tags = _base_card_tags(value)
    # Patterns can depend on built-in tags; iterate once more for derived custom tags.
    for _ in range(2):
        for pattern in patterns:
            if pattern.matches(value, tags):
                tags.update(pattern.add_tags)
    return frozenset(tags)


def _quantity(card: Mapping[str, Any]) -> int:
    value = card.get("quantity", 1)
    return max(1, int(value)) if isinstance(value, (int, float)) else 1


@dataclass(frozen=True)
class DeckAnalysis:
    primary_archetype: str
    archetype_mix: Mapping[str, float]
    average_mana_value: float
    land_ratio: float
    card_tags: Mapping[str, frozenset[str]]
    role_counts: Mapping[str, int]


def analyze_deck(
    known_deck: Sequence[Mapping[str, Any]], patterns: Sequence[PolicyPattern] = ()
) -> DeckAnalysis:
    main = [card for card in known_deck if str(card.get("section", "main")) != "sideboard"]
    total = sum(_quantity(card) for card in main) or 1
    tag_map = {card_name(card): card_tags(card, patterns) for card in main}
    counts: dict[str, int] = {}
    nonland_value = 0
    nonlands = 0
    cheap_creatures = 0
    creatures = 0
    interaction = 0
    selection = 0
    draw = 0
    combo = 0
    fast_mana = 0
    lands = 0
    for card in main:
        quantity = _quantity(card)
        tags = tag_map[card_name(card)]
        for tag in tags:
            counts[tag] = counts.get(tag, 0) + quantity
        if "land" in tags:
            lands += quantity
            continue
        nonlands += quantity
        value = mana_value(card)
        nonland_value += value * quantity
        creatures += quantity if "creature" in tags else 0
        cheap_creatures += quantity if "creature" in tags and value <= 2 else 0
        interaction += quantity if "interaction" in tags else 0
        selection += quantity if tags & {"card_selection", "tutor", "cantrip"} else 0
        draw += quantity if "card_draw" in tags else 0
        combo += quantity if tags & {"combo_enabler", "combo_payoff", "alternate_clock"} else 0
        fast_mana += quantity if "fast_mana" in tags else 0

    spell_total = max(1, nonlands)
    average_mv = nonland_value / spell_total
    creature_density = creatures / spell_total
    cheap_threat_density = cheap_creatures / spell_total
    interaction_density = interaction / spell_total
    selection_density = selection / spell_total
    draw_density = draw / spell_total
    combo_density = combo / spell_total
    fast_mana_density = fast_mana / spell_total
    land_ratio = lands / total

    raw_scores = {
        "aggro": (
            0.25
            + 2.4 * cheap_threat_density
            + 0.8 * creature_density
            + 0.7 * counts.get("burn", 0) / spell_total
            - 0.22 * max(0.0, average_mv - 2.2)
        ),
        "tempo": (
            0.2
            + 1.6 * cheap_threat_density
            + 1.25 * interaction_density
            + 1.25 * selection_density
            + 0.35 * counts.get("evasion", 0) / spell_total
        ),
        "midrange": (
            0.45
            + 0.9 * creature_density
            + 1.0 * interaction_density
            + 0.4 * draw_density
            - 0.18 * abs(average_mv - 3.0)
            - 0.8 * combo_density
        ),
        "control": (
            0.2
            + 1.6 * interaction_density
            + 1.4 * draw_density
            + 0.7 * counts.get("sweeper", 0) / spell_total
            + 0.25 * max(0.0, land_ratio - 0.34) * 10
            - 0.8 * cheap_threat_density
        ),
        "combo": (
            0.15
            + 3.2 * combo_density
            + 1.0 * selection_density
            + 0.8 * fast_mana_density
            + 0.6 * counts.get("tutor", 0) / spell_total
            - 0.35 * interaction_density
        ),
    }
    # Softmax yields a useful blend while still giving the strongest plan a clear lead.
    peak = max(raw_scores.values())
    exponentials = {key: math.exp((value - peak) * 2.2) for key, value in raw_scores.items()}
    denominator = sum(exponentials.values()) or 1.0
    mix = {key: value / denominator for key, value in exponentials.items()}
    primary = max(mix, key=mix.get)  # type: ignore[arg-type]
    return DeckAnalysis(primary, mix, average_mv, land_ratio, tag_map, counts)


@dataclass(frozen=True)
class Clock:
    owner: str
    kind: str
    turns: int
    confidence: float
    sources: tuple[str, ...] = ()


@dataclass(frozen=True)
class BoardAssessment:
    role: str
    material_advantage: float
    tempo_advantage: float
    our_clock: Clock
    opposing_clock: Clock
    needs: Mapping[str, float]


def _creature_value(card: Card) -> float:
    value = 12.0 + max(0, card.power) * 9.0 + max(0, card.toughness) * 5.0
    tags = card_tags(card)
    value += 12.0 if "evasion" in tags else 0.0
    value += 8.0 if tags & {"lifelink", "deathtouch", "first_strike", "double_strike"} else 0.0
    value += 10.0 if tags & {"card_draw", "token_maker", "tax"} else 0.0
    return value


def permanent_value(card: Card) -> float:
    tags = card_tags(card)
    if "creature" in tags:
        return _creature_value(card)
    if "planeswalker" in tags:
        return 58.0 + 6.0 * mana_value(card)
    if "land" in tags:
        return 12.0 + (10.0 if tags & {"combo_enabler", "alternate_clock"} else 0.0)
    value = 18.0 + 7.0 * mana_value(card)
    if tags & {"tax", "card_draw", "combo_enabler", "alternate_clock"}:
        value += 25.0
    return value


def combat_keywords(card: Card) -> frozenset[str]:
    """Abilities the creature HAS, not abilities mentioned in an effect's text.

    Deck-role tags deliberately search broadly. Combat cannot: Guide of Souls
    mentions a flying counter but does not fly until that counter is actually on it.
    Read only top-level keyword rules, keyword counters and standalone oracle lines.
    Conditional/static grants remain Engine territory, not optimistic text guesses.
    """

    def key(value: Any) -> str:
        return re.sub(r"[^a-z]", "", str(value).casefold())

    result = set()
    for rule in card.rules:
        if isinstance(rule, Mapping) and rule.get("kind") == "keywordAbility":
            ability = rule.get("ability", {})
            if isinstance(ability, Mapping):
                result.add(key(ability.get("kind", "")))
    known = {
        "flying",
        "reach",
        "trample",
        "menace",
        "vigilance",
        "deathtouch",
        "lifelink",
        "firststrike",
        "doublestrike",
        "indestructible",
        "shadow",
        "cantbeblocked",
        "cantblock",
        "defender",
        "haste",
        "horsemanship",
    }
    counters = card.raw.get("counters", {})
    if isinstance(counters, Mapping):
        result.update(
            key(name)
            for name, count in counters.items()
            if isinstance(count, (int, float)) and count > 0 and key(name) in known
        )
    # Some imported cards contain only oracle text or simple {text: ...} rules.
    texts = [str(card.definition.get("oracleText", ""))]
    texts.extend(str(rule.get("text", "")) for rule in card.rules if isinstance(rule, Mapping))
    for text in texts:
        for line in re.split(r"[\n,]", text):
            keyword = key(re.sub(r"\([^)]*\)", "", line).strip().rstrip("."))
            if keyword in known:
                result.add(keyword)
    return frozenset(result - {""})


def _can_combat_block(blocker: Card, attacker: Card) -> bool:
    attack = combat_keywords(attacker)
    block = combat_keywords(blocker)
    if blocker.tapped or not blocker.is_type("Creature") or "cantblock" in block:
        return False
    if "cantbeblocked" in attack:
        return False
    if "flying" in attack and not block & {"flying", "reach"}:
        return False
    if ("shadow" in attack) != ("shadow" in block):
        return False
    if "horsemanship" in attack and "horsemanship" not in block:
        return False
    return not ("canblockonlyflying" in block and "flying" not in attack)


def _damage_marked(card: Card) -> int:
    value = card.raw.get("damageMarked", 0)
    return max(0, int(value)) if isinstance(value, (int, float)) else 0


def _combat_exchange(attacker: Card, blockers: Sequence[Card]) -> tuple[float, int, bool]:
    """Small deterministic first-strike/normal-strike exchange; no imagined trick.

    Returns opposing material lost minus ours, player damage, attacker survived.
    Damage assignments favor the attacker; block sets favor the defender below.
    This is a heuristic, not an alternative rules engine.
    """
    attack = combat_keywords(attacker)
    block_tags = {card.id: combat_keywords(card) for card in blockers}
    alive = list(blockers)
    marked = {card.id: _damage_marked(card) for card in blockers}
    attacker_damage = _damage_marked(attacker)
    attacker_alive = True
    lost_value = 0.0
    player_damage = 0
    for first in (True, False):
        attacks_now = attacker_alive and (
            bool(attack & {"firststrike", "doublestrike"})
            if first
            else "firststrike" not in attack or "doublestrike" in attack
        )
        damage = max(0, attacker.power) if attacks_now else 0
        incoming = sum(
            max(0, card.power)
            for card in alive
            if (
                bool(block_tags[card.id] & {"firststrike", "doublestrike"})
                if first
                else "firststrike" not in block_tags[card.id]
                or "doublestrike" in block_tags[card.id]
            )
        )
        touched = any(
            card.power > 0
            and "deathtouch" in block_tags[card.id]
            and (
                bool(block_tags[card.id] & {"firststrike", "doublestrike"})
                if first
                else "firststrike" not in block_tags[card.id]
                or "doublestrike" in block_tags[card.id]
            )
            for card in alive
        )
        destroyed = []
        for blocker in sorted(alive, key=permanent_value, reverse=True):
            lethal = max(0, blocker.toughness - marked[blocker.id])
            if "deathtouch" in attack and damage > 0:
                lethal = min(lethal, 1)
            assigned = min(damage, lethal)
            damage -= assigned
            marked[blocker.id] += assigned
            if (
                marked[blocker.id] >= blocker.toughness or assigned > 0 and "deathtouch" in attack
            ) and "indestructible" not in block_tags[blocker.id]:
                destroyed.append(blocker)
        if attacks_now and "trample" in attack:
            player_damage += damage
        attacker_damage += incoming
        if (attacker_damage >= attacker.toughness or touched) and "indestructible" not in attack:
            attacker_alive = False
        for blocker in destroyed:
            lost_value += permanent_value(blocker)
            alive.remove(blocker)
    return (
        lost_value - (0 if attacker_alive else permanent_value(attacker)),
        player_damage,
        attacker_alive,
    )


def _attack_trigger_value(attacker: Card) -> float:
    # Only unconditional, direct self-attack effects. A costly optional nested
    # trigger (Guide's energy payment) or combat-DAMAGE trigger is not a free gain.
    result = 0.0
    for rule in attacker.rules:
        if not isinstance(rule, Mapping) or rule.get("kind") != "triggeredAbility":
            continue
        event = rule.get("event", {})
        if not isinstance(event, Mapping) or event.get("kind") != "declaredAttacker":
            continue
        if (
            event.get("object") != {"kind": "self"}
            or rule.get("condition")
            or rule.get("interveningIf")
        ):
            continue
        for effect in rule.get("effects", []):
            if not isinstance(effect, Mapping):
                continue
            player = effect.get("player", {"kind": "controllerOf", "object": {"kind": "self"}})
            if player not in (
                {"kind": "controllerOf", "object": {"kind": "self"}},
                {"kind": "you"},
            ):
                continue
            count = effect.get("count", effect.get("amount", {"kind": "integer", "value": 1}))
            if not isinstance(count, Mapping) or count.get("kind") != "integer":
                continue
            amount = count.get("value", 0)
            if not isinstance(amount, (int, float)):
                continue
            result += min(3, max(0, amount)) * {
                "drawCards": 65,
                "drawCard": 65,
                "createToken": 40,
                "gainLife": 8,
            }.get(str(effect.get("kind", "")), 0)
    return min(160.0, result)


def _incoming_combat_damage(enemies: Sequence[Card], defenders: Sequence[Card]) -> int:
    """Conservative next-turn damage, assigning each blocker at most once."""
    available = list(defenders)
    damage = 0
    for enemy in sorted(enemies, key=lambda card: card.power, reverse=True):
        power = max(0, enemy.power)
        enemy_keywords = combat_keywords(enemy)
        power *= 2 if "doublestrike" in enemy_keywords else 1
        choices = [card for card in available if _can_combat_block(card, enemy)]
        # Grouping menace blocks is deliberately not assumed by this estimate.
        if choices and "menace" not in enemy_keywords:
            prevented = [
                (min(power, max(0, card.toughness)) if "trample" in enemy_keywords else power, card)
                for card in choices
            ]
            amount, blocker = max(prevented, key=lambda entry: entry[0])
            damage += max(0, power - amount)
            available.remove(blocker)
        else:
            damage += power
    return damage


def _combat_damage(creatures: Sequence[Card], blockers: Sequence[Card]) -> int:
    attackers = [card for card in creatures if not card.tapped and card.power > 0]
    return _incoming_combat_damage(attackers, blockers)


def _clock(
    owner: str,
    life: int,
    battlefield: Sequence[Card],
    blockers: Sequence[Card],
    *,
    library_size: int = 0,
    hand: Sequence[Card] = (),
    patterns: Sequence[PolicyPattern] = (),
) -> Clock:
    candidates: list[Clock] = []
    damage = _combat_damage(battlefield, blockers)
    if damage > 0:
        candidates.append(
            Clock(
                owner,
                "combat",
                max(1, math.ceil(max(1, life) / damage)),
                min(0.95, 0.62 + 0.05 * len(battlefield)),
                tuple(card.name for card in battlefield if card.power > 0),
            )
        )

    tagged = [(card, set(card_tags(card, patterns))) for card in battlefield]
    enablers = [card for card, tags in tagged if "combo_enabler" in tags]
    payoffs = [
        card
        for card, tags in tagged
        if tags & {"combo_payoff", "alternate_clock"} and "mill" not in tags
    ]
    if enablers and payoffs:
        sources = tuple(dict.fromkeys(card.name for card in (*enablers, *payoffs)))
        candidates.append(Clock(owner, "combo", 1, 0.78, sources))

    mill_sources = [card for card, tags in tagged if "mill" in tags]
    if mill_sources:
        # Exact card text can later override this conservative generic rate through
        # a policy pattern. Even without hidden library size, the state recognizes
        # that an alternate clock exists and should be protected or disrupted.
        cards_remaining = library_size if library_size > 0 else 40
        estimated_rate = 4 * len(mill_sources)
        candidates.append(
            Clock(
                owner,
                "mill",
                max(1, math.ceil(cards_remaining / estimated_rate)),
                0.55 if library_size else 0.4,
                tuple(card.name for card in mill_sources),
            )
        )

    recurring_damage = [
        card
        for card, tags in tagged
        if "burn" in tags and tags & {"artifact", "creature", "enchantment", "planeswalker"}
    ]
    if recurring_damage:
        estimated_rate = 2 * len(recurring_damage)
        candidates.append(
            Clock(
                owner,
                "repeated-damage",
                max(1, math.ceil(max(1, life) / estimated_rate)),
                0.5,
                tuple(card.name for card in recurring_damage),
            )
        )

    burn_in_hand = [card for card in hand if "burn" in card_tags(card, patterns)]
    if burn_in_hand and len(burn_in_hand) * 3 >= life:
        candidates.append(
            Clock(
                owner,
                "direct-damage",
                1,
                0.68,
                tuple(card.name for card in burn_in_hand),
            )
        )
    if not candidates:
        return Clock(owner, "none", 99, 0.35, ())
    return min(candidates, key=lambda clock: (clock.turns, -clock.confidence, clock.kind))


def assess_board(game: Game, policy: StrategyPolicy) -> BoardAssessment:
    me = game.me
    opponents = game.opponents
    opponent_life = min((opponent.life for opponent in opponents), default=20)
    opponent_battlefield = [card for opponent in opponents for card in opponent.battlefield]
    own_value = sum(permanent_value(card) for card in me.battlefield)
    opposing_value = sum(permanent_value(card) for card in opponent_battlefield)
    scale = max(40.0, own_value + opposing_value)
    material = max(-1.0, min(1.0, (own_value - opposing_value) / scale))
    opponent_library = min(
        (len(opponent.zone("library")) for opponent in opponents),
        default=0,
    )
    our_clock = _clock(
        "us",
        opponent_life,
        me.battlefield,
        opponent_battlefield,
        library_size=opponent_library,
        hand=me.hand,
        patterns=policy.patterns,
    )
    opposing_clock = _clock(
        "opponent",
        me.life,
        opponent_battlefield,
        me.battlefield,
        library_size=len(me.zone("library")),
        hand=[card for opponent in opponents for card in opponent.hand],
        patterns=policy.patterns,
    )
    tempo = max(-1.0, min(1.0, (opposing_clock.turns - our_clock.turns) / 6.0))
    aggression = policy.archetype_mix.get("aggro", 0.0) + policy.archetype_mix.get("tempo", 0.0)
    inevitability = policy.archetype_mix.get("control", 0.0) + 0.35 * policy.archetype_mix.get(
        "combo", 0.0
    )
    # Role assignment is contextual, not a permanent archetype label. A clearly
    # faster clock forces beatdown; a clearly slower clock forces control. Close
    # races use deck posture and material to decide who benefits from waiting.
    if tempo > 0.15:
        role = "beatdown"
    elif tempo < -0.15:
        role = "control"
    else:
        pressure = aggression + 0.45 * max(0.0, material)
        patience = inevitability + 0.35 * max(0.0, -material)
        role = "beatdown" if pressure > patience + 0.05 else "control"
    hand_pressure = max(0.0, min(1.0, (4 - len(me.hand)) / 4.0))
    danger = 1.0 if opposing_clock.turns <= 1 else max(0.0, (5 - opposing_clock.turns) / 4.0)
    advantage = max(0.0, 0.55 * max(0.0, material) + 0.45 * max(0.0, tempo))
    develop = max(0.0, min(1.0, 0.55 - 0.45 * material + (0.2 if role == "beatdown" else 0.0)))
    remove = max(0.0, min(1.0, 0.35 + 0.45 * max(0.0, -material) + 0.5 * danger))
    needs = {
        "develop_board": develop,
        "draw_cards": hand_pressure,
        "remove_permanent": remove,
        "answer_stack": 1.0
        if any(
            item.get("controller") in {opponent.id for opponent in opponents} for item in game.stack
        )
        else 0.0,
        "protect_advantage": advantage,
        "stabilize_life": danger,
        "advance_win_condition": 0.8 if role == "beatdown" else 0.4,
        "preserve_resources": max(0.0, min(1.0, 0.45 + 0.35 * advantage + 0.2 * inevitability)),
    }
    return BoardAssessment(role, material, tempo, our_clock, opposing_clock, needs)


@dataclass(frozen=True)
class ScoreContribution:
    node: str
    raw: float
    weight: float
    reason: str

    @property
    def score(self) -> float:
        return self.raw * self.weight


@dataclass(frozen=True)
class ActionScore:
    action_id: str
    total: float
    contributions: tuple[ScoreContribution, ...]
    tags: frozenset[str] = frozenset()

    def explanation(self) -> dict[str, Any]:
        return {
            "actionId": self.action_id,
            "score": round(self.total, 3),
            "tags": sorted(self.tags),
            "contributions": [
                {
                    "node": item.node,
                    "raw": round(item.raw, 3),
                    "weight": round(item.weight, 3),
                    "score": round(item.score, 3),
                    "reason": item.reason,
                }
                for item in sorted(
                    self.contributions, key=lambda value: abs(value.score), reverse=True
                )
            ],
        }


class WeightedDecisionGraph:
    def __init__(self, policy: StrategyPolicy):
        self.policy = policy
        self._assessment_cache: BoardAssessment | None = None

    def _assessment(self, game: Game) -> BoardAssessment:
        if self._assessment_cache is None:
            self._assessment_cache = assess_board(game, self.policy)
        return self._assessment_cache

    def _add(
        self,
        values: list[ScoreContribution],
        node: str,
        raw: float,
        reason: str,
        *,
        weight_adjustment: float = 0.0,
    ) -> None:
        if raw:
            values.append(
                ScoreContribution(
                    node,
                    raw,
                    max(0.0, self.policy.weight(node) + weight_adjustment),
                    reason,
                )
            )

    def _pattern_adjustments(
        self, source: Card | None, targets: Sequence[Card]
    ) -> dict[str, float]:
        adjustments: dict[str, float] = {}
        cards: list[Card] = ([source] if source is not None else []) + list(targets)
        for pattern in self.policy.patterns:
            if any(
                pattern.matches(card, set(card_tags(card, self.policy.patterns))) for card in cards
            ):
                for node, value in pattern.node_adjustments.items():
                    adjustments[node] = adjustments.get(node, 0.0) + value
        return adjustments

    def _score_stack_interaction(
        self,
        decision: Decision,
        action: Action,
        source: Card | None,
        tags: set[str],
        assessment: BoardAssessment,
        adjustments: Mapping[str, float],
        values: list[ScoreContribution],
    ) -> None:
        if not action.target_stack_ids:
            return
        stack = {str(item.get("id", "")): item for item in decision.game.stack}
        opponents = {player.id for player in decision.game.opponents}
        is_counter = "counterspell" in tags
        is_copy = source is not None and (
            "copy target" in card_text(source) or source.rules_contain("copyStackObject")
        )
        for stack_id in set(action.target_stack_ids):
            target = stack.get(stack_id)
            controller = str(target.get("controller", "")) if target else ""
            if not target or controller not in opponents | {decision.player_id}:
                if is_counter:
                    self._add(
                        values,
                        "resource_preservation",
                        -240,
                        "Preserve a counter when the stack target or its controller cannot be identified.",
                        weight_adjustment=adjustments.get("resource_preservation", 0.0),
                    )
                continue
            raw_card = target.get("card")
            target_card = Card(raw_card) if isinstance(raw_card, dict) else None
            target_value = permanent_value(target_card) if target_card is not None else 30.0
            target_tags = (
                set(card_tags(target_card, self.policy.patterns))
                if target_card is not None
                else set()
            )
            if is_counter and target.get("cantBeCountered"):
                self._add(
                    values,
                    "resource_preservation",
                    -320,
                    "Do not spend a counter on an object explicitly marked uncounterable.",
                    weight_adjustment=adjustments.get("resource_preservation", 0.0),
                )
            elif is_counter and controller == decision.player_id:
                # A weighted cost, not an action ban: deliberate deck-specific
                # exceptions can still be authored through patterns/weights.
                self._add(
                    values,
                    "resource_preservation",
                    -(400 + 1.4 * target_value),
                    "Avoid countering our own spell: lose both the counter and the spell it targets.",
                    weight_adjustment=adjustments.get("resource_preservation", 0.0),
                )
                if target_tags & {"combo_enabler", "combo_payoff", "alternate_clock"}:
                    self._add(
                        values,
                        "combo_progress",
                        -100,
                        "Countering our own combo piece undoes our plan.",
                        weight_adjustment=adjustments.get("combo_progress", 0.0),
                    )
            elif is_counter and controller in opponents:
                self._add(
                    values,
                    "interaction",
                    68 + 52 * assessment.needs["answer_stack"] + 0.35 * target_value,
                    "Counter an opposing stack object, not merely any spell on the stack.",
                    weight_adjustment=adjustments.get("interaction", 0.0),
                )
                if target_tags & {"combo_enabler", "combo_payoff", "alternate_clock"}:
                    self._add(
                        values,
                        "combo_disruption",
                        95,
                        "Stop an opposing combo component before it resolves.",
                        weight_adjustment=adjustments.get("combo_disruption", 0.0),
                    )
                refs = target_references(target.get("targets", {})).values()
                protected = []
                for ref in refs:
                    if not isinstance(ref, dict):
                        continue
                    if ref.get("kind") == "stackObject":
                        threatened = stack.get(str(ref.get("stackId", "")), {})
                        if threatened.get("controller") == decision.player_id:
                            protected.append(threatened)
                    elif ref.get("kind") == "permanent":
                        permanent = decision.game.permanent(str(ref.get("instanceId", "")))
                        if permanent is not None and permanent.controller == decision.player_id:
                            protected.append({"card": permanent.raw})
                    elif ref.get("kind") == "player" and ref.get("playerId") == decision.player_id:
                        protected.append({})
                if protected:
                    self._add(
                        values,
                        "protect_advantage",
                        55 + 35 * assessment.needs["protect_advantage"],
                        "Counter the opposing response that actually targets our spell, permanent or player.",
                        weight_adjustment=adjustments.get("protect_advantage", 0.0),
                    )
                critical_protection = any(
                    isinstance(item.get("card"), dict)
                    and (
                        mana_value(Card(item["card"])) >= 3
                        or permanent_value(Card(item["card"])) >= 90
                        or set(card_tags(Card(item["card"]), self.policy.patterns))
                        & {"combo_enabler", "combo_payoff", "alternate_clock", "must_protect"}
                    )
                    for item in protected
                )
                lethal_damage = (
                    bool(protected) and "burn" in target_tags and decision.game.me.life <= 3
                )
                urgent_target = bool(
                    target_tags
                    & {"combo_enabler", "combo_payoff", "alternate_clock", "must_answer"}
                )
                if (
                    target_card is not None
                    and "haste" in target_tags
                    and target_card.power >= decision.game.me.life
                ):
                    urgent_target = True
                if not (critical_protection or urgent_target or lethal_damage):
                    counters_left = max(
                        1,
                        sum(
                            "counterspell" in card_tags(card, self.policy.patterns)
                            for card in decision.game.me.hand
                        ),
                    )
                    low_impact = (
                        target_card is not None
                        and mana_value(target_card) <= 2
                        and bool(target_tags & {"cantrip", "card_selection", "card_draw"})
                    )
                    reserve = (
                        105
                        + 85 * self.policy.archetype_mix.get("combo", 0.0)
                        + 50 * assessment.needs["preserve_resources"]
                        + (80 if low_impact else 0)
                    ) / math.sqrt(counters_left)
                    self._add(
                        values,
                        "resource_preservation",
                        -reserve,
                        "Keep scarce countermagic available to protect our combo or answer a consequential threat.",
                        weight_adjustment=adjustments.get("resource_preservation", 0.0),
                    )
            elif is_copy:
                self._add(
                    values,
                    "card_value",
                    0.65 * target_value,
                    "Copying a useful spell is not the same as countering our own spell.",
                    weight_adjustment=adjustments.get("card_value", 0.0),
                )

    @staticmethod
    def _target_value(targets: Sequence[Card]) -> float:
        return sum(permanent_value(target) for target in targets)

    def _score_land_denial(
        self,
        decision: Decision,
        action: Action,
        source: Card | None,
        targets: Sequence[Card],
        adjustments: Mapping[str, float],
        values: list[ScoreContribution],
    ) -> None:
        if source is None:
            return
        parameters = action.raw.get("decisions", {})
        parameters = parameters if isinstance(parameters, dict) else {}
        if parameters.get("manaAbility"):
            return
        rule_index = parameters.get("abilityRuleIndex")
        selected_rule = (
            source.rules[rule_index]
            if type(rule_index) is int and 0 <= rule_index < len(source.rules)
            else None
        )
        rule_text = (
            " ".join(_walk_strings(selected_rule)).casefold()
            if selected_rule
            else card_text(source)
        )
        destructive = any(
            token in rule_text
            for token in ("destroypermanent", "exilepermanent", "destroy target", "exile target")
        )
        lands = [target for target in targets if target.is_type("Land")]
        if not destructive or not (lands or source.is_type("Land") and "land" in rule_text):
            return

        def add(node: str, score: float, reason: str) -> None:
            self._add(values, node, score, reason, weight_adjustment=adjustments.get(node, 0.0))

        if not lands:
            add(
                "resource_preservation",
                -500,
                "No identifiable land target: do not spend land destruction blindly.",
            )
        for target in lands:
            if target.controller == decision.player_id:
                add(
                    "resource_preservation",
                    -520,
                    "Destroying our own land loses resources; a self-sacrificing source targeting itself will fizzle.",
                )
                continue
            owner = decision.game.player(target.controller)
            if owner is None:
                add("resource_preservation", -300, "The land's controller cannot be identified.")
                continue
            remaining = [card for card in owner.battlefield if card.id != target.id]
            remaining_lands = [card for card in remaining if card.is_type("Land")]
            remaining_colors: set[str] = set()
            for card in remaining:
                remaining_colors.update(
                    available_colors(card) if card.is_type("Land") else produced_colors(card)
                )
            cut_colors = available_colors(target) - remaining_colors
            # Public cards can reveal color demand; never inspect a hidden
            # opposing hand/library even if a local debug observation has it.
            public_demand: set[str] = set()
            for card in [*owner.battlefield, *owner.zone("graveyard"), *owner.zone("exile")]:
                if not card.is_type("Land"):
                    public_demand.update(required_colors(card))
            demanded_cut = cut_colors & public_demand
            density = (
                75
                if len(remaining_lands) == 0
                else 50
                if len(remaining_lands) == 1
                else 25
                if len(remaining_lands) == 2
                else 5
            )
            add(
                "clock_disruption",
                density + 35 * len(cut_colors) + 35 * len(demanded_cut),
                f"Remove {target.name}: {len(remaining_lands)} opposing lands remain; cut colors {','.join(sorted(cut_colors)) or 'none'}. Color demand uses public cards only.",
            )
            if not target.tapped:
                add("timing_patience", 18, "An untapped land can still supply mana this turn.")
            target_tags = set(card_tags(target, self.policy.patterns))
            utility = target_tags & {
                "card_draw",
                "token_maker",
                "tax",
                "mana_denial",
                "utility_land",
            }
            if utility:
                add(
                    "interaction",
                    38 * len(utility),
                    f"Deny land utility: {', '.join(sorted(utility))}.",
                )
            if target_tags & {"combo_enabler", "combo_payoff", "alternate_clock", "must_answer"}:
                add(
                    "combo_disruption",
                    115,
                    "Remove a tagged land that supports the opposing combo or win condition.",
                )
            if land_mana_output(target) >= 2 or target_tags & {
                "big_mana",
                "ramp",
            }:
                add(
                    "mana_efficiency",
                    55,
                    "Remove a land producing more than one mana or supporting a big-mana plan.",
                )
            if (
                "sacrifice" in card_text(target)
                and "search" in card_text(target)
                and not target.tapped
            ):
                add(
                    "timing_patience",
                    -65,
                    "An untapped fetch land can sacrifice itself in response, so mana denial is not guaranteed.",
                )

        sacrifices_source = False
        if isinstance(selected_rule, Mapping):
            sacrifices_source = any(
                isinstance(cost, Mapping)
                and str(cost.get("kind", "")).casefold() == "sacrificepermanent"
                and "self" in set(_walk_strings(cost))
                for cost in selected_rule.get("costs", [])
            )
        elif source.is_type("Land"):
            sacrifices_source = "sacrifice" in rule_text
        if sacrifices_source and source.is_type("Land"):
            own_lands = [card for card in decision.game.me.battlefield if card.is_type("Land")]
            remaining = [card for card in own_lands if card.id != source.id]
            other_colors = set().union(*(available_colors(card) for card in remaining))
            own_demand = set().union(
                *(
                    required_colors(card)
                    for card in decision.game.me.hand
                    if not card.is_type("Land")
                )
            )
            lost_colors = (available_colors(source) - other_colors) & own_demand
            scarcity = (
                125
                if not remaining
                else 75
                if len(remaining) == 1
                else 25
                if len(remaining) == 2
                else 0
            )
            add(
                "resource_preservation",
                -(50 + scarcity),
                f"Sacrificing {source.name} leaves {len(remaining)} of our lands: account for our own mana development.",
            )
            if lost_colors:
                add(
                    "mana_efficiency",
                    -70 * len(lost_colors),
                    "Do not cut a color required by our own hand.",
                )
            costs = [
                mana_value(card)
                for card in decision.game.me.hand
                if not card.is_type("Land") and mana_value(card) > 0
            ]
            if any(len(remaining) < cost <= len(own_lands) for cost in costs):
                add(
                    "mana_efficiency",
                    -65,
                    "The sacrificed mana source would prevent a currently affordable play from our hand.",
                )

    def score_priority(self, decision: Decision, action: Action) -> ActionScore:
        action = compatible_action(action)
        assessment = self._assessment(decision.game)
        values: list[ScoreContribution] = []
        if action.kind == "passPriority":
            if decision.game.stack:
                if decision.game.stack[-1].get("controller") == decision.player_id:
                    self._add(
                        values,
                        "resource_preservation",
                        12,
                        "Let our own top-of-stack spell or ability resolve.",
                    )
                else:
                    self._add(
                        values,
                        "resource_preservation",
                        -18,
                        "Passing lets the opposing stack advance.",
                    )
            elif decision.game.is_my_turn:
                step = decision.game.step.casefold()
                raw = 18 if "precombat" in step else -12 if "postcombat" in step else 5
                self._add(
                    values, "timing_patience", raw, "Sequence sorcery-speed plays around combat."
                )
            else:
                self._add(values, "timing_patience", 42, "Keep information and mana available.")
            return self._finish(action, values, set(), decision)
        if action.kind == "playLand":
            self._add(values, "mana_efficiency", 1_000, "Make the available land drop.")
            land = decision.card_for(action)
            tags = set(card_tags(land, self.policy.patterns)) if land is not None else {"land"}
            current_colors = set().union(
                *(
                    available_colors(card)
                    for card in decision.game.me.battlefield
                    if card.is_type("Land")
                )
            )
            required = set().union(
                *(
                    required_colors(card)
                    for card in decision.game.me.hand
                    if not card.is_type("Land")
                )
            )
            provided = available_colors(land) if land is not None else frozenset()
            missing = required - current_colors
            if missing & provided:
                self._add(
                    values,
                    "mana_efficiency",
                    95 * len(missing & provided),
                    "Add a color needed by spells currently in hand.",
                )
            elif provided:
                self._add(
                    values,
                    "mana_efficiency",
                    8 * len(provided),
                    "Improve the breadth of available colored mana.",
                )
            if tags & {"interaction", "combo_enabler", "alternate_clock", "card_draw"}:
                self._add(values, "card_value", 24, "Retain the land's relevant utility.")
            if land is not None and "enters the battlefield tapped" in card_text(land):
                self._add(
                    values,
                    "mana_efficiency",
                    -32,
                    "A tapped land delays this turn's mana.",
                )
            return self._finish(action, values, tags, decision)
        if action.kind not in {"castSpell", "activateAbility", "specialAction"}:
            self._add(values, "card_value", -500, "This action does not advance the current plan.")
            return self._finish(action, values, set(), decision)

        source = decision.card_for(action)
        choices = action.raw.get("decisions")
        if (
            action.kind == "activateAbility"
            and isinstance(choices, dict)
            and choices.get("equip") is True
            and source is not None
            and source.raw.get("attachedTo")
            and source.raw["attachedTo"] in action.target_permanent_ids
        ):
            # Equip remains a legal activation even on the current bearer.
            # It does not develop another permanent; rewarding its source's
            # artifact tags makes zero-cost equipment loop indefinitely.
            # Keep an explicit contribution (independent of ordinary resource
            # weights); intentional attachment-trigger plans may override it
            # through an explicit custom node rather than a generic card boost.
            values.append(
                ScoreContribution(
                    "resource_preservation",
                    -1_000,
                    1.0,
                    "Equipment is already attached to this target; avoid a redundant equip loop.",
                )
            )
            return self._finish(action, values, {"redundant_equip"}, decision)
        if (
            action.kind == "activateAbility"
            and isinstance(action.raw.get("decisions"), dict)
            and action.raw["decisions"].get("manaAbility")
        ):
            self._add(
                values,
                "mana_efficiency",
                -85,
                "Do not float mana without a committed play; Engine legal casts already include payment choices.",
            )
            return self._finish(action, values, {"mana_ability"}, decision)
        tags = set(card_tags(source, self.policy.patterns)) if source is not None else set()
        targets = decision.target_permanents(action)
        opponents = {player.id for player in decision.game.opponents}
        opposing_targets = [target for target in targets if target.controller in opponents]
        friendly_targets = [target for target in targets if target.controller == decision.player_id]
        stack_ids = set(action.target_stack_ids)
        stack_cards = [
            Card(item["card"])
            for item in decision.game.stack
            if str(item.get("id", "")) in stack_ids and isinstance(item.get("card"), dict)
        ]
        adjustments = self._pattern_adjustments(source, [*targets, *stack_cards])
        source_value = 10.0 + 4.0 * mana_value(source) if source is not None else 8.0
        if source is not None and "creature" in tags:
            source_value += max(0, source.power) * 5 + max(0, source.toughness) * 3
        self._add(values, "card_value", source_value, "Intrinsic quality of the source card.")
        payment_count = len(set(action.payment_sources))
        if payment_count:
            self._add(
                values,
                "mana_efficiency",
                -2.5 * payment_count,
                "Mana committed by the action.",
            )
            available_mana = max(payment_count, decision.game.me.available_mana_count)
            utilization = payment_count / max(1, available_mana)
            if decision.game.is_my_turn:
                self._add(
                    values,
                    "mana_efficiency",
                    26 * utilization,
                    "Use available mana efficiently before the turn ends.",
                )

        if tags & {"creature", "planeswalker", "artifact", "enchantment", "battle"}:
            self._add(
                values,
                "board_development",
                52 * assessment.needs["develop_board"],
                "Develop a permanent when the board needs presence.",
                weight_adjustment=adjustments.get("board_development", 0.0),
            )
        if "creature" in tags:
            if assessment.needs["stabilize_life"] > 0:
                self._add(
                    values,
                    "combat_survival",
                    78 * assessment.needs["stabilize_life"],
                    "Add a blocker when the opposing clock threatens our life total.",
                    weight_adjustment=adjustments.get("combat_survival", 0.0),
                )
            if assessment.role == "beatdown" and (source is None or mana_value(source) <= 3):
                self._add(
                    values,
                    "clock_pressure",
                    34,
                    "Deploy a reasonably cheap threat while assigned beatdown.",
                    weight_adjustment=adjustments.get("clock_pressure", 0.0),
                )
        if "card_draw" in tags:
            self._add(
                values,
                "card_advantage",
                64 + 48 * assessment.needs["draw_cards"],
                "Gain cards, with extra value when resources are low.",
                weight_adjustment=adjustments.get("card_advantage", 0.0),
            )
        if tags & {"card_selection", "tutor", "cantrip"}:
            self._add(
                values,
                "card_selection",
                48 + 35 * self.policy.archetype_mix.get("combo", 0.0),
                "Improve draw quality or locate a plan component.",
                weight_adjustment=adjustments.get("card_selection", 0.0),
            )
        if tags & {"combo_enabler", "combo_payoff", "reanimate"}:
            self._add(
                values,
                "combo_progress",
                75 + 65 * self.policy.archetype_mix.get("combo", 0.0),
                "Advance a synergistic or linear win condition.",
                weight_adjustment=adjustments.get("combo_progress", 0.0),
            )
        if "life_gain" in tags and assessment.needs["stabilize_life"] > 0:
            self._add(
                values,
                "combat_survival",
                62 * assessment.needs["stabilize_life"],
                "Life gain buys time against the opposing clock.",
                weight_adjustment=adjustments.get("combat_survival", 0.0),
            )
        if "tax" in tags:
            self._add(
                values,
                "clock_disruption",
                48 + 32 * self.policy.archetype_mix.get("tempo", 0.0),
                "A tax effect slows the opponent while advancing our position.",
                weight_adjustment=adjustments.get("clock_disruption", 0.0),
            )
        if "burn" in tags and action.target_player_ids:
            target_life = min(
                (
                    player.life
                    for player_id in action.target_player_ids
                    if (player := decision.game.player(player_id)) is not None
                ),
                default=20,
            )
            self._add(
                values,
                "clock_pressure",
                42 + max(0, 10 - target_life) * 7,
                "Direct damage advances the active clock.",
                weight_adjustment=adjustments.get("clock_pressure", 0.0),
            )
        if "mill" in tags and action.target_player_ids:
            self._add(
                values,
                "clock_pressure",
                68,
                "Mill advances an alternate library clock.",
                weight_adjustment=adjustments.get("clock_pressure", 0.0),
            )

        opposing_value = self._target_value(opposing_targets)
        friendly_value = self._target_value(friendly_targets)
        if tags & {"removal", "interaction"} and opposing_targets:
            self._add(
                values,
                "interaction",
                opposing_value * (0.6 + assessment.needs["remove_permanent"]),
                "Answer opposing permanent value and board pressure.",
                weight_adjustment=adjustments.get("interaction", 0.0),
            )
            combo_targets = sum(
                bool(
                    set(card_tags(target, self.policy.patterns))
                    & {"combo_enabler", "combo_payoff", "alternate_clock"}
                )
                for target in opposing_targets
            )
            if combo_targets:
                self._add(
                    values,
                    "combo_disruption",
                    95 * combo_targets,
                    "Remove a combo enabler, payoff, or alternate clock.",
                    weight_adjustment=adjustments.get("combo_disruption", 0.0),
                )
            if assessment.opposing_clock.turns <= 2:
                self._add(
                    values,
                    "clock_disruption",
                    90,
                    "Disrupt a clock that is close to winning.",
                    weight_adjustment=adjustments.get("clock_disruption", 0.0),
                )
            elif opposing_value < 40:
                self._add(
                    values,
                    "resource_preservation",
                    -(42 + max(0.0, 40 - opposing_value)),
                    "Preserve flexible interaction instead of answering a low-impact permanent.",
                    weight_adjustment=adjustments.get("resource_preservation", 0.0),
                )
        if friendly_value:
            self._add(values, "card_value", -0.8 * friendly_value, "Avoid harming our permanents.")
        self._score_land_denial(decision, action, source, targets, adjustments, values)
        if "protection" in tags and friendly_targets:
            self._add(
                values,
                "protect_advantage",
                friendly_value * (0.5 + assessment.needs["protect_advantage"]),
                "Protect a relevant friendly permanent.",
                weight_adjustment=adjustments.get("protect_advantage", 0.0),
            )

        if "sweeper" in tags:
            opposing_creatures = sum(
                card.is_type("Creature")
                for opponent in decision.game.opponents
                for card in opponent.battlefield
            )
            own_creatures = sum(card.is_type("Creature") for card in decision.game.me.battlefield)
            exchange = opposing_creatures - 1.2 * own_creatures
            self._add(
                values,
                "interaction",
                42 * exchange,
                "Evaluate how many opposing creatures a sweeper trades for our own.",
                weight_adjustment=adjustments.get("interaction", 0.0),
            )
        if "discard" in tags and not opposing_targets:
            opposing_hand_size = max(
                (len(opponent.hand) for opponent in decision.game.opponents), default=0
            )
            self._add(
                values,
                "combo_disruption",
                min(58.0, 12.0 * opposing_hand_size),
                "Use discard while the opponent still has cards to disrupt.",
                weight_adjustment=adjustments.get("combo_disruption", 0.0),
            )

        self._score_stack_interaction(
            decision, action, source, tags, assessment, adjustments, values
        )

        step = decision.game.step.casefold()
        if "instant" in tags and decision.game.is_my_turn and not decision.game.stack:
            early_information = "card_draw" in tags and len(decision.game.me.hand) <= 2
            timing_raw = 15 if early_information else -38
            self._add(
                values,
                "timing_patience",
                timing_raw,
                "Wait with instant-speed actions unless they inform this turn.",
                weight_adjustment=adjustments.get("timing_patience", 0.0),
            )
        if "instant" in tags and not decision.game.is_my_turn and "end" in step:
            self._add(values, "timing_patience", 42, "Use the opponent's end step safely.")
        if decision.game.is_my_turn and "precombat" in step and not tags & {"haste", "removal"}:
            self._add(values, "timing_patience", -12, "Conceal information until after combat.")
        if not decision.game.is_my_turn and "instant" not in tags and action.kind == "castSpell":
            self._add(values, "timing_patience", -75, "Avoid non-instant play outside our turn.")

        if source is not None:
            for pattern in self.policy.patterns:
                if pattern.matches(source, tags):
                    for node, adjustment in pattern.node_adjustments.items():
                        self._add(
                            values,
                            node,
                            35,
                            f"Deck pattern {pattern.id!r} applies.",
                            weight_adjustment=adjustment,
                        )
        return self._finish(action, values, tags, decision)

    def score_attack(self, decision: Decision, action: Action) -> ActionScore:
        action = compatible_action(action)
        assessment = self._assessment(decision.game)
        values: list[ScoreContribution] = []
        if action.kind == "finishAttackers":
            if assessment.role == "control":
                self._add(values, "combat_survival", 24, "Remain defensive while assigned control.")
            return self._finish(action, values, set(), decision)
        attacker = decision.game.permanent(action.attacker_id)
        if attacker is None:
            self._add(values, "combat_pressure", -500, "Attacker is unavailable.")
            return self._finish(action, values, set(), decision)
        tags = set(card_tags(attacker, self.policy.patterns))
        defenders = [
            player for player in decision.game.opponents if player.id in action.target_player_ids
        ]
        if not defenders:
            targets = [decision.game.permanent(key) for key in action.target_permanent_ids]
            controllers = {card.controller for card in targets if card is not None}
            defenders = [player for player in decision.game.opponents if player.id in controllers]
        # Unknown targets use conservative public blockers; another opponent's
        # creatures never block a known defender's attack in multiplayer.
        defenders = defenders or decision.game.opponents
        target_life = min((player.life for player in defenders), default=20)
        keywords = combat_keywords(attacker)
        # Do not advertise text-search role tags as currently active abilities.
        tags.difference_update(
            {"evasion", "vigilance", "deathtouch", "lifelink", "first_strike", "double_strike"}
        )
        tags.update(
            {"evasion"}
            if keywords & {"flying", "trample", "menace", "cantbeblocked", "shadow"}
            else set()
        )
        tags.update(
            keyword for keyword in ("vigilance", "deathtouch", "lifelink") if keyword in keywords
        )
        role_bonus = 38 if assessment.role == "beatdown" else -8
        blockers = [
            card
            for opponent in defenders
            for card in opponent.battlefield
            if _can_combat_block(card, attacker)
        ]
        unblocked_damage = max(0, attacker.power) * (2 if "doublestrike" in keywords else 1)
        responses = [(0.0, unblocked_damage, True)]
        minimum = 2 if "menace" in keywords else 1
        # Bound work on token-heavy boards, while retaining both large threats
        # and cheap chump blockers. Never treat trample/menace as unblockable.
        sampled = sorted(blockers, key=permanent_value, reverse=True)
        sampled = sampled if len(sampled) <= 12 else sampled[:6] + sampled[-6:]
        for count in range(minimum, min(2, len(sampled)) + 1):
            responses.extend(
                _combat_exchange(attacker, group) for group in combinations(sampled, count)
            )
        if len(blockers) > 2:
            responses.append(_combat_exchange(attacker, blockers))

        def response_score(response: tuple[float, int, bool]) -> float:
            exchange, damage, survives = response
            gain = exchange + (0 if survives else permanent_value(attacker))
            pressure = damage * 15 + (role_bonus if damage or gain else -20)
            if gain:
                pressure += 24
            return (
                exchange * self.policy.weight("card_value")
                + pressure * self.policy.weight("combat_pressure")
                - (0 if survives else 60 * self.policy.weight("combat_survival"))
            )

        exchange, damage, survives = min(responses, key=response_score)
        gain = exchange + (0 if survives else permanent_value(attacker))
        self._add(
            values,
            "card_value",
            exchange,
            "Evaluate material gained and lost against a plausible blocking response.",
        )
        self._add(
            values,
            "combat_pressure",
            damage * 15 + (role_bonus if damage or gain else -20) + (24 if gain else 0),
            "Reward damage or a useful exchange, not merely declaring an attack.",
        )
        if not survives:
            self._add(
                values, "combat_survival", -60, "Losing this attacker also loses a future blocker."
            )
        if min(response[1] for response in responses) > 0:
            self._add(
                values, "clock_pressure", 58, "Damage remains after available blocking responses."
            )
            # Only attacks on a player can be life-total lethal. A planeswalker
            # target must not inherit its controller's life-total bonus.
            if (
                action.target_player_ids
                and min(response[1] for response in responses) >= target_life
            ):
                self._add(
                    values,
                    "clock_pressure",
                    10_000,
                    "Available blockers cannot prevent this attack's lethal damage.",
                )
        trigger_value = _attack_trigger_value(attacker)
        self._add(
            values,
            "card_advantage",
            trigger_value,
            "A direct attack trigger provides value even if blocked.",
        )
        enemies = [
            card
            for opponent in decision.game.opponents
            for card in opponent.battlefield
            if card.is_type("Creature")
        ]
        our_blockers = [
            card
            for card in decision.game.me.battlefield
            if card.is_type("Creature") and not card.tapped
        ]
        if "vigilance" not in keywords:
            before = _incoming_combat_damage(enemies, our_blockers)
            after = _incoming_combat_damage(
                enemies, [card for card in our_blockers if card.id != attacker.id]
            )
            crackback = max(0, after - before)
            danger_multiplier = 2.2 if after >= decision.game.me.life else 0.8
            self._add(
                values,
                "combat_survival",
                -crackback * 8 * danger_multiplier,
                "Preserve the damage this creature could prevent as a blocker next turn.",
            )
        if tags & {"card_draw", "tax", "token_maker"} and assessment.role == "control":
            self._add(values, "card_value", -22, "Preserve a valuable utility creature.")
        return self._finish(action, values, tags, decision)

    def score_block(self, decision: Decision, action: Action) -> ActionScore:
        values: list[ScoreContribution] = []
        if action.kind == "finishBlockers":
            return self._finish(action, values, set(), decision)
        blocker = decision.game.permanent(action.blocker_id)
        attacker = decision.game.permanent(action.attacker_id)
        if blocker is None or attacker is None:
            self._add(values, "combat_survival", -500, "Combatant is unavailable.")
            return self._finish(action, values, set(), decision)
        tags = set(card_tags(blocker, self.policy.patterns))
        kills = blocker.power >= attacker.toughness
        survives = blocker.toughness > attacker.power
        lethal = attacker.power >= decision.game.me.life
        if lethal:
            self._add(values, "combat_survival", 10_000, "This block prevents lethal damage.")
        self._add(
            values,
            "combat_survival",
            attacker.power * 12 + (48 if kills else 0) + (36 if survives else -18),
            "Prevent damage and evaluate the combat exchange.",
        )
        value_delta = permanent_value(attacker) - (0 if survives else permanent_value(blocker))
        self._add(
            values, "card_value", value_delta, "Compare permanents gained and lost in combat."
        )
        if tags & {"card_draw", "tax", "token_maker"} and not lethal:
            self._add(
                values, "resource_preservation", -30, "Avoid trading a useful engine casually."
            )
        return self._finish(action, values, tags, decision)

    def _finish(
        self,
        action: Action,
        values: Sequence[ScoreContribution],
        tags: set[str],
        decision: Decision,
    ) -> ActionScore:
        values = [*values, *self.custom_contributions(decision, action, values)]
        tie_break = (
            int(hashlib.sha256(f"{decision.request_id}:{action.id}".encode()).hexdigest()[:6], 16)
            / 0xFFFFFF
        )
        total = sum(value.score for value in values) + tie_break * 0.001
        return ActionScore(action.id, total, tuple(values), frozenset(tags))

    def custom_contributions(
        self,
        decision: Decision,
        action: Action,
        base: Sequence[ScoreContribution] = (),
    ) -> list[ScoreContribution]:
        if not self.policy.custom_nodes:
            return []
        action = compatible_action(action)
        game = decision.game
        assessment = self._assessment(game)
        source = decision.card_for(action) or game.permanent(
            action.blocker_id or action.attacker_id
        )
        # Engine enum targets need the same adapter used by the base graph.
        targets = [card for key in action.target_permanent_ids if (card := game.permanent(key))]
        if action.blocker_id and action.attacker_id:
            attacker = game.permanent(action.attacker_id)
            if attacker is not None:
                targets.append(attacker)
        stack = {str(item.get("id", "")): item for item in game.stack}
        target_views: list[tuple[Card, str]] = [(card, card.controller) for card in targets]
        for key in action.target_stack_ids:
            item = stack.get(key)
            if item and isinstance(item.get("card"), dict):
                target_views.append((Card(item["card"]), str(item.get("controller", ""))))

        def view(card: Card, controller: str) -> dict[str, Any]:
            return {
                "name": card.name,
                "tags": card_tags(card, self.policy.patterns),
                "controller": "self"
                if controller == decision.player_id
                else "opponent"
                if controller in {p.id for p in game.opponents}
                else "unknown",
            }

        signals = {
            f"need.{key}": max(-1.0, min(1.0, value)) for key, value in assessment.needs.items()
        }
        signals.update(
            {
                "state.our_turn": float(game.is_my_turn),
                "state.stack_busy": float(bool(game.stack)),
                "state.our_life": max(0.0, min(1.0, game.me.life / 20)),
                "state.our_clock": 1 / max(1.0, assessment.our_clock.turns),
                "state.opposing_clock": 1 / max(1.0, assessment.opposing_clock.turns),
                "state.material": max(-1.0, min(1.0, assessment.material_advantage)),
                "hand.lands": min(1.0, sum(c.is_type("Land") for c in game.me.hand) / 5),
                "hand.size": min(1.0, len(game.me.hand) / 7),
            }
        )
        for node in STRATEGY_NODES:
            signals[f"base.{node.id}"] = max(
                -1.0, min(1.0, sum(c.score for c in base if c.node == node.id) / 100)
            )
        context = {
            "actionKind": action.kind,
            "turn": "ours" if game.is_my_turn else "opponent",
            "role": assessment.role,
            "sourceCards": [view(source, source.controller)] if source else [],
            "targetCards": [view(card, controller) for card, controller in target_views],
        }
        return [
            ScoreContribution(*entry)
            for entry in evaluate_nodes(self.policy.custom_nodes, signals, context)
        ]

    def choose(self, decision: Decision, scores: Sequence[ActionScore]) -> ActionScore:
        if not scores:
            raise ValueError("Cannot choose without scored actions.")
        ordered = sorted(scores, key=lambda value: (value.total, value.action_id), reverse=True)
        if self.policy.temperature <= 0 or len(ordered) == 1:
            return ordered[0]
        # Keep exploration among plausible actions; never turn a decisive tactical score into noise.
        top = ordered[0].total
        window = max(18.0, 70.0 * self.policy.temperature)
        candidates = [score for score in ordered if top - score.total <= window]
        if len(candidates) == 1:
            return candidates[0]
        scale = max(4.0, 32.0 * self.policy.temperature)
        probabilities = [math.exp((score.total - top) / scale) for score in candidates]
        seed_text = (
            f"{self.policy.seed}:{decision.request_id}:{decision.game.turn}:"
            f"{decision.game.step}:{','.join(score.action_id for score in candidates)}"
        )
        seed = int(hashlib.sha256(seed_text.encode()).hexdigest()[:16], 16)
        chooser = random.Random(seed)
        point = chooser.random() * sum(probabilities)
        cumulative = 0.0
        for score, probability in zip(candidates, probabilities, strict=True):
            cumulative += probability
            if point <= cumulative:
                return score
        return candidates[-1]


def opening_hand_score(
    hand: Sequence[Card], policy: StrategyPolicy, *, cards_after_keep: int | None = None
) -> tuple[float, tuple[ScoreContribution, ...]]:
    lands = [card for card in hand if card.is_type("Land")]
    spells = [card for card in hand if not card.is_type("Land")]
    tags = {card.id: card_tags(card, policy.patterns) for card in hand}
    mix = policy.archetype_mix
    target_lands = (
        2.3 * (mix.get("aggro", 0.0) + mix.get("tempo", 0.0) + mix.get("combo", 0.0))
        + 3.2 * mix.get("midrange", 0.0)
        + 3.5 * mix.get("control", 0.0)
    )
    land_fit = max(-1.0, 1.0 - abs(len(lands) - target_lands) / 2.2)
    contributions: list[ScoreContribution] = [
        ScoreContribution(
            "mulligan_plan",
            55 * land_fit,
            policy.weight("mulligan_plan"),
            "Land count fits the deck plan.",
        ),
    ]
    source_colors = set().union(*(available_colors(card) for card in lands)) if lands else set()
    early = [card for card in spells if mana_value(card) <= 2 or "fast_mana" in tags[card.id]]
    playable_early = [
        card
        for card in early
        if required_colors(card) <= source_colors
        and (mana_value(card) <= len(lands) or "fast_mana" in tags[card.id])
    ]
    if playable_early:
        contributions.append(
            ScoreContribution(
                "mana_efficiency",
                min(48.0, 16.0 * len(playable_early)),
                policy.weight("mana_efficiency"),
                "The hand has early plays supported by its mana.",
            )
        )
    else:
        contributions.append(
            ScoreContribution(
                "mana_efficiency",
                -45.0,
                policy.weight("mana_efficiency"),
                "The hand cannot affect the early game.",
            )
        )
    missing_colors = set().union(*(required_colors(card) for card in early)) - source_colors
    if missing_colors:
        contributions.append(
            ScoreContribution(
                "mulligan_plan",
                -24.0 * len(missing_colors),
                policy.weight("mulligan_plan"),
                f"The hand lacks early {', '.join(sorted(missing_colors))} mana.",
            )
        )
    if mix.get("aggro", 0.0) + mix.get("tempo", 0.0) > 0.42:
        threats = sum("threat" in tags[card.id] and mana_value(card) <= 2 for card in spells)
        contributions.append(
            ScoreContribution(
                "clock_pressure",
                35.0 if threats else -38.0,
                policy.weight("clock_pressure"),
                "Aggro and tempo hands need an early clock.",
            )
        )
    if mix.get("tempo", 0.0) + mix.get("control", 0.0) > 0.42:
        answers = sum(
            bool(tags[card.id] & {"removal", "counterspell", "discard"}) for card in spells
        )
        contributions.append(
            ScoreContribution(
                "interaction",
                min(42.0, 21.0 * answers) if answers else -24.0,
                policy.weight("interaction"),
                "Interactive decks need an early answer.",
            )
        )
    if mix.get("combo", 0.0) > 0.28:
        enablers = sum("combo_enabler" in tags[card.id] for card in spells)
        payoffs = sum("combo_payoff" in tags[card.id] for card in spells)
        selection = sum(
            bool(tags[card.id] & {"tutor", "card_selection", "cantrip"}) for card in spells
        )
        plan = (
            52.0
            if enablers and (payoffs or selection)
            else 24.0
            if enablers or selection
            else -52.0
        )
        contributions.append(
            ScoreContribution(
                "combo_progress",
                plan,
                policy.weight("combo_progress"),
                "The hand can assemble or find the linear plan.",
            )
        )
    if cards_after_keep is not None and cards_after_keep < len(hand):
        contributions.append(
            ScoreContribution(
                "card_advantage",
                -16.0 * (len(hand) - cards_after_keep),
                policy.weight("card_advantage"),
                "A further mulligan loses additional cards.",
            )
        )
    return sum(item.score for item in contributions), tuple(contributions)


def mulligan_bottom_value(card: Card, hand: Sequence[Card], policy: StrategyPolicy) -> float:
    tags = card_tags(card, policy.patterns)
    lands = sum(item.is_type("Land") for item in hand)
    value = 8.0 + 6.0 * mana_value(card)
    if "land" in tags:
        target = 2 if policy.archetype_mix.get("aggro", 0.0) > 0.4 else 3
        return 5.0 if lands > target else 95.0
    if tags & {"combo_enabler", "combo_payoff"}:
        value += 55.0 * policy.weight("combo_progress")
    if tags & {"removal", "counterspell", "discard"}:
        value += 28.0 * policy.weight("interaction")
    if "threat" in tags and mana_value(card) <= 2:
        value += 35.0 * policy.weight("clock_pressure")
    if mana_value(card) >= 5 and not tags & {"combo_payoff", "interaction"}:
        value -= 32.0
    return value


def decision_graph_catalog() -> dict[str, Any]:
    return {
        "schemaVersion": "deepdeck-strategy-graph-catalog/v1",
        "modelVersion": STRATEGY_GRAPH_VERSION,
        "implementationRevision": STRATEGY_GRAPH_IMPLEMENTATION,
        "weightRange": {"minimum": 0.0, "maximum": 2.0, "default": 1.0},
        "temperatureRange": {"minimum": 0.0, "maximum": 1.0, "default": 0.0},
        "deckAdaptationRange": {"minimum": 0.0, "maximum": 1.0, "default": 0.65},
        "archetypes": list(ARCHETYPES),
        "customNodeSignals": [{"id": key, "name": name} for key, name in SIGNALS.items()],
        "customNodeActionKinds": list(ACTION_KINDS),
        "customNodeLimit": 32,
        "nodes": [
            {
                "id": node.id,
                "name": node.name,
                "description": node.description,
                "default": node.default,
            }
            for node in STRATEGY_NODES
        ],
        "profiles": ARCHETYPE_WEIGHTS,
    }


def policy_fingerprint(policy: StrategyPolicy) -> str:
    payload = {
        "version": policy.version,
        "archetypeMix": dict(policy.archetype_mix),
        "weights": dict(policy.weights),
        "deckAdaptation": policy.deck_adaptation,
        "temperature": policy.temperature,
        "seed": policy.seed,
        "patterns": [
            {
                "id": pattern.id,
                "cardNames": pattern.card_names,
                "textContains": pattern.text_contains,
                "requireTags": pattern.require_tags,
                "addTags": pattern.add_tags,
                "nodeAdjustments": dict(pattern.node_adjustments),
            }
            for pattern in policy.patterns
        ],
    }
    if policy.custom_nodes:
        payload["customNodes"] = list(policy.custom_nodes)
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]
