"""Bounded numeric telemetry from observed Engine states; no inferred plays."""

from __future__ import annotations

import math
from collections import OrderedDict
from typing import Any

GROUPS = [
    {
        "id": "resources",
        "name": "Cards and mana",
        "description": "Observed hand size, lands, draws and mana produced.",
    },
    {
        "id": "board",
        "name": "Board and life",
        "description": "Observed life, creatures and nonland permanents.",
    },
    {
        "id": "combat",
        "name": "Combat and damage",
        "description": "Damage, attackers and blockers attributed by Engine events.",
    },
    {
        "id": "opening",
        "name": "Mulligans",
        "description": "Explicit mulligan events, not guesses from hand size.",
    },
    {
        "id": "decisions",
        "name": "Decision activity",
        "description": "Confirmed SDK choices and unconfirmed or fallback feedback.",
    },
    {
        "id": "pace",
        "name": "Match length",
        "description": "Duration and observed turns across the match.",
    },
]
SNAPSHOTS = {
    "resources": {"cardsInHand", "lands", "untappedLands"},
    "board": {"life", "creatures", "nonlandPermanents"},
}
EVENTS = {
    "resources": {"draws", "manaProduced"},
    "combat": {"damage", "combatDamage", "nonCombatDamage", "attacks", "blocks"},
    "opening": {"mulligans"},
}
METRICS = {
    "cardsInHand": "Cards in hand",
    "lands": "Lands",
    "untappedLands": "Untapped lands",
    "life": "Life",
    "creatures": "Creatures",
    "nonlandPermanents": "Nonland permanents",
    "draws": "Observed cards drawn",
    "manaProduced": "Observed mana produced",
    "damage": "Observed damage dealt",
    "combatDamage": "Observed combat damage",
    "nonCombatDamage": "Observed noncombat damage",
    "attacks": "Observed attackers",
    "blocks": "Observed blockers",
    "mulligans": "Observed mulligans",
    "castChoices": "Confirmed cast choices",
    "passChoices": "Confirmed priority passes",
    "abilityChoices": "Confirmed ability choices",
    "unconfirmedChoices": "Unconfirmed choices",
    "durationSeconds": "Match duration (seconds)",
    "turnsObserved": "Total observed turns",
}
MAX_TURN_SAMPLES = 512


def number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def _snapshot(player: dict[str, Any]) -> dict[str, float]:
    values = {}
    life = number(player.get("life"))
    if life is not None:
        values["life"] = life
    if isinstance(player.get("hand"), list):
        values["cardsInHand"] = float(len(player["hand"]))
    battlefield = player.get("battlefield")
    if isinstance(battlefield, list):
        types = []
        for card in battlefield:
            if not isinstance(card, dict):
                break
            definition = card.get("definition")
            line = card.get("typeLine") or (
                definition.get("typeLine") if isinstance(definition, dict) else None
            )
            if not isinstance(line, str) or not line:
                break
            types.append(line.casefold().split())
        else:
            lands = [c for c, t in zip(battlefield, types, strict=False) if "land" in t]
            values.update(
                lands=float(len(lands)),
                creatures=float(sum("creature" in t for t in types)),
                nonlandPermanents=float(len(types) - len(lands)),
            )
            if all(isinstance(c.get("tapped"), bool) for c in lands):
                values["untappedLands"] = float(sum(not c["tapped"] for c in lands))
    return values


class ProfileCollector:
    """Observe online without retaining raw states or cumulative event lists."""

    def __init__(self, targets: list[dict[str, Any]]) -> None:
        self.profiles: dict[str, dict[str, Any]] = {
            str(t["playerId"]): {
                "groups": list(t["groups"]),
                "totals": {},
                "droppedTurnSamples": 0,
                "observedFrames": 0,
            }
            for t in targets
        }
        self.samples: dict[str, OrderedDict[tuple[int, int], dict[str, Any]]] = {
            p: OrderedDict() for p in self.profiles
        }
        self.round = 1
        self.turn = 0
        self.max_turns: dict[int, int] = {}
        self.event_cursors: dict[int, int] = {}
        self.controllers: OrderedDict[str, str] = OrderedDict()
        self.finished = False
        self.needs_events = any(set(p["groups"]) & set(EVENTS) for p in self.profiles.values())

    def _add(self, player: Any, metric: str, amount: float, group: str) -> None:
        profile = self.profiles.get(str(player))
        if profile and group in profile["groups"]:
            profile["totals"][metric] = profile["totals"].get(metric, 0) + amount

    def observe(self, frame: dict[str, Any]) -> None:
        if not self.profiles or self.finished:
            return
        state = frame.get("state")
        if not isinstance(state, dict):
            return
        turn = state.get("turnNumber")
        if type(turn) is not int or turn < 1:
            return
        match = frame.get("matchState")
        explicit = match.get("gameNumber") if isinstance(match, dict) else None
        if type(explicit) is int and explicit > 0:
            if explicit < self.round:
                return
            if explicit > self.round:
                self.turn = 0
                self.controllers.clear()
            self.round = explicit
        elif turn == 1 and self.turn > 1:
            self.round += 1
            self.turn = 0
            self.controllers.clear()
        if turn < self.turn:
            return
        self.turn = turn
        self.max_turns[self.round] = max(turn, self.max_turns.get(self.round, 0))
        players = state.get("players")
        for player in players if isinstance(players, list) else []:
            if not isinstance(player, dict):
                continue
            player_id = str(player.get("id", ""))
            for card in (
                player.get("battlefield", []) if isinstance(player.get("battlefield"), list) else []
            ):
                if isinstance(card, dict) and card.get("instanceId"):
                    self.controllers[str(card["instanceId"])] = str(
                        card.get("controllerId") or card.get("controller") or player_id
                    )
            profile = self.profiles.get(player_id)
            if profile is None:
                continue
            profile["observedFrames"] += 1
            allowed = set().union(*(SNAPSHOTS.get(g, set()) for g in profile["groups"]))
            observed = {k: v for k, v in _snapshot(player).items() if k in allowed}
            if observed:
                key = (self.round, turn)
                self.samples[player_id][key] = {
                    "round": self.round,
                    "turn": turn,
                    "values": observed,
                }
                if len(self.samples[player_id]) > MAX_TURN_SAMPLES:
                    self.samples[player_id].popitem(last=False)
                    profile["droppedTurnSamples"] += 1
        events = state.get("events")
        if self.needs_events and isinstance(events, list):
            for profile in self.profiles.values():
                for group in profile["groups"]:
                    for metric in EVENTS.get(group, set()):
                        profile["totals"].setdefault(metric, 0)
            cursor = self.event_cursors.get(self.round, -1)
            ordered = sorted(
                (
                    e
                    for e in events
                    if isinstance(e, dict)
                    and type(e.get("sequence")) is int
                    and e["sequence"] > cursor
                ),
                key=lambda e: e["sequence"],
            )
            for event in ordered:
                sequence = event["sequence"]
                if sequence <= self.event_cursors.get(self.round, -1):
                    continue
                self.event_cursors[self.round] = sequence
                detail = event.get("detail")
                detail = detail if isinstance(detail, dict) else {}
                kind, player = event.get("kind"), event.get("playerId")
                if (
                    kind
                    in {
                        "spellCast",
                        "permanentEnteredBattlefield",
                        "activatedAbilityPutOnStack",
                        "triggeredAbilityPutOnStack",
                    }
                    and event.get("cardInstanceId")
                    and player
                ):
                    self.controllers[str(event["cardInstanceId"])] = str(player)
                event_metric = {
                    "cardDrawn": ("draws", "resources"),
                    "mulliganTaken": ("mulligans", "opening"),
                    "attackerDeclared": ("attacks", "combat"),
                    "blockerDeclared": ("blocks", "combat"),
                }.get(str(kind))
                if event_metric:
                    self._add(player, event_metric[0], 1, event_metric[1])
                elif kind == "manaAdded":
                    mana = detail.get("mana")
                    if isinstance(mana, (list, str)):
                        self._add(
                            player,
                            "manaProduced",
                            float(len(mana) if isinstance(mana, list) else 1),
                            "resources",
                        )
                elif kind == "damageDealt":
                    controller = detail.get("sourceControllerId") or self.controllers.get(
                        str(detail.get("source", ""))
                    )
                    amount = number(detail.get("amount"))
                    if controller and amount is not None and amount >= 0:
                        self._add(controller, "damage", amount, "combat")
                        if type(detail.get("combat")) is bool:
                            self._add(
                                controller,
                                "combatDamage" if detail["combat"] else "nonCombatDamage",
                                amount,
                                "combat",
                            )
        while len(self.controllers) > 10000:
            self.controllers.popitem(last=False)

    def finish(
        self, duration: float | None, decisions: list[dict[str, Any]] | None = None
    ) -> dict[str, dict[str, Any]]:
        if self.finished:
            return self.profiles
        self.finished = True
        if not self.profiles:
            return {}
        seen: set[tuple[int, str]] = set()
        round_no, last_turn = 1, 0
        for frame in decisions or []:
            state = frame.get("state") or {}
            turn = state.get("turnNumber", last_turn)
            explicit = (frame.get("matchState") or {}).get("gameNumber")
            if type(explicit) is int:
                round_no = max(round_no, explicit)
            elif turn == 1 and last_turn > 1:
                round_no += 1
            last_turn = turn
            decision = frame.get("decision") or {}
            feedback = frame.get("agentDecision")
            if not isinstance(feedback, dict):
                continue  # Polling a pending prompt is not a played SDK choice.
            key = (round_no, str(decision.get("id", "")))
            if not key[1] or key in seen:
                continue
            seen.add(key)
            player = decision.get("playerId")
            if feedback.get("resolution") != "confirmed":
                self._add(player, "unconfirmedChoices", 1, "decisions")
            else:
                action = frame.get("selectedAction") or {}
                metric = {
                    "castSpell": "castChoices",
                    "passPriority": "passChoices",
                    "activateAbility": "abilityChoices",
                }.get(str(action.get("kind", "")))
                if metric:
                    self._add(player, metric, 1, "decisions")
        for player, profile in self.profiles.items():
            profile["turns"] = list(self.samples[player].values())
            if "pace" in profile["groups"]:
                profile["totals"]["turnsObserved"] = sum(self.max_turns.values())
                if number(duration) is not None:
                    profile["totals"]["durationSeconds"] = max(0, float(duration or 0))
        return self.profiles


def collect_profiles(
    frames: list[dict[str, Any]], targets: list[dict[str, Any]], duration: float | None
) -> dict[str, dict[str, Any]]:
    collector = ProfileCollector(targets)
    for frame in frames:
        if isinstance(frame, dict):
            collector.observe(frame)
    return collector.finish(duration, frames)
