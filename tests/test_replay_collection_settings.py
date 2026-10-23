from pathlib import Path

import pytest

from deepdeck_learner.experience import save_experience_settings


def test_partial_quota_edit_preserves_paused_collection_and_custom_interval(tmp_path: Path) -> None:
    saved = save_experience_settings(
        tmp_path, {"autoCollectLeagueReplays": False, "replayPollSeconds": 1200}
    )
    updated = save_experience_settings(tmp_path, {"replayStorageLimitBytes": 3 * 1024**3})
    assert saved["autoCollectLeagueReplays"] is False
    assert updated["autoCollectLeagueReplays"] is False
    assert updated["replayPollSeconds"] == 1200
    assert updated["replayStorageLimitBytes"] == 3 * 1024**3


def test_collection_or_polling_edits_never_trigger_replay_eviction(
    tmp_path: Path, monkeypatch
) -> None:
    from deepdeck_learner import experience

    calls = []
    monkeypatch.setattr(
        experience, "enforce_replay_quota", lambda *args, **kwargs: calls.append(args)
    )
    save_experience_settings(tmp_path, {"autoCollectLeagueReplays": False})
    save_experience_settings(
        tmp_path, {"autoCollectLeagueReplays": True, "replayPollSeconds": 1200}
    )
    assert calls == []
    save_experience_settings(tmp_path, {"replayStorageLimitBytes": 3 * 1024**3})
    assert len(calls) == 1


@pytest.mark.parametrize("invalid", ["false", "true", 0, 1, None, []])
def test_collection_requires_a_real_boolean_and_invalid_edits_do_not_write(
    tmp_path: Path, invalid
) -> None:
    save_experience_settings(tmp_path, {"autoCollectLeagueReplays": False})
    # Read the real configured path, not a guessed filename.
    from deepdeck_learner.experience import _settings_path

    path = _settings_path(tmp_path)
    before = path.read_bytes()
    with pytest.raises(ValueError, match="true or false"):
        save_experience_settings(tmp_path, {"autoCollectLeagueReplays": invalid})
    assert path.read_bytes() == before
