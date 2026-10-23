"""Balanced four-seat seasons, independent of game execution and results."""

from __future__ import annotations

import heapq
import math
import random
from collections import Counter
from itertools import combinations

SCHEDULE_VERSION = "commander-per-player/v1"


def required_match_step(players: int) -> int:
    """N * M must be divisible by four for equal numbers of real matches."""
    return 4 // math.gcd(players, 4)


def season_tables(
    players: list[str],
    games: int,
    seed: int,
    *,
    allow_byes: bool = False,
) -> tuple[list[tuple[int, list[str]]], list[tuple[int, str]]]:
    """Return real pods and explicitly awarded single-player byes.

    Every player gets ``games`` opportunities (real matches plus any bye).
    Without byes, N*M divisible by 4 is necessary AND sufficient when repeat
    opponents are allowed. Choose four highest remaining quotas each time:
    the maximum quota never exceeds the remaining number of tables, so the
    construction cannot strand a player. Pair repeats are a secondary cost.

    Flexible mode replaces N*M % 4 appearances with one bye each, assigned
    to distinct seeded entrants. Physical rests in between pods are NOT byes.
    Sampling at most 16 equal-quota candidates bounds diversity-search work.
    """
    count = len(players)
    if count < 4 or len(set(players)) != count:
        raise ValueError("A Commander season needs at least four distinct entrants.")
    if type(games) is not int or not 1 <= games <= 100:
        raise ValueError("Choose 1 to 100 season matches per player.")
    remainder = count * games % 4
    if remainder and not allow_byes:
        step = required_match_step(count)
        raise ValueError(
            f"With {count} Commander entrants, matches per player must be a multiple "
            f"of {step} for equal real matches. Choose {step}, {step * 2}, ... "
            "or allow season byes."
        )
    rng = random.Random(seed)
    order = list(players)
    rng.shuffle(order)
    bye_players = order[:remainder] if allow_byes else []
    bye_set = set(bye_players)
    heap = [
        (-(games - int(player in bye_set)), rng.random(), player)
        for player in order
        if games - int(player in bye_set) > 0
    ]
    heapq.heapify(heap)
    pairs: Counter[tuple[str, ...]] = Counter()
    pods: list[tuple[int, list[str]]] = []
    round_no = 1
    in_round: set[str] = set()
    while heap:
        group: list[str] = []
        selected: list[tuple[int, float, str]] = []
        for _ in range(4):
            if not heap:
                raise AssertionError("Balanced Commander quotas stranded a partial table.")
            quota = heap[0][0]
            candidates: list[tuple[int, float, str]] = []
            while heap and heap[0][0] == quota and len(candidates) < 16:
                candidates.append(heapq.heappop(heap))
            chosen = min(
                candidates,
                key=lambda entry: (
                    sum(pairs[tuple(sorted((entry[2], other)))] for other in group),
                    entry[1],
                ),
            )
            for entry in candidates:
                if entry != chosen:
                    heapq.heappush(heap, entry)
            selected.append(chosen)
            group.append(chosen[2])
        for quota, _, player in selected:
            if quota < -1:
                heapq.heappush(heap, (quota + 1, rng.random(), player))
        if in_round.intersection(group):
            round_no += 1
            in_round.clear()
        pods.append((round_no, group))
        in_round.update(group)
        for a, b in combinations(group, 2):
            pairs[tuple(sorted((a, b)))] += 1
    # Put each awarded bye in a physical round in which that entrant is absent.
    # No extra result is assigned for any other unavoidable physical rest.
    rounds: dict[int, set[str]] = {}
    for number, group in pods:
        rounds.setdefault(number, set()).update(group)
    byes = [
        (next(number for number, present in rounds.items() if player not in present), player)
        for player in bye_players
    ]
    return pods, byes
