from __future__ import annotations

import random

import pytest

from deepdeck_examples.local_batch import _starting_player
from deepdeck_learner.play_settings import game_seed, local_play_settings


def test_relaxed_human_defaults_have_no_hidden_two_hour_cutoff() -> None:
    settings = local_play_settings({}, seats=2, has_human=True)
    assert settings["humanDecisionTimeoutSeconds"] is None
    assert settings["matchTimeoutSeconds"] is None
    assert settings["maxTurns"] == 60 and settings["startingSeat"] == "rotate"
    assert settings["aiSpeed"] == "1ms"
    assert 0 <= settings["firstSeed"] <= 2**31 - 1


def test_ai_only_defaults_stay_bounded() -> None:
    assert local_play_settings({}, seats=4, has_human=False)["matchTimeoutSeconds"] == 600
    with pytest.raises(ValueError, match="AI-only"):
        local_play_settings({"match_timeout_seconds": None}, seats=2, has_human=False)


@pytest.mark.parametrize("speed", ["1ms", "10ms", "100ms"])
@pytest.mark.parametrize("limit", [60, 180, 600])
def test_new_local_pacing_and_match_limits_are_preserved(speed, limit):
    settings = local_play_settings(
        {"ai_speed": speed, "match_timeout_seconds": limit}, seats=2, has_human=False
    )
    assert settings["aiSpeed"] == speed
    assert settings["matchTimeoutSeconds"] == limit


@pytest.mark.parametrize("turns", [20, 30, 40, 60, 100])
def test_turn_limit_presets_are_preserved(turns):
    assert local_play_settings({"max_turns": turns}, seats=2, has_human=False)["maxTurns"] == turns


def test_blank_seed_draws_fresh_entropy_and_explicit_seed_never_uses_it(monkeypatch):
    from deepdeck_learner import play_settings

    values = iter([111, 222])
    monkeypatch.setattr(play_settings.secrets, "randbelow", lambda maximum: next(values))
    first = local_play_settings({}, seats=2, has_human=False)
    second = local_play_settings({"seed": None}, seats=2, has_human=False)
    explicit = local_play_settings({"seed": 0}, seats=2, has_human=False)
    assert first["firstSeed"] == 111 and second["firstSeed"] == 222
    assert first["seedSource"] == second["seedSource"] == "random"
    assert explicit["firstSeed"] == 0 and explicit["seedSource"] == "explicit"


@pytest.mark.parametrize(
    "field,value",
    [
        ("seed", -1),
        ("seed", True),
        ("seed", 1.25),
        ("seed", 2**31),
        ("max_turns", 19),
        ("max_turns", 2001),
        ("starting_seat", 3),
        ("starting_seat", False),
        ("human_decision_timeout_seconds", 601),
        ("human_decision_timeout_seconds", 0),
        ("match_timeout_seconds", 29),
        ("match_timeout_seconds", 43201),
    ],
)
def test_session_settings_reject_invalid_or_ignored_values(field, value) -> None:
    with pytest.raises(ValueError):
        local_play_settings({field: value}, seats=2, has_human=True)


def test_seed_is_the_first_games_actual_seed_and_old_specs_remain_replayable() -> None:
    settings = local_play_settings(
        {"seed": 42, "starting_seat": 2, "max_turns": 80, "human_decision_timeout_seconds": 180},
        seats=2,
        has_human=True,
    )
    spec = {"seed": settings["firstSeed"], "firstSeed": settings["firstSeed"]}
    assert [game_seed(spec, index) for index in (1, 2, 3)] == [42, 43, 44]
    assert game_seed({"seed": 42}, 1) == 43
    assert settings["humanDecisionTimeoutSeconds"] == 180


def test_rotation_and_seeded_opening_seats_are_deterministic() -> None:
    spec = {"seats": [None] * 4, "startingSeat": "rotate"}
    assert [_starting_player(spec, index, random.Random(1)) for index in range(1, 6)] == [
        0,
        1,
        2,
        3,
        0,
    ]
    assert _starting_player({**spec, "startingSeat": 4}, 1, random.Random(1)) == 3
    assert _starting_player(
        {**spec, "startingSeat": "random"}, 1, random.Random(72)
    ) == _starting_player({**spec, "startingSeat": "random"}, 1, random.Random(72))
    assert _starting_player({"seats": [None] * 2}, 1, random.Random(1)) == 1
