from __future__ import annotations

from collections import Counter
from itertools import combinations

import pytest

from deepdeck_learner.commander_season import required_match_step, season_tables


@pytest.mark.parametrize("count", range(4, 33))
@pytest.mark.parametrize("games", [1, 2, 3, 4, 5, 8, 12])
@pytest.mark.parametrize("allow_byes", [False, True])
def test_balanced_quotas_complete_tables_and_no_duplicate_players_in_round(
    count, games, allow_byes
):
    players = [f"p{i}" for i in range(count)]
    if count * games % 4 and not allow_byes:
        with pytest.raises(ValueError, match="multiple"):
            season_tables(players, games, 42)
        return
    pods, byes = season_tables(players, games, 42, allow_byes=allow_byes)
    appearances = Counter(p for _, pod in pods for p in pod)
    bye_counts = Counter(p for _, p in byes)
    assert len(pods) == count * games // 4
    assert len(byes) == count * games % 4
    assert all(appearances[p] + bye_counts[p] == games for p in players)
    assert max(appearances.values()) - min(appearances.get(p, 0) for p in players) <= 1
    seen = {}
    for number, pod in pods:
        assert len(pod) == len(set(pod)) == 4
        assert not set(pod) & seen.setdefault(number, set())
        seen[number].update(pod)
    assert all(p not in seen[number] for number, p in byes)
    assert (pods, byes) == season_tables(players, games, 42, allow_byes=allow_byes)


def test_five_player_exact_cycle_has_balanced_opponents_not_only_equal_participation():
    players = list("ABCDE")
    pods, byes = season_tables(players, 4, 0)
    assert not byes
    assert len({frozenset(pod) for _, pod in pods}) == 5
    pairs = Counter(pair for _, pod in pods for pair in combinations(sorted(pod), 2))
    assert set(pairs.values()) == {3}


@pytest.mark.parametrize("count,step", [(4, 1), (5, 4), (6, 2), (7, 4), (8, 1), (10, 2)])
def test_required_quota_step(count, step):
    assert required_match_step(count) == step


def test_seeds_change_pairings_and_large_local_fields_still_have_exact_counts():
    players = [str(i) for i in range(128)]
    first = season_tables(players, 12, 42)
    assert first != season_tables(players, 12, 43)
    assert len(first[0]) == 384
    assert set(Counter(p for _, pod in first[0] for p in pod).values()) == {12}
