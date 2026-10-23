"""Durable local AI tournaments. Scheduling is independent of Engine execution.

These are recreational tournaments, not a certified WER/EventLink Swiss pairing
implementation. Swiss minimizes repeats, then score gaps using blossom matching.
"""

from __future__ import annotations

import copy
import json
import math
import random
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import networkx as nx  # type: ignore[import-untyped]

from .commander_season import SCHEDULE_VERSION, required_match_step, season_tables
from .local_seats import competition_player_id, local_player_ids
from .play_settings import local_play_settings

FORMATS = {
    "single",
    "double",
    "round_robin",
    "round_robin_cut",
    "swiss",
    "loss_limit",
    "groups_cut",
    "random_season",
    "random_season_cut",
}
PLAYOFF_STAGES = {"winners", "losers", "final"}


def playoff_seed(c: dict[str, Any], player: str) -> int:
    seeds = c.get("playoffSeeds", [])
    if player in seeds:
        return int(seeds.index(player)) + 1
    return int(next(p["seed"] for p in c["entrants"] if p["id"] == player))


def start_playoffs(c: dict[str, Any], players: list[str], *, double: bool = False) -> None:
    if c["format"] == "commander":
        c["reseedingPlayoffs"] = True
        tables = len(players) // 4
        groups: list[list[str]] = [[] for _ in range(tables)]
        for i, player in enumerate(players):
            row, column = divmod(i, tables)
            groups[column if row % 2 == 0 else tables - 1 - column].append(player)
        for group in groups:
            key = add_match(c, "winners", 1, group)
            if tables == 1:
                c["finalMatch"] = key
        return
    if not c["parameters"].get("reseed", False) or double:
        elimination(c, players, double=double)
        return
    c["reseedingPlayoffs"] = True
    size = 1 << (len(players) - 1).bit_length()
    slots = [players[s - 1] if s <= len(players) else None for s in seeded_slots(size)]
    for i in range(0, size, 2):
        match_id = add_match(c, "winners", 1, slots[i : i + 2])
        if size == 2:
            c["finalMatch"] = match_id


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def integer(value: Any, label: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{label}: choose a whole number between {low} and {high}.")
    return int(value)


def seeded_slots(count: int) -> list[int]:
    slots = [1, 2]
    while len(slots) < count:
        size = len(slots) * 2
        slots = [item for seed in slots for item in (seed, size + 1 - seed)]
    return slots


def add_match(c: dict[str, Any], stage: str, round_no: int, sources: list[Any]) -> str:
    match_id = f"m{len(c['matches']) + 1}"
    c["matches"].append(
        {
            "id": match_id,
            "stage": stage,
            "round": round_no,
            "sources": sources,
            "players": [],
            "status": "pending",
            "attempts": [],
            "winner": None,
            "loser": None,
            "score": None,
            "replayId": None,
        }
    )
    return match_id


def ref(match_id: str, outcome: str = "winner") -> dict[str, str]:
    return {"match": match_id, "outcome": outcome}


def elimination(c: dict[str, Any], players: list[str], *, double: bool = False) -> None:
    size = 1 << (len(players) - 1).bit_length()
    slots: list[Any] = [players[s - 1] if s <= len(players) else None for s in seeded_slots(size)]
    winners: list[list[str]] = []
    round_no = 1
    while len(slots) > 1:
        matches = [
            add_match(c, "winners", round_no, slots[i : i + 2]) for i in range(0, len(slots), 2)
        ]
        winners.append(matches)
        slots = [ref(match_id) for match_id in matches]
        round_no += 1
    if not double:
        c["finalMatch"] = winners[-1][0]
        return
    # Lower bracket alternates consolidation and insertion of upper losers.
    lower: list[str] = []
    if len(winners) > 1:
        lower = [
            add_match(c, "losers", 1, [ref(a, "loser"), ref(b, "loser")])
            for a, b in zip(winners[0][::2], winners[0][1::2], strict=False)
        ]
        for wr in range(1, len(winners)):
            incoming = list(reversed(winners[wr]))
            lower = [
                add_match(c, "losers", 2 * wr, [ref(a), ref(b, "loser")])
                for a, b in zip(lower, incoming, strict=False)
            ]
            if len(lower) > 1:
                lower = [
                    add_match(c, "losers", 2 * wr + 1, [ref(a), ref(b)])
                    for a, b in zip(lower[::2], lower[1::2], strict=False)
                ]
    upper = winners[-1][0]
    lower_source = ref(lower[0]) if lower else ref(upper, "loser")
    first = add_match(c, "final", 1, [ref(upper), lower_source])
    reset = add_match(c, "final", 2, [ref(upper), lower_source])
    c["matches"][-1]["resetAfter"] = first
    c["finalMatch"] = reset


def round_robin(c: dict[str, Any], players: list[str], *, group: str | None = None) -> None:
    if c["format"] == "commander":
        for leg in range(c["parameters"]["legs"]):
            key = add_match(c, "groups", leg + 1, players)
            next(m for m in c["matches"] if m["id"] == key)["group"] = group
        return
    ring: list[Any] = list(players)
    if len(ring) % 2:
        ring.append(None)
    for leg in range(c["parameters"]["legs"]):
        for r in range(len(ring) - 1):
            for i in range(len(ring) // 2):
                pair = [ring[i], ring[-i - 1]]
                if (r + leg + i) % 2:
                    pair.reverse()
                match_id = add_match(
                    c, "groups" if group else "round_robin", leg * (len(ring) - 1) + r + 1, pair
                )
                if group:
                    next(m for m in c["matches"] if m["id"] == match_id)["group"] = group
            ring = [ring[0], ring[-1], *ring[1:-1]]


def standings(
    c: dict[str, Any], stage: str | None = None, *, group: str | None = None
) -> list[dict[str, Any]]:
    group_players = next((g["players"] for g in c.get("groups", []) if g["id"] == group), None)
    rows = {
        p["id"]: {
            "id": p["id"],
            "name": p["name"],
            "seed": p["seed"],
            "played": 0,
            "wins": 0,
            "losses": 0,
            "draws": 0,
            "byes": 0,
            "points": 0,
            "gameWins": 0,
            "gameLosses": 0,
            "opponents": [],
        }
        for p in c["entrants"]
        if group_players is None or p["id"] in group_players
    }
    for m in c["matches"]:
        if (
            m["status"] not in {"done", "bye"}
            or (stage and m["stage"] != stage)
            or (group and m.get("group") != group)
        ):
            continue
        players = [p for p in m["players"] if p]
        # Empty bracket cells and skipped reset finals are not tournament byes.
        if len(players) == 1:
            if m["stage"] in {"round_robin", "swiss", "loss_limit", "groups", "season"}:
                row = rows[players[0]]
                row["byes"] += 1
                row["points"] += 3
            continue
        if len(players) < 2:
            continue
        for index, player in enumerate(players):
            row = rows[player]
            row["played"] += 1
            row["opponents"].extend(p for p in players if p != player)
            result = "draws" if not m["winner"] else "wins" if m["winner"] == player else "losses"
            row[result] += 1
            row["points"] += 3 if result == "wins" else 1 if result == "draws" else 0
            if m.get("score") is not None:
                row["gameWins"] += m["score"][index]
                row["gameLosses"] += sum(value for i, value in enumerate(m["score"]) if i != index)
    for row in rows.values():
        played = row["played"] + row["byes"]
        row["mwp"] = max(0.33, row["points"] / (3 * played)) if played else 0.33
        bye_game_wins = 1 if c["format"] == "commander" else 2
        gw, gl = row["gameWins"] + bye_game_wins * row["byes"], row["gameLosses"]
        row["gwp"] = max(0.33, gw / (gw + gl)) if gw + gl else 0.33
    for row in rows.values():
        opponents = row.pop("opponents")
        row["omw"] = sum(rows[p]["mwp"] for p in opponents) / len(opponents) if opponents else 0.33
        row["ogw"] = sum(rows[p]["gwp"] for p in opponents) / len(opponents) if opponents else 0.33
    ranked_rows = sorted(
        rows.values(), key=lambda r: (-r["points"], -r["omw"], -r["gwp"], -r["ogw"], r["seed"])
    )
    for rank, row in enumerate(ranked_rows, 1):
        row["rank"] = rank
    return ranked_rows


def qualification(c: dict[str, Any]) -> list[dict[str, Any]]:
    if c["type"] == "groups_cut":
        rows = []
        for group in c["groups"]:
            for row in standings(c, "groups", group=group["id"])[
                : c["parameters"]["qualifiersPerGroup"]
            ]:
                rows.append({**row, "group": group["name"], "groupRank": row["rank"]})
        rows.sort(
            key=lambda r: (r["groupRank"], -r["mwp"], -r["omw"], -r["gwp"], -r["ogw"], r["seed"])
        )
        return [{**row, "rank": i} for i, row in enumerate(rows, 1)]
    stage = "season" if c["type"].startswith("random_season") else "round_robin"
    return standings(c, stage)[: c["parameters"]["cut"]]


def random_season_round(c: dict[str, Any], players: list[str], number: int) -> None:
    """Random pairings independent of score; minimize repeats and balance byes."""
    rng = random.Random(c["playSettings"]["firstSeed"] + number * 104729)
    pool = list(players)
    rng.shuffle(pool)
    if c["format"] == "commander":
        previous_tables = [set(m["players"]) for m in c["matches"] if m["stage"] == "season"]
        while pool:
            commander_table = [pool.pop()]
            while len(commander_table) < 4:
                candidate = min(
                    pool,
                    key=lambda p: sum(
                        p in old and q in old for old in previous_tables for q in commander_table
                    ),
                )
                commander_table.append(candidate)
                pool.remove(candidate)
            add_match(c, "season", number, commander_table)
        return
    table = {r["id"]: r for r in standings(c, "season")}
    if len(pool) % 2:
        bye = min(pool, key=lambda p: table[p]["byes"])
        add_match(c, "season", number, [bye, None])
        pool.remove(bye)
    previous: dict[frozenset[str], int] = {}
    for m in c["matches"]:
        if m["stage"] == "season" and m["status"] == "done":
            key = frozenset(m["players"])
            previous[key] = previous.get(key, 0) + 1
    graph = nx.Graph()
    graph.add_nodes_from(pool)
    for i, a in enumerate(pool):
        for b in pool[i + 1 :]:
            weight = previous.get(frozenset((a, b)), 0) * (len(pool) + 1) * 1000001
            graph.add_edge(a, b, weight=weight + rng.randrange(1000000))
    pairs = sorted(
        (sorted(pair, key=pool.index) for pair in nx.min_weight_matching(graph)),
        key=lambda pair: pool.index(pair[0]),
    )
    for pair in pairs:
        match_key = add_match(c, "season", number, pair)
        if previous.get(frozenset(pair)):
            next(m for m in c["matches"] if m["id"] == match_key)["rematch"] = True


def paired_round(c: dict[str, Any], stage: str, players: list[str], number: int) -> None:
    table = {r["id"]: r for r in standings(c, stage)}
    ranked = sorted(players, key=lambda p: (-(table[p]["points"]), table[p]["seed"]))
    if len(ranked) % 2:
        bye = min(ranked, key=lambda p: (table[p]["byes"], table[p]["points"], -table[p]["seed"]))
        add_match(c, stage, number, [bye, None])
        ranked.remove(bye)
    graph = nx.Graph()
    graph.add_nodes_from(ranked)
    previous: dict[frozenset[str], int] = {}
    for m in c["matches"]:
        if m["stage"] == stage and m["status"] == "done":
            key = frozenset(m["players"])
            previous[key] = previous.get(key, 0) + 1
    # Integer weights: minimizing repeat count dominates every score-gap cost.
    scale = (len(ranked) + 1) ** 3
    repeat_cost = scale * (3 * max(1, number) + 2) * (len(ranked) + 1)
    for i, a in enumerate(ranked):
        for j, b in enumerate(ranked[i + 1 :], i + 1):
            weight = previous.get(frozenset((a, b)), 0) * repeat_cost
            weight += abs(table[a]["points"] - table[b]["points"]) * scale + abs(i - j)
            graph.add_edge(a, b, weight=weight)
    pairs = sorted(
        (sorted(pair, key=ranked.index) for pair in nx.min_weight_matching(graph)),
        key=lambda pair: ranked.index(pair[0]),
    )
    for pair in pairs:
        match_id = add_match(c, stage, number, pair)
        if previous.get(frozenset(pair)):
            next(m for m in c["matches"] if m["id"] == match_id)["rematch"] = True


def create_competition(payload: dict[str, Any]) -> dict[str, Any]:
    kind = payload.get("type", "single")
    if not isinstance(kind, str) or kind not in FORMATS:
        raise ValueError("Choose a supported competition format.")
    game_format = payload.get("format", "legacy")
    if game_format not in {"legacy", "commander"}:
        raise ValueError("Choose Legacy or Commander.")
    raw = payload.get("entrants")
    if not isinstance(raw, list) or len(raw) < 2:
        raise ValueError("Add at least two entrants.")
    humans = sum(isinstance(p, dict) and p.get("kind") == "human" for p in raw)
    settings = local_play_settings(
        payload, seats=4 if game_format == "commander" else 2, has_human=humans > 0
    )
    entrants = []
    for i, p in enumerate(raw, 1):
        if not isinstance(p, dict) or p.get("kind", "agent") not in {"agent", "human"}:
            raise ValueError("Choose a player or an AI for each entrant.")
        human = p.get("kind") == "human"
        if not human and (
            not isinstance(p.get("agent_id"), str) or not p["agent_id"] or p["agent_id"] == "random"
        ):
            raise ValueError("Each entrant needs a specific AI agent; its deck may be random.")
        if human and (
            not isinstance(p.get("deck_id"), str) or not p["deck_id"] or p["deck_id"] == "random"
        ):
            raise ValueError("Choose a local or imported deck for the player.")
        entrant_name = str(p.get("name") or f"Entrant {i}")[:100].strip() or f"Entrant {i}"
        if human and not entrant_name.isprintable():
            raise ValueError("Choose a single-line name for each local player.")
        entrants.append(
            {
                "id": f"p{i}",
                "name": entrant_name,
                "kind": "human" if human else "agent",
                "agent_id": "local-player" if human else p["agent_id"],
                "deck_id": str(p.get("deck_id") or "random"),
            }
        )
    if payload.get("seeding", "random") not in ("random", "registration"):
        raise ValueError("Seeding must be random or registration order.")
    if payload.get("seeding", "random") == "random":
        random.Random(settings["firstSeed"]).shuffle(entrants)
    for seed, p in enumerate(entrants, 1):
        p["seed"] = seed
    n = len(entrants)
    parameters: dict[str, Any] = {
        "legs": integer(payload.get("legs", 1), "Round-robin legs", 1, 10),
        "rounds": integer(
            payload.get("rounds", math.ceil(math.log2(n))),
            "Season/Swiss rounds",
            1,
            100 if kind.startswith("random_season") else n,
        ),
        "losses": integer(payload.get("losses", 3), "Losses before elimination", 1, 100),
        "cut": integer(payload.get("cut", min(n, 4)), "Playoff qualifiers", 2, n),
        "playoffWins": integer(
            payload.get("playoff_wins", 1), "Playoff match wins to advance", 1, 10
        ),
    }
    for key in ("reseed", "seed_advantage"):
        if type(payload.get(key, False)) is not bool:
            raise ValueError(f"{key} must be true or false.")
    parameters.update(
        reseed=payload.get("reseed", False), seedAdvantage=payload.get("seed_advantage", False)
    )

    def power_four(value: int) -> bool:
        return value >= 4 and value & (value - 1) == 0 and (value.bit_length() - 1) % 2 == 0

    if game_format == "commander":
        if kind not in {"single", "groups_cut", "random_season", "random_season_cut"}:
            raise ValueError(
                "Commander supports four-player elimination, groups, or random seasons."
            )
        if n < 4:
            raise ValueError(
                "Commander needs at least four entrants; every table has four players."
            )
        if not kind.startswith("random_season") and n % 4:
            raise ValueError(
                "Commander elimination/groups need complete four-player tables. Use a random season for other field sizes."
            )
        if kind.startswith("random_season"):
            allow_byes = payload.get("allow_season_byes", False)
            if type(allow_byes) is not bool:
                raise ValueError("Allow season byes must be true or false.")
            if "rounds" not in payload:
                step = required_match_step(n)
                parameters["rounds"] = math.ceil(parameters["rounds"] / step) * step
            parameters.update(
                seasonGamesPerPlayer=parameters["rounds"],
                seasonAllowByes=allow_byes,
                seasonScheduleVersion=SCHEDULE_VERSION,
            )
        if kind == "single" and not power_four(n):
            raise ValueError(
                "Commander elimination needs 4, 16, 64, ... entrants. Use a season with a cut for other fields."
            )
        if kind == "random_season_cut" and not power_four(parameters["cut"]):
            raise ValueError("Commander playoffs need 4, 16, 64, ... qualifiers.")
        if kind != "random_season":
            parameters["reseed"] = True
    if kind == "double" and parameters["reseed"]:
        raise ValueError(
            "Double elimination uses a fixed winners/losers bracket; disable reseeding."
        )
    if kind == "groups_cut":
        if n < 4:
            raise ValueError("Group playoffs need at least four entrants, two per group.")
        parameters["groups"] = integer(payload.get("groups", 2), "Groups", 2, n // 2)
        parameters["qualifiersPerGroup"] = integer(
            payload.get("qualifiers_per_group", 1),
            "Qualifiers per group",
            1,
            n // parameters["groups"],
        )
        if game_format == "commander" and (
            parameters["groups"] * 4 != n
            or not power_four(parameters["groups"] * parameters["qualifiersPerGroup"])
        ):
            raise ValueError(
                "Commander groups have four entrants each; qualify 4, 16, 64, ... players in total."
            )
    c: dict[str, Any] = {
        "version": "deepdeck-local-competition/v1",
        "id": uuid.uuid4().hex,
        "name": str(payload.get("name") or "Local competition")[:120],
        "type": kind,
        "format": game_format,
        "status": "running",
        "createdAt": now(),
        "updatedAt": now(),
        "entrants": entrants,
        "parameters": parameters,
        "matches": [],
        "champion": None,
        "activeJobId": None,
        "activeMatchId": None,
        "error": None,
        "playSettings": settings,
        "engineUrl": str(payload.get("engine_url") or "http://127.0.0.1:8787"),
    }
    for entrant in entrants:
        if entrant["kind"] == "human":
            entrant["player_id"] = competition_player_id(c["id"], entrant["id"])
    from .jobs import is_loopback_url

    if not is_loopback_url(c["engineUrl"]):
        raise ValueError("Competitions require your local Engine.")
    players = [p["id"] for p in entrants]
    if kind in {"single", "double"}:
        start_playoffs(c, players, double=kind == "double")
    elif kind in {"round_robin", "round_robin_cut"}:
        round_robin(c, players)
    elif kind == "groups_cut":
        groups: list[dict[str, Any]] = [
            {"id": f"g{i + 1}", "name": f"Group {i + 1}", "players": []}
            for i in range(parameters["groups"])
        ]
        for i, player in enumerate(players):
            row, column = divmod(i, len(groups))
            groups[column if row % 2 == 0 else len(groups) - 1 - column]["players"].append(player)
        c["groups"] = groups
        for group in groups:
            round_robin(c, group["players"], group=group["id"])
    elif kind.startswith("random_season"):
        if parameters.get("seasonScheduleVersion") == SCHEDULE_VERSION:
            pods, byes = season_tables(
                players,
                parameters["rounds"],
                settings["firstSeed"],
                allow_byes=parameters["seasonAllowByes"],
            )
            for number, pod in pods:
                add_match(c, "season", number, pod)
            for number, player in byes:
                key = add_match(c, "season", number, [player, None])
                next(m for m in c["matches"] if m["id"] == key)["seasonBye"] = True
        else:
            random_season_round(c, players, 1)
    else:
        paired_round(c, kind, players, 1)
    advance(c)
    return c


def advance(c: dict[str, Any]) -> None:
    """Propagate byes/dependencies, then generate the next adaptive round."""
    by_id = {m["id"]: m for m in c["matches"]}
    for m in c["matches"]:
        if m["status"] != "pending":
            continue
        reset = m.get("resetAfter")
        if reset:
            first = by_id[reset]
            if first["status"] not in {"done", "bye"}:
                continue
            if first["winner"] == first["players"][0]:
                m.update(status="skipped", winner=first["winner"], players=first["players"])
                continue
        players: list[Any] = []
        for source in m["sources"]:
            if isinstance(source, dict):
                prior = by_id[source["match"]]
                if prior["status"] not in {"done", "bye"}:
                    break
                players.append(prior[source["outcome"]])
            else:
                players.append(source)
        else:
            m["players"] = players
            present = [p for p in players if p]
            if len(present) < 2:
                m.update(status="bye", winner=present[0] if present else None)
            else:
                m["status"] = "ready"
    if any(
        m["status"] in {"pending", "ready", "playing", "dispatching", "error"} for m in c["matches"]
    ):
        return
    kind = c["type"]
    table = standings(c)
    if (
        kind.startswith("random_season")
        and not c.get("playoffSeeds")
        and c["parameters"].get("seasonScheduleVersion") != SCHEDULE_VERSION
    ):
        round_no = max(m["round"] for m in c["matches"] if m["stage"] == "season")
        if round_no < c["parameters"]["rounds"]:
            random_season_round(c, [p["id"] for p in c["entrants"]], round_no + 1)
            advance(c)
            return
    if kind in {"round_robin_cut", "groups_cut", "random_season_cut"} and not c.get("playoffSeeds"):
        qualifiers = [r["id"] for r in qualification(c)]
        c["playoffSeeds"] = qualifiers
        start_playoffs(c, qualifiers)
        advance(c)
        return
    if c.get("reseedingPlayoffs"):
        last_round = max(m["round"] for m in c["matches"] if m["stage"] == "winners")
        survivors = [
            m["winner"]
            for m in c["matches"]
            if m["stage"] == "winners" and m["round"] == last_round and m["winner"]
        ]
        if len(survivors) > 1:
            survivors.sort(key=lambda p: playoff_seed(c, p))
            size = 4 if c["format"] == "commander" else 2
            pairs = ([survivors[i], survivors[-i - 1]] for i in range(len(survivors) // 2))
            if size == 4:
                count = len(survivors) // 4
                commander_tables: list[list[str]] = [[] for _ in range(count)]
                for i, player in enumerate(survivors):
                    row, column = divmod(i, count)
                    commander_tables[column if row % 2 == 0 else count - 1 - column].append(player)
                next_tables = commander_tables
            else:
                next_tables = list(pairs)
            for players in next_tables:
                key = add_match(c, "winners", last_round + 1, players)
                if len(survivors) == size:
                    c["finalMatch"] = key
            advance(c)
            return
    if kind in {"swiss", "loss_limit"}:
        round_no = max(m["round"] for m in c["matches"])
        players = [
            r["id"]
            for r in table
            if kind != "loss_limit" or r["losses"] < c["parameters"]["losses"]
        ]
        if (kind == "swiss" and round_no < c["parameters"]["rounds"]) or (
            kind == "loss_limit" and len(players) > 1
        ):
            paired_round(c, kind, players, round_no + 1)
            advance(c)
            return
        c["champion"] = players[0] if kind == "loss_limit" else table[0]["id"]
    elif c.get("finalMatch"):
        c["champion"] = next(m["winner"] for m in c["matches"] if m["id"] == c["finalMatch"])
    else:
        c["champion"] = table[0]["id"]
    c["status"] = "completed"
    c["finishedAt"] = now()


def record_result(c: dict[str, Any], match_id: str, result: dict[str, Any]) -> None:
    match = next(m for m in c["matches"] if m["id"] == match_id)
    if match["status"] in {"done", "bye", "skipped"}:
        raise ValueError("This match already has a result.")
    winner = result.get("winner")
    seat_ids = local_player_ids(
        next(p for p in c["entrants"] if p["id"] == player).get("kind", "agent")
        for player in match["players"]
    )
    if winner is not None and winner not in seat_ids:
        raise ValueError("The Engine reported an unknown winner.")
    match["attempts"].append(
        copy.deepcopy(
            {
                key: result[key]
                for key in (
                    "winner",
                    "replayId",
                    "winsByPlayerId",
                    "decks",
                    "deckNames",
                    "agents",
                    "seed",
                    "replayStorageError",
                )
                if key in result
            }
        )
    )
    if winner is None and c["type"] not in {
        "round_robin",
        "round_robin_cut",
        "swiss",
        "groups_cut",
        "random_season",
        "random_season_cut",
    }:
        raise ValueError("An elimination match ended in a draw. Retry it to determine a winner.")
    if winner is None and match["stage"] in {"winners", "losers", "final"}:
        raise ValueError("The playoff match ended in a draw. Retry it to determine a winner.")
    score = result.get("winsByPlayerId")
    scores = [score.get(key, 0) for key in seat_ids] if isinstance(score, dict) else None
    winner_index = seat_ids.index(winner) if winner else None
    won = match["players"][winner_index] if winner_index is not None else None
    lost = (
        next((p for p in match["players"] if p != won), None)
        if len(match["players"]) == 2 and won
        else None
    )
    required = c["parameters"].get("playoffWins", 1) if match["stage"] in PLAYOFF_STAGES else 1
    if required > 1:
        wins = match.setdefault("seriesWins", [0] * len(match["players"]))
        assert winner_index is not None
        wins[winner_index] += 1
        match.setdefault("seriesGames", []).append(
            {
                "winner": won,
                "score": scores,
                "replayId": result.get("replayId"),
                "deckNames": result.get("deckNames", []),
                "finishedAt": now(),
            }
        )
        prior = match.get("score")
        scores = (
            [prior[i] + scores[i] for i in range(len(match["players"]))]
            if prior is not None and scores is not None
            else scores
            if len(match["seriesGames"]) == 1
            else None
        )
        if wins[winner_index] < required:
            match.update(
                status="ready",
                score=scores,
                replayId=result.get("replayId"),
                deckNames=result.get("deckNames", []),
            )
            return
    match.update(
        status="done",
        winner=won,
        loser=lost,
        score=scores,
        replayId=result.get("replayId"),
        deckNames=result.get("deckNames", []),
        deckIds=result.get("decks", []),
        finishedAt=now(),
    )
    advance(c)


class CompetitionService:
    """One serial dispatcher; state persists before and after every child job.

    Failed/stopped child jobs pause the organizer. Explicit resume retries only
    the unfinished match. No fabricated forfeits and no replay duplication.
    """

    def __init__(self, root: Path, manager: Any) -> None:
        self.root, self.manager = root, manager
        self.path = root / ".deepdeck" / "learner.db"
        self.lock = threading.RLock()
        self.thread: threading.Thread | None = None
        with sqlite3.connect(self.path) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS local_competitions (id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
            )
            # Never silently relaunch a tournament on application startup.
            for key, value in db.execute("SELECT id, payload FROM local_competitions").fetchall():
                c = json.loads(value)
                if c["status"] in {"running", "pausing"}:
                    c.update(
                        status="paused",
                        error="Application restarted. Resume to reconnect or retry the unfinished match.",
                    )
                    db.execute(
                        "UPDATE local_competitions SET payload=? WHERE id=?", (json.dumps(c), key)
                    )

    def _save(self, c: dict[str, Any]) -> None:
        c["updatedAt"] = now()
        c.pop("standings", None)
        c.pop("qualifyingStandings", None)
        c.pop("groupStandings", None)
        with sqlite3.connect(self.path) as db:
            db.execute(
                "INSERT OR REPLACE INTO local_competitions VALUES (?, ?)", (c["id"], json.dumps(c))
            )

    def get(self, key: str) -> dict[str, Any]:
        with sqlite3.connect(self.path) as db:
            row = db.execute("SELECT payload FROM local_competitions WHERE id=?", (key,)).fetchone()
        if row is None:
            raise KeyError("Competition not found.")
        c: dict[str, Any] = json.loads(row[0])
        # Hydrate old missing recording links without changing outcomes or
        # persisting a read. Current workers report recordings independently.
        from .competition_recordings import recover_attempt_recording

        for match in c.get("matches", []):
            for attempt in match.get("attempts", []):
                if attempt.get("error") and not attempt.get("replayId") and attempt.get("jobId"):
                    recording = recover_attempt_recording(
                        self.root, c["id"], match["id"], self.manager.get(attempt["jobId"])
                    )
                    if recording:
                        attempt["replayId"] = recording
        c["standings"] = standings(c)
        c["qualifyingStandings"] = (
            qualification(c)
            if c["type"] in {"round_robin_cut", "groups_cut", "random_season_cut"}
            else []
        )
        c["groupStandings"] = [
            {**g, "standings": standings(c, "groups", group=g["id"])} for g in c.get("groups", [])
        ]
        return c

    def list(self) -> list[dict[str, Any]]:
        with sqlite3.connect(self.path) as db:
            values = [
                json.loads(row[0]) for row in db.execute("SELECT payload FROM local_competitions")
            ]
        return sorted(values, key=lambda c: c["createdAt"], reverse=True)

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        from .agent_studio import find_agent

        c = create_competition(payload)
        for p in c["entrants"]:
            if p.get("kind") == "human":
                from .catalogs import downloaded_decks

                if not any(
                    d["id"] == p["deck_id"] for d in downloaded_decks(self.root, c["format"])
                ):
                    raise ValueError(f"{p['name']}: choose a local or imported {c['format']} deck.")
                continue
            _, policy = find_agent(self.root, p["agent_id"])
            if policy.get("format") != c["format"]:
                raise ValueError("Every agent must support the competition's game format.")
            allowed = policy.get("deckVersionIds", [])
            if not allowed or (p["deck_id"] != "random" and p["deck_id"] not in allowed):
                raise ValueError(f"{p['name']}: choose a deck mastered by this agent.")
            p["agentName"] = policy.get("name", p["name"])
            p["initialRevision"] = policy.get("revision")
        with self.lock:
            self._save(c)
            self._start()
        return c

    def control(self, key: str, action: str) -> dict[str, Any]:
        with self.lock:
            c = self.get(key)
            if action == "pause" and c["status"] == "running":
                c["status"] = "pausing" if c["activeJobId"] else "paused"
            elif action == "resume" and c["status"] == "paused":
                c.update(status="running", error=None)
                for m in c["matches"]:
                    if m["status"] == "dispatching" and m.get("dispatchId"):
                        # Recover the narrow crash window between persisting a
                        # JobManager child and attaching its id to this event.
                        with sqlite3.connect(self.path) as db:
                            row = db.execute(
                                "SELECT payload FROM jobs WHERE json_extract(payload, '$.details.competitionDispatchId')=?",
                                (m["dispatchId"],),
                            ).fetchone()
                        if row:
                            job = json.loads(row[0])
                            m.update(status="playing", jobId=job["id"])
                            c.update(activeJobId=job["id"], activeMatchId=m["id"])
                            continue
                    if m["status"] in {"error", "dispatching"}:
                        m["status"] = "ready"
            else:
                raise ValueError("This competition cannot perform that action right now.")
            self._save(c)
            self._start()
            return self.get(key)

    def _start(self) -> None:
        if self.thread is None or not self.thread.is_alive():
            self.thread = threading.Thread(target=self._run, daemon=True, name="local-competitions")
            self.thread.start()

    def _run(self) -> None:
        while True:
            with self.lock:
                active = [c for c in self.list() if c["status"] in {"running", "pausing"}]
                if not active:
                    self.thread = None
                    return
                for c in active:
                    self.tick(c)
            threading.Event().wait(1)

    def tick(self, c: dict[str, Any]) -> None:
        match = None
        try:
            if c["activeJobId"]:
                match = next(m for m in c["matches"] if m["id"] == c["activeMatchId"])
                job = self.manager.get(c["activeJobId"])
                if job and job["status"] in {"running", "queued"}:
                    started = job.get("started_at")
                    if started:
                        elapsed = (
                            datetime.now(timezone.utc) - datetime.fromisoformat(started)
                        ).total_seconds()
                        limit = ((job.get("details") or {}).get("playSettings") or {}).get(
                            "matchTimeoutSeconds", c["playSettings"]["matchTimeoutSeconds"]
                        )
                        if limit is not None and elapsed > limit + 60:
                            # A synchronous policy can starve the worker's own
                            # asyncio timeout. Enforce a separate parent deadline.
                            c.update(activeJobId=None, activeMatchId=None)
                            self.manager.stop(job["id"])
                            match["attempts"].append(
                                {
                                    "jobId": job["id"],
                                    "error": "Worker exceeded the match safety limit and shutdown grace.",
                                }
                            )
                            raise ValueError(
                                "Worker exceeded the match safety limit. Competition paused without assigning a loss."
                            )
                    return
                c.update(activeJobId=None, activeMatchId=None)
                progress = (job or {}).get("details") or {}
                if not job or job["status"] != "completed" or progress.get("completed") != 1:
                    logs = (job or {}).get("logs") or [
                        "The local match stopped without a completed result."
                    ]
                    match["attempts"].append(
                        {
                            "jobId": (job or {}).get("id"),
                            "error": logs[-1],
                            "replayId": progress.get("replayId"),
                        }
                    )
                    raise ValueError(logs[-1])
                record_result(c, match["id"], progress)
            if c["status"] == "pausing":
                c["status"] = "paused"
            if c["status"] != "running":
                self._save(c)
                return
            advance(c)
            if c["status"] == "completed":
                self._save(c)
                return
            match = next(m for m in c["matches"] if m["status"] == "ready")
            people = {p["id"]: p for p in c["entrants"]}
            seats = [
                {
                    "kind": people[p].get("kind", "agent"),
                    "agent_id": people[p]["agent_id"],
                    "deck_id": people[p]["deck_id"],
                    **(
                        {
                            "player_id": competition_player_id(c["id"], p),
                            "player_name": people[p]["name"],
                        }
                        if people[p].get("kind") == "human"
                        else {}
                    ),
                }
                for p in match["players"]
            ]
            has_human = any(s["kind"] == "human" for s in seats)
            settings = c["playSettings"]
            # Stable per-match/attempt seed, including deterministic explicit seed 0.
            index = c["matches"].index(match)
            seed = (settings["firstSeed"] + index * 1009 + len(match["attempts"])) % (2**31)
            opening = (
                (index % len(seats)) + 1
                if settings["startingSeat"] == "rotate"
                else settings["startingSeat"]
            )
            if match["stage"] in PLAYOFF_STAGES and c["parameters"].get("seedAdvantage", False):
                best = min(range(len(seats)), key=lambda i: playoff_seed(c, match["players"][i]))
                opening = (best + len(match.get("seriesGames", []))) % len(seats) + 1
            match.update(status="dispatching", seed=seed, dispatchId=uuid.uuid4().hex)
            self._save(c)
            job = self.manager.create(
                {
                    "kind": "playtest.table" if has_human else "playtest.batch",
                    "format": c["format"],
                    "games": 1,
                    "seats": seats,
                    "engine_url": c["engineUrl"],
                    "seed": seed,
                    "starting_seat": opening,
                    "ai_speed": settings["aiSpeed"],
                    "human_decision_timeout_seconds": settings["humanDecisionTimeoutSeconds"]
                    if has_human
                    else None,
                    "max_turns": settings["maxTurns"],
                    "match_timeout_seconds": settings["matchTimeoutSeconds"]
                    if has_human
                    else settings["matchTimeoutSeconds"] or 600,
                    "competition_id": c["id"],
                    "competition_match_id": match["id"],
                    "competition_dispatch_id": match["dispatchId"],
                }
            )
            match.update(status="playing", jobId=job["id"], seed=seed)
            c.update(activeJobId=job["id"], activeMatchId=match["id"])
            self._save(c)
        except Exception as error:
            if match and match["status"] != "done":
                match["status"] = "error"
            c.update(status="paused", error=str(error))
            self._save(c)
